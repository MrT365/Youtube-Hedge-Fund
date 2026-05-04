"""create_runs_and_heartbeat_tables

Phase 0 initial migration. Per CONTEXT D-02: this migration ships ONLY the
runs and heartbeat tables. Future phases add their own tables in their own
migrations — do not forward-declare prices / factor_scores / orders here.

Per CONTEXT D-01: raw SQL only. NO op.create_table, NO SQLAlchemy ORM types.

Revision ID: 0001
Revises:
Create Date: 2026-05-04 12:00:00 UTC
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create runs + heartbeat tables (Phase 0 scope, D-02)."""
    # ----- runs table -----
    # Per CONTEXT D-02: schema is exactly run_id TEXT PK, start_ts INT NOT NULL,
    # end_ts INT, status TEXT NOT NULL with CHECK, error TEXT.
    op.execute(
        """
        CREATE TABLE runs (
            run_id     TEXT PRIMARY KEY,
            start_ts   INTEGER NOT NULL,
            end_ts     INTEGER,
            status     TEXT NOT NULL CHECK (status IN ('RUNNING', 'OK', 'FAILED')),
            error      TEXT
        )
        """
    )
    op.execute("CREATE INDEX idx_runs_start_ts ON runs(start_ts)")

    # ----- heartbeat table -----
    # Singleton row (id=1 only, enforced by CHECK). Daily-refresh updates this row;
    # dashboard surfaces stale-heartbeat warnings (Phase 10 INFRA-05 scope).
    op.execute(
        """
        CREATE TABLE heartbeat (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            last_run_id         TEXT,
            last_heartbeat_ts   INTEGER,
            last_status         TEXT
        )
        """
    )
    op.execute(
        "INSERT INTO heartbeat (id, last_run_id, last_heartbeat_ts, last_status) "
        "VALUES (1, NULL, NULL, NULL)"
    )


def downgrade() -> None:
    """Drop runs + heartbeat tables (reverse order of upgrade)."""
    op.execute("DROP TABLE IF EXISTS heartbeat")
    op.execute("DROP INDEX IF EXISTS idx_runs_start_ts")
    op.execute("DROP TABLE IF EXISTS runs")
