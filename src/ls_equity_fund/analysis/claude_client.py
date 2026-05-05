"""ANAL-01 — Anthropic SDK wrapper enforcing prompt caching (CP2 mitigation).

Why this wrapper exists, in three sentences:
  1. CP2 says the cost ceiling depends on prompt caching working — and
     prompt caching ONLY works when ``system`` is a list of content blocks
     (NOT a plain string). One bare-string slip and the cache silently
     disables, fanning out 40 tickers worth of input tokens at full price.
  2. The wrapper makes that mistake un-typeable: callers pass a list of
     plain Python ``str`` (the "blocks") and the wrapper builds the
     content-block list with ``cache_control`` already attached.
  3. The wrapper also frames every call as "send me the structured-JSON
     response and parse it" so analyzers don't reinvent the
     <json>...</json> regex in five different files.

Constraints (CP2):
  - No ``datetime.now()`` or other per-request value in cached blocks.
  - No images conditionally — image presence anywhere invalidates cache.
  - Instructions (system) edits create a NEW prompt-version file rather
     than mutating the existing one. See ``analysis/prompts/v1/``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

try:
    import anthropic  # noqa: F401  imported for side-effect type checks
    from anthropic import Anthropic
except ImportError as e:  # pragma: no cover - handled at runtime
    raise ImportError(
        "anthropic SDK is required (>=0.97). Install with `uv sync --all-extras`."
    ) from e

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ls_equity_fund.analysis.cost_tracker import CostCeilingExceeded, CostTracker

log = structlog.get_logger(__name__)

# Versioned prompts directory — per CP2 "Freeze system prompts in versioned
# files. Edits get a new version dir." The package ships with v1; bump to v2
# when an analyzer's prompt content changes meaningfully.
PROMPTS_ROOT = Path(__file__).parent / "prompts"
DEFAULT_PROMPT_VERSION = "v1"


@dataclass(frozen=True)
class ClaudeResponse:
    """One Anthropic call's structured return.

    Attributes:
      text: Raw text from the assistant turn (used for ``parse_json`` extraction).
      usage: Token-usage dict shaped like ``response.usage`` — accepted by
             ``CostTracker.record``. Includes both prompt-cache fields:
             ``cache_creation_input_tokens`` (1.25× rate) and
             ``cache_read_input_tokens`` (0.10× rate).
      stop_reason: Anthropic's stop reason (typically "end_turn").
    """

    text: str
    usage: dict[str, Any]
    stop_reason: str


@dataclass
class ClaudeClient:
    """Thin Anthropic SDK wrapper.

    Build once per process: ``ClaudeClient.create(secrets, config)``.
    Threadsafe — the underlying ``Anthropic`` client uses an httpx pool.
    """

    api_key: str
    model: str
    cost_tracker: CostTracker
    use_cache_control: bool = True
    cache_ttl: str | None = "1h"  # "1h" or None (5min default); see CP2 doc
    _client: Anthropic = field(init=False)

    def __post_init__(self) -> None:
        self._client = Anthropic(api_key=self.api_key)

    # --- factory ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        api_key: str,
        model: str = "claude-sonnet-4-5",
        cost_tracker: CostTracker | None = None,
        use_cache_control: bool = True,
        cache_ttl: str | None = "1h",
    ) -> ClaudeClient:
        """Construct with defaults from CLAUDE.md.

        ``cost_tracker`` defaults to a fresh ``CostTracker(ceiling=$25.0)``;
        callers usually want to share one tracker across an entire run.
        """
        return cls(
            api_key=api_key,
            model=model,
            cost_tracker=cost_tracker or CostTracker(),
            use_cache_control=use_cache_control,
            cache_ttl=cache_ttl,
        )

    # --- core call --------------------------------------------------------

    def call(
        self,
        *,
        system_blocks: list[str],
        user_message: str,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> ClaudeResponse:
        """Send one Messages request with mandatory cache_control on system blocks.

        ``system_blocks`` are concatenated (each block becomes one content
        block) and tagged with ``cache_control: {"type": "ephemeral"}``. Order
        matters — Anthropic only matches a contiguous prefix of the cached
        content, so the most-stable instruction goes first.

        Per CP2 "never include datetime.now() or any per-request value in
        cached blocks": the wrapper does not rewrite blocks, so it's the
        caller's responsibility to keep them stable. Analyzers achieve this
        by reading the blocks from versioned prompt files.

        Returns ``ClaudeResponse``. Raises ``CostCeilingExceeded`` if the
        cost tracker has already crossed its ceiling.
        """
        # Pre-call ceiling check — abort BEFORE the network call if we're done.
        self.cost_tracker.assert_under_ceiling()

        system = self._build_system(system_blocks)
        # Anthropic's TypedDict MessageParam is overly strict with role; cast.
        messages: list[Any] = [{"role": "user", "content": user_message}]

        response = self._call_with_retry(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        usage_dict = _usage_to_dict(response.usage)

        # Record cost; raise if this call took us over.
        delta = self.cost_tracker.record(usage_dict)
        log.info(
            "claude_call",
            model=self.model,
            input_tokens=usage_dict.get("input_tokens"),
            output_tokens=usage_dict.get("output_tokens"),
            cache_read=usage_dict.get("cache_read_input_tokens"),
            cache_write=usage_dict.get("cache_creation_input_tokens"),
            delta_usd=round(delta, 4),
            total_usd=round(self.cost_tracker.total_usd, 4),
        )

        # Extract assistant text.
        text = _extract_text(response.content)
        return ClaudeResponse(
            text=text,
            usage=usage_dict,
            stop_reason=getattr(response, "stop_reason", "") or "",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(
            (
                # Use stringly-typed retry to avoid hard SDK coupling on rare
                # error classes that may move between minor releases.
                Exception,
            )
        ),
        reraise=True,
    )
    def _call_with_retry(
        self,
        *,
        system: Any,
        messages: list[Any],
        max_tokens: int,
        temperature: float,
    ) -> Any:
        # NB: Inside the retry wrapper we DO NOT swallow CostCeilingExceeded.
        # That's a pre-call gate; this method only runs when we're under the
        # ceiling. Network errors retry; cost errors propagate immediately.
        try:
            return self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=messages,
            )
        except CostCeilingExceeded:  # pragma: no cover - defensive
            raise

    # --- helpers ----------------------------------------------------------

    def _build_system(self, blocks: list[str]) -> list[dict[str, Any]] | str:
        """Convert plain-text blocks into Anthropic content-block list.

        If ``use_cache_control`` is False (e.g., debugging), fall back to a
        single concatenated string — caching disabled, but the call still
        works. ANAL-01 production path always sends the list.
        """
        if not blocks:
            raise ValueError("system_blocks must contain at least one block")

        if not self.use_cache_control:
            return "\n\n".join(blocks)

        ttl_marker: dict[str, Any] = {"type": "ephemeral"}
        if self.cache_ttl == "1h":
            ttl_marker["ttl"] = "1h"
        # 5min default if cache_ttl is None or any other value — Anthropic's
        # ephemeral default; we deliberately don't try to invent other TTLs.

        return [
            {
                "type": "text",
                "text": block,
                "cache_control": ttl_marker,
            }
            for block in blocks
        ]


# --- module helpers ----------------------------------------------------


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    """Extract Usage fields into a plain dict."""
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": (getattr(usage, "cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens": (getattr(usage, "cache_read_input_tokens", 0) or 0),
    }


def _extract_text(content: Any) -> str:
    """Pull the assistant's text from the content list."""
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else None
        )
        if text:
            parts.append(text)
    return "\n".join(parts)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_BARE_RE = re.compile(r"(\{.*\})", re.DOTALL)


def parse_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from a Claude response.

    Tries three strategies in order:
      1. The whole response is one JSON object.
      2. A ```json ...``` fenced block.
      3. The first ``{ ... }`` greedy match.

    Raises ``ValueError`` if no parseable JSON found — analyzers should catch
    and fall back to a degenerate-neutral response rather than crash the run.
    """
    if not text or not text.strip():
        raise ValueError("empty Claude response")
    stripped = text.strip()
    # 1. Whole-response JSON
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass
    # 2. Fenced
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass
    # 3. Greedy
    m = _JSON_BARE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            raise ValueError(f"could not parse JSON from Claude response: {e}") from e
    raise ValueError("no JSON object found in Claude response")


def load_prompt(name: str, *, version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Load a frozen prompt file from ``analysis/prompts/{version}/{name}.txt``.

    Per CP2: edits get a new version dir, never an in-place rewrite, because
    the prompt content is the prompt-cache key. Mutating v1's file silently
    invalidates the prompt cache the next time the daily run runs.
    """
    path = PROMPTS_ROOT / version / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}. Expected at {PROMPTS_ROOT}/{version}/.")
    return path.read_text(encoding="utf-8")


def estimate_cost(
    *,
    input_chars: int,
    output_chars: int,
    cache_chars: int = 0,
    cost_tracker: CostTracker | None = None,
) -> float:
    """Rough USD estimate for a Claude call given character counts.

    ANAL-12 ``--estimate-cost`` uses this without the network. We assume
    ~4 chars per token (Anthropic's own rule of thumb for English). The
    cache_chars portion is treated as cache_read at 0.1× — i.e., assumes a
    warm cache. Use this for daily-run budgeting; first-run cold-cache cost
    is ~12.5× higher per cached block (write at 1.25×, read at 0.1×).
    """
    tracker = cost_tracker or CostTracker()
    return tracker.cost_of(
        input_tokens=max(0, (input_chars - cache_chars)) // 4,
        output_tokens=output_chars // 4,
        cache_read_tokens=cache_chars // 4,
        prices=tracker.prices,
    )


__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "PROMPTS_ROOT",
    "ClaudeClient",
    "ClaudeResponse",
    "estimate_cost",
    "load_prompt",
    "parse_json",
]


# Mark the imported helpers as used (mypy sees them only via from-imports).
_USED: tuple[Any, ...] = (Iterable, time)
