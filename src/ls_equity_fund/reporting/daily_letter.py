"""Dual-mode daily letter with mandatory paper-system disclaimer (REPORT-08)."""

from __future__ import annotations

import sqlite3
import time
from datetime import date

from ls_equity_fund.reporting.commentary import TextClient

MANDATORY_DISCLAIMER = (
    "Internal performance log — Meridian Capital Partners is a single-operator "
    "paper-trading system; not an investment fund and not soliciting investors."
)


def doc_id_for(day: date) -> str:
    return f"MCP-IM-{day:%Y}-{day:%m%d}"


def generate_daily_letter(
    conn: sqlite3.Connection,
    *,
    day: date,
    mode: str,
    client: TextClient | None,
    domicile: str = "Delaware",
    fund_aum_usd: float = 1_000_000.0,
    regenerate: bool = False,
    paper_watermark: bool = True,
) -> str:
    if mode not in {"lp", "internal"}:
        raise ValueError("mode must be lp|internal")
    cached = conn.execute(
        "SELECT body_md FROM daily_letter WHERE date = ? AND mode = ?",
        (day.isoformat(), mode),
    ).fetchone()
    if cached and not regenerate:
        with conn:
            conn.execute(
                "UPDATE daily_letter SET cached = 1 WHERE date = ? AND mode = ?",
                (day.isoformat(), mode),
            )
        return str(cached[0])

    body = _body_from_client(client, mode=mode)
    doc_id = doc_id_for(day)
    if mode == "lp":
        letter = "\n\n".join(
            [
                "PAPER" if paper_watermark else "",
                "CONFIDENTIAL",
                f"Meridian Capital Partners ({domicile})",
                f"Fund AUM: ${fund_aum_usd:,.0f}",
                f"Doc ID: {doc_id}",
                "Dear Limited Partners,",
                body,
                "Sincerely,\nMeridian Capital Partners",
                "Compliance footer: Paper-only internal reporting; no offer, recommendation, or solicitation.",
                MANDATORY_DISCLAIMER,
            ]
        ).strip()
    else:
        letter = "\n\n".join(
            [
                f"Internal Ops Letter — {doc_id}",
                body or "JARVIS internal note: systems green, reporting package generated.",
                MANDATORY_DISCLAIMER,
            ]
        )
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_letter (
                date, mode, body_md, doc_id, generated_at, cached
            ) VALUES (?, ?, ?, ?, ?, 0)
            """,
            (day.isoformat(), mode, letter, doc_id, int(time.time())),
        )
    return letter


def _body_from_client(client: TextClient | None, *, mode: str) -> str:
    if client is None:
        if mode == "lp":
            return (
                "JARVIS reports that today's paper-trading session was processed through the "
                "standard reporting stack. Attribution, risk, execution, and tear-sheet outputs "
                "were generated from local SQLite records.\n\n"
                "The portfolio remains governed by hard veto controls and paper-only execution. "
                "Performance should be read as an internal operating log rather than investor material."
            )
        return "Ops note: daily package generated, SQLite cache populated, dashboard should read precomputed rows."
    prompt = "Write formal LP letter body." if mode == "lp" else "Write informal internal ops note."
    resp = client.call(
        system_blocks=["You are JARVIS. Write concise paper-trading performance commentary."],
        user_message=prompt,
        max_tokens=900,
    )
    return str(getattr(resp, "text", resp))


__all__ = ["MANDATORY_DISCLAIMER", "doc_id_for", "generate_daily_letter"]
