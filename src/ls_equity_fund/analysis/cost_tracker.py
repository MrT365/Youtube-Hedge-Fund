"""ANAL-02 + ANAL-03 — Anthropic API cost accounting + per-run ceiling.

Pure (no Anthropic SDK import; takes a usage-shaped object) so every analyzer
shares the same accounting and tests run without network.

Pricing — published Sonnet 4.5 rates as of 2026-Q2 (CLAUDE.md "Anthropic Claude"
table multipliers). Values are config-driven so a future model swap (Sonnet 4.6,
Opus 4.7) only updates one place.

  - Input:        $3.00  / 1M tokens  (1.0×)
  - Output:       $15.00 / 1M tokens  (5.0× input)
  - Cache write:  $3.75  / 1M tokens  (1.25× input — Anthropic charges for the
                  prompt-cache write that lets future calls hit the cache)
  - Cache read:   $0.30  / 1M tokens  (0.10× input — what cached prompts cost
                  when re-served)

The ceiling abort behavior is structural — once total_usd >= ceiling_usd, no
further analyzer call is made for the run. Already-charged calls remain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PriceTable:
    """Per-million-token rates in USD.

    Default values match published Sonnet 4.5 pricing as of CLAUDE.md (Apr 2026).
    Override via ``PriceTable.for_model("claude-opus-4-7", ...)`` if Anthropic
    publishes new rates without an SDK release.
    """

    input_per_mtok: float = 3.00
    output_per_mtok: float = 15.00
    cache_write_per_mtok: float = 3.75
    cache_read_per_mtok: float = 0.30


class _UsageProto(Protocol):
    """Shape of ``response.usage`` returned by anthropic.Anthropic.messages.create."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


class CostCeilingExceeded(RuntimeError):
    """Raised when a recorded call would push total cost over the configured ceiling.

    Per ANAL-03 the run aborts; the orchestrator catches this and writes a
    'partial' or 'failed' status to the runs row.
    """


@dataclass
class CostTracker:
    """Accumulates token + dollar usage across a run; aborts past ``ceiling_usd``.

    Mutable on purpose — one tracker per run, ``record(usage)`` called for every
    Claude response. ``would_exceed(estimate)`` lets the analyzer skip a call
    that's known to bust the budget BEFORE making it.
    """

    ceiling_usd: float = 25.0
    prices: PriceTable = field(default_factory=PriceTable)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    total_usd: float = 0.0
    n_calls: int = 0

    @staticmethod
    def cost_of(
        *,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
        prices: PriceTable | None = None,
    ) -> float:
        """Pure cost calculation — used both internally and by the analyzers
        for `--estimate-cost` dry runs (ANAL-12)."""
        p = prices or PriceTable()
        return (
            (input_tokens / 1_000_000.0) * p.input_per_mtok
            + (output_tokens / 1_000_000.0) * p.output_per_mtok
            + (cache_write_tokens / 1_000_000.0) * p.cache_write_per_mtok
            + (cache_read_tokens / 1_000_000.0) * p.cache_read_per_mtok
        )

    def record(self, usage: _UsageProto | dict[str, Any]) -> float:
        """Accumulate one call's usage; return the incremental cost.

        Accepts either the SDK's ``Usage`` object or an equivalent dict (handy
        for tests). The dict shape is ``{"input_tokens": int, "output_tokens":
        int, "cache_creation_input_tokens": int|None, "cache_read_input_tokens":
        int|None}``.
        """
        if isinstance(usage, dict):
            inp = int(usage.get("input_tokens") or 0)
            out = int(usage.get("output_tokens") or 0)
            cw = int(usage.get("cache_creation_input_tokens") or 0)
            cr = int(usage.get("cache_read_input_tokens") or 0)
        else:
            inp = int(getattr(usage, "input_tokens", 0) or 0)
            out = int(getattr(usage, "output_tokens", 0) or 0)
            cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

        delta = self.cost_of(
            input_tokens=inp,
            output_tokens=out,
            cache_write_tokens=cw,
            cache_read_tokens=cr,
            prices=self.prices,
        )
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_write_tokens += cw
        self.cache_read_tokens += cr
        self.total_usd += delta
        self.n_calls += 1
        return delta

    def would_exceed(self, estimated_cost_usd: float) -> bool:
        """Pre-call gate: would adding this estimated cost bust the ceiling?"""
        return (self.total_usd + estimated_cost_usd) >= self.ceiling_usd

    def assert_under_ceiling(self) -> None:
        """Post-call gate: raise if we've already hit the ceiling.

        Caller pattern is "record then assert" — record the call so its cost
        IS counted (the API charged us either way), then abort before issuing
        the next one.
        """
        if self.total_usd >= self.ceiling_usd:
            raise CostCeilingExceeded(
                f"cost ceiling ${self.ceiling_usd:.2f} hit "
                f"(total ${self.total_usd:.4f} after {self.n_calls} call(s))"
            )

    def cache_hit_rate(self) -> float:
        """Fraction of cached-input tokens out of (input + cache_read).

        Useful for the run summary; 0.0 when there are no input tokens at all.
        """
        denom = self.input_tokens + self.cache_read_tokens
        if denom == 0:
            return 0.0
        return self.cache_read_tokens / denom

    def summary(self) -> dict[str, Any]:
        """Compact summary for logging + run summary printout."""
        return {
            "calls": self.n_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_hit_rate": round(self.cache_hit_rate(), 4),
            "total_usd": round(self.total_usd, 4),
            "ceiling_usd": self.ceiling_usd,
            "remaining_usd": round(self.ceiling_usd - self.total_usd, 4),
        }


__all__ = [
    "CostCeilingExceeded",
    "CostTracker",
    "PriceTable",
]
