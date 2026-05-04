# Roadmap: Meridian Capital Partners (`MCP` / `ls_equity_fund`)

**Created:** 2026-05-04
**Granularity:** standard
**Total v1 phases:** 11 (Phase 0 through Phase 10)
**Post-v1:** Phase 11 (live-readiness review) — out of v1 scope per PROJECT.md

## Project Reference

**Core Value:** A solo operator can run a credible, sector-neutral, factor-driven L/S equity book end-to-end — score → analyze → optimize → vet → execute → report — every trading day, without manual stitching, with hard risk guardrails that cannot be bypassed.

**Architectural backbone:** layered batch pipeline; SQLite as integration hub; three swap-in seams (`MarketDataProvider`, `Optimizer`, `Broker`); conviction-tilt-first → MVO-as-plug-in resolves the L4↔L5 build-order cycle.

## Phases

- [x] **Phase 0: Foundation** — Repo scaffolding, config schema, SQLite migrations, three seam interfaces, PaperBroker stub, CLI skeleton, structlog audit setup (completed 2026-05-04)
- [ ] **Phase 1: Data Infrastructure (L1)** — Universe (3 modes) + PIT table, benchmarks, daily prices, fundamentals + 24 ratios, EDGAR (10-K/Q/8-K/Form 4 P/S/A/M/F), 13F, short interest, analyst estimates, earnings + FOMC calendars
- [ ] **Phase 2: Scoring Engine (L2)** — 8 factors × 27 sub-factors, GICS sector-percentile rank, P/S-only insider filter, factor_scores persistence
- [ ] **Phase 3: Reporting + Dashboard Skeleton** — Streamlit at `localhost:8502`, Pages I + II reading L1+L2 only; ranked candidates visible daily before the rest of the stack lands
- [ ] **Phase 4: Claude AI Analysis (L3)** — Anthropic client + cache_control + cost tracker + analysis cache (ship FIRST), then filing/risk/insider/sector analyzers, combined-score, per-candidate report; earnings analyzer ships as stub
- [ ] **Phase 5: Portfolio Construction — Conviction-tilt only (L4 partial)** — conviction-tilt optimizer, transaction-cost model, rebalance schedule, portfolio state, beta calc, factor exposure, rebalance generator with `--whatif`
- [ ] **Phase 6: Risk Management (L5)** — Barra-style cross-sectional factor risk model with Ledoit-Wolf shrinkage, 8-check pre-trade veto with earnings-blackout for new entries, explicit closing-trade definition, audit log every rejection, circuit breakers
- [ ] **Phase 7: Portfolio Construction — MVO swap-in (L4 complete)** — MVO optimizer (SLSQP) with full constraint set, conviction-tilt fallback on non-convergence with audit row
- [ ] **Phase 8: IBKR Execution — Paper (L6)** — ib_async broker, paper-first config, `MERIDIAN_LIVE_OK` gate, order executor with veto + borrow check + ADV chunking + signal-price slippage, IBKR-native short locate, borrow-rate capture, order manager + clean SIGINT, `--dry-run` / `--execute` CLI
- [ ] **Phase 9: Reporting — Full (L7)** — Daily P&L attribution, position attribution + Spearman, win/loss slicing, sector-relative alpha, turnover analytics, named tear-sheet metric set, Claude weekly commentary, dual-mode daily letter
- [ ] **Phase 10: Dashboard Polish + JARVIS + launchd + Promotion** — Pages III/IV/V/VI built out, market-hours auto-refresh, JARVIS chat with ~19KB JSON snapshot context, launchd plist with `WakeSystem=true` at 17:15 weekdays, AUDIT-03 paper→live promotion ceremony record

## Phase Details

### Phase 0: Foundation
**Goal**: System is bootable end-to-end with all seam interfaces defined and a deterministic in-memory broker, so every later phase can run against a working spine instead of a half-assembled one.
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-06, INFRA-07, INFRA-08, AUDIT-02
**Success Criteria** (what must be TRUE):
  1. Operator can run `uv sync` on a fresh clone and the project builds without errors against pinned versions (Python 3.11+, pandas>=2.2,<3.0, numpy>=2.0,<2.5, ib_async==2.1.x, edgartools>=5.30,<6, anthropic>=0.97).
  2. Operator can run a CLI smoke command that loads `config.yaml` (validated by pydantic-settings), opens the SQLite DB in WAL mode at the configured path, runs `alembic upgrade head` to apply the initial migration, and exits 0.
  3. Operator can import each of the three abstract base classes (`MarketDataProvider`, `Optimizer`, `Broker`) and instantiate the in-memory `PaperBroker` against the deterministic-fill contract — verifying the swap-in seams work before any concrete provider exists.
  4. `.gitignore` correctly excludes `.env`, `cache/`, and `output/` while keeping `.planning/` tracked; structlog emits JSON with API keys redacted from log output on a sample event.
**Plans:** 7/7 plans complete
  - [x] 00-01-PLAN.md — Project tooling (pyproject.toml, uv.lock, .gitignore, .env.example, config.yaml.example) [INFRA-06, INFRA-07]
  - [x] 00-02-PLAN.md — Composed pydantic-settings Config + isolated Secrets [INFRA-01]
  - [x] 00-03-PLAN.md — SQLite WAL gateway + Alembic migrations + initial runs/heartbeat tables [INFRA-02]
  - [x] 00-04-PLAN.md — structlog dual-sink + API-key redaction + run_id contextvars [AUDIT-02]
  - [x] 00-05-PLAN.md — Package layout + 3 seam ABCs (MarketDataProvider/Optimizer/Broker) + PaperBroker [INFRA-03]
  - [x] 00-06-PLAN.md — Typer CLI: meridian doctor working + 7 stub subcommands accepting global flags [INFRA-08]
  - [x] 00-07-PLAN.md — Phase 0 verification harness (all 4 SCs as automated tests)
**UI hint**: no

### Phase 1: Data Infrastructure (L1)
**Goal**: Operator runs one CLI command and SQLite has every feed needed to score the universe, with point-in-time integrity preserved at ingest so future backtests are not contaminated by survivorship or look-ahead bias.
**Depends on**: Phase 0
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07, DATA-08, DATA-09, DATA-10, DATA-11, DATA-12, DATA-13, DATA-14
**Success Criteria** (what must be TRUE):
  1. Operator can build the universe in any of the three modes (`sp500`, `liquid_us`, `scanner_seed`) and the resulting `universe` table has `first_seen_date`, `delisted_date`, and `inclusion_window` columns populated — delisted tickers are flagged, never deleted (binds **CP1 — survivorship/look-ahead bias**).
  2. Operator can run a daily refresh that incrementally ingests OHLCV (3y lookback, fetch-since-last-stored), fundamentals + 24 derived ratios, EDGAR filings (10-K/10-Q/8-K/Form 4) with compliant User-Agent + 10 req/s rate limiting, 13F, daily short interest, analyst estimates, earnings calendar (next 30 days), and live FOMC calendar with cached fallback.
  3. Operator can query `insider_transactions` and see `transaction_code` as a first-class column distinguishing P / S / A / M / F / G / D — synthetic Form 4 data with all code types parses with the correct codes assigned (binds **CP3 — Form 4 misclassification, ingest side**).
  4. Operator can run the same daily refresh with `--no-filings`, `--no-13f`, and `--forms` selective-pull flags and the run skips those feeds and completes in a fraction of the full-refresh time.
  5. Every concrete data fetch goes through the `MarketDataProvider` interface — a `PolygonProvider` stub class can be instantiated and selected by config without rewriting downstream code (validates the swap-in seam).
**Plans**: TBD
**UI hint**: no

### Phase 2: Scoring Engine (L2)
**Goal**: Operator runs `run-scoring` and gets a complete factor-score table for the universe, sector-neutral and audit-grade, with directional insider signal coming only from genuine buy/sell codes.
**Depends on**: Phase 1
**Requirements**: SCORE-01, SCORE-02, SCORE-03, SCORE-04, SCORE-05, SCORE-06, SCORE-07, SCORE-08, SCORE-09, SCORE-10
**Success Criteria** (what must be TRUE):
  1. Operator can run `run-scoring` and `factor_scores` is populated for the full universe with all 8 factors × 27 sub-factors, each scored 0–100 as percentile rank within GICS sector and sub-factors equal-weighted within parent factor.
  2. Operator can verify the insider factor counts only Form 4 P-codes and S-codes for net dollar flow; A/M/F/G/D codes contribute zero to the directional signal even though they remain logged for audit, and cluster-buy detection counts distinct insiders with code=P only (binds **CP3 — Form 4 misclassification, factor side**).
  3. Operator can verify side-aware short-interest scoring (longs reward declining SI, shorts reward rising SI) and the estimate-revisions factor returns a degenerate-neutral score until sufficient snapshot history accrues.
  4. Every scoring run persists the scores at-entry to `factor_scores` so a future predictive-power study can replay any historical day's signal exactly as it was generated.
**Plans**: TBD
**UI hint**: no

### Phase 3: Reporting + Dashboard Skeleton
**Goal**: Operator opens `localhost:8502` and sees today's ranked long/short candidates by sector with full Layer 1 + Layer 2 context — value before the rest of the stack lands, and a structural guard against late-pipeline blockers killing the project.
**Depends on**: Phase 2
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04
**Success Criteria** (what must be TRUE):
  1. Operator can navigate to `http://localhost:8502` and see the dark-themed dashboard (BG #0b0e17, indigo accent, Plus Jakarta Sans + JetBrains Mono, Streamlit chrome hidden) with the Roman-numeral pill nav (I PORTFOLIO · II RESEARCH · III RISK · IV PERFORMANCE · V EXECUTION · VI LETTER) where Pages I and II are fully rendered and Pages III–VI render placeholders.
  2. Operator can view Page I (Portfolio cover) with the JARVIS 92px header, the 10 metric cards (Universe / Long Candidates / Short Candidates / Positions / Crowding / Insider Events / CEO Buys / Cluster Buys / VIX / Earnings 7d) reading from L1+L2 SQLite, and the status strip with VIX-regime badge + data-source indicator.
  3. Operator can view Page II (Research) with KPIs, crowding warnings, the factor heatmap (top 30 + bottom 30 × 8 factors), and 10 long + 10 short candidate cards each showing Piotroski / Altman scores — all sourced from `factor_scores` with no Anthropic calls.
  4. The dashboard reads exclusively from SQLite — no factor compute or API call happens on page load — and the 5-minute auto-refresh hook is wired but conditional on market hours so the placeholder pages never trigger Anthropic.
**Plans**: TBD
**UI hint**: yes

### Phase 4: Claude AI Analysis (L3)
**Goal**: Operator can run Claude qualitative analysis across 4 analyzers × 40 tickers per run while staying under the $25/run hard ceiling, with infrastructure (cache + cost-tracker + analysis-result cache) shipped *before* any analyzer is enabled.
**Depends on**: Phase 2
**Requirements**: ANAL-01, ANAL-02, ANAL-03, ANAL-04, ANAL-05, ANAL-06, ANAL-07, ANAL-08, ANAL-09, ANAL-10, ANAL-11, ANAL-12
**Success Criteria** (what must be TRUE):
  1. Operator can verify that the Anthropic client passes system prompts as a content-block list (not a plain string) with `cache_control: {"type":"ephemeral"}` set, system prompts live in versioned files (`analysis/prompts/v1/`), and edits go to a new version directory — and the cost tracker correctly sums `input_tokens + (cache_creation_input_tokens × 1.25) + (cache_read_input_tokens × 0.10) + output_tokens × output_rate`, with cost validated against an actual Anthropic dashboard line item (binds **CP2 — prompt-cache invalidation + cache-write token cost**).
  2. Operator can run an integration test of 4 analyzers × 5 synthetic tickers and observe a soft warning at $20, a hard abort at $25, second-run cache hit rate > 90%, total cost < $5, and `analysis_results` SQLite cache returning hits for re-runs within the 30-day TTL keyed by `(analyzer, ticker, artifact_id)`.
  3. Operator can run each of the four analyzers (filing on 8 quarters, risk on 10-K Risk Factors with new-vs-prior diff and boilerplate %, insider on Form 4 last 90d, sector ranking) and get the spec-mandated JSON output shapes, plus the earnings-call analyzer which ships as a stub returning None.
  4. Operator can run combined-score and see 60% quant composite + 40% Claude average across available analyzers with re-rank within sector, falling back to 100% quant with no penalty when Claude data is absent — and per-candidate markdown reports land at `output/reports_{timestamp}/{TICKER}.md`.
  5. Analysis CLI supports `--estimate-cost`, `--ticker`, `--sector`, and full-run modes — the dry-cost estimate runs without calling the API.
**Plans**: TBD
**UI hint**: no

### Phase 5: Portfolio Construction — Conviction-tilt only (L4 partial)
**Goal**: Operator runs `run-portfolio --whatif` and the system delivers the first end-to-end data → score → analyze → portfolio chain using the always-works conviction-tilt optimizer, with no Layer 5 dependency — the spec-mandated fallback ships first.
**Depends on**: Phase 4
**Requirements**: PORT-01, PORT-04, PORT-05, PORT-06, PORT-07, PORT-08, PORT-09, PORT-10
**Success Criteria** (what must be TRUE):
  1. Operator can run `run-portfolio --whatif --optimize-method conviction` and see a target book of 20 longs / 20 shorts with conviction-tilt rules applied (top 5% × 1.5, top 10% × 1.25, ADV cap ≤ 5% of 20-day ADV, halve size if earnings within 5 days, beta-adjust, sector-neutral) using all `config.yaml` defaults (gross 150%, net 0–10%, max position 5%, max sector 25%, max beta 0.15, turnover 30%).
  2. Operator can run the rebalance generator and see the diff vs current positions respecting the 30% turnover budget, prioritized by largest score changes, with per-trade transaction-cost estimates broken into commission + spread + impact bps using a broker-configurable IBKR commission model (not hardcoded $0).
  3. Operator can query `portfolio_positions`, `portfolio_history`, and `position_approvals` and see corporate-action-handled state including `factor_scores_at_entry`, plus rolling 60-day stock beta vs SPY and portfolio long-book / short-book / net beta.
  4. Operator can run the rebalance schedule advisory and see warnings (not blocks) for earnings within 2d, FOMC within 5d (read from L1's live macro feed), and monthly opex within 3d (third Friday).
**Plans**: TBD
**UI hint**: no

### Phase 6: Risk Management (L5)
**Goal**: Operator's pre-trade veto, circuit breakers, and Barra-style risk model are live and absolute — the closing-trade exemption is the only carve-out and is rigorously defined; covariance is now available for Phase 7's MVO swap-in.
**Depends on**: Phase 5
**Requirements**: RISK-01, RISK-02, RISK-03, RISK-04, RISK-05, RISK-06, RISK-07, AUDIT-01
**Success Criteria** (what must be TRUE):
  1. Operator can run the Barra-style cross-sectional factor risk model with 120-day lookback and get factor returns, annualized factor covariance matrix with Ledoit-Wolf shrinkage, specific variance per stock, portfolio factor variance + specific variance + total variance, and per-position MCTR — and the predicted covariance matrix is consumable by Layer 4 MVO downstream.
  2. Operator can submit a candidate trade through the pre-trade veto and see all 8 absolute checks evaluated — (1) halt lock, (2) earnings-blackout for **new entries** (closing trades exempt), (3) liquidity ≤ 5% ADV, (4) position ≤ 5% AUM, (5) sector ≤ 25%, (6) gross/net exposure within bounds, (7) |net beta| ≤ 0.20, (8) pairwise correlation ≤ 0.80 — with any failure rejecting the trade and persisting (timestamp, ticker, reason, trade context) to an immutable audit log.
  3. Operator can verify the closing-trade definition requires *all three* conditions — `abs(new_position) < abs(old_position) AND sign(new_position) == sign(old_position) AND abs(trade_qty) <= abs(old_position)` — and the unit tests cover (a) partial reduce → NOT closing, (b) full close → closing, (c) long→short flip → NOT closing, (d) full close + reverse → NOT closing; `is_closing_trade: bool` is an explicit audit field on every order with the rule that produced it (binds **CP5 — pre-trade veto bypass via closing-trade mislabel**).
  4. Operator can simulate breaching each circuit-breaker threshold and observe the correct action firing automatically — daily loss > 1.5% → SIZE_DOWN 30%; daily loss > 2.5% → CLOSE_ALL_TODAY; weekly loss > 4% → SIZE_DOWN 30%; drawdown > 8% → KILL_SWITCH; single position > 3% NAV → force-close — with each event persisting (timestamp, breaker type, threshold, observed value, portfolio state snapshot).
**Plans**: TBD
**UI hint**: no

### Phase 7: Portfolio Construction — MVO swap-in (L4 complete)
**Goal**: Operator can flip `optimizer: mvo` in config and the MVO optimizer plugs in cleanly behind the `Optimizer` seam, using L5's covariance with Ledoit-Wolf shrinkage; conviction-tilt remains the spec-mandated fallback on non-convergence.
**Depends on**: Phase 6
**Requirements**: PORT-02, PORT-03
**Success Criteria** (what must be TRUE):
  1. Operator can run `run-portfolio --whatif --optimize-method mvo` and the SLSQP optimizer produces a target book using cost-net expected returns mapped from composite score (100 → +15%/yr, 0 → −15%/yr) with the full constraint set (gross long/short, per-position bounds, beta constraint, sector net, single-side sector) — running against L5's predicted covariance matrix with Ledoit-Wolf shrinkage applied (binds **CP4 — MVO covariance instability without Ledoit-Wolf**).
  2. Operator can intentionally feed an ill-conditioned covariance into MVO and verify the ex-ante volatility sanity check fires (refuse rebalance if model-implied portfolio vol < 5% annualized), the optimizer falls back to conviction-tilt — never silently reuses yesterday's weights — and an audit-log row is written with `(timestamp, reason, fallback used)` so MVO health can be tracked over time.
  3. Operator can flip between `optimize-method conviction` and `optimize-method mvo` via CLI flag or `config.yaml` without code changes, validating the `Optimizer` seam works as a true plug-in.
**Plans**: TBD
**UI hint**: no

### Phase 8: IBKR Execution — Paper (L6)
**Goal**: Operator can submit a vetoed rebalance through IBKR paper with full slippage capture, IBKR-native borrow checks including HTB rate, and a clean SIGINT shutdown — paper-only by default, live trading guarded by `MERIDIAN_LIVE_OK=1` plus the AUDIT-03 promotion record.
**Depends on**: Phase 7
**Requirements**: EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-05, EXEC-06, EXEC-07, EXEC-08, EXEC-09
**Success Criteria** (what must be TRUE):
  1. Operator can run `run-execution --dry-run` and see exactly what would be placed (per-ticker side / shares / limit / TIF / chunking), then run `run-execution --execute` against IBKR paper and observe orders placed via `ib_async` with the pre-trade veto and IBKR-native borrow check applied per order, ADV chunking above the configurable threshold, configurable limit-price policy (signal/close/market reference), TIF, retry-on-timeout, and `signal_price` recorded for every order.
  2. Operator can attempt to instantiate the broker in live mode without `MERIDIAN_LIVE_OK=1` set and the broker refuses to start; operator confirms the live mode also requires the AUDIT-03 promotion record (defense-in-depth against accidental live trading).
  3. Operator can query the slippage tracker and see side-aware bps slippage = `(fill − signal) / signal × 10000`, plus 30-day rolling avg / median / p95 / total $ cost / worst-5 fills, and verify HTB borrow rate is captured per short and fed into the L4 transaction-cost model (so short-side P&L is no longer silently optimistic).
  4. Operator can issue SIGINT mid-execution and the order manager cancels all pending orders, retains live positions, persists final state cleanly, and every order row contains `(timestamp, ticker, side, shares, limit, fill, slippage_bps, status, broker_order_id)`.
**Plans**: TBD
**UI hint**: no

### Phase 9: Reporting — Full (L7)
**Goal**: Operator's daily P&L, attribution, win/loss, sector-relative alpha, turnover analytics, tear sheet, weekly commentary, and dual-mode daily letter all generate from a single CLI command — pre-computed by the CLI run and stored in SQLite for the dashboard to read.
**Depends on**: Phase 8
**Requirements**: REPORT-01, REPORT-02, REPORT-03, REPORT-04, REPORT-05, REPORT-06, REPORT-07, REPORT-08
**Success Criteria** (what must be TRUE):
  1. Operator can run `run-reporting` and see daily P&L attribution decompose `daily_return` into beta (net_beta × SPY return), sector (Brinson-style), factor (regression on factor return spreads), and alpha (residual) — persisting to `output/daily_attribution.csv` — alongside position attribution with FIFO round-trips, best/worst per side, and Spearman correlation between entry-time score and realized return.
  2. Operator can view win/loss analysis sliced by side, holding period (1-5d / 5-20d / 20-60d / 60d+), sector, VIX regime at entry, and factor quintile at entry (with streaks); plus sector-relative alpha per sector over 90d (picks vs sector ETF), total alpha summed across sectors, winner/loser sector counts; plus turnover analytics over 30d / 90d / annualized vs configured budget with a configurable jurisdiction tax model (no US-only hardcoding).
  3. Operator can generate the institutional-format markdown tear sheet exposing the named metric set — Sharpe, Sortino, Calmar, max-DD, hit-rate, profit-factor, skew, kurtosis, tail — alongside metrics-vs-SPY, monthly-returns grid, equity curve, drawdown chart, rolling 12-month Sharpe, factor + sector exposures, and turnover.
  4. Operator can verify the Claude weekly commentary fires on the configurable weekday (default Friday) and the daily letter renders in both `mode: lp` (formal LP letterhead with Delaware domicile, AUM, doc ID `MCP-IM-{YYYY}-{MMDD}`, CONFIDENTIAL stamp, "Dear Limited Partners," JARVIS-voiced 3–4 paragraph body, signature, compliance footer) and `mode: internal` (ops voice), both cached by date with a regenerate action.
**Plans**: TBD
**UI hint**: no

### Phase 10: Dashboard Polish + JARVIS + launchd + Promotion
**Goal**: Operator's daily-refresh runs unattended via launchd, Pages III–VI are fully rendered, JARVIS chat answers from a ~19KB JSON snapshot of system state, and a documented paper→live promotion ceremony exists with named numeric criteria — the v1 system is operationally complete.
**Depends on**: Phase 9
**Requirements**: DASH-05, DASH-06, DASH-07, DASH-08, DASH-09, INFRA-04, INFRA-05, AUDIT-03
**Success Criteria** (what must be TRUE):
  1. Operator can navigate Page III (Risk) — circuit-breaker bars, tail-risk KPIs, risk-decomposition donut, factor-risk-contributions table, MCTR table with disproportionate-risk flag, factor-exposure bars with 1.5σ warnings, 6-scenario stress test, correlation heatmap + effective bets, 72hr alerts; Page IV (Performance) — equity curve vs SPY rebased 100, monthly-returns heatmap, drawdown chart, P&L attribution bars, rolling 12-month Sharpe, sector-relative alpha + total alpha, turnover panel, transaction-cost panel, best/worst-5, win/loss panel, Claude weekly commentary card; Page V (Execution) — KPI row, open-orders table polling IBKR, recent-trades log (last 200), worst-5 fills, short-availability panel, daily notional turnover; Page VI (Letter) — formal LP letter + internal-mode template + regenerate, cache by date.
  2. Operator can ask JARVIS a question via the chat on Page I and the response uses the cached ~19KB JSON snapshot of system state as Anthropic context with prompt caching applied — and 5-minute auto-refresh during market hours (9:30am–4:00pm ET) is idempotent: refresh does not re-trigger Anthropic calls (cache hits used; pre-computed L7 outputs read from SQLite).
  3. Operator can install the macOS launchd plist at `~/Library/LaunchAgents/com.user.hedgefund.daily.plist` with `WakeSystem=true` running `run_scoring.py --no-filings --no-13f` weekdays at 17:15 local — the daily-refresh job records a `runs` row (start_ts, end_ts, status, error) and writes a heartbeat file the dashboard surfaces if the job goes silent — and the run targets ~10 min end-to-end.
  4. Operator can read `PROMOTION.md` documenting paper→live promotion criteria with named numeric thresholds (≥ N weeks paper, max DD < X%, slippage within Y bps of model, factor IC stable, audit log clean), and verify the live mode is gated *both* by the `MERIDIAN_LIVE_OK=1` env-var *and* by an operator-signed checked-criteria record — without both, live mode refuses to instantiate.
**Plans**: TBD
**UI hint**: yes

### Phase 11: Live-Readiness Review (POST-V1 — out of v1 scope)
**Goal** (post-v1): Execute the AUDIT-03 promotion ceremony against accumulated paper-trading evidence; ungate live trading only after every named numeric criterion is met. **Not delivered in v1 per PROJECT.md constraints.**

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0. Foundation | 7/7 | Complete   | 2026-05-04 |
| 1. Data Infrastructure (L1) | 0/? | Not started | - |
| 2. Scoring Engine (L2) | 0/? | Not started | - |
| 3. Reporting + Dashboard Skeleton | 0/? | Not started | - |
| 4. Claude AI Analysis (L3) | 0/? | Not started | - |
| 5. Portfolio Construction — Conviction-tilt (L4 partial) | 0/? | Not started | - |
| 6. Risk Management (L5) | 0/? | Not started | - |
| 7. Portfolio Construction — MVO swap-in (L4 complete) | 0/? | Not started | - |
| 8. IBKR Execution — Paper (L6) | 0/? | Not started | - |
| 9. Reporting — Full (L7) | 0/? | Not started | - |
| 10. Dashboard Polish + JARVIS + launchd + Promotion | 0/? | Not started | - |

## Dependencies Graph

```
Phase 0 (Foundation)
  └── Phase 1 (L1 Data)
        └── Phase 2 (L2 Scoring)
              ├── Phase 3 (Reporting + Dashboard Skeleton)
              └── Phase 4 (L3 Claude Analysis)
                    └── Phase 5 (L4 Conviction-tilt)
                          └── Phase 6 (L5 Risk + Veto + Breakers)
                                └── Phase 7 (L4 MVO swap-in)
                                      └── Phase 8 (L6 IBKR Paper Execution)
                                            └── Phase 9 (L7 Reporting Full)
                                                  └── Phase 10 (Dashboard Polish + launchd + Promotion)
```

Phase 3 ships value early in parallel with the Phase 4 chain — operator sees daily ranked candidates as soon as L2 lands.

## Coverage

- **v1 requirements:** 90 total
- **Mapped to phases:** 90 / 90 (100%) ✓
- **Unmapped:** 0
- **Critical pitfalls bound to phase success criteria:**
  - CP1 (survivorship/look-ahead bias) → Phase 1, criterion 1
  - CP2 (Anthropic prompt-cache invalidation + cache-write token cost) → Phase 4, criterion 1
  - CP3 (Form 4 P/S vs A/M/F misclassification) → Phase 1 criterion 3 (ingest) + Phase 2 criterion 2 (factor)
  - CP4 (MVO covariance instability) → Phase 7, criterion 1
  - CP5 (closing-trade veto bypass) → Phase 6, criterion 3

## Out of v1 Scope (Tracked)

- **Phase 11 — Live-readiness review** (post-v1): executes AUDIT-03 ceremony with named criteria; live-trading promotion gate
- **BACKTEST-01 / BACKTEST-02** (v2): walk-forward harness over L1's 3y OHLCV with PIT universe; factor IC / staleness monitor
- **TRANSCRIPT-01** (v2): earnings-call transcript provider feeding L3 earnings analyzer (currently a stub)
- **LIVE-01** (v2): real-capital promotion milestone

---
*Roadmap created: 2026-05-04*
