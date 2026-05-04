# Feature Research — Meridian Capital Partners (`ls_equity_fund`)

**Domain:** Single-operator quantitative long/short US equity hedge fund (paper-first, daily cadence, IBKR)
**Researched:** 2026-05-04
**Confidence:** HIGH on table-stakes (institutional convention is well-documented); MEDIUM on differentiators (LLM-overlay tooling is still maturing); HIGH on anti-features (the operator's exclusions are conventional and correct).

## Executive Summary

The 7-layer spec covers the *spine* of a credible solo quant L/S system — universe → factor scoring → AI overlay → optimization → risk veto → execution → reporting — and aligns tightly with the convention set by the Quantopian-era stack (Zipline / Alphalens / Pyfolio) for research, the Barra-style cross-sectional model for risk, and the QuantConnect / Riskfolio-Lib / `ib_insync` patterns for construction and execution.

The spec hits ~85% of the table-stakes you'd find in an institutional L/S system. Three table-stakes are *implicit* but not *explicit* and should become first-class requirements:

1. **A backtest harness with point-in-time / survivorship-bias controls** — the spec has a paper-trade gate but no historical-replay validation step, which is the standard mechanism for validating a factor model before paper deploy.
2. **A "factor model staleness" / IC monitoring requirement** — alpha decay is the most-cited reason quant strategies fail post-launch; the spec persists factor scores at entry (good) but never specifies a periodic IC / Spearman check on whether scores are still predictive.
3. **An explicit paper→live promotion ceremony with named gates** — the spec mentions "explicit gating ceremony" in Out of Scope but never defines the gates (min N days paper, min Sharpe, max realized slippage vs model, etc.).

The differentiators are unusually strong for a solo system: the L3 Claude qualitative overlay, forensic accounting (Piotroski/Altman in the quality factor), Form 4 P/S/A/M/F decoding, multi-fund 13F crowding flag, MCTR-aware Barra risk model, and dual-mode LP letter all push this above "yet another factor screener." The JARVIS chat over a JSON system snapshot is a genuine novelty — most institutional systems leave the operator to read PDFs.

The anti-feature list is conventional and well-reasoned: deferring real money, transcripts, options/futures, intraday, multi-tenant, and Alpaca short-flags is exactly what a v1 should defer.

## Feature Landscape

### Table Stakes (Users Expect These — Missing = System Feels Broken)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Sector-neutral construction** | Industry-standard for L/S to avoid paying 1× for sector beta dressed as alpha; AQR / Two Sigma / Renaissance all do it | M | Spec covers via GICS percentile rank + 25% sector cap. **Covered.** |
| **Beta-neutral or beta-targeted exposure** | Without this, "market neutral" is marketing | M | Spec covers via 60d rolling beta + portfolio-level long/short/net beta + ~0% net target. **Covered.** |
| **Hard pre-trade risk checks** | An override-able veto is no veto; institutional risk = absolute | M | Spec covers via 8-check absolute veto, closing-trade-only exemption, no override flag. **Covered.** Strong. |
| **Position-sizing discipline (single-name cap, ADV cap)** | Concentration kills books; ADV cap prevents own-impact slippage | S | Spec covers via 5% position cap + 3% NAV breaker + ADV chunking in execution. **Covered.** |
| **Sector concentration cap** | Sector blow-ups (energy 2014, biotech 2016) wipe specialist funds | S | Spec covers via 25% sector cap. **Covered.** |
| **Slippage / transaction-cost tracking** | Backtest costs must match live costs or model is fiction | M | Spec covers via cost model (commission + spread + impact bps) + slippage tracker (rolling, p95, worst-5). **Covered.** |
| **P&L attribution beyond total return** | "Did I pick well or did I just ride beta?" — non-negotiable for a real allocator | L | Spec covers beta/sector/factor/alpha decomposition. **Covered.** Strong. |
| **Earnings-blackout handling** | Holding through unknown earnings is gambling, not investing | S | Spec covers via earnings calendar + earnings-halving in conviction-tilt. **Partial.** Gap: spec doesn't define a hard "no new entry within N days of earnings" rule, only a position-halving rule. Recommend an explicit absolute-veto check `earnings_within_3d` for *new* entries. |
| **FOMC / macro-event awareness** | Same logic as earnings; positioning into known macro events is uncompensated risk | S | Spec covers via live Federal Reserve calendar + rebalance schedule advisory. **Covered.** |
| **Circuit breakers / drawdown stops** | A book that can't stop itself is a book that *will* keep losing | S | Spec covers via -1.5%/d, -2.5%/d, -4%/wk, -8% DD. **Covered.** Strong. |
| **Audit trail of every order + veto + breaker event** | Required posture for any "live-ready" claim, and standard SEC expectation if ever scaled | M | Spec covers in cross-cutting constraints. **Covered.** |
| **Persistence of factor scores at entry** | Required to do *predictive-power* studies later (Spearman of entry score vs realized return) | S | Spec covers via predictive-power Spearman in L7 position attribution. **Covered.** |
| **Reproducible config (no hardcoded magic)** | Re-running yesterday's allocation must yield yesterday's result | S | Spec covers via `config.yaml` + live FOMC feed + configurable universes. **Covered.** |
| **Dry-run mode separate from execute** | The single most common cause of "I just sent a $1M order I didn't mean to" stories | S | Spec covers via `--dry-run` and `--execute` entrypoints. **Covered.** Strong. |
| **Tear sheet with Sharpe / Sortino / Calmar / max-DD** | Pyfolio-equivalent metrics are the lingua franca; a tear sheet without these isn't institutional | M | Spec covers via L7 tear sheet, but the *named* metrics aren't enumerated. Recommend explicitly listing: Sharpe, Sortino, Calmar, max-DD, hit rate, profit factor, beta, alpha, R², skew, kurtosis, tail ratio. |
| **Backtest / historical-replay validation** | Quantopian / Zipline / `vectorbt` etc. all assume backtest-before-paper; "paper is the backtest" is *not* the institutional convention. >90% of academic strategies fail live; this is what catches them. | **L** | **GAP.** Spec has paper-trade as the validation step but no historical replay over 3+ years of point-in-time data. Recommend: minimal Zipline-style or hand-rolled walk-forward harness over the 3y OHLCV that L1 already ingests, even if used only quarterly to recheck the score-engine. |
| **Point-in-time / survivorship-bias-aware data** | Including only stocks that survived inflates returns 1–4%/yr; this is the canonical backtest trap | M | **PARTIAL GAP.** Spec mentions "scanner_seed" for tradable universe but doesn't say how delisted names are retained in history. Recommend: explicit requirement that the universe table store inclusion-window per ticker (`first_seen_date`, `delisted_date`) so backtests can reconstruct the universe as it was. |
| **Factor IC / staleness monitoring** | Alpha decays; equities show ~60% decay before stabilizing per signal-decay literature; without monitoring you can't know your model is dead | M | **GAP.** Spec persists factor scores at entry but never specifies a *periodic* check (rolling IC, rank correlation of score-quintile vs forward return). Recommend: monthly IC report by factor, with auto-flag if any factor's 6m rolling IC < 0.02 or sign-flips. |
| **Paper→live promotion gate (named criteria)** | Without this, "paper-first" is a feeling not a process | S | **PARTIAL GAP.** Out of Scope mentions "explicit gating ceremony" but the gates aren't defined. Recommend an explicit checklist requirement: e.g., 60+ trading days paper, realized Sharpe > 0.8, slippage_realized vs slippage_modeled within 50%, max_DD ≤ -8% breaker not triggered, circuit-breaker count = 0. |
| **Borrow availability / locate check** | Naked shorts that can't be borrowed get bought-in; cost of being wrong is high | M | Spec covers via IBKR-native borrow check (correctly replacing Alpaca flags). **Covered.** |
| **Order-state lifecycle tracking** | Without it you don't know if a partial fill needs a follow-up or a cancel | S | Spec covers via order manager with full lifecycle states + clean SIGINT shutdown. **Covered.** |
| **Hard cost ceiling on external APIs** | Without this, a runaway loop empties your Anthropic credits overnight | S | Spec covers via $25 per-run hard cap with abort. **Covered.** Strong. |

### Differentiators (Lift From "Factor Screener" to "Credible Solo Quant")

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **LLM qualitative overlay on filings (L3 Claude analyzers)** | Most solo quants stop at numeric factors; reading 10-K Risk Factors / 10-Q diffs / Form 4 narrative is what fundamental analysts charge $$$ for — Claude does it for cents-per-ticker with prompt caching | **L** | Strong differentiator. Cache + cost ceiling are essential supports. The 60/40 quant/Claude blend with no-Claude-fallback is a smart resilience pattern. |
| **Forensic accounting checks (Piotroski F, Altman Z, accruals)** | Piotroski's original 1976–96 study showed +23%/yr long-winners / short-losers; Altman ~94% bankruptcy-prediction accuracy 1y out. Standard quality-factor instrumentation — but rarely all three together in solo systems | M | In Quality factor. **Covered.** Consider adding Beneish M-score (earnings-manipulation detection) as a future enhancement — same data sources. |
| **Form 4 P/S/A/M/F decoding with cluster + CEO/CFO weighting** | Most retail screeners only show "insiders bought N shares"; distinguishing **P** (open-market purchase, the only signal that matters) from **A** (award) / **M** (option exercise) / **F** (tax withholding) is the difference between signal and noise. CEO/CFO 3× weight is a known intensifier. | M | Spec covers via L1 Form 4 parser + L2 insider factor + L3 insider analyzer. **Covered.** Strong. |
| **13F multi-fund-opening flag (crowding-aware)** | Mid-Hudson / Whale Wisdom / SumZero retail tools surface 13Fs but rarely cross-check "N tracked funds opening simultaneously" — the *consensus* signal that academic work shows produces +3.8%/yr | M | Spec covers via L1 13F ingestion + L2 institutional flow factor with multi-fund-opening flag. **Covered.** Strong. Be aware: 13F is 45-day delayed, so this is a slow signal — useful for risk-on confirmation, not entry timing. |
| **Live FOMC / earnings calendar (not hardcoded)** | Hardcoded macro dates rot annually; a self-updating Federal Reserve feed is the durable answer | S | Spec covers. **Covered.** |
| **Cross-sectional Barra-style factor risk model with MCTR** | Marginal contribution to risk lets you say "this trade adds 12bp to portfolio vol" instead of "this trade is 5% of NAV" — the language allocators speak | **L** | Spec covers via L5 120d Barra-style model feeding L4 MVO covariance. **Covered.** Strong differentiator vs typical solo systems that use a diagonal covariance. |
| **Dual optimizer (MVO + conviction-tilt fallback)** | MVO chokes on degenerate covariances; without a fallback the system stops on the worst day. Conviction-tilt as ground-truth-debugger for MVO is institutional-savvy | M | Spec covers. **Covered.** Strong. |
| **Institutional-format markdown tear sheet** | Pyfolio-equivalent in markdown — not Jupyter — is a real DX win for daily ops | M | Spec covers in L7. **Covered.** |
| **Daily LP-or-internal letter (dual-mode)** | Forces operator to articulate *why* daily, which is the discipline that catches drift before P&L does. Even without LPs, the formal voice is a useful self-audit | S | Spec covers. **Covered.** Genuine differentiator — most solo systems skip this. |
| **JARVIS chat over JSON system snapshot** | Operator can ask "why did we exit XYZ" and get a synthesized answer instead of grep-ing logs. Genuinely novel for a solo quant system | M | Spec covers. **Covered.** Strong differentiator. |
| **6 Roman-numeral dashboard pages with 5-min auto-refresh** | A daily-cadence system doesn't need real-time, but it does need glanceable. Roman numerals + dark-theme tokens is brand discipline most solo systems skip | M | Spec covers via Streamlit on `localhost:8502`. **Covered.** |
| **Per-candidate markdown research reports** | Replaces "look at the Excel screen" with "read the report" — repeatable, archivable, postmortem-able | M | Spec covers in L3. **Covered.** |
| **Configurable jurisdiction tax model** | Most retail systems assume US LTCG/STCG forever; making it configurable signals "this is built to last" | S | Spec covers in L7 turnover analytics. **Covered.** |
| **Analysis cache with 30-day TTL keyed by artifact_id** | Re-running yesterday is free instead of $25 — load-bearing for cost ceiling | M | Spec covers in L3. **Covered.** Strong. |

### Anti-Features (Correctly Excluded by Spec)

| Feature | Why It Seems Good | Why Problematic | Spec's Alternative |
|---------|-------------------|-----------------|--------------------|
| **Real-money live trading at v1** | "If we built it, ship it" | Doubles risk surface; cannot validate the loop's silent failures (stale data, optimizer non-convergence, broker rejections) without the freedom of paper | Paper-first IBKR with explicit promotion gate later. **Correct.** |
| **Real LP / external investors at v1** | Letter pipeline is built; might as well have readers | Dragged-in compliance (KYC, 506(b)/(c), Form ADV, custody) is multi-quarter work that has nothing to do with whether the strategy is good | Dual-mode letter — formal voice OR internal voice; no LP infrastructure. **Correct.** |
| **Earnings-call transcript pipeline at v1** | Transcripts are alpha-rich and Claude can read them well | Requires paid provider (FactSet/AlphaSense/Q4) + entitlement issues + ingestion latency. Marginal alpha vs 10-K + 10-Q already covered | Earnings analyzer ships as **stub** returning None; clean re-entry point later. **Correct.** |
| **Paid market data (Polygon / Tiingo / IEX) at v1** | yfinance is unreliable; "real" quants use paid feeds | Paid feeds are $1k+/mo; yfinance is good-enough at daily-bar cadence with caching | Free feeds (yfinance + EDGAR) behind an interface seam for paid swap-in. **Correct.** |
| **Options / futures / FX / crypto** | Hedging, leverage, lower-margin shorting | Each adds a complete data pipeline + risk model + execution path; none are needed for L/S equity | Common stock only. **Correct.** |
| **Intraday / HFT** | "More signal more alpha" | Daily-bar factor models do not survive intraday — different alpha, different infra (co-location, FIX, microseconds) | Daily-bar cadence; rebalance is event-driven not intraday. **Correct.** |
| **Multi-tenant / web-hosted SaaS** | Could share with friends; could become a product | Auth, multi-user data isolation, GDPR, hosting costs, support burden — all unrelated to the strategy | Localhost Streamlit, no auth, no remote. **Correct.** |
| **Alpaca short-availability flags** | Already in some retail tooling | Alpaca's borrow data is for Alpaca's borrow, not IBKR's. Wrong broker-of-record = false confidence | IBKR-native borrow check. **Correct.** |
| **Hardcoded FOMC dates / sector list** | Simpler initially | Rots annually (FOMC dates change every year; GICS reclassifies periodically) | Live Federal Reserve feed + configurable lists. **Correct.** |
| **Override flag on pre-trade veto** | "I know better today" | The exact moment you "know better" is the moment you blow up; institutional risk discipline is non-overridable for a reason | Closing-trade-only exemption is the only carve-out. **Correct.** Strong. |
| **Telemetry / external reporting** | Nice for debugging | Privacy violation by default, no upside in solo-operator context | No telemetry, all data local. **Correct.** |

## Feature Dependencies

```
Universe (L1)
    └──required-by──> Sector-percentile rank (L2)
                          └──required-by──> Sector-neutral construction (L4)
                                                └──required-by──> Sector-relative attribution (L7)

Sector mapping (GICS)
    └──required-by──> Barra factor risk model (L5)
                          └──required-by──> MCTR / risk decomposition (L7)
                          └──feeds-covariance-to──> MVO optimizer (L4)

OHLCV history (L1)
    └──required-by──> Rolling 60d beta (L4)
    └──required-by──> Momentum factors (L2)
    └──required-by──> [GAP: Backtest harness — proposed]

Form 4 parser (L1)
    └──required-by──> Insider factor (L2)
    └──required-by──> Insider Claude analyzer (L3)

13F ingestion (L1)
    └──required-by──> Institutional flow factor (L2) with multi-fund-opening flag

Earnings calendar (L1)
    └──required-by──> Earnings-halving in conviction-tilt (L4)
    └──required-by──> [GAP: Hard earnings-blackout veto for new entries — proposed]

Live FOMC calendar (L1)
    └──required-by──> Rebalance schedule advisory (L4)

Cost model (L4)
    └──required-by──> MVO cost-net expected returns (L4)
    └──compared-against──> Slippage tracker (L6)
                              └──required-by──> [GAP: Paper→live gate — proposed]

Pre-trade veto (L5)
    └──required-by──> Order executor (L6)
    └──required-by──> Audit trail (cross-cutting)

Anthropic prompt caching (L3)
    └──required-by──> $25 cost ceiling (L3) — LOAD-BEARING

Analysis cache (L3)
    └──required-by──> Re-runnable daily refresh

Factor scores at entry (L7 attribution)
    └──required-by──> Predictive-power Spearman (L7)
    └──required-by──> [GAP: Factor IC / staleness monitor — proposed]

JSON system snapshot
    └──required-by──> JARVIS chat (L7)
    └──required-by──> Daily letter (L7)
```

### Key Dependency Notes

- **Sector-neutral construction is the join key for everything downstream.** GICS sector tagging in L1 is a prerequisite for L2 percentile ranks, L4 sector caps, L5 risk model, and L7 sector-relative alpha. Get it wrong once and everything is contaminated.
- **MVO requires a working covariance from L5.** This is why the conviction-tilt fallback is mandatory, not optional — on day 1, before L5 has 120 days of returns, MVO has nothing to chew on.
- **Prompt caching is load-bearing for the cost ceiling.** Without it, $25/run is hit on day 1. This is correctly called out in PROJECT.md.
- **L1 OHLCV history is the foundation of a future backtest harness.** The 3y window the spec already requires is sufficient — the backtest gap is software, not data.
- **Audit trail crosses every layer.** Each of veto rejection, breaker trigger, order state change, optimizer fallback, Claude cache miss must persist with timestamp + reason — this is what enables postmortems and the paper→live gate.

## MVP Definition

### Launch With (v1) — must-haves to ship the spine

The 7-layer spec already defines v1 well. Adding the gaps surfaced above:

- [ ] **All Layer 1–7 active requirements as-specified** (universe, scoring, Claude analyzers, MVO + conviction-tilt, Barra risk + 8-veto + breakers, IBKR paper, tear sheet + dashboard + letter)
- [ ] **Earnings-blackout absolute-veto check for new entries** (proposed gap fix) — currently only halves position; should be hard-veto for *new* entries within N=3 trading days of earnings
- [ ] **PIT-aware universe table with `first_seen_date` / `delisted_date` per ticker** (proposed gap fix) — required to make any future backtest non-fictional, cheap to add now and prohibitively expensive to retrofit
- [ ] **Named tear-sheet metric set** (proposed gap fix) — explicitly enumerate Sharpe, Sortino, Calmar, max-DD, hit rate, profit factor, beta, alpha, R², skew, kurtosis, tail ratio in the L7 requirement
- [ ] **Paper→live promotion gate with named criteria** (proposed gap fix) — make the "explicit gating ceremony" Out-of-Scope-bullet a v1 *artifact* (a checklist file) even if execution against it is a later milestone

### Add After v1 Validation (v1.x) — once paper performance is real

- [ ] **Backtest / walk-forward harness** over the L1-ingested 3y OHLCV — minimal Zipline-style event loop with PIT universe and the L2 score engine reused. Trigger: first 30 days of paper trading reveal the score engine ships well-typed data.
- [ ] **Factor IC / staleness monitor** — monthly rolling-IC report per factor with auto-flag on degradation. Trigger: 90+ days of factor-score-at-entry persisted in L7 attribution.
- [ ] **Beneish M-score** added to Quality factor — same data sources as Piotroski/Altman. Trigger: forensic accounting screen producing useful short candidates.
- [ ] **Earnings-call transcript analyzer** — un-stub L3 once a transcript provider is selected. Trigger: paid provider decision (or open-source corpus available).

### Future Consideration (v2+) — only if PMF / live-money is reached

- [ ] **Real-money live trading** — gated behind paper→live ceremony and a separate live-readiness review milestone
- [ ] **Paid market data swap-in** (Polygon / Tiingo / IEX) — interface seams already present; trigger is a yfinance reliability incident or scaling beyond ~3000-name universe
- [ ] **LP infrastructure** — KYC, 506(b)/(c), Form ADV, custody, audited NAV. Only if external capital is sought.
- [ ] **Options overlay for tail-risk hedging** — collars, put protection on long book. Only after equity-only L/S is producing stable Sharpe.
- [ ] **Multi-strategy support** — additional sleeves (mean-reversion, pairs, event-driven) inside the same risk and execution infrastructure

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| 8-factor sector-percentile scoring engine | HIGH | HIGH | P1 |
| 8-check absolute-veto risk layer | HIGH | MEDIUM | P1 |
| MVO + conviction-tilt fallback | HIGH | HIGH | P1 |
| Barra-style factor risk model (120d) | HIGH | HIGH | P1 |
| IBKR paper executor + ADV chunking | HIGH | HIGH | P1 |
| Anthropic Claude analyzers (4) with caching + cost ceiling | HIGH | HIGH | P1 |
| 4-component P&L attribution (beta/sector/factor/alpha) | HIGH | HIGH | P1 |
| Streamlit 6-page dashboard with JARVIS chat | HIGH | MEDIUM | P1 |
| Daily LP-or-internal letter | MEDIUM | LOW | P1 |
| Form 4 P/S/A/M/F insider parser | HIGH | MEDIUM | P1 |
| 13F multi-fund-opening flag | MEDIUM | MEDIUM | P1 |
| Live FOMC calendar feed | MEDIUM | LOW | P1 |
| `--dry-run` / `--execute` separation | HIGH | LOW | P1 |
| Audit trail (orders, vetoes, breakers) | HIGH | MEDIUM | P1 |
| Slippage tracker (rolling, p95, worst-5) | HIGH | MEDIUM | P1 |
| Earnings-blackout HARD veto for new entries (gap) | HIGH | LOW | **P1 (proposed addition)** |
| PIT universe `first_seen_date` / `delisted_date` (gap) | HIGH | LOW | **P1 (proposed addition)** |
| Named tear-sheet metric set (gap) | MEDIUM | LOW | **P1 (proposed addition)** |
| Paper→live promotion criteria checklist (gap) | HIGH | LOW | **P1 (proposed addition)** |
| Backtest / walk-forward harness | HIGH | HIGH | P2 |
| Factor IC / staleness monitor | HIGH | MEDIUM | P2 |
| Beneish M-score addition | LOW | LOW | P3 |
| Earnings-call transcript un-stub | MEDIUM | HIGH | P3 |
| Real-money live trading | HIGH | MEDIUM | P3 (gated) |

**Priority key:**
- P1: Must have for v1 launch (paper-trading-ready)
- P2: Should have, add when first paper validation cycle completes
- P3: Defer until product is proven and a clear trigger is met

## Reference / Comparator Analysis

| Feature | Quantopian / Zipline + Pyfolio + Alphalens | Riskfolio-Lib | Typical retail (HedgeFollow / WhaleWisdom) | Spec's Approach |
|---------|---------------------------------------------|---------------|-------------------------------------------|-----------------|
| Cross-sectional factor library | Pipeline API, hundreds of community factors | n/a (optimizer-only) | None | Custom 8 × 27 sub-factors with sector-percentile |
| Risk model | Empyrical + custom; community Barra-clones | 24 convex risk measures incl. variance, MAD, GMD, CVaR-Range; factor-risk-contribution constraints | None | Barra-style 120d cross-sectional + MCTR |
| Optimizer | scipy / cvxpy via Pipeline | CVXPY-backed mean-risk + Kelly + worst-case MV; turnover & tracking-error constraints | None | scipy SLSQP MVO + custom conviction-tilt fallback |
| Backtest engine | Zipline (Pipeline + event loop) | n/a | None | **Gap — proposed P2** |
| Tear sheet | Pyfolio (Sharpe/Sortino/Calmar/max-DD/skew/kurtosis/tail/alpha/beta) | n/a | Basic charting | Custom institutional-format markdown — recommend explicit Pyfolio-equivalent metric list |
| Factor IC / decay | Alphalens (returns, IC, turnover, grouped) | n/a | None | **Gap — proposed P2** |
| LLM qualitative overlay | None | None | None | L3 Claude analyzers — **genuine differentiator** |
| Insider transaction decoding | None | n/a | Cluster flag, no P/S/A/M/F decode | L1 Form 4 parser with code semantics — **strong** |
| 13F crowding | None | n/a | List-based, no opening flag | Multi-fund-opening flag — **strong** |
| LP-style daily letter | None | n/a | None | Dual-mode letter — **strong differentiator** |
| Conversational interface | Notebook | n/a | None | JARVIS chat over JSON snapshot — **novel** |

## Gap Analysis — Proposed New Requirements

| # | Proposed Requirement | Layer | Severity | Cost | Why It Matters |
|---|----------------------|-------|----------|------|----------------|
| G1 | Hard earnings-blackout veto on *new* entries within N=3 trading days of earnings | L5 (add 9th absolute-veto check) | HIGH | S | Current spec only halves position size into earnings — does not block new initiations. Holding through unknown earnings is the canonical uncompensated risk; the spec already disallows leverage but allows event exposure. Closing trades remain exempt as per existing veto pattern. |
| G2 | PIT universe table with per-ticker `first_seen_date`, `delisted_date`, and `inclusion_window` semantics | L1 (extend universe builder) | HIGH | S | Adding now is cheap; retrofitting is impossible because deletions destroy history. Required dependency for any future backtest. Survivorship bias inflates Sharpe by 1–4%/yr — enough to invalidate a paper→live decision. |
| G3 | Named tear-sheet metric set explicitly enumerated in L7 requirement | L7 | MEDIUM | S | "Institutional tear sheet" is too vague; pin it to the Pyfolio-equivalent set (Sharpe, Sortino, Calmar, max-DD, hit rate, profit factor, beta, alpha, R², skew, kurtosis, tail ratio) so downstream phases have an objective definition-of-done. |
| G4 | Paper→live promotion gate with named numeric criteria | Cross-cutting (companion doc / `promotion_gate.yaml`) | HIGH | S | Out-of-Scope mentions "explicit gating ceremony" but never defines gates. Suggested gates: 60+ trading days paper, realized Sharpe ≥ 0.8, max DD not breaching -8% breaker, realized vs modeled slippage within 50%, zero unexplained circuit-breaker triggers in trailing 30d, all 4 attribution components computable. Without numeric gates, "ceremony" is feel-based. |
| G5 | Backtest / walk-forward harness reusing L2 score engine over L1's 3y OHLCV with PIT universe (G2) | New layer or L4 extension | MEDIUM | L | Defer to v1.x once spine is alive. But the harness should be a stated future requirement, not silently absent. |
| G6 | Factor IC / staleness monitor (rolling 6m IC per factor with auto-flag on degradation) | L7 | MEDIUM | M | Defer to v1.x. Required to detect alpha decay before P&L does. |
| G7 | Optimizer-non-convergence audit log entry | L4 (extend audit trail) | LOW | XS | Spec mandates conviction-tilt fallback on MVO non-convergence; should require that *each fallback event* writes to audit log with reason (singular covariance, unbounded direction, etc.) to spot deteriorating model conditions. |
| G8 | Borrow-rate / hard-to-borrow cost capture for short positions | L6 | MEDIUM | S | IBKR exposes borrow rate per ticker; capturing it lets cost model in L4 reflect actual short cost (HTB names can cost 5-50%/yr) instead of assumed commission+impact only. Otherwise short-side cost model is fictional. |

## Coverage Verdict

**At-risk table-stakes (must address before v1 closes):**
- **Earnings-blackout for new entries** (G1) — present-but-soft is materially weaker than veto; one bad surprise wipes a quarter of edge
- **PIT-aware universe** (G2) — cheap now, impossible later; without it the eventual backtest harness will be fictional
- **Paper→live gate definition** (G4) — without named gates, the "explicit gating ceremony" promise is unfulfilled

**Well-positioned differentiators (lean into these):**
- L3 Claude qualitative overlay with prompt caching + analysis cache + cost ceiling — the architectural plumbing around the LLM is more sophisticated than the LLM use itself, which is the right way around
- Form 4 P/S/A/M/F decoding — most retail tooling never decodes this
- Multi-fund 13F opening flag — captures the consensus-conviction signal academic work shows produces real alpha
- Barra-style 120d risk model with MCTR feeding MVO covariance — institutional-grade for a solo system
- Dual-mode LP-or-internal letter — forces daily articulation, which is the discipline most solo systems lack
- JARVIS chat over JSON snapshot — genuinely novel interface for a solo quant operator
- MVO + conviction-tilt fallback as a *required pair* — ensures the system never stops on a bad covariance day

**Confirmed anti-features (correctly excluded, do not relitigate):**
- Real-money v1, real LPs, transcripts, paid market data, options/futures/FX/crypto, intraday/HFT, multi-tenant SaaS, Alpaca short flags, hardcoded FOMC dates, override flag on veto. Each exclusion has a sound reason and a clean re-entry seam.

**Gaps deserving dedicated requirements (in priority order):**
1. **G1 — Earnings-blackout absolute veto** (P1, S, hardens the risk layer)
2. **G2 — PIT universe table** (P1, S, prerequisite for any honest backtest)
3. **G4 — Paper→live promotion criteria** (P1, S, fulfills the "explicit gating ceremony" promise)
4. **G3 — Named tear-sheet metric set** (P1, S, pins the L7 acceptance criteria)
5. **G8 — Borrow-rate capture** (P1, S, makes short-side cost model real)
6. **G7 — Optimizer-fallback audit entry** (P1, XS, drop into existing audit trail)
7. **G5 — Backtest harness** (P2, L, post-paper-validation)
8. **G6 — Factor IC monitor** (P2, M, post-paper-validation)

**Bottom line:** the spec is unusually complete for a solo quant system. Adding G1, G2, G3, G4, G7, G8 — all S/XS-cost — closes the table-stakes risk. G5 and G6 can defer to v1.x without compromising v1 credibility. The differentiator stack (Claude overlay, Form 4 semantics, 13F crowding, MCTR risk, dual-mode letter, JARVIS chat) is well above what's typical for a single-operator system and should be preserved as-specified.

## Sources

- [AQR — Building a Better Long-Short Equity Portfolio](https://www.aqr.com/-/media/AQR/Documents/Insights/White-Papers/AQR-Building-a-Better-Long-Short-Equity-Portfolio.pdf)
- [AQR — Key Design Choices in Long/Short Equity (Alternative Thinking)](https://www.aqr.com/-/media/AQR/Documents/Alternative-Thinking/AQR-Alternative-Thinking---Key-Design-Choices-in-Long-Short-Equity.pdf)
- [HFR Hedge Fund Strategy Classification System](https://www.hfr.com/hfr-indices/hfr-hedge-fund-strategy-classifications/)
- [Long/short equity — Wikipedia](https://en.wikipedia.org/wiki/Long/short_equity)
- [Quantopian Alphalens — performance analysis of predictive alpha factors (GitHub)](https://github.com/quantopian/alphalens)
- [Quantopian Zipline — Pythonic algorithmic trading library (GitHub)](https://github.com/quantopian/zipline)
- [Pyfolio — full tear sheet example](https://github.com/quantopian/pyfolio/blob/master/pyfolio/examples/full_tear_sheet_example.ipynb)
- [Pyfolio timeseries metrics source](https://github.com/quantopian/pyfolio/blob/master/pyfolio/timeseries.py)
- [Riskfolio-Lib portfolio optimization documentation](https://riskfolio-lib.readthedocs.io/en/latest/portfolio.html)
- [Riskfolio-Lib (GitHub)](https://github.com/dcajasn/Riskfolio-Lib)
- [MSCI Barra US Equity v3 Risk Model Handbook (E3)](https://www.alacra.com/alacra/help/barra_handbook_US.pdf)
- [MSCI Barra Global Equity Risk Model Handbook](https://www.alacra.com/alacra/help/barra_handbook_gem.pdf)
- [Marginal Contribution to Risk — Breaking Down Finance](https://breakingdownfinance.com/finance-topics/modern-portfolio-theory/marginal-contribution-to-risk-mctr/)
- [MCTR for long/short portfolios — Everysk Support](https://support.everysk.com/hc/en-us/articles/360003521754-MCTR-for-long-short-portfolios)
- [Barra Multiple Factor Risk Model — Portfolio Variance and MCTR (DeepWiki)](https://deepwiki.com/hansihuang2016/Barra-Multiple-factor-risk-model/5.3-portfolio-variance-and-mctr)
- [Piotroski F-Score — Wikipedia](https://en.wikipedia.org/wiki/Piotroski_F-score)
- [Piotroski F-Score — Equities Lab](https://www.equitieslab.com/piotroski-f-score/)
- [Alpha Architect — Improving the Piotroski F-Score Measure](https://alphaarchitect.com/value-investing-factor-research-how-to-improve-the-piotroski-f-score-measure/)
- [Walk-Forward Optimization — QuantInsti](https://blog.quantinsti.com/walk-forward-optimization-introduction/)
- [Backtest Series — Cross-Validation Techniques (BSIC Bocconi)](https://bsic.it/backtesting-series-episode-2-cross-validation-techniques/)
- [Refinitiv — Using point-in-time data to avoid backtest bias](https://www.refinitiv.com/perspectives/future-of-investing-trading/how-to-use-point-in-time-data-to-avoid-bias-in-backtesting/)
- [Survivorship Bias in Backtesting Explained — LuxAlgo](https://www.luxalgo.com/blog/survivorship-bias-in-backtesting-explained/)
- [CFA — Problems in Backtesting and Biases in Data](https://analystprep.com/study-notes/cfa-level-2/problems-in-backtesting/)
- [Alpha Decay literature — MicroAlphas Signal Decay Patterns](https://microalphas.com/signal-decay-patterns/)
- [AlphaAgent — LLM-Driven Alpha Mining with Regularized Exploration to Counteract Alpha Decay (arXiv)](https://arxiv.org/html/2502.16789v2)
- [Determinants of Insider Trading Windows — Harvard Corporate Governance](https://corpgov.law.harvard.edu/2021/06/02/determinants-of-insider-trading-windows/)
- [Systematic 13F Hedge Fund Alpha (Lancaster / Barclays)](https://wp.lancs.ac.uk/fofi2020/files/2020/04/FoFI-2020-090-Farouk-Jivraj.pdf)
- [Systematic 13F Hedge Fund Alpha — SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3459526)
- [Awesome Systematic Trading (curated list)](https://github.com/paperswithbacktest/awesome-systematic-trading)

---
*Feature research for: single-operator quantitative L/S US equity system (Meridian Capital Partners / `ls_equity_fund`)*
*Researched: 2026-05-04*
