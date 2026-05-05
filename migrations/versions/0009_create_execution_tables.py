"""create execution tables (EXEC-01..09)

Phase 8 persists every broker order, side-aware slippage snapshot, and
IBKR-native borrow-rate observation used by the paper execution layer.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE orders (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        INTEGER NOT NULL,
            ticker           TEXT NOT NULL,
            side             TEXT NOT NULL,
            shares           REAL NOT NULL,
            limit_price      REAL NOT NULL,
            fill_price       REAL,
            slippage_bps     REAL,
            status           TEXT NOT NULL,
            broker_order_id  TEXT NOT NULL,
            signal_price     REAL NOT NULL,
            is_closing_trade INTEGER NOT NULL DEFAULT 0,
            run_id           TEXT NOT NULL,
            tif              TEXT NOT NULL,
            chunk_index      INTEGER NOT NULL,
            chunk_total      INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_orders_run ON orders(run_id)")
    op.execute("CREATE INDEX idx_orders_ticker ON orders(ticker)")
    op.execute("CREATE INDEX idx_orders_status ON orders(status)")

    op.execute(
        """
        CREATE TABLE slippage_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        TEXT NOT NULL,
            ticker        TEXT NOT NULL,
            side          TEXT NOT NULL,
            fill_bps      REAL NOT NULL,
            signal_price  REAL NOT NULL,
            fill_price    REAL NOT NULL,
            timestamp     INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_slip_ts ON slippage_snapshots(timestamp)")
    op.execute("CREATE INDEX idx_slip_ticker ON slippage_snapshots(ticker)")

    op.execute(
        """
        CREATE TABLE borrow_rates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            rate_pct    REAL NOT NULL,
            is_htb      INTEGER NOT NULL DEFAULT 0,
            as_of_date  TEXT NOT NULL,
            source      TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_borrow_ticker_date ON borrow_rates(ticker, as_of_date)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_borrow_ticker_date")
    op.execute("DROP TABLE IF EXISTS borrow_rates")
    op.execute("DROP INDEX IF EXISTS idx_slip_ticker")
    op.execute("DROP INDEX IF EXISTS idx_slip_ts")
    op.execute("DROP TABLE IF EXISTS slippage_snapshots")
    op.execute("DROP INDEX IF EXISTS idx_orders_status")
    op.execute("DROP INDEX IF EXISTS idx_orders_ticker")
    op.execute("DROP INDEX IF EXISTS idx_orders_run")
    op.execute("DROP TABLE IF EXISTS orders")
