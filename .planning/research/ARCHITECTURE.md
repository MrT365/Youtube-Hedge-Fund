# Architecture Research

**Domain:** Single-operator long/short US equity quant hedge fund system, daily-cadence, paper-first IBKR
**Researched:** 2026-05-04
**Confidence:** HIGH (Python packaging, SQLite, repository pattern); MEDIUM (concrete L4↔L5 sequencing — depends on operator's tolerance for "MVO ships in milestone 2"); HIGH (IBKR client choice given recent ecosystem consolidation around `ib_async`)

---

## 1. Summary

The system is a **layered batch pipeline with a SQLite hub**. Seven layers exist conceptually but collapse into **one Python package, one SQLite database, one CLI tree**. Each layer is a sub-package with a public façade (`__init__.py`) and private internals; cross-layer calls go through the façade only. Inter-layer data crosses the seam as **typed Pydantic models for control / config objects** and **Pandas DataFrames with documented index conventions for tabular bulk data**. Persistence is the primary integration mechanism: each layer reads its inputs from SQLite tables and writes its outputs to SQLite tables, so layers can be developed, tested, and re-run independently.

The L4↔L5 dependency cycle (MVO needs L5 covariance; L5 risk-attribution needs L4 holdings) is broken by **shipping conviction-tilt L4 first** (zero risk-model dependency), then L5 risk model, then MVO as an L4 *strategy plug-in* swappable behind the same `Optimizer` interface. This is also exactly what the spec mandates as the non-convergence fallback, so it costs no architectural debt.

A `data/providers/` seam (abstract `MarketDataProvider`, concrete `YFinanceProvider`, future `PolygonProvider`) keeps L1 swappable. A `broker/` seam (abstract `Broker`, concrete `IBKRBroker` via `ib_async`) keeps L6 swappable and lets a `PaperBroker` mock run end-to-end before any IBKR connection exists.

Streamlit reads from the same SQLite via a thin **`dashboard/queries.py` read-only repository** — no service layer, no API, no separate process. JARVIS chat invokes Anthropic synchronously inside the Streamlit process with a 30s timeout and aggressive caching.

`launchd` runs **one entry point**: `python -m ls_equity_fund.cli daily-refresh --no-filings --no-13f`. That entry point is a meta-orchestrator that calls each layer's run function in sequence and writes a `runs` row with status/timing/error.

---

## 2. Component Diagram

```
                 ┌────────────────────────────────────────────────────────────────────┐
                 │                  CLI / Orchestration (Typer)                         │
                 │     daily-refresh • run-data • run-scoring • run-analysis            │
                 │     run-portfolio • run-execution • run-reporting                    │
                 └─────────────────────────────┬──────────────────────────────────────┘
                                               │
   ┌───────────────────────────────────────────┴──────────────────────────────────────┐
   │                               Application Layers                                  │
   │                                                                                    │
   │   L1 data   L2 factors   L3 analysis   L4 portfolio   L5 risk   L6 exec   L7 rpt  │
   │     │           │             │             │            │         │         │     │
   │     ▼           ▼             ▼             ▼            ▼         ▼         ▼     │
   │  providers/   scoring/    analyzers/   optimizers/   risk_model/ broker/   tear/  │
   │  ingest/      composer    cache/       state/        veto/       executor sheet   │
   │                                        rebalance/    breakers/   slippage         │
   │                                                                                    │
   └────────────────────────────┬───────────────────────────────────────────────────────┘
                                │  reads / writes (Pandas DF + Pydantic models)
                                ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │                          SQLite hub  (cache/ls_equity_fund.db)                │
   │   prices • fundamentals • insider • 13f • short_interest • estimates •         │
   │   earnings_cal • macro_cal • factor_scores • analysis_results •               │
   │   portfolio_positions • portfolio_history • position_approvals • orders •     │
   │   fills • slippage • veto_events • circuit_breaker_events • daily_attribution │
   │   • runs   (WAL mode, single-writer at a time, many readers)                   │
   └────────────────┬─────────────────────────────────────────────────┬────────────┘
                    │                                                 │
                    ▼                                                 ▼
   ┌─────────────────────────────────────┐         ┌─────────────────────────────┐
   │     Streamlit dashboard (8502)        │         │   output/  (CSV, MD, PDF)    │
   │   read-only via dashboard/queries.py  │         │   tear sheets, daily letter   │
   │   JARVIS chat: Anthropic sync         │         │   rebalance.csv, audit logs   │
   └─────────────────────────────────────┘         └─────────────────────────────┘

External: yfinance · SEC EDGAR · Federal Reserve · Anthropic API · IBKR TWS/Gateway
```

**Direction of flow:** CLI → layer → SQLite → next layer. No layer calls another layer's *internals* directly; layers communicate only via SQLite tables and the layer's public façade functions.

---

## 3. Module Layout

**Single repo, single `src/` tree, one installable package.** Each layer is a sub-package with a public `__init__.py` that re-exports the layer's façade; everything else is `_private` by convention.

```
ls_equity_fund/
├── pyproject.toml
├── README.md
├── config.yaml                  # all tunables (universe, factors, portfolio, risk, exec, reporting)
├── .env                         # ANTHROPIC_API_KEY, IBKR_*, SEC_USER_AGENT (gitignored)
├── .env.example
├── cache/                       # SQLite database lives here (gitignored)
│   └── ls_equity_fund.db
├── output/                      # CSV exports, markdown tear sheets, daily letters (gitignored)
├── logs/                        # rotating run logs (gitignored)
├── tests/
│   ├── unit/                    # mirrors src/ tree
│   ├── integration/             # SQLite + provider mocks
│   └── fixtures/                # canned price/fundamental/13F data
├── scripts/
│   ├── init_db.py               # idempotent schema bootstrap
│   ├── seed_universe.py
│   └── launchd/com.meridian.daily.plist
└── src/
    └── ls_equity_fund/
        ├── __init__.py
        ├── config.py            # Pydantic Settings; loads config.yaml + .env
        ├── db.py                # connection factory, WAL setup, migrations
        ├── schemas.py           # shared Pydantic models (FactorScores, Candidate, Position, Order, ...)
        ├── logging.py           # structured logging config
        │
        ├── data/                # L1 — Data Infrastructure
        │   ├── __init__.py      # public: refresh_all(), refresh_prices(), get_fundamentals(), ...
        │   ├── universe.py
        │   ├── providers/
        │   │   ├── base.py      # MarketDataProvider, FundamentalsProvider, FilingsProvider (abstract)
        │   │   ├── yfinance_provider.py
        │   │   ├── edgar_provider.py
        │   │   ├── fred_provider.py
        │   │   └── polygon_provider.py   # stub for future paid swap-in
        │   ├── insider.py       # Form 4 parser
        │   ├── institutional.py # 13F parser
        │   ├── short_interest.py
        │   ├── estimates.py
        │   ├── earnings_calendar.py
        │   └── macro_calendar.py
        │
        ├── factors/             # L2 — Scoring Engine
        │   ├── __init__.py      # public: compute_all(date), get_latest_scores()
        │   ├── momentum.py
        │   ├── value.py
        │   ├── quality.py
        │   ├── growth.py
        │   ├── revisions.py
        │   ├── short.py
        │   ├── insider.py
        │   ├── institutional.py
        │   ├── sector_rank.py   # GICS-percentile rank, the join key for everything downstream
        │   └── composer.py      # weighted composite + per-factor breakdown
        │
        ├── analysis/            # L3 — Claude AI Analysis
        │   ├── __init__.py      # public: analyze_candidates(tickers, run_id)
        │   ├── client.py        # Anthropic SDK wrapper with prompt caching + cost tracker
        │   ├── cache.py         # SQLite analysis_results cache (TTL 30d)
        │   ├── extractors.py    # JSON extraction across 3 wrap formats
        │   ├── analyzers/
        │   │   ├── filing.py    # forensic accounting on 8 quarters
        │   │   ├── risk.py      # 10-K Risk Factors diff
        │   │   ├── insider.py   # Form 4 interpretation
        │   │   ├── sector.py    # per-sector ranking
        │   │   └── earnings.py  # STUB — returns None
        │   ├── reports.py       # per-candidate markdown report writer
        │   └── combine.py       # 60% quant + 40% Claude → fallback to 100% quant
        │
        ├── portfolio/           # L4 — Portfolio Construction
        │   ├── __init__.py      # public: build_target_portfolio(date), generate_rebalance(date)
        │   ├── state.py         # PortfolioState (positions, history, approvals)
        │   ├── beta.py          # rolling 60d
        │   ├── exposures.py     # factor / sector exposure calculator
        │   ├── costs.py         # commission + spread + impact
        │   ├── optimizers/
        │   │   ├── base.py      # Optimizer interface
        │   │   ├── conviction.py  # ALWAYS-WORKS fallback (ships first)
        │   │   └── mvo.py        # SLSQP, depends on risk.covariance() (ships after L5)
        │   ├── corp_actions.py
        │   └── rebalance.py     # 30% turnover budget, --whatif mode
        │
        ├── risk/                # L5 — Risk Management
        │   ├── __init__.py      # public: covariance(date), pre_trade_veto(orders), check_breakers(state)
        │   ├── factor_model.py  # Barra-style cross-sectional, 120d
        │   ├── covariance.py    # output consumed by portfolio.optimizers.mvo
        │   ├── veto.py          # 8 absolute-veto checks; closing-trade exemption
        │   └── breakers.py      # daily/weekly/DD/concentration circuit breakers
        │
        ├── execution/           # L6 — Execution
        │   ├── __init__.py      # public: execute_rebalance(rebalance, dry_run)
        │   ├── broker/
        │   │   ├── base.py      # Broker interface
        │   │   ├── ibkr.py      # ib_async-backed concrete impl
        │   │   └── paper.py     # in-memory mock; lets E2E tests run without IBKR
        │   ├── executor.py      # veto + borrow check + ADV chunking + retry + slippage capture
        │   ├── borrow.py        # IBKR-native short availability
        │   ├── orders.py        # OrderManager state machine + SIGINT shutdown
        │   └── slippage.py      # rolling, p95, worst-5
        │
        ├── reporting/           # L7 — Reporting
        │   ├── __init__.py      # public: build_daily_report(date), build_weekly_commentary(date)
        │   ├── attribution.py   # beta / sector / factor / alpha
        │   ├── position_attr.py # FIFO round-trips, predictive-power Spearman
        │   ├── win_loss.py
        │   ├── sector_alpha.py
        │   ├── turnover.py
        │   ├── tearsheet.py     # institutional-format markdown
        │   ├── commentary.py    # Claude weekly commentary
        │   └── letter.py        # daily letter, dual-mode
        │
        ├── dashboard/           # Streamlit at localhost:8502
        │   ├── __init__.py
        │   ├── app.py           # entry; the launchd command does NOT run this — operator launches it
        │   ├── queries.py       # READ-ONLY repository over the same SQLite
        │   ├── jarvis.py        # Anthropic chat handler, 30s timeout
        │   ├── theme.py         # dark theme tokens
        │   └── pages/           # six Roman-numeral pages
        │       ├── i_portfolio.py
        │       ├── ii_research.py
        │       ├── iii_risk.py
        │       ├── iv_performance.py
        │       ├── v_execution.py
        │       └── vi_letter.py
        │
        └── cli/                 # All entry points — single Typer app
            ├── __init__.py
            ├── app.py           # `python -m ls_equity_fund.cli ...`
            ├── data_cmd.py      # run-data
            ├── scoring_cmd.py   # run-scoring (--no-filings, --no-13f)
            ├── analysis_cmd.py  # run-analysis
            ├── portfolio_cmd.py # run-portfolio (--whatif)
            ├── execution_cmd.py # run-execution (--dry-run, --execute)
            ├── reporting_cmd.py # run-reporting
            └── orchestrator.py  # daily-refresh: meta-command that runs the chain
```

### Why this layout

- **Single src/ tree** — One installable package (`pip install -e .`) gives you proper imports, type-checking, and tests. The 7-layer structure is real internally but does not justify 7 separate packages for a solo operator.
- **Public façade per layer** — Other layers import from `ls_equity_fund.factors` (the `__init__.py`), never from `ls_equity_fund.factors._composer`. Refactor freedom inside the layer is preserved.
- **Provider seam under `data/providers/`** — The interface lives in `base.py`; concrete implementations are siblings. Swapping yfinance for Polygon means writing one file and flipping `config.yaml`.
- **Optimizer seam under `portfolio/optimizers/`** — Same pattern: interface in `base.py`, conviction and MVO are siblings, `config.yaml` chooses. Lets MVO ship as a strategy plug-in, not a rewrite.
- **Broker seam under `execution/broker/`** — Same pattern. `PaperBroker` lets the entire chain run end-to-end before IBKR is wired.
- **Single `cli/` tree** — One Typer app with sub-commands is cleaner than seven `run_*.py` scripts. Shared flags, shared config loading, shared logging. Standalone shell aliases (`run-scoring`) can be added via `[project.scripts]` entry points if desired.

---

## 4. Database Schema

**One SQLite file, WAL mode, single writer at a time.** Per-layer files would force ATTACH gymnastics for cross-layer joins (factor scores by sector, attribution by position, veto-by-order) and gain nothing. Solo operator on one machine — SQLite WAL is sufficient.

### Connection setup (`db.py`)

```python
PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",       # safe + fast under WAL
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",        # 5s wait before raising "database is locked"
    "PRAGMA cache_size=-65536",        # 64MB page cache
    "PRAGMA temp_store=MEMORY",
]
```

### Tables

| Table | Key cols (PK underlined) | Indexes | Owner layer | Purpose |
|---|---|---|---|---|
| `universe` | __ticker__, sector, industry, included, last_updated | `idx_universe_sector` | L1 | Active universe + GICS sector |
| `daily_prices` | __(ticker, date)__, open, high, low, close, adj_close, volume | `idx_prices_date` | L1 | OHLCV |
| `fundamentals` | __(ticker, period_end, period_type)__, *income*, *bs*, *cf* fields, derived 24 ratios | `idx_fund_ticker` | L1 | Quarterly + annual |
| `filings` | __(ticker, accession_no)__, form_type, filed_date, period, raw_text_path | `idx_filings_ticker_form` | L1 | 10-K, 10-Q, 8-K bodies cached on disk; row references path |
| `insider_transactions` | __(ticker, accession_no, line_no)__, insider_role, code (P/S/A/M/F), shares, price, value, filed_date | `idx_insider_ticker_date` | L1 | Form 4, decoded |
| `institutional_holdings` | __(cik, ticker, period_end)__, shares, value, change_shares | `idx_inst_ticker_period` | L1 | 13F, tracked funds |
| `short_interest` | __(ticker, snapshot_date)__, shares_short, short_ratio, short_pct_float | `idx_short_ticker_date` | L1 | Daily snapshot |
| `analyst_estimates` | __(ticker, snapshot_date)__, eps_fy1, eps_fy2, rev_fy1, n_analysts, target_price | `idx_est_ticker_date` | L1 | Snapshot history → 30/60/90d revisions |
| `earnings_calendar` | __(ticker, expected_date)__, time_of_day, fiscal_period, refreshed_at | `idx_earn_date` | L1 | Next-30-day window |
| `macro_calendar` | __(event_id)__, event_type (FOMC/CPI/...), event_date, source, fetched_at | `idx_macro_date` | L1 | FRB live feed + cached fallback |
| `factor_scores` | __(ticker, asof_date)__, momentum, value, quality, growth, revisions, short_int, insider, inst_flow, composite, sector_rank_pct | `idx_fs_date_composite` | L2 | Daily ranks |
| `factor_subscores` | __(ticker, asof_date, factor, sub_factor)__, raw_value, sector_pct | `idx_fsub_ticker_date` | L2 | All 27 sub-factors |
| `analysis_results` | __(analyzer, ticker, artifact_id)__, asof_date, model, output_json, cost_usd, tokens_in, tokens_out, ttl_expires_at | `idx_ar_ticker_analyzer` | L3 | Cache, 30d TTL |
| `analysis_costs` | __(run_id, analyzer, ticker)__, cost_usd, cumulative_run_cost | `idx_ac_run` | L3 | Cost tracker, ceiling enforcement |
| `portfolio_positions` | __(asof_date, ticker)__, side, shares, avg_cost, market_value, weight, beta_60d | `idx_pp_date` | L4 | Snapshot per day |
| `portfolio_history` | __(asof_date)__, nav, gross, net, long_beta, short_beta, vix, factor_exposure_json | `idx_ph_date` | L4 | Series for tear sheet |
| `position_approvals` | __(asof_date, ticker)__, action, approver, status, reason | `idx_pa_date` | L4 | --whatif decisions |
| `target_portfolio` | __(asof_date, ticker)__, side, target_weight, expected_return, optimizer_used | `idx_tp_date` | L4 | Output of optimizer |
| `rebalance_orders` | __(asof_date, order_id)__, ticker, side, qty, target_price, reason, post_veto_status | `idx_ro_date` | L4 | Pre-execution intent |
| `risk_covariance` | __(asof_date, factor_a, factor_b)__, covariance | — | L5 | Barra factor model output |
| `risk_factor_loadings` | __(ticker, asof_date)__, factor, loading | `idx_rfl_ticker_date` | L5 | Per-stock loadings |
| `veto_events` | __(event_id)__, asof_date, ticker, order_id, check_name, decision, reason | `idx_ve_date_ticker` | L5 | Audit log |
| `circuit_breaker_events` | __(event_id)__, asof_date, breaker_type, value, threshold, action_taken | `idx_cb_date` | L5 | Audit log |
| `orders` | __(order_id)__, asof_date, ticker, side, qty, order_type, limit_price, tif, status, broker_order_id | `idx_orders_date_status` | L6 | Lifecycle states |
| `fills` | __(fill_id)__, order_id, fill_time, qty, price, commission | `idx_fills_order` | L6 | Multi-fill capture |
| `slippage` | __(order_id)__, signal_price, exec_price, bps, dollar_impact | `idx_slip_date` | L6 | Rolling/p95/worst-5 |
| `borrow_status` | __(ticker, asof_date)__, ibkr_borrowable, fee_bps, available_shares | `idx_borrow_date` | L6 | IBKR-native, daily |
| `daily_attribution` | __(asof_date, bucket_type, bucket_id)__, pnl, contribution_bps | `idx_da_date` | L7 | Beta / sector / factor / alpha |
| `position_attribution` | __(round_trip_id)__, ticker, entry_date, exit_date, side, pnl, holding_days, predictive_spearman | `idx_paa_ticker` | L7 | FIFO round trips |
| `runs` | __(run_id)__, started_at, finished_at, command, args_json, status, error, log_path | `idx_runs_started` | meta | One row per CLI invocation; surfaces in dashboard footer |

### Indexing strategy

- Every table with a date dimension gets `(date)` and `(ticker, date)` indexes — daily-bar queries are the dominant workload.
- Composite primary keys instead of synthetic IDs where the natural key is unique (prices, fundamentals, scores). This eliminates duplicate-row bugs at the source.
- One factor-score row per (ticker, asof_date); per-sub-factor breakdown lives in `factor_subscores` for thin-table queries from the dashboard.
- WAL means readers (the dashboard, the next layer in the chain) never block the writer — but only one writer at a time. The CLI orchestrator is single-process, so this is naturally enforced.

### Migration strategy

- **Use Alembic.** Schema will evolve (new factors, new metadata fields, new audit columns). Hand-managing `init_db.py` becomes a liability by milestone 3.
- **`scripts/init_db.py`** wraps `alembic upgrade head` so a fresh clone is one command.
- **Migration discipline:** every schema change is a new revision; `alembic downgrade` works. Even though SQLite has limited ALTER, Alembic's `batch_alter_table` handles it.

---

## 5. Layer Contracts (the seams)

**Rule:** layers communicate via SQLite tables for bulk data and Pydantic models for control objects. Pandas DataFrames cross the seam *only* with a documented index convention.

### Bulk-data seam: Pandas DataFrame conventions

Standardize the DataFrame index across the codebase to eliminate "what does the index look like?" friction:

- **Time series, single ticker:** `DatetimeIndex` named `date`, columns named explicitly.
- **Cross-section at a date:** `Index` of `ticker`, columns are factor names.
- **Panel (ticker × date):** `MultiIndex(['ticker', 'date'])`, sorted, columns are values.
- **Always sorted, always typed.** No object-dtype dates. Use `pd.api.types.assert_index_equal` style checks at seam boundaries.

### Control-object seam: Pydantic models (`schemas.py`)

```python
# L1 → L2 (no model — L2 reads SQLite directly via factors.composer.fetch())
# L2 → L3:
class Candidate(BaseModel):
    ticker: str
    asof_date: date
    sector: str
    composite_score: float
    sector_rank_pct: float
    sub_factors: dict[str, float]      # 27 sub-factor values

# L3 → L4:
class AnalyzedCandidate(Candidate):
    claude_score: float | None         # None → falls back to 100% quant
    claude_breakdown: dict[str, Any]
    combined_score: float

# L4 → L5 (pre-trade veto input):
class ProposedOrder(BaseModel):
    order_id: UUID
    asof_date: date
    ticker: str
    side: Literal["BUY", "SELL", "BUY_TO_COVER", "SELL_SHORT"]
    qty: int
    is_closing: bool                   # exempts from absolute veto
    target_price: float
    rationale: str

# L5 → L4 (covariance for MVO):
class CovarianceMatrix(BaseModel):
    asof_date: date
    tickers: list[str]
    cov: np.ndarray                    # NxN, validated
    factor_loadings: pd.DataFrame      # ticker × factor

# L5 → L6 (post-veto decision):
class VetoDecision(BaseModel):
    order_id: UUID
    decision: Literal["APPROVED", "REJECTED"]
    failed_checks: list[str]
    reason: str | None

# L6 → L7:
class Fill(BaseModel):
    order_id: UUID
    fill_time: datetime
    qty: int
    price: float
    commission: float
    slippage_bps: float
```

### Why Pydantic for control, DataFrames for bulk

- Pydantic models give validation + serialization at API-style boundaries (orders, vetoes, fills) — exactly where bugs are catastrophic.
- DataFrames are the natural shape for vectorized factor math; coercing them through Pydantic per-row would tank performance.
- SQLite is the *system of record*. Models and DataFrames are transient projections.

---

## 6. Build Order — Resolving the L4↔L5 Cycle

**The cycle:** L4 MVO needs L5 covariance. L5 risk-attribution needs L4 holdings. Dependency is real.

**The resolution:** ship L4 *conviction-tilt* before L5 risk model. Conviction-tilt has zero risk-model dependency (it just needs scores + ADV + sector). MVO ships *after* L5 as a swap-in behind the `Optimizer` interface.

This is exactly what the spec mandates ("conviction-tilt is the non-convergence fallback") and exactly what L4's optimizer-plug-in seam was designed for. **No architectural debt.**

### Recommended phase sequence

| Phase | Layer focus | Ships | Rationale |
|---|---|---|---|
| **0 — Foundation** | `config.py`, `db.py`, `schemas.py`, Alembic migrations, CLI skeleton, `PaperBroker` | Bootable empty system | Infrastructure first; CLI scaffold lets every subsequent phase have an entry point |
| **1 — L1 Data + Universe** | `data/` with yfinance + EDGAR + macro calendar; `--no-filings`, `--no-13f` flags | Daily refresh writes prices, fundamentals, insider, 13F, short, estimates, earnings, macro to SQLite | Spine of everything; nothing else works without L1 |
| **2 — L2 Scoring** | `factors/` 8 factors × 27 sub-factors; sector-percentile rank | `run-scoring` writes `factor_scores` and `factor_subscores` | Pure function of L1 — easy to test, easy to validate |
| **3 — L7 Reporting (basic) + Dashboard skeleton** | `reporting/tearsheet.py` minimal version, `dashboard/` 6 pages reading factor_scores and L1 | Operator can see ranked candidates by sector daily | **Ship value early.** Even without execution, a daily ranked list with sector breakdown is independently valuable |
| **4 — L3 Claude Analysis** | `analysis/` with prompt caching, cost tracker, 4 analyzers, 30d cache | Top candidates have qualitative overlay | Combined score (60/40) feeds L4 |
| **5 — L4 Conviction-tilt + state + beta + costs** | `portfolio/` minus `optimizers/mvo.py` | `run-portfolio --whatif` writes `target_portfolio` and `rebalance_orders` | First end-to-end: data → score → analyze → portfolio. Conviction-tilt has zero L5 dependency |
| **6 — L5 Risk model + veto + breakers** | `risk/` complete | Veto runs on L4 output; covariance available to L4 | Now L5 outputs are written; nothing in L4 *requires* them yet |
| **7 — L4 MVO swap-in** | `portfolio/optimizers/mvo.py` reading `risk.covariance()`, conviction-tilt remains the fallback | Operator flips `optimizer: mvo` in `config.yaml`; conviction-tilt stays as non-convergence fallback per spec | The cycle is closed without rewriting anything: MVO is a strategy plug-in, conviction-tilt is the floor |
| **8 — L6 Execution (paper)** | `execution/broker/ibkr.py` via `ib_async`, executor, slippage, borrow, order manager | `run-execution --dry-run` and paper IBKR | `PaperBroker` from Phase 0 has been the stand-in until now |
| **9 — L7 Reporting (full)** | Attribution, win/loss, sector alpha, turnover, daily letter (dual-mode), weekly commentary | Institutional-grade tear sheet daily | Now there are real fills and real positions to report on |
| **10 — Dashboard polish + JARVIS chat + launchd schedule** | All 6 pages, JARVIS chat, dark theme, 5-min auto-refresh, `launchd` plist | Daily 17:15 weekday job; localhost:8502 | Operator daily interface complete |
| **11 — Live-readiness review** *(out of v1 per PROJECT.md)* | Veto audit, slippage validation, paper-perf review, gating ceremony | Live promotion gated | Spec mandates explicit milestone |

**Critical sequencing notes:**

- **Phase 3 ships value before execution.** A factor-ranked sector-neutral candidate list is itself a useful research product. This protects against late-pipeline blockers killing the project.
- **Phase 0's `PaperBroker`** is load-bearing — it lets the L4→L5→L6 chain be exercised end-to-end before any IBKR setup, paper or otherwise.
- **Phase 7 (MVO) is non-blocking.** If MVO is hard, conviction-tilt stays as the optimizer indefinitely — the system is still complete.

---

## 7. Concurrency Model

**Recommendation: minimal concurrency. YAGNI on async until measurement says otherwise.**

| Workload | Bound | Recommended |
|---|---|---|
| yfinance fan-out (~500–3000 tickers) | I/O + rate limit | `concurrent.futures.ThreadPoolExecutor(max_workers=8)` with a `ratelimit` decorator. Threads, not asyncio — yfinance is synchronous and threads are simpler |
| EDGAR filings | I/O + 10 req/s SEC limit | Single-threaded with explicit `time.sleep(0.11)` between requests, custom `User-Agent` header. SEC's limit is strict; any concurrency here is a footgun |
| Factor calc | CPU (vectorized pandas) | Single-threaded NumPy/Pandas — already vectorized. Don't introduce multiprocessing for factor math |
| Anthropic fan-out (4 analyzers × 40 tickers) | I/O + Anthropic rate limit | `asyncio` with `httpx` and a semaphore — Anthropic SDK supports async natively. This is the one place async pays off because it's the slowest stage |
| IBKR session | Single-threaded by protocol | One `ib_async.IB()` instance, all calls in one thread. `ib_async` already wraps this correctly |
| Dashboard reads | Many readers | SQLite WAL handles natively. No locking needed |

**Don't introduce a job queue, Celery, or multiprocessing.** Solo operator, single machine, daily cadence — `concurrent.futures` for L1, `asyncio` for L3, single-threaded everything else. Total runtime budget is 10 minutes; current bottleneck is yfinance + EDGAR latency, not CPU.

---

## 8. Streamlit / Backend Separation

**No service layer. Streamlit reads SQLite directly via `dashboard/queries.py`.**

- `queries.py` is a thin read-only repository — one function per dashboard panel, all returning DataFrames.
- The launchd job and the dashboard are separate processes that share the SQLite file. WAL mode means the dashboard never blocks the writer; the writer only blocks itself.
- **JARVIS chat:** Anthropic call lives in the Streamlit process behind a 30s timeout and `@st.cache_data(ttl=300)` for repeated questions. No background worker. If the chat is slow, surface that — don't fork an architecture for it.
- **5-min auto-refresh:** `st_autorefresh` during market hours only (configurable). Outside market hours, manual refresh only — no point hammering SQLite when no data is changing.

**Why no service layer:** a service layer adds a process, an API contract, latency, and deploy complexity for zero gain on a single machine. Streamlit's process model + SQLite WAL is the entire architecture.

---

## 9. launchd + CLI Orchestration

### The single entry point

```
launchd → /usr/bin/env python -m ls_equity_fund.cli daily-refresh \
    --no-filings --no-13f \
    --config /path/to/config.yaml \
    --log-file logs/daily-$(date +%Y%m%d).log
```

### What `daily-refresh` does (orchestrator.py)

```
1.  Open `runs` row, status='RUNNING'
2.  L1 run-data        (with --no-filings --no-13f)
3.  L2 run-scoring
4.  L3 run-analysis    (with cost ceiling)
5.  L4 run-portfolio   (--whatif=true — does not place orders)
6.  L5 veto/breakers run on the proposed rebalance
7.  L7 run-reporting   (tear sheet + daily letter)
8.  Close `runs` row, status='OK' or 'FAILED' + error
9.  exit 0 / exit non-zero
```

**Execution (L6) is NOT in `daily-refresh`.** Execution is operator-initiated: `python -m ls_equity_fund.cli run-execution --execute` after reviewing the dashboard. This matches the spec's posture: paper-first, deliberate, no automatic firing.

### Observability

- Every command writes a `runs` row with `(run_id, started_at, finished_at, command, args_json, status, error, log_path)`.
- `dashboard/pages/i_portfolio.py` shows last 7 runs as a status strip — green/yellow/red, with click-through to the log file.
- Logs go to `logs/daily-YYYYMMDD.log` via `logging.handlers.RotatingFileHandler`, 7-day retention.
- Optional: macOS notification on failure via `osascript -e 'display notification ...'` from a launchd `StandardErrorPath` watcher script. **Don't** wire this in v1 — the dashboard status strip is enough.

### Shared CLI conventions

Every sub-command supports:

- `--config PATH` (default: `./config.yaml`)
- `--log-level {DEBUG,INFO,WARNING,ERROR}` (default INFO)
- `--dry-run` (no writes to SQLite — Phase-0 plumbing for L6, also useful in L4 for cost estimation)
- `--asof DATE` (default: today; lets historical re-runs replay)

Per-layer flags:

- `run-data`: `--no-filings`, `--no-13f`, `--tickers TICKER,TICKER`
- `run-scoring`: `--factors momentum,value,...`
- `run-analysis`: `--max-candidates N`, `--no-cache`
- `run-portfolio`: `--whatif` (default), `--commit`, `--optimizer {mvo,conviction}`
- `run-execution`: `--dry-run` (default), `--execute`, `--paper`/`--live`

Use **Typer**, not argparse — auto-help, sub-command tree, type-validation, and `[project.scripts]` entry points.

---

## 10. Config + Secrets

### `config.yaml` (top-level)

Single file. Per-layer YAML files would explode the surface area — solo operator, all knobs in one place, search-friendly.

```yaml
# config.yaml
runtime:
  timezone: America/New_York
  log_level: INFO

universe:
  mode: liquid_us           # sp500 | liquid_us | scanner_seed
  benchmark: SPY
  sector_etfs: {Tech: XLK, Financials: XLF, ...}

data:
  provider: yfinance        # → swap to polygon | tiingo | iex
  edgar_user_agent: ${SEC_USER_AGENT}
  fred_endpoint: https://www.federalreserve.gov/...
  lookback_years: 3

factors:
  weights:
    momentum: 0.18
    value: 0.15
    quality: 0.18
    growth: 0.12
    revisions: 0.12
    short: 0.07
    insider: 0.10
    institutional: 0.08
  sector_neutral: true
  rank_method: percentile

analysis:
  model: claude-sonnet-4-5
  cost_ceiling_usd: 25
  cache_ttl_days: 30
  prompt_caching: true
  max_candidates: 40

portfolio:
  optimizer: conviction      # → flip to mvo after Phase 7
  long_count: 20
  short_count: 20
  turnover_budget: 0.30
  max_weight: 0.05
  beta_target: 0.0

risk:
  factor_model_window_days: 120
  veto_checks: [adv_cap, beta_band, sector_cap, ...]
  breakers:
    daily_loss_pct: -1.5
    daily_loss_pct_hard: -2.5
    weekly_loss_pct: -4.0
    drawdown_pct: -8.0
    single_position_pct: 3.0

execution:
  broker: ibkr               # → swap to paper for tests
  mode: paper                # paper | live
  ibkr:
    host: 127.0.0.1
    port: 7497               # paper TWS
    client_id: 17
  commission_per_share: 0.005
  adv_cap_pct: 0.05

reporting:
  weekly_commentary_weekday: 5   # Friday
  letter_mode: internal          # internal | lp_formal
  jurisdiction: US

dashboard:
  port: 8502
  auto_refresh_seconds: 300
  theme: dark
```

### `.env` (gitignored)

```
ANTHROPIC_API_KEY=sk-ant-...
IBKR_USERNAME=...
IBKR_ACCOUNT_ID=DU...
SEC_USER_AGENT=Meridian Capital Partners ops@example.com
```

### `config.py` — Pydantic Settings

- Use `pydantic-settings` to validate `config.yaml` and `.env` at startup.
- One `Settings` model with nested sub-models per top-level key.
- Validation errors at startup are loud and obvious — avoid late-discovery failures.
- `${VAR}` interpolation in YAML for env vars (or pre-process with `os.path.expandvars`).
- `config.example.yaml` checked in; `config.yaml` gitignored (operator may put paths or numbers they don't want public).

---

## 11. Provider, Optimizer, and Broker Seams

The three swap-in points the spec demands. All follow the same pattern: abstract base in `base.py`, concrete siblings, `config.yaml` selects.

### Data Provider seam (`data/providers/base.py`)

```python
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd

class MarketDataProvider(ABC):
    @abstractmethod
    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        """MultiIndex(ticker, date), columns: open/high/low/close/adj_close/volume."""

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> pd.DataFrame:
        """Index: period_end. Columns: standardized fundamental fields."""

    @abstractmethod
    def get_short_interest(self, ticker: str, asof: date) -> dict | None: ...

    @abstractmethod
    def get_estimates(self, ticker: str, asof: date) -> dict | None: ...
```

`YFinanceProvider` ships first. `PolygonProvider` is a stub that raises `NotImplementedError` with a message pointing to the seam — keeps the interface honest. EDGAR is a separate `FilingsProvider` (different shape: documents, not bars).

### Optimizer seam (`portfolio/optimizers/base.py`)

```python
class Optimizer(ABC):
    @abstractmethod
    def optimize(
        self,
        candidates: pd.DataFrame,        # ticker × {score, sector, expected_return, ...}
        cov: CovarianceMatrix | None,    # MVO requires; conviction-tilt ignores
        constraints: PortfolioConstraints,
    ) -> pd.DataFrame:                   # ticker × target_weight
        ...
```

`ConvictionTiltOptimizer` ships in Phase 5; ignores `cov`. `MVOOptimizer` ships in Phase 7; raises if `cov is None`. Spec-mandated fallback is implemented in `portfolio/__init__.py`'s public `build_target_portfolio()` — it tries MVO first, catches non-convergence, falls back to conviction-tilt.

### Broker seam (`execution/broker/base.py`)

```python
class Broker(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_borrowable(self, ticker: str) -> BorrowStatus: ...

    @abstractmethod
    def place_order(self, order: ProposedOrder) -> str:    # broker_order_id
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def stream_fills(self, callback: Callable[[Fill], None]) -> None: ...
```

`PaperBroker` (in-memory dict, deterministic fills at last price) ships in Phase 0. `IBKRBroker` (via `ib_async` — the live successor to the now-unmaintained `ib_insync`) ships in Phase 8.

---

## 12. Anti-Patterns

### Anti-Pattern 1: Cross-layer imports of internals

**What:** `from ls_equity_fund.factors._composer import _build_panel` from inside `portfolio/`.
**Why wrong:** Couples to private structure; breaks layer independence; refactoring the internals breaks the dependent layer.
**Instead:** Public façade only — `from ls_equity_fund.factors import get_latest_scores`.

### Anti-Pattern 2: SQLite "many small writes" inside a hot loop

**What:** `for row in df.iterrows(): conn.execute("INSERT ...")`.
**Why wrong:** Each statement is a transaction by default. Tens of thousands of fsyncs. Daily refresh balloons from 10 min to 2 hours.
**Instead:** Use `pandas.DataFrame.to_sql(method='multi', chunksize=1000)` or wrap loops in `with conn: ...` (single transaction).

### Anti-Pattern 3: Leaking ORMs into the layer code

**What:** SQLAlchemy ORM models flowing into factor calc and dashboard queries.
**Why wrong:** ORMs are great at OLTP; they're a tax on bulk numerical workloads, and they hide the SQL the operator needs to inspect at 10pm. Factor work is bulk numerical.
**Instead:** Use SQLAlchemy Core (or raw SQL) for queries. Pandas does the rest. Use Alembic for migrations only.

### Anti-Pattern 4: Hardcoding the optimizer / provider / broker

**What:** `from .optimizers.conviction import optimize` inside `rebalance.py`.
**Why wrong:** Defeats the swap-in seam. Every change to the optimizer requires editing the consumer.
**Instead:** `Optimizer = registry.get(config.portfolio.optimizer); Optimizer().optimize(...)`. The seam exists *because* spec mandates swap-in.

### Anti-Pattern 5: Streamlit doing computation

**What:** Computing factor scores or running Anthropic analysis inside a Streamlit page render.
**Why wrong:** Re-runs every page load; blocks UI; bypasses the cache; double-counts cost ceiling.
**Instead:** Streamlit reads from SQLite. Compute happens in the CLI orchestrator. JARVIS chat is the only Anthropic call from Streamlit and it's per-question with caching.

### Anti-Pattern 6: Skipping the `runs` table

**What:** Letting cron/launchd succeed silently with no DB row.
**Why wrong:** Operator has no view into "did today's job actually run?" except by tailing log files.
**Instead:** Every CLI invocation opens and closes a `runs` row. Dashboard's first piece of UI is the run-status strip.

### Anti-Pattern 7: Treating the analysis cache as best-effort

**What:** Re-calling Anthropic when the cache lookup misses for any reason.
**Why wrong:** $25/run ceiling is a hard constraint; cache misses + retries blow it.
**Instead:** Cache is a strict gate. Cache miss → record the call's cost atomically before issuing it, abort the run if cumulative > ceiling.

---

## 13. Scaling Considerations

| Scale | Adjustment |
|---|---|
| Today (one operator, ~500–3000 tickers, daily) | SQLite WAL on a single machine. No tuning needed beyond pragmas in `db.py`. |
| 2× universe / 2× factor count | Index audit; consider a second SQLite file for high-churn audit tables (`veto_events`, `orders`, `fills`) via ATTACH. Still no Postgres needed. |
| Add intraday cadence | This is out of scope per PROJECT.md. If it ever comes back, *that* is the trigger for Postgres + a real job queue + a service process. Not before. |
| Multiple operators / web-hosted | Out of scope per PROJECT.md. If revisited: Postgres + FastAPI in front of the layers + Auth — but that's a different product. |

The architecture is deliberately sized for one operator on one Mac. Don't pre-scale.

---

## 14. Open Architectural Questions

1. **MVO covariance shrinkage method** — Ledoit-Wolf vs OAS vs raw sample? Decision deferred to Phase 6 (L5 risk model). Affects MVO numerical stability.
2. **Form 4 / 13F XML caching** — Cache parsed JSON or re-parse from raw XML each run? Recommend cache parsed JSON in a sidecar table, keep raw XML on disk under `cache/edgar/`. Re-parse only on schema change.
3. **Anthropic streaming vs non-streaming** — Streaming gives partial results; non-streaming is simpler. Recommend non-streaming in v1 because we don't surface partial output anywhere; revisit if cost-tracker accuracy matters per-token.
4. **JARVIS chat context window** — Does JARVIS see the full SQLite or a curated context? Recommend a fixed system prompt + injected `today's portfolio_history + factor_scores top 50 + last 7 runs` context. No tool-use in v1.
5. **Live promotion gating** — Out of v1, but the seam (`mode: paper` vs `mode: live` in config) needs to refuse `mode: live` unless an explicit env var (`MERIDIAN_LIVE_OK=1`) is set. Defense in depth against accidental live trading.
6. **Backtest harness** — Spec doesn't mention one. Without it, factor weights and risk model parameters are tuned on production. Recommend logging a "what would the optimizer have decided?" panel daily for a self-built rolling backtest, and call out a future `backtest/` package as out of v1.

---

## Sources

- [ib_async — active fork of ib_insync (GitHub)](https://github.com/ib-api-reloaded/ib_async)
- [ib_async PyPI](https://pypi.org/project/ib_async/)
- [SQLite WAL official docs](https://www.sqlite.org/wal.html)
- [Going Fast with SQLite and Python — Charles Leifer](https://charlesleifer.com/blog/going-fast-with-sqlite-and-python/)
- [SEC EDGAR fair access policy](https://www.sec.gov/os/accessing-edgar-data) (10 req/s, User-Agent required)
- [Anthropic prompt caching docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Typer (CLI framework) docs](https://typer.tiangolo.com/)
- [Pydantic Settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Alembic migrations docs](https://alembic.sqlalchemy.org/)
- [Streamlit `st_autorefresh`](https://docs.streamlit.io/) component pattern

---
*Architecture research for: long/short US equity quant hedge fund system (single-operator, daily-cadence, paper-first IBKR)*
*Researched: 2026-05-04*
