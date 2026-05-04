# Meridian Capital Partners — `ls_equity_fund`

Single-operator long/short US equity hedge fund system. Daily-cadence batch pipeline. Paper-first; live trading gated by `MERIDIAN_LIVE_OK=1` + AUDIT-03 promotion record.

## Quickstart

```bash
# 1. Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone + install
git clone <repo>
cd ls_equity_fund
uv sync

# 3. Configure
cp config.yaml.example config.yaml
cp .env.example .env
# edit .env to add ANTHROPIC_API_KEY, IBKR_*, SEC_USER_AGENT

# 4. Smoke check
uv run meridian doctor
```

## Status

Phase 0 (Foundation) — bootable empty system with seam interfaces and PaperBroker stub.

See `.planning/ROADMAP.md` for the 11-phase v1 plan.

## License

Proprietary. Single operator only. No telemetry, no external reporting.
