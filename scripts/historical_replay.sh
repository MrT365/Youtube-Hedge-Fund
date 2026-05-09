#!/usr/bin/env bash
#
# Phase A historical replay — populate factor_scores_parent for the trailing
# N years of weekdays so `meridian compute-factor-ic` can compute a real
# 3-year cross-sectional Information Coefficient per factor.
#
# Mechanism: meridian run-scoring accepts --asof <YYYY-MM-DD>. We call it once
# per weekday in the lookback window. Scoring is purely a SQL/pandas operation
# against L1 tables already in the DB — no network calls, idempotent (INSERT
# OR REPLACE on the (ticker, score_date, factor) PK).
#
# Skips Anthropic — Phase A is testing quant factor IC, not the 60/40 blend.
# That means the `combined` parent score is the equal-weighted mean of the
# 8 base factors (Phase 2 logic), not the v2 60% quant + 40% Claude blend.
#
# Usage:
#   bash scripts/historical_replay.sh                # default 3y
#   YEARS=1 bash scripts/historical_replay.sh        # 1y window
#   START=2024-01-01 END=2024-12-31 bash scripts/historical_replay.sh
#
# Estimate: ~750 weekdays in 3y. Each run-scoring call is ~1 sec on this DB.
# Total runtime: 12-20 minutes wall-clock.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

YEARS="${YEARS:-3}"
END="${END:-$(date -u +%Y-%m-%d)}"
if [[ -n "${START:-}" ]]; then
  start="${START}"
else
  # macOS BSD date: -v is portable. -v-3y means "minus 3 years".
  start="$(date -u -v-${YEARS}y +%Y-%m-%d)"
fi

LOG_DIR="${ROOT}/logs/historical_replay"
mkdir -p "${LOG_DIR}"
PROGRESS="${LOG_DIR}/progress.txt"
ERRORS="${LOG_DIR}/errors.txt"
: > "${PROGRESS}"
: > "${ERRORS}"

echo "Phase A historical replay" | tee -a "${PROGRESS}"
echo "  start = ${start}" | tee -a "${PROGRESS}"
echo "  end   = ${END}" | tee -a "${PROGRESS}"
echo "  log   = ${LOG_DIR}" | tee -a "${PROGRESS}"
echo "" | tee -a "${PROGRESS}"

# Build the weekday list in Python (avoids macOS date arithmetic pain).
WEEKDAYS_FILE="$(mktemp)"
python3 - <<PY > "${WEEKDAYS_FILE}"
from datetime import date, timedelta
start = date.fromisoformat("${start}")
end = date.fromisoformat("${END}")
d = start
while d <= end:
    if d.weekday() < 5:
        print(d.isoformat())
    d += timedelta(days=1)
PY

n_total=$(wc -l < "${WEEKDAYS_FILE}" | tr -d ' ')
echo "  total weekdays = ${n_total}" | tee -a "${PROGRESS}"
echo "" | tee -a "${PROGRESS}"

start_ts=$(date +%s)
ok=0
failed=0
i=0
while IFS= read -r asof; do
  i=$((i + 1))
  # Quiet output: we only care about success/failure, not the per-run summary.
  if uv run meridian run-scoring --asof "${asof}" >/dev/null 2>>"${ERRORS}"; then
    ok=$((ok + 1))
  else
    failed=$((failed + 1))
    echo "FAILED ${asof}" >> "${ERRORS}"
  fi

  # Progress line every 25 runs.
  if (( i % 25 == 0 )); then
    elapsed=$(( $(date +%s) - start_ts ))
    pct=$(( i * 100 / n_total ))
    rate=$(awk -v i="$i" -v e="$elapsed" 'BEGIN { if (e > 0) printf "%.1f", i / e; else print "0.0" }')
    eta_sec=$(awk -v rem="$((n_total - i))" -v r="$rate" 'BEGIN { if (r > 0) printf "%d", rem / r; else print 0 }')
    echo "  [${i}/${n_total} ${pct}%]  asof=${asof}  ok=${ok}  failed=${failed}  rate=${rate}/s  eta=${eta_sec}s" | tee -a "${PROGRESS}"
  fi
done < "${WEEKDAYS_FILE}"
rm -f "${WEEKDAYS_FILE}"

elapsed=$(( $(date +%s) - start_ts ))
echo "" | tee -a "${PROGRESS}"
echo "DONE  total=${i}  ok=${ok}  failed=${failed}  elapsed=${elapsed}s" | tee -a "${PROGRESS}"
exit 0
