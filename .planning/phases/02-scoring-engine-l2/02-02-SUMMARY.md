# 02-02 Summary — Momentum Factor

**Completed:** 2026-05-05

## Scope

Implemented Phase 2 SCORE-01 momentum factor and the minimum SCORE-09/SCORE-10 scaffold it depends on:

- `migrations/versions/0003_create_factor_scores_tables.py`
- `src/ls_equity_fund/factors/{composer,sector_rank,persist,momentum}.py`
- `src/ls_equity_fund/cli/scoring_cmd.py`
- `tests/unit/factors/*`

## Momentum Formulas

`compute_momentum(conn, asof, tickers)` returns long-format rows with columns:

- `ticker`
- `sub_factor`
- `raw_value`

Six sub-factors are emitted per ticker:

- `mom_12_1` = `close[asof - 21bd] / close[asof - 252bd] - 1`
- `mom_6m` = `close[asof] / close[asof - 126bd] - 1`
- `mom_3m` = `close[asof] / close[asof - 63bd] - 1`
- `mom_accel` = `mom_3m - mom_6m`
- `mom_52w_high` = `close[asof] / max(close over last 252 trading days)`
- `mom_sector_rel` = stock 6m gross return / sector ETF 6m gross return

## Invariants

- Trading-day arithmetic is positional via pandas series `.iloc`; there is no `timedelta(days=...)` calendar arithmetic.
- Short-history tickers still emit all six rows. Sub-factors that cannot be computed return `NaN`.
- `compute_momentum` is registered at import time through `@register_factor("momentum")`.
- Momentum only emits raw values. Sector percentile ranking stays in `sector_rank.py` and the future orchestrator.

## Sector ETF Mapping

The current `Config` model does not expose `config.data.sector_etfs`, so `momentum.py` uses the standard GICS -> SPDR fallback mapping locally:

- Information Technology -> XLK
- Communication Services -> XLC
- Health Care -> XLV
- Energy -> XLE
- Financials -> XLF
- Industrials -> XLI
- Consumer Discretionary -> XLY
- Consumer Staples -> XLP
- Materials -> XLB
- Real Estate -> XLRE
- Utilities -> XLU

TODO: move this mapping to `config.data.sector_etfs` once the config schema exposes it.

## Verification

- `uv run pytest tests/unit/factors tests/unit/test_phase2_migration.py -q`
  - 38 passed
- `uv run pytest tests -q`
  - 315 passed, 3 warnings

The warnings are existing `edgartools` deprecation warnings from Phase 0 smoke imports.
