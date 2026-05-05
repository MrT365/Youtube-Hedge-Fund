"""create optimizer fallback audit log (PORT-03, AUDIT-01)

Phase 7 records every MVO fallback event with timestamp, reason,
fallback_used, and a JSON portfolio-state snapshot. The table is append-only
through SQLite triggers.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE optimizer_fallback_log (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp            INTEGER NOT NULL,
            reason               TEXT NOT NULL,
            fallback_used        TEXT NOT NULL,
            portfolio_state_json TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_ofl_timestamp ON optimizer_fallback_log(timestamp)")
    op.execute(
        """
        CREATE TRIGGER optimizer_fallback_log_no_delete
        BEFORE DELETE ON optimizer_fallback_log
        BEGIN
            SELECT RAISE(ABORT, 'optimizer_fallback_log is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER optimizer_fallback_log_no_update
        BEFORE UPDATE ON optimizer_fallback_log
        BEGIN
            SELECT RAISE(ABORT, 'optimizer_fallback_log is immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS optimizer_fallback_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS optimizer_fallback_log_no_delete")
    op.execute("DROP INDEX IF EXISTS idx_ofl_timestamp")
    op.execute("DROP TABLE IF EXISTS optimizer_fallback_log")
