# Meridian Capital Partners — `ls_equity_fund`

## What This Is

A single-operator long/short US equity hedge fund system that ingests market, fundamental, SEC, institutional, and short-interest data; ranks ~500–3000 names with an 8-factor sector-neutral scoring engine; runs Claude qualitative analysis on top candidates; constructs a market-neutral 20-long / 20-short book via MVO or conviction-tilt; enforces an absolute-veto risk layer; routes orders through Interactive Brokers (paper first, live-ready); and reports through an institutional-grade Streamlit dashboard with a JARVIS-voiced daily letter. Built for one operator on macOS — local SQLite, localhost dashboard, launchd-scheduled daily refresh.

## Core Value

**A solo operator can run a credible, sector-neutral, factor-driven L/S equity book end-to-end — score → analyze → optimize → vet → execute → report — every trading day, without manual stitching, with hard risk guardrails that cannot be bypassed.**

If everything else fails, the daily run must still: refresh data, produce a ranked candidate list, surface a portfolio rebalance with risk-vetoed trades, and write a tear sheet. Execution is the *output*; ranking + risk + reporting is the *spine*.

## Requirements

### Validated

(None yet — ship to validate)

### Active

**Layer 1 — Data Infrastructure**
- [ ] Build configurable US equities universe (sp500 / liquid_us / scanner_seed modes) with benchmark tickers and sector ETFs
- [ ] Daily OHLCV ingestion via yfinance with 3y lookback and incremental updates
- [ ] Fundamentals ingestion (income statement, balance sheet, cash flow) with 24 derived ratios
- [ ] SEC EDGAR integration (10-K, 10-Q, 8-K, Form 4) with proper User-Agent + rate limiting
- [ ] Form 4 insider-transaction parser distinguishing P / S / A / M / F codes; flag CEO/CFO buys + 30-day cluster buys
- [ ] 13F institutional-holdings ingestion for tracked funds with multi-fund-opening flag
- [ ] Daily short-interest snapshots (shares_short, short_ratio, short_percent_of_float)
- [ ] Daily analyst-estimate snapshots for 30/60/90-day revisions factor
- [ ] Earnings calendar (next 30 days) with daily refresh
- [ ] Live FOMC macro calendar from Federal Reserve official source with cached fallback
- [ ] `--no-filings` and `--no-13f` flags for fast daily runs

**Layer 2 — Scoring Engine**
- [ ] 8 factors × 27 sub-factors implemented with sector-percentile rank (0–100) within GICS sector
- [ ] Momentum (12-1, 6m, 3m, acceleration, 52w-high, sector-relative strength)
- [ ] Value (forward earnings yield, B/P, FCF yield, EV/EBITDA, shareholder yield, sales/EV)
- [ ] Quality (ROE stability, GM level + trend, D/E, CFO/NI, accruals, Piotroski F, Altman Z)
- [ ] Growth (revenue YoY, earnings YoY, revenue acceleration, R&D intensity, FCF YoY)
- [ ] Estimate revisions (30/60/90-day deltas; degenerate-neutral until snapshot history accrues)
- [ ] Short interest (% float, days to cover, change) with side-aware scoring
- [ ] Insider activity (90d net dollar flow, 3× CEO/CFO weight, cluster bonus; sector median fallback)
- [ ] Institutional flow (tracked-fund count, net change, multi-fund opening flag)

**Layer 3 — Claude AI Analysis**
- [ ] Anthropic SDK client with prompt caching on system prompts and JSON-extraction across 3 wrap formats
- [ ] Cost tracker with hard ceiling per run (default $25); aborts when exceeded
- [ ] Analysis cache (SQLite, 30-day TTL) keyed by (analyzer, ticker, artifact_id) — re-runs are free hits
- [ ] Filing analyzer (forensic accounting on 8 quarters)
- [ ] Risk analyzer (10-K Risk Factors; new-vs-prior diff, boilerplate %, severity)
- [ ] Insider analyzer (Form 4 interpretation; STRONG_BUY → STRONG_SELL signal)
- [ ] Sector analysis (per-sector ranking, top long/short, outlook)
- [ ] Combined score: 60% quant composite + 40% Claude average; falls back to 100% quant with no penalty
- [ ] Per-candidate markdown report generator
- [ ] Earnings-call analyzer **stub** (returns None until a transcript provider is wired later)

**Layer 4 — Portfolio Construction**
- [ ] MVO optimizer (SLSQP) with cost-net expected returns, full constraint set, conviction-tilt fallback on non-convergence
- [ ] Conviction-tilt optimizer (top 5% × 1.5, top 10% × 1.25, ADV cap, earnings halving, beta-adjust, sector-neutral)
- [ ] Transaction-cost model (commission + spread + impact in bps) with broker-configurable commission
- [ ] Rebalance schedule advisory (earnings, FOMC from L1 macro feed, monthly opex)
- [ ] Portfolio state (positions, history, approvals) with corporate-action handling
- [ ] Rolling 60d beta per stock + portfolio-level long/short/net beta
- [ ] Factor-exposure calculator with 1σ-spread flag
- [ ] Rebalance generator with 30% turnover budget and `--whatif` mode

**Layer 5 — Risk Management**
- [ ] Barra-style cross-sectional factor risk model (120d) — feeds covariance into L4 MVO
- [ ] Pre-trade veto with 8 absolute-veto checks (closing trades exempt); all rejections logged
- [ ] Circuit breakers: -1.5%/d, -2.5%/d, -4%/wk, -8% DD, single position > 3% NAV

**Layer 6 — Execution**
- [ ] IBKR broker abstraction (Client Portal API or TWS/IB Gateway) with paper/live separation
- [ ] Order executor with veto + borrow check, configurable limit policy, ADV chunking, TIF, retry, signal-price slippage capture
- [ ] Slippage tracker (rolling, p95, worst-5)
- [ ] IBKR-native short availability / borrowability check (no Alpaca flags)
- [ ] Order manager with full lifecycle states + clean SIGINT shutdown
- [ ] `--dry-run` and `--execute` entrypoints

**Layer 7 — Reporting & Dashboard**
- [ ] Daily P&L attribution (beta / sector / factor / alpha)
- [ ] Position attribution (FIFO round-trips, predictive-power Spearman)
- [ ] Win/loss analysis sliced by side / holding period / sector / VIX regime / factor quintile
- [ ] Sector-relative alpha (picks vs sector ETF over 90d)
- [ ] Turnover analytics (configurable jurisdiction tax model)
- [ ] Institutional-format markdown tear sheet
- [ ] Claude weekly commentary (configurable weekday)
- [ ] Daily letter — dual-mode (LP-formal vs internal)
- [ ] Streamlit dashboard at `localhost:8502` with 6 Roman-numeral pages (Portfolio / Research / Risk / Performance / Execution / Letter), JARVIS chat, dark theme tokens, 5-min auto-refresh during market hours

**Cross-cutting**
- [ ] All parameters in `config.yaml`; secrets in `.env` (gitignored)
- [ ] Local SQLite cache; sub-folder layout per spec (data / factors / analysis / portfolio / risk / execution / reporting / dashboard / cache / output)
- [ ] macOS launchd daily refresh job at 17:15 weekdays running `run_scoring.py --no-filings --no-13f`, target ~10min
- [ ] Data layer abstracted behind interfaces so a paid feed (Polygon/Tiingo/IEX) can drop in later without rewrites

### Out of Scope

- **Real-money live trading at v1** — paper-first; live promotion after validated paper performance with explicit gating ceremony
- **Real LPs / external investors** — single operator on laptop; LP letter is dual-mode (formal *or* internal voice) but no compliance / KYC / 506(b)(c) infrastructure
- **Earnings-call transcripts in v1** — earnings analyzer ships as a stub returning None; deferred until a transcript provider is wired
- **Paid market-data feeds in v1** — yfinance + EDGAR free-tier only; data layer abstracted so paid feeds can swap in later
- **Options / futures / FX / crypto** — long/short US equities (common stock) only
- **High-frequency / intraday strategies** — daily bar cadence; rebalance is event-driven, not intraday
- **Alpaca short-availability flags** — explicitly replaced with IBKR-native borrow checks
- **Multi-user, web-hosted, SaaS** — local-only Streamlit on `localhost:8502`; no auth, no remote access
- **Jurisdictions other than US (initial run)** — tax/turnover assumptions configurable per spec, but US is the only out-of-the-box config
- **Hardcoded FOMC dates or sector lists** — replaced with live feeds and configurable lists

## Context

**Operator profile:** single technical operator on macOS, comfortable with Python / SQLite / Streamlit / Anthropic SDK / IBKR API. Project derives from a YouTube tutorial-style spec the operator authored — folder is `Youtube Hedge Fund App` but the canonical project name is `Meridian Capital Partners` / `ls_equity_fund`.

**Trading posture:** paper-first via IBKR paper account. Execution code is built and exercised in dry-run + paper from day one; live promotion is a deliberate later milestone, not a v1 deliverable. Risk gates and audit logging are sized for the live-ready bar even while paper-only.

**Why all 7 layers in v1:** the system's value depends on the full data → score → analyze → construct → vet → execute → report loop running daily. Stopping at L4 leaves no feedback signal; stopping at L6 leaves the operator blind. The dashboard is the operator's daily interface — it cannot be a "later" item.

**Why sector-neutral percentile ranking:** the spec mandates 0–100 percentile within GICS sector for every factor. This is non-negotiable architecture; sector membership is the join key for nearly every cross-sectional calculation downstream (scoring, risk model, attribution, sector-relative alpha).

**Why prompt caching is required, not optional:** Layer 3 fans 4 analyzers across 40 tickers per run. Without `cache_control: ephemeral` on system prompts, the $25 ceiling will be hit on day one. Caching is a load-bearing architectural decision, not an optimization.

**Why two optimizers:** MVO is the institutional answer but requires a working covariance matrix from L5 and well-conditioned inputs. Conviction-tilt is the always-works fallback. Spec mandates fallback-on-non-convergence — both must ship together.

**Data freshness vs reliability:** yfinance is unreliable but free and good-enough at the daily-bar cadence the system needs. Reliability is bought via aggressive incremental caching, retry/backoff, and an interface seam for paid swap-in.

## Constraints

- **Tech stack**: Python 3.11+, SQLite, yfinance, SEC EDGAR (HTTP + XML), Anthropic SDK (`claude-sonnet-4-5` default, configurable), scipy.optimize (SLSQP), Streamlit, IBKR (Client Portal API or `ib_insync`/TWS) — chosen by the spec; no substitution
- **Storage**: local SQLite under `cache/`; no remote DB, no managed service
- **Deployment**: macOS only (launchd daily job); no Docker, no cloud
- **Cost ceiling**: $25/run hard cap on Claude spend; cost tracker aborts on exceed
- **Risk discipline**: pre-trade veto is absolute — closing trades are the only exemption; no override flag
- **Audit**: every order, every veto, every circuit-breaker event must be persisted with timestamp + reason
- **Performance**: daily refresh end-to-end ~10 min (with `--no-filings --no-13f` skip on the launchd path)
- **Privacy**: no telemetry, no external reporting, all data local
- **Compatibility**: data layer must be interface-abstracted so paid feeds (Polygon, Tiingo, IEX, Alpha Vantage) swap in without rewriting downstream code
- **Compliance posture**: paper-only at v1 — live trading promotion requires explicit milestone with separate live-readiness review

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Paper-first IBKR; live execution gated for a later milestone | Reduces v1 risk surface; allows full dry-run + paper validation before any real capital | — Pending |
| Single-operator scope (localhost Streamlit, launchd, no LPs) | Matches actual user; avoids dragging in auth / compliance / multi-tenant | — Pending |
| Free data feeds (yfinance + EDGAR) with interface abstraction for paid swap | Ships v1 at zero data cost; upgradability preserved without rewrite | — Pending |
| Earnings-call transcript analyzer ships as stub | Avoids a paid transcript dependency in v1; clean re-entry point later | — Pending |
| `Meridian Capital Partners` as canonical product name (folder remains `Youtube Hedge Fund App`) | Project name from spec; folder rename out of scope to avoid breaking external paths | — Pending |
| Anthropic prompt caching mandatory on every system prompt | Required to stay under $25/run cost ceiling at 40-candidate fan-out | — Pending |
| MVO + conviction-tilt both ship; conviction-tilt is non-convergence fallback | Spec mandates fallback; conviction-tilt also serves as ground-truth for MVO debugging | — Pending |
| Pre-trade veto checks are absolute (no override) | Risk-discipline non-negotiable per spec; closing-trade exemption is the only carve-out | — Pending |
| Local SQLite for all persistence (no Postgres / managed) | Solo-operator, single-machine; SQLite is sufficient and zero-ops | — Pending |
| FOMC dates from live Federal Reserve calendar (not hardcoded) | Spec mandate; hardcoded dates rot annually | — Pending |
| IBKR-native short availability (not Alpaca flags) | Spec mandate; Alpaca data is wrong broker-of-record | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-04 after initialization*
