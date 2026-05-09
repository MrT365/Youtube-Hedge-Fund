"""Universe builder — three modes (sp500, liquid_us, scanner_seed) with PIT integrity (DATA-01, DATA-13).

Binds CP1 — survivorship / look-ahead bias prevention. Delisted tickers are
FLAGGED (delisted_date set), never deleted. first_seen_date is preserved
across re-runs.

PIT query convention: "universe at date D" =
    SELECT * FROM universe
    WHERE first_seen_date <= D
      AND (delisted_date IS NULL OR delisted_date > D)
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from ls_equity_fund.config import Config
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def build_universe(
    config: Config,
    *,
    mode: str | None = None,
    conn: sqlite3.Connection | None = None,
    today: date | None = None,
    fixture_html_path: Path | None = None,
) -> int:
    """Build universe in given mode and merge into the ``universe`` table.

    Args:
        config: validated Config (data sub-config drives mode + thresholds).
        mode: override config.data.universe_mode if not None.
        conn: existing sqlite3 connection (e.g. from tests); when None, opens
            and closes a fresh connection from get_db_path(config).
        today: override date.today() — required for deterministic tests.
        fixture_html_path: when set, _build_sp500 reads from this path instead
            of fetching Wikipedia. Test injection only.

    Returns:
        Total row count in ``universe`` after merge (includes delisted rows
        per CP1 — delisted are FLAGGED, never DELETEd).
    """
    mode = mode or config.data.universe_mode
    today = today or date.today()
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        if mode == "sp500":
            rows = _build_sp500(fixture_html_path=fixture_html_path)
        elif mode == "liquid_us":
            rows = _build_liquid_us(config, conn)
        elif mode == "scanner_seed":
            rows = _build_scanner_seed(config)
        else:
            raise ValueError(f"unknown universe_mode: {mode!r}")

        stats = merge_universe_pit(rows, conn, today)
        log.info("universe_built", mode=mode, **stats)
        total = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
        return int(total)
    finally:
        if owns_conn:
            conn.close()


def merge_universe_pit(
    rows: list[dict[str, Any]],
    conn: sqlite3.Connection,
    today: date,
) -> dict[str, int]:
    """PIT-aware merge — flags delisted tickers; preserves first_seen_date.

    CP1 binding contract:
      - Tickers absent from ``rows`` but present in DB with delisted_date IS NULL
        → UPDATE delisted_date=today, inclusion_window="{first_seen_date}:{today}".
        Row is NEVER DELETEd.
      - first_seen_date is set ONLY on first INSERT and is preserved across all
        subsequent re-runs (including re-inclusion of a previously-delisted ticker).
      - Re-included tickers (delisted_date was non-NULL, now back in rows) get
        delisted_date cleared and inclusion_window reset to "{first_seen_date}:current".

    Returns:
        Dict with per-action counts: ``{"inserted": N, "updated": M,
        "delisted": K, "reincluded": R}``.
    """
    today_str = today.isoformat()
    now_ts = int(time.time())
    incoming_tickers = {r["ticker"] for r in rows}
    inserted = updated = delisted = reincluded = 0

    # Pre-fetch existing rows in one query (avoids N+1).
    existing = {
        row[0]: {"first_seen_date": row[1], "delisted_date": row[2]}
        for row in conn.execute("SELECT ticker, first_seen_date, delisted_date FROM universe")
    }

    # CP1 safety check: refuse to delist >50% of an established universe in one
    # run. A wholesale wipeout almost always means the upstream universe-mode
    # query failed (yfinance rate-limit, network blip, empty filter result), not
    # a legitimate mass-delisting event. Without this guard a transient upstream
    # failure poisons the entire pipeline (every downstream step then reads
    # ``WHERE delisted_date IS NULL`` and finds zero active tickers).
    #
    # The check only fires once the universe is "established" (>= 20 active
    # tickers) — otherwise tiny test/dev universes can't legitimately churn.
    n_existing_active = sum(1 for v in existing.values() if v["delisted_date"] is None)
    n_incoming = len(incoming_tickers)
    if n_existing_active >= 20 and n_incoming * 2 < n_existing_active:
        raise ValueError(
            f"universe-build aborted: incoming list has {n_incoming} tickers but "
            f"existing active universe has {n_existing_active} (would delist >50% "
            f"in one run). This usually means the upstream universe-mode query "
            f"failed. Inspect data.universe_mode + provider connectivity, then "
            f"re-run."
        )

    conn.execute("BEGIN")
    try:
        for r in rows:
            ticker = r["ticker"]
            if ticker not in existing:
                # New ticker — INSERT with first_seen_date=today, delisted_date=NULL.
                conn.execute(
                    """INSERT INTO universe
                       (ticker, company_name, exchange, primary_listing, sector,
                        industry, sub_industry, first_seen_date, delisted_date,
                        inclusion_window, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                    (
                        ticker,
                        r.get("company_name"),
                        r.get("exchange"),
                        r.get("primary_listing"),
                        r.get("sector"),
                        r.get("industry"),
                        r.get("sub_industry"),
                        today_str,
                        f"{today_str}:current",
                        now_ts,
                    ),
                )
                inserted += 1
            else:
                prev = existing[ticker]
                if prev["delisted_date"] is not None:
                    # Re-inclusion: was delisted, now back. first_seen_date PRESERVED.
                    conn.execute(
                        """UPDATE universe
                           SET company_name=?, exchange=?, primary_listing=?,
                               sector=?, industry=?, sub_industry=?,
                               delisted_date=NULL,
                               inclusion_window=?,
                               last_updated=?
                           WHERE ticker=?""",
                        (
                            r.get("company_name"),
                            r.get("exchange"),
                            r.get("primary_listing"),
                            r.get("sector"),
                            r.get("industry"),
                            r.get("sub_industry"),
                            f"{prev['first_seen_date']}:current",
                            now_ts,
                            ticker,
                        ),
                    )
                    reincluded += 1
                else:
                    # Active re-run: refresh metadata; first_seen_date preserved.
                    conn.execute(
                        """UPDATE universe
                           SET company_name=?, exchange=?, primary_listing=?,
                               sector=?, industry=?, sub_industry=?,
                               last_updated=?
                           WHERE ticker=?""",
                        (
                            r.get("company_name"),
                            r.get("exchange"),
                            r.get("primary_listing"),
                            r.get("sector"),
                            r.get("industry"),
                            r.get("sub_industry"),
                            now_ts,
                            ticker,
                        ),
                    )
                    updated += 1

        # CP1 binding — flag (do NOT delete) tickers absent from incoming list.
        for ticker, prev in existing.items():
            if ticker not in incoming_tickers and prev["delisted_date"] is None:
                conn.execute(
                    """UPDATE universe
                       SET delisted_date=?,
                           inclusion_window=?,
                           last_updated=?
                       WHERE ticker=?""",
                    (
                        today_str,
                        f"{prev['first_seen_date']}:{today_str}",
                        now_ts,
                        ticker,
                    ),
                )
                delisted += 1

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "inserted": inserted,
        "updated": updated,
        "delisted": delisted,
        "reincluded": reincluded,
    }


# ---------- Internal mode builders ----------


def _build_sp500(*, fixture_html_path: Path | None = None) -> list[dict[str, Any]]:
    """Wikipedia S&P 500 list (DATA-01 / sp500 mode).

    Reads fixture_html_path if given (tests); otherwise hits Wikipedia.
    Wikipedia column shape: Symbol, Security, GICS Sector, GICS Sub-Industry, ...

    Threat T-01-05 mitigation: ``match="Symbol"`` targets the constituents
    table; if the table moves or the column header changes, pd.read_html
    raises ValueError with a clear message.
    """
    if fixture_html_path is not None:
        tables = pd.read_html(str(fixture_html_path))
    else:
        # Wikipedia returns HTTP 403 to ``pd.read_html``'s default urllib UA.
        # Pre-fetch with requests + a real-browser UA, then hand the HTML body
        # to pd.read_html. Wikipedia's robots.txt permits this when a
        # descriptive UA + contact info are sent.
        import io
        import requests as _requests

        resp = _requests.get(
            WIKIPEDIA_SP500_URL,
            headers={
                "User-Agent": (
                    "Meridian Capital Partners ls-equity-fund "
                    "(contact@example.com) universe-builder/1.0"
                )
            },
            timeout=30,
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text), match="Symbol")
    df = tables[0]
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        # BRK.B / BF.B → BRK-B / BF-B (yfinance ticker convention).
        raw_symbol = str(row["Symbol"])
        ticker = raw_symbol.replace(".", "-")
        out.append(
            {
                "ticker": ticker,
                "company_name": str(row.get("Security", "")),
                # Wikipedia table doesn't split exchange — use a marker that
                # downstream code can interpret as "either NYSE or NASDAQ".
                "exchange": "NYSE/NASDAQ",
                "primary_listing": "US",
                "sector": str(row.get("GICS Sector", "")),
                "industry": str(row.get("GICS Sub-Industry", "")),
                "sub_industry": str(row.get("GICS Sub-Industry", "")),
            }
        )
    return out


def _build_liquid_us(config: Config, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Filtered scanner over daily_prices + yfinance metadata.

    Falls back to scanner_seed if daily_prices is empty (Phase 1 first-run
    case — Plan 04 ships OHLCV; the orchestrator sequences 04 before 02 in
    Wave 2 but a clean first run on a fresh DB still hits this branch).
    """
    threshold_cnt = conn.execute("SELECT COUNT(DISTINCT ticker) FROM daily_prices").fetchone()[0]
    if threshold_cnt == 0:
        log.warning(
            "liquid_us_falls_back_to_seed",
            reason="daily_prices empty; ADV cannot be computed yet",
            seed_count=len(config.data.scanner_seed_tickers),
        )
        return _build_scanner_seed(config)

    cfg = config.data.liquid_us
    rows = conn.execute(
        """SELECT ticker,
                  AVG(close * volume) AS adv_20d,
                  AVG(close)           AS avg_close
           FROM (
             SELECT ticker, close, volume,
                    ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
             FROM daily_prices
           )
           WHERE rn <= 20
           GROUP BY ticker
           HAVING avg_close >= ? AND adv_20d >= ?""",
        (cfg.min_price, cfg.min_avg_dollar_volume_20d),
    ).fetchall()
    candidates = [row[0] for row in rows]

    return _enrich_with_yfinance(
        candidates,
        min_market_cap=cfg.min_market_cap,
        allowed_exchanges=set(cfg.exchanges),
    )


def _build_scanner_seed(config: Config) -> list[dict[str, Any]]:
    """Use config.data.scanner_seed_tickers; enrich sector/industry via yfinance."""
    return _enrich_with_yfinance(
        config.data.scanner_seed_tickers,
        min_market_cap=0.0,
        allowed_exchanges=None,
    )


def _enrich_with_yfinance(
    tickers: list[str],
    *,
    min_market_cap: float,
    allowed_exchanges: set[str] | None,
) -> list[dict[str, Any]]:
    """Look up sector/industry/exchange/market_cap from yfinance Ticker.info.

    Threat T-01-07 mitigation: yfinance occasionally throws on bot-detection
    retries; we log and continue with empty info (sector defaults to
    'unknown'). The market-cap and exchange filters use 0 / empty-string
    defaults so a yfinance failure is treated as "include with unknown
    metadata" rather than a hard exclude — survivorship is preserved.
    """
    import yfinance as yf

    out: list[dict[str, Any]] = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
        except Exception as e:
            log.warning("yfinance_info_failed", ticker=t, error=str(e))
            info = {}

        market_cap = float(info.get("marketCap") or 0)
        exchange = str(info.get("exchange") or "")
        if allowed_exchanges is not None and exchange and exchange not in allowed_exchanges:
            continue
        if market_cap < min_market_cap:
            continue

        out.append(
            {
                "ticker": t,
                "company_name": info.get("longName") or info.get("shortName") or t,
                "exchange": exchange or "unknown",
                "primary_listing": "US",
                "sector": info.get("sector") or "unknown",
                "industry": info.get("industry") or "unknown",
                "sub_industry": info.get("industry") or "unknown",
            }
        )
    return out


__all__ = ["build_universe", "merge_universe_pit"]
