# Phase 0: Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-04
**Phase:** 0-Foundation
**Areas discussed:** Alembic-without-ORM strategy, PaperBroker deterministic-fill contract, Config schema composition + secrets, Logging output mode + redaction

---

## Alembic-without-ORM strategy

### Q1 — Migration authoring style

| Option | Description | Selected |
|--------|-------------|----------|
| Raw SQL via op.execute() | Each migration is a Python file containing op.execute("CREATE TABLE ...") with literal SQL. Maximum auditability — what you read is what runs. Matches CLAUDE.md guidance. No SQLAlchemy types leak in. Loses Alembic's autogenerate. | ✓ |
| Alembic op.* helpers (op.create_table, op.add_column) | Use Alembic's typed Python API. Slightly more abstraction (sa.Column types) but still no ORM models. Schema diffs are programmatic. | |
| Schema-only sqlalchemy.MetaData() | Declare metadata.py with sa.MetaData() + sa.Table() definitions (no ORM models). Enables alembic --autogenerate. Pulls SQLAlchemy in as a runtime dep. | |

**User's choice:** Raw SQL via op.execute()
**Notes:** Recommended option; aligns with CLAUDE.md "audit-critical raw SQL preferred at this scope".

### Q2 — Initial migration scope

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 0 only — runs + heartbeat tables | Initial migration creates only what Phase 0 itself uses. Each future phase adds its own migration. Smallest, most auditable initial diff. | ✓ |
| All ~30 tables forward-declared | Phase 0 creates the full schema for every layer from ARCHITECTURE.md §2. Schema is locked from day 1. | |
| Phase 0 creates infra tables + empty stubs per layer | Compromise: per-layer placeholder tables in Phase 0, columns added by owning phase. | |

**User's choice:** Phase 0 only — runs + heartbeat tables
**Notes:** Each phase owns its data; per-phase migrations stay reviewable.

### Q3 — Migrations directory location

| Option | Description | Selected |
|--------|-------------|----------|
| migrations/versions/ at repo root | Conventional Alembic layout. alembic.ini at repo root. Operational artifact, sibling of cache/, output/, tests/. | ✓ |
| src/ls_equity_fund/migrations/ inside the package | Migrations ship with the package. Useful only if pip-distributed. | |
| alembic/versions/ at repo root | Alembic CLI's true default. Identical structure, different label. | |

**User's choice:** migrations/versions/ at repo root

### Q4 — Schema source of truth

| Option | Description | Selected |
|--------|-------------|----------|
| Migrations are the only source of truth | Cumulative state reconstructed from migration history. Zero drift risk. `sqlite3 .schema` for on-demand inspection. | ✓ |
| Maintain schema.sql alongside migrations | Canonical schema.sql at repo root mirrors current state. Easier to read at a glance; drift risk. | |
| Auto-generated schema.sql via pre-commit hook | Pre-commit runs sqlite3 .schema and writes schema.sql. No drift but adds tooling. | |

**User's choice:** Migrations are the only source of truth

---

## PaperBroker deterministic-fill contract

### Q1 — Fill price model

| Option | Description | Selected |
|--------|-------------|----------|
| Fill at signal_price exactly — zero slippage | Most deterministic; perfect for unit tests. Slippage tracker records 0 bps as known baseline. Real slippage in IBKR paper or future BacktestBroker. | ✓ |
| Configurable fixed slippage in bps | Default 0 bps but config can bump. Lets later phases test slippage tracker with non-zero baseline. Tests must mock config. | |
| Random slippage drawn from distribution | Most realistic but breaks determinism (would need fixed RNG seed). Overkill for Phase 0. | |

**User's choice:** Fill at signal_price exactly — zero slippage

### Q2 — Partial fills / rejects

| Option | Description | Selected |
|--------|-------------|----------|
| Always full fill, never reject | Maximum determinism. Pre-trade veto (Phase 6) is the only rejector. Real partial fills come from IBKR paper in Phase 8. | ✓ |
| Configurable partial-fill probability + reject rate | Config knobs default to zero, can be flipped on. Adds branches to order manager test surface from day 1. | |
| Always full fill but support manual reject_next_order() | Default full-fill; test hook to inject rejection. Compromise: deterministic by default, controllable per test. | |

**User's choice:** Always full fill, never reject

### Q3 — State persistence

| Option | Description | Selected |
|--------|-------------|----------|
| In-memory only | Order/fill state in Python dicts/lists for process lifetime. Tests start fresh. IBKR-paper executor (Phase 8) owns persistence. | ✓ |
| Persist to orders/fills SQLite tables | PaperBroker writes to same schema IBKR will use. Forces orders/fills schema design in Phase 0 — contradicts our scope decision. | |
| Hybrid: in-memory by default, optional SQLite via flag | Default in-memory; config flag to flip. Keeps tests fast; adds branch. | |

**User's choice:** In-memory only

### Q4 — Broker ABC surface

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal Phase 0 surface, expanded in Phase 8 | ABC declares 5 methods (place_order, get_order, get_positions, cancel, is_paper). Phase 8 adds IBKR-specific methods. Avoids overdesign. | ✓ (Claude's discretion) |
| Full IBKR-shaped surface from day 1 | ABC declares every method Phase 8 will need. PaperBroker stubs all of them. Risk of getting shape wrong before IBKR's API is exercised. | |
| Minimal surface + Protocol-only optional capabilities | Core ABC + Python Protocols for borrow check, ADV chunking. Cleaner separation; adds Protocol indirection. | |

**User's choice:** [No preference] → Claude's discretion → Minimal Phase 0 surface (matches CLAUDE.md "don't design for hypothetical future requirements" + seam minimalism principle).

---

## Config schema composition + secrets

User directive after PaperBroker: "do recommended settings for everything". Remaining config and logging decisions captured below as auto-recommendations.

### Q1 — Composed vs monolithic Config

| Option | Description | Selected |
|--------|-------------|----------|
| Composed (Config.data / .broker / .risk / .portfolio / .anthropic / .logging) | Each layer reads its own slice. Easier to evolve. Field-level validation co-locates with layer. | ✓ (auto-recommended) |
| Monolithic flat fields | Single Config with all fields at top level. Simpler shape; harder to reason about which layer owns what. | |

**User's choice:** Composed (auto-recommended)

### Q2 — config.yaml location

| Option | Description | Selected |
|--------|-------------|----------|
| repo root | Single canonical config file. Matches research ARCHITECTURE.md §3. | ✓ (auto-recommended) |
| config/config.yaml | Subdirectory. Useful if multiple configs (env-specific) ever exist. | |
| ~/.config/meridian/config.yaml | XDG location. Decouples config from repo. Overkill for single-machine system. | |

**User's choice:** repo root (auto-recommended)

### Q3 — Env-var nesting

| Option | Description | Selected |
|--------|-------------|----------|
| env_nested_delimiter='__' (BROKER__PAPER_PORT pattern) | Standard pydantic-settings pattern. Documented in CLAUDE.md. | ✓ (auto-recommended) |
| Flat env vars (BROKER_PAPER_PORT) | Simpler but loses nesting; ambiguity if multiple layers share a field name. | |

**User's choice:** env_nested_delimiter='__' (auto-recommended)

### Q4 — Secrets handling

| Option | Description | Selected |
|--------|-------------|----------|
| Separate Secrets settings class loaded only from .env | Distinct pydantic-settings class with no YAML loader. Guarantees secrets never land in config.yaml. Simplifies redaction. | ✓ (auto-recommended) |
| Secrets fields tagged inside main Config | Single class, secrets marked via field metadata. Simpler but easier to leak into yaml/logs. | |

**User's choice:** Separate Secrets class (auto-recommended)

---

## Logging output mode + redaction

### Q1 — Renderer mode

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-detect (TTY → console, redirect → JSON) | sys.stderr.isatty() switches between ConsoleRenderer (colorized) and JSONRenderer. Operators on terminal get readable output; launchd writes JSON. | ✓ (auto-recommended) |
| Force JSON everywhere | Single output format. Easier audit; harder to read at terminal. | |
| Force console always | Easier to read; harder for launchd / log shipping. | |

**User's choice:** Auto-detect (auto-recommended)

### Q2 — Sink

| Option | Description | Selected |
|--------|-------------|----------|
| Both — stdout + rotating logs/{date}.jsonl | File is audit backbone (AUDIT-02); stdout for live tailing. Date-rotated; one file per UTC day. | ✓ (auto-recommended) |
| Stdout only | Simpler. Logs lost when process exits unless captured externally. | |
| File only | Audit-safe. Inconvenient during dev. | |

**User's choice:** Both (auto-recommended)

### Q3 — Redaction approach

| Option | Description | Selected |
|--------|-------------|----------|
| Allowlist + regex patterns (defense-in-depth) | Allowlist on common secret keys + regex on `sk-ant-*` and `Bearer *`. No generic random-string regex (would mask UUIDs). | ✓ (auto-recommended) |
| Allowlist only | Field-name based. Misses inline tokens in free-text values. | |
| Regex only | Catches inline tokens. Misses field-name signal; harder to maintain. | |

**User's choice:** Allowlist + regex (auto-recommended)

### Q4 — Run-id binding

| Option | Description | Selected |
|--------|-------------|----------|
| bind_contextvars(run_id=uuid4()) at CLI entry | Every log line in a run carries the same correlation id. Required for audit trail. | ✓ (auto-recommended) |
| No run-id binding | Simpler. Loses cross-layer correlation in audit. | |

**User's choice:** bind_contextvars uuid4 (auto-recommended)

---

## Claude's Discretion

- **Broker ABC surface (PaperBroker Q4)** — user said "[No preference]". Claude selected the minimal surface (5 methods) over the full or Protocol-decorated surfaces. Rationale: CLAUDE.md "don't design for hypothetical future requirements" + the surface will be exercised properly only in Phase 8 where IBKR's actual API shape becomes the constraint.
- **All Config-schema and Logging-mode decisions (Q1–Q4 of each)** — user said "do recommended settings for everything" after the PaperBroker area completed. Claude applied the recommended option (first option, marked Recommended) for each remaining question. Planner has flexibility on field names and pipeline ordering details so long as the high-level shape is preserved.

## Deferred Ideas

- Pre-commit hooks (ruff + mypy + pytest) — planner's discretion whether to add in Phase 0
- GitHub Actions CI — out of v1 scope (solo operator, no PRs)
- Auto-generated schema.sql tool — rejected for now (migrations are sole truth)
- Configurable PaperBroker slippage / partial-fills / rejects — rejected for Phase 0; future BacktestBroker territory
- Forward-declaring all ~30 tables in Phase 0 migration — rejected; each phase owns its tables
- Sentry / external error reporting — out of scope per PROJECT.md "no telemetry"
- PROMOTION.md paper→live ceremony — owned by Phase 10 (AUDIT-03)
