from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from ls_equity_fund.dashboard.jarvis_chat import (
    MAX_HISTORY_TURNS,
    SESSION_COST_LIMIT_USD,
    ask_jarvis,
    build_user_message,
)
from ls_equity_fund.dashboard.jarvis_snapshot import MAX_SNAPSHOT_BYTES, write_snapshot
from ls_equity_fund.dashboard.pages.page_iii_risk import circuit_breaker_status, mctr_flags
from ls_equity_fund.dashboard.pages.page_vi_letter import latest_letter
from ls_equity_fund.dashboard.runtime import heartbeat_status
from ls_equity_fund.reporting.daily_letter import MANDATORY_DISCLAIMER


class FakeClient:
    model = "fake"

    def call(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return type("Resp", (), {"text": "ok", "usage": {"input_tokens": 100, "output_tokens": 10}})()


def test_mctr_flag_triggers_above_2x_average() -> None:
    out = mctr_flags(pd.DataFrame({"ticker": ["A", "B", "C"], "mctr": [1.0, 1.0, 10.0]}))
    assert bool(out[out["ticker"] == "C"]["disproportionate_risk"].iloc[0])


def test_circuit_breaker_status_from_sqlite() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE circuit_breaker_log (breaker_type TEXT, threshold REAL, observed_value REAL, timestamp INTEGER)")
    conn.execute("INSERT INTO circuit_breaker_log VALUES ('DAILY_LOSS', -0.015, -0.02, 1)")
    status = circuit_breaker_status(conn)
    assert status[status["breaker"] == "DAILY_LOSS"]["status"].iloc[0] == "TRIGGERED"


def test_lp_letter_page_data_has_required_fields() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_letter (date TEXT, mode TEXT, body_md TEXT, doc_id TEXT, generated_at INTEGER, cached INTEGER)")
    body = f"CONFIDENTIAL\nPAPER\nMCP-IM-2026-0501\nDear Limited Partners,\n{MANDATORY_DISCLAIMER}"
    conn.execute("INSERT INTO daily_letter VALUES ('2026-05-01','lp',?, 'MCP-IM-2026-0501', 1, 0)", (body,))
    row = latest_letter(conn, day=datetime(2026, 5, 1).date(), mode="lp")
    assert row is not None
    assert "CONFIDENTIAL" in row["body_md"]
    assert "PAPER" in row["body_md"]
    assert MANDATORY_DISCLAIMER in row["body_md"]


def test_snapshot_valid_json_under_19kb(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    path = write_snapshot(conn, tmp_path / "jarvis_snapshot.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "portfolio" in data
    assert path.stat().st_size <= MAX_SNAPSHOT_BYTES


def test_jarvis_chat_cached_context_and_cost_guard(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"portfolio": {"net_beta": 0.1}}', encoding="utf-8")
    history = [{"role": "user", "content": str(i)} for i in range(20)]
    msg = build_user_message(snapshot.read_text(), history, "hello")
    assert len(json.loads(msg)["history"]) == MAX_HISTORY_TURNS
    client = FakeClient()
    result = ask_jarvis(client=client, snapshot_path=snapshot, history=history, user_message="hello", session_cost_usd=0.0)
    assert result.text == "ok"
    assert "snapshot" in client.kwargs["user_message"]
    blocked = ask_jarvis(client=client, snapshot_path=snapshot, history=[], user_message="x", session_cost_usd=SESSION_COST_LIMIT_USD + 0.01)
    assert blocked.disabled


def test_heartbeat_status_red_and_green(tmp_path: Path) -> None:
    hb = tmp_path / "last_run_completed.txt"
    now = datetime.now(UTC)
    hb.write_text(f"completed_at={(now - timedelta(hours=25)).isoformat()} exit_code=0", encoding="utf-8")
    assert heartbeat_status(hb, now=now).status == "red"
    hb.write_text(f"completed_at={(now - timedelta(hours=1)).isoformat()} exit_code=0", encoding="utf-8")
    assert heartbeat_status(hb, now=now).status == "green"
