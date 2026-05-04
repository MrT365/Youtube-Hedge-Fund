# Domain Pitfalls

**Domain:** Single-operator long/short US equity quant system (Meridian Capital Partners / `ls_equity_fund`)
**Stack:** Python 3.11, yfinance, SEC EDGAR, Anthropic SDK (`claude-sonnet-4-5`), scipy SLSQP, SQLite, Streamlit, IBKR (paper-first)
**Researched:** 2026-05-04
**Overall confidence:** HIGH (most claims verified against vendor docs and primary research; severity rankings opinionated)

---

## Summary

This catalogue exists to prevent the failure modes that have killed real solo-built L/S systems before they shipped or — worse — silently produced wrong P&L for months before being caught. The trap pattern in this domain is **silent correctness failure**: the system runs, the dashboard renders, the orders fill, but the inputs are wrong (look-ahead leak, survivor-only universe, F-coded "buys"), the optimizer is wrong (sample-covariance blow-up, stale weights), or the cost meter is wrong (cache_creation tokens uncounted). Loud crashes are easy. Silent rot is what eats this kind of system.

Three structural choices in this project amplify standard quant pitfalls:
1. **yfinance + free EDGAR for fundamentals** — restated, not point-in-time, no delisted tickers.
2. **Single-operator paper→live promotion path** — no second pair of eyes on the gate; needs ceremony enforced in code.
3. **Claude qualitative analysis with $25/run cap** — caching is load-bearing, not optional; one careless system-prompt edit can blow the budget on day one.

Severity reflects "how bad if it ships unaddressed in v1." Critical = strategy-invalidating or money-losing; High = forces rebuild; Medium = degrades performance/reliability; Low = annoyance or cosmetic.

---

## Top-5 Critical Pitfalls (read these first)

| # | Pitfall | Phase | Why critical |
|---|---------|-------|--------------|
| 1 | **Survivorship + look-ahead bias from yfinance fundamentals** (restated, current-S&P-500-only universe) | L1 | Backtest and live-vs-paper attribution are both invalidated. Every downstream factor inherits the bias. |
| 2 | **Anthropic prompt-cache invalidation** (system-prompt edit, image presence, tools change → cache miss → $25 ceiling blown in one run) | L3 | Hard cost ceiling is the binding constraint; one whitespace change in a system prompt re-creates a 4-tier-cache fan-out. |
| 3 | **Form 4 transaction-code misclassification** (treating F/A/M/G as buys) | L1, L2 | Insider factor becomes inverted noise; the "3× CEO/CFO buy" weighting amplifies the wrong signal. |
| 4 | **MVO covariance instability without shrinkage** (sample covariance on 120d × N stocks → garbage portfolios concentrated in lowest-variance eigenvector) | L4, L5 | Optimizer "converges" to a portfolio that is mathematically optimal on the wrong matrix; corrupts the entire book. |
| 5 | **Pre-trade veto bypass via "closing trade" mislabel** + circuit-breaker absent during volatile open | L5, L6 | The whole risk discipline is the one thing that protects a paper→live transition. A single bypass path makes the "absolute" claim false. Knight Capital 2012 ($440M, 45 minutes) is the canonical reminder of what a single un-reviewed code path on the order router can do. |

---

## Pitfalls by Domain

### Data Pitfalls (L1)

#### D1. Survivorship bias from current-S&P-500-only universe — **CRITICAL**
**What goes wrong:** Universe sourced from today's S&P 500 (or today's "liquid US" screen) excludes every name that was delisted, merged, acquired, or went to zero. yfinance does not provide point-in-time index membership.
**Why it happens:** `yfinance` exposes only "the current set of stocks"; there is no `as_of_date` knob.
**Consequences:** Backtests show inflated returns (you're holding only winners by construction); short alpha is dramatically overstated (the stocks you'd have shorted that went to zero are absent); live performance diverges from paper on day one of a real drawdown.
**Warning signs:** Backtest Sharpe > 2 on a vanilla factor model; long book outperforms a comparable equal-weight S&P benchmark by >5% / yr in backtest; no tickers in the universe ending in "Q" (bankruptcy) or with the `-OLD` suffix.
**Prevention:**
- Build the universe from a **historical** index-membership table (e.g., snapshot S&P 500 constituents per quarter, store in `cache/universe_history.db`).
- For v1: state explicitly in the LP-letter footer "backtest universe is current-membership; live performance is the only ground truth."
- Add a `data/universe.py` interface that takes `as_of: date` even if v1 ignores it — keeps the seam open for paid feed swap-in.
**Phase:** L1 (universe construction).
**Real-world cite:** Carpenter & Lynch (1999) document survivorship bias of ~1–2% / yr in mutual-fund return studies; effect is larger in factor strategies that explicitly screen on quality/value.

#### D2. Look-ahead bias from yfinance restated fundamentals — **CRITICAL**
**What goes wrong:** yfinance returns the **most recent restated** income statement / balance sheet / cash flow. A 10-K filed in March 2024 covering FY2023 may have been restated in November 2024; yfinance gives you the November version. Backtesting a 2024-Q2 trade with that data is using information that did not exist at the time.
**Why it happens:** No vendor with a free tier exposes point-in-time fundamentals. The yfinance maintainers have stated this explicitly in their issue tracker.
**Consequences:** Quality / value / growth / accruals factors all fed restated numbers. Piotroski-F and Altman-Z are particularly sensitive. Backtest Sharpe inflated; live performance disappoints.
**Warning signs:** Quality factor Spearman vs forward returns > 0.15 in backtest (real-world is ~0.03–0.06); value factor backtest beats Fama-French SMB+HML by >2σ.
**Prevention:**
- Time-stamp every fundamental row with `as_of_filing_date` at ingest, not just `period_end`.
- For v1 live trading this matters less (we only use today's data going forward) — but **never use yfinance fundamentals for backtests**, period. Treat backtests as L7-future-work, not L1.
- Document in `STACK.md` that L1 is live-data-only by design.
**Phase:** L1 (fundamentals); blocks any L7 backtest work.
**Severity:** Critical for any backtest claim; High for live-only operation if the operator is disciplined about not backtesting.

#### D3. Form 4 transaction-code misclassification — **CRITICAL**
**What goes wrong:** Treating any Form 4 acquisition as a "buy." The codes that matter:
- **P** = open-market purchase (the only true "insider buying" signal).
- **S** = open-market sale (the true "selling" signal).
- **A** = grant/award (compensation; not a buy).
- **M** = exercise of derivative (mechanical; usually accompanied by an immediate F or S).
- **F** = withhold-to-cover (tax payment; the most common code overall, **never** a directional signal).
- **G** = bona fide gift (not a sale, but reduces holdings).
- **D** = disposition to issuer (buyback-related, not directional).
**Why it happens:** Naive parsers count `transactionAcquiredDisposedCode == 'A'` (acquired/disposed flag) or sum dollar-volume across all rows. The L1 spec already lists P/S/A/M/F — but the trap is in the **scoring layer** (L2), where a "net dollar flow" that includes A and M will drown actual P-code signal.
**Consequences:** "CEO bought $50M last quarter" lights up the dashboard when it's actually $50M of vesting (A+M+F). The 3× CEO/CFO weight then amplifies the wrong direction.
**Warning signs:** Insider factor has near-zero rank correlation with subsequent returns; insider-flagged longs cluster in companies with active equity-comp programs (most large-caps).
**Prevention:**
- In `data/insider.py`, store the **transaction code** as a first-class column.
- In `factors/insider.py`, compute net flow from **P-only minus S-only**. Treat A/M/F/G/D as zero in the flow signal (still log them for audit).
- Cluster-buy detection: count distinct insider IDs with code=P in a rolling 30 days, not all distinct insiders.
- Unit-test against a known case (e.g., a CFO 10b5-1 sale schedule that file as S, not F).
**Phase:** L1 (parser) + L2 (scoring).
**Real-world cite:** Cohen, Malloy & Pomorski (2012) "Decoding Inside Information" — only **routine vs opportunistic** P-code purchases predict; A/M/F have zero predictive power and are noise.

#### D4. 13F 45-day lag treated as current — **HIGH**
**What goes wrong:** A 13F filed May 15 reports positions **as of March 31**. Treating it as "what the fund holds today" injects a 45-to-105-day staleness depending on when in the quarter you observe.
**Why it happens:** SEC rule 13F-1 gives institutional managers 45 days post-quarter-end to file; ~30% wait the full window. By the time a position is public, the manager may have trimmed or exited.
**Consequences:** "Tracked-fund opening" signal fires on positions the fund has already cut. Multi-fund-opening flag is particularly fragile because the funds may all have rotated by the time you see it.
**Warning signs:** Backtest of "buy when N tracked funds open" shows alpha; out-of-sample shows none.
**Prevention:**
- Store filings with both `period_end` and `filed_date`; never present the position as current.
- Apply institutional-flow factor only to **filings ≤ 75 days old** (filed_date - period_end + days-since-filed < 75) so you're working within the half-life of the signal (academic estimate ~4 months).
- Alpha decay half-life ~4 months means a position seen on day 45 has ~80% of its peak alpha; on day 90, ~50%; this should be the weight schedule, not a hard cutoff.
- LP letter / dashboard must label all 13F displays with "as of [period_end]" not "current."
**Phase:** L1 (ingest) + L2 (factor weighting).
**Severity:** High — wrong but not strategy-invalidating if labeled honestly.

#### D5. Stale fundamentals during pre-filing window — **HIGH**
**What goes wrong:** Company X had a fiscal year-end Dec 31, 2025. The 10-K isn't filed until March 2026. From Jan 1 to filing date, your data is the old 2024 10-K — but yfinance's "trailing twelve months" line items may already be a mix of estimates / interim 10-Q filings, depending on which field.
**Why it happens:** yfinance silently mixes annual 10-K + most-recent 10-Q + analyst estimates in different fields, with no flag for which is which.
**Consequences:** Quality and accruals factors get whipsawed at the turn of every reporting period. A stock that just reported great Q4 still scores on year-old quality numbers if you're keying on annual filings.
**Warning signs:** Quality factor rank changes by >20 percentile-points in a single day for a stock that didn't move; the day always coincides with an earnings release.
**Prevention:**
- Snapshot the 10-Q ingestion date into the row; do not silently overwrite the previous quarter's row.
- Compute trailing-twelve-month metrics from the four most recent 10-Q rows yourself; do not trust yfinance's TTM field.
- Put a `data_age_days` column on every fundamental row; if > 95 days old, demote that ticker to sector-median in scoring.
**Phase:** L1 (ingest) + L2 (factor staleness handling).

#### D6. Earnings-date data quality from yfinance — **MEDIUM**
**What goes wrong:** yfinance's `get_earnings_dates()` is wrong about confirmed vs estimated dates, occasionally drops dates entirely, and is sometimes timezone-shifted. The earnings-halving rule in L4 depends on this.
**Consequences:** A position is at full size into an earnings print; circuit breaker fires the next day.
**Prevention:**
- Cross-reference with a second source (NASDAQ earnings calendar HTML, free) at least weekly; fail the daily run if dates disagree by > 2 days.
- Treat any earnings date within 5 trading days as "earnings imminent" (wider than 1 day) to absorb timezone / confirmed-vs-estimated drift.
- Persist `earnings_date_source` and `earnings_date_confidence` columns; show in dashboard.
**Phase:** L1 + L4.

#### D7. Corporate-action handling (splits, spinoffs, ticker changes) — **HIGH**
**What goes wrong:** yfinance auto-adjusts historical prices for splits/dividends but **not** for spinoffs (where the parent + spinoff combined value ≠ the parent pre-spinoff). Ticker changes (FB→META, GOOG→GOOGL ambiguity) silently break time-series joins.
**Consequences:** Momentum factor sees a 30% "drop" on spinoff day; quality factor sees a 5σ event on the parent. The system tries to short the spinoff parent.
**Warning signs:** Single-day ±20% returns that don't appear in any news feed; tickers with NaN history before a known date.
**Prevention:**
- Maintain a `corporate_actions.csv` (manual-curated for v1, automated later from EDGAR 8-K parsing) listing splits / spinoffs / mergers / ticker changes for the universe.
- On daily ingest, flag any ticker with |day-over-day return| > 15% **and** no earnings date — surface in dashboard as "needs-review."
- For ticker changes, ingest the new ticker as a fresh time series and link via a `ticker_aliases` table.
**Phase:** L1.

#### D8. GICS sector reclassification mid-year — **MEDIUM**
**What goes wrong:** GICS reclassifications happen (the 2018 Communications Services reshuffle moved FB / GOOGL / NFLX out of Tech / Discretionary mid-September). If your sector membership is sourced once at universe build, the percentile-rank join key is wrong from that day onward for those names.
**Consequences:** GOOGL is being sector-percentile-ranked against Tech peers when it should be ranked against Comms peers. Downstream attribution by sector is also wrong.
**Prevention:**
- Snapshot sector membership monthly with `as_of_date`; use the most recent snapshot as-of the scoring date.
- Build a `sector_changes` audit table; surface any sector change in the dashboard as a yellow flag for that ticker for 30 days post-change.
- Re-rank sectors (compute percentiles) only with the membership in effect on the scoring date — never backfill old scores with new sector assignments.
**Phase:** L1 (sector data) + L2 (percentile-rank join).
**Real-world cite:** MSCI/S&P Sept 2018 Communications Services reshuffle — Comms sector weight in S&P 500 jumped 1.9% → 9.9% overnight; any sector-neutral system that didn't update mid-month was systematically mis-ranking ~10% of large-caps.

---

### Factor / Scoring Pitfalls (L2)

#### F1. Sector imbalance after percentile rank with small sectors — **HIGH**
**What goes wrong:** Real Estate and Utilities each have ~25–35 names in S&P 500. Materials and Energy have ~25 and ~22 respectively. Some scanner-seed universes can produce sectors with **3–5 names**. Percentile rank within those sectors is statistically meaningless — every name lands at one of {0, 25, 50, 75, 100}.
**Consequences:** A 3-stock sector contributes one "100th-percentile long" candidate every day, regardless of its absolute factor value. The book accumulates sector-neutral but factor-meaningless positions.
**Warning signs:** Same small-sector name appears in the long book every rebalance; sector concentration shows correctly (no flag) but factor-quality of the picks is bottom-decile.
**Prevention:**
- Set a minimum sector size of `N=10` for percentile ranking. Sectors below `N` get **z-score ranking against the whole-market median** for that factor instead.
- Surface in dashboard: "Sector X has only 4 names; percentile rank suppressed; using cross-sector z-score."
- Optionally: down-weight signals from small sectors by `sqrt(n / 10)` capped at 1.0.
**Phase:** L2.

#### F2. Z-score vs percentile mismatch — **MEDIUM**
**What goes wrong:** Z-score is sensitive to outliers (one Tesla blows up the volatility scaling). Percentile rank is robust but loses magnitude information ("this stock is 99th percentile" = "barely 99th" or "10× the median" — same score). Using percentile across the board (as the spec mandates) is mostly right, but breaks down for **estimate revisions** where the magnitude of revision (5% up vs 50% up) matters and most stocks have zero revision.
**Prevention:**
- Percentile rank across most factors (matches spec).
- For estimate revisions: percentile **within the non-zero subset**, then assign sector-median to the zero-revision majority.
- For factors with heavy zero-mass (insider, revisions, short-change), document which transformation is used in the factor's docstring.
**Phase:** L2.

#### F3. Equal-weight factor combination assumption — **HIGH**
**What goes wrong:** "Average of 8 factor percentiles" silently assumes (a) factors have equal predictive power, (b) factors are uncorrelated. Neither is true. Quality and value are ~0.3 correlated; momentum and revisions are ~0.5 correlated. Equal-weighting double-counts the correlated factors.
**Consequences:** The composite is dominated by the value-quality cluster; momentum-revisions cluster contributes less than its naive 25% weight; estimate revisions and insider (low-correlation) contribute even less than that.
**Warning signs:** Composite Spearman vs forward returns ≈ best-single-factor Spearman (no diversification benefit); long book is heavily quality-value-tilted regardless of regime.
**Prevention:**
- v1 ships with equal weights but **logs** factor-cluster correlations daily.
- If any pair > 0.6, raise warning in dashboard and document in next phase as "rotate to risk-weighted aggregation."
- Risk-weighted aggregation (factor weight ∝ 1/factor_vol, with cluster shrinkage) is a phase-3 enhancement.
- Acknowledge in LP letter: "v1 aggregation is equal-weighted; rebalance enhancements deferred."
**Phase:** L2.

#### F4. Estimate-revisions factor garbage until 90+ days of snapshots — **HIGH**
**What goes wrong:** The 30/60/90-day revision deltas need 30/60/90 days of stored snapshots before they can produce non-degenerate signal. Day 1 of operation, 100% of names have zero revision (no history) and the factor is uniform.
**Consequences:** A `mean(8 factors)` composite that includes a uniform factor on day 1 is silently weighting the other 7 factors at 12.5% each instead of 14.3%. Worse: as the snapshot history fills in over the first 90 days, the factor's effective weight **rises**, changing the strategy without anyone editing config.
**Warning signs:** Factor cross-sectional std-dev rises steadily for the first 3 months; backtest results from month 1 cannot be replicated.
**Prevention:**
- Spec already calls this "degenerate-neutral until snapshot history accrues" — implement that explicitly. While `n_snapshots < 30`, set the factor to **sector-median** for every name (effective weight = 0 in the composite).
- Recompute composite weights dynamically based on `n_active_factors` (drop degenerate factors from the average rather than letting them dilute).
- Persist the exact composite-weight formula in the audit log per run.
**Phase:** L2.

#### F5. Insider-data sparseness → sector-median fallback dominates — **MEDIUM**
**What goes wrong:** A typical 90-day window has ~30% of the universe with **zero** Form 4 P-code activity. Falling back to sector median for those names means a third of the universe gets the same score, all clustered around the sector center. The factor's effective cross-sectional variance collapses.
**Consequences:** Insider factor contributes near-zero to ranking differentiation despite its 12.5% nominal weight.
**Prevention:**
- Distinguish "no signal" (zero P-code) from "negative signal" (S-code without P-code). v1 spec already does this with side-aware scoring; verify it actually behaves so.
- For names with zero P/S activity, exclude from the insider factor entirely (set NaN, then re-normalize composite weights across non-NaN factors).
- Track and surface daily: % of universe receiving sector-median fallback per factor.
**Phase:** L2.

---

### Optimization Pitfalls (L4)

#### O1. MVO covariance instability without shrinkage — **CRITICAL**
**What goes wrong:** Sample covariance on N stocks × T days where T < 10×N is ill-conditioned. The smallest eigenvalues are noise. MVO loves loading on the lowest-variance eigenvectors → portfolio concentrates in apparently-low-risk pairs that are just noise artifacts.
**Why it happens:** With N=500 and T=120 (the spec's risk-model lookback), the sample covariance is rank-deficient (rank ≤ T-1 = 119) on a 500×500 matrix. Naive inversion in MVO blows up.
**Consequences:** "Optimal" portfolio with 80% of weight in 5 names with apparently zero-correlation; live realized risk is 3–5× the model estimate.
**Warning signs:** Position weights change by >50% between consecutive rebalances on stable inputs; ex-ante portfolio vol < 1% (laughably low); top-eigenvalue / smallest-eigenvalue ratio > 1e6.
**Prevention:**
- Use **Ledoit-Wolf shrinkage** (`sklearn.covariance.LedoitWolf`) — provably outperforms sample covariance in MVO across all empirical scenarios per Ledoit & Wolf (2003).
- Or: build covariance from the L5 Barra-style factor model (B Σ_F B' + diag(σ_specific²)) — this is the spec's intended path. Make sure L5 actually runs **before** L4 MVO, not after.
- Add ex-ante vol sanity check: if model-implied portfolio vol < 5% annualized, refuse to rebalance and fall back to conviction-tilt.
**Phase:** L4 (MVO) + L5 (covariance source).
**Real-world cite:** Ledoit & Wolf (2003) "Honey, I Shrunk the Sample Covariance Matrix" — the foundational paper; shrinkage beats sample in **every** scenario tested.

#### O2. SLSQP convergence failure with tight constraints — **HIGH**
**What goes wrong:** SLSQP can fail to converge from certain initial points, especially with simultaneously-active equality constraints (sum=0 for dollar-neutrality, sum=0 for beta-neutrality, per-sector sum=0). The solver returns `success=False` but with a non-None `x`.
**Consequences:** Code that ignores `result.success` ships nonsense weights; code that respects it but has no fallback halts the daily run.
**Prevention:**
- Always check `result.success` and `result.status` (status 9 = "iteration limit").
- On failure: warm-start from previous-day's weights, retry once with looser tolerance.
- On second failure: **fall back to conviction-tilt** (the spec's intended path) — log the failure, surface in dashboard with red flag.
- Never silently use stale weights; either rerun cleanly or rerun via fallback. Stale-weight reuse is the worst option (see O3).
- Consider `trust-constr` as third fallback for problems where SLSQP repeatedly fails.
**Phase:** L4.

#### O3. Stale-weight fallback semantics — **CRITICAL** (silent failure mode)
**What goes wrong:** When MVO fails, code paths that "use yesterday's weights" produce a portfolio that does no rebalancing. The system appears to run; orders are zero; risk drifts.
**Consequences:** Risk model becomes stale relative to actual book; circuit breakers fire on drift the operator didn't authorize.
**Warning signs:** Zero orders generated for several consecutive days while market moved.
**Prevention:**
- The spec's "conviction-tilt fallback on non-convergence" is the correct semantics — implement it as the **only** fallback path.
- Never reuse yesterday's weights as a fallback. If both MVO and conviction-tilt fail, **halt the run and notify** (write a red flag file the launchd job parses; if seen, do not execute orders).
- Audit log must record which optimizer ran (`mvo` / `conviction_tilt` / `halt`) with the reason.
**Phase:** L4.

#### O4. Transaction-cost model too optimistic → churn — **HIGH**
**What goes wrong:** Underestimating impact / spread / commission causes the optimizer to find more apparent "alpha" than exists, generating turnover that's not breakeven net of true costs.
**Consequences:** 30% turnover budget is used; net alpha is negative once realized costs are accounted.
**Warning signs:** Realized slippage (L6 tracker) > 2× modeled slippage on a rolling 60d basis.
**Prevention:**
- Use a **conservative** impact model: `impact_bps = c * sqrt(participation_rate)` with c ≥ 10 (Almgren empirically ~5–10 for liquid US equities; pad upward for paper-account liquidity uncertainty).
- Include borrow cost on shorts (negative rebate, often -0.25% to -100%/yr for hard-to-borrow names — IBKR publishes daily).
- Calibrate to L6 realized slippage every 30 days; refuse rebalances if realized > 1.5× modeled three days in a row.
**Phase:** L4 (model) + L6 (calibration).

#### O5. Beta-neutrality drift between rebalances — **MEDIUM**
**What goes wrong:** Portfolio is built beta-neutral on Monday. By Friday, longs outperformed shorts by 3% and betas have re-realized; the book is now net 0.15 beta long. The next rebalance is on Monday a week later. Three days of unintended beta exposure.
**Consequences:** P&L attribution shows the fund "made money on beta" but it was unintended; in a downturn, it's the same dynamic in reverse.
**Prevention:**
- Compute live portfolio beta intraday in the dashboard (uses current prices, current weights, rolling 60d betas).
- Add an **interim-rebalance trigger** if |portfolio beta| > 0.20 between scheduled rebalances. Single-trade adjustment to restore neutrality is cheap and avoids drifting into the next rebalance with stale exposure.
- Surface portfolio beta as a top-line metric in the L7 dashboard.
**Phase:** L4 + L7.

---

### Risk-Model Pitfalls (L5)

#### R1. Barra-style on small cross-section vs vendor 80-factor model — **HIGH**
**What goes wrong:** MSCI Barra USE4 has ~10 style factors + ~60 GICS industry factors + market = ~70 factors estimated on the entire US equity universe (~3000 names). A single-operator implementation on the 500-name S&P universe with ~8–12 factors is **structurally** less accurate.
**Consequences:** Specific risk is underestimated (factors absorb less variance, but residuals are noisier than they should be); MCTR (marginal contribution to risk) understates concentration risk.
**Prevention:**
- Be **honest** about model limitations: log explained-variance (R²) of the cross-sectional regression. Per Barra's own methodology, R² should be 0.30–0.50 for a healthy daily cross-sectional regression. If yours is < 0.20, the model is too thin; widen the universe or add factors.
- Apply **Barra-style eigenfactor risk adjustment** (winsorize residuals at 1st/99th percentile; rolling-window variance with half-life ~36–60 days, not equal-weight).
- Inflate specific-variance estimates by 1.2–1.5× to absorb known understatement bias (Barra publishes this adjustment in their methodology notes).
- Document in the LP letter that the in-house risk model is for relative attribution, not absolute risk-budget claims.
**Phase:** L5.
**Real-world cite:** MSCI USE4 Empirical Notes (2011) document the eigenfactor adjustment specifically because optimized portfolios systematically under-realize predicted risk.

#### R2. Specific-variance underestimation — **HIGH**
**What goes wrong:** Cross-sectional regression residuals are squared and treated as σ²_specific. With 120 days × 500 stocks the per-name sample is small; outlier-day shocks (earnings, news) contribute enormously to a few names' σ but get smeared into "specific risk" for everyone.
**Consequences:** A name with one 10% earnings move and 119 days of 1% noise gets σ_specific ≈ 1.5%, but its true conditional risk on event days is 10%. Position size is wrong by an order of magnitude on those days.
**Prevention:**
- Winsorize daily residuals at ±3σ before squaring.
- Add a 30% "haircut" to all specific-variance estimates for v1 (a confessed Bayesian prior toward higher risk).
- Inflate specific variance for any name with an earnings date within 10 days.
**Phase:** L5.

#### R3. Factor-model staleness during regime shifts — **HIGH**
**What goes wrong:** 120-day rolling window assumes the factor exposures and factor-vols are stable. They aren't, around regime shifts (Mar 2020 COVID, Sep 2022 inflation pivot, Aug 2007 quant quake). During shifts, last 120d data both overweights pre-shift volatility and underweights the (much higher) post-shift correlations.
**Consequences:** Risk model says "all clear" while the book is being deleveraged by other quants running similar factors.
**Prevention:**
- Track rolling 5d realized factor returns and 5d cross-sectional factor-return std-dev. If 5d std-dev > 2× rolling 60d std-dev for any factor, flag as regime-shift candidate; halve gross exposure as a precaution.
- Enable Barra's volatility-regime adjustment (cross-sectional bias statistic), updating factor variances upward toward a recent realized volatility blend.
- Use 60d half-life (not flat 120d) so recent days carry more weight.
**Phase:** L5.
**Real-world cite:** Khandani & Lo (2007) "What Happened to the Quants in August 2007?" — densely overlapping factor portfolios from hundreds of L/S funds deleveraged in cascade. Book lost 8–10% in 3 days; recovered most of it in week 2 once forced sellers were done. Risk models that day under-predicted realized vol by 3–5×.

#### R4. Pre-trade veto bypass via "closing trade" misclassification — **CRITICAL**
**What goes wrong:** The spec exempts "closing trades" from absolute veto so you can always exit a bad position. But if classification is sloppy (e.g., a partial reduce flagged as "closing"), an opening-direction veto can be bypassed.
**Consequences:** The "absolute" claim is false; one wrong classification puts the operator into a position that should have been blocked.
**Prevention:**
- Closing-trade definition must be: `abs(new_position) < abs(old_position) AND sign(new_position) == sign(old_position)` AND `abs(trade_qty) <= abs(old_position)`. Any of these failing → not a closing trade.
- Add an explicit `is_closing_trade: bool` audit field on every order with the rule that produced it.
- Unit-test specifically against (a) flipping long-to-short, (b) partial reduce, (c) full close, (d) full close + reverse.
- Periodic cron: re-evaluate classifications on stored audit log against a different implementation; alert on disagreement.
**Phase:** L5 (veto) + L6 (executor).
**Real-world cite:** Knight Capital (Aug 1, 2012) — repurposed flag on the order router triggered legacy code on one of eight servers; no second implementation existed to catch the disagreement; loss was $440M in 45 minutes.

#### R5. Circuit-breaker false positives during normal vol — **MEDIUM**
**What goes wrong:** -1.5% daily / -2.5% daily / -4% weekly thresholds are absolute. On VIX = 30 days, normal noise is ±1%; on VIX = 50 (e.g., Aug 2024 yen-carry unwind, Apr 2025 tariff shock), ±3% is normal. Static thresholds fire on noise.
**Consequences:** Halt-rebalance triggered on a vol spike that's market-wide and diversifiable; system goes flat into the recovery; misses re-entry.
**Prevention:**
- Make thresholds **VIX-adaptive**: scale the -1.5% threshold as `-1.5% × max(1, VIX/20)`. At VIX=20 it's -1.5%; at VIX=40 it's -3%.
- Distinguish single-day from drawdown-from-peak: -1.5% daily fires on a noise day; -8% drawdown is the harder stop.
- Always keep the -8% hard drawdown stop unconditional — it should never adapt; it's the operator-protection floor.
- Log the VIX-at-trigger on every breaker event for postmortem.
**Phase:** L5.

---

### Anthropic / Claude Pitfalls (L3)

#### C1. Prompt-cache invalidation on system-prompt edit — **CRITICAL**
**What goes wrong:** Per Anthropic docs, the cache hierarchy is `tools → system → messages`. Any change at level invalidates that level **and all subsequent levels**. A single whitespace fix in the system prompt invalidates every cached message prefix that depends on it.
**Why it matters here:** L3 fans 4 analyzers × 40 tickers per run. Cache write costs 1.25× input-token rate; cache read is 0.10×. With 3000-token system prompts × 160 calls, blowing the cache is the difference between $3 and $25.
**Warning signs:** Daily run cost > $5 when cache is supposed to be warm; `cache_creation_input_tokens` field in API response is non-zero on the second call onward.
**Prevention:**
- **Freeze system prompts** in versioned files (`prompts/v1/filing_analyzer.txt`). Edits get a new version dir.
- Use `cache_control: {"type": "ephemeral"}` on the system block (5-min TTL by default; consider 1h TTL = `{"type":"ephemeral","ttl":"1h"}` for the daily-run pattern).
- Place cache breakpoints carefully: spec allows up to 4 breakpoints. For Meridian: (1) tools, (2) system, (3) shared per-analyzer preamble, (4) per-ticker context. Top three are stable across 40 tickers in one run; the bottom is the fresh part.
- **Never** include `datetime.now()` or any per-request value in cached blocks.
- **Never** include images conditionally — image presence anywhere in the prompt invalidates cache per Anthropic docs.
**Phase:** L3.
**Source:** Anthropic prompt-caching docs (cache hierarchy + invalidation).

#### C2. Cache-write tokens not counted toward $25 ceiling — **CRITICAL**
**What goes wrong:** Cost tracker only sums `input_tokens` and `output_tokens`. The first request of every cache block writes `cache_creation_input_tokens` at **1.25×** the input rate. If untracked, the budget is silently overrun on the first run after cache expiry.
**Consequences:** $25 ceiling never trips; bill is whatever the API bills.
**Prevention:**
- Cost tracker MUST sum: `input_tokens + (cache_creation_input_tokens × 1.25) + (cache_read_input_tokens × 0.10) + output_tokens × output_rate`.
- Unit-test cost tracker against a known small bill from the Anthropic dashboard before relying on it.
- Persist all four fields per call to SQLite for audit; the dashboard shows daily breakdown.
- Add a **soft warning at $20** so the operator can intervene before the hard abort at $25.
**Phase:** L3.

#### C3. JSON extraction failures on prose-wrapped responses — **MEDIUM**
**What goes wrong:** Claude sometimes wraps JSON in markdown fences (```json ... ```), prose ("Here is the analysis: { ... }"), or trailing commentary. Naive `json.loads(response)` fails.
**Prevention:**
- Use 3 wrap-format extractors as the spec calls for: (1) raw JSON, (2) markdown-fenced, (3) "Here is" prose-prefixed.
- After extraction, validate against a Pydantic schema; on failure, log the raw response and **retry once with a stricter system-prompt suffix** ("Return JSON only. No prose. No markdown fences.") — but **only this retry**, then mark the analysis null.
- Anthropic supports JSON mode / tool use for hard JSON output — consider tool use for any analyzer that can express its output as a function call. Tool use is more reliable than prose-then-extract.
**Phase:** L3.

#### C4. Rate-limit handling: 429 retry vs cost-tracker abort — **HIGH**
**What goes wrong:** A 429 retry loop on a saturated API can rack up cache-creation tokens (each retry can re-cache) and burn the $25 ceiling before completing.
**Prevention:**
- Exponential backoff with jitter on 429: 1s, 2s, 4s, 8s, then abort.
- Cost tracker is checked **before** every retry, not just before the first call.
- Include `cache_creation_input_tokens` for the failed call in the cost tally even if the call errored — Anthropic still bills the cache write on the partial; verify in your account.
**Phase:** L3.

#### C5. Stale analysis cache on regime-shift day — **HIGH**
**What goes wrong:** 30-day TTL on analysis cache is fine on quiet days. On a regime-shift day, yesterday's "STRONG_BUY" recommendation for a stock that just announced bankruptcy is dangerously stale, but the cache hit doesn't re-run the analyzer.
**Prevention:**
- Cache key should include not only `(analyzer, ticker, artifact_id)` but also `recent_news_hash` for analyzers that depend on news/filings.
- Invalidate cache for any ticker with a |1d return| > 10% or a new 8-K filing in the last 24h.
- Soft TTL: hit returns `(result, age_days)`; if age_days > 7 and any major signal-changing event, re-run automatically.
**Phase:** L3.

#### C6. `claude-sonnet-4-5` model ID drift / deprecation — **HIGH**
**What goes wrong:** Anthropic removed Claude Sonnet 4.5 from the Claude web/desktop apps in March 2026 and notified developers in April 2026 of API retirement of older Sonnet/Opus 4 models. Anthropic gives ≥60 days notice for API retirement, but **silent model behavior changes** (point releases under the same ID) can occur without retirement.
**Why it matters:** A backtest of L3 analyzer outputs against a future live run is invalidated if the model ID was upgraded under you.
**Prevention:**
- Pin the **versioned** model ID, not the alias (e.g., the dated revision Anthropic publishes — verify on `platform.claude.com/docs/en/about-claude/models/overview`). The spec's `claude-sonnet-4-5` is correct as a default but should be configurable in `config.yaml`.
- Subscribe to Anthropic's deprecation notices (mailing list) and check `endoflife.date/claude` quarterly.
- Persist the resolved model ID in every analysis-cache row so historical analyses are tagged with which model produced them.
- Add a `--model` CLI flag that overrides config — useful when migrating during a deprecation window.
**Phase:** L3 + cross-cutting.

---

### IBKR / Execution Pitfalls (L6)

#### I1. ib_insync deprecated → migrate to ib_async — **HIGH**
**What goes wrong:** Original `ib_insync` author (Ewald de Wit) passed away in early 2024; library is unmaintained. Continued use means no bug fixes, no IBKR API spec updates, no Python-version compatibility.
**Migration:** `ib_async` (`ib-api-reloaded/ib_async` org, maintained by Matt Stancliff) is the supported replacement. v1.0+ migration is mostly `from ib_insync import ...` → `from ib_async import ...` for basic use; check release notes for any API surface changes.
**Prevention:**
- The spec lists `ib_insync`/TWS — explicitly choose `ib_async` for v1. Update STACK.md.
- Pin to a specific ib_async release; bump deliberately.
- Wrap broker calls in a `broker.py` interface so future migration (e.g., to Client Portal API directly) doesn't require touching execution logic.
**Phase:** L6.

#### I2. Paper-account market data permissioning — **HIGH**
**What goes wrong:** Paper account inherits market-data permissions from the linked live account. Without paid market-data subscriptions, paper trading shows **delayed** quotes (15-min for US equities), and over the API the data may differ from what TWS shows ("on-platform" free data does not cross to "off-platform" API).
**Consequences:** Signal-price slippage capture is meaningless if signal price is 15-min delayed; ADV chunking decisions based on wrong volume; paper-realized slippage is **artificially low** (you're trading against stale quotes).
**Prevention:**
- Subscribe to the **US Securities Snapshot Bundle** ($1.50–$10/month) for paper trading at minimum — gets you NBBO snapshots over the API.
- For exchange-specific streaming: Network A (NYSE), B (ARCA/AMEX), C (NASDAQ) each separate; total ~$30/month for full real-time.
- Document in operator runbook: "if PaperAccount.market_data == DELAYED, slippage numbers are not actionable; live promotion blocks until paid feed installed."
- Add explicit check at L6 startup: query data permission via API; refuse to run `--execute` (paper or live) if not real-time.
**Phase:** L6 + cross-cutting (operator setup).

#### I3. Short-locate failure / borrow recall mid-trade — **HIGH**
**What goes wrong:** A locate granted at order time can be recalled mid-day. In a squeeze (e.g., GameStop Jan 2021), borrow rates can hit -100%/yr overnight and shares can be "called away" — IBKR force-closes the short.
**Consequences:** Forced buy-in at the worst possible price; circuit breakers fire on a position you didn't authorize closing.
**Prevention:**
- Pre-trade: query IBKR's borrow availability and current borrow rate. Refuse new shorts if rate > 25%/yr (configurable threshold) — this excludes most truly hard-to-borrow names.
- Continuously poll borrow status for open shorts; if rate spikes above operator threshold, surface as **immediate review** in dashboard (not auto-cover, but visible).
- Build a "do-not-short" list seeded with names that have hit > 50% borrow rate in the trailing 90d.
- Cap individual short position at 0.5% of NAV for any name with borrow rate > 10%/yr.
**Phase:** L6.
**Real-world cite:** GameStop Jan 2021 — short interest > 100% of float, borrow rate spiked to triple-digit %, shares were being recalled in cascade. Funds that had locates at $20 were force-bought at $300.

#### I4. TWS / Gateway / Client Portal session drops — **HIGH**
**What goes wrong:** IBKR Client Portal Gateway requires reauthentication **at least once after midnight every day**. TWS / Gateway can also drop on network blips, IBKR-side restarts (Saturday weekly maintenance), and Mac sleep.
**Consequences:** 17:15 launchd run finds the gateway logged out; orders queue locally and never submit; or worse, partial submission then disconnect mid-run.
**Prevention:**
- Use **IBC (Interactive Brokers Controller)** or Anthony Garner's auto-login wrappers to handle daily reauth.
- launchd job preflight: HEAD-check `localhost:5000/v1/api/iserver/auth/status`; if not authenticated, attempt re-auth before proceeding; if still failing, abort the run with a red flag (do not proceed to L6 execute).
- On disconnect mid-run: pause for 60s, attempt reconnect 3×, then halt and persist pending-orders state to SQLite for manual review.
- Single session limit per username — never log into Client Portal in a browser while the daily run is executing.
**Phase:** L6.

#### I5. Order-ID collision / off-by-one on `nextValidId` — **MEDIUM**
**What goes wrong:** TWS API returns the next valid order ID at startup. If you persist your own counter and the TWS counter advances out-of-band (manual order in TWS, second client connection), your next order collides.
**Prevention:**
- On every connection, request `nextValidId` from TWS and use **max(persisted_local, tws_provided)** as the starting point.
- Increment locally per order, but **never** persist a value lower than what TWS reports.
- Catch the specific TWS error code for duplicate ID and retry with `current + 1` once.
- ib_async's `client.getReqId()` handles much of this; rely on the library's counter rather than rolling your own.
**Phase:** L6.

#### I6. ADV chunking that increases slippage vs single block — **MEDIUM**
**What goes wrong:** Spec says "ADV chunking" to limit market-impact. But chunking too aggressively (5% of ADV per slice over 20 slices) leaks information across the day; impact can be **worse** than a single 10% block at midday.
**Prevention:**
- Cap chunk count at 5 (not 20). Use VWAP-style schedule (heavier participation in liquid hours, lighter in first/last 30 min).
- Skip chunking entirely for orders < 1% of ADV (small enough to not move the tape).
- For orders > 5% of ADV, prefer to defer to next day rather than chunk into 10+ slices.
- Calibrate against L6 realized slippage: if chunked execution slippage > unchunked baseline, re-tune chunk size.
**Phase:** L6.

---

### Streamlit / Dashboard Pitfalls (L7)

#### S1. `st.cache_data` invalidation on SQLite writes — **HIGH**
**What goes wrong:** Dashboard caches a query result for 1h TTL; CLI run at 17:15 writes new daily data. Operator opens dashboard at 17:30 and sees yesterday's positions because the cache has 30 min of TTL left.
**Prevention:**
- Set `ttl=300` (5 min) on all `@st.cache_data` decorators reading SQLite — matches the 5-min auto-refresh cadence.
- Invalidation hook: have the CLI run write a marker file (`cache/last_write.txt`); dashboard cache key includes `os.path.getmtime(marker_file)` so any write busts the cache.
- For tables that change frequently (positions, orders), prefer no caching; SQLite reads at this scale are sub-millisecond anyway.
- Document the ttl in each cached function's docstring.
**Phase:** L7.

#### S2. 5-min auto-refresh causing duplicate Anthropic calls — **HIGH**
**What goes wrong:** Dashboard does live Claude calls (e.g., for the weekly commentary or JARVIS chat). 5-min auto-refresh re-renders the page, re-runs the function, re-calls Claude. $25 ceiling blown by a forgotten dashboard tab.
**Prevention:**
- Wrap **all** Anthropic calls behind `@st.cache_data(ttl=...)` keyed by the input + a date stamp, so identical calls within a TTL window return cached results.
- Push pre-computed daily-letter and weekly-commentary into SQLite during the 17:15 run; dashboard reads from SQLite, not Anthropic.
- JARVIS chat: explicit user-action only (button press). Never run on auto-refresh.
- Hard guard: dashboard process refuses to make Anthropic calls if today's run-budget (separate from CLI run-budget) exceeds $5.
**Phase:** L7 + L3.

#### S3. JARVIS chat blowing context budget per session — **MEDIUM**
**What goes wrong:** JARVIS chat keeps `messages[]` history per session; each turn sends the full history. Over a 30-turn session, input tokens grow O(n²); cost balloons.
**Prevention:**
- Cap chat history at last 10 turns; older turns summarized into a single context message (cheap one-off summary call).
- Use prompt caching on the chat system prompt (stable across all turns).
- Display running session cost in the chat sidebar; refuse new turn if session cost > $1.
- Persist chat transcripts so the operator can review what cost what.
**Phase:** L7.

#### S4. Streamlit chrome leak through custom CSS — **LOW**
**What goes wrong:** Custom dark theme via injected CSS; Streamlit version bump changes class names; theme breaks on refresh.
**Prevention:**
- Pin Streamlit version in `requirements.txt`.
- Prefer Streamlit 1.30+ native theming via `.streamlit/config.toml` over CSS injection.
- If CSS injection is necessary, scope to data-testid attributes (more stable than class names).
**Phase:** L7.

---

### Operational Pitfalls (cross-cutting)

#### Op1. launchd job silent failure — **HIGH**
**What goes wrong:** launchd plist has wrong path, missing env, or wrong working directory; job exits non-zero; no surface to the operator.
**Prevention:**
- Plist must specify `StandardOutPath` and `StandardErrorPath` to absolute log files (e.g., `/Users/teni/Library/Logs/meridian/run.log`).
- Run script writes a heartbeat file at start (`cache/last_run_started.txt`) and at end (`cache/last_run_completed.txt`) with timestamp + exit code.
- Dashboard reads heartbeats; banner-warns if last completion > 24h old or exit ≠ 0.
- Optional: send a macOS notification (osascript) on failure.
- Test the plist with `launchctl bootstrap` and `launchctl kickstart` before relying on the schedule.
**Phase:** Cross-cutting.

#### Op2. macOS sleep / Power Nap killing the 17:15 job — **HIGH**
**What goes wrong:** Mac is asleep at 17:15. By default, launchd `StartCalendarInterval` runs the job **on next wake** rather than skipping (better than cron) — but Power Nap doesn't run user-level launchd jobs; full wake is required.
**Prevention:**
- Add `WakeSystem = true` to the plist so launchd schedules a system wake at the exact time.
- Or: use `pmset repeat wakeorpoweron MTWRF 17:14:00` to force a wake one minute before.
- Verify with `pmset -g sched` that the wake is scheduled.
- Unconditionally log job-actually-started timestamp to detect if launchd skipped a day.
**Phase:** Cross-cutting (operator setup) + L1 (run-script preflight).

#### Op3. SQLite WAL contention between dashboard reads and CLI writes — **MEDIUM**
**What goes wrong:** Dashboard holds a read transaction open while the CLI run does a long write batch (e.g., insert 500 stock × 24 ratios = 12K rows). With WAL, readers don't block writers, but writer can stall on `SQLITE_BUSY` if a checkpoint is contended; writes via `BEGIN IMMEDIATE` still respect `busy_timeout` in 99% of cases.
**Prevention:**
- `PRAGMA journal_mode=WAL` on every connection.
- `PRAGMA busy_timeout=10000` (10s) on the writer; dashboard uses 5s.
- Writer uses `BEGIN IMMEDIATE` (acquires write lock immediately); avoids contention with deferred-mode readers escalating to writers.
- Batch CLI writes inside one transaction (12K rows / one transaction is fine; 12K transactions of one row is not).
- Run `PRAGMA wal_checkpoint(TRUNCATE)` at end of daily CLI run to keep WAL file small.
**Phase:** L1, L7, cross-cutting.

#### Op4. API key leakage to logs / commits — **HIGH**
**What goes wrong:** Anthropic key in `.env` is fine; but the SDK error path may log the request including auth headers. SQLite cache may store request URLs containing keys. Git commit captures `.env` if `.gitignore` is wrong.
**Prevention:**
- `.gitignore` includes `.env`, `cache/`, `output/`, `*.sqlite*` from day one. **Verify with `git check-ignore -v .env` before first commit.**
- Use `pre-commit` with a secrets scanner (`detect-secrets` or `gitleaks`) on every commit.
- Never log full headers or full request URLs from any SDK; configure Anthropic SDK logger at WARNING+ in production.
- Rotate the key if it's ever in any committed file, including history (use `git filter-repo` to remove).
- Use `Keychain` on macOS for the key (`security add-generic-password`) and load via shell rather than `.env` if paranoid.
**Phase:** Cross-cutting.

#### Op5. Forgetting to gitignore cache/, output/, .env — **HIGH**
**What goes wrong:** First commit pushes `cache/positions.sqlite` to GitHub; PII / strategy details / API keys leak.
**Prevention:**
- `.gitignore` template **before** `git init`:
  ```
  .env
  .env.*
  cache/
  output/
  *.sqlite
  *.sqlite-*
  *.db
  __pycache__/
  *.pyc
  .DS_Store
  .streamlit/secrets.toml
  ```
- Run `git status --ignored` after init; verify cache/output show as ignored.
- Use a private repo for v1 regardless; do not push to public.
**Phase:** Cross-cutting (Phase 0).

---

### Compliance / Discipline Pitfalls (paper→live transition)

#### Disc1. Going live without explicit promotion ceremony — **CRITICAL**
**What goes wrong:** Operator flips an `--execute` flag from paper to live one Friday afternoon. No checklist, no review, no last paper-vs-live attribution comparison.
**Prevention:**
- Encode a **promotion ceremony** as a separate CLI script (`scripts/promote_to_live.py`) that:
  1. Checks paper-trading P&L attribution exists for ≥60 trading days.
  2. Checks Sharpe of paper book ≥ configurable threshold (e.g., 0.5 net-of-costs).
  3. Checks zero veto-bypass events in audit log.
  4. Checks zero stale-cache halts in last 30 days.
  5. Prints a giant banner; requires typing the literal account number to proceed.
  6. Persists a `promotion_record.json` to `output/` with timestamp + git SHA.
- Make the live IBKR account number a separate config key, never the default.
- Initial live position size capped at 25% of intended NAV for first 20 trading days (escalation gates in code).
**Phase:** L6 + cross-cutting (separate milestone).

#### Disc2. LP letter mistaken for real LP communication — **MEDIUM**
**What goes wrong:** "Internal voice" mode is fine; "LP-formal" mode produces a letter that, if forwarded, looks like a real LP communication. There are no LPs. Sending it to anyone outside the operator could read as solicitation (508-related issues, even casual).
**Prevention:**
- Both modes carry a footer: "Internal performance log — Meridian Capital Partners is a single-operator paper-trading system; not an investment fund and not soliciting investors." Mandatory; not togglable.
- Watermark the LP-formal mode with "PAPER" until the promotion ceremony has been recorded.
- Consider a separate filename convention (`internal_letter_*.md`) and never put it in a file path including "investor" or "LP" until/unless that ever changes.
**Phase:** L7.

#### Disc3. Audit-log gaps — veto decisions un-reproducible — **HIGH**
**What goes wrong:** A veto fires; operator wants to investigate why; the inputs (factor scores, risk numbers, prices) at the moment of the decision are no longer available because they were overwritten by the next run.
**Prevention:**
- Every veto, circuit-breaker event, and order rejection writes a **complete snapshot** to `audit/decisions/<run_id>/<event_id>.json`: input prices, input scores, model versions, reasoning rule that triggered, full optimizer output.
- Include the git SHA and the model ID (Claude version) in every audit row.
- Keep audit log indefinitely (never auto-purge); it is the only proof the system worked correctly when it mattered.
- Reproducibility test: weekly cron re-runs a random past audit's decision against current code and confirms identical output. If different (code changed), log the diff for review.
**Phase:** L5 + cross-cutting.

#### Disc4. Backtest vs live-paper divergence not explained pre-flip — **HIGH**
**What goes wrong:** Backtest Sharpe was 1.8. Paper Sharpe over 60 days is 0.6. Operator decides "close enough" and goes live. The gap is real and informative.
**Prevention:**
- The promotion ceremony (Disc1) requires an explicit **divergence analysis document**: what's different between backtest and paper? Sources: survivorship/look-ahead/restated-fundamentals (likely culprits per D1/D2), modeled-vs-realized slippage (O4), market regime difference, factor-decay since backtest period.
- If divergence > 50% of backtest Sharpe and source can't be identified, **block promotion**.
- Encode the analysis as a Jupyter notebook template in `notebooks/promotion_review.ipynb`; the ceremony script refuses to run if the notebook hasn't been executed in the last 7 days.
**Phase:** L7 + cross-cutting (live-promotion milestone).

---

## Pitfalls by Phase

### L1 — Data Infrastructure
| ID | Pitfall | Severity |
|----|---------|----------|
| D1 | Survivorship bias from current-S&P-500 universe | Critical |
| D2 | Look-ahead bias from yfinance restated fundamentals | Critical |
| D3 | Form 4 transaction-code misclassification (parser side) | Critical |
| D4 | 13F 45-day lag treated as current | High |
| D5 | Stale fundamentals during pre-filing window | High |
| D6 | Earnings-date data quality from yfinance | Medium |
| D7 | Corporate-action handling (splits, spinoffs, ticker changes) | High |
| D8 | GICS sector reclassification mid-year | Medium |

### L2 — Scoring Engine
| ID | Pitfall | Severity |
|----|---------|----------|
| D3 | Form 4 misclassification (factor side) | Critical |
| F1 | Sector imbalance after percentile rank | High |
| F2 | Z-score vs percentile mismatch | Medium |
| F3 | Equal-weight factor combination | High |
| F4 | Estimate-revisions degenerate until 90d snapshots | High |
| F5 | Insider sparseness → sector-median dominance | Medium |

### L3 — Claude Analysis
| ID | Pitfall | Severity |
|----|---------|----------|
| C1 | Prompt-cache invalidation on system-prompt edit | Critical |
| C2 | Cache-write tokens not counted in cost ceiling | Critical |
| C3 | JSON extraction failures | Medium |
| C4 | Rate-limit retries inflating cost | High |
| C5 | Stale analysis cache on regime-shift day | High |
| C6 | Model ID drift / deprecation | High |

### L4 — Portfolio Construction
| ID | Pitfall | Severity |
|----|---------|----------|
| O1 | MVO covariance instability without shrinkage | Critical |
| O2 | SLSQP convergence failure | High |
| O3 | Stale-weight fallback semantics | Critical |
| O4 | Optimistic transaction-cost model | High |
| O5 | Beta-neutrality drift between rebalances | Medium |

### L5 — Risk Management
| ID | Pitfall | Severity |
|----|---------|----------|
| R1 | Barra-style on small cross-section | High |
| R2 | Specific-variance underestimation | High |
| R3 | Factor-model staleness during regime shifts | High |
| R4 | Pre-trade veto bypass via closing-trade misclassification | Critical |
| R5 | Circuit-breaker false positives in normal vol | Medium |

### L6 — Execution
| ID | Pitfall | Severity |
|----|---------|----------|
| I1 | ib_insync deprecated → ib_async | High |
| I2 | Paper-account market-data permissioning | High |
| I3 | Short locate failure / borrow recall | High |
| I4 | TWS / Gateway / Client Portal session drops | High |
| I5 | Order-ID collision / off-by-one | Medium |
| I6 | ADV chunking that increases slippage | Medium |
| O4 | Optimistic transaction-cost model (calibration side) | High |

### L7 — Reporting & Dashboard
| ID | Pitfall | Severity |
|----|---------|----------|
| S1 | st.cache_data invalidation on SQLite writes | High |
| S2 | 5-min auto-refresh causing duplicate Anthropic calls | High |
| S3 | JARVIS chat blowing context budget per session | Medium |
| S4 | Streamlit chrome leak through custom CSS | Low |
| Disc2 | LP letter mistaken for real LP communication | Medium |

### Cross-cutting
| ID | Pitfall | Severity |
|----|---------|----------|
| Op1 | launchd job silent failure | High |
| Op2 | macOS sleep / Power Nap killing 17:15 job | High |
| Op3 | SQLite WAL contention | Medium |
| Op4 | API key leakage to logs / commits | High |
| Op5 | Missing .gitignore for cache/output/.env | High |
| Disc1 | Going live without explicit promotion ceremony | Critical |
| Disc3 | Audit-log gaps | High |
| Disc4 | Backtest vs live-paper divergence pre-flip | High |
| C6 | Model ID drift (versioning side) | High |

---

## Severity Index

### Critical (8) — strategy-invalidating or directly money-losing
- D1 Survivorship bias
- D2 Look-ahead bias from yfinance fundamentals
- D3 Form 4 misclassification
- C1 Prompt-cache invalidation
- C2 Cache-write tokens uncounted
- O1 MVO covariance instability
- O3 Stale-weight fallback
- R4 Pre-trade veto bypass via closing-trade misclassification
- Disc1 Live promotion without ceremony

### High (19) — degrade performance enough to require fix before scaling
D4, D5, D7, F1, F3, F4, C4, C5, C6, O2, O4, R1, R2, R3, I1, I2, I3, I4, S1, S2, Op1, Op2, Op4, Op5, Disc3, Disc4

### Medium (10) — material but workable
D6, D8, F2, F5, O5, R5, C3, I5, I6, S3, Op3, Disc2

### Low (1)
S4

---

## Phase-Mapping Heat Map (which phases need deeper research before execution)

| Phase | Critical count | High count | Recommendation |
|-------|----------------|------------|----------------|
| L1 | 3 | 3 | **Research-heavy.** Point-in-time data and Form 4 parsing are silent-correctness risks. Spike before scaling. |
| L2 | 1 | 3 | Standard implementation; the Form 4 leakage is the live wire. |
| L3 | 2 | 4 | **Research-heavy.** Anthropic caching mechanics + cost-tracking are non-negotiable; spike with synthetic load. |
| L4 | 2 | 2 | Ledoit-Wolf shrinkage and explicit fallback paths. Standard quant patterns once decided. |
| L5 | 1 | 3 | **Research-heavy.** Barra-style model is a serious math project; closing-trade definition needs explicit specification. |
| L6 | 0 | 4 | **Operationally-heavy** (vendor / IBKR-specific surface). |
| L7 | 0 | 2 | Standard. Cache-invalidation patterns are the main concern. |
| Cross-cutting | 1 | 6 | Discipline-heavy: audit log + promotion ceremony are the spine. |

---

## Sources

### Anthropic / Caching
- [Prompt caching — Anthropic docs](https://docs.claude.com/en/docs/build-with-claude/prompt-caching) — cache_control, cache hierarchy (tools→system→messages), invalidation rules, cache_creation_input_tokens / cache_read_input_tokens fields. **HIGH confidence.**
- [Models overview — Anthropic API](https://platform.claude.com/docs/en/about-claude/models/overview) — current model IDs.
- [Model deprecations — Anthropic API](https://platform.claude.com/docs/en/about-claude/model-deprecations) — ≥60-day notice policy.
- [Anthropic deprecation commitments](https://www.anthropic.com/research/deprecation-commitments) — model-preservation policy.

### Data
- [yfinance issue: point-in-time / survivorship-bias-free data](https://github.com/ranaroussi/yfinance/discussions/1182) — explicit acknowledgement that yfinance is current-only.
- [SEC ownership form codes](https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html) — authoritative Form 4 transaction-code reference.
- [Form 4 transaction codes (SEC PDF)](https://www.sec.gov/files/forms-3-4-5.pdf) — official SEC investor bulletin.
- [SEC Form 13F FAQ](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f) — 45-day filing rule.
- [The 13F Blind Spot (exponential-tech)](https://www.exponential-tech.ai/post/13f-blind-spot) — alpha decay timing.
- [GICS Communications Services 2018 reshuffle (MSCI)](https://www.msci.com/documents/10199/bbdd3ff9-b66e-975b-d35d-1028d1013837) — sector reclassification reference.
- [Wikipedia — Communication services sector reshuffle](https://en.wikipedia.org/wiki/Communication_services_sector_reshuffle) — narrative summary.

### Optimization / Risk
- [Honey, I Shrunk the Sample Covariance Matrix — Ledoit & Wolf (PDF)](http://www.ledoit.net/honey.pdf) — foundational shrinkage paper.
- [scikit-learn LedoitWolf docs](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.LedoitWolf.html) — implementation reference.
- [scipy.optimize SLSQP docs](https://docs.scipy.org/doc/scipy/reference/optimize.minimize-slsqp.html) — convergence behavior.
- [Improved SLSQP — arXiv 2402.10396](https://arxiv.org/pdf/2402.10396) — known SLSQP convergence-failure modes.
- [MSCI USE4 Methodology](https://www.top1000funds.com/wp-content/uploads/2011/09/USE4_Methodology_Notes_August_2011.pdf) — Barra US Equity Model methodology including eigenfactor and volatility-regime adjustments.
- [Khandani & Lo — What Happened to the Quants in August 2007? (NBER)](https://www.nber.org/system/files/working_papers/w14465/w14465.pdf) — quant-quake mechanism.

### IBKR / Execution
- [ib_async GitHub (replaces ib_insync)](https://github.com/ib-api-reloaded/ib_async) — maintained successor.
- [IBKR Market Data Considerations for Paper Account](https://www.ibkrguides.com/kb/article-1719.htm) — paper-account permissioning.
- [IBKR Market Data Subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/) — Network A/B/C, snapshot bundle.
- [IBKR Client Portal Gateway authentication](https://www.interactivebrokers.com/campus/trading-lessons/launching-and-authenticating-the-gateway/) — daily reauth requirement.
- [TWS API connectivity](https://interactivebrokers.github.io/tws-api/connection.html) — single-session limit, reauth, midnight rollover.
- [TWS API order submission / nextValidId](https://interactivebrokers.github.io/tws-api/order_submission.html) — order-ID protocol.
- [GameStop short squeeze (Wikipedia)](https://en.wikipedia.org/wiki/GameStop_short_squeeze) — borrow recall and forced buy-in mechanics.
- [IBKR Campus — Risks of Shorting: Borrow Fees](https://www.interactivebrokers.com/campus/traders-insight/securities/short-selling/the-risks-of-shorting-series-part-ii-borrow-fees/) — negative rebate / hard-to-borrow rates.

### Streamlit / SQLite
- [Streamlit st.cache_data](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data) — TTL, manual clear, invalidation hooks.
- [Streamlit caching overview](https://docs.streamlit.io/develop/concepts/architecture/caching) — cache_data vs cache_resource semantics.
- [SQLite WAL mode](https://www.sqlite.org/wal.html) — concurrent reader/writer model.
- [SQLite forum — BEGIN IMMEDIATE busy_timeout](https://sqlite.org/forum/info/f75c87afed27840adf594a5ae1b09dacbf190139bd99e45ceac4037ec778a5ec) — known busy_timeout edge case.

### Operational / macOS
- [Apple Developer — launchd jobs scheduled at midnight](https://developer.apple.com/forums/thread/52369) — sleep/wake/missed-job behavior.
- [Scheduling a Cron Job on macOS with Wake Support](https://deniapps.com/blog/scheduling-a-cron-job-on-macos-with-wake-support) — pmset + WakeSystem patterns.

### Real-world incidents
- [Knight Capital incident (Wikipedia)](https://en.wikipedia.org/wiki/Knight_Capital_Group) — Aug 1, 2012, $440M loss in 45 minutes from one un-deployed code path.
- [SEC Charges Knight Capital (SEC press release)](https://www.sec.gov/newsroom/press-releases/2013-222) — official record.
