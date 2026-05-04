# Technology Stack — Meridian Capital Partners (`ls_equity_fund`)

**Project:** Single-operator long/short US equity factor-model hedge fund system
**Researched:** 2026-05-04
**Researcher:** gsd-project-researcher (stack dimension)
**Overall confidence:** HIGH (versions verified live against PyPI 2026-05-04)

---

## Summary

The 2026 canonical Python stack for this system is opinionated and largely non-negotiable because the spec already prescribes most of it (yfinance, EDGAR, scipy SLSQP, Anthropic SDK, Streamlit, IBKR, SQLite). My job is to fill in the *unspecified* glue (project tooling, ORM-or-not, scheduling library, config validation, logging, testing) and to pin specific 2026 versions for the spec-mandated pieces while flagging deprecations.

**Headline calls:**

1. **Python 3.11** is the floor (spec mandate; `numpy 2.x` and `pandas 3.x` require 3.11+).
2. **uv** wins over poetry / pip-tools for project tooling — single binary, 10-100x faster, native Python version management, lockfile is platform-complete.
3. **`ib_async` (NOT `ib_insync`)** — `ib_insync`'s author Ewald de Wit died in early 2024; `ib_async` is the maintained fork under `ib-api-reloaded`, last release 2.1.0 on 2025-12-08. The spec says "`ib_insync`" — treat that as historical naming and use `ib_async`.
4. **`edgartools` for SEC filings, NOT `sec-edgar-downloader` or roll-your-own** — `edgartools` ships structured Python objects for 10-K/10-Q/8-K/Form 4/13F with built-in rate-limiting; the alternatives either dump raw HTML/XML or are stale (`sec-edgar-api` last updated 2023).
5. **Pin `pandas==2.2.x` and `numpy<2.5`, not the bleeding edge.** pandas 3.0 (Jan 2026) introduced breaking copy-on-write semantics and a new string dtype; `statsmodels` needed a patch release. We do not need pandas 3.0 features. Stability wins.
6. **No ORM.** SQLite via `sqlite3` stdlib + plain SQL. SQLAlchemy/SQLModel buy nothing at this scope and cost migration mental tax.
7. **`launchd` (not cron, not APScheduler) for the daily refresh.** Cron does not run jobs while machine sleeps; `launchd` does (`StartCalendarInterval`).
8. **Anthropic SDK ≥ 0.42** for the typed `cache_control: {"type": "ephemeral"}` API; spec mandates `claude-sonnet-4-5` as default model ID (newer 4-6 exists but is out of spec scope).

---

## Recommended Stack

### Runtime + Project Tooling

| Component | Version | Purpose | Why |
|---|---|---|---|
| **Python** | `3.11.x` (floor); `3.12` OK | Interpreter | Spec mandate; required by `scipy 1.17`, `numpy 2`, `pandas 3` |
| **uv** | `0.11.8` | Dependency manager + venv + Python install | Single binary replaces pip/pyenv/virtualenv/pip-tools/poetry; 10-100x faster; resolution-complete cross-platform `uv.lock` |
| **`pyproject.toml`** | PEP 621 layout | Project metadata + deps | Standard; works with uv natively |
| **`uv.lock`** | committed | Reproducible installs | Cross-platform, includes every variant; never re-resolve on different OS |
| **Ruff** | latest (`uv tool install ruff`) | Linter + formatter | Fast; replaces black/flake8/isort; default for new 2026 Python projects |

### Market Data

| Component | Version | Purpose | Why |
|---|---|---|---|
| **yfinance** | `0.2.6x` series — pin a *specific* known-good version, do **not** float | OHLCV + fundamentals + short interest + analyst estimates | Spec mandate. Note: PyPI shows `1.3.0` (Apr 2026) — yfinance has had a turbulent 2025-2026 with frequent rate-limit changes, the `curl_cffi` migration, and the `request_cache` break. Pin and isolate behind data interface. |
| **`curl_cffi`** | latest compatible with chosen yfinance | TLS impersonation transport for yfinance | Required by recent yfinance to bypass Yahoo's bot detection; standard `requests.Session` no longer works |
| **Data interface seam** | first-party `MarketDataProvider` ABC | Abstract OHLCV / fundamentals / short interest behind one interface | Spec mandate — paid swap-in (Polygon/Tiingo/IEX) without rewriting downstream |

### SEC EDGAR + 13F + Form 4

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`edgartools`** | `5.30.x` | 10-K, 10-Q, 8-K, Form 4, 13F parsing → structured Python objects | One library covers all spec-required forms with structured DataFrames; built-in compliance with EDGAR's 10 req/sec rule + User-Agent. Active dev (last release 2026-04-29). |
| `sec-edgar-downloader` | `5.1.0` | (alternative — only filing download, no parsing) | Use only if `edgartools` parsing fails on a specific filing and you need raw bytes |
| `lxml` | `6.1.0` | XML parsing fallback for Form 4 raw XML | Backstop when `edgartools` Form 4 schema changes; well-maintained |
| **User-Agent string** | required | EDGAR compliance | `"Meridian Capital Partners contact@example.com"` — non-optional, EDGAR 403s without it |

### IBKR Execution

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`ib_async`** | `2.1.0` (2025-12-08) | IBKR TWS/Gateway client (sync + async) | Maintained successor to `ib_insync`; same API surface; supports paper port 7497 / live port 7496 separation natively |
| **TWS or IB Gateway** | latest stable | Local broker bridge | Required by `ib_async`; spec acknowledges TWS/Gateway path |
| ~~`ib_insync`~~ | `0.9.86` (last release **2023-07-02**) | DO NOT USE | Author deceased early 2024; project moved to `ib_async`. Library is dead. |
| ~~`ibapi`~~ | `9.81.1.post1` (2020) | DO NOT USE directly | Official but raw; massive boilerplate for callbacks; no async; `ib_async` wraps it cleanly |
| ~~Client Portal Web API~~ | — | NOT for this project | Requires a separate gateway process + browser auth flow + session keepalive; TWS/Gateway path is simpler for a desktop solo operator |

### Optimization + Statistics

| Component | Version | Purpose | Why |
|---|---|---|---|
| **scipy** | `1.17.x` | `optimize.minimize(method="SLSQP")` for MVO; `linalg` for covariance ops | Spec mandate. SLSQP API stable across 1.16→1.17; no breaking changes flagged. |
| **numpy** | `>=2.0,<2.5` | Numerical core | Pin sub-2.5 for ecosystem stability. numpy 2 ABI break is past us; statsmodels 0.14.4+ supports it. |
| **pandas** | `>=2.2,<3.0` | DataFrames | **Do NOT jump to pandas 3.0** (Jan 2026 release introduced copy-on-write + new string dtype + breaking inplace returns). 2.2.x is stable, mature, and statsmodels-compatible without surprises. Revisit pandas 3 in a future milestone. |
| **statsmodels** | `0.14.6` | Cross-sectional Barra-style factor regressions (OLS / WLS) | Standard for econometric regressions; 0.14.6 is patched for numpy 2 + pandas 3 compatibility |
| `cvxpy` | `1.8.x` | (optional alternative MVO) | NOT required; spec says SLSQP. Keep on the bench in case SLSQP's nonlinear-equality constraint handling proves brittle for sector-neutrality + dollar-neutrality + beta-neutrality joint constraints. Convex MVO is cvxpy's home turf. |

### Anthropic Claude

| Component | Version | Purpose | Why |
|---|---|---|---|
| **anthropic** | `0.97.0` (or `>=0.42`) | Claude API client | Native `cache_control` typing since 0.42; current 0.97 is stable. Async client supports concurrency for fan-out across 4 analyzers × 40 tickers. |
| **Model ID** | `claude-sonnet-4-5` | Default analyzer model | Spec mandate. Configurable via `config.yaml` so 4.6 / future models can drop in. |
| **Prompt caching** | `cache_control: {"type": "ephemeral"}` on system prompt content blocks | Cost ceiling enforcement | Spec mandate — load-bearing. 5-min TTL default; 1h TTL via `"ttl": "1h"` if a daily run exceeds 5min. Cache reads at 0.1× input, writes at 1.25×. **Critical: `system` must be a list of content blocks (not a plain string) for `cache_control` to bind.** |
| **`tenacity`** | `9.1.4` | Retry/backoff for Anthropic + EDGAR + IBKR | Decorator-based, supports exponential backoff + jitter + tenant-specific retry conditions |

### Storage

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`sqlite3`** (stdlib) | bundled with Python | All persistence | Spec mandate (local SQLite). Stdlib is sufficient; no async needed at daily cadence; thin DAO functions per table |
| **NOT SQLAlchemy / SQLModel** | — | — | At this scope (single-process, write-once-per-day, ~30 tables max), an ORM adds complexity without benefit. Plain SQL + `sqlite3.Row` factory + parameterized queries are clearer to audit (audit is a spec requirement). Revisit if/when the data layer grows beyond 50 tables. |

### Configuration + Secrets

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`pydantic`** | `2.13.3` | Typed config models (`Config`, `RiskConfig`, `BrokerConfig`, etc.) | v2 is fast (Rust core); validation at boot prevents 30-min runs that crash on a bad config field |
| **`pydantic-settings`** | latest | Bridge env vars + `config.yaml` → typed pydantic models | Single load path; nested `env_nested_delimiter="__"` for `BROKER__PAPER_PORT` style |
| **`PyYAML`** | `6.0.3` | Read `config.yaml` | Standard. `safe_load` only. |
| ~~`ruamel.yaml`~~ | — | NOT needed | Only valuable if you need to *write back* YAML preserving comments/order. We read; we don't round-trip. |
| **`python-dotenv`** | `1.2.2` | Load `.env` for `ANTHROPIC_API_KEY`, `IBKR_*` | Spec mandate (`.env` gitignored). Load before pydantic-settings. |

### Scheduling

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`launchd`** plist | OS-native | Daily 17:15 weekday refresh | Spec mandate. `StartCalendarInterval` with `Hour=17 Minute=15 Weekday=1..5`. Crucially, **launchd runs jobs when machine wakes from sleep — cron does not**. Operator's MacBook will sleep at 17:15. |
| ~~cron~~ | — | NOT used | Misses jobs during sleep; legacy on macOS |
| ~~APScheduler / `schedule`~~ | — | NOT used | Requires a daemon process always running; launchd already is the daemon |
| **plist location** | `~/Library/LaunchAgents/com.meridian.ls-equity.daily.plist` | User-scope agent | Loads at login automatically; `launchctl bootstrap gui/$(id -u)` to install |

### Logging + Observability

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`structlog`** | `25.5.0` | All logging | Structured JSON output, processor pipeline (PII masking, run-id binding, timestamp injection), `bind_contextvars` for per-run correlation. Critical for audit trail per spec ("every order, every veto, every circuit-breaker event must be persisted with timestamp + reason"). |
| **stdlib `logging`** | bundled | Backbone (`structlog` writes to it) | structlog is configured to render through stdlib's `logging.Logger` so third-party libs (anthropic, ib_async, requests) flow into the same pipeline |
| **Per-run audit DB** | SQLite table | Orders, vetoes, breakers | Beyond logs — structured rows for queryable audit. Spec mandate. |

### Testing

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`pytest`** | `9.0.3` | Test runner | Standard. v9 (Apr 2026) is current. |
| **`pytest-asyncio`** | `1.3.0` | Async tests for `ib_async` event loop | `ib_async` is fundamentally async-first; sync API is a wrapper |
| **`freezegun`** | `1.5.5` | Time-based test control | Required for testing 30/60/90-day rolling estimate revisions, 90-day insider net flow, FOMC blackout windows, earnings calendars |
| **`responses`** | `0.26.0` | Mock HTTP for EDGAR + Federal Reserve calendar | Plays nicely with `requests`; for `httpx` (used internally by anthropic SDK) use `respx` if needed |
| **`pytest-cov`** | latest | Coverage | Standard; aim ≥80% on L4 (portfolio construction) and L5 (risk vetos) — these are correctness-critical |

### Type Checking

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`mypy`** | `1.20.2` | Type checker | Mature, well-integrated with pydantic plugin |
| **Strictness** | `--strict` on `src/risk/` and `src/portfolio/`; relaxed elsewhere | Targeted strictness | Risk + portfolio are the load-bearing correctness layers; full-strict on data ingestion fights yfinance/edgartools' loose typing for no win |
| `pyright` | `1.1.409` | (alternative — Microsoft's, faster) | Use only if mypy proves too slow; mypy is the conventional Python choice |

### Dashboard

| Component | Version | Purpose | Why |
|---|---|---|---|
| **`streamlit`** | `1.57.0` | Localhost dashboard at `:8502` | Spec mandate. Native multi-page, dark theme, session state, `st.experimental_set_query_params` for deep links |
| **`@st.cache_data`** | built-in | DataFrames, query results, API JSON | Default for almost everything. Returns copies; pickled cache. |
| **`@st.cache_resource`** | built-in | DB connections, Anthropic client, ib_async `IB` instance | Singleton-style; stores object itself. **Critical: use this for the SQLite connection and any persistent client.** |
| **Custom CSS injection** | `st.markdown(..., unsafe_allow_html=True)` with `<style>` block | Hide chrome (footer, "Made with Streamlit", hamburger), JARVIS dark theme tokens | Spec calls for institutional dark theme + 6 Roman-numeral pages. Standard pattern. |
| **Auto-refresh during market hours** | `st.autorefresh` (in `streamlit-autorefresh` extra) or HTML meta-refresh | 5-min refresh 9:30-16:00 ET | Spec mandate. Conditional on market-hours check. |

---

## Detailed Rationale (Why each choice beats alternatives)

### Why uv beats poetry for this project

Three reasons specific to this project:

1. **Lockfile portability**: `uv.lock` records every platform's resolution, so a future migration to a Linux box (CI, paid cloud worker) does not require re-resolving and discovering version drift. Poetry's lockfile is cross-platform but not resolution-complete in the same way.
2. **No separate pyenv**: macOS ships an old Python; this project requires 3.11+. uv installs the Python interpreter itself (`uv python install 3.11`), which removes a whole class of "wrong Python" bugs. Poetry needs pyenv + a `pyenv local` file.
3. **Speed matters in this loop**: When the daily run fails and you're iterating on a fix at 17:30 with 16:00-tomorrow as the next attempt, `uv sync` in 2s vs `poetry install` in 90s materially affects iteration rate.

### Why edgartools, not roll-your-own EDGAR

The spec requires 10-K, 10-Q, 8-K, Form 4 (with P/S/A/M/F transaction codes), 13F (institutional holdings, fund-tracking, multi-fund openings). That is a *lot* of XML schema variation. EdgarTools handles all of it with structured Python objects + DataFrames. Rolling your own:

- Form 4 XML schema changed at least twice in the last 5 years (XBRL adoption)
- 13F has multiple table formats (XML primary doc, INFORMATION TABLE secondary, sometimes nested)
- 10-K Risk Factors section is in HTML/iXBRL; locating it requires section-tag detection
- EdgarTools already implements EDGAR's 10 req/sec compliance + User-Agent header handling

Time saved: weeks of dev. Risk reduced: schema-change robustness handled upstream.

### Why ib_async, definitively, not ib_insync

`ib_insync`'s creator Ewald de Wit died in early 2024. PyPI shows `0.9.86` last released 2023-07-02. The codebase is unmaintained. `ib_async` is the same API, same import patterns, same docs — under new maintainership at `github.com/ib-api-reloaded/ib_async`. Migration is `pip uninstall ib_insync && pip install ib_async` plus updating the import. The spec text says "`ib_insync`" because it's the historically dominant name; treat the spec as semantically meaning "the ib_insync API" and use ib_async.

### Why pin pandas 2.2.x, not jump to 3.0

Pandas 3.0 shipped Jan 21, 2026 with three breaking changes that bite this project specifically:

1. **Copy-on-write default** — changes how chained assignment behaves; risk of silent quiet data corruption in the factor pipeline
2. **`str` dtype** — separate from `object`; breaks `df.dtypes == 'object'` checks that are common in factor scoring code
3. **In-place returns** — `fillna(inplace=True)` now returns `self` instead of `None`, so `x = df.fillna(0, inplace=True)` flips behavior

`statsmodels` needed a patch (0.14.5+) to even import on pandas 3.0. There is zero pandas-3.0-only feature this project needs. Pin to `pandas>=2.2,<3.0` for v1 stability.

### Why no ORM

The data model:

- ~30 tables (prices, fundamentals, factor scores, positions, orders, vetoes, etc.)
- Single writer per day (the launchd job)
- Read-heavy from the dashboard
- Audit trail must be exact and queryable in raw SQL for compliance review

SQLAlchemy/SQLModel buys: object-oriented query syntax. It costs: another mental model, lazy-loading footguns, harder-to-audit generated SQL, ORM-aware migrations.

For this scope, plain `sqlite3` with a thin `repositories/` layer (one module per table, plain SQL strings, parameterized) is auditable, debuggable in `sqlite3` CLI directly, and zero-magic. If the schema grows past ~50 tables or this becomes a multi-process system, revisit.

### Why launchd, not APScheduler

APScheduler requires a Python process running 24/7. On a MacBook that sleeps, that process either dies or misses its trigger. `launchd` is the OS scheduler — it knows about sleep/wake, runs missed `StartCalendarInterval` jobs after wake, integrates with macOS power management, and survives reboots without manual intervention. Spec already calls for launchd; this is the right call.

### Why structlog, not stdlib alone

The spec requires audit logs of every order, veto, and circuit-breaker event with reason. That's structured data — `event="veto", ticker="XYZ", reason="LIQUIDITY_FAIL", adv_pct=12.4` — not free-text strings. structlog's processor pipeline:

- Renders JSON in production, colorized key=value in dev
- Binds run-id once via `bind_contextvars(run_id=...)` so every log line in the run carries it
- Lets you write a single processor that mirrors veto/breaker events into the audit SQLite table
- Plays nicely with stdlib (third-party loggers flow through)

Stdlib alone gets you there with more boilerplate and more risk of inconsistent fields.

---

## Anti-Recommendations (Do NOT use)

| Library / Pattern | Why NOT |
|---|---|
| **Alpaca SDK / Alpaca short-availability flags** | Spec mandate to use IBKR-native borrowability. Alpaca data is wrong broker-of-record; its borrow flags reflect Alpaca's lender pool, not IBKR's. |
| **Hardcoded FOMC dates** | Spec mandate — pull from Federal Reserve's `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` (HTML) or RSS. Hardcoded dates rot annually. |
| **Hardcoded sector ETF lists** | Spec mandate — config.yaml. Reduces code change for sector-mapping updates. |
| **`ib_insync` (post-2024)** | Author deceased; library frozen since 2023-07. Use `ib_async`. |
| **Client Portal Web API as primary IBKR transport** | Adds a second daemon (CP Gateway) + browser-OAuth flow + 24h session expiry handling. TWS/Gateway path with `ib_async` is one less moving part. CP API is an option later if going headless on a server. |
| **Generic ML libraries (sklearn, lightgbm, xgboost)** | This is a factor-model + Claude-LLM system, not an ML system. No supervised training in v1. Adding sklearn invites scope creep ("just one quick model") and sklearn drags numpy ABI compatibility headaches. |
| **`backtrader`, `zipline`, `vectorbt`** | This is a *live system*, not a backtester. The spec does not require backtesting in v1. Adding a backtest framework is a separate research milestone with its own pitfalls (point-in-time data, survivorship bias, look-ahead leaks). |
| **`pandas-ta` / `ta-lib` for technical indicators** | The 8 factors are fundamental + sentiment, not technical. Momentum factors are simple price-return calcs (`pct_change`, `rolling`) that pandas does natively. Don't pull in a 200-indicator library for 6 indicators. |
| **`celery` / `rq` for task queues** | Single-process daily job. No queue needed. |
| **`fastapi` / `flask`** | The dashboard *is* the UI. No separate API server. Streamlit is enough. |
| **`docker`** | Spec explicitly says macOS-only, no Docker, no cloud. |
| **Hardcoded LP letterhead / 506(b)/(c) compliance** | Out of spec scope (single operator, no real LPs). Letter is dual-mode markdown only. |
| **`requests-cache`** | Broken with current yfinance (yfinance now uses `curl_cffi`). Use yfinance's built-in caching + your own SQLite layer. |
| **`pandas` 3.0** | See above. Not yet for v1. |
| **SQLAlchemy / SQLModel for v1** | See above. Not for this scope. |
| **`ruamel.yaml`** | Only useful for round-tripping comments. We read config; we don't write it. |
| **`schedule` library** | In-process scheduler. Same pitfall as APScheduler — process must stay alive. |

---

## 2025–2026 Watch List (Deprecations + Breaking Changes)

| Item | Status | Action |
|---|---|---|
| **`ib_insync` → `ib_async`** | Migration **required** (original lib unmaintained since 2023-07) | Use `ib_async==2.1.0`. Spec text saying "`ib_insync`" is historical. |
| **pandas 3.0** (Jan 2026) | Breaking: copy-on-write, `str` dtype, in-place returns | Pin `pandas>=2.2,<3.0` for v1. Revisit in a future milestone with explicit migration plan. |
| **numpy 2.x ABI break** | Past us; statsmodels 0.14.4+ supports it | Use `numpy>=2.0,<2.5`. Verify any compiled deps via `pip check` after install. |
| **yfinance `curl_cffi` migration + `request_cache` break** | yfinance no longer compatible with `requests-cache` sessions | Use yfinance's native session handling; add SQLite caching layer in your own data interface. **Pin a specific yfinance version** — every minor release in 2025 had behavior changes. |
| **EDGAR rate limit: 10 req/sec, User-Agent required** | Active since 2021, enforced 2025 | `edgartools` handles this; if you bypass, **always** set `User-Agent: "Meridian Capital Partners contact@example.com"` and rate-limit to <10/sec or you get a 10-minute IP block. |
| **Anthropic SDK `cache_control` typing** | Stable since 0.42 | Use `>=0.42`. Current is 0.97. `system` must be a content-block list for caching to bind. |
| **Claude model deprecation** | Sonnet 4.6 released; spec uses 4.5 | Configurable via `config.yaml`. Watch for Sonnet 4.5 EOL announcement; plan to switch default within 30 days of Anthropic deprecation notice. |
| **scipy 1.17 SLSQP** | API stable; no breaking changes | Pin `scipy>=1.16,<1.18`. |
| **Python 3.13 GC pause** | Not yet a factor at our scope | 3.11 / 3.12 are fine. Don't chase 3.13 until ecosystem catches up (statsmodels, scipy, pandas all need wheels). |
| **`pytest` 9.0** (Apr 2026) | Some plugin compatibility shifts | If a plugin breaks, pin `pytest<9` until the plugin is updated. |

---

## Installation (Reference)

```bash
# 1. Install uv (one-line, system-wide)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Init the project
cd "Youtube Hedge Fund App"
uv init --python 3.11
uv python install 3.11

# 3. Add runtime deps
uv add \
  "yfinance==0.2.65" \
  "curl_cffi" \
  "edgartools>=5.30,<6" \
  "ib_async>=2.1,<3" \
  "anthropic>=0.97,<1" \
  "scipy>=1.16,<1.18" \
  "numpy>=2.0,<2.5" \
  "pandas>=2.2,<3.0" \
  "statsmodels>=0.14.6" \
  "streamlit>=1.57,<2" \
  "pydantic>=2.13,<3" \
  "pydantic-settings>=2.14,<3" \
  "PyYAML>=6.0.3,<7" \
  "python-dotenv>=1.2,<2" \
  "structlog>=25.5,<26" \
  "tenacity>=9.1,<10" \
  "lxml>=6.1,<7"

# 4. Add dev deps
uv add --dev \
  "pytest>=9.0,<10" \
  "pytest-asyncio>=1.3,<2" \
  "pytest-cov" \
  "freezegun>=1.5,<2" \
  "responses>=0.26,<1" \
  "mypy>=1.20,<2" \
  "ruff"

# 5. Lock + install
uv sync

# 6. Run anything
uv run python -m src.daily_refresh
uv run streamlit run src/dashboard/app.py --server.port 8502
uv run pytest
```

---

## Confidence Index

| Component | Recommendation | Confidence | Source |
|---|---|---|---|
| Python 3.11+ | uv-managed | HIGH | Spec mandate + PyPI requires_python |
| uv 0.11.x | Project tooling | HIGH | PyPI live + 2026 community consensus |
| yfinance pinned | Market data | HIGH | Spec mandate; pin justified by GitHub issue trail |
| edgartools 5.30 | SEC filings | HIGH | PyPI live; covers all spec form types |
| ib_async 2.1.0 | IBKR client | HIGH | PyPI live; ib_insync death documented; migration is import-rename |
| scipy 1.17 SLSQP | MVO | HIGH | Spec mandate; API stable |
| numpy 2.x / pandas 2.2.x | Numerical core | HIGH | Pin 2.2 explicitly to dodge 3.0 breakage |
| statsmodels 0.14.6 | Factor regressions | HIGH | PyPI live; numpy-2 compatible |
| anthropic 0.97 | Claude SDK | HIGH | PyPI live; cache_control verified in docs |
| `claude-sonnet-4-5` | Default model | HIGH | Spec mandate; configurable |
| sqlite3 stdlib (no ORM) | Persistence | HIGH | Scope-driven; spec says local SQLite |
| launchd plist | Scheduling | HIGH | Spec mandate; sleep-aware behavior verified |
| pydantic 2.13 + pydantic-settings | Typed config | HIGH | Industry standard for typed config in 2026 |
| PyYAML 6.0.3 | YAML read | HIGH | Standard; safe_load |
| structlog 25.5 | Logging | HIGH | Audit-trail requirements drive structured logging |
| pytest 9.0 + asyncio + freezegun + responses | Testing | HIGH | Standard; freezegun specifically required for time-based factors |
| mypy 1.20 (targeted strict) | Types | MEDIUM | Pyright is also defensible; mypy is conventional |
| streamlit 1.57 + cache_data/cache_resource | Dashboard | HIGH | Spec mandate; cache strategy verified in current docs |
| cvxpy as MVO fallback | Optimization | MEDIUM | Only if SLSQP non-convergence becomes pathological; spec says SLSQP + conviction-tilt fallback, not cvxpy |

---

## Sources

- [yfinance on PyPI](https://pypi.org/project/yfinance/) — verified version 1.3.0 / 2026-04-16
- [yfinance #2496 — request_cache breakage](https://github.com/ranaroussi/yfinance/issues/2496)
- [yfinance #2422 — YFRateLimitError](https://github.com/ranaroussi/yfinance/issues/2422)
- [Why yfinance keeps getting blocked (Trading Dude / Medium)](https://medium.com/@trading.dude/why-yfinance-keeps-getting-blocked-and-what-to-use-instead-92d84bb2cc01)
- [edgartools on PyPI](https://pypi.org/project/edgartools/) — 5.30.2 / 2026-04-29
- [edgartools — The Complete Guide to SEC Filings in Python (2026)](https://edgartools.readthedocs.io/en/stable/complete-guide/)
- [edgartools GitHub](https://github.com/dgunning/edgartools)
- [SEC EDGAR rate limit policy (10 req/sec, User-Agent required)](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits)
- [SEC EDGAR Rate Limits explained (dealcharts)](https://dealcharts.org/blog/edgar-scraping-rate-limits-explained)
- [ib_async on PyPI](https://pypi.org/project/ib_async/) — 2.1.0 / 2025-12-08
- [ib_async GitHub (replaces ib_insync)](https://github.com/ib-api-reloaded/ib_async)
- [ib_insync — author deceased note](https://github.com/mattsta/ib_insync)
- [pysystemtrade discussion: should we change from ib_insync to ib_async?](https://github.com/robcarver17/pysystemtrade/discussions/1577)
- [scipy on PyPI](https://pypi.org/project/scipy/) — 1.17.1 / 2026-02-23
- [scipy SLSQP docs (1.17)](https://docs.scipy.org/doc/scipy/reference/optimize.minimize-slsqp.html)
- [pandas 3.0.0 release notes (Jan 21, 2026)](https://pandas.pydata.org/docs/whatsnew/v3.0.0.html)
- [pandas 3.0 breaking changes summary (Real Python)](https://realpython.com/python-news-february-2026/)
- [statsmodels NumPy 2.0 support issue](https://github.com/statsmodels/statsmodels/issues/9194)
- [anthropic Python SDK on PyPI](https://pypi.org/project/anthropic/) — 0.97.0 / 2026-04-23
- [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Claude Sonnet 4.5 launch](https://www.anthropic.com/news/claude-sonnet-4-5)
- [How to add prompt caching to an Anthropic SDK app](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/)
- [streamlit on PyPI](https://pypi.org/project/streamlit/) — 1.57.0 / 2026-04-28
- [Streamlit caching overview (cache_data vs cache_resource)](https://docs.streamlit.io/develop/concepts/architecture/caching)
- [uv on PyPI](https://pypi.org/project/uv/) — 0.11.8 / 2026-04-27
- [uv vs pip vs Poetry (2026)](https://www.danilchenko.dev/posts/uv-vs-pip-vs-poetry/)
- [Best Python Package Managers 2026 (Scopir)](https://scopir.com/posts/best-python-package-managers-2026/)
- [launchd — Apple developer docs (Scheduling Timed Jobs)](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)
- [launchd vs cron on macOS (David Hamann)](https://davidhamann.de/2018/03/13/setting-up-a-launchagent-macos-cron/)
- [structlog on PyPI](https://pypi.org/project/structlog/) — 25.5.0 / 2025-10-27
- [structlog vs stdlib logging (BSWEN, 2026)](https://docs.bswen.com/blog/2026-04-29-structlog-vs-stdlib-logging/)
- [Choosing a Python Logging Library in 2026 (Dash0)](https://www.dash0.com/guides/python-logging-libraries)
- [pydantic-settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Form 4 transaction codes — StockTitan](https://www.stocktitan.net/articles/form-4-insider-transactions-guide)
- [TWS API Documentation (IBKR Campus)](https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/)
- [IBKR Client Portal Web API v1.0](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
