# Phase 0: Foundation - Context

**Gathered:** 2026-05-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 0 bootstraps the project so every later phase runs against a working spine. It delivers:

1. **Repo + tooling** — `pyproject.toml` + `uv.lock` with pinned versions; `.gitignore` covering `.env`, `cache/`, `output/`, `logs/` while keeping `.planning/` tracked; ruff configured.
2. **Module layout** — `src/ls_equity_fund/{data,factors,analysis,portfolio,risk,execution,reporting,dashboard,cli}/` with public-façade `__init__.py` per layer, `_private` internals.
3. **Config + secrets** — `config.yaml` at repo root, pydantic-settings validation at startup, `.env` for secrets.
4. **SQLite + Alembic** — `cache/ls_equity_fund.db` opened in WAL mode; Alembic configured at `migrations/`; initial migration creates only the tables Phase 0 itself uses (`runs`, heartbeat).
5. **Three swap-in seam ABCs** — `MarketDataProvider`, `Optimizer`, `Broker` declared as abstract base classes; concrete `PaperBroker` stub implements the minimal `Broker` surface with a deterministic-fill contract.
6. **CLI skeleton** — Typer-based `python -m ls_equity_fund.cli` with subcommand dispatch; `meridian doctor` smoke command that loads config, opens the DB in WAL mode, applies migrations, and exits 0.
7. **structlog audit** — auto-detect renderer (TTY → console, redirect → JSON), API-key redaction, run-id correlation, dual-sink (stdout + rotating `logs/{date}.jsonl`).

**Out of scope for Phase 0** — anything that fills business tables (DATA, SCORE, ANAL, PORT, RISK, EXEC, REPORT, DASH); the launchd plist (Phase 10); the AUDIT-01 audit-log tables (Phase 6); concrete `MarketDataProvider` / `Optimizer` implementations beyond the ABCs themselves.

</domain>

<decisions>
## Implementation Decisions

### Alembic-without-ORM strategy
- **D-01:** **Migration authoring style — raw SQL via `op.execute()`.** Each migration is a Python file containing literal `op.execute("CREATE TABLE ...")` statements. Maximum auditability — what the operator reads is what runs against the DB. No SQLAlchemy types leak in. Loses Alembic's `--autogenerate`, but the audit-grade-SQL philosophy (CLAUDE.md "audit is a spec requirement") wins over autogen convenience.
- **D-02:** **Initial migration scope — Phase 0 only.** The first migration creates only the tables Phase 0 itself uses: a `runs` table (`run_id TEXT PRIMARY KEY, start_ts INTEGER, end_ts INTEGER, status TEXT, error TEXT`) and any heartbeat-related infrastructure. Future phases add their own migrations for their own tables. Keeps the diff small and reviewable.
- **D-03:** **Migrations directory — `migrations/versions/` at repo root.** Conventional Alembic layout. `alembic.ini` at repo root points to `migrations/`. Migrations are operational artifacts, not part of the installable package — sibling of `cache/`, `output/`, `tests/`, `scripts/`.
- **D-04:** **Schema source-of-truth — migrations only.** No `schema.sql` or auto-dump file. Cumulative state is reconstructed by reading every migration in order. `sqlite3 cache/ls_equity_fund.db .schema` is the on-demand inspection tool. Eliminates drift risk.
- **D-05:** **SQLite ALTER limits — use Alembic `batch_alter_table` for column changes.** SQLite cannot drop or rename columns directly; Alembic's batch mode rewrites the table. Required by INFRA-02. Migration helper docs go in `migrations/README.md`.

### PaperBroker deterministic-fill contract
- **D-06:** **Default fill price — fill at `signal_price` exactly with zero slippage.** Every accepted order fills at the limit/signal price the executor passed in. The slippage tracker (Phase 8 EXEC-04) records 0 bps against PaperBroker, which is a known/expected baseline. Real slippage simulation belongs to IBKR paper or a future `BacktestBroker`.
- **D-07:** **Fill behavior — always full fill, never reject.** PaperBroker accepts every order and fills 100% in one shot. The pre-trade veto (Phase 6) is the only thing that rejects trades. This keeps the executor harness deterministic for unit tests of the L4→L5→L6 spine; partial-fill state machines come naturally from IBKR paper in Phase 8.
- **D-08:** **State persistence — in-memory only.** PaperBroker keeps order/fill/position state in Python data structures for the lifetime of the process. Each test starts with a fresh broker. No SQLite tables added by Phase 0 for paper orders. The IBKR-paper executor in Phase 8 owns the persistent `orders` / `fills` schema.
- **D-09:** **Broker ABC surface — minimal in Phase 0, expanded in Phase 8** (Claude's discretion). Phase 0 ABC declares only what's needed to run the spine: `place_order(order) -> OrderId`, `get_order(id) -> Order`, `get_positions() -> list[Position]`, `cancel(id) -> None`, `is_paper: bool` property. Phase 8 expands the ABC to add IBKR-specific methods (borrow check, ADV chunking, slippage hooks) when those needs are real. Avoids overdesigning for a surface that will change once IBKR's API is exercised.
- **D-10:** **`is_paper` is the connection between PaperBroker and the live-mode gate.** PaperBroker.is_paper returns True. The MERIDIAN_LIVE_OK env-var check (Phase 8) refuses to instantiate any non-paper broker without both the env-var AND the AUDIT-03 promotion record — that gate logic is Phase 8's, but the `is_paper` property must exist on the ABC from Phase 0 so downstream callers can branch on it.

### Config schema composition + secrets
- **D-11:** **Composed pydantic models** (auto-recommended). Top-level `Config` is a pydantic-settings model with nested fields: `Config(data: DataConfig, broker: BrokerConfig, risk: RiskConfig, portfolio: PortfolioConfig, anthropic: AnthropicConfig, logging: LoggingConfig)`. Each layer reads its own slice — `from ls_equity_fund.config import config; config.broker.paper_port`. Easier to evolve a single layer without touching others; field-level validation co-locates with each layer's concerns.
- **D-12:** **`config.yaml` at repo root** (auto-recommended; matches research ARCHITECTURE.md §3). Single canonical config file, loaded by pydantic-settings via PyYAML `safe_load` only. No round-tripping (PyYAML, not ruamel.yaml).
- **D-13:** **Env-var nesting via `env_nested_delimiter='__'`** (auto-recommended). `BROKER__PAPER_PORT=7497` in `.env` flows into `Config.broker.paper_port`. Standard pydantic-settings pattern; documented in CLAUDE.md as the recommended convention for this project.
- **D-14:** **Secrets isolation — separate `Secrets` settings class loaded ONLY from `.env`** (auto-recommended). `Secrets(anthropic_api_key, ibkr_username, ibkr_password, sec_user_agent)` is a distinct pydantic-settings class with `model_config = SettingsConfigDict(env_file='.env', extra='ignore')` and **no YAML loader**. The main `Config` references `Secrets` via dependency injection, never embedding them. This guarantees secrets cannot accidentally land in `config.yaml` (which is checked into git) and keeps the redaction processor's job simple — it only needs to know about the `Secrets` field set.
- **D-15:** **Validation at boot.** pydantic-settings validates the loaded `Config` immediately on import. A bad config raises before any layer runs — INFRA-01 requirement: "surfaces errors at startup".

### Logging output mode + redaction
- **D-16:** **structlog renderer — auto-detect** (auto-recommended). When `sys.stderr.isatty()` is True, structlog uses `ConsoleRenderer` (colorized key=value, dev-friendly). When stderr is not a TTY (e.g., launchd, redirect to file), it uses `JSONRenderer`. Operators on the terminal get readable output; the launchd job writes JSON suitable for `jq` and audit ingestion.
- **D-17:** **Dual sink — stdout + rotating file** (auto-recommended). Logs flow to stdout AND to a rotating file at `logs/{YYYY-MM-DD}.jsonl` (one file per UTC day, opened append). The file is the audit-trail backbone (AUDIT-02); stdout is for live tailing during dev. Rotation by date is sufficient — no size-based rotation needed at this volume.
- **D-18:** **Redaction — allowlist + regex (defense-in-depth)** (auto-recommended). A structlog processor sits early in the pipeline and:
  1. Allowlist match on common secret keys: `api_key`, `apikey`, `password`, `passwd`, `token`, `secret`, `authorization`, `auth`, `key` — replaces value with `***REDACTED***`.
  2. Regex pass on every string value: `sk-ant-[A-Za-z0-9_-]+` (Anthropic), `Bearer\s+[A-Za-z0-9_.-]+` (auth headers), generic `[A-Za-z0-9_-]{32,}` is NOT redacted (too aggressive — would mask UUIDs and order IDs); only known-secret patterns.
  3. Test fixture: a unit test logs a sample event with `api_key="sk-ant-FAKE"` and asserts the rendered output contains `***REDACTED***` (Phase 0 success criterion 4).
- **D-19:** **Run-id correlation via `bind_contextvars(run_id=uuid4())`** (auto-recommended). Every CLI entry point (run-data, run-scoring, run-analysis, run-portfolio, run-execution, run-reporting, daily-refresh) starts by generating a run_id and binding it to structlog's contextvars. Every log line in that run carries the run_id. Required for the audit trail (per-run correlation across layers).
- **D-20:** **`logging.py` is the single configuration point.** `src/ls_equity_fund/logging.py` exports `configure_logging(config: LoggingConfig) -> None` called once from the CLI entry point before any other module logs. Third-party libraries (`anthropic`, `ib_async`, `requests`) flow through stdlib `logging` which structlog wraps — single pipeline, single redaction policy.

### Carrying-forward decisions (locked upstream — re-stated here so planning doesn't re-debate)
- **D-21:** **Tech stack pins** (CLAUDE.md, research/STACK.md): Python 3.11+, uv, ruff, `pandas>=2.2,<3.0`, `numpy>=2.0,<2.5`, `ib_async==2.1.x`, `edgartools>=5.30,<6`, `anthropic>=0.97`, `scipy>=1.16,<1.18`, `pydantic 2.13.x`, `pydantic-settings`, `structlog 25.5.0`, `PyYAML 6.0.3` (`safe_load` only), `python-dotenv 1.2.2`, `pytest 9.0.3` + `pytest-asyncio 1.3.0` + `freezegun 1.5.5` + `responses 0.26.0`, `mypy 1.20.2` (`--strict` only on `src/risk/` and `src/portfolio/` initially — Phase 0 only sets up the config; the strict surface grows as those packages do).
- **D-22:** **Module layout** (research/ARCHITECTURE.md §3): `src/ls_equity_fund/{config,db,schemas,logging,data,factors,analysis,portfolio,risk,execution,reporting,dashboard,cli}/` with `tests/{unit,integration,fixtures}/`, `scripts/`, `cache/`, `output/`, `logs/`, `migrations/versions/` at repo root.
- **D-23:** **CLI library — Typer** (research/ARCHITECTURE.md §2). One `python -m ls_equity_fund.cli` entry point with Typer subcommands (`daily-refresh`, `run-data`, `run-scoring`, `run-analysis`, `run-portfolio`, `run-execution`, `run-reporting`, `doctor`). Phase 0 ships the skeleton — most subcommands are stubs that print "not implemented in this phase" and exit 0.
- **D-24:** **`.gitignore` contents** (INFRA-06): excludes `.env`, `cache/`, `output/`, `logs/`, plus the standard Python set (`__pycache__/`, `.venv/`, `*.egg-info`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`). Tracks `.planning/`, `pyproject.toml`, `uv.lock`, `config.yaml.example`, `.env.example`.
- **D-25:** **Smoke command — `meridian doctor`.** Phase 0 success criterion 2 mandates a CLI smoke that (a) loads `config.yaml` via pydantic-settings (b) opens SQLite at the configured path in WAL mode (c) runs `alembic upgrade head` to apply pending migrations (d) emits a structured "doctor passed" log line and exits 0. Idempotent — re-running it on a healthy system does nothing destructive. (Doctor verifies; it does NOT initialize secrets — `.env` must already exist.)

### Claude's Discretion
- **CD-01:** Broker ABC surface (D-09) — user said "[No preference]"; recommended decision applied. Plan should keep the surface minimal.
- **CD-02:** All Config-schema and Logging questions (D-11 through D-20) — user said "do recommended settings for everything"; recommended decisions captured. Planner has flexibility to fine-tune field names and processor pipeline order so long as the high-level shape (composed Config, separate Secrets, auto-detect renderer, dual sink, allowlist+regex redaction, run-id contextvars) is preserved.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level (canonical for the whole project)
- `.planning/PROJECT.md` — project core value, constraints, key decisions; every phase reads this
- `.planning/REQUIREMENTS.md` — 90 v1 requirements with phase traceability; Phase 0 owns INFRA-01, INFRA-02, INFRA-03, INFRA-06, INFRA-07, INFRA-08, AUDIT-02
- `.planning/ROADMAP.md` — 11-phase plan with Phase 0 success criteria (4 numbered criteria) and dependency graph
- `CLAUDE.md` — tech-stack table, anti-recommendations, 2025–2026 deprecation watchlist, installation reference, confidence index

### Research (consulted to lock pre-Phase-0 decisions)
- `.planning/research/SUMMARY.md` — synthesized confidence; "Stack at a Glance" table is the authoritative pin list
- `.planning/research/STACK.md` — per-library version + rationale + alternatives considered + sources
- `.planning/research/ARCHITECTURE.md` §2 + §3 — component diagram, module layout (the layout this phase scaffolds)
- `.planning/research/PITFALLS.md` — CP1–CP5 critical pitfalls; only AUDIT-relevant ones (logging redaction) are Phase 0 — but planner reads it for awareness of downstream landmines

### Spec sections that bind Phase 0 success criteria
- ROADMAP.md → "Phase 0: Foundation" Success Criteria 1–4 (uv build, smoke command, three ABCs + PaperBroker, gitignore + structlog redaction)
- REQUIREMENTS.md → INFRA-01 (config.yaml + .env + pydantic-settings), INFRA-02 (SQLite WAL + Alembic + batch_alter_table), INFRA-03 (`src/ls_equity_fund/...` layout + 3 seams), INFRA-06 (`.gitignore`), INFRA-07 (Python 3.11 via uv + pinned versions), INFRA-08 (CLI entrypoints + shared flags), AUDIT-02 (structlog with API-key redaction)

### External reference docs (read on-demand during planning/research)
- pydantic-settings docs — https://docs.pydantic.dev/latest/concepts/pydantic_settings/ (composed models, env_nested_delimiter, env_file isolation)
- structlog docs — https://www.structlog.org/ (processor pipeline, ConsoleRenderer / JSONRenderer, bind_contextvars)
- Alembic + SQLite batch_alter_table — https://alembic.sqlalchemy.org/en/latest/batch.html
- uv project tooling — https://docs.astral.sh/uv/

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **None yet.** Repo contains only `.planning/` and `CLAUDE.md`. Phase 0 is the first phase that writes code.

### Established Patterns
- **None yet.** Phase 0 establishes the patterns. Specifically: composed pydantic-settings, raw-SQL Alembic migrations, structlog processor pipeline, Typer CLI dispatch, ABC-with-`is_paper` for the Broker seam.

### Integration Points
- **`src/ls_equity_fund/db.py` is the central SQLite gateway** — every layer that persists imports `from ls_equity_fund.db import get_connection`. Phase 0 defines `get_connection() -> sqlite3.Connection` with `PRAGMA journal_mode=WAL` set on first open, `row_factory=sqlite3.Row`, `detect_types=PARSE_DECLTYPES | PARSE_COLNAMES` for timestamp handling.
- **`src/ls_equity_fund/config.py` is loaded once at import** — every layer that reads config does `from ls_equity_fund.config import config`. Phase 0 ensures pydantic-settings validation fires at module import; a bad config raises before anything else runs.
- **`src/ls_equity_fund/logging.py:configure_logging(config.logging)` is called exactly once at the CLI entry point** before any other module logs. Layers do `log = structlog.get_logger(__name__)` and never reconfigure.
- **`src/ls_equity_fund/{data,portfolio,execution}/base.py` host the three seam ABCs.** Concrete implementations live as siblings (`data/providers/yfinance_provider.py`, etc.), not in `base.py`. Phase 0 only writes the ABCs and one concrete `PaperBroker`; Phase 1+ add the rest.

</code_context>

<specifics>
## Specific Ideas

- **`is_paper: bool` property on the Broker ABC is non-negotiable** — it's the API surface the live-mode gate (Phase 8) keys off. Even though MERIDIAN_LIVE_OK enforcement is Phase 8's, the property must exist from Phase 0.
- **Smoke command is `meridian doctor`, not `meridian smoke` or `meridian init`** — "doctor" is the conventional name for an idempotent health check (Homebrew, Flutter, Rails). Communicates "I check; I do not initialize."
- **Run-id is a UUID4 generated at the CLI entry point** — bound via `structlog.contextvars.bind_contextvars(run_id=...)`. Logged as a top-level field on every event. The eventual `runs` table primary key is `run_id` (TEXT, the UUID4 string).
- **`logs/{YYYY-MM-DD}.jsonl` rotation is by UTC date, not local date** — avoids ambiguity around DST transitions. Operator-local time is in the log payload, not the filename.
- **Redaction regex does NOT include a generic "long random-looking string" pattern** — that would falsely redact UUIDs (run_id, order_id) and SHA hashes. Only known-secret patterns (`sk-ant-*`, `Bearer *`) plus the explicit field allowlist.

</specifics>

<deferred>
## Deferred Ideas

- **Pre-commit hooks (ruff + mypy + pytest)** — not blocking Phase 0 success criteria. Could be added in Phase 0 as nice-to-have or deferred. Planner's discretion.
- **GitHub Actions CI** — solo operator on macOS, no team, no PRs. Out of v1 scope unless explicitly added later.
- **`schema.sql` auto-dump tool** — rejected for now (D-04). If schema introspection becomes painful, can add later as `make schema` target.
- **Configurable PaperBroker slippage / partial-fills / rejects** — rejected for Phase 0 (D-06, D-07). Belongs in a future `BacktestBroker` if backtesting becomes a milestone (currently v2 BACKTEST-01).
- **Forward-declaring all ~30 tables in Phase 0's initial migration** — rejected (D-02). Each phase owns its tables.
- **Sentry / structured-error reporting service integration** — out of scope; PROJECT.md mandates "no telemetry, no external reporting, all data local".
- **`PROMOTION.md` paper→live ceremony doc (AUDIT-03)** — owned by Phase 10, not Phase 0.

</deferred>

---

*Phase: 0-Foundation*
*Context gathered: 2026-05-04*
