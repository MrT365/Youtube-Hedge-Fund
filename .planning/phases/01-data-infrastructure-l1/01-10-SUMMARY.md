---
phase: 01-data-infrastructure-l1
plan: 10
subsystem: testing
tags: [phase-1, integration-test, smoke-test, closure-gate, sc-binding, cp1, cp3, data-14, pytest]

requires:
  - phase: 01-data-infrastructure-l1
    provides:
      - "01-01..01-09 ship all 11 ingestion modules + orchestrator + CLI + DATA-14 seam — Plan 01-10 binds the 5 ROADMAP success criteria to automated tests."

provides:
  - "tests/integration/test_phase1_smoke.py — 31-test closure gate covering all 5 ROADMAP Phase 1 SCs."
  - "tests/fixtures/13f_information_table_fixture.xml — 4-position 13F INFORMATION TABLE fixture (AAPL/MSFT/NVDA/JPM) for 13F-ingestion future plans."
  - "Closure-gate semantics — passing this file is the Phase 1 advancement condition; failing means downstream phases must not advance."

affects:
  - "Phase 2 (factor scoring) — relies on the test harness to detect Phase 1 regressions before scoring runs against poisoned data."
  - "All future ingest phases — pattern of `class TestPhase{N}SC{M}` per-SC closure tests can be replicated."
  - "/gsd-verify-phase — uses this test file as the Phase 1 acceptance probe."

tech-stack:
  added: []
  patterns:
    - "Closure-gate integration test — one test class per ROADMAP SC; one parametrized test per CP-bound code (e.g. Form 4's 7 transaction codes)."
    - "Orchestrator step-adapter patching — `patch('ls_equity_fund.data.orchestrator._refresh_X_step')` is the documented test seam; the 11 lazy-import wrappers in orchestrator.py are designed for this."
    - "Workspace fixture — config.yaml.example + .env synthesized in tmp_path with cache_dir + log_dir repointed; mirrors the Phase 0 doctor_workspace and Phase 1 data_cmd _setup_workspace patterns."

key-files:
  created:
    - "tests/integration/test_phase1_smoke.py — 31 tests across 6 classes (5 SC + 1 closure invariant)."
    - "tests/fixtures/13f_information_table_fixture.xml — 4-position 13F XML fixture."
  modified: []

key-decisions:
  - "Use MagicMock providers + step-adapter patches throughout — real network calls would make the smoke flaky and slow. Real-network smoke is a manual operator check (`meridian run-data --no-filings --no-13f` against a real .env), recommended in this SUMMARY but NOT automated."
  - "One pytest class per SC — `class TestPhase1SC{N}` mirrors the Phase 0 pattern; failures self-document in pytest output as `TestPhase1SC3Form4Codes::test_form4_code_roundtrip[F-form4_f_withhold.xml] FAILED`."
  - "Phase 0 closure invariants live at the bottom (`TestPhase1Closure`) — defense-in-depth probe to detect regressions Phase 1 code would otherwise hide."
  - "Add `test_phase1_migration_at_head_after_upgrade` asserting `alembic_version == '0002'` — when a future phase ships migration 0003, this assertion will trip and the closure gate will move with it."

patterns-established:
  - "PIT integrity binding — every CP1 test asserts BOTH `delisted_date IS NOT NULL` AND total row count is preserved (catches accidental `DELETE FROM` regressions in merge logic)."
  - "Form 4 code parametrization — `@pytest.mark.parametrize('code,filename', [(P, p.xml), (S, s.xml), ...])` over the 7-letter VALID_TRANSACTION_CODES set; full schema CHECK constraint tested separately."
  - "Cluster-buy P-only assertion — insert P + A transactions for the same ticker, assert `distinct_insiders == 3` (P count) NOT 6 (P + A); proves CP3-aligned filter."
  - "Provider seam dual probe — instantiate stub directly + run CLI with `provider: polygon` set to verify both unit-level and end-to-end DATA-14 wiring."

requirements-completed: [DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07, DATA-08, DATA-09, DATA-10, DATA-11, DATA-12, DATA-13, DATA-14]

duration: 22min
completed: 2026-05-04
---

# Phase 1 Plan 10: Closure-Gate Integration Test Summary

**31-test pytest closure gate that binds all 5 Phase 1 ROADMAP success criteria — CP1 (universe PIT survivorship), CP3 (Form 4 7-code round-trip + cluster-buy P-only), and DATA-14 (PolygonProvider swap-in seam) — to automated assertions; passing this file is the Phase 1 advancement condition.**

## Performance

- **Duration:** ~22 min
- **Completed:** 2026-05-04
- **Tasks:** 1
- **Files created:** 2 (test + fixture)
- **Files modified:** 0

## Accomplishments

- 5 SC classes, one per ROADMAP Phase 1 success criterion, with explicit CP1 / CP3 / DATA-14 binding tests embedded.
- 7-code parametrized Form 4 round-trip test — every transaction letter (P/S/A/M/F/G/D) parses, persists, and SELECTs back with the correct `transaction_code` column populated.
- Schema-level CP3 guard — `INSERT ... transaction_code='X'` raises `sqlite3.IntegrityError`, proving the migration 0002 CHECK constraint enforces the 7-letter set.
- Cluster-buy P-only test — 3 P + 3 A transactions on the same ticker; detector reports `distinct_insiders=3` (P only), proving compensation grants do NOT pollute the directional buy signal.
- Orchestrator end-to-end probe — patches all 11 step adapters, asserts every key in the manifest, and verifies the `runs` row closes with `status='OK'` and `error IS NULL`.
- DATA-14 dual probe — `PolygonProvider(api_key="dummy")` instantiates AND `provider: polygon` in config.yaml routes through CLI exit code 6 with `DATA-14` in stderr.
- Phase 0 invariant probe — `meridian doctor --help` and `meridian run-data --help` still exit 0; alembic head still resolves to `'0002'`; all 13 Phase 1 tables + 2 Phase 0 tables present.

## Task Commits

1. **Task 1: 13F fixture XML + tests/integration/test_phase1_smoke.py with all 5 SC tests** — `4b880cf` (test)

## Files Created

- `tests/integration/test_phase1_smoke.py` (~770 lines, 31 tests, 6 classes) — Phase 1 closure-gate integration test.
- `tests/fixtures/13f_information_table_fixture.xml` — 4-position 13F INFORMATION TABLE (AAPL / MSFT / NVDA / JPM); reserved for the eventual full 13F end-to-end test (currently unused — institutional ingest path is exercised by orchestrator-level patches).

## Decisions Made

- **Mocking depth:** Patches at the orchestrator step-adapter boundary (`_refresh_X_step` lazy-import wrappers in `data/orchestrator.py`), NOT at the provider boundary. This proves the orchestrator wires every step into the manifest + commits a `runs` row; the underlying refresh-function bodies are exercised by the per-plan unit tests (Wave 2).
- **Workspace fixture:** Built `workspace` fixture inline in this file rather than importing from `tests/conftest.py` — Phase 1 smoke needs cache_dir AND log_dir BOTH repointed under tmp_path AND the migrated DB at the canonical `cache_dir/ls_equity_fund.db` path.
- **Closure invariant section:** Added `TestPhase1Closure` (4 tests) at the bottom — its sole purpose is to detect Phase 0 regressions caused by Phase 1 work (doctor surface, run-data --help surface, alembic head, table set). When a future phase ships migration 0003, the head-assertion will trip — that is the intended signal to advance the closure gate forward.
- **13F fixture committed but not yet referenced by an automated test:** the orchestrator-level `_refresh_13f_step` patch covers SC2; a future plan can land an end-to-end 13F test against this fixture without re-creating it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's SC1 tests called `load_config(yaml_path=EXAMPLE_YAML)` without `env_path`**

- **Found during:** Task 1 (first test run)
- **Issue:** `load_config(yaml_path=...)` without `env_path` caused pydantic-settings to instantiate `Secrets()` against the process environment. The autouse `isolate_env` fixture in `tests/conftest.py` strips `ANTHROPIC_API_KEY` and `SEC_USER_AGENT` from the environment between tests, so `Secrets()` raised `ValidationError` (2 missing fields). Three SC1 tests crashed in setup — symptom-only, not surfaced to the operator until execution.
- **Fix:** All three SC1 tests now take the `workspace` fixture as a parameter and call `load_config(yaml_path=workspace["config"], env_path=workspace["env"])`. The workspace fixture writes a deterministic `.env` with both required secrets.
- **Files modified:** tests/integration/test_phase1_smoke.py (3 test signatures + 3 load_config calls)
- **Verification:** All 31 tests pass.
- **Committed in:** 4b880cf (Task 1 commit)

**2. [Rule 2 - Missing Critical] Plan's draft used `# type: ignore[abstract]` comments that mypy rejected**

- **Found during:** Task 1 (mypy clean check)
- **Issue:** Two `cls()  # type: ignore[abstract]` comments in SC5 tests triggered `unused-ignore` errors under our project mypy config. Without the fix, mypy fails on the new file even though pytest passes.
- **Fix:** Removed both `# type: ignore[abstract]` comments — `pytest.raises(TypeError)` already documents the intent; mypy doesn't flag instantiation-of-ABC at the call site under our settings.
- **Files modified:** tests/integration/test_phase1_smoke.py (2 comment removals)
- **Verification:** `mypy tests/integration/test_phase1_smoke.py` — Success: no issues found.
- **Committed in:** 4b880cf (Task 1 commit)

**3. [Rule 2 - Missing Critical] `conn` fixture lacked return-type annotation**

- **Found during:** Task 1 (mypy clean check)
- **Issue:** The shared `conn` fixture used `def conn(migrated_db: Path):` (no return type) which mypy flagged as `no-untyped-def`.
- **Fix:** Annotated as `def conn(migrated_db: Path) -> Iterator[sqlite3.Connection]:` and added `from collections.abc import Iterator` to imports.
- **Files modified:** tests/integration/test_phase1_smoke.py (1 import + 1 signature)
- **Verification:** `mypy` clean.
- **Committed in:** 4b880cf (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (1 bug, 2 missing critical type-correctness)
**Impact on plan:** All three deviations were necessary to land a green test file with mypy + ruff clean. None expanded the test scope; the SC bindings, parametrizations, and assertion shape match the plan's intent.

## Issues Encountered

None during planned work — the three deviations above were caught by the test/lint sequence and fixed in-loop within the single task commit.

## Threat Model Disposition (from PLAN.md)

- **T-01-31 (Tampering — false-positive smoke):** mitigated. Tests assert table CONTENT (rows persisted, columns populated) AND orchestrator manifest fields, not just "no exception". The 7-code parametrized Form 4 test reads real fixture XML through `EdgarProvider.parse_form4` and round-trips through `_insert_filing` + `_insert_insider` — a parser regression would surface a row-count or column-value mismatch.
- **T-01-32 (DoS — slow tests):** accepted. Full file runs in ~1.5s on this machine (alembic upgrade is the dominant cost; one upgrade per migrated_db fixture invocation). Total suite at 278 tests in ~7.5s.
- **T-01-33 (Repudiation — passes against mocks while real network broken):** accepted-with-known-limit. Operator-driven check recommended below.

## Operator-Driven Verification (NOT Automated)

Before merging Phase 1, the operator should run ONCE on a healthy network:

```bash
cp config.yaml.example config.yaml  # if not already present
cp .env.example .env  # populate ANTHROPIC_API_KEY + SEC_USER_AGENT
uv run meridian run-data --no-filings --no-13f
```

Expected: exit code 0, `runs` row written with `status='OK'`, daily_prices populated for the configured tickers, structlog audit trail in `logs/YYYY-MM-DD.jsonl`. This catches yfinance / fed.gov rate-limit / TLS regressions that the mocked test cannot see.

Should this real-network probe fail, file a Phase 1 follow-up plan rather than blocking the closure gate — the live-feed fragility is a known surface (yfinance has had a turbulent 2025-2026; CLAUDE.md flags this).

## Next Phase Readiness

- Phase 1 closure gate is GREEN: 31/31 Phase 1 smoke tests pass; 278/278 total suite passes; ruff lint + format clean; mypy clean on the new file.
- Phase 2 (factor scoring) can begin against the L1 schema with confidence: every ingestion path has a per-plan unit test PLUS a binding closure assertion in this file. A Phase 2 change that breaks Phase 1 contracts (e.g., touches `insider_transactions.transaction_code`) will trip a specific SC test, not a vague unit failure.
- Closure invariant `test_phase1_migration_at_head_after_upgrade` asserts `alembic_version == '0002'`. When Phase 2 ships migration 0003, advance that assertion to `'0003'` (and re-evaluate the closure-gate philosophy — the file may want to assert head `>= '0002'` instead).

## Self-Check: PASSED

- File `tests/integration/test_phase1_smoke.py` exists at expected path.
- File `tests/fixtures/13f_information_table_fixture.xml` exists at expected path.
- Commit `4b880cf` exists in `git log --oneline`.
- `uv run pytest tests/integration/test_phase1_smoke.py -v` reports 31 passed.
- `uv run pytest -q` reports 278 passed (full suite, no regressions).
- `uvx ruff check tests/integration/test_phase1_smoke.py` reports All checks passed.
- `uvx ruff format --check tests/integration/test_phase1_smoke.py` reports already formatted.
- `mypy tests/integration/test_phase1_smoke.py` reports Success: no issues found.

---
*Phase: 01-data-infrastructure-l1*
*Plan: 10*
*Completed: 2026-05-04*
