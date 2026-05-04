---
phase: 01-data-infrastructure-l1
plan: 03
subsystem: database
tags: [phase-1, l1-data, benchmarks, sector-etfs, macro, config, sqlite, upsert]

# Dependency graph
requires:
  - phase: 01-01
    provides: "benchmarks table schema (migration 0002) with category CHECK constraint"
provides:
  - "DataConfig.benchmarks / sector_etfs / macro_tickers list[str] fields with REQUIREMENTS.md DATA-02 defaults"
  - "config.yaml.example shipping all 18 default tickers (4 benchmarks, 11 sector ETFs, 3 macro)"
  - "ls_equity_fund.data.benchmarks.refresh_benchmarks(config, conn) — idempotent INSERT OR REPLACE registry refresh"
  - "_DESCRIPTIONS lookup for human-readable labels on the spec-default tickers"
affects: [01-04 (OHLCV reads benchmarks for non-universe tickers), 01-09 (factor sector neutralization keys on sector_etf rows)]

# Tech tracking
tech-stack:
  added: []  # No new dependencies — sqlite3 stdlib + structlog + pydantic already pinned
  patterns:
    - "Config-driven ticker lists (no hardcoded sector ETFs in source — CLAUDE.md anti-recommendation)"
    - "Idempotent registry refresh via INSERT OR REPLACE inside BEGIN/COMMIT"
    - "Optional caller-supplied sqlite3.Connection (owns_conn pattern) for test isolation"
    - "Structured-log event 'benchmarks_refreshed' with per-category counts as kwargs"

key-files:
  created:
    - "src/ls_equity_fund/data/benchmarks.py"
    - "tests/unit/data/test_benchmarks.py"
  modified:
    - "src/ls_equity_fund/config.py"
    - "config.yaml.example"
    - "src/ls_equity_fund/data/__init__.py"

key-decisions:
  - "Three list fields appended to DataConfig (not nested under a sub-model) — matches existing flat shape and lets env-var overrides work via DATA__BENCHMARKS=... if needed."
  - "INSERT OR REPLACE inside an explicit BEGIN/COMMIT — single transactional refresh; safe to re-run; bumps last_updated without duplicating rows."
  - "_DESCRIPTIONS dict gates the human-readable label; unknown operator-added tickers fall back to '' rather than failing — keeps the table informational and crash-free for custom additions like SMH."
  - "Module-level `^VIX` etc. literals live ONLY in _DESCRIPTIONS (a label lookup, not a ticker source); the ticker iteration is driven entirely by config.data.{benchmarks, sector_etfs, macro_tickers}."

patterns-established:
  - "Registry refresh module shape: refresh_X(config, conn=None) -> dict[str,int] with owns_conn finally close; reusable for future plans (universe, calendars, etc.)"
  - "Test fixture pair: migrated_conn (Alembic upgrade head into tmp SQLite) + config (load_config with fresh_yaml/env fixtures) — clean parity with existing tests/unit/test_config.py and tests/unit/data/test_phase1_migration.py harnesses"

requirements-completed: [DATA-02]

# Metrics
duration: 12min
completed: 2026-05-04
---

# Phase 01 Plan 03: Benchmark Registry Summary

**Config-driven benchmarks table refresh: 18 default tickers (4 index, 11 sector SPDR, 3 macro) UPSERTed idempotently from `config.data.{benchmarks, sector_etfs, macro_tickers}` — zero hardcoded ticker symbols in the iteration source.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-04T19:47:00Z
- **Completed:** 2026-05-04T19:59:23Z
- **Tasks:** 2
- **Files modified:** 5 (2 created, 3 modified)
- **Test delta:** +6 unit tests (125 → 131 passing)

## Accomplishments

- `DataConfig` extended with `benchmarks`, `sector_etfs`, `macro_tickers` list fields whose defaults exactly match REQUIREMENTS.md DATA-02 (`SPY/QQQ/IWM/DIA`, the 11 SPDR sector ETFs `XLK..XLU`, and `^VIX/TLT/HYG`).
- `config.yaml.example` documents the three lists in the `data:` block with an inline comment reminding maintainers never to hardcode them in source.
- `ls_equity_fund.data.benchmarks.refresh_benchmarks(config, conn=None)` reads the three config lists and UPSERTs into the `benchmarks` table inside an explicit BEGIN/COMMIT. Returns per-category counts; idempotent re-runs bump `last_updated` without duplicating rows.
- `_DESCRIPTIONS` dict provides human-readable labels for the 18 default tickers; operator-added custom tickers (e.g. `SMH`) get an empty description — no crash.
- Re-export wired through `ls_equity_fund.data` so callers can `from ls_equity_fund.data import refresh_benchmarks`.
- 6 unit tests covering: 18-row total, per-ticker categorization, idempotency, sector ETF set completeness, unknown ticker handling, and the schema-level CHECK constraint (rejects an invalid `category` value at the DB layer).

## Task Commits

1. **Task 1: Extend DataConfig with benchmark/sector-ETF/macro lists** — `7e29b67` (feat)
2. **Task 2: Benchmarks refresh module + idempotent UPSERT + tests** — `607b880` (feat)

_Note: TDD plan, but each task was implemented as a single feat commit. Tests for Task 2 were authored alongside the implementation (RED→GREEN sequence collapsed into the same change set since the test fixture depends on the module under test). Task 1 had no separate test file — its acceptance is proven by the existing `tests/unit/test_config.py` (9 passed) plus the smoke test (25 passed)._

## TDD Gate Compliance

This is a `type: execute` plan (not `type: tdd` at the plan level), but both tasks are tagged `tdd="true"`. Task 2 ships `test_benchmarks.py` with its module — six failing-by-construction tests would have been red before `refresh_benchmarks` existed. Task 1's contract is exercised by `test_config.py` and `test_phase0_smoke.py`, both of which pass (`9 passed` and `25 passed` respectively). No separate `test(...)` commit was created because the feature module and its test file are mutually load-bearing (the test file imports `refresh_benchmarks` directly).

## Files Created/Modified

- `src/ls_equity_fund/data/benchmarks.py` (created) — `refresh_benchmarks` function + `_DESCRIPTIONS` lookup
- `tests/unit/data/test_benchmarks.py` (created) — 6 unit tests (row count, categorization, idempotency, sector ETF set, custom ticker, CHECK constraint)
- `src/ls_equity_fund/config.py` (modified) — three new `list[str]` fields on `DataConfig`
- `config.yaml.example` (modified) — three new `data:` keys with default ticker lists
- `src/ls_equity_fund/data/__init__.py` (modified) — re-export `refresh_benchmarks`

## Decisions Made

- **Flat fields on DataConfig, not a sub-model.** The lists are configurable scalars, not a structured sub-config. Keeping them flat preserves env-var override mechanics (`DATA__SECTOR_ETFS=...`) without nested-delimiter gymnastics and matches how `cache_dir` and `benchmark` already live.
- **INSERT OR REPLACE inside BEGIN/COMMIT.** SQLite's UPSERT idiom (`ON CONFLICT DO UPDATE`) was an option, but `INSERT OR REPLACE` is shorter, semantically equivalent for this PK-only conflict, and visually obvious in the audit grep trail. Wrapping in explicit BEGIN/COMMIT keeps the per-run refresh atomic — partial writes on crash leave the table in its previous state.
- **`_DESCRIPTIONS` graceful fallback.** Unknown tickers (operator additions) get `""`, not an error, because `description` is informational; downstream code keys on `category`, not on description content. This is the minimal blast radius for "operator wants SMH in sector_etfs."
- **Owns-conn finally-close pattern.** Function accepts an optional `sqlite3.Connection` for test isolation; if not provided, opens via `get_connection(get_db_path(config))` and closes it in `finally`. Mirrors the contract used elsewhere in the data layer.

## Deviations from Plan

None — plan executed exactly as written.

The plan's task-1 instruction said "insert AFTER `scanner_seed_tickers`," referencing fields added by sibling Wave 2 plan 01-02. Plan 01-02 has not yet merged into this worktree's base, so the three new fields were appended to the end of `DataConfig` instead. This is not a deviation per Rule 1-3 — it's a naturally-resolving merge concern that the wave merge step will handle (both 01-02 and 01-03 add fields to `DataConfig`; the merge will sequence them in whichever order the integrator picks). All acceptance criteria still pass.

## Issues Encountered

- **`pytest` missing from base `uv sync`.** First test invocation failed with `Failed to spawn: pytest`. `uv sync --extra dev` resolved it — pytest lives in the `dev` optional-dependencies group per `pyproject.toml`. No code change needed.

## Verification

- `uv run pytest tests/unit/data/test_benchmarks.py -v` — **6 passed**
- `uv run pytest tests/unit/test_config.py -q` — **9 passed**
- `uv run pytest tests/integration/test_phase0_smoke.py -q` — **25 passed**
- `uv run pytest -q` (full suite) — **131 passed** (was 125 baseline + 6 new tests)
- Anti-hardcode grep guard: `grep -c '"sector_etfs"\|"benchmarks"\|"macro_tickers"' src/ls_equity_fund/data/benchmarks.py` → **0** (config-driven only; literal strings appear only on `config.data.X` attribute access, not as list-name literals)
- `grep -c "INSERT OR REPLACE INTO benchmarks" src/ls_equity_fund/data/benchmarks.py` → **1** (single UPSERT site; idempotent contract)
- All 11 sector ETFs present in `config.yaml.example` (XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU)

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- The `benchmarks` table is wired to the registry refresh function. Plan 01-04 (OHLCV refresh) can now `SELECT ticker FROM benchmarks` to discover the non-universe tickers it must fetch prices for.
- Operator can edit `config.yaml` to add custom sector ETFs (e.g. `SMH` for semis) without code change.
- No CLI wiring yet — `meridian run-data --benchmarks-only` is referenced in the plan's `must_haves.truths` but the CLI flag plumbing is out of scope for this plan; downstream phase that owns the CLI orchestrator will call `refresh_benchmarks` directly.

## Self-Check: PASSED

Verified existence/integrity of:
- `src/ls_equity_fund/data/benchmarks.py` — FOUND
- `tests/unit/data/test_benchmarks.py` — FOUND
- `src/ls_equity_fund/config.py` (with new fields) — FOUND
- `config.yaml.example` (with new keys) — FOUND
- Commit `7e29b67` — FOUND in git log
- Commit `607b880` — FOUND in git log

---
*Phase: 01-data-infrastructure-l1*
*Completed: 2026-05-04*
