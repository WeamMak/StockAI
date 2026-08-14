# Exact dev release validation

## Purpose

This T23 procedure validates the one Argo-reconciled dev release recorded in
`deploy/releases/dev.json`. It does not build, retag, push, deploy, or replace
an image. The smoke result is appended to that release; its `releaseId`, four
image digests, provenance, source, Scout result, build identities, and creation
time remain immutable.

Generated evidence stays local under `reports/smoke/`. The committed release
record contains only its SHA-256 digest and bounded identifiers—never cookies,
credentials, model content, or raw business data.

## Run the walking-skeleton smoke

1. Confirm Argo reports `Synced` and `Healthy` for `stockai-dev` and that its
   revision contains the exact four image digests in the release manifest.
2. Open <https://app.dev.stockai.fursa.click/auth/login> and authenticate with
   an existing fictional officer or manager through Cognito.
3. In the browser's developer tools, copy the fresh `stockai_session` and
   matching `stockai_csrf` cookie values. Do not paste either value into a
   command line, file, issue, log, or chat.
4. Run `make smoke-dev`. If the variables are absent, the script reads both
   values silently. It unsets them immediately after the live test process.

The smoke must pass all of these checks before evidence is recorded:

- the public entry point uses HTTPS and serves the compiled React frontend;
- `/api/v1/session` accepts the real Cognito-created opaque session;
- frontend-equivalent create and list/detail polling completes through
  FastAPI, compiled LangGraph, Bedrock GPT-OSS, authenticated Streamable HTTP
  MCP, and the real seeded Odoo read;
- the resulting case is present and successful in the dev DynamoDB table;
- Argo is healthy and the four deployed images equal the manifest digests;
- Prometheus reports successful LLM, agent-side MCP, server-side MCP, and Odoo
  calls;
- Loki contains the matching scan identifier, the queried records do not
  contain the session/CSRF/Grafana credentials or cookie/header names, and the
  dev Loki S3 prefix contains retained objects.

On success, `scripts/smoke/dev.sh` hashes the sanitized evidence file and calls
`scripts.release.record_validation`. The recorder appends one attempt bound to
the release ID, exact image map, Argo revision, smoke-run ID, UTC timestamp,
result, and evidence digest. Failed attempts may be recorded explicitly, but a
passed release cannot be downgraded, rerun under a different Argo revision, or
have its passed evidence replaced.

Commit only the release/status evidence with the message marker
`[record dev-validation]`. The dev image workflow recognizes that bounded
validation-only marker and does not rebuild the accepted T22 images.

## Representative worker-replacement drill

Run this section once for the accepted T23 release. Stop immediately before
the termination command and obtain explicit user approval for that exact dev
worker instance ID.

Before termination, record only sanitized evidence:

1. the dev ASG desired capacity and current instance/private-DNS identity;
2. the Kubernetes Node's dev label, dev `NoSchedule` taint, Ready condition,
   and dev worker IAM instance profile;
3. the three dev PVC/PV/VolumeAttachment bindings and EC2 attachment state;
4. one fictional seeded Odoo product identity and PostgreSQL row count;
5. one Prometheus sample timestamp and the four provisioned Grafana dashboard
   UIDs;
6. application, observability, ALB target, and Argo health.

After approval, use only the existing ASG replacement mechanism from
`docs/runbooks/worker-termination.md`:

```bash
aws autoscaling terminate-instance-in-auto-scaling-group \
  --region us-east-1 \
  --instance-id "<approved-dev-worker-instance-id>" \
  --no-should-decrement-desired-capacity
```

Acceptance requires all of the following:

- the lifecycle Lambda records `outcome=clean` with bounded heartbeats and no
  secret content;
- the old Node disappears and the ASG returns to one healthy `InService`
  replacement;
- the replacement independently joins Ready with exactly the dev label, dev
  taint, dev subnet/AZ, and dev worker role;
- all three retained EBS volumes reattach without forced detach, and the Odoo,
  PostgreSQL, and Prometheus workloads recover;
- the recorded fictional Odoo/PostgreSQL data and Prometheus history remain;
- the frontend, API, MCP, Odoo, observability workloads, ALB target, and Argo
  application recover healthy;
- Grafana recreates the same four Git-provisioned dashboard UIDs despite its
  disposable runtime storage.

Store the sanitized checklist under `reports/smoke/` and record only the date,
release ID, old/new infrastructure identities, successful outcome, and
evidence digest in `docs/implementation-status.md`. Future releases rerun the
walking-skeleton smoke but reuse this drill unless worker or retained-storage
behavior changes or the evidence becomes invalid.

## Failure handling

Do not append passed evidence after any failed check. Preserve the sanitized
local report, diagnose the existing component through its runbook, and append
an explicit failed attempt only when that failure record is useful. Never
invent a passing digest, alter an earlier attempt, force-detach EBS, manually
repair desired state, or continue into T24.
