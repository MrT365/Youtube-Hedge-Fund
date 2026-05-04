"""``python -m ls_equity_fund.cli`` entry — equivalent to the ``meridian`` script.

Per CONTEXT D-23: a single Typer entry must be reachable via both the
``meridian`` console script (declared in pyproject.toml ``[project.scripts]``)
AND ``python -m ls_equity_fund.cli`` for environments where the script shim
is unavailable (e.g., a freshly checked-out repo before ``uv sync``).
"""

from __future__ import annotations

from ls_equity_fund.cli.app import app

if __name__ == "__main__":
    app()
