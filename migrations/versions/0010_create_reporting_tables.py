"""create reporting tables (REPORT-01..08)

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE daily_attribution (
            run_id        TEXT NOT NULL,
            date          TEXT NOT NULL,
            daily_return  REAL NOT NULL,
            beta_return   REAL NOT NULL,
            sector_return REAL NOT NULL,
            factor_return REAL NOT NULL,
            alpha_return  REAL NOT NULL,
            net_beta      REAL NOT NULL,
            spy_return    REAL NOT NULL,
            PRIMARY KEY (run_id, date)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE position_attribution (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                   TEXT NOT NULL,
            side                     TEXT NOT NULL,
            entry_date               TEXT NOT NULL,
            exit_date                TEXT NOT NULL,
            entry_price              REAL NOT NULL,
            exit_price               REAL NOT NULL,
            entry_score              REAL,
            realized_pnl             REAL NOT NULL,
            holding_days             INTEGER NOT NULL,
            holding_bucket           TEXT NOT NULL,
            sector                   TEXT,
            vix_at_entry             REAL,
            factor_quintile_at_entry INTEGER
        )
        """
    )
    op.execute("CREATE INDEX idx_pa_ticker_exit ON position_attribution(ticker, exit_date)")
    op.execute(
        """
        CREATE TABLE tear_sheet_metrics (
            run_id       TEXT NOT NULL,
            date         TEXT NOT NULL,
            metric_name  TEXT NOT NULL,
            metric_value REAL NOT NULL,
            PRIMARY KEY (run_id, date, metric_name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE weekly_commentary (
            week_ending TEXT PRIMARY KEY,
            model_id    TEXT NOT NULL,
            body_md     TEXT NOT NULL,
            generated_at INTEGER NOT NULL,
            cached      INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE daily_letter (
            date         TEXT NOT NULL,
            mode         TEXT NOT NULL CHECK (mode IN ('lp','internal')),
            body_md      TEXT NOT NULL,
            doc_id       TEXT NOT NULL,
            generated_at INTEGER NOT NULL,
            cached       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, mode)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_letter")
    op.execute("DROP TABLE IF EXISTS weekly_commentary")
    op.execute("DROP TABLE IF EXISTS tear_sheet_metrics")
    op.execute("DROP INDEX IF EXISTS idx_pa_ticker_exit")
    op.execute("DROP TABLE IF EXISTS position_attribution")
    op.execute("DROP TABLE IF EXISTS daily_attribution")
