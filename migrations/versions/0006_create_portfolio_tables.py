"""create portfolio tables (PORT-06)

Three tables landed by Phase 5:

  * ``portfolio_positions`` — current book of record. Single row per (ticker,
    side); shares is signed (negative for shorts). ``factor_scores_at_entry``
    is a JSON snapshot of the parent factor scores at the moment the position
    was opened so REPORT-02's entry-time-vs-realised-return Spearman has a
    stable input that won't drift as new scores land.

  * ``portfolio_history`` — append-only daily snapshot. Used by Phase 9 P&L
    attribution and the dashboard equity curve. One row per (ticker, asof_date)
    plus a ``__PORTFOLIO__`` ticker that captures aggregate-book metrics.

  * ``position_approvals`` — every candidate trade emitted by the conviction-
    tilt or MVO optimiser is logged here at ``--whatif`` time so a later run
    can audit "what did the optimiser propose vs what shipped". Includes the
    reason any trade was sized differently than equal-weight (tilt bucket,
    ADV cap, earnings halve, beta adjust).

Per Phase 0 D-01: raw SQL via op.execute() only. No SQLAlchemy ORM types.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE portfolio_positions (
            ticker                   TEXT NOT NULL,
            side                     TEXT NOT NULL CHECK (side IN ('long','short')),
            shares                   REAL NOT NULL,
            entry_price              REAL NOT NULL,
            entry_date               TEXT NOT NULL,
            current_price            REAL,
            unrealized_pnl           REAL,
            sector                   TEXT,
            factor_scores_at_entry   TEXT,
            beta_at_entry            REAL,
            last_marked_at           INTEGER,
            PRIMARY KEY (ticker, side)
        )
        """
    )
    op.execute("CREATE INDEX idx_pp_sector ON portfolio_positions(sector)")
    op.execute("CREATE INDEX idx_pp_side ON portfolio_positions(side)")

    op.execute(
        """
        CREATE TABLE portfolio_history (
            asof_date          TEXT NOT NULL,
            ticker             TEXT NOT NULL,
            side               TEXT,
            shares             REAL,
            mark_price         REAL,
            market_value       REAL,
            weight             REAL,
            unrealized_pnl     REAL,
            beta               REAL,
            sector             TEXT,
            gross_exposure     REAL,
            net_exposure       REAL,
            net_beta           REAL,
            long_book_beta     REAL,
            short_book_beta    REAL,
            recorded_at        INTEGER NOT NULL,
            PRIMARY KEY (asof_date, ticker)
        )
        """
    )
    op.execute("CREATE INDEX idx_ph_asof ON portfolio_history(asof_date)")
    op.execute("CREATE INDEX idx_ph_ticker ON portfolio_history(ticker)")

    op.execute(
        """
        CREATE TABLE position_approvals (
            run_id                   TEXT NOT NULL,
            asof_date                TEXT NOT NULL,
            ticker                   TEXT NOT NULL,
            side                     TEXT NOT NULL CHECK (side IN ('long','short')),
            optimizer                TEXT NOT NULL CHECK (optimizer IN ('conviction','mvo')),
            tilt_bucket              TEXT,
            base_weight              REAL NOT NULL,
            tilted_weight            REAL NOT NULL,
            adv_capped_weight        REAL NOT NULL,
            earnings_halved          INTEGER NOT NULL DEFAULT 0,
            beta_adjusted_weight     REAL NOT NULL,
            final_weight             REAL NOT NULL,
            final_shares             REAL NOT NULL,
            target_dollar            REAL NOT NULL,
            limit_price              REAL,
            score                    REAL,
            sector                   TEXT,
            beta                     REAL,
            advisory_flags           TEXT,
            decided_at               INTEGER NOT NULL,
            PRIMARY KEY (run_id, ticker, side)
        )
        """
    )
    op.execute("CREATE INDEX idx_pa_asof ON position_approvals(asof_date)")
    op.execute("CREATE INDEX idx_pa_run ON position_approvals(run_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pa_run")
    op.execute("DROP INDEX IF EXISTS idx_pa_asof")
    op.execute("DROP TABLE IF EXISTS position_approvals")
    op.execute("DROP INDEX IF EXISTS idx_ph_ticker")
    op.execute("DROP INDEX IF EXISTS idx_ph_asof")
    op.execute("DROP TABLE IF EXISTS portfolio_history")
    op.execute("DROP INDEX IF EXISTS idx_pp_side")
    op.execute("DROP INDEX IF EXISTS idx_pp_sector")
    op.execute("DROP TABLE IF EXISTS portfolio_positions")
