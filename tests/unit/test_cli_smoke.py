"""TDD RED — minimal smoke that the Typer app + doctor command exist.

This is a thin gating test for Task 1 only. The full coverage suite lives in
test_cli_doctor.py + test_cli_stubs.py (Task 3).
"""

from __future__ import annotations


def test_app_module_importable() -> None:
    from ls_equity_fund.cli.app import app

    assert app is not None
    assert app.info.name == "meridian"


def test_doctor_callable_importable() -> None:
    from ls_equity_fund.cli.doctor import doctor

    assert callable(doctor)
