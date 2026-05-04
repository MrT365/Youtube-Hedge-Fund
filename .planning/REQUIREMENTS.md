# Requirements: Meridian Capital Partners (`ls_equity_fund`)

**Defined:** 2026-05-04
**Core Value:** A solo operator can run a credible, sector-neutral, factor-driven L/S equity book end-to-end — score → analyze → optimize → vet → execute → report — every trading day, without manual stitching, with hard risk guardrails that cannot be bypassed.

## v1 Requirements

### DATA (Layer 1 — Data Infrastructure)

- [ ] **DATA-01**: Operator can build a US equities universe in three modes (`sp500` from Wikipedia, `liquid_us` filtered by exchange + min price + min ADV + min market cap, `scanner_seed` from IBKR scanner output or seed list) with ticker, company, exchange, primary listing, sector, industry/sub-industry persisted
- [ ] **DATA-02**: System maintains benchmark tickers (SPY, QQQ, IWM, DIA; sector ETFs XLK/XLF/XLV/XLE/XLI/XLC/XLY/XLP/XLB/XLRE/XLU; ^VIX, TLT, HYG) refreshed on schedule
- [ ] **DATA-03**: System ingests daily OHLCV via yfinance for universe + benchmarks with 3-year lookback and incremental updates (only fetch since last stored date) into SQLite `daily_prices`
- [ ] **DATA-04**: System ingests quarterly + annual income statement, balance sheet, cash flow via yfinance and computes 24 derived ratios (ROE, ROA, gross/op/net margin, rev growth YoY/QoQ, earnings growth YoY/QoQ, D/E, FCF yield, current ratio, AR/rev, CFO/NI, accruals, retained earnings, working capital, total liabilities, EBIT, R&D, shares out, dividends, buybacks, asset turnover)
- [ ] **DATA-05**: System fetches 10-K (Risk Factors), 10-Q (MD&A), recent 8-K, and recent Form 4 filings from SEC EDGAR with compliant User-Agent and rate limiting
- [ ] **DATA-06**: System parses Form 4 XML into `insider_transactions` (ticker, insider_name, title, transaction_type, transaction_code, shares, price, date, ownership_type) distinguishing P/S from A/M/F codes; flags CEO/CFO purchases and 30-day 3+ insider cluster buys
- [ ] **DATA-07**: System ingests 13F filings from a tracked-fund list (Citadel, Point72, Bridgewater, Tiger Global, Third Point, Berkshire, Appaloosa, Baupost, Pershing Square) and flags tickers with 3+ tracked funds opening simultaneously
- [ ] **DATA-08**: System captures daily snapshots of `shares_short`, `short_ratio`, `short_percent_of_float` into `short_interest`
- [ ] **DATA-09**: System captures daily snapshots of forward EPS estimate and price-target consensus into `analyst_estimates` for 30/60/90-day revision deltas
- [ ] **DATA-10**: System maintains an upcoming-earnings calendar (next 30 days) refreshed daily
- [ ] **DATA-11**: System maintains a live FOMC macro calendar parsed from the official Federal Reserve source, ET + local TZ, refreshed weekly, with cached fallback + warning on parse failure
- [ ] **DATA-12**: Daily refresh supports `--no-filings` and `--no-13f` skip flags + `--forms` selective pull
- [ ] **DATA-13**: System persists a point-in-time universe table with `first_seen_date` and `delisted_date` columns so historical queries can be reproduced free of survivorship bias *(gap G2 from research)*
- [ ] **DATA-14**: Data layer exposes a `MarketDataProvider` interface (yfinance is the default implementation) so a paid feed (Polygon / Tiingo / IEX / Alpha Vantage) can drop in by config without rewriting downstream code

### SCORE (Layer 2 — Scoring Engine)

- [ ] **SCORE-01**: Momentum factor implemented with 6 sub-factors (12-1 month return, 6-month return, 3-month return, acceleration, 52-week-high proximity, sector-relative strength)
- [ ] **SCORE-02**: Value factor implemented with 6 sub-factors (forward earnings yield, B/P, FCF yield, EV/EBITDA inverted, shareholder yield, sales/EV)
- [ ] **SCORE-03**: Quality factor implemented with 8 sub-factors (ROE stability, GM level, GM trend, D/E inverted, CFO/NI, accruals inverted, Piotroski F-Score, Altman Z-Score with safe/grey/distress zones)
- [ ] **SCORE-04**: Growth factor implemented with 5 sub-factors (revenue YoY, earnings YoY, revenue acceleration, R&D intensity, FCF YoY)
- [ ] **SCORE-05**: Estimate-revisions factor implemented with 3 sub-factors (30/60/90-day deltas) and returns a degenerate-neutral score until sufficient snapshot history exists
- [ ] **SCORE-06**: Short-interest factor implemented with 3 sub-factors (% float, days-to-cover, change vs prior); side-aware scoring (longs reward declining SI; shorts reward rising SI)
- [ ] **SCORE-07**: Insider-activity factor implemented with 3 sub-factors (90d net dollar flow, CEO/CFO open-market purchases weighted 3×, cluster-buy bonus); counts only Form 4 codes P and S; A/M/F ignored *(reinforced by pitfall CP3)*; sector-median fallback when no data
- [ ] **SCORE-08**: Institutional-flow factor implemented with 3 sub-factors (count of tracked funds holding, net change in aggregate holdings vs prior quarter, multi-fund-opening flag)
- [ ] **SCORE-09**: All sub-factor scores produced as 0–100 percentile rank within GICS sector; sub-factors equal-weighted within parent factor
- [ ] **SCORE-10**: All factor scores persisted at entry to `factor_scores` for predictive-power studies and audit

### ANAL (Layer 3 — Claude AI Qualitative Analysis)

- [ ] **ANAL-01**: Anthropic SDK client wraps every call with `cache_control: ephemeral` on system prompts; system passed as a content-block list (not a string) to bind the cache *(addresses pitfall CP2)*
- [ ] **ANAL-02**: Cost tracker reads `response.usage` and accumulates input, output, cache_creation, and cache_read tokens at correct billing multipliers (cache_creation billed at 1.25×)
- [ ] **ANAL-03**: Cost tracker enforces a per-run hard cost ceiling (default $25, configurable) and aborts the run when exceeded
- [ ] **ANAL-04**: Analysis cache (SQLite `analysis_results`, default 30-day TTL) keyed by (analyzer, ticker, artifact_id) — re-running the same artifact returns a free cache hit
- [ ] **ANAL-05**: Filing analyzer scores 8 quarters of fundamentals on earnings quality, revenue quality, balance-sheet health, and accruals; output JSON includes earnings_quality_score, balance_sheet_score, red/green flags, risk_level
- [ ] **ANAL-06**: Risk analyzer ingests 10-K Risk Factors (HTML stripped, 80K cap), separates material risks from boilerplate, flags new vs prior filing; output JSON includes new_risks, material_risks, boilerplate_percentage, risk_severity, one_line_summary
- [ ] **ANAL-07**: Insider analyzer interprets Form 4 (last 90d) into a STRONG_BUY → STRONG_SELL signal with confidence, key_transactions, reasoning, one_line_summary; returns None if no insider data
- [ ] **ANAL-08**: Sector analyzer ranks per-sector candidates and returns top_long_idea, top_short_idea, sector_outlook with reasoning
- [ ] **ANAL-09**: Combined-score module produces 60% quant composite + 40% Claude average across available analyzers, re-ranks within sector, and falls back to 100% quant with no penalty when Claude data is absent
- [ ] **ANAL-10**: Per-candidate markdown report generator writes to `output/reports_{timestamp}/{TICKER}.md` with scores, Claude summaries, upcoming catalysts, risk flags
- [ ] **ANAL-11**: Earnings-call analyzer ships as a stub returning None (transcript pipeline deferred to v2)
- [ ] **ANAL-12**: Analysis CLI supports `--estimate-cost`, `--ticker`, `--sector`, and full-run modes

### PORT (Layer 4 — Portfolio Construction)

- [ ] **PORT-01**: Conviction-tilt optimizer: equal-weight base within each book; top 5% × 1.5; top 10% × 1.25; ADV cap (no position > 5% of 20-day ADV); halve size if earnings within 5 days; beta-adjust to target; sector-neutral
- [ ] **PORT-02**: MVO optimizer (scipy.optimize SLSQP) with cost-net expected returns mapped from composite score (100 → +15%/yr, 0 → −15%/yr), full constraint set (gross long/short, per-position bounds, beta constraint, sector net, single-side sector); falls back to conviction-tilt on non-convergence
- [ ] **PORT-03**: Optimizer non-convergence writes an audit-log row (timestamp, reason, fallback used) so MVO health can be tracked over time *(gap G7 from research)*
- [ ] **PORT-04**: Transaction cost model decomposes commission + spread + impact in bps and feeds net-of-cost expected returns into MVO; commission is broker-configurable (IBKR pricing model, not hardcoded $0)
- [ ] **PORT-05**: Rebalance schedule advisory checks earnings within 2d, FOMC within 5d (from L1 macro feed), monthly opex within 3d (third Friday); returns advisory warnings only, does not block trading
- [ ] **PORT-06**: Portfolio state persisted in `portfolio_positions`, `portfolio_history`, `position_approvals` with ticker, shares, entry_price, entry_date, current_price, unrealized_pnl, sector, factor_scores_at_entry; corporate actions handled
- [ ] **PORT-07**: Beta calculator produces rolling 60-day stock beta vs SPY plus portfolio long-book / short-book / net beta
- [ ] **PORT-08**: Factor-exposure calculator returns weighted average of each factor across long and short books, flags when long-short spread > 1σ from historical
- [ ] **PORT-09**: Rebalance generator compares current vs target, applies 30% turnover budget, prioritizes largest score changes, estimates per-trade transaction costs, supports `--whatif` mode and `--optimize-method mvo|conviction`
- [ ] **PORT-10**: Defaults: num_longs=20, num_shorts=20, max_position=5%, max_sector=25%, gross=150%, net=[0%,+10%], max_beta=0.15, turnover_budget=30%, mvo_risk_aversion=1.0 — all in `config.yaml`

### RISK (Layer 5 — Risk Management)

- [ ] **RISK-01**: Barra-style cross-sectional factor risk model (120-day lookback) produces factor returns, annualized factor covariance matrix, specific variance per stock, portfolio factor variance, specific variance, total variance, and per-position MCTR
- [ ] **RISK-02**: Risk model emits a predicted covariance matrix consumable by Layer 4 MVO (with Ledoit-Wolf shrinkage to address sample-covariance instability — pitfall CP4)
- [ ] **RISK-03**: Pre-trade veto evaluates 8 absolute checks; any failure rejects the trade: (1) halt lock, (2) earnings blackout on **new entries** *(gap G1)*, (3) liquidity ≤ 5% ADV, (4) position ≤ 5% AUM, (5) sector ≤ 25%, (6) gross/net exposure within bounds, (7) |net beta| ≤ 0.20, (8) pairwise correlation ≤ 0.80 with existing positions
- [ ] **RISK-04**: "Closing trade" exemption is explicitly defined (sign-preserving + magnitude-reducing + qty ≤ existing); any trade that fails this definition cannot bypass the veto regardless of label *(addresses pitfall CP5)*
- [ ] **RISK-05**: Every veto rejection persists timestamp, ticker, reason, and trade context to an immutable audit log
- [ ] **RISK-06**: Circuit breakers fire automatically: daily loss > 1.5% → SIZE_DOWN 30%; daily loss > 2.5% → CLOSE_ALL_TODAY; weekly loss > 4% → SIZE_DOWN 30%; drawdown > 8% → KILL_SWITCH; single position > 3% NAV → force-close
- [ ] **RISK-07**: Every circuit-breaker event persists timestamp, breaker type, threshold, observed value, and portfolio state snapshot

### EXEC (Layer 6 — Execution)

- [ ] **EXEC-01**: Broker abstraction connects to IBKR via `ib_async` (or Client Portal API) with explicit paper/live config separation; paper is default
- [ ] **EXEC-02**: Live mode is gated by an explicit env-var (e.g. `MERIDIAN_LIVE_OK=1`) plus the paper→live promotion record (AUDIT-03); without both, the broker refuses to instantiate in live mode
- [ ] **EXEC-03**: Order executor runs the pre-trade veto, the borrow check, applies a configurable limit-price policy (signal/close/market reference), chunks orders > configurable ADV threshold, applies configurable TIF, polls/subscribes to status, cancels + retries on timeout, and records signal_price for slippage capture
- [ ] **EXEC-04**: Slippage tracker computes side-aware bps slippage = (fill − signal) / signal × 10000, plus 30-day rolling avg / median / p95 / total $ cost / worst-5 fills
- [ ] **EXEC-05**: Short-availability check uses IBKR-native short-securities/borrow workflow (no Alpaca flags); names not borrowable or failing locate are skipped + logged
- [ ] **EXEC-06**: System captures borrow rate / HTB cost per short and includes it in the transaction-cost model so short-side P&L is not silently optimistic *(gap G8 from research)*
- [ ] **EXEC-07**: Order manager tracks pending → submitted → partial → filled / cancelled / rejected; on SIGINT, cancels all pending orders, retains live positions, logs final state cleanly
- [ ] **EXEC-08**: Every order persists timestamp, ticker, side, shares, limit, fill, slippage_bps, status, broker_order_id
- [ ] **EXEC-09**: Execution CLI supports `--dry-run` (log what would happen) and `--execute` (place orders via IBKR)

### REPORT (Layer 7 — Reporting)

- [ ] **REPORT-01**: Daily P&L attribution decomposes daily_return into beta (net_beta × SPY return), sector (Brinson-style), factor (regression on factor return spreads), alpha (residual); persists to `output/daily_attribution.csv`
- [ ] **REPORT-02**: Position attribution computes mark-to-market, FIFO round-trips, best/worst per side, and Spearman correlation between entry-time score and realized return
- [ ] **REPORT-03**: Win/loss analysis sliced by side, holding period (1-5d / 5-20d / 20-60d / 60d+), sector, VIX regime at entry, factor quintile at entry; includes streaks
- [ ] **REPORT-04**: Sector-relative performance per sector over 90d (picks vs sector ETF) → stock-selection alpha, total alpha summed across sectors, winner/loser sector counts
- [ ] **REPORT-05**: Turnover analytics over 30d / 90d, annualized, vs configured budget; tax estimate logic configurable by jurisdiction/entity (no US-only hardcoding)
- [ ] **REPORT-06**: Tear sheet (markdown, institutional format) exposes a named metric set — Sharpe, Sortino, Calmar, max-DD, hit-rate, profit-factor, skew, kurtosis, tail — alongside metrics-vs-SPY, monthly returns grid, equity curve, drawdown chart, rolling 12-month Sharpe, factor + sector exposures, turnover *(gap G3 from research)*
- [ ] **REPORT-07**: Claude weekly commentary in JARVIS voice fires on a configurable weekday (default Friday)
- [ ] **REPORT-08**: Daily letter is dual-mode (`mode: lp` formal | `mode: internal` ops voice); LP mode renders letterhead, fund domicile (Delaware), inception, AUM, doc ID `MCP-IM-{YYYY}-{MMDD}`, CONFIDENTIAL stamp, "Dear Limited Partners," intro, JARVIS-voiced 3–4 paragraph body, signature, compliance footer; both modes cache by date and support a regenerate action

### DASH (Layer 7 — Streamlit Dashboard)

- [ ] **DASH-01**: Dashboard served at `http://localhost:8502` with dark theme tokens (BG #0b0e17, card gradient #131827→#1a2035, accent indigo #6366f1, long #10b981, short #f43f5e), Plus Jakarta Sans + JetBrains Mono fonts, Streamlit chrome hidden
- [ ] **DASH-02**: Roman-numeral pill nav with 6 pages: I PORTFOLIO · II RESEARCH · III RISK · IV PERFORMANCE · V EXECUTION · VI LETTER; active page rendered with indigo gradient
- [ ] **DASH-03**: Page I (Portfolio cover) — JARVIS 92px header + LONG/SHORT HEDGE FUND ANALYST small caps, robot image (or dark gradient fallback), Ask JARVIS chat with preserved short conversation memory, 10 metric cards (Universe, Long Candidates, Short Candidates, Positions, Crowding, Insider Events, CEO Buys, Cluster Buys, VIX, Earnings 7d), status strip with VIX-regime badge + data-source indicator; ~19KB JSON snapshot of system state cached and sent as Claude context
- [ ] **DASH-04**: Page II (Research) — KPIs + crowding warnings + rebalance advisory banner (earnings/FOMC/opex) + optimization toggle (MVO/conviction) + factor heatmap (top 30 + bottom 30 × 8 factors) + approval banner with Execute button + 10 long + 10 short candidate cards (Piotroski / Altman, Approve / Reject / Reset) + expandable Claude analysis per ticker; Execute → veto (8 checks) → IBKR; rejected trades show veto reason
- [ ] **DASH-05**: Page III (Risk) — circuit-breaker bars (daily/weekly/DD), tail-risk KPIs (VIX + credit spread), risk decomposition donut (factor vs specific variance), factor-risk-contributions table, MCTR table with disproportionate-risk flag, factor-exposure bars with 1.5σ warnings, stress test (6 scenarios), correlation heatmap + effective bets, 72hr alerts
- [ ] **DASH-06**: Page IV (Performance) — equity curve vs SPY (rebased 100), monthly-returns heatmap, drawdown chart, P&L attribution bars (Beta / Sector / Factor / Alpha), rolling 12-month Sharpe, sector-relative alpha + total alpha KPI + winner/loser counts, turnover panel (30d / annualized / budget / tax), transaction-cost panel (estimated vs actual vs model error), best/worst-5 contributors, win/loss panel, Claude weekly commentary card
- [ ] **DASH-07**: Page V (Execution) — KPI row (filled orders 30d, avg slippage bps, total slippage $, open orders count), open-orders table (polling IBKR), recent-trades log (last 200), worst-5 fills, short-availability panel per current short, daily notional turnover table
- [ ] **DASH-08**: Page VI (Letter) — formal LP letter (letterhead, CONFIDENTIAL stamp, Dear Limited Partners, body, signature, compliance footer) plus alternate internal-mode template; regenerate button; cache by date
- [ ] **DASH-09**: Auto-refresh every 5 minutes during market hours (9:30am–4:00pm ET); refresh is idempotent (does not re-trigger Anthropic calls; cache hits used) *(addresses pitfall on Streamlit cache + repeat-call cost)*

### INFRA (Cross-Cutting Infrastructure)

- [ ] **INFRA-01**: All parameters live in `config.yaml`; secrets in `.env` (gitignored); pydantic-settings validates load and surfaces errors at startup
- [ ] **INFRA-02**: Local SQLite database under `cache/` in WAL mode; one writer (CLI), many readers (dashboard); single migration story (Alembic with `batch_alter_table` for SQLite ALTER limits)
- [ ] **INFRA-03**: Project layout under `src/ls_equity_fund/{data,factors,analysis,portfolio,risk,execution,reporting,dashboard,cli}/` with three swap-in seams: `MarketDataProvider`, `Optimizer`, `Broker` — each abstract base + concrete sibling + config selector
- [ ] **INFRA-04**: macOS launchd plist at `~/Library/LaunchAgents/com.user.hedgefund.daily.plist`; weekdays 17:15 local; `WakeSystem=true` so the job runs after sleep; runs `run_scoring.py --no-filings --no-13f`; target ~10 min
- [ ] **INFRA-05**: Daily run records a `runs` row (start_ts, end_ts, status, error) and writes a heartbeat file so the dashboard can surface silent-failure
- [ ] **INFRA-06**: `.gitignore` covers `.env`, `cache/`, `output/`; `.planning/` stays tracked
- [ ] **INFRA-07**: Python 3.11+ via `uv`; `pyproject.toml` + `uv.lock`; pin `pandas>=2.2,<3.0` and `numpy>=2.0,<2.5` to dodge pandas 3.0 breakage; pin `ib_async==2.1.x`, `edgartools==5.30.x`, Anthropic SDK ≥ 0.97
- [ ] **INFRA-08**: Two CLI entrypoints per layer plus a `daily-refresh` meta-command; shared flags (`--dry-run`, `--whatif`, `--no-filings`, `--no-13f`, `--ticker`, `--sector`, `--optimize-method`)

### AUDIT (Cross-Cutting Discipline)

- [ ] **AUDIT-01**: Every order, veto rejection, circuit-breaker firing, and optimizer fallback persists with timestamp, reason, and snapshot context to immutable audit tables
- [ ] **AUDIT-02**: Logging via structlog; API keys redacted from log output; `.env` secrets never written to logs or commits
- [ ] **AUDIT-03**: Paper→live promotion ceremony documented in `PROMOTION.md` with named numeric criteria (e.g., ≥ N weeks paper, max DD < X%, slippage within Y bps of model, factor IC stable, audit log clean) — gated by code (`MERIDIAN_LIVE_OK=1` env-var) AND by the operator signing a checked-criteria record *(gap G4 from research)*

## v2 Requirements

Deferred. Tracked but not in current roadmap.

### BACKTEST (Validation Tooling)

- **BACKTEST-01**: Walk-forward backtest harness reusing the L2 score engine over L1's 3-year OHLCV (gap G5)
- **BACKTEST-02**: Factor IC / staleness monitor — rolling 6-month information coefficient per factor, auto-flag on degradation (gap G6)

### TRANSCRIPT (Earnings-Call Pipeline)

- **TRANSCRIPT-01**: Transcript provider integration (AssemblyAI, paid feed, or self-hosted) feeding the L3 earnings-call analyzer (currently a stub)

### LIVE (Real-Capital Promotion)

- **LIVE-01**: Live-readiness review milestone executing the AUDIT-03 promotion ceremony; ungated only after full criteria are met

## Out of Scope

| Feature | Reason |
|---------|--------|
| Real-money live trading at v1 | Paper-first; live promotion is an explicit later milestone with named gating criteria |
| Real LPs / external investors | Single operator on a laptop; LP letter is voice/aesthetic, not for actual LPs |
| Earnings-call transcripts in v1 | Avoids paid transcript dependency; analyzer ships as stub returning None |
| Paid market-data feeds in v1 | yfinance + EDGAR free tier sufficient; data layer abstracted for paid swap later |
| Options / futures / FX / crypto | US common stock only |
| High-frequency / intraday strategies | Daily bar cadence; rebalance is event-driven, not intraday |
| Alpaca short-availability flags | Replaced with IBKR-native borrow checks (Alpaca data is wrong broker-of-record) |
| Multi-user / web-hosted SaaS | Local-only Streamlit on `localhost:8502`; no auth, no remote |
| Hardcoded FOMC dates or sector lists | Replaced with live feeds and configurable lists |
| Pre-trade veto override flag | Veto is absolute; closing-trade exemption is the only carve-out and explicitly defined |
| ORM (SQLAlchemy / SQLModel) | Plain `sqlite3` stdlib; audit-critical raw SQL preferred at this scope |
| `ib_insync` | Unmaintained since author's death (2024); `ib_async` is the live successor |

## Traceability

Populated by roadmap creation 2026-05-04. Every v1 REQ-ID maps to exactly one phase in `.planning/ROADMAP.md`.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 1 | Pending |
| DATA-02 | Phase 1 | Pending |
| DATA-03 | Phase 1 | Pending |
| DATA-04 | Phase 1 | Pending |
| DATA-05 | Phase 1 | Pending |
| DATA-06 | Phase 1 | Pending |
| DATA-07 | Phase 1 | Pending |
| DATA-08 | Phase 1 | Pending |
| DATA-09 | Phase 1 | Pending |
| DATA-10 | Phase 1 | Pending |
| DATA-11 | Phase 1 | Pending |
| DATA-12 | Phase 1 | Pending |
| DATA-13 | Phase 1 | Pending |
| DATA-14 | Phase 1 | Pending |
| SCORE-01 | Phase 2 | Pending |
| SCORE-02 | Phase 2 | Pending |
| SCORE-03 | Phase 2 | Pending |
| SCORE-04 | Phase 2 | Pending |
| SCORE-05 | Phase 2 | Pending |
| SCORE-06 | Phase 2 | Pending |
| SCORE-07 | Phase 2 | Pending |
| SCORE-08 | Phase 2 | Pending |
| SCORE-09 | Phase 2 | Pending |
| SCORE-10 | Phase 2 | Pending |
| ANAL-01 | Phase 4 | Pending |
| ANAL-02 | Phase 4 | Pending |
| ANAL-03 | Phase 4 | Pending |
| ANAL-04 | Phase 4 | Pending |
| ANAL-05 | Phase 4 | Pending |
| ANAL-06 | Phase 4 | Pending |
| ANAL-07 | Phase 4 | Pending |
| ANAL-08 | Phase 4 | Pending |
| ANAL-09 | Phase 4 | Pending |
| ANAL-10 | Phase 4 | Pending |
| ANAL-11 | Phase 4 | Pending |
| ANAL-12 | Phase 4 | Pending |
| PORT-01 | Phase 5 | Pending |
| PORT-02 | Phase 7 | Pending |
| PORT-03 | Phase 7 | Pending |
| PORT-04 | Phase 5 | Pending |
| PORT-05 | Phase 5 | Pending |
| PORT-06 | Phase 5 | Pending |
| PORT-07 | Phase 5 | Pending |
| PORT-08 | Phase 5 | Pending |
| PORT-09 | Phase 5 | Pending |
| PORT-10 | Phase 5 | Pending |
| RISK-01 | Phase 6 | Pending |
| RISK-02 | Phase 6 | Pending |
| RISK-03 | Phase 6 | Pending |
| RISK-04 | Phase 6 | Pending |
| RISK-05 | Phase 6 | Pending |
| RISK-06 | Phase 6 | Pending |
| RISK-07 | Phase 6 | Pending |
| EXEC-01 | Phase 8 | Pending |
| EXEC-02 | Phase 8 | Pending |
| EXEC-03 | Phase 8 | Pending |
| EXEC-04 | Phase 8 | Pending |
| EXEC-05 | Phase 8 | Pending |
| EXEC-06 | Phase 8 | Pending |
| EXEC-07 | Phase 8 | Pending |
| EXEC-08 | Phase 8 | Pending |
| EXEC-09 | Phase 8 | Pending |
| REPORT-01 | Phase 9 | Pending |
| REPORT-02 | Phase 9 | Pending |
| REPORT-03 | Phase 9 | Pending |
| REPORT-04 | Phase 9 | Pending |
| REPORT-05 | Phase 9 | Pending |
| REPORT-06 | Phase 9 | Pending |
| REPORT-07 | Phase 9 | Pending |
| REPORT-08 | Phase 9 | Pending |
| DASH-01 | Phase 3 | Pending |
| DASH-02 | Phase 3 | Pending |
| DASH-03 | Phase 3 | Pending |
| DASH-04 | Phase 3 | Pending |
| DASH-05 | Phase 10 | Pending |
| DASH-06 | Phase 10 | Pending |
| DASH-07 | Phase 10 | Pending |
| DASH-08 | Phase 10 | Pending |
| DASH-09 | Phase 10 | Pending |
| INFRA-01 | Phase 0 | Pending |
| INFRA-02 | Phase 0 | Pending |
| INFRA-03 | Phase 0 | Pending |
| INFRA-04 | Phase 10 | Pending |
| INFRA-05 | Phase 10 | Pending |
| INFRA-06 | Phase 0 | Pending |
| INFRA-07 | Phase 0 | Pending |
| INFRA-08 | Phase 0 | Pending |
| AUDIT-01 | Phase 6 | Pending |
| AUDIT-02 | Phase 0 | Pending |
| AUDIT-03 | Phase 10 | Pending |

**Coverage:**
- v1 requirements: 90 total
- Mapped to phases: 90 (100%)
- Unmapped: 0

**Phase requirement counts:**
- Phase 0 (Foundation): 7 — INFRA-01, INFRA-02, INFRA-03, INFRA-06, INFRA-07, INFRA-08, AUDIT-02
- Phase 1 (Data Infrastructure): 14 — DATA-01 through DATA-14
- Phase 2 (Scoring Engine): 10 — SCORE-01 through SCORE-10
- Phase 3 (Reporting + Dashboard Skeleton): 4 — DASH-01, DASH-02, DASH-03, DASH-04
- Phase 4 (Claude AI Analysis): 12 — ANAL-01 through ANAL-12
- Phase 5 (Portfolio — Conviction-tilt): 8 — PORT-01, PORT-04, PORT-05, PORT-06, PORT-07, PORT-08, PORT-09, PORT-10
- Phase 6 (Risk Management): 8 — RISK-01 through RISK-07, AUDIT-01
- Phase 7 (Portfolio — MVO swap-in): 2 — PORT-02, PORT-03
- Phase 8 (IBKR Execution — Paper): 9 — EXEC-01 through EXEC-09
- Phase 9 (Reporting — Full): 8 — REPORT-01 through REPORT-08
- Phase 10 (Dashboard Polish + JARVIS + launchd + Promotion): 8 — DASH-05, DASH-06, DASH-07, DASH-08, DASH-09, INFRA-04, INFRA-05, AUDIT-03

Total: 7+14+10+4+12+8+8+2+9+8+8 = **90** ✓

---
*Requirements defined: 2026-05-04*
*Last updated: 2026-05-04 — traceability populated by roadmap creation*
