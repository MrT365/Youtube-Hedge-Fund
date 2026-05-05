---
phase: 01-data-infrastructure-l1
plan: 02
subsystem: data
tags: [universe, pit, survivorship, sp500, liquid_us, scanner_seed, wikipedia, yfinance, sqlite, alembic, cp1]

# Dependency graph
requires:
  - phase: 00-foundation
    provides: Config + DataConfig + load_config + structlog + alembic env.py + db.get_connection / db.get_db_path
  - phase: 01-data-infrastructure-l1 (Plan 01-01)
    provides: universe table schema (first_seen_date, delisted_date, inclusion_window) in migrations/0002 + provider ABCs (data/providers/base.py)
provides:
  - LiquidUSConfig pydantic sub-config (exchanges, min_price, min_avg_dollar_volume_20d, min_market_cap)
  - DataConfig.scanner_seed_tickers (50-ticker default; 10 GICS sectors x 5 mega-caps)
  - ls_equity_fund.data.universe.build_universe(config, *, mode, conn, today, fixture_html_path) -> int
  - ls_equity_fund.data.universe.merge_universe_pit(rows, conn, today) -> {inserted, updated, delisted, reincluded}
  - Three universe modes wired: sp500 (Wikipedia pd.read_html), liquid_us (daily_prices ADV scanner with seed fallback), scanner_seed
  - PIT-correctness contract: delisted tickers FLAGGED (delisted_date set), never DELETEd (binds CP1 / SC1)
  - tests/fixtures/sp500_wikipedia_fixture.html (5-row Wikipedia constituents fixture)
affects:
  - 01-03 (benchmarks ingestion uses universe sector tagging)
  - 01-04 (OHLCV ingestion targets universe.ticker; ADV computation closes the liquid_us-fallback loop)
  - 01-05..01-08 (fundamentals / filings / 13F / short-interest all read tickers from universe with PIT filter)
  - 02-* (factor scoring joins on universe.sector for sector-percentile rank)
  - all backtest / attribution work — PIT query convention is now load-bearing

# Tech tracking
tech-stack:
  added:
    - pd.read_html (Wikipedia parsing — uses lxml + bs4 already pinned by edgartools transitively)
  patterns:
    - "PIT-aware merge: pre-fetch existing rows once, then INSERT/UPDATE/UPDATE-as-delisted in a single BEGIN/COMMIT — no DELETE statements ever appear in the universe code path"
    - "Test injection seam: build_universe accepts fixture_html_path so sp500-mode tests are hermetic and never hit Wikipedia"
    - "yfinance enrichment is best-effort: yf.Ticker(t).info failures log+continue with empty info; tickers are still included with sector='unknown' (preserves survivorship over data-availability)"
    - "Configurable seed list pattern: scanner_seed_tickers in DataConfig + config.yaml.example documents both the default and the 10-sector grouping"

key-files:
  created:
    - src/ls_equity_fund/data/universe.py
    - tests/unit/data/test_universe.py
    - tests/fixtures/__init__.py
    - tests/fixtures/sp500_wikipedia_fixture.html
  modified:
    - src/ls_equity_fund/config.py (LiquidUSConfig + DataConfig.liquid_us + DataConfig.scanner_seed_tickers)
    - src/ls_equity_fund/data/__init__.py (re-export build_universe + merge_universe_pit)
    - config.yaml.example (liquid_us thresholds + scanner_seed_tickers list)

key-decisions:
  - "10 GICS sectors x 5 mega-caps = 50 in scanner_seed_tickers (XLRE dropped) — resolves plan-prose math contradiction (5 x 11 = 55) against the firm 50-ticker constraint"
  - "Wikipedia is the only canonical S&P-500 source pulled via pd.read_html(match='Symbol'); yfinance does NOT provide a constituents feed (PITFALLS D1)"
  - "liquid_us mode falls back to scanner_seed when daily_prices is empty — first-run case before Plan 04 ships OHLCV; structlog warning narrates the fallback"
  - "Re-listed tickers preserve original first_seen_date and clear delisted_date back to NULL with inclusion_window reset to '{first_seen}:current' — the cleanest reconstructable history without an audit-trail table"
  - "BRK.B / BF.B and similar dotted symbols are normalized to BRK-B / BF-B (yfinance ticker convention) at parse time, not at query time — single source of truth"

patterns-established:
  - "merge_universe_pit returns a structured per-action stats dict ({inserted, updated, delisted, reincluded}) — caller logs it via structlog so daily-run audit trail is self-narrating"
  - "Tests use migrated_conn fixture (alembic upgrade head against tmp_path SQLite) — same pattern as test_migrations.py; reusable for every Phase 1 ingestion test"
  - "PIT query convention encoded in module docstring: WHERE first_seen_date <= D AND (delisted_date IS NULL OR delisted_date > D) — every downstream query must use this pattern"

requirements-completed: [DATA-01, DATA-13]

# Metrics
duration: ~25min
completed: 2026-05-04
---

# Phase 01 Plan 02: Universe Construction + PIT Merge Summary

**Three-mode universe builder (sp500 / liquid_us / scanner_seed) with PIT-aware merge that flags delisted tickers (delisted_date set, inclusion_window updated) and never DELETEs — binds CP1 / SC1 survivorship-bias prevention.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-04T19:35:30Z (approx)
- **Completed:** 2026-05-04T20:00:54Z
- **Tasks:** 2
- **Files created:** 4
- **Files modified:** 3

## Accomplishments

- LiquidUSConfig pydantic sub-config + DataConfig.scanner_seed_tickers (50-ticker default across 10 GICS sectors)
- `build_universe(config, mode=...)` dispatches to sp500 (Wikipedia), liquid_us (daily_prices ADV scanner), or scanner_seed; falls back gracefully when daily_prices is empty
- `merge_universe_pit(rows, conn, today)` PIT-aware merge: INSERT new tickers with first_seen_date=today; UPDATE existing tickers' metadata (preserving first_seen_date); FLAG tickers absent from incoming rows with delisted_date=today; clear delisted_date when a previously-delisted ticker reappears
- Wikipedia HTML fixture + 6 unit tests, including the load-bearing `test_merge_flags_delisted_does_not_delete` survivorship-simulation test (CP1 / SC1 binding contract)
- All 131 tests in the suite pass (Phase 0 + 01-01 + the 6 new tests); zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend DataConfig with universe-specific fields + ship config.yaml defaults** - `65b76ec` (feat)
2. **Task 2: Universe builder module with PIT-aware merge + 3 modes + survivorship test** - `1688180` (feat)

## Files Created/Modified

- `src/ls_equity_fund/data/universe.py` (created) — `build_universe` + `merge_universe_pit` + three private mode builders (`_build_sp500`, `_build_liquid_us`, `_build_scanner_seed`) + yfinance enrichment helper
- `src/ls_equity_fund/data/__init__.py` (modified) — re-export `build_universe` + `merge_universe_pit`
- `src/ls_equity_fund/config.py` (modified) — `LiquidUSConfig` sub-config; `DataConfig.liquid_us` + `DataConfig.scanner_seed_tickers` fields; `LiquidUSConfig` added to `__all__`
- `config.yaml.example` (modified) — `data.liquid_us` block + `data.scanner_seed_tickers` 50-line list with sector-group comments
- `tests/unit/data/test_universe.py` (created) — 6 tests covering all 3 modes + 4 PIT-correctness scenarios
- `tests/fixtures/__init__.py` (created) — empty package marker
- `tests/fixtures/sp500_wikipedia_fixture.html` (created) — 5-row Wikipedia-style constituents table for hermetic sp500-mode tests

## Decisions Made

- **scanner_seed_tickers shipped at 50 (10 sectors x 5), not 55 (11 sectors x 5).** The plan prose said "50-ticker seed list" but enumerated "5 each from 11 sectors" (= 55). Acceptance check `assert len == 50` made the firm intent clear — XLRE (Real Estate, ~3% S&P weight, lowest sector-ETF liquidity) was dropped. Rationale captured inline in the source comment so future readers see the math contradiction was intentional.
- **liquid_us seed fallback narrated via structlog.warning, not a hard error.** The orchestrator (Plan 09) sequences Plan 04 (OHLCV) before Plan 02 inside Wave 2's intra-wave ordering, so under steady-state the fallback is rare; but on a fresh DB or after a cache wipe, falling back to scanner_seed is correct (universe must build something to unblock downstream factors).
- **Dotted-symbol normalization (BRK.B → BRK-B) happens at parse time in `_build_sp500`.** yfinance uses dashes for share classes; doing the swap once at ingest avoids per-call normalization in every downstream module.
- **yfinance failure is "include with sector=unknown", not "exclude"** — survivorship-of-coverage is preserved (a ticker we know exists but can't classify shouldn't vanish from the universe just because Yahoo's bot detection blocked us this run).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] scanner_seed_tickers length mismatch (plan-internal contradiction)**
- **Found during:** Task 1 verification (`assert len(c.data.scanner_seed_tickers) == 50` failed with `got 55`)
- **Issue:** Plan said "50-ticker seed list... 5 each from XLK/XLF/XLV/XLE/XLI/XLC/XLY/XLP/XLB/XLRE/XLU" — 11 sectors × 5 = 55 ≠ 50. The plan's prose count and breakdown contradicted; the acceptance test demanded 50.
- **Fix:** Dropped the XLRE (Real Estate) sector to ship 10 sectors × 5 = exactly 50 tickers. Updated both `src/ls_equity_fund/config.py` default_factory list and `config.yaml.example` YAML list. Documented the math reconciliation in a comment above the default_factory so the deviation is visible at the source-of-truth.
- **Files modified:** `src/ls_equity_fund/config.py`, `config.yaml.example`
- **Verification:** `assert len(c.data.scanner_seed_tickers) == 50` now passes; Phase 0 smoke 25/25 still green.
- **Committed in:** `65b76ec` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 plan-internal contradiction resolved as Rule 1 bug)
**Impact on plan:** Single deviation resolved a self-contradicting plan spec; no scope creep, no behavioral surprises downstream. The dropped sector (Real Estate) can be added back in a future plan if the seed list expands; the choice was documented so future operators see why XLRE is absent.

## Issues Encountered

None of consequence. The lone wrinkle was the 50-vs-55 ticker count, handled as an auto-fix above. yfinance was not exercised at unit-test time (test seam injects fixtures), so no rate-limit/bot-detection issues surfaced; those will be exercised when Plans 04+ wire real ingestion.

## TDD Gate Compliance

This plan's tasks carried `tdd="true"` markers but ship as a single coherent feature where the test fixture (Wikipedia HTML) is the contract gate; the test file and the implementation were written together and committed together (`1688180`) rather than as separate RED/GREEN commits. Both task verify-gates explicitly run pytest (`uv run pytest tests/unit/data/test_universe.py -v` reports 6 passed) and the survivorship test (`test_merge_flags_delisted_does_not_delete`) is the load-bearing assertion. No `test(...)` standalone commit precedes the feat — recording this as a TDD-cycle deviation for verifier visibility.

## Threat Surface Scan

No new security-relevant surface beyond the threat model already catalogued in the PLAN:
- T-01-05 (Wikipedia HTML structure changes) — mitigated via `match="Symbol"` in `_build_sp500` and the fixture-based unit test that catches column-shape regressions.
- T-01-07 (yfinance bot-detection bans) — mitigated by `try/except` around `yf.Ticker(t).info` in `_enrich_with_yfinance`; warnings logged via structlog, tickers still included with `sector='unknown'`.
- T-01-08 (survivorship integrity) — bound by `test_merge_flags_delisted_does_not_delete`: the row count is asserted unchanged when a ticker disappears from the source, and `grep -c "DELETE FROM universe"` returns 0.

## Known Stubs

None. `build_universe` ships fully wired across all three modes; the only conditional fallback (liquid_us → scanner_seed when daily_prices is empty) is a documented graceful-degradation path, not a stub.

## User Setup Required

None — this plan ships entirely under-the-hood library code + tests. No external services, no env-var changes, no dashboard configuration. The first time an operator runs the data layer, the universe will populate from `config.data.universe_mode` (default `liquid_us`, which falls back to `scanner_seed`'s 50 mega-caps until OHLCV lands).

## Next Phase Readiness

- **Plan 01-03 (benchmarks):** the universe `sector` column is populated; benchmark/sector-ETF/macro tagging can join on it cleanly.
- **Plan 01-04 (OHLCV):** can target every ticker in `universe` (filter `WHERE delisted_date IS NULL` for active set, or use the PIT convention for historical backfills).
- **Plans 01-05..01-08 (fundamentals / filings / 13F / short-interest):** all read tickers from `universe` with the PIT-filter pattern; the contract is in place.
- **Phase 2 (factor scoring):** can rely on `sector` being non-NULL on every row (yfinance fallback uses `'unknown'` rather than NULL — sector-percentile rank can either include or skip the unknown bucket; design choice deferred to factor implementation).

## Self-Check: PASSED

**Files verified to exist:**
- FOUND: src/ls_equity_fund/data/universe.py
- FOUND: src/ls_equity_fund/config.py (LiquidUSConfig + scanner_seed_tickers present)
- FOUND: src/ls_equity_fund/data/__init__.py (build_universe + merge_universe_pit re-exports)
- FOUND: config.yaml.example (liquid_us + scanner_seed_tickers blocks present)
- FOUND: tests/unit/data/test_universe.py
- FOUND: tests/fixtures/__init__.py
- FOUND: tests/fixtures/sp500_wikipedia_fixture.html

**Commits verified to exist:**
- FOUND: 65b76ec (Task 1: extend DataConfig)
- FOUND: 1688180 (Task 2: universe builder + PIT merge + tests)

**Test gate verified:**
- `uv run pytest tests/unit/data/test_universe.py -v` → 6 passed
- `uv run pytest -q` (full suite) → 131 passed, 3 deprecation warnings (edgartools internal — pre-existing, not introduced here)
- `grep -c "DELETE FROM universe" src/ls_equity_fund/data/universe.py` → 0 (CP1 binding intact)

---
*Phase: 01-data-infrastructure-l1*
*Completed: 2026-05-04*
