---
phase: 01-data-infrastructure-l1
verified: 2026-05-04T18:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
---

# Phase 1: Data Infrastructure (L1) Verification Report

**Phase Goal:** Operator runs one CLI command and SQLite has every feed needed to score the universe, with point-in-time integrity preserved at ingest so future backtests are not contaminated by survivorship or look-ahead bias.
**Verified:** 2026-05-04
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Three universe modes (`sp500`/`liquid_us`/`scanner_seed`) populate `first_seen_date`, `delisted_date`, `inclusion_window`; delisted tickers flagged, never deleted (CP1) | ✓ VERIFIED | `data/universe.py` `merge_universe_pit()` — UPDATE not DELETE path confirmed; smoke test `test_delisted_ticker_flagged_not_deleted_CP1_binding` PASSES |
| 2 | Daily refresh incrementally ingests all 11 feeds (OHLCV, fundamentals, ratios, filings, 13F, short int, estimates, earnings cal, FOMC) and persists a `runs` row | ✓ VERIFIED | `orchestrator.py` chains 11 steps; `_open_runs_row` / `_close_runs_row` write RUNNING then OK; smoke test `test_orchestrator_chains_all_eleven_steps` PASSES |
| 3 | `insider_transactions.transaction_code` distinguishes all 7 codes (P/S/A/M/F/G/D); unknown codes rejected at schema; cluster-buy counts only P | ✓ VERIFIED | Migration 0002 `CHECK (transaction_code IN ('P','S','A','M','F','G','D'))`; 7-fixture parametrized round-trip tests all PASS; `detect_cluster_buys` queries WHERE `transaction_code = 'P'` only |
| 4 | `--no-filings`, `--no-13f`, `--forms` flags work; `--no-filings` + `--forms` is mutually exclusive (exit code 5) | ✓ VERIFIED | `cli/data_cmd.py` accepts all three flags; `orchestrator.py` `raises ValueError` on both set; `test_cli_no_filings_and_forms_mutually_exclusive_exits_5` PASSES |
| 5 | `PolygonProvider` instantiates without error; every method raises `NotImplementedError` with DATA-14 message; orchestrator refuses `provider=polygon` at runtime | ✓ VERIFIED | `providers/polygon_provider.py` implements all 6 ABC interfaces; `test_polygon_provider_instantiates_proves_seam` PASSES; `test_polygon_selected_via_config_orchestrator_refuses_DATA14_message` exits 6 with DATA-14 in output |

**Score:** 5/5 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/ls_equity_fund/data/orchestrator.py` | ONE-CLI pipeline chaining 11 steps | ✓ VERIFIED | 377 lines; `run_data_pipeline()` chains universe→benchmarks→prices→fundamentals→ratios→filings→13F→short→estimates→earnings→macro; runs row persisted |
| `src/ls_equity_fund/cli/data_cmd.py` | `meridian run-data` with flags | ✓ VERIFIED | All 3 skip flags declared; exit-code map 2-7; wired to orchestrator |
| `src/ls_equity_fund/data/universe.py` | 3-mode builder + PIT merge | ✓ VERIFIED | `build_universe()` dispatches sp500/liquid_us/scanner_seed; `merge_universe_pit()` sets `first_seen_date`, flags delisteds with `delisted_date`, NEVER deletes |
| `src/ls_equity_fund/data/benchmarks.py` | Config-driven benchmark refresh | ✓ VERIFIED | Reads `config.data.benchmarks` + `sector_etfs` + `macro_tickers` from config.yaml — no hardcoded lists |
| `src/ls_equity_fund/data/prices.py` | Incremental OHLCV via yfinance | ✓ VERIFIED | File present; wired through orchestrator `_refresh_prices_step` |
| `src/ls_equity_fund/data/fundamentals.py` | Append-only fundamentals (D2) | ✓ VERIFIED | File present; wired through orchestrator |
| `src/ls_equity_fund/data/ratios.py` | 24 derived ratios | ✓ VERIFIED | File present; wired through orchestrator `_compute_ratios_step` |
| `src/ls_equity_fund/data/filings.py` | EDGAR 10-K/Q/8-K/Form 4 | ✓ VERIFIED | Uses `EdgarProvider(sec_user_agent=secrets.sec_user_agent)` — User-Agent from Secrets, not hardcoded |
| `src/ls_equity_fund/data/insider.py` | Cluster-buy + CEO/CFO analytics | ✓ VERIFIED | `detect_cluster_buys` queries `transaction_code = 'P'` only (CP3 binding) |
| `src/ls_equity_fund/data/institutional.py` | 13F tracked-fund ingestion | ✓ VERIFIED | Iterates `config.data.tracked_funds` (not hardcoded); `period_end` / `filed_date` stored as distinct columns (D4) |
| `src/ls_equity_fund/data/short_interest.py` | Daily short-interest snapshots | ✓ VERIFIED | File present; wired through orchestrator |
| `src/ls_equity_fund/data/estimates.py` | Analyst estimates snapshots | ✓ VERIFIED | File present; wired through orchestrator |
| `src/ls_equity_fund/data/earnings_calendar.py` | Earnings calendar 30d | ✓ VERIFIED | File present; wired through orchestrator |
| `src/ls_equity_fund/data/macro_calendar.py` | FOMC calendar + fallback | ✓ VERIFIED | Live scrape + cached fallback confirmed; `test_macro_calendar_falls_back_without_aborting` PASSES |
| `src/ls_equity_fund/data/providers/base.py` | 6 sibling ABCs | ✓ VERIFIED | `OHLCVProvider`, `FundamentalsProvider`, `ShortInterestProvider`, `EstimatesProvider`, `FilingsProvider`, `MacroProvider` — all abstract |
| `src/ls_equity_fund/data/providers/polygon_provider.py` | DATA-14 stub | ✓ VERIFIED | Implements all 6 ABCs; every method raises `NotImplementedError` with DATA-14 message |
| `src/ls_equity_fund/data/providers/edgar_provider.py` | EDGAR + lxml Form 4 / 13F parser | ✓ VERIFIED | User-Agent enforced at `__init__`; `VALID_TRANSACTION_CODES = frozenset({"P","S","A","M","F","G","D"})` |
| `migrations/versions/0002_create_phase1_tables.py` | 13 Phase 1 tables via `op.execute()` only | ✓ VERIFIED | No `op.create_table()` calls; all tables created via raw SQL; insider_transactions CHECK constraint confirmed |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `cli/data_cmd.py::run_data` | `orchestrator.py::run_data_pipeline` | direct import + call | ✓ WIRED | `from ls_equity_fund.data.orchestrator import run_data_pipeline` |
| `orchestrator.py` | `data/universe.py::build_universe` | lazy import in `_build_universe_step` | ✓ WIRED | All 11 step adapters use lazy imports |
| `orchestrator.py` | `runs` table | `_open_runs_row` / `_close_runs_row` | ✓ WIRED | INSERT on entry, UPDATE on exit with status + end_ts |
| `data/filings.py` | `EdgarProvider` | `secrets.sec_user_agent` | ✓ WIRED | Provider constructed with `sec_user_agent=secrets.sec_user_agent`; rejects missing @ in UA |
| `data/institutional.py` | `institutional_holdings` | `period_end` + `filed_date` distinct columns | ✓ WIRED | D4 binding preserved in schema and ingest code |
| `orchestrator.py` | `SystemExit` on `provider != yfinance` | `SUPPORTED_PROVIDERS` guard | ✓ WIRED | Checked before any step runs; CLI maps to exit code 6 |

---

## Data-Flow Trace (Level 4)

Spot-checks on dynamic data artifacts:

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `universe.py` | `rows` from mode builders | `_build_sp500` → Wikipedia; `_build_scanner_seed` → yfinance | Yes — live or fixture HTML; persists to `universe` table | ✓ FLOWING |
| `orchestrator.py` | `manifest` | 11 `_step()` wrappers returning module results | Yes — real persistence path; `runs` table row confirmed in test | ✓ FLOWING |
| `insider.py::detect_cluster_buys` | `rows` | SQL query on `insider_transactions WHERE transaction_code='P'` | Yes — real DB query, not hardcoded | ✓ FLOWING |
| `macro_calendar.py` | `events_written` | `FredProvider` or cached fallback from `macro_calendar` table | Yes — fallback path confirmed in SC2 test | ✓ FLOWING |

---

## Behavioral Spot-Checks

| Behavior | Result | Status |
|----------|--------|--------|
| 31 SC closure-gate tests (`tests/integration/test_phase1_smoke.py`) | 31/31 PASS in 3.04s | ✓ PASS |
| Full test suite | 278/278 PASS in 7.88s | ✓ PASS |
| `--no-filings` + `--forms` → exit code 5 | Confirmed via test | ✓ PASS |
| `provider=polygon` → exit code 6, DATA-14 in output | Confirmed via test | ✓ PASS |
| Form 4 unknown code 'X' → `sqlite3.IntegrityError` | Confirmed via test | ✓ PASS |
| Delisted ticker stays in DB with `delisted_date` set | row count = 3 not 2 confirmed | ✓ PASS |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| DATA-01 | 01-02 | Universe 3 modes | ✓ SATISFIED | `data/universe.py` 3-mode dispatch + PIT merge |
| DATA-02 | 01-03 | Benchmarks + sector ETFs + macro tickers | ✓ SATISFIED | `data/benchmarks.py` reads from config.yaml lists |
| DATA-03 | 01-04 | OHLCV incremental via yfinance | ✓ SATISFIED | `data/prices.py` + `providers/yfinance_provider.py`; yfinance pinned `==0.2.65`; curl_cffi in deps |
| DATA-04 | 01-05 | Fundamentals + 24 ratios, append-only (D2) | ✓ SATISFIED | fundamentals PK includes `as_of_ingest_date`; `data/ratios.py` computes 24 ratios |
| DATA-05 | 01-06 | EDGAR 10-K/Q/8-K/Form 4 + User-Agent compliance | ✓ SATISFIED | `EdgarProvider.__init__` rejects missing '@' in UA; UA sourced from `secrets.sec_user_agent` |
| DATA-06 | 01-06 | Form 4 P/S/A/M/F/G/D codes + CEO/CFO + cluster-buy | ✓ SATISFIED | 7 fixtures round-trip; CHECK constraint enforced; cluster-buy filters `code='P'` only |
| DATA-07 | 01-06 | 13F tracked-fund list; 3+ funds opening simultaneously | ✓ SATISFIED | `data/institutional.py` iterates `config.data.tracked_funds`; `detect_multi_fund_openings()` present |
| DATA-08 | 01-07 | Short interest daily snapshots | ✓ SATISFIED | `data/short_interest.py` + `short_interest` table |
| DATA-09 | 01-07 | Analyst estimates 30/60/90d revisions | ✓ SATISFIED | `data/estimates.py` + `analyst_estimates` table |
| DATA-10 | 01-07 | Earnings calendar next 30 days | ✓ SATISFIED | `data/earnings_calendar.py` + `earnings_calendar` table |
| DATA-11 | 01-08 | FOMC calendar live + cached fallback + warning on parse failure | ✓ SATISFIED | `data/macro_calendar.py` + `providers/fred_provider.py`; fallback path tested |
| DATA-12 | 01-09 | `--no-filings` / `--no-13f` / `--forms` skip flags + mutual exclusion | ✓ SATISFIED | `cli/data_cmd.py` + orchestrator guard; exit code 5 on conflict |
| DATA-13 | 01-01 + 01-02 | PIT universe table with `first_seen_date` + `delisted_date` | ✓ SATISFIED | Migration 0002 schema + `merge_universe_pit()` logic |
| DATA-14 | 01-01 + 01-04 + 01-09 | `MarketDataProvider` seam + PolygonProvider stub + runtime guard | ✓ SATISFIED | 6 sibling ABCs; `PolygonProvider` implements all; orchestrator `SUPPORTED_PROVIDERS` guard |

**Requirements satisfied: 14/14**

---

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| None found | — | — | — |

**Anti-pattern audit results:**

- `ib_insync` usage: NONE found in `src/` (comment in `yfinance_provider.py` explicitly warns against it; `ib_async` referenced where needed)
- `op.create_table()` in migration 0002: NONE — all tables created via `op.execute("CREATE TABLE ...")`
- Hardcoded sector ETF lists: NONE — `config.py` `DataConfig.sector_etfs` is a config-driven field list
- Hardcoded FOMC dates: NONE — `macro_calendar.py` line 3 explicitly states "NO hardcoded FOMC dates"
- Hardcoded tracked fund names: NONE — `institutional.py` iterates `config.data.tracked_funds`
- `requests-cache` import: NONE in `src/`
- yfinance version: PINNED at `==0.2.65` in `pyproject.toml` (spec mandates pin)
- `curl_cffi`: present in `pyproject.toml` deps (`curl-cffi>=0.7`)
- pandas pin: `>=2.2,<3.0` in `pyproject.toml` (correct — avoids pandas 3 breaking changes)
- EDGAR User-Agent: enforced via `secrets.sec_user_agent` (Secrets, not config.yaml); `EdgarProvider.__init__` raises `ValueError` without '@'
- 13F `period_end` vs `filed_date` distinct: VERIFIED in migration schema and `institutional.py` ingest code
- fundamentals `as_of_ingest_date` in PK: VERIFIED in migration 0002 fundamentals table definition

---

## Human Verification Required

None. All success criteria are verifiable programmatically and confirmed by the 278-test suite.

---

## Gaps Summary

No gaps. All 5 ROADMAP success criteria verified. All 14 DATA-* requirements satisfied. All CP1/CP3/D2/D4 bindings confirmed in schema and ingest code. Anti-pattern audit clean. Full test suite 278/278 passing.

---

_Verified: 2026-05-04T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
