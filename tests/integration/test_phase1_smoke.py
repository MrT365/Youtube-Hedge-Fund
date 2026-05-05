"""Phase 1 closure-gate integration tests — all 5 ROADMAP SCs as automated tests.

Mirrors the Phase 0 ``test_phase0_smoke.py`` structure (one class per SC; one
class per closure invariant). Uses MagicMock providers + per-step orchestrator
patches throughout — does NOT hit yfinance / EDGAR / fed.gov / Anthropic.
The orchestrator + 11-step adapters + persistence layer + CLI run end-to-end
against canned data; this is INTEGRATION at the Python-module level, not a
network smoke test.

Passing this file is the closure condition for Phase 1. A failing assertion
here means a downstream phase MUST NOT advance until Phase 1 is restored.

SC-to-class mapping (ROADMAP.md Phase 1):
  SC1 → ``TestPhase1SC1UniversePIT`` — three modes populate
        ``universe.{first_seen_date, delisted_date, inclusion_window}``;
        delisted FLAGGED never deleted (binds CP1).
  SC2 → ``TestPhase1SC2FullPipelineSmoke`` — orchestrator runs all 11 steps,
        manifest carries every step key, runs row closes status='OK',
        macro fall-back path returns ``fell_back=True`` without aborting.
  SC3 → ``TestPhase1SC3Form4Codes`` — every Form 4 transaction code
        (P/S/A/M/F/G/D) round-trips through parse → INSERT → SELECT;
        CHECK constraint rejects unknown codes; cluster-buy detector
        counts ONLY ``transaction_code='P'`` (binds CP3).
  SC4 → ``TestPhase1SC4SelectiveFlags`` — ``--no-filings``, ``--no-13f``,
        ``--forms`` flag matrix produces correct exit codes and orchestrator
        kwargs.
  SC5 → ``TestPhase1SC5DataProviderSeam`` — ``PolygonProvider`` instantiates,
        is selectable via config, orchestrator runtime guard rejects with
        DATA-14 message, six sibling ABCs declared, Phase 0 ``MarketDataProvider``
        facade still importable.

Phase 0 invariant probe lives at the bottom (``TestPhase1Closure``) — its
sole job is to detect Phase 0 regressions caused by Phase 1 work.

This file is read-only on production code: no patches against the modules
under test EXCEPT at orchestrator step boundaries (the documented test
seam — see ``data/orchestrator.py`` step adapters).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from typer.testing import CliRunner

from ls_equity_fund.cli.app import app
from ls_equity_fund.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
EXAMPLE_YAML = REPO_ROOT / "config.yaml.example"

runner = CliRunner()


# =====================================================================
# Shared fixtures
# =====================================================================


def _alembic_cfg(db: Path) -> AlembicConfig:
    """Build an Alembic config bound to a tmp DB with absolute paths.

    Mirrors the pattern used in ``tests/unit/test_migrations.py`` and
    ``tests/unit/data/test_prices_ingest.py`` — sets ``script_location``
    AND ``sqlalchemy.url`` explicitly so env.py's ``_resolve_db_url``
    fallback does NOT clobber the test path.
    """
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    """Fresh SQLite at tmp_path/ls_equity_fund.db, migrations applied to head."""
    db = tmp_path / "ls_equity_fund.db"
    alembic_command.upgrade(_alembic_cfg(db), "head")
    return db


@pytest.fixture
def conn(migrated_db: Path) -> Iterator[sqlite3.Connection]:
    """sqlite3 connection to the migrated DB; closed on teardown."""
    c = sqlite3.connect(str(migrated_db), isolation_level=None)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture
def workspace(tmp_path: Path, migrated_db: Path) -> dict[str, Path]:
    """config.yaml + .env in tmp_path; cache_dir + log_dir under tmp_path.

    Mirrors ``tests/unit/cli/test_data_cmd.py::_setup_workspace``. The
    migrated DB lives at ``cache_dir/ls_equity_fund.db`` so anything that
    calls ``get_db_path(config)`` resolves to our migrated tmp DB.
    """
    yaml_text = EXAMPLE_YAML.read_text(encoding="utf-8")
    yaml_text = yaml_text.replace("cache_dir: cache", f"cache_dir: {tmp_path}")
    yaml_text = yaml_text.replace("log_dir: logs", f"log_dir: {tmp_path / 'logs'}")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-phase1-smoke\n"
        "IBKR_USERNAME=test\n"
        "IBKR_PASSWORD=test\n"
        "SEC_USER_AGENT=Meridian Capital Partners smoke@example.com\n",
        encoding="utf-8",
    )
    return {"config": yaml_path, "env": env_path, "tmp": tmp_path}


# =====================================================================
# SC1 — Universe PIT integrity (CP1 binding)
# =====================================================================


class TestPhase1SC1UniversePIT:
    """SC1 — three modes populate first_seen_date / delisted_date /
    inclusion_window; delisted tickers are FLAGGED, never deleted (CP1)."""

    def test_sp500_mode_populates_pit_columns(
        self, conn: sqlite3.Connection, workspace: dict[str, Path]
    ) -> None:
        """sp500 mode + Wikipedia fixture → 5 rows with PIT columns set."""
        from ls_equity_fund.data.universe import build_universe

        config_obj, _ = load_config(yaml_path=workspace["config"], env_path=workspace["env"])
        n = build_universe(
            config_obj,
            mode="sp500",
            conn=conn,
            today=date(2026, 4, 1),
            fixture_html_path=FIXTURES / "sp500_wikipedia_fixture.html",
        )
        assert n == 5
        rows = conn.execute(
            "SELECT ticker, first_seen_date, delisted_date, inclusion_window "
            "FROM universe ORDER BY ticker"
        ).fetchall()
        assert {r["ticker"] for r in rows} == {"AAPL", "JNJ", "JPM", "MSFT", "NVDA"}
        for r in rows:
            assert r["first_seen_date"] == "2026-04-01"
            assert r["delisted_date"] is None
            assert r["inclusion_window"] == "2026-04-01:current"

    def test_scanner_seed_mode_populates_pit_columns(
        self, conn: sqlite3.Connection, workspace: dict[str, Path]
    ) -> None:
        """scanner_seed mode → rows with PIT columns set; yfinance.info mocked."""
        from ls_equity_fund.data.universe import build_universe

        config_obj, _ = load_config(yaml_path=workspace["config"], env_path=workspace["env"])
        # Restrict to 3 tickers and stub yfinance enrichment so no network call.
        config_obj.data.scanner_seed_tickers = ["AAPL", "MSFT", "NVDA"]

        with patch("yfinance.Ticker") as yt_mock:
            yt_mock.return_value.info = {
                "longName": "Test Co",
                "exchange": "NASDAQ",
                "sector": "Information Technology",
                "industry": "Hardware",
                "marketCap": 1_000_000_000_000,
            }
            n = build_universe(
                config_obj,
                mode="scanner_seed",
                conn=conn,
                today=date(2026, 4, 1),
            )
        assert n == 3
        rows = conn.execute(
            "SELECT ticker, first_seen_date, delisted_date, inclusion_window FROM universe"
        ).fetchall()
        for r in rows:
            assert r["first_seen_date"] == "2026-04-01"
            assert r["delisted_date"] is None
            assert r["inclusion_window"] == "2026-04-01:current"

    def test_liquid_us_mode_falls_back_to_seed_when_prices_empty(
        self, conn: sqlite3.Connection, workspace: dict[str, Path]
    ) -> None:
        """liquid_us mode with empty daily_prices → falls back to scanner_seed
        (documented behavior in ``_build_liquid_us``).

        Validates that all three modes produce universe rows on a fresh DB,
        which is the SC1 ROADMAP wording ("Three universe modes populate ...").
        """
        from ls_equity_fund.data.universe import build_universe

        config_obj, _ = load_config(yaml_path=workspace["config"], env_path=workspace["env"])
        config_obj.data.scanner_seed_tickers = ["AAPL", "MSFT"]

        with patch("yfinance.Ticker") as yt_mock:
            yt_mock.return_value.info = {
                "longName": "Test Co",
                "exchange": "NASDAQ",
                "sector": "Tech",
                "industry": "X",
                "marketCap": 1_000_000_000_000,
            }
            n = build_universe(
                config_obj,
                mode="liquid_us",
                conn=conn,
                today=date(2026, 4, 1),
            )
        assert n == 2  # fell back to scanner_seed (2 tickers configured)

    def test_delisted_ticker_flagged_not_deleted_CP1_binding(
        self, conn: sqlite3.Connection
    ) -> None:
        """CP1 / SC1 contract — delisted ticker stays in DB with delisted_date set.

        Day 1: 3 tickers ingested. Day 60: ENRN gone from incoming list →
        UPDATE delisted_date=today, inclusion_window=window:today.
        Total row count preserved (3, NOT 2).
        """
        from ls_equity_fund.data.universe import merge_universe_pit

        merge_universe_pit(
            [{"ticker": t, "sector": "X"} for t in ["AAPL", "MSFT", "ENRN"]],
            conn,
            today=date(2026, 1, 1),
        )
        stats = merge_universe_pit(
            [{"ticker": t, "sector": "X"} for t in ["AAPL", "MSFT"]],
            conn,
            today=date(2026, 3, 1),
        )
        assert stats["delisted"] == 1

        enrn = conn.execute(
            "SELECT first_seen_date, delisted_date, inclusion_window "
            "FROM universe WHERE ticker='ENRN'"
        ).fetchone()
        assert enrn is not None, "delisted ticker MUST NOT be deleted (CP1 / SC1 binding)"
        assert enrn["first_seen_date"] == "2026-01-01"
        assert enrn["delisted_date"] == "2026-03-01"
        assert enrn["inclusion_window"] == "2026-01-01:2026-03-01"

        total = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
        assert total == 3, "row count should be preserved post-delist (3, not 2)"

    def test_pit_query_at_date_excludes_post_delisting(self, conn: sqlite3.Connection) -> None:
        """The PIT query convention works — universe at 2026-12-01 is empty
        when X was delisted on 2026-06-01."""
        from ls_equity_fund.data.universe import merge_universe_pit

        merge_universe_pit([{"ticker": "X", "sector": "Y"}], conn, today=date(2026, 1, 1))
        merge_universe_pit([], conn, today=date(2026, 6, 1))  # delist all
        rows = conn.execute(
            "SELECT ticker FROM universe "
            "WHERE first_seen_date <= ? "
            "AND (delisted_date IS NULL OR delisted_date > ?)",
            ("2026-12-01", "2026-12-01"),
        ).fetchall()
        assert rows == []


# =====================================================================
# SC2 — Full pipeline smoke (orchestrator + persistence)
# =====================================================================


class TestPhase1SC2FullPipelineSmoke:
    """SC2 — daily refresh runs all 11 steps end-to-end (mocked providers)."""

    def test_orchestrator_chains_all_eleven_steps(self, workspace: dict[str, Path]) -> None:
        """Orchestrator manifest carries every step key and a runs row closes
        with ``status='OK'``. All step adapters are patched; the orchestrator
        is exercised with real persistence + run-row bookkeeping.
        """
        from ls_equity_fund.data.orchestrator import run_data_pipeline

        config_obj, secrets = load_config(yaml_path=workspace["config"], env_path=workspace["env"])
        config_obj.data.universe_mode = "scanner_seed"
        config_obj.data.scanner_seed_tickers = ["AAPL"]

        # Patch every step adapter — orchestrator-level integration only.
        # The adapters' real bodies are exercised by the per-plan unit tests.
        patches = [
            patch(
                "ls_equity_fund.data.orchestrator._build_universe_step",
                return_value=1,
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_benchmarks_step",
                return_value={"benchmark": 4},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_prices_step",
                return_value={"ok": 1, "rows_written": 750},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_fundamentals_step",
                return_value={"ok": 1, "rows_written": 5},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._compute_ratios_step",
                return_value=1,
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_filings_step",
                return_value={"ok": 1, "filings_inserted": 3, "insider_inserted": 0},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_13f_step",
                return_value={"ok": 9},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_short_interest_step",
                return_value={"ok": 1},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_estimates_step",
                return_value={"ok": 1},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_earnings_step",
                return_value={"ok": 1},
            ),
            patch(
                "ls_equity_fund.data.orchestrator._refresh_macro_step",
                return_value={"events_written": 8, "fell_back": False},
            ),
        ]
        for p in patches:
            p.start()
        try:
            db = workspace["tmp"] / "ls_equity_fund.db"
            c = sqlite3.connect(str(db), isolation_level=None)
            c.row_factory = sqlite3.Row
            try:
                manifest = run_data_pipeline(
                    config_obj,
                    secrets,
                    conn=c,
                    today=date(2026, 4, 1),
                )
            finally:
                c.close()
        finally:
            for p in patches:
                p.stop()

        # Manifest carries every required step key.
        for key in (
            "universe",
            "benchmarks",
            "prices",
            "fundamentals",
            "ratios",
            "filings",
            "institutional",
            "short_interest",
            "estimates",
            "earnings_calendar",
            "macro",
        ):
            assert key in manifest, f"step {key!r} missing from manifest"
        assert "run_id" in manifest
        assert "duration_seconds" in manifest

        # Runs row recorded with status='OK'.
        c = sqlite3.connect(str(workspace["tmp"] / "ls_equity_fund.db"))
        c.row_factory = sqlite3.Row
        try:
            run_row = c.execute(
                "SELECT status, end_ts, error FROM runs WHERE run_id=?",
                (manifest["run_id"],),
            ).fetchone()
        finally:
            c.close()
        assert run_row is not None, "runs row missing"
        assert run_row["status"] == "OK"
        assert run_row["end_ts"] is not None
        assert run_row["error"] is None

    def test_macro_calendar_falls_back_without_aborting(
        self, workspace: dict[str, Path], conn: sqlite3.Connection
    ) -> None:
        """SC2 includes 'live FOMC calendar with cached fallback'.

        Pre-seed a stale row, force a NetworkError on the provider, assert
        ``fell_back=True`` and the daily run does NOT raise.
        """
        from ls_equity_fund.data.macro_calendar import refresh_macro_calendar
        from ls_equity_fund.data.providers.fred_provider import NetworkError

        config_obj, _ = load_config(yaml_path=workspace["config"], env_path=workspace["env"])
        # Seed a row 30 days stale so the 7-day refresh-skip gate does NOT fire
        # AND the staleness threshold (>=7 days) is exceeded — proves both
        # branches of refresh_macro_calendar.
        stale_ts = int(time.time()) - 30 * 86400
        conn.execute(
            "INSERT INTO macro_calendar "
            "(event_id, event_type, event_date_et, source, fetched_at, last_refreshed) "
            "VALUES (?, 'FOMC', '2026-06-17', 'fed', ?, ?)",
            ("seed1", stale_ts, stale_ts),
        )
        provider = MagicMock()
        provider.fetch_macro_events.side_effect = NetworkError("fed.gov down")

        result = refresh_macro_calendar(
            config_obj,
            conn=conn,
            today=date.today(),
            provider=provider,
        )
        assert result["fell_back"] is True
        assert result["events_written"] == 0
        assert result["staleness_days"] is not None and result["staleness_days"] >= 7

    def test_orchestrator_provider_guard_rejects_polygon_runtime(
        self, workspace: dict[str, Path]
    ) -> None:
        """SC2 + DATA-14 — orchestrator refuses to run if config.data.provider
        is anything other than 'yfinance' (raises SystemExit with DATA-14 msg)."""
        from ls_equity_fund.data.orchestrator import run_data_pipeline

        config_obj, secrets = load_config(yaml_path=workspace["config"], env_path=workspace["env"])
        config_obj.data.provider = "polygon"
        with pytest.raises(SystemExit) as excinfo:
            run_data_pipeline(config_obj, secrets, today=date(2026, 4, 1))
        # SystemExit message contains DATA-14 reference per orchestrator.py
        assert "DATA-14" in str(excinfo.value)


# =====================================================================
# SC3 — Form 4 transaction codes (CP3 binding)
# =====================================================================


class TestPhase1SC3Form4Codes:
    """SC3 / CP3 — every Form 4 transaction code (P/S/A/M/F/G/D) round-trips
    with the correct ``transaction_code`` column populated."""

    @pytest.mark.parametrize(
        "code,filename",
        [
            ("P", "form4_p_purchase.xml"),
            ("S", "form4_s_sale.xml"),
            ("A", "form4_a_grant.xml"),
            ("M", "form4_m_exercise.xml"),
            ("F", "form4_f_withhold.xml"),
            ("G", "form4_g_gift.xml"),
            ("D", "form4_d_disposition.xml"),
        ],
    )
    def test_form4_code_roundtrip_through_persistence(
        self, code: str, filename: str, conn: sqlite3.Connection
    ) -> None:
        """Parse fixture → INSERT filings_metadata + insider_transactions →
        SELECT WHERE accession=fixture → assert ``transaction_code == code``.

        Binds CP3: every code persists with the literal letter the schema
        CHECK constraint allows.
        """
        from ls_equity_fund.data.filings import _insert_filing, _insert_insider
        from ls_equity_fund.data.providers.edgar_provider import EdgarProvider

        provider = EdgarProvider(sec_user_agent="Smoke Test smoke@example.com")
        rows = provider.parse_form4(filename, FIXTURES / filename)
        assert len(rows) >= 1, f"parse_form4 returned no rows for {filename!r} — fixture broken"
        assert rows[0]["transaction_code"] == code

        # Insert filings_metadata first (no FK, but mirrors production order).
        _insert_filing(
            conn,
            {
                "accession_number": filename,
                "ticker": rows[0]["ticker"],
                "cik": "0000320193",
                "form_type": "4",
                "filed_date": "2026-04-15",
                "period_of_report": None,
                "filepath": str(FIXTURES / filename),
                "content_hash": None,
            },
        )
        rows[0]["filed_date"] = "2026-04-15"
        _insert_insider(conn, rows[0])

        persisted = conn.execute(
            "SELECT transaction_code FROM insider_transactions WHERE accession_number = ?",
            (filename,),
        ).fetchone()
        assert persisted is not None, "insider row not persisted"
        assert persisted["transaction_code"] == code

    def test_db_check_constraint_rejects_unknown_transaction_code(
        self, conn: sqlite3.Connection
    ) -> None:
        """Schema-level CP3 guard — INSERT with code='X' raises IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO insider_transactions "
                "(accession_number, line_no, ticker, transaction_code, "
                "transaction_date, filed_date) "
                "VALUES ('bad', 1, 'AAPL', 'X', '2026-01-01', '2026-01-01')"
            )

    def test_cluster_buys_count_only_p_codes(self, conn: sqlite3.Connection) -> None:
        """CP3 binding — cluster-buy detector counts ONLY ``transaction_code='P'``.

        Insert 3 P-purchases by 3 distinct insiders + 3 A-grants by 3 distinct
        names. Detector must flag the ticker with distinct_insiders=3 (P-only),
        NOT 6 (P + A combined).
        """
        from ls_equity_fund.data.insider import detect_cluster_buys

        sql = (
            "INSERT INTO insider_transactions "
            "(accession_number, line_no, ticker, insider_name, "
            "transaction_code, transaction_date, filed_date, total_value) "
            "VALUES (?, 1, 'AAPL', ?, ?, '2026-04-15', '2026-04-15', ?)"
        )
        # 3 P-purchases (directional — should count).
        for i, name in enumerate(["Alice", "Bob", "Carol"]):
            conn.execute(sql, (f"acc-p-{i}", name, "P", 100_000.0))
        # 3 A-grants (compensation — must NOT count).
        for i, name in enumerate(["Xavier", "Yvonne", "Zeke"]):
            conn.execute(sql, (f"acc-a-{i}", name, "A", 50_000.0))

        clusters = detect_cluster_buys(conn, today=date(2026, 4, 30))
        assert len(clusters) == 1
        assert clusters[0]["ticker"] == "AAPL"
        # 3 distinct P-coders — A-grants excluded.
        assert clusters[0]["distinct_insiders"] == 3


# =====================================================================
# SC4 — Selective skip flags (CLI + orchestrator)
# =====================================================================


class TestPhase1SC4SelectiveFlags:
    """SC4 — ``--no-filings``, ``--no-13f``, ``--forms`` and the
    mutually-exclusive guard between ``--no-filings`` and ``--forms``."""

    def test_cli_no_filings_and_forms_mutually_exclusive_exits_5(
        self, workspace: dict[str, Path]
    ) -> None:
        result = runner.invoke(
            app,
            [
                "run-data",
                "--config",
                str(workspace["config"]),
                "--env",
                str(workspace["env"]),
                "--no-filings",
                "--forms",
                "10-K",
            ],
        )
        assert result.exit_code == 5
        combined = (result.stdout or "") + (result.stderr or "")
        assert "mutually exclusive" in combined.lower()

    def test_cli_no_filings_forwards_flag_to_orchestrator(self, workspace: dict[str, Path]) -> None:
        with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
            orch.return_value = {
                "run_id": "x" * 16,
                "duration_seconds": 0,
                "filings": None,
                "institutional": None,
                "universe": 0,
            }
            result = runner.invoke(
                app,
                [
                    "run-data",
                    "--config",
                    str(workspace["config"]),
                    "--env",
                    str(workspace["env"]),
                    "--no-filings",
                ],
            )
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert orch.call_args.kwargs["no_filings"] is True
        assert orch.call_args.kwargs["no_13f"] is False

    def test_cli_no_13f_skips_only_13f(self, workspace: dict[str, Path]) -> None:
        with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
            orch.return_value = {
                "run_id": "x" * 16,
                "duration_seconds": 0,
                "filings": {"ok": 1},
                "institutional": None,
                "universe": 0,
            }
            result = runner.invoke(
                app,
                [
                    "run-data",
                    "--config",
                    str(workspace["config"]),
                    "--env",
                    str(workspace["env"]),
                    "--no-13f",
                ],
            )
        assert result.exit_code == 0
        kwargs = orch.call_args.kwargs
        assert kwargs["no_13f"] is True
        assert kwargs["no_filings"] is False

    def test_cli_forms_parsed_to_list_of_strings(self, workspace: dict[str, Path]) -> None:
        with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
            orch.return_value = {
                "run_id": "y" * 16,
                "duration_seconds": 0,
                "universe": 0,
            }
            result = runner.invoke(
                app,
                [
                    "run-data",
                    "--config",
                    str(workspace["config"]),
                    "--env",
                    str(workspace["env"]),
                    "--forms",
                    "10-K,10-Q",
                ],
            )
        assert result.exit_code == 0
        assert orch.call_args.kwargs["forms"] == ["10-K", "10-Q"]


# =====================================================================
# SC5 — DATA-14 swap-in seam (PolygonProvider stub)
# =====================================================================


class TestPhase1SC5DataProviderSeam:
    """SC5 — every fetch goes through MarketDataProvider; PolygonProvider stub
    instantiates and is selectable via config without rewriting downstream
    (DATA-14)."""

    def test_polygon_provider_instantiates_proves_seam(self) -> None:
        """Stub instantiation succeeds — the swap-in seam works."""
        from ls_equity_fund.data.providers import PolygonProvider

        provider = PolygonProvider(api_key="dummy")
        assert provider is not None
        assert provider.api_key == "dummy"

    def test_polygon_provider_methods_raise_not_implemented_with_data14_message(
        self,
    ) -> None:
        """Calling a Polygon method raises NotImplementedError with DATA-14 hint."""
        from ls_equity_fund.data.providers import PolygonProvider

        provider = PolygonProvider(api_key="dummy")
        with pytest.raises(NotImplementedError) as excinfo:
            provider.get_prices(["AAPL"], date(2026, 1, 1), date(2026, 4, 1))
        assert "DATA-14" in str(excinfo.value)

    def test_polygon_selected_via_config_orchestrator_refuses_DATA14_message(
        self, workspace: dict[str, Path]
    ) -> None:
        """End-to-end SC5 — set ``provider: polygon`` in config.yaml, run the
        CLI, expect exit 6 + DATA-14 in the user-facing error.
        """
        cfg_text = workspace["config"].read_text(encoding="utf-8")
        workspace["config"].write_text(
            cfg_text.replace("provider: yfinance", "provider: polygon"),
            encoding="utf-8",
        )
        result = runner.invoke(
            app,
            [
                "run-data",
                "--config",
                str(workspace["config"]),
                "--env",
                str(workspace["env"]),
            ],
        )
        assert result.exit_code == 6
        combined = (result.stdout or "") + (result.stderr or "")
        assert "DATA-14" in combined

    def test_six_sibling_provider_abcs_declared_and_abstract(self) -> None:
        """All six sibling ABCs (Phase 1 DATA-14) are declared and have at
        least one abstract method — provider authors can't subclass without
        implementing the contract."""
        from ls_equity_fund.data.providers import (
            EstimatesProvider,
            FilingsProvider,
            FundamentalsProvider,
            MacroProvider,
            OHLCVProvider,
            ShortInterestProvider,
        )

        for cls in (
            OHLCVProvider,
            FundamentalsProvider,
            ShortInterestProvider,
            EstimatesProvider,
            FilingsProvider,
            MacroProvider,
        ):
            assert cls.__abstractmethods__, (
                f"{cls.__name__} has no abstract methods — seam contract broken"
            )
            with pytest.raises(TypeError):
                cls()

    def test_phase0_marketdataprovider_facade_still_importable(self) -> None:
        """Phase 0 SC3 imports ``MarketDataProvider`` — must remain importable
        for backward compatibility (INFRA-03)."""
        from ls_equity_fund.data.base import MarketDataProvider

        assert MarketDataProvider is not None
        assert MarketDataProvider.__abstractmethods__
        with pytest.raises(TypeError):
            MarketDataProvider()

    def test_yfinance_provider_is_concrete(self) -> None:
        """The default v1 provider is concrete (instantiable).

        We don't construct the network session here; we assert the class is
        not abstract — that proves yfinance is the working concrete impl that
        Phase 1 relies on while PolygonProvider is the stub.
        """
        from ls_equity_fund.data.providers import YFinanceProvider

        assert YFinanceProvider is not None
        # Concrete class — abstractmethods set is empty.
        assert not getattr(YFinanceProvider, "__abstractmethods__", set())


# =====================================================================
# Phase 0 closure-invariant probe (defense in depth)
# =====================================================================


class TestPhase1Closure:
    """Phase 0 invariants must remain green — Phase 1 must not regress them."""

    def test_phase0_doctor_help_still_exits_zero(self) -> None:
        """Surface invariant — ``meridian doctor --help`` works post-Phase-1."""
        result = runner.invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0

    def test_phase0_run_data_help_still_exits_zero(self) -> None:
        """``meridian run-data --help`` is the Phase 1 surface; must work."""
        result = runner.invoke(app, ["run-data", "--help"])
        assert result.exit_code == 0
        for flag in ("--no-filings", "--no-13f", "--forms"):
            assert flag in result.stdout

    def test_phase1_migration_at_head_after_upgrade(self, migrated_db: Path) -> None:
        """Post-``alembic upgrade head``, alembic_version == '0002' (Phase 1
        head). If a future phase ships 0003+, this test will fail — at which
        point the closure gate moves with it."""
        c = sqlite3.connect(str(migrated_db))
        try:
            head = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        finally:
            c.close()
        assert head == "0002", (
            f"alembic head={head!r}; Phase 1 closure expects 0002. If a later "
            "phase has shipped a new migration, advance this assertion."
        )

    def test_phase1_tables_all_present_after_migration(self, migrated_db: Path) -> None:
        """The 13 Phase 1 tables (migration 0002) + 2 Phase 0 tables (0001)
        + alembic_version exist after upgrade."""
        c = sqlite3.connect(str(migrated_db))
        try:
            tables = {
                row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            c.close()
        # Phase 0
        assert {"runs", "heartbeat", "alembic_version"}.issubset(tables)
        # Phase 1
        phase1_tables = {
            "universe",
            "benchmarks",
            "daily_prices",
            "fundamentals",
            "fundamental_ratios",
            "filings_metadata",
            "insider_transactions",
            "institutional_holdings",
            "short_interest",
            "analyst_estimates",
            "earnings_calendar",
            "macro_calendar",
            "refresh_state",
        }
        missing = phase1_tables - tables
        assert not missing, f"Phase 1 tables missing post-migration: {missing}"
