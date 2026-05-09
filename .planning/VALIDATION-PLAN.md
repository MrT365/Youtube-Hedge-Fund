# Validation Plan — Meridian Capital Partners

**Created:** 2026-05-06
**Owner:** single operator
**Purpose:** the deliberate ramp from "code is built" → "real money deployed", with explicit kill criteria at every gate so the project gets stopped quickly if the strategy doesn't work, instead of slowly bleeding money.

---

## What strategy is baked in (confirmed against the code)

This system runs **one specific strategy**:

> **Sector-neutral, market-neutral L/S US equity at daily cadence, 20 longs / 20 shorts, sized by an 8-factor composite score blended 60% quantitative / 40% Claude-qualitative, ranked within GICS sector, executed via IBKR with hard pre-trade vetos and 5 named circuit breakers.**

### The 8 quant factors (verified in `src/ls_equity_fund/factors/composer.py`)

1. **momentum** — price-trend signals
2. **value** — earnings/cashflow yield, book multiples
3. **quality** — Piotroski F-score, Altman Z-score, ROE, margins
4. **growth** — revenue/earnings growth, expansion vs peers
5. **revisions** — analyst estimate-revisions trend
6. **short_interest** — side-aware (longs reward declining SI, shorts reward rising SI)
7. **insider** — Form 4 P/S codes only, CEO/CFO weighted 3×, cluster-buy bonus
8. **institutional** — 13F-tracked-fund opening positions

Each factor scores 0–100 as **percentile rank within GICS sector**. Sub-factors are equal-weighted within parent factor. The 8 parent scores are then equal-weighted into a `combined` composite (0–100).

### The qualitative overlay (`src/ls_equity_fund/analysis/combined_score.py`)

`combined_score_v2 = 0.60 × quant_composite + 0.40 × claude_avg`

Claude analyzes 4 things per top candidate: filing diff vs prior, 10-K Risk Factors, Form 4 insider activity, sector-relative ranking. The earnings-call analyzer is a v2 stub returning `None`.

### The portfolio construction (`src/ls_equity_fund/portfolio/conviction_tilt.py`)

- 20 longs (top combined score) / 20 shorts (bottom combined score), sector-balanced soft cap
- Equal-weight base sized to `gross_target / 2 / N` per side
- Conviction tilt: top 5% × 1.5, top 10% × 1.25, rest × 1.0, renormalised to preserve gross
- ADV cap: no position > 5% of 20-day average dollar volume
- Earnings halve: any name with earnings in next 5 days sized 50%
- Beta-adjusted to bring net beta inside `max_beta` (default 0.15)
- Sector net cap: long-minus-short per sector ≤ `max_sector_pct` (default 25%)

### The risk and execution layer (Phases 6 + 8 — verified shipped)

- 8 absolute pre-trade vetos (halt, earnings blackout, ADV, position cap, sector cap, gross/net bounds, net beta ≤ 0.20, pairwise correlation ≤ 0.80)
- 5 circuit breakers (daily loss 1.5% / 2.5% / weekly 4% / drawdown 8% / single-position 3%)
- IBKR paper broker via `ib_async`, with `MERIDIAN_LIVE_OK=1` env-var + `output/promotion_record.json` both required for live mode

### Is this the only strategy?

**Yes for now, but the architecture is plug-replaceable.**

- The `Optimizer` ABC seam ([src/ls_equity_fund/portfolio/base.py](../src/ls_equity_fund/portfolio/base.py)) already supports two implementations: `ConvictionTiltOptimizer` and `MVOOptimizer` (SLSQP). They behave differently but both use the same 8-factor input.
- A different strategy (e.g. mean-reversion, pairs trading, momentum-only long-only) would mean swapping factor definitions and/or replacing the `Optimizer`. It does NOT mean rebuilding the data layer, dashboard, risk system, or execution.

### What "kill" means

If this strategy doesn't work, **kill ≠ delete the project**. The data, scoring, risk, execution, dashboard, and reporting layers are reusable. Three things you can do if the strategy fails validation:

1. **Pivot the strategy** — replace the 8-factor list or the optimizer. The infrastructure stays. Maybe 2–4 weeks of focused work.
2. **Rebuild on a different thesis** — e.g. trend-following on commodities, or options-overlay strategies. Bigger rewrite, but the data + risk + execution + dashboard layers still help.
3. **Shelve and harvest** — keep the codebase as a portfolio piece, walk away from the trading idea entirely. Nothing wrong with this.

The validation phases below are designed so the **most expensive failure modes get caught earliest** at the lowest cost. Phase A costs you a week and zero dollars. Phase D risks 10% of intended capital. The discipline is: don't move to the next phase until the current one passes.

---

## The 5-phase ramp

### Phase A — Pulse check (light backtest)

**Goal:** Do the 8 factors have any predictive power on this universe over the last 3 years?

**Time:** ~1 week elapsed (mostly compute time)
**Money at risk:** $0
**Status:** ❌ **COMPLETE — STRICT NO-GO with stronger evidence after SimFin PIT data added (2026-05-09 v2)**

### Phase A v1 (yfinance only — current snapshot fundamentals):

| Factor | IC (3y) | Verdict | Notes |
|---|---|---|---|
| `insider` | **+0.0910** | ✅ PASS | Real edge — Form 4 P/S codes work. |
| `momentum` | +0.0134 | ⚠️ FAIL | Positive but weak. |
| `revisions` | -0.0105 | ❌ FAIL | Only 4 distinct scores → unmeasurable. |
| `value/quality/growth/short_interest/institutional` | 0.0000 | ❌ UNTESTABLE | Zero distinct scores → no PIT history. |

### Phase A v2 (SimFin PIT fundamentals added — 1,766 cells across 563 publish dates):

| Factor | IC (3y) | Verdict | Notes |
|---|---|---|---|
| `insider` | **+0.0910** | ✅ **PASS** | Same as v1 — real, persistent edge. |
| `momentum` | +0.0134 | ⚠️ FAIL | Same as v1. |
| `value` | **−0.0882** | ❌ **STRONGLY ANTI-PREDICTIVE** | NEW data; high-value scores predict *lower* forward returns. |
| `quality` | **−0.0927** | ❌ **STRONGLY ANTI-PREDICTIVE** | NEW data; high Piotroski/Altman scores predict *lower* returns. |
| `growth` | **−0.1280** | ❌ **STRONGLY ANTI-PREDICTIVE** | NEW data; the strongest anti-signal of all 8. |
| `revisions` | -0.0105 | ❌ FAIL | Still untestable (yfinance estimates only today's snapshot). |
| `short_interest` | 0.0000 | ❌ UNTESTABLE | Still no PIT history. |
| `institutional` | 0.0000 | ❌ UNTESTABLE | EDGAR 13F parser broken on pre-2010 filings. |

**Strict gate verdict:** 1 of 8 factors clears 0.03 → ❌ **NO-GO**.

### Phase A v3 (S&P 500 — 503 tickers, broader universe, SimFin PIT fundamentals)

Re-ran Phase A on the broader S&P 500 universe to test the universe-bias hypothesis from v2 (the conjecture that fundamentals factors anti-predict on too-narrow mega-cap universes but should normalise on broad ones).

**Setup:**
- universe_mode: `sp500` → 503 active tickers (Wikipedia scrape, fixed HTTP 403 with proper User-Agent)
- 391k daily-price rows backfilled
- 12,187 SimFin fundamentals rows (8,819 ticker-period cells, 968 distinct PIT publish dates)
- Form 4 / 13F / short / estimates NOT backfilled at scale (EDGAR parser hang + only-current-snapshot from yfinance)
- Historical replay: 784 weekdays, 722 ok / 62 failed (early-2023 dates with insufficient lookback)

| Factor | v2 (90) | v3 (500) | What changed |
|---|---|---|---|
| `momentum` | +0.0134 | **+0.0290** | **Doubled, right at threshold** — broader universe gives momentum more dispersion to work with. |
| `value` | −0.0882 | **+0.0060** | Flipped from strongly anti-predictive to ~zero. **Universe-bias hypothesis confirmed for value.** |
| `quality` | −0.0927 | **−0.0147** | Magnitude collapsed 6×. Still slightly anti, but barely above noise. |
| `growth` | −0.1280 | **−0.0228** | Magnitude collapsed 5×. Same story as quality. |
| `revisions` | −0.0105 | −0.0257 | Still untestable (no PIT estimates). |
| `insider` | +0.0910 | 0.0000 | **Untestable in v3** — Form 4 backfill not run on 503 tickers (parser hangs at scale). v1/v2 result on 90 tickers was the only valid measurement. |
| `short_interest` | 0.0000 | 0.0000 | Still untestable. |
| `institutional` | 0.0000 | 0.0000 | Still untestable. |

**Strict gate verdict:** 0 of 8 factors clears 0.03 → ❌ **NO-GO** (worse than v1/v2 on the strict count, but cleaner data).

### What v3 actually told us

The universe-bias hypothesis was **partially** correct:

1. ✅ **Confirmed:** mega-cap universe was distorting fundamentals factor IC. Value/quality/growth all moved from -0.09 to -0.13 (strongly anti) to roughly zero (essentially noise) on the broader universe.

2. ❌ **Disconfirmed:** the broader universe did NOT reveal positive predictive power that was hidden by mega-cap regime. The factors are just noise on S&P 500, not strong-positive.

3. **Momentum** edged up to +0.029 — within striking distance of the 0.03 threshold but not over. With more universe expansion (Russell 1000, Russell 3000) it might cross into PASS territory, but that's speculative.

4. **The insider factor remains the strongest single signal we've found** (IC = +0.091 on 90-ticker), but we can't yet confirm it scales — Form 4 backfill on 503 tickers is blocked by an EDGAR parser bug that hangs after ~30 minutes.

### Honest reading of all three runs

We have run Phase A in three configurations now, with progressively better data:

- **v1 (yfinance only, 90 tickers):** 1/8 PASS (insider). 5 factors structurally untestable.
- **v2 (yfinance + SimFin PIT, 90 tickers):** 1/8 PASS. Fundamentals factors revealed as strongly anti-predictive on mega-caps.
- **v3 (yfinance + SimFin PIT, 503 tickers):** 0/8 PASS. Fundamentals factors revert to noise on broad universe; momentum edges close to threshold.

**The strategy as designed does not show predictive power on either universe.** Across 3 years and ~390k price-rows, ~12k fundamentals rows, and 784 replay dates, the only factor with documented edge is `insider` on the 90-ticker universe (IC = +0.091, reproduced across v1 and v2).

The "buy high-score, short low-score" thesis on value/quality/growth doesn't validate. Period.

### Updated paths forward (post v3)

- **Path A — Shelve.** The strict criteria say stop. Three Phase A runs and zero clean factor PASSes (except insider, which is untestable at scale). Defensible kill.
- **Path C — Pivot to insider-only, with a scale validation step first.** The insider factor showed IC = +0.091 on 90 tickers across two independent runs. Before betting the strategy on it, fix the EDGAR Form 4 parser hang and re-run insider-only IC on S&P 500. If insider survives the universe expansion, that's a real signal worth building a focused strategy around.
- **Path B' (further expansion)** — try Russell 1000 or 3000. Universe expansion *did* normalise the fundamentals anomaly between v2 and v3, but didn't push them positive. Diminishing returns from going broader. Low priority.
- **Path D (invert signals)** — still the textbook overfitting trap. Don't.

### What v2 actually told us (the load-bearing finding)

The SimFin PIT backfill gave us real history for value, quality, and growth. The result is **not** "factors are noise" — it's **stronger than that**: the factors are *anti-predictive* on this universe. Highly-rated value/quality/growth names *underperform* over the next 20 days, with IC magnitudes 3× to 4× the threshold. This is a strong signal in the wrong direction.

Three plausible explanations:

1. **Universe bias.** The 50–90 mega-cap US universe (mostly tech-heavy S&P 100 names) is a regime where the "cheap" / "high-quality" / "high-growth" names within each GICS sector tend to be the laggards. Within only 5–9 names per sector, ranking by traditional value/quality/growth metrics often surfaces value traps and saturated growth stories rather than true outperformers. **Broad-universe factor research (academic IC ~0.03–0.06) typically uses 500–3,000 names, not 50–90 mega-caps.**

2. **Regime effect.** 2023-2026 was dominated by AI/mega-cap-growth rallies. The cheapest-by-P/E mega-cap during that window was often the *least* exciting (META vs NVDA, etc.). High Piotroski-quality often meant defensive/mature sectors (utilities, staples) that lagged the rally.

3. **Sign convention.** Possible but unlikely — three independent factors all flipped the same direction with consistent magnitudes argues for genuine regime/universe effect, not a code bug.

### Honest reading of the verdict

**The strategy as designed does not validate on this universe.** That's the data, not an excuse. Three of the eight factors aren't just noise — they're anti-predictive (negative IC magnitudes 0.09–0.13). Following the existing rules (long high-score, short low-score) over the last 3 years would have systematically lost money on those three factors. Only `insider` clearly works.

### Paths forward (re-evaluated after v2)

- **Path A — Shelve.** The strict criteria say stop. v2 gave us *stronger* NO-GO signal than v1. Walk away.
- **Path B (revised) — Re-run on broader universe.** Switch from 90-ticker scanner-seed to S&P 500 (`universe_mode: sp500`). The factor IC literature says factors work on broad universes; with 500 names ranked within each sector you get 50+ per sector instead of 5–9, which gives the percentile rank room to discriminate. This is a single config change + re-run of all the L1 backfill steps. Cost: ~1 day of focused work + maybe a Polygon/Tiingo/SimFin paid tier for the larger universe.
- **Path C — Pivot to insider-only.** The one factor that works has IC = +0.091 — that's actually exceptional for a single factor. A focused single-factor strategy around insider buys (CEO/CFO weighted) could be a real edge. This would be a different project but starts from real evidence.
- **Path D — Invert the fundamentals signals.** The data says high-value/quality/growth scores anti-predict. A contrarian strategy (long the LOW scores, short the HIGH scores) would have the opposite IC sign. This is risky — overfitting to in-sample data — but matches what the data actually shows. Treat with extreme suspicion until validated on a different time window.

**The structural finding (load-bearing for v2 planning):**

The L1 data layer ingests today's snapshot only for fundamentals, short interest, analyst estimates, and institutional holdings. Historical PIT replay against those tables produces identical scores across every replay date → zero rank-correlation variance → IC ≈ 0. **Five of the eight factors are not just "failing" — they are structurally untestable** with the current data architecture.

This is a real-world consequence of the previous AI feedback's warning: "yfinance fundamentals — restated numbers silently replacing historical values; D2 mitigation helps but doesn't fully solve this." Phase A discovered exactly that limitation. The `as_of_ingest_date` append-only design works going *forward* (each day's snapshot is preserved), but historical backfill of past quarter snapshots is not possible from yfinance.

**What this means for the project:**

The strategy as designed cannot be validated end-to-end on 3 years of history without one of:

1. **Paid PIT-historical fundamentals feed** (Polygon, Tiingo, or IEX with 3-5y historical PIT data — typically ~$50–500/month). The `MarketDataProvider` seam already exists; swap-in is a config flip + provider class.
2. **Forward-accumulated snapshots** — paper-trade for 60–90 days while ingesting daily snapshots; the system *naturally* builds the PIT history forward over time. Slow but free.
3. **Pivot to insider-only strategy** — only one factor demonstrably has edge on this data; rebuild a focused strategy around the `insider` factor's signal.

**Steps actually run:**
1. ✅ Smoke-tested daily pipeline (doctor + run-data + run-scoring + run-portfolio --whatif)
2. ✅ Found and fixed two L1 layer bugs:
   - **Bug 1:** `YFinanceProvider` had stub methods raising `NotImplementedError` for fundamentals/short/estimates/earnings — wired them to delegate to existing `_impl` modules. (commit pending)
   - **Bug 2:** Universe builder mass-delisted all 90 tickers when upstream `liquid_us` lookup failed; added safety check that aborts when incoming list would delist >50% of established universe. (commit pending)
3. ✅ Re-ran full data refresh: prices (39k rows), fundamentals (849), ratios (90), filings (2,255 metadata + 1,273 insider transactions). The filings step hung after ~30 min on EDGAR rate-limit retries; killed, recovered short_interest + estimates via direct refresh calls.
4. ✅ Wrote `scripts/historical_replay.sh`, ran across 784 weekdays (2023-05-09 → 2026-05-09), 0 failures, 36 minutes.
5. ✅ Ran `meridian compute-factor-ic` against the populated `factor_scores_parent` table.
6. ⚠️ Discovered 5 of 8 factors are structurally untestable due to current-snapshot-only L1 data.

**Go/No-Go gate (strict, original):**
- ❌ **NO-GO** — 1 of 8 factors clears 0.03; gate required ≥ 4.

**Go/No-Go gate (nuanced reading):**
- ⚠️ **PARTIAL** — 1 of 2 *testable* factors passes strongly (`insider`); the other 6 need data infrastructure work to be validated.

**Recommended next decision (for operator):**

Pick ONE of:
- **Path A — Accept NO-GO and shelve / pivot.** The strict criteria say stop. The insider factor alone is too thin a strategy.
- **Path B — Switch to forward-accumulation Phase C.** Skip Phase B, run paper-trading for 60–90 days, naturally build PIT history forward, then re-run Phase A with that data. Total elapsed: 3 months before re-validation.
- **Path C — Pay for a PIT historical data feed.** Subscribe to Polygon/Tiingo PIT fundamentals (~$50–500/month), backfill 3y of historical fundamentals/short/estimates/13F, re-run Phase A in ~1 day. Most expensive but fastest path to a real Phase A verdict.
- **Path D — Pivot to insider-only strategy.** The one factor that works has IC = 0.0910 — that's actually high. Rebuild a focused single-factor strategy around it; this would be a different project.

**Steps:**
1. Add Anthropic API key to `.env` (or skip Claude for Phase A — see note)
2. Smoke-test the daily pipeline (`meridian doctor`, `run-data`, `run-scoring`, `run-portfolio --whatif`)
3. Write a shell loop that runs `meridian run-scoring --asof <date>` for every weekday in the last 3 years (≈750 dates). This populates `factor_scores_parent` for the historical window.
4. Run `meridian compute-factor-ic`. Read the IC numbers per factor.

**Note on Claude for Phase A:** Phase A only tests *quant* factor predictive power. Running Claude analysis 750 times historically would cost $1,000+ and isn't necessary — we only need the parent-score IC. So Phase A can use raw quant signals (no Claude blend) and gate on whether the 8 quant factors alone show edge.

**Go/No-Go gate:**
- ✅ **GO** if **at least 4 of 8 factors** have IC ≥ 0.03 over 3 years AND **the combined composite** has IC ≥ 0.04
- ❌ **NO-GO** if no factor clears 0.02, or only 1–2 factors clear 0.03, or composite IC is negative

**If NO-GO:** Strategy is dead on this universe. Pivot factor definitions or shelve. Do NOT proceed to Phase B.

---

### Phase B — Full strategy walk-forward backtest

**Goal:** Would this complete strategy — with all costs, vetos, and rules — have made money over the last 3 years?

**Time:** 3–4 weeks of focused work
**Money at risk:** $0
**Status:** ⏳ NOT STARTED (this is BACKTEST-01 from v2)

**Steps:**
1. Build `src/ls_equity_fund/backtest/` module. Walk day-by-day through history, replaying the full pipeline.
2. Honor point-in-time data: only use information available *as of that day*.
3. Apply the existing transaction cost model (`portfolio/transaction_cost.py`) plus realistic slippage (10–25 bps additional).
4. Handle delistings, mergers, splits, dividends correctly.
5. Run rolling 1-year Sharpe, max-DD, hit-rate metrics across the full window.
6. **Lock parameters before running.** No tuning.

**Go/No-Go gate:**
- ✅ **GO** if backtest produces:
  - Sharpe ratio ≥ **1.0** after costs
  - Max drawdown ≤ **15%**
  - Hit rate ≥ **52%** on round-trips
  - Positive returns in ≥ 2 of 3 years
  - No 12-month rolling window with < −10% return
- ❌ **NO-GO** if any of the above fails

**If NO-GO:** Don't tweak. The strategy doesn't survive costs in real history. Either rebuild on a different thesis or shelve.

---

### Phase C — Paper trading (live forward test)

**Goal:** Does the system perform on real-time data the way the backtest predicted?

**Time:** 60–90 calendar days
**Money at risk:** $0
**Status:** ⏳ NOT STARTED

**Steps:**
1. Add Anthropic API key to `.env` (Claude is now active).
2. Install the launchd plist for daily 5:15pm runs.
3. Let it run continuously. Don't intervene.
4. Each week, compare paper P&L to the backtest's expected return for the same period.
5. Track every system error (data missing, broker disconnect, scoring crash) — there should be zero unresolved issues by the end.

**Go/No-Go gate:**
- ✅ **GO** if paper trading produces:
  - Returns within ±50% of backtest expectations for the same window
  - All 8 absolute vetos worked when triggered (zero bypasses, audit log confirms)
  - Zero unresolved system failures
  - Slippage observed within 50 bps of model
  - Live IC on ≥ 4 of 8 factors ≥ 0.03
- ❌ **NO-GO** if paper diverges materially from backtest, any veto failed, or there were unresolved system bugs

**If NO-GO:** Investigate divergence. Most likely causes: backtest had look-ahead bias, cost model is too optimistic, or the universe behavior changed. Fix before live.

---

### Phase D — Initial live (small allocation)

**Goal:** Confirm the system survives real money + behavioral discipline.

**Time:** 3 months
**Money at risk:** **5–10% of intended capital** (NOT 100%)
**Status:** ⏳ NOT STARTED

**Steps:**
1. Run `python scripts/promote_to_live.py --account-number <DU/U-account>`. All 8 criteria must pass.
2. Set `MERIDIAN_LIVE_OK=1`. Set `broker.mode: live` in config.
3. Start with **one-tenth** of intended deployment size.
4. Live trade for 3 months without manual intervention.
5. Track every divergence between live fills and the paper model (slippage, partial fills, rejections).

**Go/No-Go gate (for scaling up):**
- ✅ **GO** if:
  - Live results within ±30% of paper expectations
  - Drawdown stays under 8% (kill-switch threshold)
  - Slippage on real fills within 30 bps of paper
  - You did NOT override the system's decisions even once
- ❌ **NO-GO** if live underperforms by > 30%, drawdown > 8%, or you intervened manually

**If NO-GO:** Stop the live deployment. Investigate. Common causes: real slippage > paper, hidden bug surfaced under real-money conditions, regime change, or behavioral failure (you can't stomach drawdowns).

---

### Phase E — Scale up

**Goal:** Reach intended deployment size only with proven evidence.

**Time:** 9+ months total ramp
**Money at risk:** ramps from 10% → 25% → 50% → 100%
**Status:** ⏳ NOT STARTED

**Steps:**
- After 3 months at 10% with passing metrics → scale to 25%
- After 3 more months at 25% → scale to 50%
- After 3 more months at 50% → scale to 100%
- **Total elapsed from paper-trade-end to full deployment: 12 months minimum**

**Go/No-Go gate (for full deployment):**
- 12 months of live data with realized Sharpe ≥ 0.7 after all costs
- No 12-month rolling period with negative return
- Factor IC monitor (BACKTEST-02 by then) shows factors still alive

**If NO-GO at any rung:** Stay at current size or scale down. Never add capital after a drawdown unless explicit evidence shows regime has changed back.

---

## Kill criteria (the contract with yourself)

Sign and date this section. It is the most important part of the document.

> **I will stop this project and not deploy real money if:**
>
> - Phase A IC backtest shows fewer than 4 factors above 0.03
> - Phase B portfolio backtest shows Sharpe < 1.0 after costs
> - Phase C paper trading diverges by more than 50% from backtest expectations
> - Phase D live trading shows a 5%+ drawdown in the first month
> - I find myself wanting to override the system's decisions manually
>
> Signed: _____________________
> Date: _______________________

---

## Current position

**Active phase:** Phase A v3 complete — awaiting operator decision on Path A vs Path C (see Phase A section)
**Next concrete action:** Operator picks Path A (shelve based on 3 runs of NO-GO) or Path C (fix EDGAR Form 4 parser hang and validate insider-only at S&P 500 scale)

## Phase log

| Phase | Status | Started | Completed | Result | Notes |
|-------|--------|---------|-----------|--------|-------|
| A — Pulse check (v1) | ⚠️ Complete (NO-GO strict) | 2026-05-09 | 2026-05-09 | 1/8 PASS; 5/8 untestable (no PIT data) | `insider` IC=+0.091; `momentum` IC=+0.013 |
| A — Pulse check (v2 with SimFin) | ❌ Complete (NO-GO strict, stronger evidence) | 2026-05-09 | 2026-05-09 | 1/8 PASS; 3/8 strongly ANTI-predictive | `value/quality/growth` ICs −0.09 to −0.13 on mega-cap universe |
| A — Pulse check (v3 S&P 500 + SimFin) | ❌ Complete (NO-GO strict; universe-bias hypothesis partially confirmed) | 2026-05-09 | 2026-05-09 | 0/8 PASS; momentum +0.029 at threshold | fundamentals factors reverted to ~noise on broader universe; `insider` not measured at scale |
| B — Full backtest | ⏳ Blocked on Phase A path decision | — | — | — | Cannot run until L1 PIT-history is solved (Path B or C) |
| C — Paper trading | ⏳ Not started | — | — | — | Path B route uses Phase C as forward-accumulation |
| D — Initial live | ⏳ Not started | — | — | — | — |
| E — Scale up | ⏳ Not started | — | — | — | — |

Update this log as each phase completes. The log becomes the audit trail that proves you didn't skip a gate.

---

*Last updated: 2026-05-09 (v3) — Phase A re-run on S&P 500; verdict NO-GO strict (0/8 PASS, momentum at +0.029 just below threshold); universe-bias hypothesis partially confirmed (fundamentals reverted to noise); only `insider` factor has documented edge but only on 90-ticker universe*
