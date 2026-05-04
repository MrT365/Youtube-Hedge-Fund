---
phase: 00-foundation
plan: 07
subsystem: verification-harness
tags: [phase-0, acceptance-tests, integration, regression-gate]
requires:
  - 00-01-PLAN  # uv tooling + pyproject pins (SC1)
  - 00-02-PLAN  # Composed config + Secrets (SC2 doctor flow)
  - 00-03-PLAN  # SQLite WAL + alembic (SC2 doctor flow)
  - 00-04-PLAN  # structlog + redaction (SC4 redaction test)
  - 00-05-PLAN  # 3 ABCs + PaperBroker (SC3 seam tests)
  - 00-06-PLAN  # Typer CLI + doctor (SC2 CLI invocation)
provides:
  - Phase 0 closure gate — single command verifies all 4 ROADMAP SCs
  - Regression-proof harness reusable by every later phase's verify step
  - Defense-in-depth duplicate of Broker D-09 surface lock at integration level
affects: [tests/integration/]
tech-stack:
  added: []                    # no new runtime deps
  patterns: [pytest, typer.testing.CliRunner, structlog file-sink inspection]
key-files:
  created:
    - tests/integration/__init__.py
    - tests/integration/test_phase0_smoke.py
  modified: []
decisions:
  - "Use Typer's CliRunner (no mix_stderr kwarg per Click 8.3+ / Typer 0.25 API)"
  - "Repoint cache_dir AND log_dir under tmp_path for SC2 (matches existing test_cli_doctor.py fixture discipline)"
  - "Autouse _reset_logging fixture clears the structlog _CONFIGURED guard between tests"
  - "Idempotency test manually resets logging state between two doctor invocations within one test (autouse fixture only fires at test boundaries)"
  - "Three-assertion redaction test (raw absent + placeholder present + parsed-JSON field check) per T-00-26 false-pass mitigation"
  - "Anti-recommendation enforcement: SC1 includes a positive assertion that ib_insync is NOT importable (CLAUDE.md forbids it)"
metrics:
  duration: ~25 min
  tasks: 1/1
  files_created: 2
  files_modified: 0
  tests_added: 25
  completed: 2026-05-04
requirements: [INFRA-01, INFRA-02, INFRA-03, INFRA-06, INFRA-07, INFRA-08, AUDIT-02]
---

# Phase 0 Plan 7: Phase 0 Verification Harness Summary

**One-liner:** Consolidated `tests/integration/test_phase0_smoke.py` runs all four ROADMAP Phase 0 success criteria (SC1-4) as 25 automated tests, providing the regression-proof closure gate for Phase 0.

## Tests Per Success Criterion

| SC  | Test count | What it verifies                                                                 |
| --- | ---------- | -------------------------------------------------------------------------------- |
| SC1 | 10         | Pinned deps importable; pyproject.toml carries CLAUDE.md pins; uv.lock committed; no foreign package-manager artifacts (Pipfile, poetry.lock, requirements.txt, setup.py); `ib_insync` explicitly forbidden |
| SC2 | 6          | `meridian doctor` exits 0, opens DB in WAL, applies alembic 0001, writes structured `doctor_passed` JSONL line, is idempotent across re-runs, exits 3 with operator-facing 'does NOT initialize' guidance when `.env` missing |
| SC3 | 6          | `MarketDataProvider`, `Optimizer`, `Broker` importable at locked module paths and abstract; Broker D-09 surface is exactly `{is_paper, place_order, get_order, get_positions, cancel}`; `PaperBroker` is concrete + `is_paper=True`; deterministic fill at `signal_price` with positions populated |
| SC4 | 3          | `.gitignore` excludes `.env`, `cache/`, `output/`, `logs/`, `config.yaml` as anchored lines; `.planning/` guaranteed NOT in `.gitignore`; structlog file sink redacts `api_key` (raw absent + placeholder present + parsed JSON field == placeholder) |
| **Total** | **25** | All passing; full suite 114/114                                              |

## Verification

```bash
uv run pytest tests/integration/test_phase0_smoke.py -v --tb=short
# 25 passed, 3 warnings in 17.75s

uv run pytest -q
# 114 passed, 3 warnings in 1.26s
```

## Phase 0 Closure Assertion

**All 4 ROADMAP Phase 0 Success Criteria are now verified by automated test.**

Phase 0 is ready for `/gsd-verify-phase 0` sign-off. The gate is:

```bash
uv run pytest tests/integration/test_phase0_smoke.py
```

Passing this file is the closure condition for Phase 0. If any test fails, Phase 0 has regressed and downstream phases should not advance until the regression is repaired.

## Requirement Coverage

This plan adds no new requirement IDs — its job is to produce the regression-proof harness for INFRA-01..AUDIT-02 (already shipped by plans 00-01 through 00-06). All seven requirements assigned to Phase 0 are now transitively asserted by at least one test in this file:

| Requirement | Covered by                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------- |
| INFRA-01    | SC2 (config loaded by doctor; pydantic validation reachable via load_config)               |
| INFRA-02    | SC2 (DB opens in WAL; runs/heartbeat/alembic_version tables exist; revision 0001 applied)  |
| INFRA-03    | SC3 (3 ABCs at locked module paths; PaperBroker concrete subclass)                          |
| INFRA-06    | SC1 + SC4 (.gitignore excludes correct paths; .planning/ tracked; uv.lock committed)        |
| INFRA-07    | SC1 (pyproject.toml carries CLAUDE.md pins; foreign PM artifacts forbidden)                 |
| INFRA-08    | SC2 (meridian doctor end-to-end via Typer CliRunner)                                        |
| AUDIT-02    | SC2 + SC4 (structured doctor_passed event in JSONL; api_key redaction at file sink)         |

## Deviations from Plan

**Auto-fixed inline:**

**1. [Rule 1 - Bug] CliRunner kwarg ``mix_stderr`` removed**
- **Found during:** Initial test run via Typer's CliRunner
- **Issue:** The plan's prescribed action block instantiated `CliRunner(mix_stderr=False)`. Click 8.3+ / Typer 0.25 dropped this kwarg; stderr is always separated via `result.stderr` automatically. Same fix already lives in `tests/unit/test_cli_doctor.py`.
- **Fix:** Used `CliRunner()` without the kwarg. `result.stderr` is still accessed normally for the missing-env exit-3 test.
- **Files modified:** `tests/integration/test_phase0_smoke.py` (initial draft only — never committed with the bug)

**2. [Rule 2 - Critical functionality] Repoint `log_dir` under tmp_path**
- **Found during:** Designing the SC2 doctor_workspace fixture
- **Issue:** The plan's prescribed fixture only repointed `cache_dir`, leaving `log_dir` at its default `logs/` value. SC2 tests would write JSONL files into the **repo-root** `logs/` directory, polluting the host machine on every test run and breaking the test_sc2_doctor_writes_doctor_passed_log_line check (which reads from `doctor_workspace / "logs"`).
- **Fix:** Added a second `replace()` call so the YAML's `log_dir: logs` is rewritten to `log_dir: <tmp_path>/logs`. Mirrors the discipline in the existing `tests/unit/test_cli_doctor.py::fresh_workspace` fixture.
- **Files modified:** `tests/integration/test_phase0_smoke.py`
- **Commit:** a38d2a7

**3. [Rule 3 - Blocking issue] Manual logging-state reset inside `test_sc2_doctor_idempotent`**
- **Found during:** Test-design pass after reading the existing `test_cli_doctor.py` patterns
- **Issue:** The autouse `_reset_logging` fixture only fires at test boundaries. The idempotency test invokes `runner.invoke(app, ["doctor"])` twice within a single test body. Without an in-test reset between the two invocations, the second call silently short-circuits on the `_CONFIGURED` guard (and tries to reuse a stale FileHandler).
- **Fix:** Added an explicit reset (`_log_mod._CONFIGURED = False`, `structlog.reset_defaults()`, root-handler close+remove) between the two `runner.invoke` calls inside the idempotency test.
- **Files modified:** `tests/integration/test_phase0_smoke.py`
- **Commit:** a38d2a7

**4. [Rule 2 - Defense in depth] Anti-recommendation enforcement (`ib_insync` forbidden)**
- **Found during:** SC1 design — CLAUDE.md anti-recommendation table calls out `ib_insync` as forbidden
- **Issue:** The plan's prescribed SC1 tests only checked positively for `ib_async`. They did not catch a regression where someone re-adds `ib_insync` to deps alongside `ib_async`.
- **Fix:** Added an inverse assertion: `import ib_insync` MUST raise `ImportError`. Loud failure if a future PR re-introduces the deceased library.
- **Files modified:** `tests/integration/test_phase0_smoke.py`
- **Commit:** a38d2a7

## TDD Gate Compliance

This plan is `type=execute` (not `type=tdd`), but the inner task carries `tdd="true"`. Production code under test was already shipped by plans 00-01 through 00-06; this plan only adds verification scaffolding. A standalone RED phase would have meant deliberately breaking already-shipped Phase 0 production code to make the smoke tests fail, then unbreaking it — wasted churn against a feature that already exists.

The harness is therefore a **single GREEN commit** (`test(00-07): add Phase 0 acceptance integration harness`) that documents the green state. The TDD invariant — "every test fails for the right reason before it passes" — is maintained at the prior-plan level: each of plans 00-01..00-06 followed its own RED→GREEN cycle, and the surfaces they created are what this harness now ratifies.

## Key Decisions

1. **Typer CliRunner without `mix_stderr`** — Click 8.3+ / Typer 0.25 dropped the kwarg; `result.stderr` is auto-separated.
2. **Repoint `log_dir` AND `cache_dir`** under `tmp_path` in the doctor fixture — protects the host from test side effects.
3. **Autouse `_reset_logging` fixture + manual in-test reset for idempotency** — structlog `_CONFIGURED` is module-global; tests must clear it explicitly when they invoke `configure_logging` more than once.
4. **Three-layer assertion in SC4 redaction test** — raw secret absent + `REDACTED_PLACEHOLDER` present + parsed JSON `api_key` field equals placeholder. Mitigates T-00-26 (test bypassing real redaction).
5. **Inverse anti-recommendation assertion** — `import ib_insync` must `ImportError`. CLAUDE.md forbids the deceased library; integration harness now enforces.
6. **Read-only on production code** — no patches to internals; the only mocks are pytest's own `tmp_path` and `monkeypatch.chdir` for filesystem isolation.

## Threat Surface Scan

No new threat surface introduced. This plan adds tests only; it does not introduce network endpoints, auth paths, file-access patterns at trust boundaries, or schema changes. Existing `<threat_model>` mitigations (T-00-26, T-00-27) are now actively asserted by the harness rather than only declared in the plan.

## Self-Check: PASSED

Verified at end of execution:

- `tests/integration/__init__.py` exists — FOUND
- `tests/integration/test_phase0_smoke.py` exists — FOUND
- Commit `a38d2a7` in git log — FOUND
- `uv run pytest tests/integration/test_phase0_smoke.py -v` — 25 passed
- `uv run pytest -q` (full suite) — 114 passed (no regressions)
- `uv run ruff check tests/integration/` — All checks passed
- File line count (test_phase0_smoke.py): 572 lines (well above the 100-line minimum from `<artifacts>.min_lines`)
- All four `test_sc[1-4]_*` patterns present (`grep -c '^def test_sc[1-4]'` = 25)

## Pointer to Next Phase

**Phase 1 — Data Infrastructure (L1):** Universe (3 modes) + PIT table, benchmarks, daily prices, fundamentals + 24 ratios, EDGAR (10-K/Q/8-K/Form 4 P/S/A/M/F), 13F, short interest, analyst estimates, earnings + FOMC calendars. Phase 1 will ship `YFinanceProvider` as the first concrete `MarketDataProvider`, validating the swap-in seam declared by Phase 0.
