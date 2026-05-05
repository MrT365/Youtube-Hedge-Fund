# Phase 2 Plan 02-03 Summary

Implemented SCORE-02 value factor in `src/ls_equity_fund/factors/value.py`.

## Sub-factors

- `val_fwd_ey = analyst_estimates.eps_fy1 / close`
- `val_bp = total_equity / (close * shares_outstanding)`
- `val_fcf_yield = fundamental_ratios.fcf_yield`
- `val_ev_ebit_inv = ebit / enterprise_value`
- `val_shareholder_yield = dividend_yield + buyback_yield`
- `val_sales_ev = revenue / enterprise_value`

`enterprise_value = market_cap + long_term_debt - cash_and_equivalents`.

## PIT Helpers

The value factor uses the canonical Phase 2 PIT helpers from `factors/_pit.py`:

- `latest_fundamentals_pit`
- `latest_estimates_pit`
- `latest_close_pit`

These helpers centralize as-of-safe fundamentals, estimates, and close lookups for reuse by 02-04, 02-05, and 02-06.

## Encoded Assumptions

- A1: EV earnings yield uses EBIT over EV because L1 fundamentals do not store depreciation and amortization.
- A2: EV uses long-term debt only because L1 fundamentals do not store short-term debt.

## Tests

Added focused unit coverage in `tests/unit/factors/test_value.py` for:

- exact six-subfactor long-format output
- PIT-correct forward earnings yield
- missing-estimate NaN behavior
- book-to-price, FCF yield, EV/EBIT inverse, shareholder yield, and sales/EV formulas
- missing EV component NaN propagation
- registry wiring
- source invariants for PIT helper usage and auditable sub-factor naming
