---
phase: 00-foundation
plan: 02
subsystem: config
tags: [config, secrets, pydantic-settings, yaml, env, validation]
requires:
  - .planning/phases/00-foundation/00-01-SUMMARY.md  # config.yaml.example, .env.example, pyproject.toml
provides:
  - "from ls_equity_fund.config import Config, Secrets, load_config"
  - "Six sub-config classes: DataConfig, BrokerConfig, RiskConfig, PortfolioConfig, AnthropicConfig, LoggingConfig (+ nested BreakersConfig)"
  - "load_config(yaml_path, env_path) -> tuple[Config, Secrets] with validation-at-boot"
affects:
  - "Every layer in Phase 1+ that does `from ls_equity_fund.config import config` reads its slice through these sub-configs"
  - "Tests can stub yaml + env paths via fresh_yaml_path / fresh_env_path fixtures"
tech-stack:
  added:
    - "types-PyYAML (dev) — required for mypy to type-check yaml.safe_load"
  patterns:
    - "settings_customise_sources to inject YamlConfigSettingsSource after env_settings — env > YAML priority (D-13)"
    - "Separate Secrets BaseSettings class with env_file='.env' and NO YAML loader (D-14)"
    - "Field-level Field(ge=, le=, gt=) bounds catch typo'd ratios (e.g. 5.0 instead of 0.05) at boot (D-15)"
key-files:
  created:
    - "src/ls_equity_fund/__init__.py"
    - "src/ls_equity_fund/config.py"
    - "tests/__init__.py"
    - "tests/unit/__init__.py"
    - "tests/conftest.py"
    - "tests/unit/test_config.py"
  modified:
    - "pyproject.toml (added types-PyYAML to dev extras — Rule 3 dev-tooling fix)"
decisions:
  - "Composed Config with six sub-configs (D-11)"
  - "config.yaml at repo root, PyYAML safe_load only (D-12)"
  - "env_nested_delimiter='__' on Config; env vars override YAML via settings_customise_sources (D-13)"
  - "Secrets is yaml-blind — separate BaseSettings, env_file='.env', no yaml_file path (D-14)"
  - "Validation at load_config() boundary; bad config raises pydantic.ValidationError BEFORE any layer runs (D-15)"
metrics:
  tasks: 2
  files_created: 6
  files_modified: 1
  test_count: 9
  test_runtime_seconds: 0.09
  duration: "~25 min"
  completed: 2026-05-04
---

# Phase 0 Plan 02: Composed Config + isolated Secrets — Summary

**One-liner:** Composed pydantic-settings `Config` (six sub-configs: data, broker, risk, portfolio, anthropic, logging) + a yaml-blind `Secrets` BaseSettings that loads only from `.env`, with `load_config()` validating both at boot via `pydantic.ValidationError` before any downstream layer runs.

## What shipped

### Public API surface

```python
from ls_equity_fund.config import (
    Config, Secrets,
    DataConfig, BrokerConfig, RiskConfig, BreakersConfig,
    PortfolioConfig, AnthropicConfig, LoggingConfig,
    load_config,
)

config, secrets = load_config(
    yaml_path="config.yaml",   # optional — defaults to repo-root config.yaml
    env_path=".env",           # optional — Secrets uses its declared default if None
)

# Layer-slice access pattern downstream layers will use:
config.broker.paper_port      # 7497
config.portfolio.num_longs    # 20
config.risk.breakers.daily_loss_pct  # -1.5
secrets.anthropic_api_key     # from .env, NEVER from yaml
```

### Sub-config field counts

| Sub-config | Fields | Notes |
|---|---|---|
| DataConfig | 5 | provider, universe_mode (Literal), lookback_years (1..20), benchmark, cache_dir |
| BrokerConfig | 5 | paper_host, paper_port (1..65535), live_port, client_id (0..999), mode (Literal) |
| RiskConfig | 3 | factor_model_window_days (20..500), veto_checks (list), breakers (nested BreakersConfig) |
| BreakersConfig | 5 | daily_loss_pct, daily_loss_pct_hard, weekly_loss_pct, drawdown_pct, single_position_pct (all required, no defaults — fail loudly if missing) |
| PortfolioConfig | 11 | optimizer (Literal), num_longs/num_shorts (1..200), bounded ratios on max_position_pct / max_sector_pct / gross_target / max_beta / turnover_budget / mvo_risk_aversion |
| AnthropicConfig | 5 | model, cost_ceiling_usd (>0), cache_ttl_days (≥1), prompt_caching, max_candidates (1..500) |
| LoggingConfig | 4 | level (Literal), log_dir, json_renderer_when_non_tty, redact_keys |
| Secrets | 4 | anthropic_api_key (required), ibkr_username/password (optional empty default), sec_user_agent (required) |

### Decisions covered

| Decision | Where it lives in this plan |
|---|---|
| **D-11** composed pydantic models, six sub-configs | Top-level `Config(BaseSettings)` declares each sub-config as a typed field; sub-configs are plain `BaseModel` for cheap nesting |
| **D-12** config.yaml at repo root, PyYAML safe_load only | `load_config()` calls `yaml.safe_load`; `YamlConfigSettingsSource` (which pydantic-settings provides) also delegates to safe_load. No ruamel.yaml. |
| **D-13** `env_nested_delimiter='__'`; env overrides YAML | `Config.model_config['env_nested_delimiter'] == '__'`; `settings_customise_sources` returns `(init, env, dotenv, yaml, file_secrets)` so env outranks yaml. Verified by `test_env_nested_delimiter_overrides_yaml`. |
| **D-14** Secrets is yaml-blind | `Secrets.model_config` declares `env_file='.env'` only — no `yaml_file` Path is set. Verified by `test_secrets_does_not_load_from_yaml` (puts `anthropic_api_key: sk-ant-LEAKED-IN-YAML` in yaml; assertion confirms Secrets reads `.env` value, not yaml's). |
| **D-15** validation at boot | `load_config()` runs `yaml.safe_load` (raises `yaml.YAMLError` on syntactically broken yaml) then `Config()` (raises `pydantic.ValidationError` on missing fields, wrong types, invalid Literals). Verified by tests 3, 4, 5. |

### Test coverage (9 tests, runtime 0.09s)

| # | Test | Behavior covered |
|---|---|---|
| 1 | `test_imports_succeed` | Module imports without YAML/env present (no module-level loading) |
| 2 | `test_load_config_from_example` | Round-trip: shipped config.yaml.example produces fully-populated Config; secrets populated from .env |
| 3 | `test_missing_required_field_raises` | YAML missing `risk.breakers.daily_loss_pct` raises ValidationError matching "daily_loss_pct" |
| 4 | `test_wrong_type_raises` | `paper_port: not_an_int` raises ValidationError |
| 5 | `test_invalid_literal_raises` | `optimizer: lasso` (not in Literal["conviction","mvo"]) raises matching "optimizer" |
| 6 | `test_env_nested_delimiter_overrides_yaml` | `BROKER__PAPER_PORT=9999` env override wins over yaml's 7497 (D-13) |
| 7 | `test_secrets_loads_from_env` | All four Secrets fields populated from .env file content |
| 8 | `test_secrets_does_not_load_from_yaml` | YAML containing `anthropic_api_key: sk-ant-LEAKED-IN-YAML` does NOT pollute Secrets — D-14 isolation property |
| 9 | `test_secrets_missing_required_raises` | Empty .env + cleared env vars → Secrets raises ValidationError on `anthropic_api_key` (no default) |

### conftest fixtures

- **`fresh_yaml_path`** — copies `config.yaml.example` into `tmp_path/config.yaml`; tests mutate the copy.
- **`fresh_env_path`** — writes a known-good `.env` (sk-ant-test-key-do-not-use, test_user, test_pass, Meridian Capital Partners test@example.com) into `tmp_path/.env`.
- **`isolate_env` (autouse)** — strips `BROKER__*`, `DATA__*`, `RISK__*`, `PORTFOLIO__*`, `ANTHROPIC__*`, `LOGGING__*` and the four secret env names between tests so outer-shell leakage doesn't poison isolation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Env vars did NOT override YAML in initial implementation**
- **Found during:** Task 2 (test_env_nested_delimiter_overrides_yaml failed)
- **Issue:** Original `load_config()` did `Config(**raw)` — passing yaml content as init kwargs. pydantic-settings ranks init kwargs ABOVE env vars by default, so `BROKER__PAPER_PORT=9999` was silently outranked by yaml's 7497. This violates D-13 ("env vars override per env_nested_delimiter").
- **Fix:** Override `Config.settings_customise_sources` to register `YamlConfigSettingsSource` AFTER `env_settings` in the source tuple. Added `_build_config()` helper that sets `model_config['yaml_file']` to the runtime path before instantiation and restores it after. Now source priority is: init kwargs > env vars > YAML > file secrets — matching D-13.
- **Files modified:** `src/ls_equity_fund/config.py`
- **Commit:** `f9349e0`

**2. [Rule 3 - Blocking] mypy missing types-PyYAML stubs**
- **Found during:** Task 1 verification
- **Issue:** `uv run mypy src/ls_equity_fund/config.py` reported `Library stubs not installed for "yaml"`. Plan 01's pyproject.toml dev extras did not ship type stubs. Plan success criterion requires mypy to pass.
- **Fix:** Added `types-PyYAML>=6.0,<7.0` to `[project.optional-dependencies].dev`.
- **Files modified:** `pyproject.toml`, `uv.lock`
- **Commit:** `3d0920e`

**3. [Rule 1 - Type] mypy could not see env-populated required fields on Secrets()**
- **Found during:** Task 1 verification
- **Issue:** `Secrets()` and `Secrets(_env_file=...)` calls produce `Missing named argument "anthropic_api_key"` because mypy can't infer that pydantic-settings populates required fields from .env / env vars at runtime.
- **Fix:** `# type: ignore[call-arg]` on the two instantiations in `load_config()`, with a comment explaining why mypy can't see what pydantic-settings does at runtime.
- **Files modified:** `src/ls_equity_fund/config.py`
- **Commit:** `3d0920e`

### Notes on plan instructions adjusted

- Plan's `<acceptance_criteria>` for Task 1 said "Secrets has NO yaml-related model_config keys (`yaml_file` not present)". This is technically untrue — pydantic-settings 2.14's `SettingsConfigDict` auto-populates `yaml_file: None` (and `yaml_file_encoding`, `yaml_config_section`, etc.) on every BaseSettings subclass. The semantic intent — "Secrets cannot load from yaml" — is what matters and is enforced by (a) NOT setting a yaml_file path on Secrets, (b) NOT overriding `settings_customise_sources` on Secrets to add a yaml source, and (c) test_secrets_does_not_load_from_yaml proving the property by construction. No code change needed.

- Plan's instruction to commit "Task 1" then "Task 2" was followed structurally, but Task 1's commit shipped a config.py whose env-priority bug was discovered during Task 2's tests. Rather than amend Task 1's commit (forbidden per executor protocol), Task 2's commit explicitly notes the inline fix as a `[Rule 1 - Bug]` deviation. Two-commit history preserved.

## CLAUDE.md compliance

- PyYAML 6.0.3 used via `safe_load` only — no ruamel.yaml (CLAUDE.md anti-recommendation).
- pydantic 2.13 + pydantic-settings 2.14 — matches CLAUDE.md table.
- types-PyYAML added (CLAUDE.md doesn't pin it; chose `>=6.0,<7.0` to track PyYAML major).
- No emojis in code or docs (per User instructions noted in role context).
- All edits routed through GSD plan execution (CLAUDE.md GSD Workflow Enforcement).

## Self-Check

Files exist:
- `src/ls_equity_fund/__init__.py` — FOUND
- `src/ls_equity_fund/config.py` — FOUND (264 lines, ≥120 minimum)
- `tests/__init__.py` — FOUND
- `tests/unit/__init__.py` — FOUND
- `tests/conftest.py` — FOUND
- `tests/unit/test_config.py` — FOUND (200 lines, ≥80 minimum)

Commits exist:
- `3d0920e` feat(00-02): add composed Config + isolated Secrets module — FOUND
- `f9349e0` test(00-02): add 9-behavior config test suite + fix env-over-yaml priority — FOUND

Verification passes:
- `uv run pytest tests/unit/test_config.py -v` → 9 passed in 0.08s
- `uv run mypy src/ls_equity_fund/config.py` → Success: no issues found in 1 source file

## Self-Check: PASSED
