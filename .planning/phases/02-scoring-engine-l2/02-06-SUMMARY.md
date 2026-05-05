---
phase: 02-scoring-engine-l2
plan: 06
type: summary
requirements:
  - SCORE-05
  - SCORE-09
---

## Summary

Implemented the SCORE-05 estimate-revisions factor in
`src/ls_equity_fund/factors/revisions.py`.

The factor emits exactly three sub-factors per ticker:

- `rev_30d`
- `rev_60d`
- `rev_90d`

Each sub-factor computes the point-in-time delta between the latest
`eps_fy1` snapshot at or before `asof` and the closest `eps_fy1` snapshot at or
before the calendar lookback date.

## A4 / Pitfall 7 Binding

The degenerate-neutral branch is explicit: when the current snapshot or prior
lookback snapshot is missing, the factor returns literal `raw_value=0.0`, not
NaN, and sets `sufficient_history=0`.

This keeps tickers with insufficient analyst-estimate history inside the
sector-percentile cohort so they rank near neutral instead of being dropped.
Real revision deltas set `sufficient_history=1`.

## Output Schema Extension

Unlike the earlier three-column raw factor modules, revisions returns:

```text
ticker, sub_factor, raw_value, sufficient_history
```

Plan 02-10 orchestration must preserve this column when present and pass it to
`factor_scores.sufficient_history`. Factors that do not emit the column should
continue to default to `1`.

## Verification

- `uv run pytest tests/unit/factors/test_revisions.py -q`
- `uv run ruff check src/ls_equity_fund/factors/revisions.py tests/unit/factors/test_revisions.py`
