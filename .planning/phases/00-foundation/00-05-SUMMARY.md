---
phase: 00-foundation
plan: 05
subsystem: infra
tags: [pydantic, abc, broker, paper-trading, module-layout, seams]

# Dependency graph
requires:
  - phase: 00-foundation
    provides: pyproject.toml + uv environment (Plan 00-01); src/ls_equity_fund package root (Plan 00-02 in parallel)
provides:
  - Module skeleton: src/ls_equity_fund/{data,factors,analysis,portfolio,risk,execution,reporting,dashboard}/ each with __init__.py (D-22)
  - Three swap-in seam ABCs (INFRA-03):
      - MarketDataProvider (data/base.py) - 4 abstract methods
      - Optimizer (portfolio/base.py) - 1 abstract method
      - Broker (execution/base.py) - exactly 5 abstract members locked by D-09
  - PaperBroker concrete (execution/paper_broker.py) implementing the deterministic-fill contract (D-06/07/08/10)
  - schemas.py: Order, Position, OrderId, Side, OrderStatus (Pydantic v2 + StrEnum)
  - Test scaffolding under tests/unit/ (test_seams.py, test_paper_broker.py, 25 tests, all passing)
affects: phase-01-data, phase-04-analysis, phase-05-portfolio, phase-06-risk, phase-07-mvo, phase-08-execution

# Tech tracking
tech-stack:
  added: []  # all libraries (pydantic, structlog, pytest) were already in pyproject.toml from Plan 00-01
  patterns:
    - "ABC-with-base.py convention for swap-in seams (data/base.py, portfolio/base.py, execution/base.py); concrete impls land as siblings (e.g. data/providers/yfinance_provider.py in Phase 1)"
    - "Pydantic v2 idioms: model_config = ConfigDict(extra='forbid'); StrEnum for string-valued enums (Python 3.11+)"
    - "structlog get_logger(__name__) per-module; every paper-broker fill emits paper_order_filled audit event"
    - "Test guard pattern: assert ABC.__abstractmethods__ == {locked-set} to catch silent surface drift (D-09 lock test)"

key-files:
  created:
    - src/ls_equity_fund/__init__.py
    - src/ls_equity_fund/schemas.py
    - src/ls_equity_fund/data/__init__.py
    - src/ls_equity_fund/data/base.py
    - src/ls_equity_fund/factors/__init__.py
    - src/ls_equity_fund/analysis/__init__.py
    - src/ls_equity_fund/portfolio/__init__.py
    - src/ls_equity_fund/portfolio/base.py
    - src/ls_equity_fund/risk/__init__.py
    - src/ls_equity_fund/execution/__init__.py
    - src/ls_equity_fund/execution/base.py
    - src/ls_equity_fund/execution/paper_broker.py
    - src/ls_equity_fund/reporting/__init__.py
    - src/ls_equity_fund/dashboard/__init__.py
    - tests/__init__.py
    - tests/unit/__init__.py
    - tests/unit/test_seams.py
    - tests/unit/test_paper_broker.py
  modified: []  # plan creates only

key-decisions:
  - "Broker ABC surface locked at exactly 5 abstract members per D-09 (is_paper, place_order, get_order, get_positions, cancel); guarded by test_broker_abc_surface_locked. Phase 8 owns expansion."
  - "PaperBroker fills synchronously at order.signal_price with zero slippage (D-06) and never rejects (D-07); cancel() of FILLED order raises ValueError because PaperBroker has no real PENDING window."
  - "Sign-flip semantics on a position (long -> short across zero, or vice versa) reset avg_cost to the new fill price; documented in code and not tested for in Phase 0 because Phase 0 callers are expected to close fully then re-open."
  - "Created src/ls_equity_fund/__init__.py as a minimal docstring + __version__ to make the package importable for tests; Plan 00-02 owns this file in its files_modified list, so the merge order will determine final content (worktree merge concern, not in-plan deviation)."
  - "tests/__init__.py and tests/unit/__init__.py created as empty markers for pytest discovery (not in plan files_modified but unavoidable for the test suite to import; tracked here)."

patterns-established:
  - "Layer __init__.py docstrings document the layer purpose, the public façade methods (planned), and the phase that owns concrete implementations"
  - "Pydantic 2 BaseModel with model_config = ConfigDict(extra='forbid') + Field(gt=0) for positive-int constraints"
  - "OrderId = NewType('OrderId', str) - opaque identifier without runtime overhead; future Phase 8 may upgrade to UUID"
  - "PaperBroker uses Order.model_copy(update={...}) for the fill state transition (immutable-ish update pattern)"

requirements-completed: [INFRA-03]

# Metrics
duration: ~25min
completed: 2026-05-04
---

# Phase 0 Plan 05: Module Layout + Seam ABCs + PaperBroker Summary

**Established the eight-package module skeleton, the three swap-in seam ABCs (MarketDataProvider, Optimizer, Broker), and a deterministic in-memory PaperBroker that lets the L4-L5-L6 spine run in unit tests before any IBKR connection exists.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-04T14:18Z (approx)
- **Completed:** 2026-05-04T14:24Z (approx)
- **Tasks:** 3/3
- **Files created:** 18 (14 source + 4 test)

## Accomplishments
- All eight layer packages (data, factors, analysis, portfolio, risk, execution, reporting, dashboard) exist with public `__init__.py`; the package layout matches D-22 / ARCHITECTURE.md §3.
- Three seam ABCs declared at their canonical paths (`data/base.py`, `portfolio/base.py`, `execution/base.py`); all three raise TypeError on direct instantiation.
- Broker ABC surface locked to EXACTLY {is_paper, place_order, get_order, get_positions, cancel} per D-09, with `test_broker_abc_surface_locked` guarding against accidental Phase 0 expansion.
- PaperBroker implements the deterministic-fill contract: fills at `order.signal_price` (D-06), always full fill / never rejects (D-07), in-memory state per instance (D-08), `is_paper == True` (D-10).
- 25 unit tests written, all passing (9 in test_seams.py, 16 in test_paper_broker.py); INFRA-03 closed.

## Task Commits

Each task was committed atomically:

1. **Task 1: Package skeleton + schemas + three seam ABCs** - `4fdc91c` (feat)
2. **Task 2: PaperBroker (deterministic-fill contract)** - `8310ade` (feat)
3. **Task 3: test_seams.py + test_paper_broker.py** - `135fd19` (test)

## Files Created

### Source (14 files)
- `src/ls_equity_fund/__init__.py` - package marker + __version__ (NOTE: also declared by Plan 00-02; merge will reconcile)
- `src/ls_equity_fund/schemas.py` - Order, Position, OrderId, Side, OrderStatus (Pydantic v2)
- `src/ls_equity_fund/data/__init__.py` - L1 layer marker; re-exports MarketDataProvider
- `src/ls_equity_fund/data/base.py` - MarketDataProvider ABC (4 methods: get_prices, get_fundamentals, get_short_interest, get_estimates)
- `src/ls_equity_fund/factors/__init__.py` - L2 layer marker (Phase 2)
- `src/ls_equity_fund/analysis/__init__.py` - L3 layer marker (Phase 4)
- `src/ls_equity_fund/portfolio/__init__.py` - L4 layer marker; re-exports Optimizer
- `src/ls_equity_fund/portfolio/base.py` - Optimizer ABC (1 method: optimize)
- `src/ls_equity_fund/risk/__init__.py` - L5 layer marker (Phase 6)
- `src/ls_equity_fund/execution/__init__.py` - L6 layer marker; re-exports Broker + PaperBroker
- `src/ls_equity_fund/execution/base.py` - Broker ABC (5 abstract members, locked by D-09)
- `src/ls_equity_fund/execution/paper_broker.py` - PaperBroker concrete (~150 lines)
- `src/ls_equity_fund/reporting/__init__.py` - L7 layer marker (Phase 9)
- `src/ls_equity_fund/dashboard/__init__.py` - dashboard layer marker (Phase 3)

### Tests (4 files)
- `tests/__init__.py` - empty (pytest discovery)
- `tests/unit/__init__.py` - empty (pytest discovery)
- `tests/unit/test_seams.py` - 9 tests for ABC surfaces and module layout
- `tests/unit/test_paper_broker.py` - 16 tests for PaperBroker deterministic-fill contract

## Decisions Made

- **Broker ABC surface frozen at five members + is_paper.** Per D-09, no methods beyond {is_paper, place_order, get_order, get_positions, cancel} ship in Phase 0. Phase 8 owns the expansion (borrow check, ADV chunking, fills streaming). The lock test (`test_broker_abc_surface_locked`) treats any deviation as a planning conversation.
- **schemas.py minimal Order/Position.** Order has `order_id, ticker, side, qty, signal_price, status, fill_price, fill_ts`; Position has `ticker, qty (signed), avg_cost`. Phase 8 expands Order with broker_order_id, fills[], slippage_bps, etc.
- **PaperBroker cancel() raises ValueError on FILLED orders.** Since PaperBroker fills synchronously, no real PENDING state exists in normal flow; cancel is API-parity for Phase 8's IBKR async flow.
- **Sign-flip avg_cost reset.** A long-to-short flip across zero resets avg_cost to the new fill price (documented in `_apply_fill`); ordinary callers should close to flat then re-open, but the code handles flips defensively.
- **Created src/ls_equity_fund/\_\_init\_\_.py.** Plan_specifics asked us not to touch this file (Plan 00-02 owns it), but the package would not be importable without it and tests cannot run. Created with a minimal docstring + `__version__ = "0.1.0"`. Plan 00-02's worktree merge will rewrite/extend this file as needed.

## Deviations from Plan

### Auto-added Files (Rule 3 - missing test infrastructure)

**1. [Rule 3 - Missing infra] Created src/ls_equity_fund/__init__.py**
- **Found during:** Task 1 verification
- **Issue:** Without a top-level `__init__.py`, `import ls_equity_fund.*` fails in editable install (the package is not importable, hatchling needs it). Plan 02 owns this file but runs in a parallel worktree.
- **Fix:** Created a minimal `__init__.py` with docstring + `__version__ = "0.1.0"`. Plan 02's parallel worktree will conflict on this file at merge time; resolution is left to the merge ceremony (this is a worktree-coordination concern, not a runtime bug).
- **Files modified:** `src/ls_equity_fund/__init__.py` (created)
- **Verification:** `uv run python -c "from ls_equity_fund.schemas import Order"` succeeds.
- **Committed in:** 4fdc91c (Task 1)

**2. [Rule 3 - Missing infra] Created tests/__init__.py and tests/unit/__init__.py**
- **Found during:** Task 3 (test scaffolding)
- **Issue:** pytest discovers tests in non-package mode by default, but the project uses `src/` layout with `pythonpath = ["src"]` in pyproject and editable install for source. Empty `__init__.py` markers prevent ambiguity and match the explicit-package convention used elsewhere.
- **Fix:** Empty `tests/__init__.py` and `tests/unit/__init__.py`.
- **Verification:** `pytest tests/unit/` discovers and runs all 25 tests.
- **Committed in:** 135fd19 (Task 3)

**3. [Rule 3 - Missing dep step] Ran `uv sync --all-extras` and `uv pip install -e .`**
- **Found during:** Pre-Task-1 setup (no `.venv` existed)
- **Issue:** Worktree was clean of any installed environment; pytest unavailable.
- **Fix:** `uv sync --all-extras` to create the venv and install all deps (including dev extras), then `uv pip install -e .` to make the local package importable.
- **Verification:** `uv run pytest --version` shows 9.0.3; `python -c "import ls_equity_fund"` succeeds.
- **Committed in:** N/A (environment-only changes; .venv is gitignored).

**4. [Rule 2 - Missing critical functionality] Added test_seam_abcs_use_documented_module_paths**
- **Found during:** Task 3 (writing test_seams.py)
- **Issue:** Plan acceptance lists the ABCs at specific module paths (data/base.py, portfolio/base.py, execution/base.py) but provides no test that locks the path convention. Future refactoring could move ABCs without tripping any guard.
- **Fix:** Added a 9th test asserting `MarketDataProvider.__module__ == "ls_equity_fund.data.base"` etc.
- **Files modified:** `tests/unit/test_seams.py`
- **Verification:** Test passes.
- **Committed in:** 135fd19 (Task 3)

## Test Counts

- `tests/unit/test_seams.py`: **9 tests** (plan minimum: 8) - exceeds.
- `tests/unit/test_paper_broker.py`: **16 tests** (plan minimum: 11) - exceeds.
- **Total:** 25 tests, all passing in 0.49s.

## Files NOT Shipped (Deferred to Later Phases)

Per the plan's `<output>` section — explicit list so Phase 1+ planners know what to create:

| File | Owning Phase | Notes |
|---|---|---|
| `src/ls_equity_fund/data/providers/yfinance_provider.py` | Phase 1 | Concrete `MarketDataProvider`. Phase 1 may extend the ABC. |
| `src/ls_equity_fund/portfolio/conviction_tilt.py` | Phase 5 | Concrete `Optimizer` (always-works fallback). |
| `src/ls_equity_fund/portfolio/mvo.py` | Phase 7 | Concrete `Optimizer` (SLSQP). |
| `src/ls_equity_fund/execution/ibkr_broker.py` | Phase 8 | Concrete `Broker` against ib_async; expands the ABC; carries the MERIDIAN_LIVE_OK gate logic. |
| `src/ls_equity_fund/execution/registry.py` | Phase 8 | Selector that maps `config.broker.mode == 'paper'` to PaperBroker, `'live'` to IBKRBroker. |
| Schema expansions on `Order` (broker_order_id, fills[], slippage_bps, TIF, limit_price) | Phase 8 | Per D-09. |
| Persistence of orders/fills in SQLite | Phase 8 | Per D-08 (Phase 0 is in-memory only). |

## Locked Decisions Honored

| Decision | How honored |
|---|---|
| D-06 (fill at signal_price exactly) | `PaperBroker.place_order` sets `fill_price = order.signal_price`; `test_place_order_fills_at_signal_price` asserts equality. |
| D-07 (always full fill, never reject) | No fill-size or fill-rate logic in PaperBroker; even `qty=1_000_000` is accepted (test_place_order_full_fill_never_rejects). |
| D-08 (in-memory only) | `_orders` and `_positions` are plain dicts on `self`; no SQLite imports anywhere in execution/. test_two_instances_have_independent_state proves no global/shared state. |
| D-09 (Broker ABC surface = 5 + is_paper, locked) | Broker ABC has exactly 5 `@abstractmethod` members; `test_broker_abc_surface_locked` enforces the set equality. |
| D-10 (is_paper True) | Hard-coded property returning True; `test_is_paper_true` asserts. |
| D-22 (module layout) | All 8 layer packages exist at the documented paths; `test_all_layer_packages_importable` asserts. |

## Threat-Model Mitigations

| Threat ID | Mitigation Status |
|---|---|
| T-00-18 (is_paper accidentally True on live) | Mitigated: PaperBroker.is_paper hard-coded True; Phase 8's IBKRBroker will return False (not in this plan). test_is_paper_true guards Phase 0. |
| T-00-19 (ABC drift) | Mitigated: test_broker_abc_surface_locked locks the abstract method set; adding a method requires updating the test (forces a planning conversation). |
| T-00-20 (PaperBroker fills not logged) | Mitigated: every place_order emits structlog `paper_order_filled` event with order_id, ticker, side, qty, fill_price (verified by smoke test in Task 2 verification). |
| T-00-21 (in-memory state) | Accepted per D-08; documented in PaperBroker docstring. |

## Self-Check: PASSED

- Created files exist on disk (14 src + 4 test files verified via `find`).
- Commits exist in `git log` (4fdc91c, 8310ade, 135fd19).
- All 25 tests pass.
- D-09 surface lock test passes.
- D-22 layer-package import test passes.
- INFRA-03 fully delivered (module layout + 3 seam ABCs + 1 concrete).
