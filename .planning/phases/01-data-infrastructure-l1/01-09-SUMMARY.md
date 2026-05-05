---
phase: 01-data-infrastructure-l1
plan: 09
subsystem: cli
tags: [cli, typer, orchestrator, daily-refresh, data-12, data-14, audit-02]

# Dependency graph
requires:
  - phase: 00-foundation
    provides: "Typer app + cli/stubs.py + cli/doctor.py (load_config -> configure_logging -> bind_run_id pattern); migration 0001 ships the runs table"
  - phase: 01-data-infrastructure-l1
    provides: "Wave 2 refresh entry points (universe, benchmarks, prices, fundamentals, ratios, filings, institutional, short_interest, estimates, earnings_calendar, macro_calendar)"
provides:
  - "src/ls_equity_fund/data/orchestrator.py — run_data_pipeline() chains 11 L1 refresh steps"
  - "src/ls_equity_fund/cli/data_cmd.py — meridian run-data Typer command"
  - "DEFAULT_PHASE1_FORMS, SUPPORTED_PROVIDERS module-level contracts"
  - "runs row lifecycle (RUNNING -> OK / FAILED) — every meridian run-data invocation persists a row"
  - "Selective skip flag plumbing (--no-filings / --no-13f / --forms / --ticker / --universe-mode)"
  - "Provider guard (DATA-14): orchestrator refuses non-yfinance until that integration ships"
affects:
  - "Phase 1 wrap-up — daily-refresh launchd path can call meridian run-data --no-filings --no-13f"
  - "Phase 2 (scoring) — depends on a successful run-data leaving universe + factor inputs in place"
  - "Phase 10 (orchestrator) — daily-refresh meta-command will call run-data first"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Orchestrator step adapters: thin _xxx_step lazy-import wrappers so tests patch one symbol per step"
    - "Per-step log+continue: try/except around each step returns {error: msg} instead of aborting the chain"
    - "runs row lifecycle: INSERT 'RUNNING' at entry, UPDATE 'OK'/'FAILED' in finally"
    - "CLI exit-code matrix: distinct codes for config / env / validation / flag-conflict / provider-guard / unexpected"

key-files:
  created:
    - "src/ls_equity_fund/data/orchestrator.py"
    - "src/ls_equity_fund/cli/data_cmd.py"
    - "tests/unit/data/test_orchestrator.py"
    - "tests/unit/cli/__init__.py"
    - "tests/unit/cli/test_data_cmd.py"
    - ".planning/phases/01-data-infrastructure-l1/deferred-items.md"
  modified:
    - "src/ls_equity_fund/data/__init__.py — re-export run_data_pipeline + DEFAULT_PHASE1_FORMS"
    - "src/ls_equity_fund/cli/app.py — wire run-data to data_cmd, replace stub import + help text"
    - "src/ls_equity_fund/cli/stubs.py — delete run_data stub function and __all__ entry"
    - "tests/unit/test_cli_stubs.py — remove obsolete test_run_data_stub_accepts_flags"

key-decisions:
  - "Per-step failure does NOT abort the chain — captured as {error: str} in manifest, runs.status stays OK if all steps reached completion (only orchestrator-level fatal flips runs.status='FAILED')"
  - "DATA-14 provider guard at orchestrator entry uses SystemExit (not ValueError) so Polygon/Tiingo/IEX configs surface as a process-level abort BEFORE any DB write — defense in depth on top of pydantic Literal[...] in DataConfig"
  - "--forms is a whitelist applied AFTER --no-13f filter; --no-filings + --forms is hard-rejected as a contradiction (ValueError -> exit 5)"
  - "Step adapters are private _xxx_step wrappers with lazy imports — keeps the orchestrator surface readable AND lets tests patch each step independently without touching the underlying refresh modules' import-time side effects"

patterns-established:
  - "Pattern: log+continue per-step in a chained pipeline (carries through Phase 5/7 portfolio + Phase 9 reporting orchestrators)"
  - "Pattern: orchestrator owns the runs row lifecycle; refresh-N functions stay run-id agnostic (they receive bind_contextvars'd run_id implicitly via structlog)"
  - "Pattern: Typer CLI command thin wrapper — load_config -> configure_logging -> orchestrator -> exit-code-mapped exception handling"

requirements-completed: [DATA-12, DATA-14]

# Metrics
duration: ~30min
completed: 2026-05-05
---

# Phase 1 Plan 9: CLI orchestrator + skip flags Summary

**`meridian run-data` chains 11 L1 refresh steps under one CLI command with --no-filings/--no-13f/--forms selective skips, DATA-14 provider guard, and runs-row lifecycle for AUDIT-02.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-05-05T06:44Z (approximate; SUMMARY-time: 07:14Z)
- **Completed:** 2026-05-05T07:14:09Z
- **Tasks:** 2 (both auto, both TDD-style)
- **Files created:** 6
- **Files modified:** 4

## Accomplishments

- `run_data_pipeline(config, secrets, *, no_filings, no_13f, forms, tickers, today, conn, universe_mode)` orchestrator chains: universe → benchmarks → prices → fundamentals → ratios → filings → 13F → short → estimates → earnings → macro
- `meridian run-data` Typer command replaces the Phase 0 stub; --help lists all six flags
- Selective skip flags wired and unit-tested:
  - `--no-filings` skips both filings + 13F adapters
  - `--no-13f` skips 13F adapter only
  - `--forms 10-K,10-Q` parses CSV and forwards a list[str] to refresh_filings
  - `--no-filings` + `--forms` rejected with exit 5 ("mutually exclusive")
- `--ticker` wraps singleton into list[str] for refresh-N tickers param
- DATA-14 provider guard: `data.provider != "yfinance"` raises SystemExit with a "see DATA-14" message; CLI maps to exit 6 (`test_run_data_exit_code_6_on_polygon_provider`)
- Per-step failure tolerance: any single refresh step's exception is logged and surfaced in the manifest as `{"error": str}` without aborting the rest of the chain
- `runs` row lifecycle: INSERT 'RUNNING' at start, UPDATE 'OK'/'FAILED' in `finally`; the run_id is the same UUID4 bound to structlog's `contextvars` (single correlation ID across the run)

## Task Commits

Each task was committed atomically:

1. **Task 1: run_data_pipeline orchestrator with selective skip + provider guard** — `d86a31a` (feat)
2. **Task 2: CLI 'run-data' command + wire into Typer app** — `12b72bf` (feat)

**Plan metadata commit:** to be added in this final docs commit (SUMMARY + deferred-items + uv.lock + pyproject.toml).

_Note: per the parent-agent constraint, this plan does NOT update STATE.md or ROADMAP.md._

## Files Created/Modified

**Created:**
- `src/ls_equity_fund/data/orchestrator.py` — 11-step L1 chain with run_id binding, runs-row lifecycle, per-step log+continue, mutual-exclusion guard, DATA-14 provider guard
- `src/ls_equity_fund/cli/data_cmd.py` — Typer `run-data` command with full exit-code matrix (2/3/4/5/6/7)
- `tests/unit/data/test_orchestrator.py` — 10 unit tests (provider guard, mutual exclusion, full chain, skip flags, forms passthrough, runs lifecycle, step-failure isolation, contract assertions)
- `tests/unit/cli/__init__.py` — package marker for tests/unit/cli/
- `tests/unit/cli/test_data_cmd.py` — 9 unit tests (help surface, exit-code matrix, flag plumbing, ticker wrap)
- `.planning/phases/01-data-infrastructure-l1/deferred-items.md` — pre-existing test-isolation pollution between configure_logging-using tests and capsys-based macro tests (out of scope; predates this plan)

**Modified:**
- `src/ls_equity_fund/data/__init__.py` — export `run_data_pipeline` + `DEFAULT_PHASE1_FORMS`
- `src/ls_equity_fund/cli/app.py` — replaced `from ...stubs import run_data` with `from ...data_cmd import run_data`; updated help text from "(stub) ..." to the real description
- `src/ls_equity_fund/cli/stubs.py` — deleted `run_data` def + removed from `__all__`; updated module docstring to reflect the Phase 1 hand-off
- `tests/unit/test_cli_stubs.py` — removed obsolete `test_run_data_stub_accepts_flags` (the stub no longer exists)

## Decisions Made

- **Per-step failure does NOT abort the chain.** The plan's framing ("daily run must always produce SOMETHING for the dashboard") and Wave-2's per-ticker log+continue philosophy both pull the orchestrator toward partial completion. Implementation: each step lives inside a try/except wrapper (`_step()`) that returns `{"error": msg}` and the chain proceeds. Only an orchestrator-level fatal (e.g., DB lost) flips `runs.status='FAILED'`.
- **DATA-14 provider guard uses SystemExit**, not ValueError. This surfaces as a process-level abort distinct from the mutual-exclusion ValueError, mapping cleanly to two CLI exit codes (5 vs 6). Defense-in-depth on top of pydantic's Literal[...] type in DataConfig.
- **`--forms` is a whitelist applied AFTER `--no-13f`.** They're orthogonal axes (forms governs WHICH forms; no-13f governs WHETHER 13F is fetched). The only contradiction is `--no-filings` + `--forms` (one says "skip all", the other says "include these") — that is the lone hard-rejected combination.
- **Step adapters are private `_xxx_step` wrappers with lazy imports.** This preserves orchestrator readability AND lets tests patch each step at one symbol without touching the underlying refresh-N modules' import-time side effects (yfinance session init, edgar User-Agent setup, etc.).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing dev dependencies (pytest stack)**
- **Found during:** Pre-Task-1 collection (`uv run pytest --collect-only` failed with "No such file or directory: pytest")
- **Issue:** Worktree `.venv` did not have pytest, pytest-asyncio, freezegun, responses, or pytest-cov installed despite being declared in pyproject.toml. Cannot RED/GREEN tests without pytest.
- **Fix:** `uv add --dev pytest pytest-asyncio freezegun responses pytest-cov`. Resolved via uv lockfile sync.
- **Files modified:** `pyproject.toml`, `uv.lock` (committed in this plan's final docs commit, not a per-task commit — environment-level change)
- **Verification:** `uv run pytest --collect-only -q` returns "229 tests collected" baseline
- **Committed in:** final docs commit (separate from task commits)

### Micro-deviations

- **Test count:** Plan said "7 passed" per test file; actual is 10 orchestrator tests + 9 CLI tests = 19 total (slightly over). Extra tests cover module-level contract assertions (`SUPPORTED_PROVIDERS == frozenset({"yfinance"})`, `DEFAULT_PHASE1_FORMS == [...]`) and ticker-wrap behavior. All pass; no scope creep.
- **`tests/unit/test_cli_stubs.py` modification not listed in plan's `files_modified`:** The plan deletes the `run_data` stub but didn't pre-list the existing stub-test file as needing modification. Deleting the stub forces `test_run_data_stub_accepts_flags` to be removed (the function doesn't exist anymore — Typer would error). Documented under Rule 3 (blocking — required to remove the obsolete test that would now fail-import).

---

**Total deviations:** 1 auto-fixed (Rule 3 — dev-deps install) + 1 micro-deviation (extra tests, obsolete test removed)
**Impact on plan:** No scope creep. All deviations were correctness-required.

## Issues Encountered

- **Pre-existing test pollution: `test_macro_calendar.py` 2 tests fail in full-suite runs after any test that calls `configure_logging`.** Verified at HEAD~1 (before this plan's work) — same 2 tests fail when `tests/unit/test_cli_doctor.py` runs first in collection order. Root cause: `configure_logging` mutates stdlib root handlers + structlog's stdlib bridge, and the autouse `reset_logging` fixture in doctor tests doesn't call `structlog.reset_defaults()` or wipe stdlib handlers, leaving structlog routing through dead FileHandlers between tests. The macro tests use `capsys` to detect specific structlog event names, which silently misses when stdlib handlers are stale. **Out of scope** for this plan (architectural fix needs its own conftest-level cleanup fixture). Logged in `.planning/phases/01-data-infrastructure-l1/deferred-items.md`.

## Verification Evidence

- `uv run pytest tests/unit/data/test_orchestrator.py -v` → 10 passed
- `uv run pytest tests/unit/cli/test_data_cmd.py -v` → 9 passed
- `uv run pytest tests/integration/test_phase0_smoke.py` → 25 passed (Phase 0 doctor smoke regression unchanged)
- `uv run meridian run-data --help` → exit 0; lists --no-filings, --no-13f, --forms, --ticker, --universe-mode, --config, --env
- `uv run pytest -q` → 245 passed, 2 pre-existing failed (documented above)

**Acceptance criteria from plan:**
- ✓ `grep -c "def run_data_pipeline" src/ls_equity_fund/data/orchestrator.py` = 1
- ✓ `grep -cE "no_filings.*forms|forms.*no_filings"` = 2 (mutual-exclusion check)
- ✓ `grep -c "DATA-14"` = 2 (module docstring + SystemExit message)
- ✓ `grep -cE "INSERT INTO runs|UPDATE runs SET"` = 2 (lifecycle)
- ✓ `grep -c "bind_run_id"` = 2 (import + use)
- ✓ `grep -c "def run_data" src/ls_equity_fund/cli/data_cmd.py` = 1
- ✓ `grep -c "from ls_equity_fund.cli.data_cmd import run_data" src/ls_equity_fund/cli/app.py` = 1
- ✓ `grep "from ls_equity_fund.cli.stubs import" src/ls_equity_fund/cli/app.py | grep -c "run_data"` = 0 (replaced)
- ✓ `grep -c "def run_data" src/ls_equity_fund/cli/stubs.py` = 0 (deleted)

## User Setup Required

None — no external service configuration introduced.

## Next Phase Readiness

- **Phase 2 (scoring) ready** — `meridian run-data` populates the universe, prices, fundamentals, ratios, and SEC filings tables that L2 factor scoring will read from.
- **Phase 10 (daily-refresh) ready** — the meta-orchestrator can shell out to `meridian run-data --no-filings --no-13f` for the launchd 17:15 path (skip-flags satisfy DATA-12 spec mandate for the 10-min performance budget).
- **Phase 1 wrap-up** — only Plan 01-10 (launchd plist + 17:15 cron) remains in this phase. Plan 01-09 closes out Wave 3.
- **No blockers.** Pre-existing test-pollution bug is documented and out-of-scope.

## Self-Check: PASSED

All claimed files exist on disk:
- ✓ `src/ls_equity_fund/data/orchestrator.py` (FOUND)
- ✓ `src/ls_equity_fund/cli/data_cmd.py` (FOUND)
- ✓ `tests/unit/data/test_orchestrator.py` (FOUND)
- ✓ `tests/unit/cli/test_data_cmd.py` (FOUND)
- ✓ `tests/unit/cli/__init__.py` (FOUND)
- ✓ `.planning/phases/01-data-infrastructure-l1/deferred-items.md` (FOUND)

All claimed commits exist in git log:
- ✓ `d86a31a` — feat(01-09): add run_data_pipeline L1 orchestrator (FOUND)
- ✓ `12b72bf` — feat(01-09): replace run-data Phase 0 stub with real CLI command (FOUND)

---
*Phase: 01-data-infrastructure-l1*
*Plan: 09*
*Completed: 2026-05-05*
