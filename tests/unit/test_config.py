"""Tests for ls_equity_fund.config — composed Config + isolated Secrets.

Covers all 9 behaviors from Plan 00-02:
1. Imports succeed without YAML/env present (no module-level loading).
2. Loading the shipped config.yaml.example produces a fully-populated Config.
3. Missing required field (risk.breakers.daily_loss_pct) raises ValidationError.
4. Wrong type (paper_port: not_an_int) raises ValidationError.
5. Invalid Literal (optimizer: lasso) raises ValidationError.
6. env-var BROKER__PAPER_PORT=9999 overrides yaml's 7497 (D-13).
7. Secrets reads ANTHROPIC_API_KEY etc. from a supplied .env file.
8. Putting anthropic_api_key in YAML does NOT make it appear on Secrets (D-14).
9. Secrets with no env at all raises ValidationError on missing required field.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from ls_equity_fund.config import (
    AnthropicConfig,
    BreakersConfig,
    BrokerConfig,
    Config,
    DataConfig,
    LoggingConfig,
    PortfolioConfig,
    RiskConfig,
    Secrets,
    load_config,
)


# Test 1
def test_imports_succeed() -> None:
    """All public names import without side effects (no module-level YAML/env load)."""
    assert Config is not None
    assert Secrets is not None
    assert callable(load_config)
    # Each sub-config class is exported.
    for cls in (
        DataConfig,
        BrokerConfig,
        RiskConfig,
        BreakersConfig,
        PortfolioConfig,
        AnthropicConfig,
        LoggingConfig,
    ):
        assert isinstance(cls, type)


# Test 2
def test_load_config_from_example(fresh_yaml_path: Path, fresh_env_path: Path) -> None:
    """Loading the shipped config.yaml.example produces fully-populated Config."""
    config, secrets = load_config(yaml_path=fresh_yaml_path, env_path=fresh_env_path)

    # Six sub-configs all populated.
    assert isinstance(config.data, DataConfig)
    assert isinstance(config.broker, BrokerConfig)
    assert isinstance(config.risk, RiskConfig)
    assert isinstance(config.portfolio, PortfolioConfig)
    assert isinstance(config.anthropic, AnthropicConfig)
    assert isinstance(config.logging, LoggingConfig)

    # Spot-check key field values match config.yaml.example.
    assert config.broker.paper_port == 7497
    assert config.broker.live_port == 7496
    assert config.broker.mode == "paper"

    assert config.portfolio.num_longs == 20
    assert config.portfolio.num_shorts == 20
    assert config.portfolio.optimizer == "conviction"

    assert config.risk.breakers.daily_loss_pct == -1.5
    assert config.risk.breakers.drawdown_pct == -8.0
    assert config.risk.factor_model_window_days == 120

    assert config.anthropic.model == "claude-sonnet-4-5"
    assert config.anthropic.cost_ceiling_usd == 25.0

    assert config.logging.level == "INFO"

    # Secrets populated from .env (D-14).
    assert secrets.anthropic_api_key == "sk-ant-test-key-do-not-use"
    assert secrets.sec_user_agent.startswith("Meridian Capital Partners")


# Test 3
def test_missing_required_field_raises(tmp_path: Path, fresh_env_path: Path) -> None:
    """A YAML missing risk.breakers.daily_loss_pct raises ValidationError before any layer runs."""
    bad_yaml = tmp_path / "config.yaml"
    bad_yaml.write_text(
        textwrap.dedent("""
            data: {provider: yfinance, universe_mode: liquid_us, lookback_years: 3, benchmark: SPY, cache_dir: cache}
            broker: {paper_host: 127.0.0.1, paper_port: 7497, live_port: 7496, client_id: 17, mode: paper}
            risk:
              factor_model_window_days: 120
              veto_checks: []
              breakers:
                # daily_loss_pct missing
                daily_loss_pct_hard: -2.5
                weekly_loss_pct: -4.0
                drawdown_pct: -8.0
                single_position_pct: 3.0
            portfolio: {optimizer: conviction, num_longs: 20, num_shorts: 20, max_position_pct: 0.05, max_sector_pct: 0.25, gross_target: 1.5, net_target_low: 0.0, net_target_high: 0.1, max_beta: 0.15, turnover_budget: 0.3, mvo_risk_aversion: 1.0}
            anthropic: {model: claude-sonnet-4-5, cost_ceiling_usd: 25, cache_ttl_days: 30, prompt_caching: true, max_candidates: 40}
            logging: {level: INFO, log_dir: logs, json_renderer_when_non_tty: true, redact_keys: []}
        """).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="daily_loss_pct"):
        load_config(yaml_path=bad_yaml, env_path=fresh_env_path)


# Test 4
def test_wrong_type_raises(fresh_yaml_path: Path, fresh_env_path: Path) -> None:
    """broker.paper_port = 'not_an_int' raises ValidationError (D-15: bad type fails at boot)."""
    text = fresh_yaml_path.read_text()
    text = text.replace("paper_port: 7497", "paper_port: not_an_int")
    fresh_yaml_path.write_text(text)
    with pytest.raises(ValidationError):
        load_config(yaml_path=fresh_yaml_path, env_path=fresh_env_path)


# Test 5
def test_invalid_literal_raises(fresh_yaml_path: Path, fresh_env_path: Path) -> None:
    """portfolio.optimizer = 'lasso' (not in Literal) raises ValidationError."""
    text = fresh_yaml_path.read_text()
    text = text.replace("optimizer: conviction", "optimizer: lasso")
    fresh_yaml_path.write_text(text)
    with pytest.raises(ValidationError, match="optimizer"):
        load_config(yaml_path=fresh_yaml_path, env_path=fresh_env_path)


# Test 6
def test_env_nested_delimiter_overrides_yaml(
    fresh_yaml_path: Path, fresh_env_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env-var BROKER__PAPER_PORT=9999 overrides yaml's 7497 (D-13)."""
    monkeypatch.setenv("BROKER__PAPER_PORT", "9999")
    config, _ = load_config(yaml_path=fresh_yaml_path, env_path=fresh_env_path)
    assert config.broker.paper_port == 9999


# Test 7
def test_secrets_loads_from_env(fresh_env_path: Path) -> None:
    """Secrets reads from the supplied .env file."""
    secrets = Secrets(_env_file=str(fresh_env_path))  # type: ignore[call-arg]
    assert secrets.anthropic_api_key == "sk-ant-test-key-do-not-use"
    assert secrets.ibkr_username == "test_user"
    assert secrets.ibkr_password == "test_pass"
    assert secrets.sec_user_agent == "Meridian Capital Partners test@example.com"


# Test 8
def test_secrets_does_not_load_from_yaml(tmp_path: Path, fresh_env_path: Path) -> None:
    """Putting anthropic_api_key in config.yaml does NOT leak into Secrets (D-14).

    This is the load-bearing test for the secrets-isolation property.
    """
    bad_yaml = tmp_path / "config.yaml"
    bad_yaml.write_text(
        textwrap.dedent("""
            anthropic_api_key: sk-ant-LEAKED-IN-YAML
            sec_user_agent: LEAKED-AGENT
            data: {provider: yfinance, universe_mode: liquid_us, lookback_years: 3, benchmark: SPY, cache_dir: cache}
            broker: {paper_host: 127.0.0.1, paper_port: 7497, live_port: 7496, client_id: 17, mode: paper}
            risk:
              factor_model_window_days: 120
              veto_checks: []
              breakers: {daily_loss_pct: -1.5, daily_loss_pct_hard: -2.5, weekly_loss_pct: -4.0, drawdown_pct: -8.0, single_position_pct: 3.0}
            portfolio: {optimizer: conviction, num_longs: 20, num_shorts: 20, max_position_pct: 0.05, max_sector_pct: 0.25, gross_target: 1.5, net_target_low: 0.0, net_target_high: 0.1, max_beta: 0.15, turnover_budget: 0.3, mvo_risk_aversion: 1.0}
            anthropic: {model: claude-sonnet-4-5, cost_ceiling_usd: 25, cache_ttl_days: 30, prompt_caching: true, max_candidates: 40}
            logging: {level: INFO, log_dir: logs, json_renderer_when_non_tty: true, redact_keys: []}
        """).strip(),
        encoding="utf-8",
    )
    # Secrets only sees fresh_env_path's value, NOT the YAML's.
    _config, secrets = load_config(yaml_path=bad_yaml, env_path=fresh_env_path)
    assert secrets.anthropic_api_key == "sk-ant-test-key-do-not-use"
    assert "LEAKED" not in secrets.anthropic_api_key
    assert "LEAKED" not in secrets.sec_user_agent


# Test 9
def test_secrets_missing_required_raises(tmp_path: Path) -> None:
    """Secrets with no .env and no env vars set raises ValidationError on anthropic_api_key.

    The autouse `isolate_env` fixture has already cleared ANTHROPIC_API_KEY /
    SEC_USER_AGENT from process env, so this validates that BaseSettings has
    no default for these required fields.
    """
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError):
        Secrets(_env_file=str(empty_env))  # type: ignore[call-arg]
