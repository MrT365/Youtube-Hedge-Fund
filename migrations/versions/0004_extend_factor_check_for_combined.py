"""extend factor CHECK constraint to allow 'combined'

Phase 2 finishes the scoring engine with a 9th synthetic factor named
'combined' — the equal-weighted composite over the 8 base factors. The
0003 migration created factor_scores and factor_scores_parent with a CHECK
constraint pinned to exactly the 8 base names. SQLite cannot ALTER a CHECK
in place, so we use Alembic batch_alter_table (D-05) to rebuild both tables
with the expanded constraint.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_FACTORS_NEW = (
    "'momentum','value','quality','growth','revisions',"
    "'short_interest','insider','institutional','combined'"
)
_ALLOWED_FACTORS_OLD = (
    "'momentum','value','quality','growth','revisions',"
    "'short_interest','insider','institutional'"
)


def _rebuild(table: str, allowed: str, parent_columns: bool) -> None:
    """Recreate one factor_scores* table with a different CHECK on `factor`.

    SQLite has no DROP CHECK; the canonical workaround is rename → create new →
    copy → drop old. Indexes are recreated on the new table.
    """
    op.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
    if parent_columns:
        op.execute(
            f"""
            CREATE TABLE {table} (
                ticker              TEXT NOT NULL,
                score_date          TEXT NOT NULL,
                factor              TEXT NOT NULL CHECK (factor IN ({allowed})),
                parent_score        REAL,
                sector              TEXT NOT NULL,
                n_subfactors_used   INTEGER NOT NULL,
                computed_at         INTEGER NOT NULL,
                PRIMARY KEY (ticker, score_date, factor)
            )
            """
        )
    else:
        op.execute(
            f"""
            CREATE TABLE {table} (
                ticker             TEXT NOT NULL,
                score_date         TEXT NOT NULL,
                factor             TEXT NOT NULL CHECK (factor IN ({allowed})),
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
    op.execute(f"INSERT INTO {table} SELECT * FROM {table}_legacy")
    op.execute(f"DROP TABLE {table}_legacy")


def upgrade() -> None:
    _rebuild("factor_scores", _ALLOWED_FACTORS_NEW, parent_columns=False)
    op.execute("CREATE INDEX idx_fs_score_date ON factor_scores(score_date)")
    op.execute("CREATE INDEX idx_fs_ticker_date ON factor_scores(ticker, score_date)")
    op.execute("CREATE INDEX idx_fs_factor_date ON factor_scores(factor, score_date)")
    op.execute("CREATE INDEX idx_fs_sector_date ON factor_scores(sector, score_date)")

    _rebuild("factor_scores_parent", _ALLOWED_FACTORS_NEW, parent_columns=True)
    op.execute("CREATE INDEX idx_fsp_score_date ON factor_scores_parent(score_date)")
    op.execute("CREATE INDEX idx_fsp_ticker_date ON factor_scores_parent(ticker, score_date)")
    op.execute("CREATE INDEX idx_fsp_factor_date ON factor_scores_parent(factor, score_date)")


def downgrade() -> None:
    # Strip any 'combined' rows so the old CHECK accepts the data, then rebuild.
    op.execute("DELETE FROM factor_scores WHERE factor = 'combined'")
    op.execute("DELETE FROM factor_scores_parent WHERE factor = 'combined'")

    _rebuild("factor_scores", _ALLOWED_FACTORS_OLD, parent_columns=False)
    op.execute("CREATE INDEX idx_fs_score_date ON factor_scores(score_date)")
    op.execute("CREATE INDEX idx_fs_ticker_date ON factor_scores(ticker, score_date)")
    op.execute("CREATE INDEX idx_fs_factor_date ON factor_scores(factor, score_date)")
    op.execute("CREATE INDEX idx_fs_sector_date ON factor_scores(sector, score_date)")

    _rebuild("factor_scores_parent", _ALLOWED_FACTORS_OLD, parent_columns=True)
    op.execute("CREATE INDEX idx_fsp_score_date ON factor_scores_parent(score_date)")
    op.execute("CREATE INDEX idx_fsp_ticker_date ON factor_scores_parent(ticker, score_date)")
    op.execute("CREATE INDEX idx_fsp_factor_date ON factor_scores_parent(factor, score_date)")
