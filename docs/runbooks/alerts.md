# StockAI Alert Runbook

These alerts are owned by the StockAI operator. Delivery is intentionally
internal-only: active alerts are visible in Prometheus, Alertmanager, and
Grafana. Check the alert labels and dashboard evidence first, then use Loki to
correlate sanitized logs. Never paste credentials, raw procurement records, or
secret values into notes.

Silence an alert only for a bounded maintenance window with an owner and
reason. Do not silence procurement-safety or unexplained availability alerts.
After recovery, confirm the alert resolves and record the cause and action.

## StockAIHttpErrorRateHigh

Open **StockAI Agent Health** and identify the bounded route/status class.
Check API readiness and correlated Loki errors. If a dependency is failing,
follow its alert below; otherwise roll back only through the approved GitOps
promotion path. Verify the five-minute error ratio stays below 5%.

## StockAILlmFailures

Open **StockAI LLM and MCP** and inspect the failure code and latency. Confirm
Bedrock access, model availability, quota, and schema-validation failures.
The deterministic fallback must remain active; do not switch models. Verify a
safe fictional scan succeeds before closing the incident.

## StockAIMcpFailures

Compare agent-side and server-side MCP failures, timeouts, and retries. Check
MCP readiness, Service endpoints, NetworkPolicy, and Odoo reachability. Do not
retry a write with an ambiguous outcome; reconcile it first. Verify one
read-only real-transport tool call succeeds.

## StockAIPurchaseOrderActionFailures

Open **StockAI LLM and MCP** and inspect the bounded confirm/cancel result and
latency series. Correlate sanitized MCP logs, then verify Odoo reachability and
the exact draft state with a read. Never resend an ambiguous write. Correct the
dependency or configuration cause and verify the sustained error rate returns
to zero.

## StockAIDecisionReconciliationRequired

Treat this as an immediate procurement-safety incident. Read the immutable
decision and current fictional Odoo purchase order, compare their bound PO
identity and commercial snapshot, and preserve the audit trail. Do not confirm
or cancel again until an operator has established whether the first action
committed. Resolve the case through the reviewed reconciliation workflow and
verify no unresolved reconciliation remains.

## StockAIPodUnavailable

Inspect the Deployment conditions, unavailable pod events, readiness probe,
image digest, scheduling, and resource limits. Check whether the environment
worker is Ready. Make desired-state changes in Git and let Argo CD reconcile;
verify all requested replicas become available.

## StockAIPodCrashLooping

Inspect `kubectl describe pod` and the previous container's sanitized logs.
Check the exit reason, OOM state, configuration, secret references, and probes.
Do not expose secret values while diagnosing. Correct the Git-managed cause
and verify restart growth stops.

## StockAIWorkerDiskPressure

Inspect node conditions and filesystem usage. Remove only documented
reconstructable transient data, and preserve retained application volumes.
If image or log growth is the cause, enforce the approved retention and image
cleanup policy. Verify the node becomes Ready without disk pressure.

## StockAIPersistentVolumePressure

Identify the environment, claim, volume, and consuming workload. Check
Prometheus retention or Odoo/PostgreSQL growth and confirm the latest required
prod snapshot. Do not delete database or filestore data. Expand a retained EBS
volume only through a reviewed Terraform and Kubernetes change.

## StockAIWorkerReadyMismatch

Open **StockAI Kubernetes and Capacity** and compare correctly labeled Ready
workers with the ASG desired and in-service values. Inspect instance health,
cloud-init, SSM join-parameter access, token rotation, kubelet, and CNI. After
the bounded replacement window, follow the worker termination runbook for a
verified stale node or retained-volume attach problem.

## StockAIDependencyUnavailable

Open **StockAI Dependencies and Edge** and inspect the exact failed scrape
target. Check Service endpoints, pod readiness, DNS, and NetworkPolicy before
restarting anything. For Odoo or PostgreSQL, protect retained data and verify a
read-only dependency check succeeds after recovery.

## StockAIPublicHttpsUnavailable

Identify the failed hostname, then check DNS, the ACM certificate, ALB target
health, security groups, the NGINX NodePort, Ingress host rules, and backend
readiness in that order. Keep internal services private. Verify both the
blackbox probe and the expected public health or login response recover.

## StockAIHttpsCertificateExpiring

Inspect the ACM certificate and its Route 53 DNS-validation records. Confirm
the certificate covers all six approved hostnames and remains attached to the
HTTPS listener. Fix reproducibly through Terraform; verify the probe reports
more than 21 days remaining.

## StockAIOdooKeyExpiring

Follow replacement-before-revocation: create the new bounded key through the
approved bootstrap/rotation workflow, write only the exact environment secret,
wait for External Secrets reconciliation, and verify the new key. Revoke the
old key only after verification. Never print either key.

## CloudWatch-owned edge and lifecycle alerts

ALB target health/5xx and Lambda cleanup failures are queried in the Git-managed
dashboards, while the Terraform-managed Lambda error and forced/failed cleanup
alarms remain authoritative. For a forced/failed cleanup or lifecycle timeout,
inspect the sanitized Lambda and SSM outcome, validate instance-to-node
identity, remove only a verified stale Node, confirm EBS detachment, and follow
the worker termination runbook. For ALB failures, follow
`StockAIPublicHttpsUnavailable` above.

## Safe alert verification

In dev only, use a reviewed temporary Prometheus rule with the same labels and
annotations and expression `vector(1)`, wait for it to appear in Alertmanager,
follow the matching evidence steps, then remove the temporary rule from Git.
Exercise one application, Kubernetes, and dependency alert. Never induce an
Odoo key expiry, worker termination, disk pressure, or production outage solely
to test alert delivery.
