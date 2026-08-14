#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_root"

if test -z "${STOCKAI_PROD_SESSION_TOKEN:-}"; then
  read -r -s -p "Fresh prod stockai_session value: " STOCKAI_PROD_SESSION_TOKEN
  echo
  export STOCKAI_PROD_SESSION_TOKEN
fi
if test -z "${STOCKAI_PROD_CSRF_TOKEN:-}"; then
  read -r -s -p "Matching prod stockai_csrf value: " STOCKAI_PROD_CSRF_TOKEN
  echo
  export STOCKAI_PROD_CSRF_TOKEN
fi
: "${STOCKAI_PROD_SESSION_TOKEN:?A fresh prod Cognito-backed session is required}"
: "${STOCKAI_PROD_CSRF_TOKEN:?The matching prod CSRF value is required}"

export STOCKAI_SMOKE_RUN_ID="${STOCKAI_SMOKE_RUN_ID:-prod-smoke-$(date --utc +%Y%m%dT%H%M%SZ)}"
export STOCKAI_SMOKE_EVIDENCE="${STOCKAI_SMOKE_EVIDENCE:-$project_root/reports/smoke/${STOCKAI_SMOKE_RUN_ID}.json}"
export STOCKAI_RUN_PROD_SMOKE=1
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"

uv run pytest -q tests/smoke/test_prod_skeleton.py
unset STOCKAI_PROD_SESSION_TOKEN STOCKAI_PROD_CSRF_TOKEN

evidence_digest="sha256:$(sha256sum "$STOCKAI_SMOKE_EVIDENCE" | cut -d' ' -f1)"
echo "T24 prod smoke passed; sanitized evidence: $STOCKAI_SMOKE_EVIDENCE"
echo "T24 prod smoke evidence digest: $evidence_digest"
