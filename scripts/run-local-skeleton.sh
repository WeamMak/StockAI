#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_root="${project_root}/frontend"
frontend_port="${PROCUREMENT_FRONTEND_PORT:-5173}"
api_pid=""
mcp_pid=""

cleanup() {
  trap - EXIT INT TERM
  for process_id in "${api_pid}" "${mcp_pid}"; do
    if [[ -n "${process_id}" ]] && kill -0 "${process_id}" 2>/dev/null; then
      kill -TERM "${process_id}" 2>/dev/null || true
    fi
  done
  for process_id in "${api_pid}" "${mcp_pid}"; do
    if [[ -n "${process_id}" ]]; then
      wait "${process_id}" 2>/dev/null || true
    fi
  done
}

wait_for_url() {
  local service_name="$1"
  local url="$2"
  local process_id="$3"

  for _ in {1..100}; do
    if ! kill -0 "${process_id}" 2>/dev/null; then
      echo "${service_name} stopped before becoming ready." >&2
      return 1
    fi
    if curl --fail --silent --show-error "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  echo "${service_name} did not become ready at ${url}." >&2
  return 1
}

if [[ ! -d "${frontend_root}/node_modules" ]]; then
  echo "Frontend dependencies are missing. Run: cd frontend && npm ci" >&2
  exit 1
fi

command -v curl >/dev/null 2>&1 || {
  echo "curl is required to check local service readiness." >&2
  exit 1
}

trap cleanup EXIT INT TERM

export PROCUREMENT_ENVIRONMENT="dev"
export PROCUREMENT_LOG_LEVEL="INFO"
export PROCUREMENT_MCP_URL="http://127.0.0.1:9000/mcp"
export PROCUREMENT_MCP_TOKEN
PROCUREMENT_MCP_TOKEN="$(
  cd "${project_root}"
  uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
)"
export PROCUREMENT_LOCAL_ERP_MODE="success"
export PROCUREMENT_MCP_READ_TIMEOUT_SECONDS="1"
export PROCUREMENT_MCP_MAX_RETRIES="2"
export PROCUREMENT_MCP_RETRY_DELAY_SECONDS="0.01"

cd "${project_root}"
uv run uvicorn procurement.bootstrap.mcp:app \
  --host 127.0.0.1 \
  --port 9000 \
  --log-level warning \
  --no-access-log &
mcp_pid="$!"
wait_for_url "Procurement MCP" "http://127.0.0.1:9000/metrics" "${mcp_pid}"

uv run uvicorn procurement.bootstrap.api:app \
  --host 127.0.0.1 \
  --port 8000 \
  --log-level warning \
  --no-access-log &
api_pid="$!"
wait_for_url "Procurement API" "http://127.0.0.1:8000/health/live" "${api_pid}"

echo "Local walking skeleton is ready at http://127.0.0.1:${frontend_port}"
echo "Press Ctrl+C to stop the frontend, API, and MCP processes."
npm --prefix "${frontend_root}" run dev -- \
  --host 127.0.0.1 \
  --port "${frontend_port}" \
  --strictPort
