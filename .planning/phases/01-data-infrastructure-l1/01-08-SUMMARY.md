---
phase: 01-data-infrastructure-l1
plan: 08
subsystem: macro-calendar
tags: [phase-1, l1-data, macro-calendar, fomc, fed-scraper, beautifulsoup4, cached-fallback]

# Dependency graph
requires:
  - phase: 01-data-infrastructure-l1
    provides: macro_calendar table (migration 0002), MacroProvider ABC, get_connection / get_db_path, structlog configured-or-default pipeline
provides:
  - "FedScraperProvider — MacroProvider impl scraping FOMC meeting dates from federalreserve.gov via BeautifulSoup4 (no JS rendering required)"
  - "refresh_macro_calendar(config, conn=None, *, force=False, today=None, provider=None) — orchestrator-callable refresh with 7-day interval gate, cached fallback on NetworkError, and staleness warning escalation"
  - "FOMC_CALENDAR_URL constant — single source of truth for the live URL (anti-rec guard tests for hardcoded dates anywhere else)"
  - "Anti-hardcoded-date lint test (test_no_hardcoded_dates_in_source) — fails CI if anyone slips an ISO date or quoted year into fred_provider.py"
affects: [phase-05-portfolio-construction, phase-06-risk-vetoes, phase-09-orchestrator, phase-10-dashboard]

# Tech tracking
tech-stack:
  added:
    - "beautifulsoup4>=4.12,<5  # static-HTML parser; no JS rendering, lightweight"
    - "requests>=2.32,<3  # explicit pin (curl_cffi normally provides it transitively)"
  patterns:
    - "Plain-HTML scraping with bs4 + a polite User-Agent (`Meridian Capital Partners macro-calendar-bot`); session is dependency-injected so tests pass MagicMock or pre-fetched HTML"
    - "Provider raises NetworkError on HTTP failure OR parse failure; orchestrator (refresh_macro_calendar) catches it and falls back to cached rows — daily run NEVER raises, only logs"
    - "Two-stage staleness escalation: cache <7d old + scrape failure → INFO log only (`macro_calendar_fetch_failed_falling_back`); cache >=7d old + scrape failure → that PLUS WARNING (`macro_calendar_stale_warning` with `days_since`)"
    - "7-day refresh-interval gate: orchestrator can call daily but the function no-ops if the freshest row is < REFRESH_INTERVAL_DAYS old; `force=True` bypasses for manual ops"
    - "Deterministic event_id = sha1('FOMC|<event_date_et>')[:16] — INSERT OR REPLACE keys cleanly so re-running with the same events does NOT duplicate rows"
    - "Decision-day convention: meeting `event_date_et` = LAST day of the published day-range (16-17 → 17); multi-month labels (`April/May`) use the SECOND month token"
    - "event_date_local stored as a separate column (mirrors event_date_et at v1, ET-equivalent) so a future timezone-aware refactor can populate it without a schema change"
    - "structlog default PrintLogger writes to stdout, so test verification of stale-warning escalation uses `capsys` rather than `caplog` (caplog only sees stdlib logging records)"

key-files:
  created:
    - "src/ls_equity_fund/data/providers/fred_provider.py"
    - "src/ls_equity_fund/data/macro_calendar.py"
    - "tests/fixtures/fomccalendars_fixture.html"
    - "tests/unit/data/test_fred_provider.py"
    - "tests/unit/data/test_macro_calendar.py"
  modified:
    - "src/ls_equity_fund/data/__init__.py  # re-export refresh_macro_calendar"
    - "pyproject.toml  # add beautifulsoup4, requests"

key-decisions:
  - "BeautifulSoup4 over edgartools/Selenium — fed.gov FOMC page is static HTML with a predictable year-grouped table structure; no JS rendering needed and bs4 is lighter weight"
  - "On HTTP / parse failure, FedScraperProvider raises NetworkError — refresh_macro_calendar catches it. The daily run NEVER propagates a fed.gov outage to the operator; the dashboard surfaces staleness via the structlog warning"
  - "STALENESS_WARN_THRESHOLD_DAYS = REFRESH_INTERVAL_DAYS = 7 — same threshold for 'should refresh' and 'cache is stale enough to warn about' keeps semantics simple"
  - "event_id is a 16-char sha1 truncation of `FOMC|<event_date_et>` — deterministic across runs so INSERT OR REPLACE upserts the same logical event without duplicating rows; 16 hex chars (64 bits) is overkill collision space for a calendar with ~10 events/year"
  - "Decision-day = LAST day of meeting day-range — matches the FOMC convention that the announcement / press conference happens on the second day"
  - "v1 ships `event_date_local == event_date_et` (ET-equivalent) — solo macOS operator running in a US timezone gets the correct CALENDAR DAY for the blackout veto. D-19 timezone conversion is a column-only contract; the future zoneinfo refactor changes ONE helper without touching the schema"
  - "Refresh interval gating lives in refresh_macro_calendar (not the orchestrator) — the function self-protects against accidental daily fed.gov hits even if a future caller forgets to gate"
  - "Anti-recommendation guard (CLAUDE.md): test_no_hardcoded_dates_in_source greps fred_provider.py for ISO dates and quoted year literals — fails CI if anyone slips a hardcoded calendar entry into the source"

patterns-established:
  - "Macro provider: bs4 + injectable session + injectable html — production calls fetch_macro_events() which round-trips fed.gov; tests pass `html=fixture_html` to skip the network entirely"
  - "Cached-fallback orchestrator pattern: try{ provider.fetch(); UPSERT; return success } except NetworkError { read cache; if stale, log warning; return fell_back=True } — daily run never crashes on upstream outage"
  - "structlog event-name conventions: `<subsystem>_<verb>_<state>` — `macro_calendar_refreshed`, `macro_calendar_refresh_skipped`, `macro_calendar_fetch_failed_falling_back`, `macro_calendar_stale_warning`. Operator-facing dashboard greps event names verbatim"
  - "Anti-rec lint via test: regex-grep the source file for forbidden patterns (hardcoded ISO dates, hardcoded quoted years) — cheap CI guard against CLAUDE.md anti-recommendations drifting into the codebase"
  - "Test secret-loading: when a fixture needs `load_config()`, monkeypatch.setenv the required secrets inside the fixture (conftest's `isolate_env` autouse strips them between tests)"

# Metrics
metrics:
  duration: ~30m (initial run interrupted by Anthropic quota; resumed for ~5m of test scaffolding + final commit)
  completed_date: 2026-05-05
  tasks_completed: 2
  files_created: 5
  files_modified: 2
  commits:
    - hash: d208bd0
      message: "feat(01-08): add FedScraperProvider for FOMC calendar via fed.gov scrape"
    - hash: 541ed26
      message: "feat(01-08): refresh_macro_calendar with cached fallback + staleness warning"
  test_count:
    fred_provider: 19
    macro_calendar: 10
    total_new: 29
  test_status: 154 / 154 passing (full suite, no regressions)
---

# Phase 1 Plan 8: FOMC Calendar — Live Scrape + 7-Day Cached Fallback Summary

Live FOMC calendar from `federalreserve.gov` per DATA-11. `FedScraperProvider` parses the year-grouped panel-table structure with BeautifulSoup4; `refresh_macro_calendar` orchestrates with a 7-day refresh-interval gate, a NetworkError-driven cached fallback, and a structlog staleness-warning escalation when the cache passes 7 days. CLAUDE.md's "no hardcoded FOMC dates" anti-recommendation is enforced by a source-scanning lint test.

## Tasks Completed

### Task 1 — FedScraperProvider via BeautifulSoup4 + HTML fixture + scraper tests (commit `d208bd0`)

**Files:**
- `src/ls_equity_fund/data/providers/fred_provider.py` — `FedScraperProvider` (MacroProvider impl), `FOMC_CALENDAR_URL`, `NetworkError`, `_resolve_meeting_date`, `_to_local`
- `tests/fixtures/fomccalendars_fixture.html` — minimal panel-table fixture covering 2026 (8 meetings) + 2027 (2 meetings)
- `tests/unit/data/test_fred_provider.py` — 19 unit tests (parse, date-resolution, ID determinism, lookahead filter, network errors, User-Agent, anti-hardcoded-date lint guard)
- `pyproject.toml` — added `beautifulsoup4>=4.12,<5` + `requests>=2.32,<3`

**Behavior:**
- GET `FOMC_CALENDAR_URL` with a polite User-Agent, parse `.panel-default` blocks, extract year from heading + month/day-range from each tbody row
- Decision day = LAST day of the published day-range; multi-month labels (`April/May`) use the SECOND month token
- Returns FOMC events sorted ascending, filtered to `today <= event <= today + lookahead_days`
- Raises `NetworkError` on HTTP failure (no session, status != 200, parse failure)

### Task 2 — refresh_macro_calendar with cached fallback + staleness warning (commit `541ed26`)

**Files:**
- `src/ls_equity_fund/data/macro_calendar.py` — `refresh_macro_calendar`, `REFRESH_INTERVAL_DAYS=7`, `STALENESS_WARN_THRESHOLD_DAYS=7`
- `src/ls_equity_fund/data/__init__.py` — re-exports `refresh_macro_calendar`
- `tests/unit/data/test_macro_calendar.py` — 10 unit tests (happy path, gate, force, fallback, two-stage staleness escalation, idempotency, local-tz column, constants)

**Behavior:**
- `refresh_macro_calendar(config, conn=None, *, force=False, today=None, provider=None) -> {events_written, fell_back, staleness_days}`
- 7-day gate: returns early if `MAX(last_refreshed) < REFRESH_INTERVAL_DAYS` ago and `not force`
- On scrape success: `INSERT OR REPLACE` events; `last_refreshed = now`
- On scrape failure: keep stored rows; if cache >= STALENESS_WARN_THRESHOLD_DAYS old, also emit `macro_calendar_stale_warning` with `days_since`
- Daily run continuity: function NEVER raises; even with no cached rows + scrape failure it returns `fell_back=True, staleness_days=9999`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test fixture conftest stripped required secrets**
- **Found during:** Task 2 — first test run failed with `pydantic_core.ValidationError: anthropic_api_key + sec_user_agent missing`
- **Issue:** `conftest.py` autouse `isolate_env` fixture deletes ANTHROPIC_API_KEY / SEC_USER_AGENT between tests; `load_config()` in the per-test fixture then can't construct the Secrets model
- **Fix:** Inside the test fixture, `monkeypatch.setenv()` the two required secrets BEFORE calling `load_config()`; the autouse fixture still strips them between tests so isolation is preserved
- **Files modified:** `tests/unit/data/test_macro_calendar.py`
- **Commit:** 541ed26

**2. [Rule 3 - Blocking] Plan-spec test used `caplog` but structlog writes to stdout**
- **Found during:** Task 2 — `test_fallback_beyond_7d_emits_stale_warning` failed because `caplog.records` was empty even though stderr clearly showed the structlog warning
- **Issue:** structlog's default `PrintLogger` (used when `configure_logging()` hasn't run) writes to stdout — `caplog` only captures stdlib `logging` records, not structlog's print output
- **Fix:** Switched the two staleness-escalation tests to `capsys` and assert on the captured stdout/stderr. This also matches how the dashboard / launchd log file will see these events (post-`configure_logging` they end up on stderr + a JSONL file; pre-config they hit stdout)
- **Files modified:** `tests/unit/data/test_macro_calendar.py`
- **Commit:** 541ed26

**3. [Rule 2 - Critical functionality] Idempotency test added beyond plan spec**
- **Found during:** Task 2 — plan listed 5 unit tests; SQLite `INSERT OR REPLACE` semantics deserve their own test so a future contributor cannot quietly switch to plain `INSERT` and silently corrupt the table
- **Issue:** Plan's `test_upsert_keeps_event_id_unique` was a single happy-path call; we replaced with a clearer `test_upsert_is_idempotent` (re-run with `force=True`, assert COUNT stays 2) and added `test_local_tz_field_persisted` to lock the D-19 column contract + two constant tests
- **Fix:** Net result is 10 tests for macro_calendar instead of the plan's 5 — exceeds spec
- **Files modified:** `tests/unit/data/test_macro_calendar.py`
- **Commit:** 541ed26

### Architectural changes
None — Rule 4 not triggered.

## Authentication Gates
None — both tasks are local-only (HTML fixture in unit tests; live fetch deferred to Phase 9 orchestrator).

## Verification Results

```
$ uv run pytest tests/unit/data/test_fred_provider.py tests/unit/data/test_macro_calendar.py tests/integration/test_phase0_smoke.py
54 passed in 9.83s

$ uv run pytest
154 passed, 3 warnings in 17.04s
```

- Plan-required (12+): 29 unit tests pass (19 fred + 10 macro)
- Phase 0 smoke (25/25): pass
- Full suite: 154/154 pass — zero regressions from the new code

## Acceptance Criteria

### Task 1
- [x] `grep -c "class FedScraperProvider" fred_provider.py` = 1
- [x] `grep -c "FOMC_CALENDAR_URL.*federalreserve.gov" fred_provider.py` = 1
- [x] `grep -c "from bs4 import BeautifulSoup" fred_provider.py` = 1
- [x] `grep -cE '^\\s+"beautifulsoup4' pyproject.toml` = 1
- [x] `grep -c "class NetworkError" fred_provider.py` = 1
- [x] `uv sync` succeeds
- [x] 19 fred_provider tests pass (>= 6 required)

### Task 2
- [x] `grep -c "def refresh_macro_calendar" macro_calendar.py` = 1
- [x] `grep -cE 'fell_back|staleness' macro_calendar.py` = 12 (>= 3 required)
- [x] `grep -c "INSERT OR REPLACE INTO macro_calendar" macro_calendar.py` = 1
- [x] `grep -c "REFRESH_INTERVAL_DAYS = 7" macro_calendar.py` = 1
- [x] `grep -c "macro_calendar_stale_warning" macro_calendar.py` = 2 (>= 1 required)
- [x] 10 macro_calendar tests pass (5 required)

## Plan-Level Success Criteria

- [x] FedScraperProvider parses fed.gov FOMC calendar HTML deterministically (sorted, sha1 IDs, decision-day convention)
- [x] refresh_macro_calendar gates on 7-day refresh interval, falls back to cache + warns on staleness >= 7d
- [x] NetworkError raised on parse / HTTP failure; daily run continues with cached data (refresh_macro_calendar swallows it)
- [x] 12+ unit tests pass; bs4 added to pyproject.toml

## Threat Mitigations Discharged

| Threat ID | Mitigation Shipped |
|-----------|-------------------|
| T-01-25 (DoS — fed.gov HTML structure changes) | Fixture-based unit tests detect parser regressions; NetworkError fallback keeps daily run alive |
| T-01-26 (DoS — fed.gov rate-limits scraping) | 7-day refresh-interval gate; polite User-Agent identifies us; default scrape is ONCE per week not per day |
| T-01-27 (Tampering — hardcoded FOMC dates leak into source) | `test_no_hardcoded_dates_in_source` lints fred_provider.py for ISO dates / quoted years / `Month DD` calendar entries |

## Self-Check: PASSED

- [x] `src/ls_equity_fund/data/providers/fred_provider.py` exists
- [x] `src/ls_equity_fund/data/macro_calendar.py` exists
- [x] `src/ls_equity_fund/data/__init__.py` re-exports `refresh_macro_calendar`
- [x] `tests/fixtures/fomccalendars_fixture.html` exists
- [x] `tests/unit/data/test_fred_provider.py` exists (19 tests pass)
- [x] `tests/unit/data/test_macro_calendar.py` exists (10 tests pass)
- [x] commit `d208bd0` exists in `git log --all`
- [x] commit `541ed26` exists in `git log --all`
- [x] full suite: 154 / 154 pass
