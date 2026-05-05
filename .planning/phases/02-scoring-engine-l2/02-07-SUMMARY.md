---
phase: 02-scoring-engine-l2
plan: 07
subsystem: scoring
tags: [factors, short-interest, sqlite, pandas]
requires:
  - phase: 01-data-infrastructure-l1
    provides: short_interest snapshots with short_percent_of_float and short_ratio
provides:
  - SCORE-06 short-interest factor with three long-side semantic sub-factors
  - PIT-bounded short_interest lookups at or before scoring dates
  - Unit coverage for sign-flipped short-interest raw values
affects: [scoring-engine-l2, portfolio-construction, risk-management]
tech-stack:
  added: []
  patterns: [registered factor module, long-format factor output, PIT snapshot lookup]
key-files:
  created:
    - src/ls_equity_fund/factors/short_interest.py
    - tests/unit/factors/test_short_interest.py
  modified: []
key-decisions:
  - "Used user-requested module path short_interest.py instead of plan's original short.py."
  - "Persisted all SCORE-06 raw values in long-side semantics with _inv audit suffixes."
patterns-established:
  - "Short-side scoring is derived later as 100 - long-side score; no side column is emitted."
requirements-completed: [SCORE-06, SCORE-09]
duration: 5min
completed: 2026-05-05
---

# Phase 02-07: Short-Interest Factor Summary

**Short-interest factor emits three sign-flipped, PIT-correct long-side sub-factors for sector-neutral scoring.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-05-05T08:48:44Z
- **Completed:** 2026-05-05T08:53:06Z
- **Tasks:** 1
- **Files modified:** 3

## Accomplishments

- Added `compute_short_interest(conn, asof, tickers)` registered as `short_interest`.
- Emitted exactly `short_pct_float_inv`, `short_dtc_inv`, and `short_change_inv` with long-side semantics.
- Added focused unit tests for output shape, PIT cutoff behavior, NaN behavior, registry binding, and sign convention.

## Files Created/Modified

- `src/ls_equity_fund/factors/short_interest.py` - SCORE-06 short-interest computation.
- `tests/unit/factors/test_short_interest.py` - Unit tests for the short-interest factor.
- `.planning/phases/02-scoring-engine-l2/02-07-SUMMARY.md` - Execution summary.

## Decisions Made

- Followed the user-requested `short_interest.py` module path instead of the plan's original `short.py` filename.
- Reused `universe_tickers` from `_pit.py` so ticker resolution matches existing Phase 2 helpers.
- Returned `NaN` for unavailable short-interest values while still emitting all three rows per ticker.

## Deviations from Plan

The original plan named `src/ls_equity_fund/factors/short.py` and `tests/unit/factors/test_short.py`; the user explicitly requested `short_interest.py` and `test_short_interest.py`. No behavioral deviation from SCORE-06.

## Issues Encountered

None.

## Verification

- `uv run pytest tests/unit/factors/test_short_interest.py -q`
- `uv run --extra dev ruff check src/ls_equity_fund/factors/short_interest.py tests/unit/factors/test_short_interest.py`

## User Setup Required

None.

## Next Phase Readiness

The factor is available through `FACTOR_REGISTRY["short_interest"]` after module import and is ready for scoring orchestration and later short-side derivation.

---
*Phase: 02-scoring-engine-l2*
*Completed: 2026-05-05*
