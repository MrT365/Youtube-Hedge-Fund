---
phase: 00-foundation
plan: 06
subsystem: infra
tags: [typer, cli, alembic, sqlite, structlog, pydantic-settings]

# Dependency graph
requires:
  - phase: 00-foundation
    provides: load_config + Secrets (Plan 02), get_connection + WAL pragmas + get_db_path (Plan 03), configure_logging + bind_run_id + redaction (Plan 04), Broker/MarketDataProvider/Optimizer ABCs (Plan 05)
provides:
  - "meridian doctor — Phase 0 SC2 smoke command (config + DB + migrations + structured success log + exit 0)"
  - "Typer app at ls_equity_fund.cli.app:app exposing 8 subcommands (doctor + 7 stubs)"
  - "python -m ls_equity_fund.cli alternate entry equivalent to the meridian script"
  - "Locked global-flag wiring per D-23 (--dry-run, --whatif, --no-filings, --no-13f, --ticker, --sector, --optimize-method, --estimate-cost) so Phase 1+ tasks plug bodies in without re-litigating CLI shape"
  - "Distinct exit codes (0/2/3/4/5/6/7) mapping each doctor failure mode to operator-actionable hints"
affects: [phase-01-data, phase-02-scoring, phase-04-analysis, phase-05-portfolio, phase-08-execution, phase-09-reporting, phase-10-orchestrator]

# Tech tracking
tech-stack:
  added:
    - "typer (already pinned in pyproject; first concrete usage)"
    - "alembic.command (Python API for upgrade head, not subprocess)"
  patterns:
    - "Single Typer app, kebab-case command names, one entry-point hook (D-23)"
    - "Doctor exit-code map: each failure mode gets a unique non-zero code so launchd / CI can branch on it"
    - "Stubs accept locked global flags from day one — Phase 1+ fills bodies, never re-litigates CLI surface"
    - "Post-alembic logging-pipeline restoration pattern (re-enable disabled loggers + structlog.reset_defaults + configure_logging) — reused anywhere a downstream lib calls fileConfig"

key-files:
  created:
    - "src/ls_equity_fund/cli/__init__.py"
    - "src/ls_equity_fund/cli/__main__.py"
    - "src/ls_equity_fund/cli/app.py"
    - "src/ls_equity_fund/cli/doctor.py"
    - "src/ls_equity_fund/cli/stubs.py"
    - "tests/unit/test_cli_smoke.py"
    - "tests/unit/test_cli_doctor.py"
    - "tests/unit/test_cli_stubs.py"
    - ".planning/phases/00-foundation/deferred-items.md"
  modified: []

key-decisions:
  - "Used alembic.command.upgrade Python API (not subprocess) for cleaner error capture in tests and structured exception surface"
  - "Doctor exit codes 2..7 map distinct failure modes (config missing / .env missing / validation / WAL fail / migration / tables missing) — operator-actionable without log inspection"
  - "Re-attached structlog pipeline AFTER alembic.command.upgrade to recover from fileConfig wipe; localized scar tissue to doctor.py rather than modifying env.py"
  - "Click 8.3 dropped CliRunner(mix_stderr=) — switched to plain CliRunner() (stderr is now always separated via result.stderr)"

patterns-established:
  - "Doctor pattern: locate-files → load_config → configure_logging → bind_run_id → open-DB-WAL → upgrade-migrations → verify-required-tables → emit-success-log+banner. Reusable shape for any phase-N readiness check."
  - "Stub-with-flags pattern: every stub declares the flags it WILL consume so plan-N+1 can fill the body without changing the CLI signature."

requirements-completed: [INFRA-08]

# Metrics
duration: 9min
completed: 2026-05-04
---

# Phase 0 Plan 6: CLI Skeleton + meridian doctor Summary

**Typer-based meridian CLI with one working `doctor` smoke command (config → WAL DB → alembic upgrade → audit-log success → exit 0) and seven Phase-0 stub subcommands accepting their locked global flags so Phase 1+ plugs bodies in without re-litigating CLI shape.**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-05-04T13:30:22Z
- **Completed:** 2026-05-04T13:39:35Z
- **Tasks:** 3 (RED smoke + GREEN skeleton/stubs + tests; merged Task 1 & 2 into one feat commit due to import cycle, see Deviations)
- **Files created:** 9 (5 src + 3 tests + 1 deferred-items)
- **Files modified:** 0
- **Lines of code:** ~424 src + ~338 test

## Accomplishments

- **Phase 0 SC2 fully met.** `uv run meridian doctor` exits 0 against the shipped `config.yaml.example`/`.env.example` (after copy): loads config, opens SQLite in WAL, applies `0001_create_runs_and_heartbeat_tables`, and emits `doctor_passed` to both stderr (JSON when non-TTY) and `logs/{UTC-date}.jsonl`.
- **Doctor is idempotent (D-25).** Re-running on a healthy system exits 0 with no schema change; alembic_version stays at exactly one row. Verified by `test_doctor_is_idempotent`.
- **Doctor does NOT initialize `.env` (D-25).** Missing `.env` exits with code 3 and the literal phrase "does NOT initialize" — operator copies `.env.example` themselves. Verified by `test_doctor_missing_env`.
- **All 8 subcommands wired (D-23).** `meridian --help` lists `doctor`, `daily-refresh`, `run-data`, `run-scoring`, `run-analysis`, `run-portfolio`, `run-execution`, `run-reporting`.
- **Locked global flags accepted today (INFRA-08).** Stubs accept `--dry-run`, `--whatif`, `--no-filings`, `--no-13f`, `--ticker`, `--sector`, `--optimize-method`, `--estimate-cost`; flags parse without error even though Phase 0 bodies do nothing.
- **Distinct exit codes (operator-actionable).** 0=ok, 2=config.yaml missing, 3=.env missing, 4=config validation, 5=WAL not active, 6=migration failure, 7=required tables missing.
- **Audit trail intact across alembic boundary.** `doctor_passed` event reaches the log file even though alembic's env.py wipes the stdlib root via `fileConfig`. Verified by `test_doctor_emits_doctor_passed_log`.

## Task Commits

1. **Task 1 RED (smoke test fails — no cli module yet)** — `c6098fc` (`test`)
2. **Task 1+2 GREEN (Typer app + doctor + 7 stubs together)** — `c2ce9df` (`feat`)
3. **Rule-1 deviation: restore logging pipeline after alembic.fileConfig wipe** — `612ea22` (`fix`)
4. **Task 3 (test_cli_doctor.py + test_cli_stubs.py — 20 tests, all pass)** — `5dc30e6` (`test`)

_Plan metadata commit (this SUMMARY + deferred-items) follows below._

## Files Created/Modified

- `src/ls_equity_fund/cli/__init__.py` (11 lines) — re-exports `app` for the `meridian` console-script hook in `pyproject.toml [project.scripts]`.
- `src/ls_equity_fund/cli/__main__.py` (14 lines) — `python -m ls_equity_fund.cli` alternate entry routing to the same Typer app.
- `src/ls_equity_fund/cli/app.py` (85 lines) — single Typer instance wiring all 8 subcommands. `pretty_exceptions_enable=False` so structlog/our handlers format errors consistently. `add_completion=False` (solo operator, no completion needed).
- `src/ls_equity_fund/cli/doctor.py` (204 lines) — 9-step doctor flow with distinct exit codes 2..7 and Rule-1 fix for the alembic.fileConfig logging wipe. Uses `alembic.command.upgrade` Python API.
- `src/ls_equity_fund/cli/stubs.py` (110 lines) — 7 stub subcommands accepting locked global flags from D-23/INFRA-08; each prints `<cmd>: not implemented in this phase (Phase X)` and exits 0.
- `tests/unit/test_cli_smoke.py` (20 lines) — TDD RED gate: app + doctor module-import smoke.
- `tests/unit/test_cli_doctor.py` (210 lines) — 9 tests covering doctor behavior, exit codes, idempotency, log audit trail, redaction cross-check.
- `tests/unit/test_cli_stubs.py` (108 lines) — 11 tests covering stub flag wiring, --help listing, and unknown-flag negative behavior.
- `.planning/phases/00-foundation/deferred-items.md` — pre-existing test_migrations.py failures documented (not introduced by 00-06).

## Decisions Made

- **alembic.command.upgrade Python API over subprocess** — cleaner exception surface, no shell quoting concerns, alembic.cfg is constructed in-process so the resolved db_path can be injected via `set_main_option('sqlalchemy.url', ...)` without env-var dance.
- **Distinct doctor exit codes 2..7** — operator-friendly: launchd or a future CI gate can branch on the code without parsing stderr. Code 1 reserved for Typer's own runtime errors so we don't conflict.
- **Logging-recovery pattern lives in doctor.py, NOT env.py** — env.py ships from plan 00-03 and modifying it now would silently change behavior for non-doctor alembic invocations (e.g., `uv run alembic upgrade head` directly). Localizing the scar to doctor.py keeps env.py's contract stable.
- **Click 8.3 / Typer 0.25 CliRunner change handled** — dropped `mix_stderr=False`; stderr is always separated via `result.stderr` in current Click. Worth recording because plan text predates the Click change.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] doctor_passed log line silently dropped after alembic.command.upgrade**
- **Found during:** Task 3 (`test_doctor_emits_doctor_passed_log` failure)
- **Issue:** alembic's `migrations/env.py` calls `logging.config.fileConfig(alembic.ini)` at import time. fileConfig defaults to `disable_existing_loggers=True`, which (a) wipes the stdlib root handlers attached by `configure_logging`, (b) lowers root level to WARNING per `alembic.ini`'s `[logger_root] level=WARNING`, and (c) sets `disabled=True` on every previously-named stdlib logger including `"doctor"`. Result: every event after `alembic_command.upgrade(...)` — including `doctor_passed` — went to /dev/null. Phase 0 SC2 explicitly requires the structured success log; this was a correctness bug, not a feature gap.
- **Fix:** After `alembic_command.upgrade` returns successfully, doctor.py now (1) iterates `logging.Logger.manager.loggerDict` and re-enables every Logger instance, (2) calls `structlog.reset_defaults()` to drop the `cache_logger_on_first_use=True` cached `BoundLogger`s, (3) clears `ls_equity_fund.logging._CONFIGURED` and re-runs `configure_logging`, (4) re-binds `run_id` and re-fetches the doctor logger.
- **Files modified:** `src/ls_equity_fund/cli/doctor.py`
- **Verification:** `test_doctor_emits_doctor_passed_log` now passes; manual `uv run meridian doctor` against shipped examples shows `doctor_passed` JSON event in `logs/{UTC-date}.jsonl`.
- **Committed in:** `612ea22` (separate `fix(00-06)` commit so the regression is isolated in `git log` if a future plan re-litigates the alembic-fileConfig pattern).

**2. [Rule 3 — Blocking] Tests could not import `pytest` until dev extras were synced**
- **Found during:** Task 1 (running RED smoke test).
- **Issue:** `uv run pytest ...` failed with "Failed to spawn pytest" because the venv was synced with runtime-only dependencies. Tests are dev-extras.
- **Fix:** `uv sync --extra dev` once at the start of execution; all subsequent `uv run pytest` invocations resolve normally.
- **Files modified:** none (uv.lock already pins pytest 9.0.3 + pytest-asyncio 1.3.0 + freezegun 1.5.5 + responses 0.26.0 + ruff 0.15.12; `--extra dev` materialized them).
- **Verification:** `uv run pytest --version` returns 9.0.3.
- **Committed in:** N/A (environmental setup, not a code change).

**3. [Plan-structure deviation] Tasks 1 and 2 merged into a single `feat` commit**
- **Found during:** Task 1 commit step.
- **Issue:** Plan 00-06 separates "Task 1: Build the Typer app + doctor command + module entry" from "Task 2: Implement seven stub subcommands". But `cli/app.py` (Task 1) imports from `cli/stubs.py` (Task 2) — committing Task 1 alone would leave HEAD in a state where `from ls_equity_fund.cli.app import app` fails with ImportError, breaking other tests that already import the package (e.g., the parallel `test_seams.py`/`test_paper_broker.py` runs).
- **Fix:** Wrote both files in the same logical step and committed them together as `feat(00-06): add Typer CLI app + doctor command + 7 stub subcommands` (`c2ce9df`). Atomic commit preserves importability of the repo at every commit point.
- **Files modified:** `src/ls_equity_fund/cli/{app,doctor,stubs,__init__,__main__}.py`
- **Verification:** Per-commit importability — `git checkout c2ce9df && uv run python -c "from ls_equity_fund.cli.app import app"` succeeds.
- **Committed in:** `c2ce9df`.

**4. [Click 8.3 API drift] Removed `mix_stderr=False` kwarg from CliRunner**
- **Found during:** Task 3 (test collection ERROR).
- **Issue:** Plan text uses `CliRunner(mix_stderr=False)`. Click 8.3.3 (installed via `uv sync`) removed the kwarg; stderr is always separated now via `result.stderr`.
- **Fix:** Use `CliRunner()` (no kwargs); `result.stderr` works as before.
- **Files modified:** `tests/unit/test_cli_doctor.py`, `tests/unit/test_cli_stubs.py`
- **Verification:** All 20 cli tests collect and pass.
- **Committed in:** `5dc30e6`.

---

**Total deviations:** 4 — 1 bug fix (Rule 1), 1 blocker (Rule 3), 1 atomic-commit merge, 1 upstream-API drift.
**Impact on plan:** No scope creep. Bug fix (#1) was load-bearing for Phase 0 SC2's audit-log requirement and would have masked an INFRA-08-relevant log-loss in production. The other three are environmental/cosmetic.

## Issues Encountered

- **Pre-existing failing tests in `tests/unit/test_migrations.py`** (4 tests). Verified by stashing 00-06 changes — failures are present on commit `c2ce9df`'s parent state. Not caused by 00-06; tracked in `.planning/phases/00-foundation/deferred-items.md` for a follow-up plan.
- **Vercel plugin auto-suggested skills** (bootstrap, env-vars, vercel-services) on `pyproject.toml` and `.env.example` reads. These were ignored — this is a Python CLI hedge-fund system with no Vercel/Next.js deployment surface; the basename matches were coincidental. No action taken; no deferral needed (the suggestions are advisory only).

## TDD Gate Compliance

This plan is `type: execute` (per frontmatter), not `type: tdd`, so plan-level TDD gates do not apply. However, individual tasks marked `tdd="true"` (Task 1, Task 3) followed RED → GREEN at the task level:
- Task 1 RED commit: `c6098fc` (`test`)
- Task 1 GREEN commit: `c2ce9df` (`feat`)
- Task 3 GREEN commit: `5dc30e6` (`test`) — tests written together with bug-fix `612ea22` (`fix`) catching a regression introduced by integration with alembic.

## Test Verification Snapshot

```
$ rm -f cache/ls_equity_fund.db && rm -rf logs/
$ uv run pytest tests/unit/test_cli_doctor.py tests/unit/test_cli_stubs.py tests/unit/test_cli_smoke.py -v
collected 22 items
tests/unit/test_cli_doctor.py ......... [9/9 pass]
tests/unit/test_cli_smoke.py ..        [2/2 pass]
tests/unit/test_cli_stubs.py ...........[11/11 pass]
============================== 22 passed in 0.34s ==============================

$ uv run meridian --help | grep -E "^[^a-z]+(doctor|daily-refresh|run-data|run-scoring|run-analysis|run-portfolio|run-execution|run-reporting)\b" | wc -l
8
```

## User Setup Required

None — no external service configuration introduced by this plan. (`.env` already requires `ANTHROPIC_API_KEY` + `SEC_USER_AGENT` from plan 00-02; doctor verifies their presence but does not set them.)

## Next Phase Readiness

- **Phase 0 SC2 closure note:** With this plan, all four Phase 0 success criteria are met (uv-managed Python 3.11+; `meridian doctor` smoke; three ABCs + PaperBroker; `.gitignore` + structlog redaction). Phase 0 is ready for `/gsd-transition` to Phase 1.
- **Phase 1 entry point:** `meridian run-data` is the stub Phase 1 will fill. Its CLI surface (`--no-filings`, `--no-13f`, `--ticker`) is locked; Phase 1 only needs to replace the body.
- **Phase 10 launchd plist** (deferred) will invoke `uv run meridian daily-refresh --no-filings --no-13f` — that command + flag combo already parses today (verified by `test_daily_refresh_stub_accepts_flags`).

## Self-Check: PASSED

All 9 source/test files claimed created exist on disk (verified with `[ -f ]`).
All 4 commits claimed exist in git log (`c6098fc`, `c2ce9df`, `612ea22`, `5dc30e6`).
Plan SUMMARY itself exists at `.planning/phases/00-foundation/00-06-SUMMARY.md`.
The 22 tests (`test_cli_smoke.py` + `test_cli_doctor.py` + `test_cli_stubs.py`) all pass; `uv run meridian --help` lists all 8 subcommands; `uv run meridian doctor` exits 0 against shipped `config.yaml.example` + `.env.example` (after copy).

---

*Phase: 00-foundation*
*Completed: 2026-05-04*
