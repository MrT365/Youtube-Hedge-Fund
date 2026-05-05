---
phase: 01-data-infrastructure-l1
plan: 06
subsystem: l1-data-edgar
tags: [phase-1, l1-data, edgar, form-4, 13f, cp3, sc3, d4]

# Dependency graph
requires:
  - phase: 01-data-infrastructure-l1
    plan: 01
    provides: filings_metadata / insider_transactions / institutional_holdings / refresh_state tables (alembic head=0002), FilingsProvider ABC at data/providers/base.py
provides:
  - "EdgarProvider concrete FilingsProvider — edgartools 5.30.x for the EDGAR HTTP path (10 req/s rate-limit + User-Agent compliance handled natively); lxml XPath against the public Form 4 / 13F schemas for actual XML parsing (deterministic, version-pinned, schema-drift-resilient)"
  - "refresh_filings(config, secrets, conn=None, *, forms=None, tickers=None, today=None, provider=None) — orchestrator that iterates active universe × forms, fetches via EdgarProvider, writes filings_metadata + (for form='4') insider_transactions; idempotent via INSERT OR IGNORE on both PK-keyed tables"
  - "refresh_institutional_holdings(config, secrets, conn=None, *, provider=None) — iterates config.data.tracked_funds, persists 13F-HR rows with period_end and filed_date as DISTINCT columns (D4 binding) and INSERT OR REPLACE per (cik, ticker, period_end)"
  - "detect_cluster_buys / flag_ceo_cfo_purchases — on-demand analytics over insider_transactions; both filter on transaction_code='P' only (CP3 binding — A/M/F/G/D are NOT directional and are deliberately excluded from the buy signal)"
  - "detect_multi_fund_openings — groups is_new_position=1 rows by ticker at a given period_end; surfaces tickers with >= min_funds tracked funds opening simultaneously"
  - "TrackedFund pydantic sub-model + DataConfig.tracked_funds field — 9 spec funds default with public CIKs; lives in config so fund-list edits do not require source changes (CLAUDE.md anti-recommendation against hardcoded fund names)"
  - "VALID_TRANSACTION_CODES frozenset({'P','S','A','M','F','G','D'}) — single source of truth for the 7 Form 4 codes; mirrors migration 0002 CHECK constraint at the parser layer"
  - "CEO_CFO_TITLE_RE — case-insensitive regex matching 'Chief Executive/Financial Officer' or word-boundary 'CEO'/'CFO'; consumed by flag_ceo_cfo_purchases for 3x weighting input"
affects: [phase-01-wave-2, phase-2-factor-scoring, phase-2-insider-cluster-factor, phase-2-13f-multi-fund-factor, phase-4-risk-analyzer]

# Tech tracking
tech-stack:
  added:
    - "edgartools 5.30.x — used for the EDGAR HTTP fetch path (set_identity + Company.get_filings) where its built-in 10 req/s + User-Agent handling earns its keep"
    - "lxml 6.1.x — used for actual Form 4 / 13F XML parsing via XPath against the public schemas; deliberate split (see Decision 1)"
  patterns:
    - "edgartools-fetches / lxml-parses split: the constructor calls `edgar.set_identity(sec_user_agent)` and `fetch_filings` uses `Company(ticker).get_filings(form=...)`, but `parse_form4` and `parse_13f` go straight to lxml XPath. The plan documents this as 'edgartools primary, lxml fallback'; the actual implementation is 'edgartools fetches, lxml parses' — the try/except shape is preserved so future upgrades can flip the default without touching callers"
    - "Constructor-time User-Agent guard: EdgarProvider raises ValueError if sec_user_agent lacks '@'; EDGAR returns 403 to anonymous traffic so this is a fail-fast, not a soft warning (T-01-17 mitigation)"
    - "P-only directional signal at the SQL layer: detect_cluster_buys and flag_ceo_cfo_purchases each pin `transaction_code = 'P'` in their WHERE clauses — the CP3 binding is enforced at the query, not in caller logic, so analytics cannot accidentally count A/M/F/G/D"
    - "D4 column distinction at every layer: institutional_holdings table has period_end + filed_date as separate NOT NULL columns; the orchestrator reads ff['period_of_report'] and ff['filed_date'] separately and writes them to the correct columns; downstream factor logic computes alpha-decay on (today - period_end), not (today - filed_date)"
    - "Schema-layer rejection of unknown transaction codes: parser drops codes not in VALID_TRANSACTION_CODES with a structlog warning rather than raising; the DB CHECK constraint is the second line of defense if the parser ever lets one through"
    - "Anti-hardcoded fund-name guard via grep test: tests assert 'Citadel'/'Berkshire'/'Pershing'/'Bridgewater'/'Tiger Global' are NOT present in filings.py or institutional.py — fund names live in config.data.tracked_funds only"
    - "Per-(provider, feed_type, ticker) cursor in refresh_state: refresh_filings writes one row per (edgar, filings_<form>, ticker) so subsequent runs only ask EDGAR for filings filed since the prior MAX(filed_date)"
    - "Idempotent ingest via PK-keyed INSERT OR IGNORE: filings_metadata is keyed by accession_number; insider_transactions by (accession_number, line_no); institutional_holdings uses INSERT OR REPLACE on (cik, ticker, period_end) since 13F restatements update the same row"

key-files:
  created:
    - "src/ls_equity_fund/data/providers/edgar_provider.py"
    - "src/ls_equity_fund/data/filings.py"
    - "src/ls_equity_fund/data/insider.py"
    - "src/ls_equity_fund/data/institutional.py"
    - "tests/fixtures/form4_p_purchase.xml"
    - "tests/fixtures/form4_s_sale.xml"
    - "tests/fixtures/form4_a_grant.xml"
    - "tests/fixtures/form4_m_exercise.xml"
    - "tests/fixtures/form4_f_withhold.xml"
    - "tests/fixtures/form4_g_gift.xml"
    - "tests/fixtures/form4_d_disposition.xml"
    - "tests/unit/data/test_form4_parser.py"
    - "tests/unit/data/test_filings_ingest.py"
  modified:
    - "src/ls_equity_fund/config.py"
    - "src/ls_equity_fund/data/__init__.py"
    - "config.yaml.example"

key-decisions:
  - "edgartools fetches, lxml parses — NOT 'edgartools primary, lxml fallback'. The plan-checker correctly flagged the original wording as misleading. edgartools' internal Form 4 dataclass is version-drifty across releases; going straight to lxml XPath against the public ownershipDocument schema gives deterministic, version-pinned behavior. edgartools is still load-bearing for the EDGAR HTTP path (rate-limit + User-Agent), so the import is required, not optional. The try/except scaffolding is retained so a future upgrade can flip the default by replacing the lxml call inside _parse_form4_edgartools without touching callers."
  - "User-Agent comes from Secrets.sec_user_agent (loaded from .env), not config.yaml — D-21 binding. EDGAR mandates a contact email; storing it in Secrets keeps it out of repo-tracked config and lets structlog redaction handle it via the existing 'key' allowlist (T-01-21 acceptance)."
  - "Form 4 lookback window = 90 days on first run — matches DATA-06 cluster-buy window. Subsequent runs use refresh_state.last_value_text as the since= cursor; 10-K/10-Q/8-K use a 5-year first-run backfill window."
  - "Filesystem-backed raw bodies under cache/filings/{ticker}/{accession}.{ext} — keeps SQLite size bounded (analyzers in Phase 4 read filepath when needed). content_hash is SHA-256 of the persisted file body so future audit can detect tampering (T-01-19 mitigation)."
  - "Cluster-buy detection is a derived view, NOT a separate table — Phase 2 reads insider_transactions directly via detect_cluster_buys(). No cluster_buys table avoids a denormalized cache that has to be invalidated whenever Form 4 ingest runs."
  - "tracked_funds CIKs ship as defaults in DataConfig (9 spec funds with public CIKs) — operator can override in config.yaml without source edits; CLAUDE.md anti-recommendation against hardcoded fund names is enforced by grep tests at module load."
  - "13F INSERT OR REPLACE on (cik, ticker, period_end) — 13F restatements/amendments (13F-HR/A) update the same row rather than appending. Distinguishing amendments from originals is deferred (the spec explicitly does not require amendment provenance for v1)."
  - "is_new_position computed at write time — orchestrator looks up the most-recent prior period_end for (cik, ticker) and sets is_new_position=1 only when no prior row exists AND current shares > 0. Avoids a Phase 2-side window function and lets detect_multi_fund_openings be a simple GROUP BY query."
  - "Cross-task plan idiom: filings.py owns the ingest pipeline (because it needs the EDGAR fetch + parse fan-out); insider.py and institutional.py own only the on-demand analytics that read the resulting tables. Keeps each module under 200 lines and matches the data-layer log+continue pattern established by prices.py."

patterns-established:
  - "Provider constructor-time invariants: critical preconditions (User-Agent has @, IBKR port matches mode, etc.) raise ValueError in __init__ rather than during the first call. Surfaces config errors at boot, not 30 minutes into a run."
  - "Test-fixture-driven CP3/SC3 binding: 7 hand-crafted Form 4 XML fixtures — one per transaction code — are the parametrized contract. The parser MUST round-trip each code's literal letter through to the dict's transaction_code field; the test name (test_form4_parses_all_seven_transaction_codes) is the spec citation."
  - "Anti-hardcoded grep tests as enforcement: where CLAUDE.md anti-recommendations are easy to violate (fund names, sector ETFs, FOMC dates), add a unit test that read_text()s the source and asserts the forbidden string is absent. Fast, deterministic, and a junior contributor will see the failure immediately."
  - "Mock-based orchestrator tests: refresh_filings + refresh_institutional_holdings tests inject a MagicMock provider so we exercise the persistence + idempotency path without real EDGAR traffic. The real EdgarProvider is exercised by the test_form4_parser fixtures + the future integration smoke."
  - "Per-task self-check via grep counts: the plan's <acceptance_criteria> contains literal grep counts that the executor verifies before commit. When a docstring contains the literal substring being matched, rephrase the docstring (use code-formatting markup) instead of changing the criterion — see 01-01-SUMMARY.md."

# Plan execution metrics
metrics:
  duration: "~25 min (resumed after Anthropic quota interruption)"
  tasks_completed: 2
  tests_added: 24  # 13 in test_form4_parser.py + 11 in test_filings_ingest.py
  tests_passing: 149/149 (full suite — no regressions)
  files_created: 13  # provider + 3 ingest modules + 7 fixtures + 2 test files
  files_modified: 3  # config.py + data/__init__.py + config.yaml.example
  completed_date: "2026-05-04"
---

# Phase 01 Plan 06: SEC EDGAR Filings + Form 4 + 13F Ingestion Summary

**One-liner:** EDGAR ingestion pipeline using `edgartools 5.30.x` for the HTTP fetch path (rate-limit + User-Agent compliance) and `lxml` XPath for deterministic Form 4 / 13F XML parsing — persists filings_metadata + insider_transactions (all 7 Form 4 codes parametrized, CP3-bound) + institutional_holdings (period_end and filed_date distinct, D4-bound).

## Executive Summary

Plan 01-06 delivers the L1 SEC ingestion pipeline. The plan executed across two waves: Task 1 (Form 4 parser + 7 fixture XMLs + TrackedFund config) shipped before the Anthropic quota interruption; Task 2 (refresh_filings + insider/13F analytics + ingest tests) completed on resume. All 24 plan-scoped tests pass; the full suite of 149 tests passes with no regressions.

The headline correctness binding is the **CP3 / SC3 binding**: every Form 4 transaction code (P/S/A/M/F/G/D) is parsed distinctly via 7 hand-crafted fixture XMLs, and `detect_cluster_buys` + `flag_ceo_cfo_purchases` count ONLY `transaction_code='P'` rows. A/M/F/G/D codes are NOT directional and would dilute the signal if counted — Phase 2 scoring depends on this filter being inside the analytics SQL, not in caller logic.

The headline architectural decision is the **edgartools-fetches / lxml-parses split**, documented below. This corrects misleading "edgartools primary, lxml fallback" wording in the plan.

## Decisions Made (Plan-Level Architecture)

### Decision 1: edgartools fetches, lxml parses — NOT a fallback chain

The original plan text described the parsing strategy as "edgartools primary, lxml fallback." The plan-checker flagged this as misleading. The actual implementation is:

- **edgartools is load-bearing for the HTTP path.** `EdgarProvider.__init__` calls `edgar.set_identity(sec_user_agent)`. `fetch_filings` uses `Company(ticker).get_filings(form=...).filter(filing_date=...)`. This buys us the 10 req/s rate-limit and User-Agent header for free.
- **lxml is the actual parser.** `parse_form4` and `parse_13f` go straight to `lxml.etree.parse(path)` and XPath against the public `ownershipDocument` (Form 4) and `informationtable` (13F) schemas. edgartools' built-in Form 4 dataclass is version-drifty across releases (5.28 → 5.29 changed field names twice in 2025); going to lxml gives deterministic, version-pinned behavior.
- **The try/except scaffolding is retained.** `_parse_form4_edgartools` currently delegates to `_parse_form4_lxml` so the try/except in `parse_form4` is a no-op. A future upgrade can flip this by implementing real edgartools parsing inside `_parse_form4_edgartools`; callers do not change.

This split is documented in the EdgarProvider module docstring AND in the parsed-method docstrings to prevent the next contributor from "fixing" what looks like dead try/except scaffolding.

### Decision 2: User-Agent loaded from Secrets, not config.yaml (D-21)

EDGAR mandates `User-Agent: "<Org Name> <contact@email>"` and returns 403 to anonymous or email-less requests. The User-Agent string lives on `Secrets.sec_user_agent` (loaded from `.env`), not in `config.yaml`. This keeps the operator's email out of the repo-tracked config and lets structlog redaction handle it via the existing key allowlist. T-01-21 (information disclosure of operator email) is **accepted** — the email is the EDGAR-mandated contact, and there is no way to use EDGAR without it.

The constructor refuses to operate without an `@` in the User-Agent — fail fast at boot, not 30 minutes into a run.

### Decision 3: Form 4 lookback = 90 days; 10-K/10-Q/8-K = 5 years on first run

`refresh_filings._last_filed_date` returns the prior `MAX(filed_date)` from `filings_metadata` if any rows exist; otherwise it returns a backfill anchor: 90 days for Form 4 (matches DATA-06 cluster-buy window) and 5 years for 10-K/10-Q/8-K. Subsequent runs use the cursor in `refresh_state.last_value_text` as the `since=` parameter to edgartools' filing filter.

### Decision 4: Raw bodies on disk, metadata in SQLite

`cache/filings/{TICKER}/{accession}.{ext}` (`.xml` for Form 4 / 13F, `.txt` for 10-K/10-Q/8-K). `filings_metadata.filepath` points to the file. SHA-256 stored in `content_hash` so future audit can detect tampering (T-01-19 mitigation). Phase 4 risk analyzer reads `filepath` when it needs to extract sections; Phase 1 just stores the body.

### Decision 5: 13F INSERT OR REPLACE on (cik, ticker, period_end), with period_end + filed_date distinct

D4 binding is non-negotiable: `period_end` (the report-as-of date) and `filed_date` (when the SEC accepted the filing) are stored as separate NOT NULL columns. The 45-day legal lag MUST survive the persistence layer so Phase 2 factor logic can compute alpha-decay weighting on `(today - period_end)`, never `(today - filed_date)`. Tests assert the columns are different in fixture rows.

### Decision 6: Cluster-buy as derived view, not separate table

`detect_cluster_buys(conn, today, window_days=30, min_insiders=3)` is a SQL `GROUP BY` query against `insider_transactions` filtered to `transaction_code='P'` and a 30-day window. No `cluster_buys` cache table — avoids invalidation hazards when Form 4 ingest re-runs. Phase 2 reads insider_transactions and applies this query on demand.

### Decision 7: tracked_funds CIK list lives in config (CLAUDE.md anti-recommendation)

`DataConfig.tracked_funds: list[TrackedFund]` ships 9 spec funds (Berkshire, Citadel, Point72, Bridgewater, Tiger Global, Third Point, Appaloosa, Baupost, Pershing Square) with their public CIKs as defaults. Anti-hardcoded enforcement: `test_no_hardcoded_fund_names_in_filings_module` and `test_no_hardcoded_fund_names_in_institutional_module` `read_text()` the source and assert no fund-name string is present. A junior contributor adding `if fund_name == "Berkshire": ...` will see an immediate test failure.

## Tasks Completed

### Task 1 — Form 4 parser + 7 fixtures + TrackedFund config (RED → GREEN)

- **RED commit `3d31cb1`:** `test(01-06): add failing Form 4 parser tests (CP3 — 7 codes)` — added 13 failing tests across 7 parametrized fixture cases plus 6 standalone assertions on the parser surface (regex, valid-set, unknown-code drop, User-Agent guard, etc.).
- **GREEN commit `9ed5fd3`:** `feat(01-06): EdgarProvider with lxml Form 4 parser + TrackedFund config` — created EdgarProvider with the documented edgartools-fetches/lxml-parses split, 7 fixture XMLs (one per code, varying only the transaction_code line + insider details + price/shares), TrackedFund pydantic sub-model, DataConfig.tracked_funds field with 9-fund default, config.yaml.example update.
- All 13 Task 1 tests pass.

### Task 2 — refresh_filings + insider/13F analytics + ingest tests

- **Commit `697b682`:** `feat(01-06): refresh_filings + insider/13F analytics + ingest tests` — created filings.py (orchestrator + idempotent INSERT helpers + per-(provider, feed_type, ticker) refresh_state cursor), insider.py (detect_cluster_buys + flag_ceo_cfo_purchases, both P-only), institutional.py (refresh_institutional_holdings + detect_multi_fund_openings, period_end + filed_date distinct), and the test file with 11 tests including idempotency, P-only filter, D4 separation, anti-hardcoded-fund-name guards, and end-to-end mock-driven 13F refresh.

## Verification

| Check                                                              | Result      |
| ------------------------------------------------------------------ | ----------- |
| `uv run pytest tests/unit/data/test_form4_parser.py -v`            | 13/13 pass  |
| `uv run pytest tests/unit/data/test_filings_ingest.py -v`          | 11/11 pass  |
| `uv run pytest` (full suite)                                       | 149/149 pass |
| 7 Form 4 fixtures exist (`tests/fixtures/form4_*.xml`)             | yes         |
| `VALID_TRANSACTION_CODES` is `frozenset({'P','S','A','M','F','G','D'})` | yes |
| `class EdgarProvider(FilingsProvider)` declared                    | yes         |
| `class TrackedFund` declared in config.py                          | yes         |
| `transaction_code = 'P'` filter present in insider.py (>= 2 hits)  | 4 hits      |
| `period_end` / `filed_date` distinct in institutional.py (>= 4)    | 15 hits     |
| Idempotent INSERT statements (3 distinct: filings/insider/13F)     | 3 hits      |
| Anti-hardcoded grep guards for filings.py + institutional.py       | both pass   |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Ambiguous column reference in test SQL**

- **Found during:** Task 2 — `test_refresh_filings_persists_metadata_and_parses_form4`
- **Issue:** Test SQL `JOIN insider_transactions USING(accession_number) WHERE ticker='AAPL'` failed with `OperationalError: ambiguous column name: ticker` because both `filings_metadata` and `insider_transactions` carry a `ticker` column.
- **Fix:** Aliased both tables (`fm` / `it`) and qualified the WHERE clause as `WHERE fm.ticker='AAPL'`. Functionally equivalent; resolves the SQLite ambiguity.
- **Files modified:** `tests/unit/data/test_filings_ingest.py:141-145`
- **Commit:** `697b682`

**2. [Rule 1 - Bug] Plan wording on parser strategy was misleading**

- **Found during:** plan-checker pre-execution review (flagged as a known concern)
- **Issue:** Plan said "edgartools primary, lxml fallback." Implementation is "edgartools fetches HTTP, lxml parses XML deterministically; try/except scaffolding retained for future API stabilization." The plan wording would mislead a future contributor into thinking edgartools' parser was load-bearing.
- **Fix:** Module docstring on `edgar_provider.py` already documents the split clearly. SUMMARY.md "Decision 1" elevates this so it is the first thing the next contributor reads. No code change required — the implementation was correct; the plan documentation is what was off.
- **Files modified:** `01-06-SUMMARY.md` (this file)
- **Commit:** N/A — documentation-only clarification

### Auth Gates

None during this run. (The Anthropic quota exhaustion that interrupted the prior run was a per-API rate limit, not an authentication gate; on resume, the existing key still authenticated.)

## Threat Mitigations Realized

| Threat ID | Mitigation In Code                                                                  |
| --------- | ----------------------------------------------------------------------------------- |
| T-01-17   | `EdgarProvider.__init__` ValueError on missing `@` in User-Agent; edgartools 10 req/s |
| T-01-18   | 7-fixture parametrized test in `test_form4_parser.py`; schema CHECK constraint at DB |
| T-01-19   | SHA-256 `content_hash` column written at fetch time in `_insert_filing`             |
| T-01-20   | `period_end` + `filed_date` as distinct columns; tests assert non-equality          |
| T-01-21   | Accepted — operator email is EDGAR-mandated; structlog key-allowlist redaction      |

## Known Stubs / Deferred

- **edgartools `_parse_form4_edgartools` and `_parse_13f_edgartools` delegate to lxml.** Documented in Decision 1. NOT a stub in the "blocks plan goal" sense — the code path is fully functional via lxml. Future plan can swap in real edgartools parsing when its Form 4 dataclass API stabilizes.
- **13F amendment provenance.** 13F-HR and 13F-HR/A both go through `INSERT OR REPLACE` on the (cik, ticker, period_end) PK; amendments overwrite originals without a flag. Spec does not require amendment provenance for v1.
- **EdgarProvider real-network integration smoke test.** All Task 1 + Task 2 tests use mocked or fixture-fed providers. A live-EDGAR smoke test belongs in a Phase 1 wave-3 integration plan, not this one.

## Self-Check: PASSED

- [x] All 13 created/modified files exist on disk
- [x] All 3 commits in git log: `3d31cb1` (RED), `9ed5fd3` (GREEN — Task 1), `697b682` (Task 2)
- [x] 24/24 plan-scoped tests pass
- [x] 149/149 full-suite tests pass (no regressions)
- [x] No commits on protected branches; all work on `worktree-agent-a4a19f2c693e5bf1c`
- [x] No hardcoded fund names in filings.py / institutional.py (grep tests pass)
- [x] Plan-checker concern about "edgartools primary, lxml fallback" wording addressed in Decision 1
