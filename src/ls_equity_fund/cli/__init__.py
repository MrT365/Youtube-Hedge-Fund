"""CLI package — Typer-based entry points (per CONTEXT D-23).

Public:
  app — Typer instance; entry-point hook for the ``meridian`` console script.
"""

from __future__ import annotations

from ls_equity_fund.cli.app import app

__all__ = ["app"]
