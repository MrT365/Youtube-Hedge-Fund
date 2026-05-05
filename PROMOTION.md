# Paper-to-Live Promotion Ceremony (AUDIT-03)

Meridian Capital Partners v1 is paper-only until an operator-signed promotion
record exists at `output/promotion_record.json` and `MERIDIAN_LIVE_OK=1` is set.

Promotion requires all named numeric thresholds below:

1. Paper trading history: at least 8 weeks, defined as 40 trading days minimum.
2. Max drawdown during paper period: strictly less than 15%.
3. Slippage quality: 30-day rolling average slippage within 50 bps of model estimate.
4. Factor IC stability: Spearman IC greater than 0.03 on at least 4 of 8 factors over trailing 20 days.
5. Audit discipline: zero veto-bypass events in audit log.
6. Operations discipline: zero stale-cache halts in last 30 days.
7. Live broker account number configured in `config.yaml`.
8. All criteria confirmed and operator-signed in the promotion record.

Run:

```bash
uv run python scripts/promote_to_live.py --account-number DU1234567
```

If all checks pass, the script prints `ALL CRITERIA MET — PROMOTION ELIGIBLE`,
requires the operator to type the live account number, and writes
`output/promotion_record.json` containing timestamp, git SHA, criteria values,
account-number last four digits, and `operator_confirmed: true`.
