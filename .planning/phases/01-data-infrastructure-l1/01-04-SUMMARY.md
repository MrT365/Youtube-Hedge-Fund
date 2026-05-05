---
phase: 01-data-infrastructure-l1
plan: 04
subsystem: l1-data-ingestion
tags: [phase-1, l1-data, ohlcv, yfinance, curl-cffi, tenacity, threadpool]
requirements: [DATA-03, DATA-14]
dependency_graph:
  requires:
    - "01-01: schema (daily_prices, refresh_state) + provider ABCs (OHLCVProvider, FundamentalsProvider, ShortInterestProvider, EstimatesProvider)"
  provides:
    - "YFinanceProvider concrete class (default OHLCVProvider; stubs for fundamentals/short/estimates filled by Plans 05/07)"
    - "refresh_prices(config, conn) — incremental, threadpooled, log+continue OHLCV ingestion orchestrator"
  affects:
    - "Plan 01-05 (fundamentals) — extends same YFinanceProvider class"
    - "Plan 01-07 (short interest + estimates) — extends same YFinanceProvider class"
    - "Plan 01-09 (daily orchestrator) — calls refresh_prices first in pipeline"
tech_stack:
  added:
    - "yfinance (pinned via uv.lock) — OHLCV download"
    - "curl_cffi — TLS impersonation transport (chrome) for Yahoo bot-detection bypass"
    - "tenacity — exponential backoff retry decorator (3 attempts, 1s/2s/4s/...8s cap)"
    - "concurrent.futures.ThreadPoolExecutor — bounded parallel fetch (max_workers from config)"
  patterns:
    - "Provider seam: YFinanceProvider implements OHLCVProvider ABC; orchestrator depends on ABC, not concrete"
    - "Incremental refresh via MAX(date) per ticker; first-run = lookback_years window, subsequent = last_stored_date+1d"
    - "Log+continue on per-ticker failure (refresh_state.status='FAILED' with truncated last_error); daily run never aborts on transient yfinance errors"
    - "INSERT OR IGNORE on (ticker, date) PK — idempotent re-ingestion safe"
    - "Session injection in constructor — production uses curl_cffi, tests inject sentinel object to avoid network"
key_files:
  created:
    - "src/ls_equity_fund/data/providers/yfinance_provider.py — YFinanceProvider (238 lines)"
    - "src/ls_equity_fund/data/prices.py — refresh_prices orchestrator (228 lines)"
    - "tests/unit/data/test_yfinance_provider_ohlcv.py — 5 unit tests (110 lines)"
    - "tests/unit/data/test_prices_ingest.py — 5 unit tests (227 lines)"
  modified:
    - "src/ls_equity_fund/config.py — added DataConfig.yfinance_max_workers (Field default=8, ge=1, le=32)"
    - "src/ls_equity_fund/data/__init__.py — re-export refresh_prices, YFinanceProvider, YFinanceError"
    - "src/ls_equity_fund/data/providers/__init__.py — re-export YFinanceProvider, YFinanceError"
    - "config.yaml.example — added data.yfinance_max_workers: 8"
decisions:
  - "curl_cffi mandatory: Yahoo bot-detects standard requests.Session; impersonate='chrome' is the only reliable transport for current yfinance"
  - "Tenacity policy: stop_after_attempt(3) + wait_exponential(min=1, max=8). After exhaustion, raise YFinanceError; orchestrator catches and continues"
  - "Incremental window: MAX(date) per ticker. last_stored_date == today => SKIP. last is None => fetch lookback_years*366 days back"
  - "ThreadPoolExecutor with config.data.yfinance_max_workers (default 8) — chosen to balance throughput vs Yahoo bot-detection. yfinance threads=False inside the pool to avoid pool-of-pools"
  - "Per-ticker failures log+continue (do not abort daily run). Operator review via refresh_state.status='FAILED' rows"
  - "yfinance end-date is exclusive — pass end + 1 day to download() to make caller semantics inclusive"
  - "auto_adjust=False explicit — store unadjusted OHLCV + adj_close separately so dividend/split adjustments are reproducible at query time (T-01-12 disposition: accept-with-known-limit)"
  - "Stubs for FundamentalsProvider/ShortInterestProvider/EstimatesProvider raise NotImplementedError with 'Filled by Plan 01-XX' messages — clear contract for Plans 05/07"
metrics:
  duration_minutes: ~12
  tasks_completed: 2
  tests_added: 10
  tests_passing: 10
  files_created: 4
  files_modified: 4
  lines_added: 817
completed: 2026-05-04
---

# Phase 01 Plan 04: OHLCV Ingestion (yfinance + curl_cffi + tenacity) Summary

**One-liner:** YFinanceProvider implementing OHLCVProvider ABC with curl_cffi TLS impersonation and tenacity 3-retry exponential backoff, plus refresh_prices orchestrator that incrementally refreshes daily_prices for `universe ∪ benchmarks` via ThreadPoolExecutor with log-and-continue per-ticker failure handling.

## What Shipped

### Task 1 — YFinanceProvider concrete class (commit `50d387e`, RED `d98cd31`)

`src/ls_equity_fund/data/providers/yfinance_provider.py` (238 lines):

- **Multi-ABC implementation**: inherits OHLCVProvider, FundamentalsProvider, ShortInterestProvider, EstimatesProvider. OHLCV is filled by this plan; the other three raise `NotImplementedError("Filled by Plan 01-05")` / `01-07` so future plans extend this class instead of creating sibling providers.
- **curl_cffi transport**: constructor builds `curl_cffi.requests.Session(impersonate="chrome")` by default. ImportError logs warning + falls back to None session (yfinance default) so test environments without curl_cffi do not break.
- **Session injection**: tests inject sentinel `object()` to avoid touching network; `yf.download` is monkeypatched at the call site.
- **Tenacity decorator**: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)` on `_download_with_retry`. Public `get_prices` catches `RetryError` and re-raises as `YFinanceError` for caller-friendly semantics.
- **Empty-frame retry trigger**: `yf.download` returning empty DataFrame raises `ValueError`, which tenacity treats as retry. Yahoo returns empty when rate-limited mid-stream, so this is the actual failure mode we mitigate.
- **MultiIndex normalization**: `_normalize_to_panel` flattens both single-ticker (flat columns) and multi-ticker (level-0=ticker, level-1=field) yfinance shapes into canonical `MultiIndex(['ticker','date'])` with lower_snake_case column names + `adj_close` rename.
- **`get_last_stored_date`**: SELECT MAX(date) FROM daily_prices WHERE ticker=? — returns `date | None`. Used by orchestrator for incremental window.
- **Config**: added `DataConfig.yfinance_max_workers: int = Field(default=8, ge=1, le=32)` and mirrored in `config.yaml.example`.

### Task 2 — refresh_prices orchestrator (commit `de73214`, RED `e9d7e02`)

`src/ls_equity_fund/data/prices.py` (228 lines):

- **Signature**: `refresh_prices(config, conn=None, *, tickers=None, today=None, provider=None) -> dict[str, int]` returning `{"ok", "failed", "skipped", "rows_written"}`.
- **Default ticker set**: `universe WHERE delisted_date IS NULL UNION benchmarks` — delisted names excluded (would 404 yfinance and waste rate-limit budget).
- **Incremental window**: `last = provider.get_last_stored_date(t)`; `None` → `today - lookback_years*366d`; `last >= today` → SKIPPED (no fetch); else `last + 1d` → today.
- **ThreadPoolExecutor**: `max_workers = config.data.yfinance_max_workers`. `as_completed` loop drains futures; per-future try/except so one ticker's exception cannot leak into the others.
- **Persistence**: `INSERT OR IGNORE INTO daily_prices` on (ticker, date) PK so partial re-runs are idempotent. `INSERT OR REPLACE INTO refresh_state` per ticker with status OK/FAILED/SKIPPED.
- **Log+continue**: `except YFinanceError` and bare `except Exception` both record FAILED with truncated `last_error[:500]` (T-01-13 mitigation), bump `failed` counter, and continue to next ticker. Daily run cannot be killed by transient yfinance issues.
- **NaN safety**: `_f` and `_i` row-coercion helpers use `v != v` NaN-detection (avoids importing math/numpy just for isnan) and convert to `None` to map to SQLite NULL cleanly.

## TDD Gate Compliance

Plan executed full RED → GREEN cycle for both tasks (no REFACTOR commits needed):

1. RED — Task 1: `d98cd31 test(01-04): add failing tests for YFinanceProvider OHLCV (RED)`
2. GREEN — Task 1: `50d387e feat(01-04): YFinanceProvider with curl_cffi + tenacity (GREEN)`
3. RED — Task 2: `e9d7e02 test(01-04): add failing tests for refresh_prices orchestrator (RED)`
4. GREEN — Task 2: `de73214 feat(01-04): refresh_prices orchestrator with incremental + ThreadPoolExecutor (GREEN)`

## Verification Results

**Plan-scope unit tests** (`uv run pytest tests/unit/data/test_yfinance_provider_ohlcv.py tests/unit/data/test_prices_ingest.py -v`): **10/10 passed in 4.67s**

| Test | Verifies |
|------|---------|
| `test_yfinance_provider_implements_ohlcv_abc` | Multi-ABC inheritance — passes `isinstance(p, OHLCVProvider)` |
| `test_get_prices_normalizes_multiindex` | `yf.download` mocked → MultiIndex(['ticker','date']) shape with `adj_close` column |
| `test_get_prices_retries_then_raises_yfinance_error` | ConnectionError side_effect → tenacity retries → final `YFinanceError` raise |
| `test_get_last_stored_date_reads_max` | Inserts two AAPL rows, asserts MAX = 2026-01-15; absent ticker returns None |
| `test_unfilled_methods_raise_with_plan_reference` | Stubs raise NotImplementedError matching `01-05` and `01-07` regex |
| `test_refresh_writes_rows_and_updates_refresh_state` | 2 rows persisted, refresh_state row = ('OK', '2026-04-02') |
| `test_refresh_skips_when_already_current` | `last_stored_date == today` → skipped=1, `get_prices.assert_not_called()` |
| `test_refresh_logs_and_continues_on_yfinance_error` | AAPL ok=1 + BADTICK failed=1 in same run; FAILED row contains "bot detection" |
| `test_refresh_uses_universe_and_benchmarks_by_default` | tickers=None loads AAPL (universe) + SPY (benchmarks) → ok=2 |
| `test_refresh_excludes_delisted_universe_tickers` | Delisted ENRN row in universe → not fetched, ok=0 |

**Integration smoke** (`uv run pytest tests/integration/ -q`): **25/25 passed in 2.37s** — no regressions from Phase 0 or Plan 01-01 schema.

## Acceptance Criteria — All Met

| Criterion | Result |
|-----------|--------|
| `class YFinanceProvider` count = 1 | 1 |
| ABC inheritance count >= 4 | 15 (multiple references + imports) |
| `@retry` count >= 1 | 2 |
| `stop_after_attempt(3)` count = 1 | 1 |
| `curl_cffi` references >= 1 | 9 |
| `impersonate` references >= 1 | 3 |
| `def refresh_prices` count = 1 | 1 |
| `ThreadPoolExecutor` count = 1 | 3 (import + use + as_completed) |
| `INSERT OR IGNORE INTO daily_prices` count = 1 | 1 |
| `delisted_date IS NULL` count >= 1 | 1 |
| `except YFinanceError` count >= 1 | 1 |
| Tests pass: 10 plan-scope, 25 integration | 10 + 25 |

## Threat Model — Mitigations Applied

| Threat ID | Disposition | Implementation |
|-----------|-------------|----------------|
| T-01-11 (DoS via Yahoo bot-detection) | mitigate | `curl_cffi.requests.Session(impersonate="chrome")` + tenacity 3-attempt exponential backoff + per-ticker log+continue (one ticker's IP-block does not kill the run; failure is captured to `refresh_state.status='FAILED'` for operator review) |
| T-01-12 (silently auto-adjusted prices) | accept-with-known-limit | `auto_adjust=False` explicit; raw OHLCV + `adj_close` stored separately so corp-action adjustments are reproducible at query time. Corp-action gap (D7) deferred to v2. |
| T-01-13 (info disclosure via last_error) | mitigate | `last_error` truncated to 500 chars at insert. structlog redaction processor (per Phase 0) further scrubs known secret patterns. |

## Deviations from Plan

None — plan executed exactly as written. No Rule 1/2/3 auto-fixes triggered. No Rule 4 architectural questions raised.

## Known Stubs

The following methods on `YFinanceProvider` raise `NotImplementedError` by design (deliberate seams for downstream plans, NOT incomplete work):

- `get_fundamentals(ticker)` — filled by Plan 01-05
- `get_short_interest(ticker, asof)` — filled by Plan 01-07
- `get_estimates(ticker, asof)` — filled by Plan 01-07
- `get_next_earnings_dates(ticker, lookahead_days)` — filled by Plan 01-07

Each stub raises with a `"Filled by Plan 01-XX"` string that the unit tests assert against, so accidental upstream callers fail loudly with a precise pointer. This is the deliberate provider-seam pattern from Plan 01-01.

## Threat Flags

None — no new security-relevant surface beyond the threats already enumerated in `<threat_model>`.

## Self-Check: PASSED

**Files exist:**
- `src/ls_equity_fund/data/providers/yfinance_provider.py` — FOUND
- `src/ls_equity_fund/data/prices.py` — FOUND
- `tests/unit/data/test_yfinance_provider_ohlcv.py` — FOUND
- `tests/unit/data/test_prices_ingest.py` — FOUND

**Commits exist:**
- `d98cd31` — FOUND (Task 1 RED)
- `50d387e` — FOUND (Task 1 GREEN)
- `e9d7e02` — FOUND (Task 2 RED)
- `de73214` — FOUND (Task 2 GREEN)

**Tests pass:** 10/10 plan-scope + 25/25 integration on `worktree-agent-a1e8d1e2438866e4a`.
