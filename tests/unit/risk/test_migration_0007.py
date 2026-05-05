"""Migration 0007 schema and immutability tests."""

from __future__ import annotations

import sqlite3

import pytest


def test_risk_tables_created(migrated_conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in migrated_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"risk_snapshots", "veto_log", "circuit_breaker_log"} <= tables


def test_veto_log_immutable_no_delete(migrated_conn: sqlite3.Connection) -> None:
    migrated_conn.execute(
        """
        INSERT INTO veto_log (timestamp, ticker, side, shares, reason, trade_context_json)
        VALUES (1, 'AAPL', 'long', 10, 'test', '{}')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute("DELETE FROM veto_log")


def test_veto_log_immutable_no_update(migrated_conn: sqlite3.Connection) -> None:
    migrated_conn.execute(
        """
        INSERT INTO veto_log (timestamp, ticker, side, shares, reason, trade_context_json)
        VALUES (1, 'AAPL', 'long', 10, 'test', '{}')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute("UPDATE veto_log SET reason = 'changed'")


def test_circuit_breaker_log_immutable_no_delete(migrated_conn: sqlite3.Connection) -> None:
    migrated_conn.execute(
        """
        INSERT INTO circuit_breaker_log (
            timestamp, breaker_type, threshold, observed_value, portfolio_state_json
        ) VALUES (1, 'daily_loss', -0.015, -0.02, '{}')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute("DELETE FROM circuit_breaker_log")
