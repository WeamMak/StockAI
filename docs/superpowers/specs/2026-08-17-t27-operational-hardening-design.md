# T27 Operational Hardening Design

**Status:** Approved in conversation and reviewed on 2026-08-17.

## Purpose and scope

This amendment closes six operational gaps discovered while validating T27.
It preserves the existing application architecture and changes only shutdown
coordination, join-token readiness, scheduled workload recovery, metrics
collection, smoke assertions, and production smoke authentication.

The included issues are 1, 3, 5, 6, 7, and 8 from the reviewed incident list.
Automatic stale-node and `VolumeAttachment` cleanup after an uncoordinated
shutdown (issue 4), automatic startup, application behavior, and UI changes are
explicitly excluded.

## Selected design

### 1. Graceful scheduled worker shutdown

`[Project decision]` Terraform creates recurring scheduled actions for both
worker Auto Scaling groups at 15:45 and 23:45 in `Asia/Jerusalem`. Each action
sets minimum and desired capacity to zero while retaining maximum capacity
three. This starts shutdown 15 minutes before the staff cutoff.

The existing Auto Scaling termination lifecycle hook remains responsible for
cordoning, draining, and deleting each Kubernetes Node before instance
termination. This allows CSI detach to finish while the control plane is still
available. The staff action that later sets the same capacities to zero is
compatible and idempotent. The user continues to start the control plane and
restore worker capacity manually.

This does not guarantee recovery from an early, simultaneous, or failed staff
shutdown; that residual risk belongs to deferred issue 4.

### 3. Join token ready shortly after control-plane boot

`[Project decision]` The existing systemd timer runs the existing join-token
rotation service about one minute after boot instead of twelve hours after
boot, while retaining its periodic rotation. The service uses a small bounded
retry policy so temporary API-server unavailability during startup does not
leave workers without a current join command in Parameter Store.

No new token service or secret store is introduced.

### 5. Scheduled workload recovery

`[Project decision]` The completed Odoo bootstrap Job is retained instead of
being removed by a TTL. It therefore does not restart merely because workers
return; an intentional manifest or image change can still replace it through
the existing Argo CD behavior.

The daily scan CronJob receives a short `startingDeadlineSeconds` value so a
scan missed while the cluster was intentionally stopped is skipped rather than
started unexpectedly after recovery. Existing active deadlines, backoff
limits, and concurrency policy remain unchanged.

### 6. Per-pod Prometheus scraping

`[Project decision]` Add dedicated headless metrics Services for the agent API
and procurement MCP workloads. Prometheus uses DNS service discovery for those
Services and scrapes every ready pod endpoint directly. This avoids adding
Kubernetes API credentials or discovery RBAC to Prometheus and replaces the
current load-balanced Service targets that can repeatedly select only one pod.

Dashboards and alerts aggregate replica-labelled series where an application-
wide total is intended. Existing metric names and instrumentation remain
unchanged.

### 7. Current-run smoke metric proof

`[Project decision]` Before starting the scan, the smoke test records aggregate
raw counter totals for the required successful LLM, agent-MCP, MCP-tool, and
Odoo operations. After the scan, it waits until each counter is greater than
its recorded baseline and confirms the relevant Prometheus targets are up.

Missing pre-scan series are treated as zero. This replaces the timing-sensitive
`increase(...[10m])` assertions and proves the current smoke interaction
produced each signal.

### 8. Fresh production smoke authentication

`[Project decision]` Provision a dedicated Cognito user in the existing
`officer` group using the existing identity-bootstrap pattern. Its stable
username and password are stored only as protected GitHub production
environment secrets.

The production workflow performs a real headless browser login through the
existing Cognito authorization-code/PKCE flow, captures the short-lived session
and CSRF cookies in memory, masks them, and immediately runs the unchanged
public smoke path. Authentication screenshots, traces, cookies, and passwords
must not be uploaded or logged. Cookies are discarded when the job ends.

This removes manually refreshed cookie secrets without adding an authentication
bypass or broader permissions. The smoke identity cannot perform manager-only
actions.

## Security and failure behavior

- All AWS configuration remains reproducible through Terraform or the existing
  idempotent bootstrap mechanism; no production resource is console-created.
- Secrets are never committed, printed, or stored in smoke reports.
- Scheduled scale-in continues to use the existing bounded lifecycle handler.
- Token rotation retries are bounded and failures remain visible in systemd.
- A failed automated login fails the workflow clearly before the smoke test.
- Prometheus receives no new Kubernetes API permission.

## Verification

- Terraform formatting, validation, and focused tests verify both recurring
  scheduled actions, timezone, time, and capacity values.
- Cluster bootstrap tests verify early token rotation and bounded retry.
- Kubernetes render/policy tests verify retained bootstrap completion, skipped
  stale CronJob runs, headless metrics Services, and DNS discovery.
- Dashboard tests verify correct aggregation across replicas.
- Smoke helper tests verify zero baselines, counter deltas, timeout failures,
  and target-health checks.
- Workflow/security tests verify fresh browser authentication, secret masking,
  absence of cookie artifacts, and officer-only identity configuration.
- Existing required repository checks run before live validation.
- Dev and production smoke tests run through the existing promotion path.

## Documentation outcome

After verification, `docs/implementation-status.md` records T27 as complete
using the actual already-passed production smoke evidence and records the six
hardening results truthfully. No unexecuted validation is reported as passed.

## Alternatives rejected

- EventBridge plus a new Lambda for scheduled shutdown adds an unnecessary
  component when Auto Scaling scheduled actions already express the change.
- Kubernetes API service discovery for Prometheus requires additional service
  account credentials and RBAC; DNS discovery is sufficient for these two
  stable Services.
- A long Prometheus range query remains vulnerable to scrape timing and can
  pass using an earlier run; before/after counters directly prove this run.
- Long-lived cookies and authentication bypasses are less secure than obtaining
  short-lived cookies through the real login flow.
