---
phase: 01-data-infrastructure-l1
plan: 05
subsystem: l1-data-fundamentals
tags: [phase-1, l1-data, fundamentals, ratios, d2-mitigation, pit-aware, yfinance]

# Dependency graph
requires:
  - phase: 01-data-infrastructure-l1
    plan: 01
    provides: fundamentals + fundamental_ratios tables (migration 0002), append-only PK with as_of_ingest_date, FundamentalsProvider ABC, YFinanceProvider stub class
  - phase: 01-data-infrastructure-l1
    plan: 04
    provides: prices.refresh_prices orchestrator pattern (ThreadPoolExecutor + log+continue + refresh_state persistence), YFinanceProvider with stubbed get_fundamentals
provides:
  - "YFinanceProvider.get_fundamentals(ticker) — annual+quarterly DataFrame, MultiIndex(period_end, period_type), columns matching migration 0002 fundamentals schema"
  - "src/ls_equity_fund/data/fundamentals.py refresh_fundamentals(config, conn, *, tickers, today, provider) — append-only ingest with today's as_of_ingest_date, ThreadPoolExecutor fan-out, log+continue on YFinanceError, refresh_state persistence per ticker"
  - "src/ls_equity_fund/data/ratios.py compute_ratios(ticker, asof, conn) + compute_all_ratios(conn, asof) — 24 derived ratios per DATA-04 with PIT-aware reads (latest as_of_ingest_date <= asof, period_end <= asof) and _safe_div guard returning None on missing inputs / zero denominator"
  - "D2 mitigation binding test (test_d2_mitigation_appends_on_restated_rerun): restated yfinance values write a NEW row with the new ingest date, the original row is preserved — PIT replay can choose the as-of view"
  - "D2 PIT read binding test (test_pit_aware_uses_latest_as_of_ingest): compute_ratios reads the latest as_of_ingest_date <= asof, so an April 10 ratio computation sees the April-1 ingest while a May 1 computation sees the April-15 restatement"
affects: [phase-01-wave-2, plan-01-06-filings, phase-02-factor-engine, phase-03-claude-analyzers, phase-08-streamlit-fundamentals-page]

# Tech tracking
tech-stack:
  added:
    - "yfinance Ticker.income_stmt + .balance_sheet + .cashflow (annual) and .quarterly_* variants — extracted via _YF_LABEL_MAP that translates yfinance row labels (\"Total Revenue\", \"Net Income\", ...) to schema column names (\"revenue\", \"net_income\", ...)"
    - "tenacity retry on the get_fundamentals_impl entry point (3 attempts, exponential 1/2/4s, reraise) — same policy as the OHLCV path established in plan 01-04"
  patterns:
    - "Append-only PIT ingest (D2 binding): every refresh writes (ticker, period_end, period_type, today_as_of_ingest_date) via INSERT OR IGNORE. Same-day reruns are no-ops; later-day reruns with restated yfinance values land a new row alongside the original; downstream readers pick MAX(as_of_ingest_date) per (ticker, period_end, period_type) bounded by the asof query date."
    - "PIT-aware ratio reads: ratios.py uses a CTE that computes MAX(as_of_ingest_date) per period_end with a hard ceiling of asof_str on BOTH as_of_ingest_date and period_end, so an April-10 asof never sees an April-15 restatement."
    - "yfinance row-label drift isolation: _YF_LABEL_MAP is a single module-level dict; future yfinance label changes touch one dict entry, not every consumer. Plan 10 integration test will surface drift before it reaches production data."
    - "_safe_div guard pattern: every ratio (27 call sites) routes division through _safe_div, which returns None on (a) None inputs, (b) NaN inputs, (c) zero denominator. Result: ratios table never contains inf/nan, downstream factor code can branch cleanly on None."
    - "Yields use shares × close as market_cap proxy (NOT yfinance-reported market cap) — preserves the data interface seam: paid feeds (Polygon/Tiingo) provide shares + close, market cap is computed."
    - "Sign-flip yields: yfinance reports dividends_paid and buybacks as NEGATIVE cash outflows; ratios.py multiplies by -1 so dividend_yield and buyback_yield are reported as POSITIVE percentages, matching factor-model convention."
    - "YoY/QoQ growth uses q0-vs-q4 / q0-vs-q1 indexing on a list ordered DESC by period_end; with <5 quarters available the YoY falls back to q0-vs-q1 on the annual list, matching the requirement for new IPOs that lack 4-quarter history."
    - "Ratios table is INSERT OR REPLACE keyed by (ticker, asof_date) — derived snapshot, not historical. The historical record (D2-binding) lives in fundamentals keyed by as_of_ingest_date; ratios are a daily-recomputed view of it."

key-files:
  created:
    - "src/ls_equity_fund/data/providers/yfinance_provider_fundamentals.py"
    - "src/ls_equity_fund/data/fundamentals.py"
    - "src/ls_equity_fund/data/ratios.py"
    - "tests/unit/data/test_fundamentals_ingest.py"
    - "tests/unit/data/test_ratios.py"
  modified:
    - "src/ls_equity_fund/data/providers/yfinance_provider.py"  # get_fundamentals stub replaced with delegation to get_fundamentals_impl
    - "src/ls_equity_fund/data/__init__.py"  # re-export refresh_fundamentals + compute_ratios + compute_all_ratios

key-decisions:
  - "Append-only refresh (D2 mitigation) — every fetch writes today's as_of_ingest_date; PK from migration 0002 makes INSERT OR IGNORE same-day idempotent and later-day-after-restatement appends a new row. Pinned by test_d2_mitigation_appends_on_restated_rerun."
  - "Ratios stored separately in fundamental_ratios — recomputed every refresh from latest fundamentals snapshot + latest close (yields depend on price which moves daily). Keeps fundamentals reproducible from yfinance alone."
  - "yfinance row labels mapped via single module-level _YF_LABEL_MAP dict — easy maintenance when yfinance drifts (CLAUDE.md flags 0.2.6x churn); integration test in plan 10 surfaces drift."
  - "get_fundamentals_impl is a function not a mixin — simpler to test (mock the session, call directly) and the YFinanceProvider just delegates one line."
  - "compute_ratios accepts asof:date and reads close from daily_prices on/before asof — keeps the function testable without freezegun (tests pass an explicit date) and PIT-correct for v2 backtest replay."
  - "YoY growth prefers quarterly (q0 vs q4) over annual (yr0 vs yr1) — quarterly captures four-quarter rolling momentum; annual is the fallback when fewer than 5 quarters are available."
  - "_OUTPUT_COLS list is the source of truth for column order — both compute_all_ratios INSERT and the test_24_output_cols_match_spec count guard depend on it; reordering is a single-place change."

decision-records:
  []

deviations: []

patterns-established:
  - "yfinance fundamentals extraction pattern: getattr(Ticker, attr) for the 6 income/balance/cash properties, label-translate via _YF_LABEL_MAP, merge into a per-period dict, build DataFrame with MultiIndex(period_end, period_type), reindex on SCHEMA_COLS so missing yfinance fields land as None, not absent columns."
  - "Append-only ingest persistence: build column list + placeholders dynamically from SCHEMA_COLS so adding a new fundamentals column in a future migration extends the ingest with one list edit (no SQL rewrite)."
  - "PIT-aware MAX(as_of_ingest_date) read pattern: WITH latest AS (SELECT ticker, period_end, period_type, MAX(as_of_ingest_date) FROM fundamentals WHERE ... <= asof) JOIN back to the source table — this is the canonical 'as of asof' view of an append-only table."
  - "Ratio function shape: pure function (ticker, asof, conn) → dict[str, float | None]; the persister (compute_all_ratios) iterates universe and writes; this split lets factor code call compute_ratios in-memory without touching the ratios table."
  - "Test fixture pattern for migration-backed tests: alembic upgrade head into tmp_path/test.db using AlembicConfig with explicit sqlalchemy.url + script_location overrides — each test gets a fresh DB at the latest migration, no test ordering issues."
  - "Math-pinning test approach for ratios: integer-friendly fixture values so every formula collapses to a verifiable rational (rev=100, ni=20 → roe = 0.04 exactly); no floating-point slop hiding wrong formulas."

# Threat surface flags
threat_flags: []

# Metrics
metrics:
  duration: "9h 45min (across 2 sessions; first interrupted by Anthropic quota at ~7min into Task 1, resumed after quota reset)"
  task-count: 2
  files-created: 5
  files-modified: 2
  tests-added: 18
  tests-passed: 18
  ratios-implemented: 24
  commits: 2
  completed-date: "2026-05-04"
---

# Phase 01 Plan 05: Fundamentals + 24 Derived Ratios + D2 Mitigation Summary

Append-only quarterly+annual fundamentals via yfinance keyed by as_of_ingest_date (D2 binding) + 24 derived ratios per DATA-04 with PIT-aware reads.

## Objective Recap

Quarterly and annual fundamentals via yfinance plus the 24 derived ratios required by DATA-04. The plan-defining decision is the D2 mitigation: yfinance silently restates historical fundamentals (a 2025-Q4 row pulled in March 2026 may differ from the same 2025-Q4 row pulled in November 2026). The fundamentals PK from migration 0002 — `(ticker, period_end, period_type, as_of_ingest_date)` — makes append-only ingest the schema-enforced answer: same-day reruns are no-ops via `INSERT OR IGNORE`, later-day reruns with restated values land a new row alongside the original, and PIT-correct backtests can read with `WHERE as_of_ingest_date <= replay_date`.

## What Got Built

### Task 1 — `YFinanceProvider.get_fundamentals` + `refresh_fundamentals` orchestrator (commit 9ec92cd)

- **`src/ls_equity_fund/data/providers/yfinance_provider_fundamentals.py`** — `get_fundamentals_impl(session, ticker)` pulls `Ticker.income_stmt` + `.balance_sheet` + `.cashflow` (annual) and the `quarterly_*` variants, translates yfinance row labels through `_YF_LABEL_MAP` (e.g., `"Total Revenue" → "revenue"`, `"Net Income" → "net_income"`), merges into a per-period dict, computes `accruals = NI - CFO` when both present, and returns a `MultiIndex(period_end, period_type)` DataFrame projected onto `SCHEMA_COLS` (28 columns matching migration 0002). Tenacity retry: 3 attempts, exponential 1/2/4s, reraise.
- **`YFinanceProvider.get_fundamentals`** — replaced the `NotImplementedError` stub from plan 01-04 with a one-line delegation to `get_fundamentals_impl`, wrapped in `try/except Exception → YFinanceError`.
- **`src/ls_equity_fund/data/fundamentals.py`** — `refresh_fundamentals(config, conn, *, tickers, today, provider)` orchestrates the universe pull. Defaults: `tickers = SELECT ticker FROM universe WHERE delisted_date IS NULL ORDER BY ticker` (delisted excluded; benchmarks excluded by virtue of not being in `universe`); `today = date.today()`; `provider = YFinanceProvider(db_path=...)`. ThreadPoolExecutor fan-out at `config.data.yfinance_max_workers`. Per-ticker: `provider.get_fundamentals` → `_persist_fundamentals` (`INSERT OR IGNORE` with today's `as_of_ingest_date`) → `_persist_refresh_state` (`OK` with today's date, or `FAILED` with truncated error). Returns `{"ok": N, "failed": M, "rows_written": R}`.
- **6 unit tests** in `tests/unit/data/test_fundamentals_ingest.py`:
  - `test_refresh_writes_with_today_as_ingest_date` — every persisted row has today's date in `as_of_ingest_date`.
  - **`test_d2_mitigation_appends_on_restated_rerun`** (the D2 binding): two refreshes of the same period (April 1 with revenue=100, November 1 with revenue=110 restated) — afterwards `fundamentals` has TWO rows for that period_end, the April row preserved, the November row added.
  - `test_same_day_rerun_is_idempotent` — second run of the same day adds zero rows (PK collision → IGNORE).
  - `test_log_and_continue_on_provider_error` — `YFinanceError` on one ticker leaves other tickers' rows intact and writes `refresh_state.status='FAILED'` for the broken one.
  - `test_excludes_delisted_tickers` — `delisted_date IS NOT NULL` rows are skipped.
  - `test_append_only_no_replace_in_source` — grep-style guard that `fundamentals.py` source contains `INSERT OR IGNORE` and never `INSERT OR REPLACE` for the fundamentals table.

### Task 2 — Compute 24 derived ratios (commit cd4cd42)

- **`src/ls_equity_fund/data/ratios.py`** — two public functions plus an internal helper:
  - `_safe_div(num, den)` — division guard returning None on (a) None inputs, (b) NaN inputs (`x != x`), (c) zero denominator. Used at every division site (27 call sites in the file).
  - `_latest_per_period(conn, ticker, period_type, asof_str, limit)` — PIT-aware read: CTE picks `MAX(as_of_ingest_date)` per `period_end` with a hard ceiling of `asof_str` on BOTH `as_of_ingest_date` and `period_end`, joined back to fundamentals, ordered by `period_end DESC LIMIT N`. This is the D2-correct "as of asof" view.
  - `compute_ratios(ticker, asof, conn) -> dict[str, float | None]` — reads up to 5 quarters + 2 annuals + the latest close on/before asof from `daily_prices`, computes `market_cap = shares_outstanding * close`, derives 24 ratios. YoY uses q0 vs q4 (preferred) with annual q0-vs-q1 fallback when fewer than 5 quarters exist. QoQ uses q0 vs q1 on the quarterly list. Yields (FCF, dividend, buyback) divide by `market_cap`; dividend/buyback sign-flip the negative-cash-outflow yfinance convention. Returns the dict keyed by `_OUTPUT_COLS`.
  - `compute_all_ratios(conn, asof) -> int` — iterates active universe, calls `compute_ratios`, persists via `INSERT OR REPLACE INTO fundamental_ratios (ticker, asof_date, ...) VALUES (...)`. Returns row count. Same-asof reruns overwrite (ratios are a derived snapshot, not historical record).
- **`_OUTPUT_COLS`** — 24-element list pinning the exact ratio names from REQUIREMENTS.md DATA-04, source of truth for both the SQL column list and the test count check.
- **12 unit tests** in `tests/unit/data/test_ratios.py`:
  - `test_24_output_cols_match_spec` — `len(_OUTPUT_COLS) == 24`.
  - `test_basic_ratios_correct` — roe/roa/net_margin/gross_margin/operating_margin against integer-friendly fixture values.
  - `test_yoy_growth_uses_q0_vs_q4` — revenue_growth_yoy and earnings_growth_yoy use q0=2026-Q1 vs q4=2025-Q1.
  - `test_qoq_growth_uses_q0_vs_q1` — revenue_growth_qoq and earnings_growth_qoq use q0 vs q1.
  - `test_yields_use_market_cap` — fcf_yield = fcf / (shares × close); buyback_yield and dividend_yield sign-flip yfinance's negative cash-outflow convention.
  - `test_balance_sheet_ratios_correct` — current_ratio / ar_to_revenue / debt_to_equity / cfo_to_ni / accruals_ratio / asset_turnover.
  - `test_normalized_ratios_correct` — retained_earnings_ratio / working_capital_ratio / total_liabilities_ratio / ebit_margin / rd_intensity / shares_out passthrough.
  - `test_safe_div_returns_none_on_zero_revenue` — revenue=0 → all revenue-denominator ratios are None, not inf/nan.
  - `test_compute_all_ratios_writes_row_per_ticker` — one active universe ticker → one row in fundamental_ratios with correct values.
  - `test_compute_all_ratios_idempotent_via_replace` — second run of same asof overwrites, doesn't append.
  - `test_returns_none_when_fundamentals_absent` — universe ticker with no fundamentals → all 24 values None.
  - **`test_pit_aware_uses_latest_as_of_ingest`** — D2 PIT-read binding: a restated row for 2026-03-31 ingested 2026-04-15 is invisible to a `compute_ratios(asof=2026-04-10)` call (which sees the April-1 ingest's revenue=100) and visible to a `compute_ratios(asof=2026-05-01)` call (which sees the April-15 ingest's revenue=110).

## Verification Results

- `uv run pytest tests/unit/data/test_fundamentals_ingest.py tests/unit/data/test_ratios.py -v` → **18/18 passed** (6 fundamentals + 12 ratios).
- All Task 2 acceptance criteria pinned by grep:
  - `_OUTPUT_COLS` references in ratios.py: 6 (>=2 required)
  - `_OUTPUT_COLS` length: 24 (==24 required)
  - `INSERT OR REPLACE INTO fundamental_ratios` in ratios.py: 1 (==1 required)
  - `_safe_div` references in ratios.py: 27 (>=20 required — every ratio uses it)

## Deviations from Plan

None. The plan's 2 tasks executed as written. Two adjustments are worth flagging as design refinements rather than deviations:

1. **PIT read pattern strengthened** — the plan-as-written had `compute_ratios` use `ORDER BY period_end DESC, as_of_ingest_date DESC LIMIT 5`, which would silently include a future restated row if it existed. The shipped implementation uses an explicit `MAX(as_of_ingest_date) <= asof_str` CTE to make the PIT contract explicit and testable. The new `test_pit_aware_uses_latest_as_of_ingest` test pins this behavior — a strict superset of the plan's stated intent.

2. **NaN guard in `_safe_div`** — added an `x != x` check beyond None and zero-division. yfinance occasionally hands back `nan` floats for missing fields rather than None; the guard ensures a stray NaN never propagates into the ratios table.

Both refinements preserve the plan's success criteria and tighten the D2 mitigation surface.

## Authentication Gates

None.

## Known Stubs

None. All 24 ratios are wired end-to-end against migration-0002 fundamentals + daily_prices.

## Files Created / Modified

**Created:**
- `src/ls_equity_fund/data/providers/yfinance_provider_fundamentals.py` — yfinance fundamentals extraction (label map + retry + DataFrame builder)
- `src/ls_equity_fund/data/fundamentals.py` — `refresh_fundamentals` orchestrator (append-only ingest, refresh_state persistence)
- `src/ls_equity_fund/data/ratios.py` — `compute_ratios` + `compute_all_ratios` (24 ratios, PIT-aware reads, _safe_div guard)
- `tests/unit/data/test_fundamentals_ingest.py` — 6 tests including D2 mitigation binding
- `tests/unit/data/test_ratios.py` — 12 tests including PIT-correctness binding

**Modified:**
- `src/ls_equity_fund/data/providers/yfinance_provider.py` — `get_fundamentals` body replaced (delegation to `get_fundamentals_impl`)
- `src/ls_equity_fund/data/__init__.py` — re-export `refresh_fundamentals`, `compute_ratios`, `compute_all_ratios`

## Commits

| Commit | Subject |
|--------|---------|
| 9ec92cd | feat(01-05): YFinanceProvider.get_fundamentals + append-only refresh orchestrator |
| cd4cd42 | feat(01-05): compute 24 derived fundamental ratios (DATA-04) |

## Self-Check: PASSED

All claimed files exist:
- src/ls_equity_fund/data/providers/yfinance_provider_fundamentals.py FOUND
- src/ls_equity_fund/data/fundamentals.py FOUND
- src/ls_equity_fund/data/ratios.py FOUND
- tests/unit/data/test_fundamentals_ingest.py FOUND
- tests/unit/data/test_ratios.py FOUND

Both commits exist on the branch:
- 9ec92cd FOUND
- cd4cd42 FOUND

All 18 unit tests pass; 24 ratios verified; D2 mitigation pinned at both ingest layer (test_d2_mitigation_appends_on_restated_rerun) and read layer (test_pit_aware_uses_latest_as_of_ingest).
