"""create risk management tables (RISK-01..07, AUDIT-01)

Phase 6 adds the persistent risk/audit tables used by the factor risk model,
pre-trade veto layer, and circuit breakers. Audit event tables are append-only:
SQLite triggers reject DELETE and UPDATE so veto and breaker events cannot be
silently rewritten after the fact.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE risk_snapshots (
            run_id            TEXT NOT NULL,
            ticker            TEXT NOT NULL,
            factor_variance   REAL,
            specific_variance REAL,
            total_variance    REAL,
            mctr              REAL,
            timestamp         INTEGER NOT NULL,
            PRIMARY KEY (run_id, ticker)
        )
        """
    )
    op.execute("CREATE INDEX idx_risk_snapshots_run ON risk_snapshots(run_id)")
    op.execute("CREATE INDEX idx_risk_snapshots_ticker ON risk_snapshots(ticker)")

    op.execute(
        """
        CREATE TABLE veto_log (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp          INTEGER NOT NULL,
            ticker             TEXT NOT NULL,
            side               TEXT NOT NULL,
            shares             REAL NOT NULL,
            reason             TEXT NOT NULL,
            trade_context_json TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_veto_log_timestamp ON veto_log(timestamp)")
    op.execute("CREATE INDEX idx_veto_log_ticker ON veto_log(ticker)")
    op.execute(
        """
        CREATE TRIGGER veto_log_no_delete
        BEFORE DELETE ON veto_log
        BEGIN
            SELECT RAISE(ABORT, 'veto_log is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER veto_log_no_update
        BEFORE UPDATE ON veto_log
        BEGIN
            SELECT RAISE(ABORT, 'veto_log is immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TABLE circuit_breaker_log (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp            INTEGER NOT NULL,
            breaker_type         TEXT NOT NULL,
            threshold            REAL NOT NULL,
            observed_value       REAL NOT NULL,
            portfolio_state_json TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_cbl_timestamp ON circuit_breaker_log(timestamp)")
    op.execute("CREATE INDEX idx_cbl_type ON circuit_breaker_log(breaker_type)")
    op.execute(
        """
        CREATE TRIGGER circuit_breaker_log_no_delete
        BEFORE DELETE ON circuit_breaker_log
        BEGIN
            SELECT RAISE(ABORT, 'circuit_breaker_log is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER circuit_breaker_log_no_update
        BEFORE UPDATE ON circuit_breaker_log
        BEGIN
            SELECT RAISE(ABORT, 'circuit_breaker_log is immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS circuit_breaker_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS circuit_breaker_log_no_delete")
    op.execute("DROP INDEX IF EXISTS idx_cbl_type")
    op.execute("DROP INDEX IF EXISTS idx_cbl_timestamp")
    op.execute("DROP TABLE IF EXISTS circuit_breaker_log")
    op.execute("DROP TRIGGER IF EXISTS veto_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS veto_log_no_delete")
    op.execute("DROP INDEX IF EXISTS idx_veto_log_ticker")
    op.execute("DROP INDEX IF EXISTS idx_veto_log_timestamp")
    op.execute("DROP TABLE IF EXISTS veto_log")
    op.execute("DROP INDEX IF EXISTS idx_risk_snapshots_ticker")
    op.execute("DROP INDEX IF EXISTS idx_risk_snapshots_run")
    op.execute("DROP TABLE IF EXISTS risk_snapshots")
