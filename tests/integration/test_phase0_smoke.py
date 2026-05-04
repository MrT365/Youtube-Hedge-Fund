"""Phase 0 acceptance harness — verifies all 4 ROADMAP success criteria.

Each ``test_sc<N>_*`` function corresponds to ROADMAP.md Phase 0 Success
Criterion N. This file is the gate that closes Phase 0; downstream phases
run it via ``/gsd-verify-phase`` to confirm Phase 0 has not regressed.

SC mapping (ROADMAP.md Phase 0):
  SC1 — ``uv sync`` builds the project against pinned versions.
        Verified here by importing each pinned dep and asserting version
        constraints from CLAUDE.md (pandas>=2.2,<3.0; numpy>=2.0,<2.5;
        ib_async==2.1.x; edgartools>=5.30,<6; anthropic>=0.97; scipy>=1.16,<1.18;
        statsmodels>=0.14.6; structlog>=25.5; pydantic>=2.13; pytest==9.0.3).
        Also asserts pyproject.toml carries the pins and that no foreign
        package-manager artifact (Pipfile, poetry.lock, requirements.txt,
        setup.py) lives at repo root (uv-only project).
  SC2 — ``meridian doctor`` end-to-end: loads config + opens DB in WAL +
        runs ``alembic upgrade head`` + writes log + exits 0. Idempotent.
  SC3 — Three swap-in seam ABCs (``MarketDataProvider``, ``Optimizer``,
        ``Broker``) are importable and abstract; ``PaperBroker`` is a
        concrete subclass that fills deterministically at signal_price (D-06).
        D-09 surface lock asserted for ``Broker`` (defense-in-depth duplicate
        of unit-level surface guard).
  SC4 — ``.gitignore`` excludes the right paths AND keeps ``.planning/``
        tracked; structlog redacts API keys from the JSON file sink.

This file is read-only on production code: no fixtures patch internals;
no mocks of the modules under test. Per AUDIT-02, the redaction test reads
the actual JSONL file from disk and asserts (a) raw key absent, (b) the
``REDACTED_PLACEHOLDER`` literal present, (c) the ``api_key`` field on the
parsed JSON object equals the placeholder.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging as _stdlib_logging
import sqlite3
import sys
from pathlib import Path

import pytest
import structlog
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
runner = CliRunner()


# =====================================================================
# Shared autouse fixture — keeps structlog state isolated between tests
# =====================================================================


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    """Reset structlog + stdlib root handlers between tests.

    Several SC2/SC4 tests call ``configure_logging`` (directly or via the
    doctor command). Without this reset, a second invocation in the same
    pytest session would short-circuit on the ``_CONFIGURED`` guard and
    write to the previous test's tmp_path log file (which has been removed
    on tmp_path teardown).
    """
    import ls_equity_fund.logging as _log_mod

    _log_mod._CONFIGURED = False
    root = _stdlib_logging.getLogger()
    for h in list(root.handlers):
        with contextlib.suppress(Exception):
            h.close()
        root.removeHandler(h)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    _log_mod._CONFIGURED = False
    for h in list(root.handlers):
        with contextlib.suppress(Exception):
            h.close()
        root.removeHandler(h)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


# =====================================================================
# SC1: Project builds against pinned versions (CLAUDE.md tech-stack pins)
# =====================================================================


def _parse_version_pair(version: str) -> tuple[int, int]:
    """Return ``(major, minor)`` of a SemVer-ish string. Tolerates 'X.Y.ZrcN'."""
    parts = version.split(".")
    major = int(parts[0])
    # Strip any non-digit suffix on minor (e.g. '14rc1' -> '14').
    minor_raw = parts[1] if len(parts) > 1 else "0"
    minor_digits = ""
    for ch in minor_raw:
        if ch.isdigit():
            minor_digits += ch
        else:
            break
    minor = int(minor_digits) if minor_digits else 0
    return major, minor


def test_sc1_python_version() -> None:
    """SC1: Python 3.11+ floor (CLAUDE.md tech stack)."""
    assert sys.version_info[:2] >= (3, 11), f"Python {sys.version_info} < 3.11"


def test_sc1_pandas_version_pin() -> None:
    """SC1: pandas >=2.2,<3.0 (CLAUDE.md — pandas 3.0 explicitly forbidden in v1)."""
    import pandas

    major, minor = _parse_version_pair(pandas.__version__)
    assert major == 2, f"pandas {pandas.__version__}: major={major}, expected 2"
    assert minor >= 2, f"pandas {pandas.__version__}: minor={minor}, expected >=2"


def test_sc1_numpy_version_pin() -> None:
    """SC1: numpy >=2.0,<2.5 (CLAUDE.md — numpy 2 ABI break is past us)."""
    import numpy

    major, minor = _parse_version_pair(numpy.__version__)
    assert major == 2, f"numpy {numpy.__version__}: major={major}, expected 2"
    assert minor < 5, f"numpy {numpy.__version__}: minor={minor}, expected <5"


def test_sc1_scipy_version_pin() -> None:
    """SC1: scipy >=1.16,<1.18 (pyproject.toml pin)."""
    import scipy

    major, minor = _parse_version_pair(scipy.__version__)
    assert major == 1, f"scipy {scipy.__version__}: major={major}, expected 1"
    assert 16 <= minor < 18, f"scipy {scipy.__version__}: minor={minor}, expected 16<=x<18"


def test_sc1_ib_async_imports_and_no_ib_insync() -> None:
    """SC1: ``ib_async`` imports (the live successor to deceased ``ib_insync``).

    CLAUDE.md anti-recommendation: ``ib_insync`` MUST NOT appear in the deps.
    A live ``ib_insync`` installed in the venv would mean someone added it as
    a dep against the locked technology stack — fail loudly.
    """
    import ib_async  # noqa: F401  (import is the assertion)

    # ib_insync must NOT be installed alongside; CLAUDE.md forbids it.
    with pytest.raises(ImportError):
        import ib_insync  # type: ignore[import-not-found]  # noqa: F401


def test_sc1_edgartools_imports() -> None:
    """SC1: edgartools >=5.30,<6.

    edgartools is distributed as ``edgartools`` on PyPI but the importable
    package name is ``edgar``. The importable name is the contract.
    """
    import edgar  # noqa: F401  (import is the contract)


def test_sc1_anthropic_imports() -> None:
    """SC1: anthropic >=0.97 (CLAUDE.md — cache_control typing stable since 0.42)."""
    import anthropic

    version = getattr(anthropic, "__version__", None)
    if version:
        major, minor = _parse_version_pair(version)
        # anthropic uses 0.x.y SemVer.
        assert (major, minor) >= (0, 97), f"anthropic {version} < 0.97"


def test_sc1_pinned_deps_all_import() -> None:
    """SC1 sanity: every pinned runtime dep imports without error.

    Mirrors the dependency block of pyproject.toml. If ``uv sync`` failed,
    one of these imports would raise. Order is alphabetical for clarity.
    """
    import alembic  # noqa: F401
    import anthropic  # noqa: F401
    import curl_cffi  # noqa: F401
    import edgar  # noqa: F401
    import ib_async  # noqa: F401
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pydantic  # noqa: F401
    import pydantic_settings  # noqa: F401
    import scipy  # noqa: F401
    import statsmodels  # noqa: F401
    import streamlit  # noqa: F401
    import structlog  # noqa: F401
    import tenacity  # noqa: F401
    import typer  # noqa: F401
    import yaml  # noqa: F401  (PyYAML — pinned 6.0.3)
    import yfinance  # noqa: F401


def test_sc1_pyproject_carries_required_pins() -> None:
    """SC1: pyproject.toml lists the pinned constraints from CLAUDE.md."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # CLAUDE.md hard constraints — substring match (order-insensitive).
    required_substrings = [
        'pandas>=2.2,<3.0',
        'numpy>=2.0,<2.5',
        'ib-async>=2.1',           # PyPI distribution name uses dash
        'edgartools>=5.30,<6',
        'anthropic>=0.97',
        'scipy>=1.16,<1.18',
        'structlog>=25.5',
        'pydantic>=2.13',
        'pydantic-settings>=2.6',
        'pytest==9.0.3',
    ]
    for needle in required_substrings:
        assert needle in pyproject, f"pyproject.toml missing pin: {needle!r}"

    # Anti-recommendation: ib_insync must not appear. ``requires-python`` floors at 3.11.
    assert 'ib_insync' not in pyproject, "ib_insync forbidden by CLAUDE.md (deceased lib)"
    assert 'requires-python = ">=3.11' in pyproject


def test_sc1_uv_lock_committed_no_foreign_pm_artifacts() -> None:
    """SC1: uv.lock is committed; no Pipfile/poetry.lock/requirements.txt/setup.py.

    CLAUDE.md mandates uv as the sole package manager. Foreign artifacts
    indicate a divergent build path that breaks the locked-version contract.
    """
    assert (REPO_ROOT / "uv.lock").exists(), "uv.lock missing — uv sync was never run"
    forbidden = ["Pipfile", "Pipfile.lock", "poetry.lock", "requirements.txt", "setup.py"]
    for name in forbidden:
        assert not (REPO_ROOT / name).exists(), (
            f"{name} present at repo root — uv-only project (CLAUDE.md)"
        )


# =====================================================================
# SC2: ``meridian doctor`` exits 0 + config + WAL + migration
# =====================================================================


@pytest.fixture
def doctor_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up an isolated tmp project root for the doctor command.

    Mirrors ``tests/unit/test_cli_doctor.py::fresh_workspace`` but lives in
    the integration suite. cache_dir AND log_dir are repointed under
    tmp_path so the test never touches repo-root ``cache/`` or ``logs/``.
    """
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text(encoding="utf-8")
    yaml_text = yaml_text.replace("cache_dir: cache", f"cache_dir: {tmp_path / 'cache'}")
    yaml_text = yaml_text.replace("log_dir: logs", f"log_dir: {tmp_path / 'logs'}")
    (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")

    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-phase0-smoke\n"
        "SEC_USER_AGENT=Meridian Capital Partners smoke@example.com\n",
        encoding="utf-8",
    )

    alembic_text = (REPO_ROOT / "alembic.ini").read_text(encoding="utf-8")
    alembic_text = alembic_text.replace(
        "script_location = migrations",
        f"script_location = {REPO_ROOT / 'migrations'}",
    )
    (tmp_path / "alembic.ini").write_text(alembic_text, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_sc2_doctor_exits_zero_on_fresh_workspace(doctor_workspace: Path) -> None:
    """SC2: ``meridian doctor`` exits 0 against a healthy fresh workspace."""
    from ls_equity_fund.cli.app import app

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert "doctor passed" in result.stdout


def test_sc2_doctor_creates_db_in_wal_mode(doctor_workspace: Path) -> None:
    """SC2: post-doctor, the SQLite DB at the configured path is in WAL mode."""
    from ls_equity_fund.cli.app import app

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    db_path = doctor_workspace / "cache" / "ls_equity_fund.db"
    assert db_path.exists(), f"DB not created at {db_path}"
    conn = sqlite3.connect(str(db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal", f"journal_mode={mode!r}, expected 'wal'"


def test_sc2_doctor_runs_alembic_upgrade_to_head(doctor_workspace: Path) -> None:
    """SC2: post-doctor, the Phase 0 baseline tables exist and ``alembic_version``
    holds the current head.

    The literal head revision advances as later phases ship their own migrations
    (Phase 1 ships 0002, Phase 2 will ship 0003, ...). The SC2 contract is
    "alembic upgrade head succeeds and the Phase 0 baseline survives" — not
    that head is permanently pinned at 0001.
    """
    from ls_equity_fund.cli.app import app

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    db_path = doctor_workspace / "cache" / "ls_equity_fund.db"
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        versions = list(conn.execute("SELECT version_num FROM alembic_version"))
    finally:
        conn.close()

    assert {"runs", "heartbeat", "alembic_version"}.issubset(tables), (
        f"missing post-migration tables; got {sorted(tables)}"
    )
    assert len(versions) == 1, f"alembic_version row count={len(versions)}, expected 1"
    # Head must be a 4-digit revision id and >= 0001 (post-Phase-0).
    head = versions[0][0]
    assert isinstance(head, str) and head.isdigit() and len(head) == 4, (
        f"alembic head={head!r}, expected a 4-digit revision string"
    )
    assert head >= "0001", f"alembic head={head!r}, expected >= '0001'"


def test_sc2_doctor_writes_doctor_passed_log_line(doctor_workspace: Path) -> None:
    """SC2: doctor emits structured ``doctor_passed`` event to the JSONL audit log."""
    from ls_equity_fund.cli.app import app

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    log_file = doctor_workspace / "logs" / f"{today}.jsonl"
    assert log_file.exists(), f"audit log not at {log_file}"

    text = log_file.read_text(encoding="utf-8")
    found_passed = False
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "doctor_passed":
            found_passed = True
            break
    assert found_passed, f"doctor_passed event not in log file:\n{text}"


def test_sc2_doctor_idempotent(doctor_workspace: Path) -> None:
    """SC2 + D-25: re-running doctor on a healthy system exits 0 with no schema change."""
    from ls_equity_fund.cli.app import app

    r1 = runner.invoke(app, ["doctor"])
    assert r1.exit_code == 0, f"first doctor failed: {r1.stderr!r}"

    # Second invocation; structlog state is reset by the autouse fixture
    # only at test boundaries, so reset manually here.
    import ls_equity_fund.logging as _log_mod

    _log_mod._CONFIGURED = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    for h in list(_stdlib_logging.getLogger().handlers):
        with contextlib.suppress(Exception):
            h.close()
        _stdlib_logging.getLogger().removeHandler(h)

    r2 = runner.invoke(app, ["doctor"])
    assert r2.exit_code == 0, f"second doctor failed: {r2.stderr!r}"

    # Exactly ONE alembic_version row after two runs (idempotent migration).
    db_path = doctor_workspace / "cache" / "ls_equity_fund.db"
    conn = sqlite3.connect(str(db_path))
    try:
        versions = list(conn.execute("SELECT version_num FROM alembic_version"))
    finally:
        conn.close()
    assert len(versions) == 1, f"alembic_version rows after 2 doctor runs: {len(versions)}"


def test_sc2_doctor_refuses_when_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC2 / D-25: doctor exits 3 with operator-facing 'does NOT initialize' guidance.

    Doctor verifies; it does not create ``.env`` for the operator.
    """
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text(encoding="utf-8")
    (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
    # Intentionally omit .env.
    monkeypatch.chdir(tmp_path)

    from ls_equity_fund.cli.app import app

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3, f"got exit={result.exit_code}; stderr={result.stderr!r}"
    assert ".env not found" in result.stderr
    assert "does NOT initialize" in result.stderr


# =====================================================================
# SC3: 3 ABCs importable + PaperBroker deterministic-fill contract
# =====================================================================


def test_sc3_market_data_provider_abc_importable_and_abstract() -> None:
    """SC3: MarketDataProvider seam exists at its locked module path and is abstract."""
    from ls_equity_fund.data.base import MarketDataProvider

    assert MarketDataProvider.__module__ == "ls_equity_fund.data.base"
    with pytest.raises(TypeError):
        MarketDataProvider()  # type: ignore[abstract]


def test_sc3_optimizer_abc_importable_and_abstract() -> None:
    """SC3: Optimizer seam exists at its locked module path and is abstract."""
    from ls_equity_fund.portfolio.base import Optimizer

    assert Optimizer.__module__ == "ls_equity_fund.portfolio.base"
    with pytest.raises(TypeError):
        Optimizer()  # type: ignore[abstract]


def test_sc3_broker_abc_importable_and_abstract() -> None:
    """SC3: Broker seam exists at its locked module path and is abstract."""
    from ls_equity_fund.execution.base import Broker

    assert Broker.__module__ == "ls_equity_fund.execution.base"
    with pytest.raises(TypeError):
        Broker()  # type: ignore[abstract]


def test_sc3_broker_abc_surface_is_d09_locked() -> None:
    """SC3 + D-09: Broker's abstract surface is exactly the 5-member Phase 0 set.

    Defense-in-depth duplicate of ``test_seams.test_broker_abc_surface_locked``.
    Phase 8 will EXPAND this set; until then, drift is a planning bug.
    """
    from ls_equity_fund.execution.base import Broker

    assert set(Broker.__abstractmethods__) == {
        "is_paper",
        "place_order",
        "get_order",
        "get_positions",
        "cancel",
    }


def test_sc3_paper_broker_is_concrete_and_paper_flagged() -> None:
    """SC3 + D-10: PaperBroker is a concrete Broker subclass and ``is_paper`` is True.

    The Phase 8 ``MERIDIAN_LIVE_OK`` gate keys off this flag.
    """
    from ls_equity_fund.execution.base import Broker
    from ls_equity_fund.execution.paper_broker import PaperBroker

    assert issubclass(PaperBroker, Broker)
    pb = PaperBroker()
    assert pb.is_paper is True


def test_sc3_paper_broker_deterministic_fill_at_signal_price() -> None:
    """SC3 + D-06/07: place_order BUY 10 @ 100 fills at exactly 100 (zero slippage)."""
    from ls_equity_fund.execution.paper_broker import PaperBroker
    from ls_equity_fund.schemas import Order, OrderId, OrderStatus, Side

    pb = PaperBroker()
    order = Order(
        order_id=OrderId("smoke-sc3-001"),
        ticker="AAPL",
        side=Side.BUY,
        qty=10,
        signal_price=100.0,
    )
    oid = pb.place_order(order)
    filled = pb.get_order(oid)
    assert filled.status == OrderStatus.FILLED
    assert filled.fill_price == 100.0  # D-06: zero slippage paper fill

    positions = pb.get_positions()
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"
    assert positions[0].qty == 10
    assert positions[0].avg_cost == 100.0


# =====================================================================
# SC4: ``.gitignore`` + structlog redaction
# =====================================================================


def test_sc4_gitignore_excludes_secrets_and_caches() -> None:
    """SC4: .gitignore excludes .env, cache/, output/, logs/, config.yaml.

    Each pattern must appear as a standalone, anchored line (not just a
    substring that could match a prefix like ``.env-something``).
    """
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {ln.strip() for ln in text.splitlines()}
    required = [".env", "cache/", "output/", "logs/", "config.yaml"]
    for pat in required:
        assert pat in lines, f".gitignore missing required line: {pat!r}"


def test_sc4_gitignore_does_not_exclude_planning() -> None:
    """SC4: ``.planning/`` MUST remain tracked (not in .gitignore).

    ``.planning/`` carries the GSD audit trail — STATE.md, ROADMAP.md, plan
    histories. If it ever lands in .gitignore that audit chain is broken.
    """
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for raw in text.splitlines():
        stripped = raw.strip()
        # Reject any negated-or-not pattern that would match .planning or .planning/.
        if stripped.startswith(".planning"):
            pytest.fail(f".planning entry in .gitignore violates INFRA-06: {stripped!r}")


def test_sc4_structlog_redacts_api_key_in_jsonl_sink(tmp_path: Path) -> None:
    """SC4 + D-18: structlog JSON file sink redacts ``api_key`` values.

    Three independent assertions reduce false-pass risk (T-00-26):
      1. The literal raw key string MUST NOT appear anywhere in the file.
      2. ``REDACTED_PLACEHOLDER`` MUST appear in the file.
      3. The parsed JSON object's ``api_key`` field MUST equal the placeholder.
    """
    from ls_equity_fund.config import LoggingConfig
    from ls_equity_fund.logging import REDACTED_PLACEHOLDER, configure_logging

    cfg = LoggingConfig(
        level="INFO",
        log_dir=str(tmp_path),
        json_renderer_when_non_tty=True,
        redact_keys=[],
    )
    configure_logging(cfg)

    log = structlog.get_logger("sc4_redaction")
    secret = "sk-ant-DO-NOT-LEAK-AAAA-BBBB-CCCC"
    log.info("api_call_sample", api_key=secret, user="alice")

    for h in _stdlib_logging.getLogger().handlers:
        h.flush()

    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    log_file = tmp_path / f"{today}.jsonl"
    assert log_file.exists(), f"JSONL audit log not created at {log_file}"

    text = log_file.read_text(encoding="utf-8")

    # Assertion 1: raw secret NEVER on disk.
    assert secret not in text, "API key leaked verbatim into JSONL audit log"
    # Assertion 2: placeholder present (proves redaction ran, not just dropped key).
    assert REDACTED_PLACEHOLDER in text, "redaction placeholder absent from log file"

    # Assertion 3: parse each line; the ``api_call_sample`` record's ``api_key``
    # field must equal the placeholder, and ``user`` must pass through unchanged.
    found_event = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "api_call_sample":
            found_event = True
            assert obj.get("api_key") == REDACTED_PLACEHOLDER, (
                f"api_key field not redacted: {obj!r}"
            )
            assert obj.get("user") == "alice"
    assert found_event, "api_call_sample event missing from JSONL output"
