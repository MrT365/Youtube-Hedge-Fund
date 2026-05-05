"""L1 daily-refresh orchestrator (DATA-12).

Chains Plans 02-08 refresh functions in dependency order:
    universe -> benchmarks -> prices -> fundamentals -> ratios ->
    filings -> 13F -> short -> estimates -> earnings -> macro

Per-step failure logs but does NOT abort the chain — partial L1 is acceptable;
full halt is not (the launchd 17:15 job must always produce SOMETHING for
the dashboard).

Selective skip flags (DATA-12):
    --no-filings  -> skip 10-K/10-Q/8-K/4 + 13F
    --no-13f      -> skip 13F only
    --forms       -> restrict filings to listed forms; mutually exclusive with --no-filings

Provider guard (DATA-14):
    config.data.provider must be "yfinance" — Polygon stub refuses with a
    clear deferred-feature message.
"""
from __future__ import annotations

import sqlite3
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

import structlog

from ls_equity_fund.config import Config, Secrets
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.logging import bind_run_id

log = structlog.get_logger(__name__)

DEFAULT_PHASE1_FORMS: list[str] = ["10-K", "10-Q", "8-K", "4"]
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"yfinance"})


def run_data_pipeline(
    config: Config,
    secrets: Secrets,
    *,
    no_filings: bool = False,
    no_13f: bool = False,
    forms: list[str] | None = None,
    tickers: list[str] | None = None,
    today: date | None = None,
    conn: sqlite3.Connection | None = None,
    universe_mode: str | None = None,
) -> dict[str, Any]:
    """Orchestrate the L1 pipeline. Returns per-step manifest.

    Args:
        config: validated Config (pydantic-settings).
        secrets: validated Secrets (.env-loaded).
        no_filings: skip refresh_filings + refresh_institutional_holdings.
        no_13f: skip refresh_institutional_holdings only.
        forms: restrict filings to these forms (mutually exclusive with no_filings).
        tickers: restrict to these tickers (testing/dev override).
        today: date override (testing/replay).
        conn: existing SQLite connection (caller-owned). When None, opens + closes.
        universe_mode: override config.data.universe_mode for build_universe.

    Returns:
        Manifest dict with per-step results, run_id, start/end ts, duration.

    Raises:
        ValueError: --forms passed alongside --no-filings.
        SystemExit: provider guard rejection (config.data.provider != yfinance).
    """
    if no_filings and forms:
        raise ValueError(
            "--forms is mutually exclusive with --no-filings; pick one"
        )
    if config.data.provider not in SUPPORTED_PROVIDERS:
        raise SystemExit(
            f"provider={config.data.provider!r} not yet supported "
            f"(see DATA-14). Set data.provider='yfinance' in config.yaml "
            f"until the {config.data.provider} integration milestone ships."
        )

    today = today or date.today()
    forms = forms or list(DEFAULT_PHASE1_FORMS)

    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))

    run_id = str(uuid.uuid4())
    bind_run_id(run_id)
    start_ts = int(time.time())
    _open_runs_row(conn, run_id, start_ts)
    log.info(
        "run_data_pipeline_started",
        run_id=run_id,
        no_filings=no_filings,
        no_13f=no_13f,
        forms=forms,
        today=today.isoformat(),
        provider=config.data.provider,
    )

    manifest: dict[str, Any] = {"run_id": run_id, "start_ts": start_ts}
    overall_status = "OK"
    error_msg: str | None = None

    try:
        # 1. Universe
        manifest["universe"] = _step(
            "universe",
            lambda: _build_universe_step(config, conn, today, universe_mode),
        )

        # 2. Benchmarks
        manifest["benchmarks"] = _step(
            "benchmarks",
            lambda: _refresh_benchmarks_step(config, conn),
        )

        # 3. Prices
        manifest["prices"] = _step(
            "prices",
            lambda: _refresh_prices_step(config, conn, tickers, today),
        )

        # 4. Fundamentals
        manifest["fundamentals"] = _step(
            "fundamentals",
            lambda: _refresh_fundamentals_step(config, conn, tickers, today),
        )

        # 5. Ratios
        manifest["ratios"] = _step(
            "ratios", lambda: _compute_ratios_step(conn, today)
        )

        # 6. Filings (gated by --no-filings + --forms)
        if no_filings:
            log.info("skip_filings", reason="--no-filings")
            manifest["filings"] = None
        else:
            manifest["filings"] = _step(
                "filings",
                lambda: _refresh_filings_step(
                    config, secrets, conn, forms, tickers, today
                ),
            )

        # 7. 13F (gated by --no-filings OR --no-13f)
        if no_filings or no_13f:
            log.info(
                "skip_13f",
                reason="--no-filings" if no_filings else "--no-13f",
            )
            manifest["institutional"] = None
        else:
            manifest["institutional"] = _step(
                "13f",
                lambda: _refresh_13f_step(config, secrets, conn),
            )

        # 8. Short interest
        manifest["short_interest"] = _step(
            "short_interest",
            lambda: _refresh_short_interest_step(config, conn, tickers, today),
        )

        # 9. Estimates
        manifest["estimates"] = _step(
            "estimates",
            lambda: _refresh_estimates_step(config, conn, tickers, today),
        )

        # 10. Earnings calendar
        manifest["earnings_calendar"] = _step(
            "earnings_calendar",
            lambda: _refresh_earnings_step(config, conn, tickers, today),
        )

        # 11. Macro calendar (weekly-gated internally)
        manifest["macro"] = _step(
            "macro", lambda: _refresh_macro_step(config, conn, today)
        )

    except SystemExit:
        raise
    except Exception as e:  # pragma: no cover — orchestrator-level fatal
        overall_status = "FAILED"
        error_msg = (str(e) + "\n" + traceback.format_exc())[:500]
        log.error("run_data_pipeline_failed", error=str(e))
    finally:
        end_ts = int(time.time())
        manifest["duration_seconds"] = end_ts - start_ts
        _close_runs_row(conn, run_id, end_ts, overall_status, error_msg)
        if owns_conn:
            conn.close()
        log.info(
            "run_data_pipeline_complete",
            status=overall_status,
            duration_seconds=manifest["duration_seconds"],
        )

    return manifest


def _step(name: str, fn: Callable[[], Any]) -> Any:
    """Run a step; per-step failure logs and returns ``{"error": str}`` —
    does NOT abort the chain. Matches Wave 2 plans' log+continue philosophy.
    """
    try:
        log.info("step_started", step=name)
        result = fn()
        log.info("step_complete", step=name, result=str(result)[:200])
        return result
    except Exception as e:
        log.error("step_failed", step=name, error=str(e))
        return {"error": str(e)}


def _open_runs_row(conn: sqlite3.Connection, run_id: str, start_ts: int) -> None:
    """INSERT a 'RUNNING' row in `runs` at orchestrator entry."""
    conn.execute(
        "INSERT INTO runs (run_id, start_ts, end_ts, status, error) "
        "VALUES (?, ?, NULL, 'RUNNING', NULL)",
        (run_id, start_ts),
    )


def _close_runs_row(
    conn: sqlite3.Connection,
    run_id: str,
    end_ts: int,
    status: str,
    error: str | None,
) -> None:
    """UPDATE the runs row at orchestrator exit (OK or FAILED)."""
    conn.execute(
        "UPDATE runs SET end_ts=?, status=?, error=? WHERE run_id=?",
        (end_ts, status, error, run_id),
    )


# ---------- step adapters ----------
# Lazy imports + thin wrappers so the orchestrator surface stays clean and
# tests can patch each adapter independently without touching the underlying
# refresh-function modules.


def _build_universe_step(
    config: Config,
    conn: sqlite3.Connection,
    today: date,
    mode: str | None,
) -> Any:
    from ls_equity_fund.data.universe import build_universe

    return build_universe(config, mode=mode, conn=conn, today=today)


def _refresh_benchmarks_step(config: Config, conn: sqlite3.Connection) -> Any:
    from ls_equity_fund.data.benchmarks import refresh_benchmarks

    return refresh_benchmarks(config, conn=conn)


def _refresh_prices_step(
    config: Config,
    conn: sqlite3.Connection,
    tickers: list[str] | None,
    today: date,
) -> Any:
    from ls_equity_fund.data.prices import refresh_prices

    return refresh_prices(config, conn=conn, tickers=tickers, today=today)


def _refresh_fundamentals_step(
    config: Config,
    conn: sqlite3.Connection,
    tickers: list[str] | None,
    today: date,
) -> Any:
    from ls_equity_fund.data.fundamentals import refresh_fundamentals

    return refresh_fundamentals(config, conn=conn, tickers=tickers, today=today)


def _compute_ratios_step(conn: sqlite3.Connection, today: date) -> Any:
    from ls_equity_fund.data.ratios import compute_all_ratios

    return compute_all_ratios(conn, today)


def _refresh_filings_step(
    config: Config,
    secrets: Secrets,
    conn: sqlite3.Connection,
    forms: list[str],
    tickers: list[str] | None,
    today: date,
) -> Any:
    from ls_equity_fund.data.filings import refresh_filings

    return refresh_filings(
        config,
        secrets,
        conn=conn,
        forms=forms,
        tickers=tickers,
        today=today,
    )


def _refresh_13f_step(
    config: Config,
    secrets: Secrets,
    conn: sqlite3.Connection,
) -> Any:
    from ls_equity_fund.data.institutional import refresh_institutional_holdings

    return refresh_institutional_holdings(config, secrets, conn=conn)


def _refresh_short_interest_step(
    config: Config,
    conn: sqlite3.Connection,
    tickers: list[str] | None,
    today: date,
) -> Any:
    from ls_equity_fund.data.short_interest import refresh_short_interest

    return refresh_short_interest(config, conn=conn, tickers=tickers, today=today)


def _refresh_estimates_step(
    config: Config,
    conn: sqlite3.Connection,
    tickers: list[str] | None,
    today: date,
) -> Any:
    from ls_equity_fund.data.estimates import refresh_estimates

    return refresh_estimates(config, conn=conn, tickers=tickers, today=today)


def _refresh_earnings_step(
    config: Config,
    conn: sqlite3.Connection,
    tickers: list[str] | None,
    today: date,
) -> Any:
    from ls_equity_fund.data.earnings_calendar import refresh_earnings_calendar

    return refresh_earnings_calendar(
        config, conn=conn, tickers=tickers, today=today
    )


def _refresh_macro_step(
    config: Config,
    conn: sqlite3.Connection,
    today: date,
) -> Any:
    from ls_equity_fund.data.macro_calendar import refresh_macro_calendar

    return refresh_macro_calendar(config, conn=conn, today=today)


__all__ = [
    "DEFAULT_PHASE1_FORMS",
    "SUPPORTED_PROVIDERS",
    "run_data_pipeline",
]
