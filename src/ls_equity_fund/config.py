"""Configuration models — minimal shim for Plan 00-04 (logging).

This file is a *placeholder* established by Plan 00-04 so that
`from ls_equity_fund.config import LoggingConfig` resolves while Plan 00-02
(the full Config schema with secrets, broker, risk, etc.) is being developed
in a parallel worktree.

Plan 00-02 will expand this file with the full `Config` model. The
`LoggingConfig` field set defined here is the locked surface (D-11..D-20)
and MUST be preserved when Plan 00-02 lands.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """Per CONTEXT D-11..D-20 — logging policy.

    Fields intentionally minimal at this stage; Plan 00-02 may add validators
    or related fields, but MUST keep the four below name-stable.
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: str = "logs"
    json_renderer_when_non_tty: bool = True
    # Extends DEFAULT_REDACT_KEYS at runtime (allowlist semantics).
    redact_keys: list[str] = Field(default_factory=list)


__all__ = ["LoggingConfig"]
