"""Precomputed weekly Claude commentary (REPORT-07)."""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import Protocol


class TextClient(Protocol):
    model: str

    def call(self, *, system_blocks: list[str], user_message: str, max_tokens: int = 1500, temperature: float = 0.0) -> object:
        ...


def should_generate_commentary(day: date, *, weekday: int = 4) -> bool:
    return day.weekday() == weekday


def generate_weekly_commentary(
    conn: sqlite3.Connection,
    *,
    week_ending: date,
    client: TextClient | None,
    regenerate: bool = False,
) -> str | None:
    row = conn.execute(
        "SELECT body_md FROM weekly_commentary WHERE week_ending = ?",
        (week_ending.isoformat(),),
    ).fetchone()
    if row and not regenerate:
        with conn:
            conn.execute(
                "UPDATE weekly_commentary SET cached = 1 WHERE week_ending = ?",
                (week_ending.isoformat(),),
            )
        return str(row[0])
    if client is None:
        body = "JARVIS weekly commentary: reporting metrics precomputed and stored for dashboard consumption."
        model = "local-template"
    else:
        resp = client.call(
            system_blocks=["You are JARVIS writing concise weekly hedge-fund performance commentary."],
            user_message="Summarize the tear sheet metrics, top/bottom positions, and sector alpha.",
            max_tokens=900,
        )
        body = str(getattr(resp, "text", resp))
        model = getattr(client, "model", "unknown")
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO weekly_commentary (
                week_ending, model_id, body_md, generated_at, cached
            ) VALUES (?, ?, ?, ?, 0)
            """,
            (week_ending.isoformat(), model, body, int(time.time())),
        )
    return body


__all__ = ["TextClient", "generate_weekly_commentary", "should_generate_commentary"]
