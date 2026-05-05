# 02-04 Summary — Quality Factor

**Completed:** 2026-05-05

## Scope

Implemented Phase 2 SCORE-03 quality factor:

- `src/ls_equity_fund/factors/quality.py`
- `src/ls_equity_fund/factors/_piotroski.py`
- `src/ls_equity_fund/factors/_altman.py`
- `tests/unit/factors/test_quality.py`
- `tests/unit/factors/test_piotroski.py`
- `tests/unit/factors/test_altman.py`

## Quality Formulas

`compute_quality(conn, asof, tickers)` returns long-format rows with columns:

- `ticker`
- `sub_factor`
- `raw_value`

Eight sub-factors are emitted per ticker:

- `qual_roe_stability` = `1 / (std(last 8 quarterly ROE values) + 1e-9)`
- `qual_gm_level` = latest point-in-time `fundamental_ratios.gross_margin`
- `qual_gm_trend` = latest annual gross margin minus prior annual gross margin
- `qual_de_inv` = `-fundamental_ratios.debt_to_equity`
- `qual_cfo_ni` = latest point-in-time `fundamental_ratios.cfo_to_ni`
- `qual_accruals_inv` = `-fundamental_ratios.accruals_ratio`
- `qual_piotroski_f` = Piotroski F-Score, `0..9`, from nine binary checks
- `qual_altman_z` = original Altman Z-Score formula

## Helper Invariants

- Piotroski uses exactly nine checks: F1 through F9.
- Piotroski returns `None` when the prior annual row or critical fields are missing.
- Altman uses `ALTMAN_COEFFS = (1.2, 1.4, 3.3, 0.6, 1.0)`.
- Altman zone classification is available via `classify_zone(z)` for `safe`, `grey`, and `distress`.
- The original Altman Z formula is applied across all sectors per plan decision A3.

## Deviations from Plan

None - plan executed exactly as written, with one naming correction from the user instruction: the Piotroski sub-factor is `qual_piotroski_f`.

## Verification

- `uv run pytest tests/unit/factors/test_quality.py tests/unit/factors/test_piotroski.py tests/unit/factors/test_altman.py -q`
  - 34 passed
- `uv run --extra dev ruff check src/ls_equity_fund/factors/quality.py src/ls_equity_fund/factors/_piotroski.py src/ls_equity_fund/factors/_altman.py tests/unit/factors/test_quality.py tests/unit/factors/test_piotroski.py tests/unit/factors/test_altman.py`
  - All checks passed
- `uv run pytest -q`
  - 358 passed, 3 warnings

The warnings are existing `edgartools` deprecation warnings from Phase 0 smoke imports.

## Next Phase Readiness

SCORE-03 is ready for the Phase 2 composer/orchestrator path. Quality emits raw values only; sector-neutral percentile ranking and parent-score composition remain in the shared Phase 2 infrastructure.
