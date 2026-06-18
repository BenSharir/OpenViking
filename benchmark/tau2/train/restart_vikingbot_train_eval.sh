#!/usr/bin/env bash
set -euo pipefail

# Restart the OpenViking bot server and tau2 rollout service, wait until both
# are healthy, then start tau2 vikingbot batch train/eval.
#
# Default training args match the common vikingbot run:
#   --commit-concurrency 100 --epochs 2 --trials 8 --skip-final-eval
# Pass any arguments to override/extend the batch train/eval invocation.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAU2_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TAU2_DIR}/../.." && pwd)"

OPENVIKING_PORT="${OPENVIKING_PORT:-1933}"
OPENVIKING_BOT_PORT="${OPENVIKING_BOT_PORT:-18790}"
TAU2_SERVICE_HOST="${TAU2_SERVICE_HOST:-127.0.0.1}"
TAU2_SERVICE_PORT="${TAU2_SERVICE_PORT:-1944}"
TAU2_ROLLOUT_BACKEND="${TAU2_ROLLOUT_BACKEND:-vikingbot}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-180}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/result/tau2/train/service_logs}"

OPENVIKING_LOG="${LOG_DIR}/openviking-server.log"
TAU2_SERVICE_LOG="${LOG_DIR}/tau2-service.log"

mkdir -p "${LOG_DIR}"

log() {
  printf '[restart-vikingbot-train] %s\n' "$*"
}

fail() {
  printf '[restart-vikingbot-train] ERROR: %s\n' "$*" >&2
  exit 1
}

wait_for_http_json_ok() {
  local name="$1"
  local url="$2"
  local required_pattern="$3"
  local log_file="$4"
  local deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
  local response=""

  log "waiting for ${name}: ${url}"
  while (( SECONDS < deadline )); do
    response="$(curl -fsS "${url}" 2>/dev/null || true)"
    if [[ -n "${response}" && "${response//[[:space:]]/}" == *"${required_pattern}"* ]]; then
      log "✓ ${name} is ready"
      return 0
    fi
    sleep 2
  done

  log "last ${name} response: ${response:-<empty>}"
  if [[ -f "${log_file}" ]]; then
    log "recent ${name} logs:"
    tail -80 "${log_file}" >&2 || true
  fi
  fail "${name} did not become ready within ${WAIT_TIMEOUT_SECONDS}s"
}

start_openviking_server() {
  log "restarting OpenViking server on port ${OPENVIKING_PORT}, bot port ${OPENVIKING_BOT_PORT}"
  log "OpenViking log: ${OPENVIKING_LOG}"
  : > "${OPENVIKING_LOG}"

  (
    cd "${REPO_ROOT}"
    exec bot/scripts/restart_openviking_server.sh \
      --port "${OPENVIKING_PORT}" \
      --bot-port "${OPENVIKING_BOT_PORT}"
  ) >"${OPENVIKING_LOG}" 2>&1 &

  echo "$!" > "${LOG_DIR}/openviking-server.pid"
  log "OpenViking restart wrapper pid: $(cat "${LOG_DIR}/openviking-server.pid")"

  wait_for_http_json_ok \
    "OpenViking bot API" \
    "http://127.0.0.1:${OPENVIKING_PORT}/bot/v1/health" \
    '"status":"healthy"' \
    "${OPENVIKING_LOG}"
}

start_tau2_service() {
  log "restarting tau2 service on ${TAU2_SERVICE_HOST}:${TAU2_SERVICE_PORT} backend=${TAU2_ROLLOUT_BACKEND}"
  log "tau2 service log: ${TAU2_SERVICE_LOG}"
  : > "${TAU2_SERVICE_LOG}"

  (
    cd "${REPO_ROOT}"
    exec benchmark/tau2/train/run_service.sh \
      --host "${TAU2_SERVICE_HOST}" \
      --port "${TAU2_SERVICE_PORT}" \
      --rollout-backend "${TAU2_ROLLOUT_BACKEND}"
  ) >"${TAU2_SERVICE_LOG}" 2>&1 &

  echo "$!" > "${LOG_DIR}/tau2-service.pid"
  log "tau2 service pid: $(cat "${LOG_DIR}/tau2-service.pid")"

  wait_for_http_json_ok \
    "tau2 rollout service" \
    "http://${TAU2_SERVICE_HOST}:${TAU2_SERVICE_PORT}/health" \
    '"status":"ok"' \
    "${TAU2_SERVICE_LOG}"
}

run_train_eval() {
  local -a train_args=("$@")
  if [[ ${#train_args[@]} -eq 0 ]]; then
    train_args=(
      --commit-concurrency 100
      --epochs 2
      --trials 8
      --skip-final-eval
    )
  fi

  export BENCHMARK_SERVICE_URL="http://${TAU2_SERVICE_HOST}:${TAU2_SERVICE_PORT}"
  log "starting batch train/eval with BENCHMARK_SERVICE_URL=${BENCHMARK_SERVICE_URL}"
  log "command: benchmark/tau2/train/run_batch_train_eval.sh ${train_args[*]}"
  cd "${REPO_ROOT}"
  exec benchmark/tau2/train/run_batch_train_eval.sh "${train_args[@]}"
}

main() {
  start_openviking_server
  start_tau2_service
  run_train_eval "$@"
}

main "$@"
