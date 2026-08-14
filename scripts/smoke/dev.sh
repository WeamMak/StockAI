#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_root"

if test -z "${STOCKAI_DEV_SESSION_TOKEN:-}"; then
  read -r -s -p "Fresh stockai_session value: " STOCKAI_DEV_SESSION_TOKEN
  echo
  export STOCKAI_DEV_SESSION_TOKEN
fi
if test -z "${STOCKAI_DEV_CSRF_TOKEN:-}"; then
  read -r -s -p "Matching stockai_csrf value: " STOCKAI_DEV_CSRF_TOKEN
  echo
  export STOCKAI_DEV_CSRF_TOKEN
fi
: "${STOCKAI_DEV_SESSION_TOKEN:?A fresh Cognito-backed session is required}"
: "${STOCKAI_DEV_CSRF_TOKEN:?The matching CSRF value is required}"

export STOCKAI_SMOKE_RUN_ID="${STOCKAI_SMOKE_RUN_ID:-dev-smoke-$(date --utc +%Y%m%dT%H%M%SZ)}"
export STOCKAI_SMOKE_EVIDENCE="${STOCKAI_SMOKE_EVIDENCE:-$project_root/reports/smoke/${STOCKAI_SMOKE_RUN_ID}.json}"
export STOCKAI_RUN_DEV_SMOKE=1
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"

uv run pytest -q tests/smoke/test_dev_skeleton.py
unset STOCKAI_DEV_SESSION_TOKEN STOCKAI_DEV_CSRF_TOKEN

evidence_digest="sha256:$(sha256sum "$STOCKAI_SMOKE_EVIDENCE" | cut -d' ' -f1)"
release_id="$(uv run python -c 'import json,os; print(json.load(open(os.environ["STOCKAI_SMOKE_EVIDENCE"], encoding="utf-8"))["releaseId"])')"
argo_revision="$(uv run python -c 'import json,os; print(json.load(open(os.environ["STOCKAI_SMOKE_EVIDENCE"], encoding="utf-8"))["argoRevision"])')"
timestamp="$(uv run python -c 'import json,os; print(json.load(open(os.environ["STOCKAI_SMOKE_EVIDENCE"], encoding="utf-8"))["completedAt"])')"

uv run python -m scripts.release.record_validation deploy/releases/dev.json \
  --release-id "$release_id" \
  --argo-revision "$argo_revision" \
  --smoke-run-id "$STOCKAI_SMOKE_RUN_ID" \
  --timestamp "$timestamp" \
  --result passed \
  --evidence-digest "$evidence_digest"

echo "T23 dev smoke passed; sanitized evidence: $STOCKAI_SMOKE_EVIDENCE"
