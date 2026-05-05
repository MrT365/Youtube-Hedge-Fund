# 02-05 Summary — Growth Factor

**Completed:** 2026-05-05

## Scope

Implemented SCORE-04 growth factor:

- `src/ls_equity_fund/factors/growth.py`
- `tests/unit/factors/test_growth.py`

## Growth Formulas

`compute_growth(conn, asof, tickers)` returns long-format rows with columns:

- `ticker`
- `sub_factor`
- `raw_value`

Five sub-factors are emitted per ticker:

- `grow_rev_yoy` = `fundamental_ratios.revenue_growth_yoy`
- `grow_earn_yoy` = `fundamental_ratios.earnings_growth_yoy`
- `grow_rev_accel` = `revenue_growth_yoy[latest] - revenue_growth_yoy[1 year ago]`
- `grow_rd_intensity` = `fundamental_ratios.rd_intensity`
- `grow_fcf_yoy` = `(fcf[latest] - fcf[4 quarters ago]) / abs(fcf[4 quarters ago])`

## Invariants

- `compute_growth` is registered at import time through `@register_factor("growth")`.
- Ratio snapshots are read point-in-time using latest rows at or before the scoring date.
- FCF YoY uses `latest_fundamentals_pit` for quarterly fundamentals and uses `abs(prior_fcf)` in the denominator.
- Missing prior ratio or FCF history still emits the canonical row with `NaN`.
- Growth only emits raw values. Sector percentile ranking stays in `sector_rank.py`.

## Verification

- `uv run pytest tests/unit/factors/test_growth.py -q`
  - 11 passed
- `uv run --extra dev ruff check src/ls_equity_fund/factors/growth.py tests/unit/factors/test_growth.py`
  - All checks passed
