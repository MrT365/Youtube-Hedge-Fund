"""create analysis_results cache table (ANAL-04)

Stores Claude response JSON keyed by (analyzer_type, ticker, artifact_id) so
re-running the same analysis on the same filing/snapshot returns a free hit.
TTL is enforced via expires_at; expired rows are evicted by ``analysis.cache``
on read or by an opportunistic VACUUM-style sweep at run end.

The same row also serves as the audit record — cost_usd, token counts, and the
serving run_id are persisted so a later predictive-power study can replay
"what did Claude say at ingest time, and what did it cost?".

Per Phase 0 D-01: raw SQL via op.execute() only. No SQLAlchemy ORM types.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ANALYZER_TYPES = (
    "'earnings','filing','risk','insider','sector','earnings_call'"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE analysis_results (
            analyzer_type       TEXT NOT NULL CHECK (analyzer_type IN ({_ANALYZER_TYPES})),
            ticker              TEXT NOT NULL,
            artifact_id         TEXT NOT NULL,
            run_id              TEXT,
            model               TEXT NOT NULL,
            response_json       TEXT NOT NULL,
            input_tokens        INTEGER NOT NULL DEFAULT 0,
            output_tokens       INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
            cost_usd            REAL NOT NULL DEFAULT 0.0,
            cached_from         TEXT,
            computed_at         INTEGER NOT NULL,
            expires_at          INTEGER NOT NULL,
            PRIMARY KEY (analyzer_type, ticker, artifact_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_ar_ticker_run ON analysis_results(ticker, run_id)")
    op.execute("CREATE INDEX idx_ar_expires ON analysis_results(expires_at)")
    op.execute("CREATE INDEX idx_ar_analyzer_ticker ON analysis_results(analyzer_type, ticker)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ar_analyzer_ticker")
    op.execute("DROP INDEX IF EXISTS idx_ar_expires")
    op.execute("DROP INDEX IF EXISTS idx_ar_ticker_run")
    op.execute("DROP TABLE IF EXISTS analysis_results")
