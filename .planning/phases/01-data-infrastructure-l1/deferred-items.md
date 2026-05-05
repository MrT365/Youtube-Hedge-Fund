# Phase 1 Deferred Items

Out-of-scope discoveries during plan execution that should be addressed in a
later plan (or filed as a phase-level cleanup task).

## Test isolation: structlog/stdlib bridge state pollution

**Discovered during:** Plan 01-09 execution (2026-05-04)

**Files affected:**
- `tests/unit/data/test_macro_calendar.py::test_fallback_within_7d_does_not_emit_stale_warning`
- `tests/unit/data/test_macro_calendar.py::test_fallback_beyond_7d_emits_stale_warning`

**Symptom:** Both tests pass in isolation (`pytest tests/unit/data/test_macro_calendar.py`) but
fail when ANY earlier test invokes `configure_logging()` via the Typer
`CliRunner` (e.g. `tests/unit/test_cli_doctor.py`, the new
`tests/unit/cli/test_data_cmd.py`).

**Root cause:** `ls_equity_fund/logging.py::configure_logging` mutates the
stdlib root logger's handler list and reconfigures structlog's stdlib bridge.
The macro tests rely on `capsys` to detect specific structlog event names —
but post-`configure_logging`, structlog routes through stdlib's `logging`
which still carries handlers from the previous test (now pointing at deleted
tmp_path log files). The `reset_logging` autouse fixture only flips
`_CONFIGURED = False` and doesn't call `structlog.reset_defaults()` or
clear stdlib root handlers.

**Why deferred:** This is a pre-existing condition (verified by reverting my
plan-01-09 changes — the same 2 tests fail at baseline when doctor tests
precede them in collection order). It surfaces more visibly with my new CLI
tests because they add another `configure_logging` call site, but the bug
predates this plan.

**Architectural fix needed:** Add a session-scoped `cleanup_logging` fixture
in `conftest.py` that calls `structlog.reset_defaults()` AND wipes
`logging.getLogger().handlers` between tests, OR refactor
`configure_logging` to be fully reversible. Should be its own plan.

**Workaround for now:** Run the 2 failing tests in isolation, or before any
CLI test, to confirm L1 macro behavior. Pre-existing baseline: same 2 tests
fail at HEAD~1 with the same symptom.
