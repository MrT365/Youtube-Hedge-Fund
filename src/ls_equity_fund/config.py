"""Composed pydantic-settings configuration + isolated Secrets.

Per CONTEXT D-11: Config is composed of six sub-config classes
(data, broker, risk, portfolio, anthropic, logging).
Per CONTEXT D-12: config.yaml lives at repo root; loaded via PyYAML safe_load only.
Per CONTEXT D-13: env-var nesting via env_nested_delimiter='__' so
  ``BROKER__PAPER_PORT=7497`` flows into ``Config.broker.paper_port`` and
  OVERRIDES the YAML value (env beats YAML).
Per CONTEXT D-14: Secrets is a separate BaseSettings class loaded ONLY from .env.
  No YAML loader. Putting secret-named fields in config.yaml has zero effect.
Per CONTEXT D-15: validation fires at ``load_config()`` — bad config raises
  ``pydantic.ValidationError`` BEFORE any layer code runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# ---------- Sub-configs (composed under Config) ----------


class DataConfig(BaseModel):
    """Market-data layer config (DataConfig)."""

    provider: Literal["yfinance", "polygon", "tiingo", "iex"] = "yfinance"
    universe_mode: Literal["sp500", "liquid_us", "scanner_seed"] = "liquid_us"
    lookback_years: int = Field(default=3, ge=1, le=20)
    benchmark: str = "SPY"
    cache_dir: str = "cache"
    yfinance_max_workers: int = Field(default=8, ge=1, le=32)


class BrokerConfig(BaseModel):
    """IBKR broker connection config (BrokerConfig)."""

    paper_host: str = "127.0.0.1"
    paper_port: int = Field(default=7497, ge=1, le=65535)
    live_port: int = Field(default=7496, ge=1, le=65535)
    client_id: int = Field(default=17, ge=0, le=999)
    mode: Literal["paper", "live"] = "paper"


class BreakersConfig(BaseModel):
    """Circuit-breaker thresholds (nested under RiskConfig)."""

    daily_loss_pct: float
    daily_loss_pct_hard: float
    weekly_loss_pct: float
    drawdown_pct: float
    single_position_pct: float


class RiskConfig(BaseModel):
    """Risk layer config (RiskConfig)."""

    factor_model_window_days: int = Field(default=120, ge=20, le=500)
    veto_checks: list[str] = Field(default_factory=list)
    breakers: BreakersConfig


class PortfolioConfig(BaseModel):
    """Portfolio construction config (PortfolioConfig)."""

    optimizer: Literal["conviction", "mvo"] = "conviction"
    num_longs: int = Field(default=20, ge=1, le=200)
    num_shorts: int = Field(default=20, ge=1, le=200)
    max_position_pct: float = Field(default=0.05, gt=0, le=1)
    max_sector_pct: float = Field(default=0.25, gt=0, le=1)
    gross_target: float = Field(default=1.50, gt=0, le=5)
    net_target_low: float = 0.0
    net_target_high: float = 0.10
    max_beta: float = Field(default=0.15, ge=0, le=2)
    turnover_budget: float = Field(default=0.30, gt=0, le=1)
    mvo_risk_aversion: float = Field(default=1.0, gt=0)


class AnthropicConfig(BaseModel):
    """Anthropic Claude analysis config (AnthropicConfig)."""

    model: str = "claude-sonnet-4-5"
    cost_ceiling_usd: float = Field(default=25.0, gt=0)
    cache_ttl_days: int = Field(default=30, ge=1)
    prompt_caching: bool = True
    max_candidates: int = Field(default=40, ge=1, le=500)


class LoggingConfig(BaseModel):
    """structlog + stdlib logging config (LoggingConfig)."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: str = "logs"
    json_renderer_when_non_tty: bool = True
    redact_keys: list[str] = Field(default_factory=list)


# ---------- Top-level Config (composed; loaded from YAML + env overrides) ----------


class Config(BaseSettings):
    """Composed runtime configuration.

    Loaded from config.yaml; env vars OVERRIDE yaml values per D-13
    (env_nested_delimiter='__'). Example: ``BROKER__PAPER_PORT=9999`` overrides
    config.broker.paper_port even if config.yaml says 7497.

    The yaml path is supplied at construction time by ``load_config()`` via the
    ``model_config['yaml_file']`` slot, which ``YamlConfigSettingsSource`` reads.
    Source priority (highest wins):
      init kwargs > env vars > YAML file > file secrets.
    """

    data: DataConfig
    broker: BrokerConfig
    risk: RiskConfig
    portfolio: PortfolioConfig
    anthropic: AnthropicConfig
    logging: LoggingConfig

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
        yaml_file=None,  # set at runtime by load_config()
        yaml_file_encoding="utf-8",
        # No env_file — secrets are isolated on the Secrets class.
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Inject YamlConfigSettingsSource so env vars override YAML (D-13).

        Default order is (init, env, dotenv, file_secrets) — first source wins.
        We append YAML AFTER env_settings so env-var overrides take priority,
        and use deep_merge=True so nested env keys (e.g. BROKER__PAPER_PORT)
        merge into rather than replace the YAML's broker block.
        """
        yaml_source = YamlConfigSettingsSource(settings_cls, deep_merge=True)
        return (init_settings, env_settings, dotenv_settings, yaml_source, file_secret_settings)


# ---------- Secrets (isolated; loads ONLY from .env) ----------


class Secrets(BaseSettings):
    """Secrets — loaded ONLY from .env per CONTEXT D-14.

    NEVER references config.yaml. NEVER inherits a YAML loader.
    Putting these fields in config.yaml has zero effect on this class.
    """

    anthropic_api_key: str
    ibkr_username: str = ""  # may be empty for paper-only operators
    ibkr_password: str = ""
    sec_user_agent: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # NO yaml loader. NO env_nested_delimiter — secrets are flat.
    )


# ---------- Public load entry point ----------


def load_config(
    yaml_path: Path | str = Path("config.yaml"),
    env_path: Path | str | None = None,
) -> tuple[Config, Secrets]:
    """Load config.yaml + .env, validate both, return ``(config, secrets)``.

    Source priority (highest first):
      env vars (with ``__`` nesting) > YAML file > sub-config defaults.

    Args:
        yaml_path: path to config.yaml (defaults to repo-root config.yaml).
        env_path: optional override for the .env path used by Secrets. When
            ``None``, Secrets uses its declared default (``.env``) plus process env.

    Returns:
        Tuple of (Config, Secrets), both fully validated.

    Raises:
        FileNotFoundError: if ``yaml_path`` does not exist.
        yaml.YAMLError: if YAML is syntactically malformed (PyYAML safe_load).
        pydantic.ValidationError: if config.yaml or .env fail schema validation.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"config not found at {yaml_path}; copy config.yaml.example to config.yaml"
        )

    # Eagerly run yaml.safe_load to surface syntax errors at load_config() boundary
    # (D-15: validation fires BEFORE any layer code runs). pydantic-settings'
    # YamlConfigSettingsSource will re-read the same file; this duplicated read is
    # cheap and gives us a clean YAMLError surface point. PyYAML safe_load only
    # (D-12, CLAUDE.md anti-recommendations: no ruamel.yaml).
    with yaml_path.open("r", encoding="utf-8") as f:
        yaml.safe_load(f)

    # Construct Config with yaml_file pointing at the runtime-supplied path.
    # YamlConfigSettingsSource reads model_config['yaml_file']; env vars then
    # override yaml values per settings_customise_sources priority.
    config = _build_config(str(yaml_path))

    # Secrets uses its own env_file; pass through env_path when caller customizes (tests).
    # The required fields (anthropic_api_key, sec_user_agent) are populated from .env /
    # env vars by pydantic-settings — mypy can't see that, hence the call-arg ignore.
    secrets = (
        Secrets(_env_file=str(env_path))  # type: ignore[call-arg]
        if env_path is not None
        else Secrets()  # type: ignore[call-arg]
    )

    return config, secrets


def _build_config(yaml_file: str) -> Config:
    """Instantiate Config with model_config['yaml_file'] set to ``yaml_file``.

    pydantic-settings reads ``model_config['yaml_file']`` at instantiation time
    via ``YamlConfigSettingsSource``. We mutate the class-level mapping just
    before instantiation and restore it afterwards so concurrent ``load_config``
    calls in tests don't interfere. Single-process daily-run cadence makes this
    safe; if we ever go multi-threaded, this needs a lock.
    """
    prev = Config.model_config.get("yaml_file")
    Config.model_config["yaml_file"] = yaml_file
    try:
        return Config()  # type: ignore[call-arg]
    finally:
        Config.model_config["yaml_file"] = prev


__all__ = [
    "AnthropicConfig",
    "BreakersConfig",
    "BrokerConfig",
    "Config",
    "DataConfig",
    "LoggingConfig",
    "PortfolioConfig",
    "RiskConfig",
    "Secrets",
    "load_config",
]
