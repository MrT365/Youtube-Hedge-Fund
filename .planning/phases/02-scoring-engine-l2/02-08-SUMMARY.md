# 02-08 Summary — Insider Factor

**Completed:** 2026-05-05

## Scope

Implemented Phase 2 SCORE-07 insider activity factor:

- `src/ls_equity_fund/factors/insider.py`
- `tests/unit/factors/test_insider.py`

## Sub-Factors

`compute_insider(conn, asof, tickers)` returns long-format rows with columns:

- `ticker`
- `sub_factor`
- `raw_value`
- `sufficient_history`

Three sub-factors are emitted per ticker:

- `ins_net_flow_90d` = P-code dollar value minus S-code dollar value over the last 90 calendar days.
- `ins_ceo_cfo_buys` = 3x weighted P-code purchases by officers whose title matches `CEO_CFO_TITLE_RE`.
- `ins_cluster_buy_count` = distinct P-code insider names over the last 30 calendar days.

## Invariants

- Net flow SQL filters `transaction_code IN ('P','S')`.
- CEO/CFO buys and cluster buys SQL filter `transaction_code = 'P'`.
- A/M/F/G/D Form 4 codes contribute zero to directional raw values.
- Tickers with no directional insider data receive same-sector median fallback values and `sufficient_history=0`.
- `compute_insider` is registered at import time through `@register_factor("insider")`.

## Verification

- `uv run pytest tests/unit/factors/test_insider.py -q`
- `uv run ruff check src/ls_equity_fund/factors/insider.py tests/unit/factors/test_insider.py`
