# Phase 2: Scoring Engine (L2) — Research

**Researched:** 2026-05-04
**Domain:** Cross-sectional factor scoring, GICS sector-neutral percentile rank, audit-grade factor persistence
**Confidence:** HIGH (factor formulas, scipy API, schema shape) / MEDIUM (per-sub-factor input field choice for degenerate cases)

## Summary

Phase 2 reads from L1's already-populated SQLite tables — `daily_prices`, `fundamentals`, `fundamental_ratios`, `insider_transactions`, `institutional_holdings`, `short_interest`, `analyst_estimates`, `universe(sector)` — and produces one row per `(ticker, score_date, factor, sub_factor)` in a new `factor_scores` table. Each row carries both the raw value (for audit / replay / debug) and the 0–100 percentile rank of that raw value within the ticker's GICS sector on that date. Parent-factor scores are the equal-weighted mean of the sub-factor percentile ranks. The scoring engine has zero network calls — it is pure SQL → pandas → SQL — so the whole pass over ~3000 names × 27 sub-factors completes in seconds, not minutes.

Three load-bearing constraints distinguish this phase from a generic factor library:

1. **CP3 binding (insider P/S only):** every insider sub-factor query is `WHERE transaction_code IN ('P','S')` exclusively; A/M/F/G/D contribute zero. Cluster-buy uses `WHERE transaction_code = 'P'`. The L1 module `data/insider.py` already enforces this for cluster-buy; Phase 2 must enforce it on net-dollar-flow as well.
2. **Side-awareness (SCORE-06):** short-interest is the only factor whose direction differs between the long book and the short book. The recommended pattern is to compute the score with *long-side semantics* by default (declining SI = high score) and provide a derived `score_short_side = 100 - score_long_side` rather than carrying a "side" column on `factor_scores`. The optimizer in L4 picks per-side.
3. **At-entry persistence (SCORE-10):** scores are never recomputed historically. The PK `(ticker, score_date, factor, sub_factor)` is idempotent — re-running today's scoring is a no-op via `INSERT OR REPLACE`; yesterday's row is never touched.

**Primary recommendation:** Build one `compute_X_factor(asof: date, conn) -> pd.DataFrame` function per factor (8 of them), all returning a tidy `(ticker, sub_factor, raw_value)` frame. Concatenate, attach sector via `JOIN universe`, group-by `(score_date, factor, sub_factor, sector)`, apply `scipy.stats.rankdata(method='average') / N * 100`, batch-write via `executemany`. ~600 lines of code total.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCORE-01 | Momentum factor — 6 sub-factors (12-1 mo, 6m, 3m, acceleration, 52w-high proximity, sector-relative) | Per-factor cookbook §Momentum; `daily_prices` is the only input |
| SCORE-02 | Value factor — 6 sub-factors (forward EY, B/P, FCF yield, EV/EBITDA inverted, shareholder yield, sales/EV) | §Value; combines `fundamental_ratios` + `daily_prices` (close × shares_out for market cap) + `analyst_estimates.eps_fy1` for forward EY |
| SCORE-03 | Quality factor — 8 sub-factors (ROE stability, GM level, GM trend, D/E inverted, CFO/NI, accruals inverted, Piotroski F, Altman Z) | §Quality; Piotroski 9-binary + Altman 5-component formulas verified below |
| SCORE-04 | Growth factor — 5 sub-factors (rev YoY, earnings YoY, rev acceleration, R&D intensity, FCF YoY) | §Growth; reads `fundamental_ratios` directly |
| SCORE-05 | Estimate revisions — 3 sub-factors (30/60/90-day deltas), degenerate-neutral until sufficient history | §EstRev; uses snapshot history in `analyst_estimates` |
| SCORE-06 | Short interest — 3 sub-factors (% float, days-to-cover, change vs prior); side-aware | §ShortInt; long-side semantics persisted, short-side = 100 - long-side at read time |
| SCORE-07 | Insider activity — 3 sub-factors (90d net $ flow, CEO/CFO 3× weight, cluster-buy bonus); P/S-only (CP3) | §Insider; `WHERE transaction_code IN ('P','S')` for net flow, `WHERE transaction_code='P'` for cluster |
| SCORE-08 | Institutional flow — 3 sub-factors (count of tracked funds, net change vs prior quarter, multi-fund-opening flag) | §InstFlow; uses L1's `institutional_holdings.is_new_position` |
| SCORE-09 | 0–100 percentile rank within GICS sector; sub-factors equal-weighted within parent | §Sector-Percentile-Rank Algorithm; `scipy.stats.rankdata(method='average') / N * 100` |
| SCORE-10 | Persist all scores at-entry to `factor_scores` for replay | §`factor_scores` schema; PK `(ticker, score_date, factor, sub_factor)` |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Factor sub-factor compute | `factors/{momentum,value,...}.py` (CPU pandas) | `db.py` (SQL reads) | Pure compute on already-ingested L1 data. No network. |
| GICS sector percentile rank | `factors/sector_rank.py` | scipy.stats | Single utility used by every factor — DRY. |
| Sub-factor → parent factor aggregation | `factors/composer.py` | — | Equal-weighted mean of per-sub-factor percentile ranks. |
| Persistence (at-entry) | `factors/persist.py` (writer) → `factor_scores` table | `db.py` connection | Single writer; idempotent on `(ticker, score_date, factor, sub_factor)`. |
| CLI orchestration | `cli/scoring_cmd.py` | `factors/__init__.py` façade | Typer subcommand `meridian run-scoring [--ticker T] [--sector S] [--asof DATE]`. |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `scipy` | `1.17.x` | `scipy.stats.rankdata(method='average')` for percentile rank | Spec mandate; `method='average'` is the standard tie-handling for cross-sectional rank in factor literature [VERIFIED: scipy.stats.rankdata docs] |
| `pandas` | `>=2.2,<3.0` | DataFrames for cross-sectional grouping | Already pinned by Phase 0; pandas 3.0 has breaking inplace-return changes — DO NOT bump [CITED: CLAUDE.md] |
| `numpy` | `>=2.0,<2.5` | Numerical core for vectorized percentile math | Already pinned by Phase 0 [CITED: CLAUDE.md] |
| `sqlite3` (stdlib) | bundled | Read L1 tables, write `factor_scores` via `executemany` | Spec mandate; raw SQL audit-grade [CITED: CLAUDE.md] |
| `structlog` | `25.5.0` | Per-run audit log of scoring runs | Already configured by Phase 0 [CITED: phase 0 CONTEXT D-19] |
| `typer` | (already in deps) | `meridian run-scoring` subcommand | Already configured by Phase 0 [CITED: phase 0 CONTEXT D-23] |

### Don't Pull In
| Library | Why NOT |
|---------|---------|
| `ta-lib` / `pandas-ta` | CLAUDE.md anti-rec — momentum is `pct_change` / `rolling`, not 200 indicators [CITED: CLAUDE.md anti-recs] |
| `sklearn` / `lightgbm` | This is a factor-rank system, not an ML system. No supervised training in v1 [CITED: CLAUDE.md anti-recs] |
| `statsmodels` | Reserved for L5 Barra-style cross-sectional regressions (Phase 6). L2 percentile rank is descriptive, not regression-based. |
| `scipy.optimize` | Reserved for L4 MVO (Phase 7). L2 has no optimization. |

**Installation:** No new packages. Phase 0 already pins scipy / pandas / numpy. [VERIFIED: pyproject.toml in Phase 1 verification]

## Per-Factor Implementation Cookbook

> Conventions used below:
> - `asof: date` — the score date.
> - `conn: sqlite3.Connection` — read-only against L1 tables.
> - All sub-factor functions return `pd.DataFrame(columns=['ticker', 'sub_factor', 'raw_value'])`.
> - "Higher raw_value = better" is the default convention. Where the spec inverts (debt/equity, accruals), invert the sign of `raw_value` *before* persisting so the percentile rank direction is uniform across factors.

### 1. Momentum (SCORE-01) — 6 sub-factors

Source data: `daily_prices` only. No fundamentals.

| Sub-factor | Formula | Notes |
|-----------|---------|-------|
| `mom_12_1` | `close[asof - 21bd] / close[asof - 252bd] - 1` | The Jegadeesh-Titman canonical: 12-month return *skipping the most recent month*. Skipping suppresses 1-month mean reversion contamination. [CITED: Jegadeesh & Titman 1993] [CITED: Carhart 1997] |
| `mom_6m` | `close[asof] / close[asof - 126bd] - 1` | Standard 6-month total return. |
| `mom_3m` | `close[asof] / close[asof - 63bd] - 1` | Standard 3-month total return. |
| `mom_accel` | `mom_3m - mom_6m` | Acceleration: is recent momentum *faster* than older? Positive = improving trend. |
| `mom_52w_high` | `close[asof] / max(close[asof-252bd:asof])` | Proximity to 52-week high. ∈ (0, 1]. Closer to 1 = stronger. |
| `mom_sector_rel` | `(close[asof]/close[asof-126bd]) / (sector_etf_close[asof]/sector_etf_close[asof-126bd])` | Sector-relative strength: stock 6m return ÷ sector ETF 6m return. Sector ETF mapping comes from `config.data.sector_etfs` and is joined via `universe.sector → sector_etfs[sector]`. |

**Trading-day arithmetic:** the spec uses month-counts but markets are open ~21 trading days/month. Use trading-day offsets (21/63/126/252) by indexing `daily_prices` by trading-day position, not calendar `date - timedelta(days=N)`. SQL pattern: `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC)` to find the Nth-back close, or pull the close series into pandas and use `.shift(N)`.

**Edge case:** ticker has < 252 trading days in `daily_prices` (recent IPO or recently-added universe row). Return `None` for `raw_value`; `compute_sector_percentile_rank` excludes nulls before ranking. The factor row is still written to `factor_scores` with `raw_value=NULL, percentile_rank=NULL` for audit completeness — never silently drop a (ticker, sub_factor, score_date) row.

### 2. Value (SCORE-02) — 6 sub-factors

Source data: `fundamental_ratios` (already computed in L1, asof_date keyed) + `daily_prices` for market cap + `analyst_estimates.eps_fy1` for the forward look.

| Sub-factor | Formula | Source |
|-----------|---------|--------|
| `val_fwd_ey` | `eps_fy1 / close` | Forward earnings yield. From `analyst_estimates.eps_fy1` (latest snapshot ≤ asof). |
| `val_bp` | `total_equity / market_cap` | Book-to-price (inverse of P/B). `market_cap = close × shares_outstanding` from latest `fundamentals`. |
| `val_fcf_yield` | `fundamental_ratios.fcf_yield` | Already computed in L1 (`free_cash_flow / market_cap`). Pass-through. |
| `val_ev_ebitda_inv` | `1 / (ev / ebitda)` = `ebitda / ev` | Inverted (higher = cheaper). `ev = market_cap + total_debt - cash_and_equivalents`. EBITDA = `ebit + depreciation_amortization` — *but L1's `fundamentals` does not store D&A*. Use `ebit` as a proxy and label the sub-factor `val_ev_ebit_inv` if D&A truly unavailable. **[ASSUMED]** that operator accepts EBIT proxy until D&A is added — flag for discuss-phase. |
| `val_shareholder_yield` | `dividend_yield + buyback_yield` | Already computed in L1 (sign-flipped to positive). Sum directly. |
| `val_sales_ev` | `revenue / ev` | Sales-to-EV. From `fundamentals.revenue` and computed EV. |

**[ASSUMED]** EV computation uses `total_debt = long_term_debt` (L1 stores only LTD, not short-term debt). This is the standard simplification in factor research at this data resolution; flag for discuss-phase if operator wants STD added to L1.

### 3. Quality (SCORE-03) — 8 sub-factors

Source data: `fundamental_ratios` + `fundamentals` (for Piotroski/Altman year-over-year math).

| Sub-factor | Formula | Source |
|-----------|---------|--------|
| `qual_roe_stability` | `1 / std(roe[last 8 quarters])` | Stability = inverse of standard deviation. Higher = more stable. |
| `qual_gm_level` | `fundamental_ratios.gross_margin` | Pass-through. |
| `qual_gm_trend` | `gross_margin[latest] - gross_margin[4 quarters ago]` | YoY change in GM. Positive = improving. |
| `qual_de_inv` | `-fundamental_ratios.debt_to_equity` | **Sign-flipped** (lower D/E = better, so we negate for uniform "higher = better" semantics). |
| `qual_cfo_ni` | `fundamental_ratios.cfo_to_ni` | Higher = earnings backed by cash. Pass-through. |
| `qual_accruals_inv` | `-fundamental_ratios.accruals_ratio` | **Sign-flipped**. High accruals = aggressive accounting → bad. Low accruals → good → high score after invert + percentile rank. |
| `qual_piotroski_f` | 0–9 integer (see below) | Sum of 9 binary checks. |
| `qual_altman_z` | Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5 (see below) | Continuous; thresholds for zone classification stored alongside raw_value. |

#### Piotroski F-Score (9 binary criteria, sum to 0–9)

Latest annual `fundamentals` row (period_type='annual') vs prior annual row. Each check is 1 if true else 0. [CITED: Piotroski (2000); en.wikipedia.org/wiki/Piotroski_F-score]

**Profitability (4 points):**
1. **F1 — Positive Net Income:** `net_income > 0` for current year.
2. **F2 — Positive CFO:** `cfo > 0` for current year.
3. **F3 — Improving ROA:** `(net_income[t] / total_assets[t]) > (net_income[t-1] / total_assets[t-1])`.
4. **F4 — CFO > NI (accruals quality):** `cfo > net_income`.

**Leverage / Liquidity / Source of Funds (3 points):**
5. **F5 — Decreasing leverage:** `(long_term_debt[t] / total_assets[t]) < (long_term_debt[t-1] / total_assets[t-1])`.
6. **F6 — Improving current ratio:** `(current_assets[t] / current_liabilities[t]) > (current_assets[t-1] / current_liabilities[t-1])`.
7. **F7 — No new shares issued:** `shares_outstanding[t] <= shares_outstanding[t-1]`.

**Operating Efficiency (2 points):**
8. **F8 — Improving gross margin:** `(gross_profit[t] / revenue[t]) > (gross_profit[t-1] / revenue[t-1])`.
9. **F9 — Improving asset turnover:** `(revenue[t] / total_assets[t]) > (revenue[t-1] / total_assets[t-1])`.

`raw_value = sum(F1..F9)` ∈ {0,...,9}. Strong: 8–9, Weak: 0–2 (informational, not used for branching — the percentile rank handles relative scoring).

**Edge case:** missing prior-year row → `raw_value = NULL`. Do not impute.

#### Altman Z-Score (original 1968 manufacturing-firms model, 5 components)

[CITED: Altman 1968; en.wikipedia.org/wiki/Altman_Z-score]

```
X1 = working_capital / total_assets
X2 = retained_earnings / total_assets
X3 = ebit / total_assets
X4 = market_cap / total_liabilities      # market value of equity / book value of liabilities
X5 = revenue / total_assets

Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
```

**Note:** original published 5th coefficient is 0.999 (≈ 1.0). Sources differ between 0.99 and 1.0; either is acceptable. Use `1.0` for cleanliness.

**Zones:**
- `Z > 2.99` → safe
- `1.81 ≤ Z ≤ 2.99` → grey
- `Z < 1.81` → distress

`raw_value` is the continuous Z score. Optionally persist a sidecar column `altman_zone TEXT` ∈ {'safe','grey','distress'} for dashboard display — but the percentile-rank-on-Z is what feeds composite scoring, not the zone label.

**Note on variants:** spec says "with safe/grey/distress zones" — that is the original public-manufacturing Z. The Z' (private firms) and Z'' (non-manufacturers, emerging markets) variants exist but are NOT spec'd. Use original Z for v1 across all sectors. Discuss-phase candidate: should financials/utilities use Z'' instead? Defer.

### 4. Growth (SCORE-04) — 5 sub-factors

Source: `fundamental_ratios` and `fundamentals`.

| Sub-factor | Formula | Source |
|-----------|---------|--------|
| `grow_rev_yoy` | `fundamental_ratios.revenue_growth_yoy` | Pass-through. |
| `grow_earn_yoy` | `fundamental_ratios.earnings_growth_yoy` | Pass-through. |
| `grow_rev_accel` | `revenue_growth_yoy[latest] - revenue_growth_yoy[1 year ago]` | Acceleration of revenue growth. Need to recompute or store the prior-year `fundamental_ratios.revenue_growth_yoy` snapshot. |
| `grow_rd_intensity` | `fundamental_ratios.rd_intensity` | Pass-through. |
| `grow_fcf_yoy` | `(fcf[latest] - fcf[4 quarters ago]) / abs(fcf[4 quarters ago])` | Compute from `fundamentals.free_cash_flow`. Use `abs` in denominator to handle negative FCF prior period. |

### 5. Estimate Revisions (SCORE-05) — 3 sub-factors, degenerate-neutral

Source: `analyst_estimates` snapshot history (one row per `(ticker, snapshot_date)`).

| Sub-factor | Formula | Sufficient-history threshold |
|-----------|---------|------------------------------|
| `rev_30d` | `eps_fy1[asof] - eps_fy1[asof - 30 days]` | Need ≥ 1 snapshot at or before `asof - 30d`. |
| `rev_60d` | `eps_fy1[asof] - eps_fy1[asof - 60 days]` | Need ≥ 1 snapshot at or before `asof - 60d`. |
| `rev_90d` | `eps_fy1[asof] - eps_fy1[asof - 90 days]` | Need ≥ 1 snapshot at or before `asof - 90d`. |

**Degenerate-neutral rule:** If insufficient history for a sub-factor, set `raw_value = 0.0` (literal zero, not null), so the percentile rank within sector lands at the median (50.0) for that ticker — neutral signal. Persist `sufficient_history: bool = False` as an audit flag in a sidecar JSON column or in a parallel `factor_scores_audit` row.

**Why zero raw_value not NULL:** SCORE-05 explicitly mandates "degenerate-neutral score" — a NULL would be excluded from the rank and create a score gap. Operator wants the ticker to *stay in the running* with a neutral signal during the L1 snapshot bootstrap period (~30/60/90 days after first DATA-09 ingest).

**Implementation tip:** the closest snapshot ≤ `asof - N days` is found with `SELECT eps_fy1 FROM analyst_estimates WHERE ticker=? AND snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1`.

### 6. Short Interest (SCORE-06) — 3 sub-factors, side-aware

Source: `short_interest` daily snapshots.

| Sub-factor | Formula (long-side semantics) | Notes |
|-----------|-------------------------------|-------|
| `short_pct_float_inv` | `-short_percent_of_float[asof]` | **Sign-flipped**: high SI = bearish for longs. After negating, "higher raw_value = better for longs". |
| `short_dtc_inv` | `-short_ratio[asof]` (short_ratio IS days-to-cover in L1 schema) | Sign-flipped same logic. |
| `short_change_inv` | `-(short_percent_of_float[asof] - short_percent_of_float[asof - 30 days])` | **Declining SI is bullish for longs** → after negating change, "higher = better for longs". |

**Side-awareness pattern:** persist all three sub-factors as long-side semantics. At read time (in `portfolio/optimizers/conviction.py`), the short book reads `short_side_score = 100 - long_side_score` for each of the three SI sub-factors. This avoids duplicating rows in `factor_scores` and keeps the table single-perspective.

**Alternative (rejected):** persist a `side` column in `factor_scores`. Doubles the table size for one factor. Worse for queries.

### 7. Insider Activity (SCORE-07) — 3 sub-factors, P/S-only (CP3 binding)

Source: `insider_transactions`. **Every query MUST filter `transaction_code IN ('P','S')` for net flow OR `transaction_code = 'P'` for cluster.** A/M/F/G/D contribute zero. CP3 binding is the load-bearing rule.

| Sub-factor | SQL pattern |
|-----------|-------------|
| `ins_net_flow_90d` | `SELECT SUM(CASE WHEN transaction_code='P' THEN total_value WHEN transaction_code='S' THEN -total_value END) FROM insider_transactions WHERE ticker=? AND transaction_date BETWEEN date(?, '-90 days') AND ? AND transaction_code IN ('P','S')` |
| `ins_ceo_cfo_buys` | `SELECT 3 * SUM(total_value) FROM insider_transactions WHERE ticker=? AND transaction_code='P' AND is_officer=1 AND <CEO/CFO_TITLE_RE match> AND transaction_date BETWEEN date(?, '-90 days') AND ?` — uses the `CEO_CFO_TITLE_RE` already exported by `data/providers/edgar_provider.py`. The 3× multiplier is per spec — bake it into the raw_value before percentile rank. |
| `ins_cluster_buy_count` | `SELECT COUNT(DISTINCT insider_name) FROM insider_transactions WHERE ticker=? AND transaction_code='P' AND transaction_date BETWEEN date(?, '-30 days') AND ?` — already implemented as `data/insider.py::detect_cluster_buys()`. Reuse, don't duplicate. |

**No-data fallback:** spec says "sector-median fallback when no data". When all three sub-factor raw values are NULL for a ticker (no Form 4 activity in the 90d window), set `raw_value = sector_median(raw_value)` for each sub-factor. Implementation: compute sector median from non-null tickers in the same GICS sector first, then fill NULLs with that median, then percentile rank. This puts no-insider-data tickers at the sector midpoint instead of dragging them to the bottom of the rank.

**Why P/S only:** A (grants) and M (option exercises) are not directional — they happen on a vesting schedule. F (tax withholding) is a forced sale, not an opinion. G (gifts) and D (other dispositions) are non-economic. P (open-market purchase) and S (open-market sale) are the only codes where the insider is putting their *own discretionary capital* in or out of the stock. CP3 binding from project-level pitfalls research.

### 8. Institutional Flow (SCORE-08) — 3 sub-factors

Source: `institutional_holdings` (13F, period_end + filed_date distinct per D4).

| Sub-factor | Formula |
|-----------|---------|
| `inst_fund_count` | `SELECT COUNT(DISTINCT cik) FROM institutional_holdings WHERE ticker=? AND period_end = (SELECT MAX(period_end) FROM institutional_holdings WHERE ticker=?)` — number of tracked funds holding at the latest period_end. |
| `inst_net_change` | `SELECT SUM(change_shares × value_per_share) FROM institutional_holdings WHERE ticker=? AND period_end = latest_period_end` — net change in aggregate position value vs prior quarter. `change_shares` already computed at ingest by L1. |
| `inst_multi_fund_open_flag` | `1 if (SELECT COUNT(DISTINCT cik) FROM institutional_holdings WHERE ticker=? AND is_new_position=1 AND filed_date BETWEEN date(?, '-90 days') AND ?) >= 3 else 0` — flag if 3+ tracked funds opened a new position in last 90 days. Binary 0/1; percentile rank still applies (within-sector ranking of binary values still produces a 0–100 spread because of the equal-rank averaging method). |

**Latest period_end resolution:** use `MAX(period_end)` *as of asof_date* — i.e., `WHERE filed_date <= asof_date` so historical replays don't pull future-filed 13Fs. D4 binding from L1.

## Sector-Percentile-Rank Algorithm (SCORE-09)

The single load-bearing utility. Lives in `factors/sector_rank.py`.

### Pseudocode

```python
import numpy as np
from scipy.stats import rankdata
import pandas as pd

def compute_sector_percentile_rank(
    df: pd.DataFrame,  # columns: ticker, sector, raw_value
) -> pd.DataFrame:    # adds: percentile_rank ∈ [0, 100] or NaN
    """Within each sector, compute 0-100 percentile rank of raw_value.

    - NaN raw_values are excluded from ranking and remain NaN in output.
    - Tie handling: 'average' method (the scipy default).
    - Sectors with N=1 valid value: that ticker gets percentile_rank = 50.0
      (degenerate; rank-of-1 / 1 * 100 = 100 would be misleading at N=1).
    - Sectors with N=0 valid values: all NaN.
    """
    out = df.copy()
    out["percentile_rank"] = np.nan
    for sector, group in df.groupby("sector"):
        valid = group["raw_value"].dropna()
        n = len(valid)
        if n == 0:
            continue
        if n == 1:
            out.loc[valid.index, "percentile_rank"] = 50.0
            continue
        ranks = rankdata(valid.values, method="average")
        # Map ranks to (0, 100]: rank/n * 100. Lowest rank=1, highest=n.
        # Result: lowest gets 100/n, highest gets 100. Tie groups get the
        # mean rank within the tie group, so percentile is symmetric.
        pct = ranks / n * 100.0
        out.loc[valid.index, "percentile_rank"] = pct
    return out
```

### Why `method='average'`

[VERIFIED: scipy.stats.rankdata docs] — `'average'` assigns the average of the ranks that would have been assigned to all tied values to each value. Example: `rankdata([0, 2, 3, 2])` returns `[1.0, 2.5, 4.0, 2.5]`. This is the standard for cross-sectional factor rank in academic finance because it preserves rank symmetry around tie groups (alternatives `min` / `max` / `dense` / `ordinal` all introduce bias).

### Edge cases

| Case | Handling |
|------|----------|
| Sector has 1 ticker (e.g., utilities subsector with only 1 tracked name) | Assign `percentile_rank = 50.0` (neutral). Persist sidecar flag `n_in_sector = 1`. |
| Sector has 2 tickers | `rankdata([a, b])` → `[1, 2]` → percentile `[50, 100]`. The "loser" gets 50, not 0 — by design of `rank/n * 100` with `min_rank=1`. |
| All sector values NaN | Persist `percentile_rank = NULL`; downstream composer handles. |
| Single ticker has NaN raw_value but sector is valid | Persist `(raw_value=NULL, percentile_rank=NULL)`. Other tickers in sector ranked normally. |

### Composer (sub-factor → parent factor)

```python
def compute_parent_factor_score(subfactors_df: pd.DataFrame) -> pd.DataFrame:
    """Equal-weighted mean of sub-factor percentile ranks → parent factor score.

    Input:  rows of (ticker, factor, sub_factor, percentile_rank)
    Output: rows of (ticker, factor, parent_score) — equal-weighted mean.
    Skips NaN percentile_ranks: mean over non-null values.
    A ticker with all-NaN sub-factors for a parent factor yields parent_score=NaN.
    """
    return (
        subfactors_df
        .groupby(["ticker", "factor"], dropna=False)["percentile_rank"]
        .mean()  # pandas .mean() skips NaN by default
        .reset_index(name="parent_score")
    )
```

`SCORE-09` says "sub-factors equal-weighted within parent factor" — this is `pd.DataFrame.mean()` skipna-default, NOT a weighted average. Confirmed.

## `factor_scores` Schema Proposal (SCORE-10)

```sql
CREATE TABLE factor_scores (
    ticker          TEXT NOT NULL,
    score_date      TEXT NOT NULL,           -- ISO date
    factor          TEXT NOT NULL,           -- 'momentum', 'value', 'quality', 'growth',
                                             -- 'revisions', 'short_interest', 'insider', 'institutional'
    sub_factor      TEXT NOT NULL,           -- e.g. 'mom_12_1', 'val_fwd_ey', 'qual_piotroski_f', ...
                                             -- OR '__parent__' for the equal-weighted parent score
    raw_value       REAL,                    -- nullable; original unranked value
    percentile_rank REAL,                    -- nullable; 0-100 within GICS sector
    sector          TEXT NOT NULL,           -- denormalized for fast dashboard reads
    n_in_sector     INTEGER,                 -- count of valid (non-NaN) tickers used in rank
    sufficient_history INTEGER NOT NULL DEFAULT 1,  -- 0 = degenerate (e.g. SCORE-05 < 30d snapshots)
    computed_at     INTEGER NOT NULL,        -- unix epoch seconds
    PRIMARY KEY (ticker, score_date, factor, sub_factor)
);
CREATE INDEX idx_fs_score_date ON factor_scores(score_date);
CREATE INDEX idx_fs_ticker_date ON factor_scores(ticker, score_date);
CREATE INDEX idx_fs_factor_date ON factor_scores(factor, score_date);
CREATE INDEX idx_fs_sector_date ON factor_scores(sector, score_date);
```

### Design notes

- **PK `(ticker, score_date, factor, sub_factor)`** — idempotent: re-running today's scoring is a no-op via `INSERT OR REPLACE`. Yesterday's row has `score_date='2026-05-03'` so it is *never touched* by today's run. This delivers SC4 (replay any historical day's signal exactly).
- **Long-format, not wide** — 27 sub-factors as 27 rows, not 27 columns. Adding/removing a sub-factor in a future research milestone is a code change, not a schema migration. Dashboard pivots at read time via pandas `.pivot_table()`.
- **`__parent__` row convention** — for each (ticker, score_date, factor), one row has `sub_factor = '__parent__'` carrying the equal-weighted mean parent score. Dashboard queries `WHERE sub_factor = '__parent__'` for the 8-factor heatmap; queries omitting the filter get the full 27-sub-factor matrix.
- **Composite score lives separately** — the spec mentions "composite_score" for combined scoring (60/40 quant/Claude in Phase 4 ANAL-09). That belongs in a `combined_scores` table built by Phase 4, not `factor_scores`. Phase 2 stops at the 8 parent scores.
- **Denormalized `sector`** — yes, `universe.sector` is the SoT, but joining 27 × ~3000 rows × every dashboard query is wasteful. Snapshot the sector at compute-time. Trade-off: if a ticker reclassifies (e.g., XYZ moves from XLK to XLY), the historical `factor_scores.sector` reflects the sector *at the time of scoring*, which is exactly the PIT-correct behavior for replay.
- **Migration:** new file `migrations/versions/0003_create_factor_scores_table.py` with raw SQL via `op.execute()` per CONTEXT D-01.

### Batch insert pattern

```python
sql = """INSERT OR REPLACE INTO factor_scores
         (ticker, score_date, factor, sub_factor, raw_value, percentile_rank,
          sector, n_in_sector, sufficient_history, computed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
with conn:
    conn.executemany(sql, rows)  # rows is list[tuple], thousands at a time
```

Wrap in a single transaction (`with conn:`) — anti-pattern 2 in ARCHITECTURE.md ("many small writes inside a hot loop") would blow the 10-min budget by 100×.

## `run-scoring` CLI Surface

```
meridian run-scoring [--asof DATE] [--ticker T] [--sector S] [--factors F1,F2,...] [--dry-run]
```

| Flag | Default | Behavior |
|------|---------|----------|
| `--asof DATE` | `today` (UTC) | Score date. Reads L1 tables PIT-correctly (`fundamentals.as_of_ingest_date <= asof`, `analyst_estimates.snapshot_date <= asof`, etc.). |
| `--ticker T` | unset | Score only one ticker. Useful for debug. The sector percentile rank still runs against the full sector cohort (you can't rank a single ticker against itself). |
| `--sector S` | unset | Score only one GICS sector. Useful for incremental re-runs. |
| `--factors F1,F2` | all 8 | Comma-separated factor names. Default: all 8. |
| `--dry-run` | False | Compute but do not persist. For cost-free debug. |
| `--config PATH` | `./config.yaml` | Per Phase 0 D-23 shared CLI conventions. |

**Orchestration:**
```python
# cli/scoring_cmd.py — Typer subcommand
def run_scoring(asof: date, ticker: Optional[str], sector: Optional[str],
                factors: Optional[List[str]], dry_run: bool, config_path: Path):
    # 1. Open runs row (status=RUNNING, command='run-scoring')
    # 2. Load Config + open SQLite via db.get_connection()
    # 3. Resolve ticker list (universe filtered by --ticker / --sector)
    # 4. For each factor in factors:
    #       df = compute_X_factor(asof, conn, tickers)
    #       df = sector_rank.compute_sector_percentile_rank(df)
    # 5. Compose parent scores (composer.compute_parent_factor_score)
    # 6. If dry_run: log row count + bail. Else: batch INSERT OR REPLACE.
    # 7. Close runs row (status=OK or FAILED + error)
```

**Daily-refresh integration:** `cli/orchestrator.py daily-refresh` already chains `run-data → run-scoring → ...` (per ARCHITECTURE.md §9). Phase 2 wires the `run-scoring` step in. Phase 0 already shipped the orchestrator skeleton.

## Pitfalls

### Pitfall 1: Lookback window arithmetic (calendar vs trading days)
**What goes wrong:** `close[asof - timedelta(days=252)]` over a calendar year → returns the ~178-trading-day-back close, not the 252-trading-day-back close. Momentum factor is off by ~3 months.
**Why it happens:** 252 trading days/year, 365 calendar days/year. Holidays + weekends.
**How to avoid:** index `daily_prices` by trading-day position (`row_number() over (...)` in SQL or `.iloc[-N]` in pandas after sorting), not calendar date arithmetic.
**Warning sign:** momentum scores wildly different from quarter to quarter for the same stable name.

### Pitfall 2: Including A/M/F/G/D codes in insider net flow (CP3 violation)
**What goes wrong:** the insider score becomes noise because option exercises (M) and tax withholdings (F) flood the signal with non-discretionary trades.
**Why it happens:** developer writes `SELECT SUM(total_value) FROM insider_transactions WHERE ...` without the code filter — SQL accepts it silently.
**How to avoid:** every insider query in `factors/insider.py` MUST have `WHERE transaction_code IN ('P','S')` (net flow) or `transaction_code = 'P'` (cluster / CEO-CFO buys). Add a unit test that inserts one of each of P/S/A/M/F/G/D and asserts the factor reads only P+S — mirror the `test_cluster_buys_p_only` test from L1 phase 1.
**Warning sign:** insider net flow correlates 0.3+ with options-vesting calendar quarters (Mar/Jun/Sep/Dec).
**Binding:** **CP3 — Form 4 misclassification, factor side**. SC2 in Phase 2 ROADMAP.

### Pitfall 3: NaN-handling in percentile rank
**What goes wrong:** `rankdata([NaN, 1, 2, 3])` returns `[2.5, 1, 2.5, 4]` because scipy treats NaN as a value. Total nonsense rank.
**Why it happens:** developer forgets to `.dropna()` before `rankdata`.
**How to avoid:** the `sector_rank.py` utility above explicitly calls `valid = group["raw_value"].dropna()` before rankdata. Unit test: insert one NaN ticker into a sector and assert the NaN ticker comes out with `percentile_rank = NULL`, and other tickers' ranks are computed against the smaller cohort.

### Pitfall 4: Small-sector edge cases (N=1, N=2)
**What goes wrong:** at N=1 the lone ticker gets percentile = 100 (rank 1 / N 1 × 100). That's the *highest* possible score for a sector with only one valid ticker — completely misleading.
**Why it happens:** naive `rank/n*100` formula without N-guard.
**How to avoid:** explicit N-guard in `sector_rank.py` — at N=1, return 50.0 (neutral). At N=0, return NaN. At N≥2, use the formula. Documented above in pseudocode.
**Warning sign:** a single utility-sector micro-cap shows up at #1 across all 8 factors despite being a no-op stock.

### Pitfall 5: Forgetting `INSERT OR REPLACE` and getting integrity errors on rerun
**What goes wrong:** developer uses plain `INSERT INTO factor_scores ...`; second run on same `asof` raises `sqlite3.IntegrityError: UNIQUE constraint failed`.
**Why it happens:** the PK is `(ticker, score_date, factor, sub_factor)` and the rerun is hitting the same key.
**How to avoid:** use `INSERT OR REPLACE` for idempotent same-day reruns. *Do not* use `INSERT OR IGNORE` — that would silently skip rewriting rows when an upstream L1 fix produces a different raw_value, creating stale-score bugs.

### Pitfall 6: Wrong sign convention on inverted ratios (D/E, accruals, short interest)
**What goes wrong:** debt-to-equity should rank LOW = HIGH score (less leveraged is better quality). Forgetting to negate raw_value before percentile rank means LOW D/E gets LOW percentile = LOW score. Quality scores are inverted from reality.
**Why it happens:** "higher = better" is the universal convention assumed by the percentile-rank utility, but D/E, accruals, and short interest are "lower = better" in their natural form.
**How to avoid:** the cookbook above explicitly negates `raw_value` for `qual_de_inv`, `qual_accruals_inv`, `short_pct_float_inv`, `short_dtc_inv`, `short_change_inv` *before persistence*. The `_inv` suffix is the audit trail for "I have already inverted; the percentile rank is correct as-stored". Unit test: a clearly low-leverage company should land in the 90th percentile of `qual_de_inv` for its sector.

### Pitfall 7: Estimate-revisions degenerate scoring (SCORE-05) — picking the wrong neutral
**What goes wrong:** during the L1 snapshot bootstrap (first ~30/60/90 days after `data/estimates.py` first runs), `eps_fy1[asof - 90d]` simply doesn't exist for many tickers. If the function returns `None`/NULL, those tickers are excluded from the rank → they have *no* score → portfolio composer drops them.
**Why it happens:** intuition says "no data → null score". But the spec mandates "degenerate-neutral" specifically because dropping is wrong.
**How to avoid:** when prior snapshot doesn't exist for the lookback window, return `raw_value = 0.0` (literal zero), not NULL. After percentile rank, the ticker lands near the median of the "no-data cohort" (which is everyone else early on, so ~50). Persist `sufficient_history = 0` as audit. After ~90 days of L1 snapshot history accrual, this branch flips to false naturally.

### Pitfall 8: PIT (point-in-time) violations on fundamentals lookups
**What goes wrong:** `SELECT * FROM fundamentals WHERE ticker=? ORDER BY period_end DESC LIMIT 1` reads the *latest* fundamentals row regardless of when it was *ingested*. If you're scoring asof=2025-09-15 and there's a row with `period_end=2025-09-30, as_of_ingest_date=2025-11-01`, the query returns it — but on 2025-09-15 we did not yet know that row existed.
**Why it happens:** ignoring the `as_of_ingest_date` column that L1's D2 mitigation added specifically to prevent this.
**How to avoid:** every fundamentals read in Phase 2 must filter `as_of_ingest_date <= asof`. The pattern is already implemented in `data/ratios.py::_latest_per_period` — copy it. For backtesting / replay correctness this is non-negotiable.

## Runtime State Inventory

This is a greenfield phase (no rename / refactor). No runtime state to inventory. New schema artifact (`factor_scores` table) created by a fresh migration; no data to migrate from prior state.

## Environment Availability

No external dependencies new to this phase. All inputs are SQLite tables populated by L1; all libraries (scipy, pandas, numpy, structlog, typer) are pinned and installed by Phase 0.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | All Phase 2 code | ✓ (Phase 0) | 3.11+ | — |
| scipy | sector_rank.py rankdata | ✓ (Phase 0) | 1.17.x | — |
| pandas | factor compute | ✓ (Phase 0) | >=2.2,<3.0 | — |
| numpy | percentile math | ✓ (Phase 0) | >=2.0,<2.5 | — |
| SQLite | L1 reads + factor_scores write | ✓ (Phase 0) | stdlib | — |
| Populated L1 tables | factor inputs | ✓ (Phase 1 verified 14/14 reqs) | — | — |

No missing dependencies.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (existing from Phase 0) |
| Quick run command | `uv run pytest tests/unit/factors/ -x` |
| Full suite command | `uv run pytest tests/ -x` |
| Phase gate | `uv run pytest tests/integration/test_phase2_smoke.py` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCORE-01 | Momentum 12-1 skip-month math correct | unit | `pytest tests/unit/factors/test_momentum.py::test_mom_12_1_skips_last_month` | ❌ Wave 0 |
| SCORE-01 | All 6 momentum sub-factors produced | unit | `pytest tests/unit/factors/test_momentum.py::test_six_subfactors_emitted` | ❌ Wave 0 |
| SCORE-02 | Forward EY uses analyst_estimates.eps_fy1 latest snapshot ≤ asof | unit | `pytest tests/unit/factors/test_value.py::test_fwd_ey_pit_correct` | ❌ Wave 0 |
| SCORE-03 | Piotroski F is 0–9 integer; all 9 binary checks present | unit | `pytest tests/unit/factors/test_quality.py::test_piotroski_nine_checks` | ❌ Wave 0 |
| SCORE-03 | Altman Z formula `1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5` | unit | `pytest tests/unit/factors/test_quality.py::test_altman_z_formula` | ❌ Wave 0 |
| SCORE-03 | Altman zone classification at boundaries (1.81, 2.99) | unit | `pytest tests/unit/factors/test_quality.py::test_altman_zones` | ❌ Wave 0 |
| SCORE-03 | qual_de_inv sign-flip: low D/E → high percentile | unit | `pytest tests/unit/factors/test_quality.py::test_de_inv_sign` | ❌ Wave 0 |
| SCORE-03 | qual_accruals_inv sign-flip: low accruals → high percentile | unit | `pytest tests/unit/factors/test_quality.py::test_accruals_inv_sign` | ❌ Wave 0 |
| SCORE-04 | Growth sub-factors read from fundamental_ratios | unit | `pytest tests/unit/factors/test_growth.py::test_five_subfactors` | ❌ Wave 0 |
| SCORE-05 | **Degenerate-neutral: insufficient history → raw_value=0.0, sufficient_history=0** | unit | `pytest tests/unit/factors/test_revisions.py::test_degenerate_neutral_zero` | ❌ Wave 0 |
| SCORE-05 | After 90+ days history, real revision delta computed | unit | `pytest tests/unit/factors/test_revisions.py::test_revision_after_history_accrues` | ❌ Wave 0 |
| SCORE-06 | **Side-aware: short_pct_float_inv stored as -short_pct_float; high SI → low long-side score** | unit | `pytest tests/unit/factors/test_short.py::test_high_si_low_long_score` | ❌ Wave 0 |
| SCORE-06 | Short-side score = 100 - long-side score (read-time derivation) | unit | `pytest tests/unit/factors/test_short.py::test_short_side_inverts` | ❌ Wave 0 |
| SCORE-07 | **CP3 BINDING: net flow query filters `transaction_code IN ('P','S')` only** | unit | `pytest tests/unit/factors/test_insider.py::test_net_flow_p_s_only_CP3_binding` | ❌ Wave 0 |
| SCORE-07 | A/M/F/G/D codes contribute zero to net flow | unit | `pytest tests/unit/factors/test_insider.py::test_amfgd_codes_zero_contribution` | ❌ Wave 0 |
| SCORE-07 | Cluster-buy uses `transaction_code='P'` only (DISTINCT insider count) | unit | `pytest tests/unit/factors/test_insider.py::test_cluster_buy_p_only` | ❌ Wave 0 |
| SCORE-07 | CEO/CFO buys carry 3× weight | unit | `pytest tests/unit/factors/test_insider.py::test_ceo_cfo_3x_weight` | ❌ Wave 0 |
| SCORE-07 | Sector-median fallback when ticker has no insider data | unit | `pytest tests/unit/factors/test_insider.py::test_sector_median_fallback` | ❌ Wave 0 |
| SCORE-08 | Multi-fund-opening flag fires at 3+ tracked funds in 90d | unit | `pytest tests/unit/factors/test_institutional.py::test_multi_fund_open_flag_3plus` | ❌ Wave 0 |
| SCORE-08 | inst_net_change uses period_end ≤ asof (D4 PIT-correct) | unit | `pytest tests/unit/factors/test_institutional.py::test_pit_correct_period_end` | ❌ Wave 0 |
| SCORE-09 | Percentile rank uses scipy.stats.rankdata(method='average') | unit | `pytest tests/unit/factors/test_sector_rank.py::test_average_method` | ❌ Wave 0 |
| SCORE-09 | Equal-weighted parent score = mean of sub-factor percentile ranks | unit | `pytest tests/unit/factors/test_sector_rank.py::test_parent_equal_weighted` | ❌ Wave 0 |
| SCORE-09 | NaN raw_value excluded from rank | unit | `pytest tests/unit/factors/test_sector_rank.py::test_nan_excluded` | ❌ Wave 0 |
| SCORE-09 | N=1 sector → percentile_rank=50.0 | unit | `pytest tests/unit/factors/test_sector_rank.py::test_n1_neutral_50` | ❌ Wave 0 |
| SCORE-09 | Tie handling: equal raw_values get equal percentile_rank | unit | `pytest tests/unit/factors/test_sector_rank.py::test_ties_get_average_rank` | ❌ Wave 0 |
| SCORE-10 | factor_scores PK is (ticker, score_date, factor, sub_factor); rerun is idempotent | unit | `pytest tests/unit/factors/test_persist.py::test_idempotent_rerun_same_asof` | ❌ Wave 0 |
| SCORE-10 | Historical asof_date NOT overwritten by today's rerun | integration | `pytest tests/integration/test_phase2_smoke.py::test_historical_replay_preserved_SC4` | ❌ Wave 0 |
| SCORE-10 | All 27 sub-factors + 8 parent rows persisted per ticker per asof | integration | `pytest tests/integration/test_phase2_smoke.py::test_all_27_subfactors_plus_8_parents` | ❌ Wave 0 |
| Phase gate | `meridian run-scoring` produces non-empty factor_scores table for full universe | integration | `pytest tests/integration/test_phase2_smoke.py::test_run_scoring_full_universe` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit/factors/ -x` (target: < 10 seconds)
- **Per wave merge:** `uv run pytest tests/ -x` (target: < 30 seconds; current Phase 1 baseline 7.88s for 278 tests)
- **Phase gate:** Full suite green + `tests/integration/test_phase2_smoke.py` all-green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/factors/__init__.py` — package marker
- [ ] `tests/unit/factors/conftest.py` — shared fixtures: synthetic universe (5 sectors × 6 tickers), synthetic L1 tables (prices, fundamentals, ratios, insider, institutional, short, estimates) populated for asof=2026-05-04 with 252 days lookback
- [ ] `tests/unit/factors/test_momentum.py`
- [ ] `tests/unit/factors/test_value.py`
- [ ] `tests/unit/factors/test_quality.py`
- [ ] `tests/unit/factors/test_growth.py`
- [ ] `tests/unit/factors/test_revisions.py`
- [ ] `tests/unit/factors/test_short.py`
- [ ] `tests/unit/factors/test_insider.py`
- [ ] `tests/unit/factors/test_institutional.py`
- [ ] `tests/unit/factors/test_sector_rank.py`
- [ ] `tests/unit/factors/test_persist.py`
- [ ] `tests/integration/test_phase2_smoke.py` — closure-gate: 4 ROADMAP SCs as automated tests

## Project Constraints (from CLAUDE.md)

| Directive | Source | Application to Phase 2 |
|-----------|--------|------------------------|
| pandas pinned `>=2.2,<3.0` | CLAUDE.md Watch List | Use `df.mean()` (skipna=True default in 2.x); avoid pandas 3.0 inplace-return changes |
| numpy pinned `>=2.0,<2.5` | CLAUDE.md Watch List | numpy 2 ABI break is past; statsmodels not used in this phase |
| scipy pinned `>=1.16,<1.18` | CLAUDE.md | scipy.stats.rankdata API stable across the pin |
| Raw SQL only (no SQLAlchemy ORM) | CLAUDE.md Stack table; CONTEXT D-01 | All factor SQL is hand-written; migrations use `op.execute()` |
| Migrations via `op.execute()` only | CONTEXT D-01 | New `0003_create_factor_scores_table.py` migration |
| Single SQLite writer | ARCHITECTURE.md | Phase 2's run-scoring CLI is the only writer to factor_scores; dashboard reads only |
| Audit-grade persistence | CLAUDE.md "audit is a spec requirement" | factor_scores has computed_at + sufficient_history; runs row records every scoring invocation |
| structlog for all logging | CLAUDE.md; CONTEXT D-19 | `log = structlog.get_logger(__name__)` per module; bind run_id at CLI entry |
| Typer for CLI | CONTEXT D-23 | run-scoring is a Typer subcommand of the existing `meridian` app |
| ta-lib / pandas-ta forbidden | CLAUDE.md anti-recs | Momentum is `pct_change` and trading-day-position indexing only |

## Code Examples

Verified patterns reused from existing project code:

### Cluster-buy P-only filter (already implemented; reuse)
```python
# Source: src/ls_equity_fund/data/insider.py [VERIFIED: read this session]
rows = conn.execute(
    """SELECT ticker, COUNT(DISTINCT insider_name) AS distinct_insiders, ...
       FROM insider_transactions
       WHERE transaction_code = 'P'
         AND transaction_date BETWEEN ? AND ?
       GROUP BY ticker
       HAVING distinct_insiders >= ?""",
    (start, end, min_insiders),
)
```

### PIT-correct fundamentals lookup (pattern to copy)
```python
# Source: src/ls_equity_fund/data/ratios.py::_latest_per_period [VERIFIED: read this session]
sql = """
    WITH latest AS (
        SELECT ticker, period_end, period_type, MAX(as_of_ingest_date) AS aoid
        FROM fundamentals
        WHERE ticker = ?
          AND period_type = ?
          AND as_of_ingest_date <= ?
          AND period_end <= ?
        GROUP BY ticker, period_end, period_type
    )
    SELECT f.* FROM fundamentals f
    JOIN latest l USING (ticker, period_end, period_type)
    WHERE f.as_of_ingest_date = l.aoid
    ORDER BY f.period_end DESC
    LIMIT ?
"""
```

### Sector-percentile rank with NaN safety (new pattern)
```python
# New code for src/ls_equity_fund/factors/sector_rank.py [CITED: scipy docs]
import numpy as np
from scipy.stats import rankdata

def percentile_rank_within(values: np.ndarray) -> np.ndarray:
    """0-100 percentile rank. NaN-safe. method='average' for ties."""
    out = np.full(values.shape, np.nan)
    mask = ~np.isnan(values)
    n = mask.sum()
    if n == 0:
        return out
    if n == 1:
        out[mask] = 50.0
        return out
    out[mask] = rankdata(values[mask], method='average') / n * 100.0
    return out
```

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | EV/EBITDA can substitute EV/EBIT (D&A not stored in L1 fundamentals) | Value cookbook | Modest — EV/EBIT is a known second-best for sectors with low D&A; flag for discuss |
| A2 | Total debt = `long_term_debt` only (short-term debt not in L1 schema) | Value cookbook | Low — at the daily-bar / annual-fundamentals cadence the difference is marginal for most names |
| A3 | Altman original Z formula is appropriate across all GICS sectors (not Z' or Z'') | Quality cookbook | Medium — financials/utilities have different operating-leverage assumptions; flag for discuss |
| A4 | Estimate-revisions degenerate-neutral = `raw_value = 0.0` (not NULL, not 50) | EstRev cookbook | Low — both designs satisfy "neutral"; 0.0 keeps the ticker in the rank cohort which is the spirit of "degenerate-neutral until history accrues" |
| A5 | Sub-factor `sufficient_history` flag persisted on every row (not just SCORE-05) | Schema | Low — keeps schema uniform; only SCORE-05 currently uses the flag, others always 1 |
| A6 | Side-awareness for SCORE-06 implemented as `100 - long_side_score` at read time, not as a `side` column | ShortInt cookbook | Low — alternative is more rows; chosen design is simpler and matches the optimizer's per-side read pattern |
| A7 | Multi-fund-opening flag is binary 0/1, percentile-ranked within sector | InstFlow cookbook | Low — within-sector ranking of binary values still produces a 0–100 spread because of equal-rank averaging |

**These assumptions should be confirmed in `/gsd-discuss-phase` before planning locks.** Particularly A1 (EV/EBIT vs EV/EBITDA) and A3 (Altman variant choice) — both are factor-construction choices the operator may have a strong opinion on.

## Open Questions

1. **Should `combined_scores` (60% quant + 40% Claude per ANAL-09) live in `factor_scores` or a separate table?**
   - What we know: ANAL-09 is Phase 4. The composite-score concept is mentioned in ROADMAP but spec-mandated table is unspecified.
   - What's unclear: schema ownership.
   - Recommendation: put it in a separate `combined_scores` table built by Phase 4. Phase 2 stops at the 8 parent factor scores. Keep concerns separated by phase.

2. **Phase 2 ROADMAP doesn't say "operator runs `run-scoring`" verbatim — should the CLI subcommand be named `run-scoring` or `score`?**
   - What we know: Phase 0 D-23 lists `run-scoring` as the planned Typer subcommand name; CLI stub already exists per Phase 0.
   - Recommendation: stick with `run-scoring` for consistency with the `run-data`, `run-portfolio`, `run-execution`, `run-reporting` family.

3. **Are sub-factor weights truly equal across all 27, or did the spec say "equal within parent factor"?**
   - What we know: SCORE-09 says "sub-factors equal-weighted within parent factor" — explicitly within-parent.
   - Composer rule: each parent factor is the equal-weighted mean of *its own* sub-factors. The 8 parent factors themselves can carry different weights when forming a composite (those weights live in `config.factors.weights` per ARCHITECTURE.md §10 — but composite construction is Phase 4's ANAL-09 problem, not Phase 2's).
   - No ambiguity for Phase 2. Logging here for cross-phase clarity.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pandas.DataFrame.rank(pct=True)` | `scipy.stats.rankdata(method='average') / n * 100` | scipy is already a project dep for L4 SLSQP; explicit `method='average'` is more transparent than pandas' `method='average'` (which is also default but easier to forget) | Cleaner code, identical numerics |
| Carrying `side` column on factor table | Compute `100 - long_score` for short-side at read time | Saves ~30% storage on a 27-sub-factor × 3000-ticker × 365-day table | Simpler schema |
| Hardcoding sub-factor list as columns | Long-format with `sub_factor TEXT` column | Schema evolution: adding a sub-factor is code-only, not migration | Future-proof |

**Deprecated / outdated:**
- `pandas-ta` / `ta-lib` for technical indicators — never adopt; momentum is `pct_change` not 200 indicators (CLAUDE.md anti-rec) [CITED: CLAUDE.md anti-recs]

## Sources

### Primary (HIGH confidence)
- [scipy.stats.rankdata — SciPy v1.17.0 Manual](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.rankdata.html) — verified `method='average'` is default and tie-handling semantics
- [Altman Z-score — Wikipedia](https://en.wikipedia.org/wiki/Altman_Z-score) — verified original formula `1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 0.999·X5` and zone thresholds (>2.99 safe, 1.81–2.99 grey, <1.81 distress)
- [Piotroski F-score — Wikipedia](https://en.wikipedia.org/wiki/Piotroski_F-score) — verified 9-criteria structure (4 profitability + 3 leverage/liquidity + 2 efficiency)
- [Altman's Z-Score Model — Corporate Finance Institute](https://corporatefinanceinstitute.com/resources/commercial-lending/altmans-z-score-model/) — corroborated five-component formula and X1–X5 definitions
- [Piotroski F-Score — StableBread](https://stablebread.com/piotroski-f-score/) — corroborated 9 binary checks with explicit accruals-quality and shares-issued criteria
- `.planning/research/ARCHITECTURE.md` (this project) — module layout, schema conventions, CLI patterns
- `.planning/research/STACK.md` (this project) — pinned versions, anti-recs
- `migrations/versions/0002_create_phase1_tables.py` (this project) — L1 schema columns and CHECK constraints
- `src/ls_equity_fund/data/ratios.py` (this project) — PIT-correct fundamentals lookup pattern, 24 ratios available
- `src/ls_equity_fund/data/insider.py` (this project) — CP3 P-only filter pattern already in production for cluster-buy
- `.planning/phases/01-data-infrastructure-l1/01-VERIFICATION.md` (this project) — confirms L1 deliverables (CP1, CP3 ingest, D2, D4, all 14 DATA-* satisfied)

### Secondary (MEDIUM confidence)
- [Jegadeesh & Titman (1993) — SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1919226) — 12-1 momentum canonical reference; one-month skip lag rationale (short-term mean reversion contamination)
- [Momentum: 30 years after Jegadeesh & Titman — Springer](https://link.springer.com/article/10.1007/s11408-022-00417-8) — corroborates the skip-lag standard practice
- [Altman Z-Score — Wall Street Prep](https://www.wallstreetprep.com/knowledge/altman-z-score/) — formula corroboration
- [Altman Z-Score Insolvency Predictor for Non-Manufacturers (Z'') — CreditGuru](https://www.creditguru.com/index.php/bankruptcy-and-insolvency/altman-z-score-insolvency-predictor-for-non-manufacturers-emerging-markets) — variant context (we use original Z, not Z'', for v1)

### Tertiary (LOW confidence — not relied on)
- None — every recommendation is HIGH or MEDIUM.

## Metadata

**Confidence breakdown:**
- Standard stack (scipy/pandas/numpy versions): HIGH — pinned by Phase 0, verified
- Factor formulas (Piotroski, Altman, 12-1 momentum, percentile rank): HIGH — multi-source verified, canonical academic references
- Sub-factor input field choice (e.g., EV using LTD only): MEDIUM — pragmatic given L1 schema; flagged as A1, A2 assumptions
- factor_scores schema design: HIGH — derives directly from SCORE-09/SCORE-10 + Phase 0 conventions (raw SQL, idempotent PK)
- run-scoring CLI surface: HIGH — extends established Phase 0 + Phase 1 CLI pattern
- Pitfalls: HIGH — CP3 binding from project-level pitfalls research; PIT pattern mirrors L1's already-verified pattern in `data/ratios.py`

**Research date:** 2026-05-04
**Valid until:** 2026-06-04 (30 days — stable factor literature; scipy/pandas pinned)

---
*Research for Phase 2 — Scoring Engine (L2)*
*Researcher: gsd-phase-researcher*
*Researched: 2026-05-04*
