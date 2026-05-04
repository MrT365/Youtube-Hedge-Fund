# Migrations — `ls_equity_fund`

**Authoring style:** raw SQL via `op.execute("...")` (CONTEXT D-01).
NEVER `op.create_table(...)`. NEVER SQLAlchemy `Table()` / `MetaData()` declarations
in a migration.

## Why raw SQL?

Audit-grade discipline. What the operator reads in the migration file is exactly
what runs against SQLite. CLAUDE.md mandates: *"audit is a spec requirement."*
Raw SQL also keeps the diff trivial to review under regulatory scrutiny.

## Phase 0 scope (D-02)

The initial migration `0001_create_runs_table.py` ships ONLY:

- `runs` table — every CLI invocation writes one row at start + updates on completion.
- `heartbeat` table — singleton row surfaced by the dashboard's stale-heartbeat warning.

Future phases own their own tables. Do **not** forward-declare `prices`,
`factor_scores`, `orders`, `vetoes`, etc. — each phase adds its own migration when
it adds its own schema.

## SQLite ALTER limits — use `batch_alter_table` (D-05)

SQLite cannot directly drop or rename columns. To add / remove / rename columns later,
wrap the change in `batch_alter_table`:

```python
def upgrade() -> None:
    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.add_column(sa.Column("new_field", sa.Text()))
```

`render_as_batch=True` is set globally in `migrations/env.py` so this Just Works.

## Common operations

```bash
# Create a new revision (manually edited — autogenerate is OFF per D-01):
uv run alembic revision -m "add_factor_scores_table"

# Apply all pending migrations:
uv run alembic upgrade head

# Show current revision:
uv run alembic current

# Roll back one:
uv run alembic downgrade -1

# Inspect cumulative schema (the "source of truth" check):
sqlite3 cache/ls_equity_fund.db ".schema"
```

## Anti-patterns (do NOT do these)

- `op.create_table(...)` — use `op.execute("CREATE TABLE ...")` instead.
- `from sqlalchemy import Column, Integer, Text` at module top — only if you genuinely
  need `batch_alter_table` column ops; for plain CREATE / INDEX / INSERT, stay raw.
- `--autogenerate` — deliberately off (we do **not** have `target_metadata` set in env.py).
- `schema.sql` snapshots — D-04 forbids; cumulative state is reconstructed by reading
  every migration in order.

## Authoring checklist for a new migration

1. `uv run alembic revision -m "short_snake_message"` to scaffold from `script.py.mako`.
2. Replace the `pass` in `upgrade()` with one or more `op.execute("...")` calls.
3. Replace the `pass` in `downgrade()` with the inverse (DROP, etc.) — keep it honest.
4. Run `uv run alembic upgrade head` against a throwaway DB to verify.
5. Run the migration test suite: `uv run pytest tests/unit/test_migrations.py`.
6. Commit migration + any new application code that depends on the new schema.
