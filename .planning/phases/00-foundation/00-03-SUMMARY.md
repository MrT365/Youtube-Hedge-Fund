---
phase: 00-foundation
plan: 03
subsystem: foundation/db
tags: [sqlite, wal, alembic, migrations, infra]
requires: []
provides:
  - "src/ls_equity_fund/db.py: get_connection() / get_db_path() / PRAGMAS"
  - "alembic.ini + migrations/env.py — Alembic at repo root, raw-SQL policy"
  - "migrations/versions/0001_create_runs_table.py — runs + heartbeat tables (D-02)"
affects:
  - "every layer that persists imports from ls_equity_fund.db"
  - "Phase 0 doctor smoke command (Plan 00-06) will call alembic upgrade head"
tech-stack:
  added: [alembic, sqlalchemy]
  patterns:
    - "raw-SQL migrations via op.execute (D-01)"
    - "render_as_batch=True for SQLite ALTER limits (D-05)"
    - "WAL set on every runtime connection, NOT in env.py"
key-files:
  created:
    - src/ls_equity_fund/__init__.py
    - src/ls_equity_fund/db.py
    - alembic.ini
    - migrations/env.py
    - migrations/script.py.mako
    - migrations/README.md
    - migrations/versions/.gitkeep
    - migrations/versions/0001_create_runs_table.py
    - tests/__init__.py
    - tests/unit/__init__.py
    - tests/unit/test_db.py
    - tests/unit/test_migrations.py
  modified: []
decisions:
  - "Removed PRAGMA journal_mode=WAL from migrations/env.py online-mode connection (Rule 1 fix) — issuing it via SQLAlchemy promotes Alembic into non-transactional DDL mode and silently drops INSERT statements. WAL is per-database persistent and set once by get_connection()."
  - "Defensive lazy import of ls_equity_fund.config inside db.py and migrations/env.py so this plan loads cleanly during the parallel-execution window with plan 00-02 (which owns config.py). Once 00-02 merges, the runtime path uses the real Config without modification."
  - "Test suite uses alembic.command.upgrade programmatically (not subprocess) so tests are hermetic and decoupled from operator config.yaml setup."
metrics:
  duration: ~12 minutes
  completed: 2026-05-04
---

# Phase 0 Plan 03: SQLite WAL gateway + Alembic migrations — Summary

**One-liner:** SQLite gateway with WAL mode + foreign keys + 5s busy timeout, plus Alembic at repo root authoring migrations as raw SQL via `op.execute()`; initial migration ships only the Phase 0 `runs` and `heartbeat` tables.

## What Shipped

### `src/ls_equity_fund/db.py` (public API)

| Symbol | Purpose |
|---|---|
| `PRAGMAS: list[str]` | Six-entry constant — applied on every `get_connection()` call |
| `get_db_path(config) -> Path` | Resolves `Path(config.data.cache_dir) / "ls_equity_fund.db"` |
| `get_connection(db_path, *, create_parent=True) -> sqlite3.Connection` | Open + apply PRAGMAs + set `row_factory=sqlite3.Row` + `detect_types=PARSE_DECLTYPES \| PARSE_COLNAMES` |

PRAGMAs applied (D-21 / ARCHITECTURE.md §4):

1. `PRAGMA journal_mode=WAL`
2. `PRAGMA synchronous=NORMAL`
3. `PRAGMA foreign_keys=ON`
4. `PRAGMA busy_timeout=5000`
5. `PRAGMA cache_size=-65536` (64 MB page cache)
6. `PRAGMA temp_store=MEMORY`

Connection conventions: `isolation_level=None` (autocommit; explicit `BEGIN/COMMIT` in callers), `row_factory=sqlite3.Row` (column-name access), type-detection enabled for timestamp adapters.

### Alembic skeleton (`alembic.ini` + `migrations/`)

- `alembic.ini` at repo root (D-03), `script_location = migrations`, stub `sqlalchemy.url` overridden by `env.py` at runtime.
- `migrations/env.py`:
  - Resolves DB URL by calling `ls_equity_fund.config.load_config()` + `db.get_db_path()`; falls back to `alembic.ini`'s stub URL when config import fails (parallel-execution window).
  - `target_metadata = None` — autogenerate deliberately disabled (D-01).
  - `render_as_batch=True` in both online and offline modes (D-05) — enables future `op.batch_alter_table` for SQLite ALTER limits.
  - **Does NOT issue `PRAGMA journal_mode=WAL` on the migration connection** — see Deviations.
- `migrations/script.py.mako` — minimal raw-SQL revision template (no `import sqlalchemy as sa` at module top).
- `migrations/README.md` — documents D-01 raw-SQL policy, D-02 phase scope, D-04 source-of-truth, D-05 `batch_alter_table`, common operations, and explicit anti-patterns.
- `migrations/versions/.gitkeep` tracks the empty versions directory.

### Initial migration `migrations/versions/0001_create_runs_table.py`

`runs` table (D-02 locked schema):

```sql
CREATE TABLE runs (
    run_id     TEXT PRIMARY KEY,
    start_ts   INTEGER NOT NULL,
    end_ts     INTEGER,
    status     TEXT NOT NULL CHECK (status IN ('RUNNING', 'OK', 'FAILED')),
    error      TEXT
);
CREATE INDEX idx_runs_start_ts ON runs(start_ts);
```

`heartbeat` singleton table:

```sql
CREATE TABLE heartbeat (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_id         TEXT,
    last_heartbeat_ts   INTEGER,
    last_status         TEXT
);
INSERT INTO heartbeat (id, last_run_id, last_heartbeat_ts, last_status) VALUES (1, NULL, NULL, NULL);
```

- 7 `op.execute(` calls (4 upgrade + 3 downgrade); 0 `op.create_table(` (D-01 verified at the source-text level by `test_initial_migration_uses_raw_sql`).
- Downgrade reverses upgrade: drops heartbeat, drops index, drops runs.

## Tests

| Test file | Test count | Status |
|---|---|---|
| `tests/unit/test_db.py` | 8 | All pass |
| `tests/unit/test_migrations.py` | 5 | All pass |
| **Total** | **13** | **13 passed in 0.10s** |

`test_db.py` covers: connection type, WAL active, foreign_keys/busy_timeout/synchronous/cache_size pragmas, `row_factory=sqlite3.Row` column access, parent-dir creation, `get_db_path(config)` shape, WAL sidecar (`-wal` / `-shm`) appearance after a write, PRAGMAS constant has six entries.

`test_migrations.py` covers: source-level raw-SQL policy enforcement, full `runs` schema match against D-02 (column names, types, NOT NULL, PK), index existence, status `CHECK` rejection of invalid values plus acceptance of `RUNNING/OK/FAILED`, heartbeat singleton row + `id != 1` rejection, idempotent re-upgrade (schema unchanged, exactly one `alembic_version` row, exactly one heartbeat row).

Test invocation: `uv run pytest tests/unit/test_db.py tests/unit/test_migrations.py -v`.

## Commits

| Task | Commit | Description |
|---|---|---|
| 1 | `f3e38d6` | `feat(00-03): add SQLite WAL connection factory in src/ls_equity_fund/db.py` |
| 2 | `6804094` | `feat(00-03): scaffold Alembic at repo root with raw-SQL migration policy` |
| 3 | `6723393` | `feat(00-03): add initial migration creating runs + heartbeat tables` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Removed `PRAGMA journal_mode=WAL` from migrations/env.py online-mode connection**

- **Found during:** Task 3 (`uv run pytest tests/unit/test_migrations.py`).
- **Symptom:** `test_heartbeat_singleton_row` returned 0 rows; `test_upgrade_idempotent` raised `sqlite3.OperationalError: table runs already exists` on the second upgrade because `alembic_version` was empty.
- **Root cause:** Setting `journal_mode=WAL` via `connection.exec_driver_sql` inside the SQLAlchemy connection caused Alembic to log `Will assume non-transactional DDL`. Under that mode, the heartbeat `INSERT` and the `alembic_version` row were not committed.
- **Fix:** Removed both `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` from `migrations/env.py`'s `run_migrations_online()` body. WAL is a per-database persistent property — set once on every runtime connection by `ls_equity_fund.db.get_connection()`. Migration connection doesn't need it.
- **Files modified:** `migrations/env.py`
- **Commit:** `6723393`

**2. [Rule 3 — Blocking] Plan-supplied `tests/unit/test_db.py` references `fresh_yaml_path` / `fresh_env_path` fixtures owned by plan 00-02 (parallel)**

- **Found during:** Task 1 design (before writing the test file).
- **Issue:** Plan body's `test_get_db_path_uses_config_cache_dir` calls `load_config(yaml_path=fresh_yaml_path, env_path=fresh_env_path)`. Those fixtures are defined by plan 00-02's conftest, which is not in this worktree.
- **Fix:** Rewrote that one test to use a lightweight duck-typed config object with `.data.cache_dir` so the contract under test (`get_db_path` reads `config.data.cache_dir`) is exercised without depending on plan 00-02's `Config` class shape.
- **Files modified:** `tests/unit/test_db.py`
- **Commit:** `f3e38d6`

**3. [Rule 3 — Blocking] Plan-supplied `tests/unit/test_migrations.py` shells out to `uv run alembic` and depends on operator-side `config.yaml` + `.env`**

- **Found during:** Task 3 design.
- **Issue:** Subprocess approach added two layers of fragility: (a) requires `config.yaml.example` shape that 00-02 owns, (b) `subprocess.run(["uv", "run", ...])` is ~1s slower per invocation and brittle on PATH.
- **Fix:** Rewrote tests to call `alembic.command.upgrade(cfg, "head")` programmatically with an `AlembicConfig` whose `sqlalchemy.url` points at a per-test `tmp_path` DB. This is fully hermetic — no `config.yaml`, no subprocess, no env vars required.
- **Files modified:** `tests/unit/test_migrations.py`
- **Commit:** `6723393`

### Defensive design choices (not technically deviations — flagged for reviewer awareness)

- `db.py` imports `ls_equity_fund.config.Config` only inside `TYPE_CHECKING` and lazily inside `get_db_path()` so the module loads cleanly when plan 00-02's `config.py` isn't yet on disk. After 00-02 merges, the runtime path uses real `Config` without modification.
- `migrations/env.py` uses the same lazy-import + try/except pattern for `load_config` / `get_db_path` so `alembic upgrade head` works even before plan 00-02's `config.py` lands.

## Locked-decision compliance

| Decision | Status | Evidence |
|---|---|---|
| **D-01** raw SQL via `op.execute` only | PASS | `test_initial_migration_uses_raw_sql` asserts `op.execute(` count >=4 and `op.create_table(` count == 0; no `MetaData` import |
| **D-02** initial migration scope = Phase 0 only (runs + heartbeat) | PASS | `test_alembic_upgrade_head_creates_tables` confirms exactly `{runs, heartbeat, alembic_version}` after upgrade; runs schema matched column-by-column against the spec |
| **D-03** migrations at repo root | PASS | `alembic.ini` and `migrations/` both at repo root; `script_location = migrations` (relative) |
| **D-04** migrations are sole schema source | PASS | No `schema.sql` in tree; `migrations/README.md` documents the rule |
| **D-05** `render_as_batch=True` | PASS | Set in both `run_migrations_offline` and `run_migrations_online` in `migrations/env.py`; documented in `migrations/README.md` |

## Threat model coverage

| Threat ID | Disposition | Mitigation evidence |
|---|---|---|
| T-00-09 (Tampering — migration authoring) | mitigated | `test_initial_migration_uses_raw_sql` source-level enforcement of D-01 |
| T-00-10 (Repudiation — schema drift) | mitigated | D-04 enforced; no `schema.sql`; cumulative state reproducible by reading versions/ in order |
| T-00-11 (Tampering — runs.status) | mitigated | `test_runs_status_check_constraint` exercises both valid (`RUNNING/OK/FAILED`) and invalid status values |
| T-00-12 (DoS — WAL contention) | accepted | Single-writer model + `busy_timeout=5000` per ARCHITECTURE.md §7 |

## Forward dependencies

- **Plan 00-02** (`src/ls_equity_fund/config.py`): once merged, `db.get_db_path()` and `migrations/env.py`'s `_resolve_db_url()` will use the real `Config` without code change. Defensive lazy imports already in place.
- **Plan 00-06** (`meridian doctor` smoke command): will call `alembic.command.upgrade(cfg, "head")` against the configured cache path and verify `runs` table exists. Skeleton is ready.
- **Phase 6 / AUDIT-01**: future migrations add `orders`, `vetoes`, `breakers` tables — same raw-SQL policy.
- **Future SQLite ALTER changes**: documented `batch_alter_table` recipe in `migrations/README.md`; `render_as_batch=True` is already wired so future column add/drop/rename migrations Just Work.

## Self-Check: PASSED

Verified all created files exist on disk:
- `src/ls_equity_fund/__init__.py` FOUND
- `src/ls_equity_fund/db.py` FOUND
- `alembic.ini` FOUND
- `migrations/env.py` FOUND
- `migrations/script.py.mako` FOUND
- `migrations/README.md` FOUND
- `migrations/versions/.gitkeep` FOUND
- `migrations/versions/0001_create_runs_table.py` FOUND
- `tests/__init__.py` FOUND
- `tests/unit/__init__.py` FOUND
- `tests/unit/test_db.py` FOUND
- `tests/unit/test_migrations.py` FOUND

Verified all task commits exist on the worktree branch:
- `f3e38d6` (Task 1) FOUND
- `6804094` (Task 2) FOUND
- `6723393` (Task 3) FOUND

Final verification run: `uv run pytest tests/unit/test_db.py tests/unit/test_migrations.py` — **13 passed in 0.10s**.
