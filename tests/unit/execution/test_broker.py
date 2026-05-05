from __future__ import annotations

from pathlib import Path

import pytest

from ls_equity_fund.config import BrokerConfig
from ls_equity_fund.execution.broker import IBKRBroker, LiveTradingGateError


def test_live_broker_refuses_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MERIDIAN_LIVE_OK", raising=False)
    cfg = BrokerConfig(mode="live", audit_promotion_path=str(tmp_path / "AUDIT-03.json"))
    with pytest.raises(LiveTradingGateError, match="MERIDIAN_LIVE_OK"):
        IBKRBroker(cfg, connect=False)


def test_live_broker_refuses_without_audit_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MERIDIAN_LIVE_OK", "1")
    cfg = BrokerConfig(mode="live", audit_promotion_path=str(tmp_path / "AUDIT-03.json"))
    with pytest.raises(LiveTradingGateError, match="AUDIT-03"):
        IBKRBroker(cfg, connect=False)


def test_live_broker_requires_both_gates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit = tmp_path / "AUDIT-03.json"
    audit.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("MERIDIAN_LIVE_OK", raising=False)
    with pytest.raises(LiveTradingGateError):
        IBKRBroker(BrokerConfig(mode="live", audit_promotion_path=str(audit)), connect=False)

    monkeypatch.setenv("MERIDIAN_LIVE_OK", "1")
    broker = IBKRBroker(BrokerConfig(mode="live", audit_promotion_path=str(audit)), connect=False)
    assert broker.is_paper is False
