# 02-09 Summary — Institutional Flow Factor

**Completed:** 2026-05-05

## Scope

Implemented `SCORE-08` institutional-flow factor in `src/ls_equity_fund/factors/institutional.py`.

## Sub-Factors

- `inst_fund_count`: distinct tracked-fund CIK count at latest PIT-visible `period_end`
- `inst_net_change`: sum of `change_shares * (value_usd / shares)` at latest PIT-visible `period_end`
- `inst_multi_fund_open_flag`: binary 1 when 3+ tracked funds opened positions within the last 90 calendar days

## PIT Binding

Latest 13F period is resolved with `MAX(period_end)` restricted to `filed_date <= asof`, preserving the 13F filing lag and preventing future-filed data from entering historical scores.

## Verification

- `uv run pytest tests/unit/factors/test_institutional.py -q`
- `uv run ruff check src/ls_equity_fund/factors/institutional.py tests/unit/factors/test_institutional.py`
