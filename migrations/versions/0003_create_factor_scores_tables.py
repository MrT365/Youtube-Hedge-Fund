"""create_factor_scores_tables

Phase 2 (L2 — Scoring Engine) migration. Adds the audit-grade factor score
tables consumed by every Phase 2 factor module and later dashboard/portfolio
phases.

Per Phase 0 migration convention: raw SQL only via ``op.execute()``.

Schema bindings:
  - factor_scores PK (ticker, score_date, factor, sub_factor) -> SCORE-10 / SC4
    idempotent replay via INSERT OR REPLACE.
  - factor_scores_parent PK (ticker, score_date, factor) -> SCORE-09 parent
    equal-weighted mean of sub-factor ranks.
  - sector is denormalized at compute time for PIT-correct historical replay.
  - sufficient_history records degenerate-neutral/bootstrap cases for SCORE-05.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-05 09:30:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Phase 2 factor score tables (raw SQL only)."""

    op.execute(
        """
        CREATE TABLE factor_scores (
            ticker             TEXT NOT NULL,
            score_date         TEXT NOT NULL,
            factor             TEXT NOT NULL CHECK (factor IN
                              ('momentum','value','quality','growth','revisions',
                               'short_interest','insider','institutional')),
            sub_factor         TEXT NOT NULL,
            raw_value          REAL,
            percentile_rank    REAL,
            sector             TEXT NOT NULL,
            n_in_sector        INTEGER,
            sufficient_history INTEGER NOT NULL DEFAULT 1 CHECK (sufficient_history IN (0,1)),
            computed_at        INTEGER NOT NULL,
            PRIMARY KEY (ticker, score_date, factor, sub_factor)
        )
        """
    )
    op.execute("CREATE INDEX idx_fs_score_date ON factor_scores(score_date)")
    op.execute("CREATE INDEX idx_fs_ticker_date ON factor_scores(ticker, score_date)")
    op.execute("CREATE INDEX idx_fs_factor_date ON factor_scores(factor, score_date)")
    op.execute("CREATE INDEX idx_fs_sector_date ON factor_scores(sector, score_date)")

    op.execute(
        """
        CREATE TABLE factor_scores_parent (
            ticker              TEXT NOT NULL,
            score_date          TEXT NOT NULL,
            factor              TEXT NOT NULL CHECK (factor IN
                                ('momentum','value','quality','growth','revisions',
                                 'short_interest','insider','institutional')),
            parent_score        REAL,
            sector              TEXT NOT NULL,
            n_subfactors_used   INTEGER NOT NULL,
            computed_at         INTEGER NOT NULL,
            PRIMARY KEY (ticker, score_date, factor)
        )
        """
    )
    op.execute("CREATE INDEX idx_fsp_score_date ON factor_scores_parent(score_date)")
    op.execute("CREATE INDEX idx_fsp_ticker_date ON factor_scores_parent(ticker, score_date)")
    op.execute("CREATE INDEX idx_fsp_factor_date ON factor_scores_parent(factor, score_date)")


def downgrade() -> None:
    """Drop Phase 2 factor score tables in reverse creation order."""

    op.execute("DROP INDEX IF EXISTS idx_fsp_factor_date")
    op.execute("DROP INDEX IF EXISTS idx_fsp_ticker_date")
    op.execute("DROP INDEX IF EXISTS idx_fsp_score_date")
    op.execute("DROP TABLE IF EXISTS factor_scores_parent")
    op.execute("DROP INDEX IF EXISTS idx_fs_sector_date")
    op.execute("DROP INDEX IF EXISTS idx_fs_factor_date")
    op.execute("DROP INDEX IF EXISTS idx_fs_ticker_date")
    op.execute("DROP INDEX IF EXISTS idx_fs_score_date")
    op.execute("DROP TABLE IF EXISTS factor_scores")
