#!/usr/bin/env python3
"""Backfill 3+ years of SEC Form 4 filings for Phase A v4 insider IC validation.

The default ``meridian run-data --forms 4`` uses Phase 1's
``FORM4_LOOKBACK_DAYS = 90`` for first-run, which is the right cadence for
*ongoing* daily refresh (cluster-buy factor uses 30-day window, CEO/CFO
factor uses 90-day window). But for HISTORICAL replay across 3 years of
Phase A backtesting, we need every Form 4 from
``replay_start_date - 90 days`` onward — otherwise the insider factor scores
for early replay dates have no input data.

This script:
  1. Loads the active universe.
  2. For each ticker, calls EdgarProvider.fetch_filings with form="4" and
     since=BACKTEST_START minus the lookback window.
  3. Parses each Form 4 XML into insider_transactions rows.
  4. Persists into filings_metadata + insider_transactions (idempotent).
  5. Has a per-ticker wall-clock timeout (default 90 sec) so a single
     misbehaving filing cannot hang the entire backfill.

Idempotent. Re-running picks up where it left off.

Usage:
  uv run python scripts/backfill_form4_phase_a.py
  uv run python scripts/backfill_form4_phase_a.py --since 2023-02-09
  uv run python scripts/backfill_form4_phase_a.py --tickers AAPL,MSFT,NVDA  # smoke test
"""

from __future__ import annotations

import argparse
import signal
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ls_equity_fund.config import load_config
from ls_equity_fund.data.filings import _insert_filing, _insert_insider
from ls_equity_fund.data.providers.edgar_provider import EdgarProvider
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)


class _Timeout(Exception):
    """Raised by SIGALRM when a per-ticker fetch exceeds the wall-clock budget."""


def _alarm_handler(signum: int, frame: Any) -> None:
    raise _Timeout(f"signal {signum} fired — ticker exceeded wall-clock budget")


def _backfill_ticker(
    conn: sqlite3.Connection,
    provider: EdgarProvider,
    ticker: str,
    *,
    since: date,
    cache_dir: Path,
    timeout_sec: int,
) -> tuple[int, int]:
    """Fetch + parse + persist Form 4s for one ticker. Returns
    (filings_inserted, insider_inserted). Raises _Timeout if it goes over."""
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(timeout_sec)
    filings_inserted = 0
    insider_inserted = 0
    try:
        rows = provider.fetch_filings(
            ticker, ["4"], since=since, cache_dir=cache_dir
        )
        for fr in rows:
            try:
                _insert_filing(conn, fr)
                filings_inserted += 1
                insider_rows = provider.parse_form4(
                    fr["accession_number"], Path(fr["filepath"])
                )
                for ir in insider_rows:
                    ir["filed_date"] = fr["filed_date"]
                    _insert_insider(conn, ir)
                    insider_inserted += 1
            except Exception as exc:
                # One bad filing should not abort the whole ticker.
                log.warning(
                    "form4_filing_skipped",
                    ticker=ticker,
                    accession=fr.get("accession_number"),
                    error=str(exc)[:200],
                )
                continue
    finally:
        signal.alarm(0)
    return filings_inserted, insider_inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since",
        default=(date.today() - timedelta(days=3 * 365 + 90)).isoformat(),
        help="ISO date for the earliest Form 4 to fetch (default: today − 3y90d)",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Optional comma-separated ticker list to restrict scope (smoke testing)",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=90,
        help="Per-ticker wall-clock budget (default 90 sec)",
    )
    args = parser.parse_args()

    since_date = datetime.fromisoformat(args.since).date()
    config, secrets = load_config()
    db_path = get_db_path(config)
    cache_dir = Path(config.data.cache_dir) / "filings"
    cache_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        if args.tickers:
            universe = [t.strip() for t in args.tickers.split(",") if t.strip()]
        else:
            universe = [
                r[0]
                for r in conn.execute(
                    "SELECT ticker FROM universe WHERE delisted_date IS NULL ORDER BY ticker"
                )
            ]
        print(f"backfilling Form 4 for {len(universe)} tickers since={since_date}")
        print(f"cache_dir={cache_dir}")
        print(f"timeout per ticker: {args.timeout_sec}s\n")

        provider = EdgarProvider(sec_user_agent=secrets.sec_user_agent)
        start_ts = time.time()
        ok = failed = timed_out = 0
        total_filings = 0
        total_insiders = 0

        for i, ticker in enumerate(universe, start=1):
            try:
                f, ins = _backfill_ticker(
                    conn, provider, ticker,
                    since=since_date,
                    cache_dir=cache_dir,
                    timeout_sec=args.timeout_sec,
                )
                total_filings += f
                total_insiders += ins
                ok += 1
            except _Timeout:
                timed_out += 1
                log.warning("ticker_timed_out", ticker=ticker, budget=args.timeout_sec)
            except Exception as exc:
                failed += 1
                log.error("ticker_failed", ticker=ticker, error=str(exc)[:200])

            if i % 20 == 0:
                elapsed = int(time.time() - start_ts)
                rate = i / max(elapsed, 1)
                eta = int((len(universe) - i) / max(rate, 0.001))
                print(
                    f"  [{i}/{len(universe)}]  ok={ok}  failed={failed}  "
                    f"timed_out={timed_out}  filings={total_filings}  "
                    f"insiders={total_insiders}  elapsed={elapsed}s  eta={eta}s"
                )

        elapsed = int(time.time() - start_ts)
        print(
            f"\nDONE  total={len(universe)}  ok={ok}  failed={failed}  "
            f"timed_out={timed_out}  elapsed={elapsed}s\n"
            f"filings_inserted={total_filings}  insider_inserted={total_insiders}"
        )
        # Inventory.
        n_insider = conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0]
        n_form4 = conn.execute(
            "SELECT COUNT(*) FROM filings_metadata WHERE form_type='4'"
        ).fetchone()[0]
        print(f"DB now: {n_insider} insider_transactions, {n_form4} Form 4 metadata rows")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
