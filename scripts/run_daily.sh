#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${ROOT}/cache"
DB_PATH="${CACHE_DIR}/ls_equity_fund.db"
RUN_ID="daily-$(date -u +%Y%m%dT%H%M%SZ)"
START_TS="$(date +%s)"
mkdir -p "${CACHE_DIR}" "${ROOT}/output" "${HOME}/Library/Logs/meridian"

echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) run_id=${RUN_ID}" > "${CACHE_DIR}/last_run_started.txt"

record_start() {
  if command -v sqlite3 >/dev/null 2>&1 && [[ -f "${DB_PATH}" ]]; then
    sqlite3 "${DB_PATH}" "INSERT OR REPLACE INTO runs (run_id,start_ts,end_ts,status,error) VALUES ('${RUN_ID}',${START_TS},NULL,'RUNNING',NULL);"
  fi
}

record_end() {
  local exit_code="$1"
  local status="OK"
  local error=""
  if [[ "${exit_code}" != "0" ]]; then
    status="FAILED"
    error="daily run exited ${exit_code}"
  fi
  local end_ts
  end_ts="$(date +%s)"
  if command -v sqlite3 >/dev/null 2>&1 && [[ -f "${DB_PATH}" ]]; then
    sqlite3 "${DB_PATH}" "UPDATE runs SET end_ts=${end_ts}, status='${status}', error='${error}' WHERE run_id='${RUN_ID}'; UPDATE heartbeat SET last_run_id='${RUN_ID}', last_heartbeat_ts=${end_ts}, last_status='${status}' WHERE id=1;"
  fi
  echo "completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${exit_code}" > "${CACHE_DIR}/last_run_completed.txt"
  if [[ "${exit_code}" != "0" ]]; then
    osascript -e "display notification \"Meridian daily run failed (${exit_code})\" with title \"Meridian\"" >/dev/null 2>&1 || true
  fi
}

cd "${ROOT}" || exit 1
record_start

meridian run-data --no-filings --no-13f
rc=$?
if [[ "${rc}" == "0" ]]; then meridian run-scoring; rc=$?; fi
if [[ "${rc}" == "0" ]]; then meridian run-portfolio --whatif --optimize-method conviction; rc=$?; fi
if [[ "${rc}" == "0" ]]; then meridian run-reporting; rc=$?; fi

record_end "${rc}"
exit "${rc}"
