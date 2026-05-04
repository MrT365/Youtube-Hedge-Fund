---
phase: 00-foundation
plan: 04
subsystem: infra
tags: [structlog, logging, redaction, audit, observability, contextvars]

requires:
  - phase: 00-foundation
    provides: "LoggingConfig pydantic model (Plan 00-02; provided here as a minimal shim until 00-02 lands)"

provides:
  - "Single-entry-point logging configuration: configure_logging(LoggingConfig)"
  - "API-key redaction processor (allowlist-by-key + regex on string values)"
  - "Run-id correlation via bind_run_id helper (structlog.contextvars)"
  - "Dual-sink architecture (stderr + per-day rotating UTC-dated JSONL file)"
  - "Stdlib-logging bridge — third-party libs (anthropic, ib_async, requests) flow through the same redaction pipeline"

affects: [00-05, 00-06, 00-07, all subsequent phases that emit logs or audit trails]

tech-stack:
  added:
    - "structlog 25.5.0 (already in pyproject.toml via Plan 00-01)"
    - "structlog.stdlib.ProcessorFormatter for one-pipeline bridging"
  patterns:
    - "Two-step redaction (allowlist-by-key + regex-on-value) with explicit exclusion of generic alnum patterns to preserve UUIDs/order_ids"
    - "Idempotent module-level configuration (one configure_logging per process)"
    - "UTC-dated daily log files (no DST gaps; jq-friendly)"

key-files:
  created:
    - "src/ls_equity_fund/logging.py"
    - "src/ls_equity_fund/__init__.py"
    - "src/ls_equity_fund/config.py (minimal LoggingConfig shim — Plan 00-02 expands)"
    - "tests/__init__.py"
    - "tests/unit/__init__.py"
    - "tests/unit/test_logging_redaction.py (20 tests)"
  modified: []

key-decisions:
  - "FileHandler with date-stamped filename computed at configure_logging time (not TimedRotatingFileHandler) — daily refresh re-invokes configure_logging which picks up the new UTC date naturally; simpler than rotation orchestration."
  - "Provided minimal LoggingConfig shim in src/ls_equity_fund/config.py to unblock parallel execution while Plan 00-02 (full Config schema) lands. Field set is locked surface — Plan 00-02 must keep level/log_dir/json_renderer_when_non_tty/redact_keys names."
  - "ProcessorFormatter renderer introspection in tests goes via .processors[-1] (modern structlog API) — older docs reference .processor."

patterns-established:
  - "Logging module convention: every layer obtains a logger via structlog.get_logger(__name__); never reconfigure."
  - "Run-id binding pattern: every CLI entry calls configure_logging(cfg) then bind_run_id(uuid4()) before any other code runs."
  - "Redaction as a structlog processor — last in the shared pre-chain so it sees keys merged in by earlier processors (e.g., contextvars)."

requirements-completed: [AUDIT-02]

duration: 7min
completed: 2026-05-04
---

# Phase 0 Plan 04: structlog dual-sink logging with redaction + run-id correlation

**Single configuration entry point that emits structured JSON or colored console output, redacts API keys via allowlist + targeted regex (no generic alnum pattern, so UUIDs survive), correlates events by run_id via contextvars, and bridges stdlib logging so third-party libraries flow through the same pipeline.**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-05-04T13:15:00Z (approx — pre-task uv sync)
- **Completed:** 2026-05-04T13:22:48Z
- **Tasks:** 2 (executed as a TDD pair: RED → GREEN)
- **Files created:** 6

## Accomplishments

- `configure_logging(LoggingConfig)` is the single configuration point. Idempotent. Wires the structlog native pipeline AND attaches stdlib root handlers — one redaction policy, one renderer choice for both code paths.
- Redaction is two-step (D-18): allowlist on key names (9 entries, case-insensitive) + 2 explicit regex patterns (`sk-ant-...` and `Bearer ...`). The plan's hard requirement — that UUIDs and 32-char order ids are NOT redacted — is enforced by two unit tests.
- Dual sink (D-17): events flow to stderr AND to `{log_dir}/{UTC-YYYY-MM-DD}.jsonl` in append mode. File handler always emits JSON regardless of TTY (audit trail must be jq-friendly).
- Renderer auto-detection (D-16): `sys.stderr.isatty() ? ConsoleRenderer : JSONRenderer`, decided once at configure time, not per-event.
- `bind_run_id(uuid)` (D-19) wraps `structlog.contextvars.bind_contextvars` and accepts both `str` and `UUID`.
- Stdlib bridge (D-20) via `ProcessorFormatter` — `logging.getLogger("anthropic").error(...)` walks the same redaction processor as `structlog.get_logger().info(...)`. Verified by `test_stdlib_bridge_redacts`.

## Task Commits

1. **Task 2 (TDD RED): tests** — `3ae1cdd` (`test`)
   `test(00-04): add failing tests for structlog redaction + dual sink + run_id`

2. **Task 1 (TDD GREEN): implementation** — `3fbbf15` (`feat`)
   `feat(00-04): structlog dual-sink logging with redaction + run_id (AUDIT-02)`
   Implementation also fixes the two test introspection helpers to use `ProcessorFormatter.processors[-1]` (modern structlog API). No REFACTOR commit was needed — code came out clean on first GREEN.

(SUMMARY commit follows separately.)

## Files Created/Modified

- `src/ls_equity_fund/__init__.py` — package marker.
- `src/ls_equity_fund/config.py` — minimal `LoggingConfig` pydantic model (placeholder; Plan 00-02 expands to full Config tree).
- `src/ls_equity_fund/logging.py` — 319 lines. Exports `configure_logging`, `bind_run_id`, `clear_run_id`, `redaction_processor`, `DEFAULT_REDACT_KEYS`, `REDACT_PATTERNS`, `REDACTED_PLACEHOLDER`.
- `tests/__init__.py`, `tests/unit/__init__.py` — package markers.
- `tests/unit/test_logging_redaction.py` — 347 lines, 20 tests (see truth-coverage below).

## Test → Truth Coverage

The plan defined 8 truths in `<must_haves>.truths`. All are covered:

| Truth | Decision | Test(s) |
| ----- | -------- | ------- |
| 1. configure_logging callable from CLI | D-20 | implicit in every dual-sink test |
| 2. allowlist redacts api_key value | D-18 | `test_allowlist_redaction_by_key`, `test_allowlist_is_case_insensitive` |
| 3. regex redacts sk-ant-LEAK in non-allowlisted key | D-18 | `test_regex_redaction_on_string_value`, `test_bearer_token_regex` |
| 4. bind_run_id puts run_id in every subsequent line | D-19 | `test_run_id_appears_in_log`, `test_run_id_accepts_uuid_object` |
| 5. TTY → ConsoleRenderer; non-TTY → JSONRenderer | D-16 | `test_renderer_selection_when_tty`, `test_renderer_selection_when_non_tty`, `test_file_sink_uses_json_regardless_of_tty` |
| 6. Dual sink: stderr AND `logs/{UTC}.jsonl` | D-17 | `test_dual_sink_writes_to_file`, `test_file_sink_redacts`, `test_file_sink_is_jsonl` |
| 7. stdlib loggers flow through pipeline | D-20 | `test_stdlib_bridge_redacts`, `test_stdlib_bridge_carries_logger_name` |
| 8. UUIDs/order_ids NOT redacted | D-18 | `test_uuid_not_redacted`, `test_order_id_not_redacted`, `test_redact_patterns_does_not_include_generic_random` |

Plus 4 sanity tests: `test_default_redact_keys_set`, `test_redact_patterns_count`, `test_configure_logging_idempotent`.

**Test result:** `20 passed in 0.08s`. Ruff: `All checks passed!`.

## Decisions Made

- **File rotation strategy.** Used `logging.FileHandler` with the date computed at `configure_logging` time, not `TimedRotatingFileHandler`. Rationale: the daily-refresh launchd job re-invokes `configure_logging` once per run; sub-daily reconfigs (rare — REPL, tests) reopen the same file in append mode. `TimedRotatingFileHandler` adds rollover-thread machinery we do not need at this volume. Documented in module docstring.
- **`config.redact_keys` field is currently advisory.** v1 binds the redaction processor to the locked 9-key `DEFAULT_REDACT_KEYS`. The field is in the pydantic model so future plans can wire a configurable allowlist without a schema migration. This keeps the D-18 "locked allowlist" guarantee unambiguous for AUDIT-02 evidence.
- **Modern structlog API.** Tests introspect `ProcessorFormatter.processors[-1]` — older tutorials reference `.processor` (singular) which no longer exists in structlog 25.x.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created minimal `src/ls_equity_fund/config.py` shim**
- **Found during:** Task 2 (RED phase — tests need to import `LoggingConfig`)
- **Issue:** This worktree is on a parallel-wave executor; Plan 00-02 (which produces the full `Config` schema with `LoggingConfig`) hasn't merged into this worktree yet. The plan's `<plan_specifics>` explicitly anticipates this and offers two fallback strategies; we chose the "minimal pydantic LoggingConfig" route because it keeps tests authoring identical to the post-merge state.
- **Fix:** Created `src/ls_equity_fund/config.py` with a single `LoggingConfig` BaseModel exposing the four fields specified in the plan's `<interfaces>` block (`level`, `log_dir`, `json_renderer_when_non_tty`, `redact_keys`). Documented in the module docstring that Plan 00-02 will expand this file and MUST keep these field names.
- **Files modified:** `src/ls_equity_fund/config.py` (new — 30 lines).
- **Verification:** Tests import `LoggingConfig` and instantiate it directly. Plan 00-02 merge will replace this file or extend it; field names are stable.
- **Committed in:** `3ae1cdd` (Task 2 RED commit).

**2. [Rule 1 - Bug] Fixed `ProcessorFormatter` attribute introspection in 2 renderer-selection tests**
- **Found during:** Task 1 GREEN — first pytest run showed 18/20 pass; the two renderer-introspection tests failed because they referenced a non-existent `.processor` (singular) attribute.
- **Issue:** Modern structlog (25.x) `ProcessorFormatter` exposes the chain as `.processors` (plural tuple) and stores the renderer as the LAST element. The plan-authored tests expected `.processor` (singular) which returns `None`.
- **Fix:** Updated `test_renderer_selection_when_non_tty` and `test_renderer_selection_when_tty` to introspect `formatter.processors[-1]` with a `.processor` fallback for older structlog. Behavior under test (which renderer is chosen) is unchanged; only the introspection path was incorrect.
- **Files modified:** `tests/unit/test_logging_redaction.py`.
- **Verification:** All 20 tests pass after the fix; ruff clean.
- **Committed in:** `3fbbf15` (Task 1 GREEN commit, alongside the implementation).

**3. [Rule 1 - Bug] Replaced `try/except/pass` and `datetime.timezone.utc` per ruff hints**
- **Found during:** Task 1 GREEN — ruff flagged `UP017` (use `datetime.UTC`), `SIM105` (use `contextlib.suppress`), and `RUF022` (sort `__all__`).
- **Fix:** Imported `contextlib`, switched to `contextlib.suppress(Exception)` for handler-close error swallowing, switched to `_dt.UTC` (Python 3.11+ alias), and alphabetized `__all__`. Identical fixes applied in test file.
- **Verification:** `uv run ruff check` reports clean.
- **Committed in:** `3fbbf15`.

---

**Total deviations:** 3 auto-fixed (1 blocking — needed config shim for parallel wave; 2 bugs in plan-authored test code).
**Impact on plan:** All deviations were within scope; locked decisions D-16..D-20 and AUDIT-02 are fully satisfied without reinterpretation.

## Issues Encountered

- The plan-authored test cases for renderer selection (Task 2) referenced `formatter.processor` — a stale API name. Caught by GREEN run, fixed inside the same Task 1 commit (since the implementation was correct and only the test introspection needed updating). See deviation #2.

## User Setup Required

None — `configure_logging` is fully self-contained. Future plans (00-06 CLI doctor, 01+ data ingest) will call `configure_logging(config.logging)` then `bind_run_id(uuid4())` at every entry point.

## Next Phase Readiness

- **AUDIT-02 closed.** All locked decisions D-16..D-20 implemented and unit-tested.
- **Plan 00-02 (parallel wave 1) will need to merge cleanly with the `LoggingConfig` shim in `src/ls_equity_fund/config.py`.** Two valid merge strategies:
  1. Plan 00-02's `Config` model imports `LoggingConfig` from this file (preferred — keeps single source of truth).
  2. Plan 00-02 replaces this file with an expanded version that keeps `LoggingConfig` field names byte-stable.
- **Plan 00-06 (CLI doctor)** will be the first consumer: it must `configure_logging(cfg.logging)` then `bind_run_id(uuid4())` at the entry of every Typer command.
- **Future stdlib library imports** (e.g., yfinance, anthropic, ib_async): no extra wiring needed — they automatically flow through the redaction pipeline because root logger handlers are owned by `configure_logging`.

## Self-Check: PASSED

Verified the following exist on disk and in git:

- `src/ls_equity_fund/logging.py` — FOUND (319 lines)
- `src/ls_equity_fund/config.py` — FOUND
- `src/ls_equity_fund/__init__.py` — FOUND
- `tests/unit/test_logging_redaction.py` — FOUND (347 lines, 20 tests)
- `tests/__init__.py`, `tests/unit/__init__.py` — FOUND
- Commit `3ae1cdd` (Task 2 RED) — FOUND in `git log`
- Commit `3fbbf15` (Task 1 GREEN) — FOUND in `git log`
- `uv run pytest tests/unit/test_logging_redaction.py` — 20/20 PASS
- `uv run ruff check src/ls_equity_fund/logging.py` — clean

## TDD Gate Compliance

Although the plan-level `type: execute` (not `type: tdd`) does not formally require RED/GREEN gate commits, both tasks declared `tdd="true"` and were executed as a TDD pair:

- RED: `3ae1cdd` (`test(00-04): add failing tests…`) — verified failing import collection before implementation.
- GREEN: `3fbbf15` (`feat(00-04): structlog dual-sink logging…`) — all 20 tests pass.
- REFACTOR: not needed — first GREEN was clean enough that ruff passed and the code reads naturally.

---
*Phase: 00-foundation*
*Plan: 04*
*Completed: 2026-05-04*
