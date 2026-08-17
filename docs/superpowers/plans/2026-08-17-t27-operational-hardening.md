# T27 Operational Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix operational issues 1, 3, 5, 6, 7, and 8 without changing StockAI application behavior or architecture.

**Architecture:** Reuse the worker termination lifecycle hook, systemd token rotation, existing Kubernetes workloads, Prometheus, Cognito, and exact-release smoke test. Add only native ASG schedules, bounded startup behavior, direct per-pod metric discovery, current-run counter assertions, and a dedicated browser-authenticated production smoke identity.

**Tech Stack:** Terraform, AWS Auto Scaling and Cognito, systemd, Kubernetes/Kustomize, Prometheus, Python/pytest/httpx/boto3/Playwright, GitHub Actions.

## Global Constraints

- Include only issues 1, 3, 5, 6, 7, and 8.
- Keep worker startup and control-plane startup manual.
- Do not implement stale-node or `VolumeAttachment` startup cleanup.
- Do not change the UI, agent, Bedrock, LangGraph, MCP business flow, or Odoo seed.
- Never print, commit, upload, or retain authentication credentials or cookies.
- Provision AWS configuration through Terraform or the existing idempotent identity-bootstrap boundary.
- Keep dev and prod configuration separate and use the existing promotion path.

---

### Task 1: Schedule graceful worker scale-in

**Files:**
- Modify: `infra/terraform/modules/compute/main.tf`
- Modify: `tests/infra/test_platform_plan.py`
- Modify: `docs/runbooks/cost-and-shutdown.md`

**Interfaces:**
- Consumes: `aws_autoscaling_group.worker` and the existing termination lifecycle hook.
- Produces: four `aws_autoscaling_schedule.worker_shutdown` instances, two times for each environment.

- [ ] **Step 1: Add a failing Terraform plan assertion**

Assert that the plan contains four schedules, each targets the correct worker
ASG, uses `Asia/Jerusalem`, sets `min_size=0`, `desired_capacity=0`,
`max_size=3`, and has recurrence `45 15 * * *` or `45 23 * * *`.

```python
schedules = list(_values(platform_plan, "aws_autoscaling_schedule"))
assert len(schedules) == 4
assert {item["recurrence"] for item in schedules} == {
    "45 15 * * *",
    "45 23 * * *",
}
assert all(item["time_zone"] == "Asia/Jerusalem" for item in schedules)
assert all(item["min_size"] == 0 for item in schedules)
assert all(item["desired_capacity"] == 0 for item in schedules)
assert all(item["max_size"] == 3 for item in schedules)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `uv run pytest tests/infra/test_platform_plan.py -q`

Expected: FAIL because no Auto Scaling schedules exist.

- [ ] **Step 3: Add the minimal native schedules**

Create a local map containing `dev-afternoon`, `dev-night`,
`prod-afternoon`, and `prod-night`, then add one resource:

```hcl
resource "aws_autoscaling_schedule" "worker_shutdown" {
  for_each = local.worker_shutdown_schedules

  autoscaling_group_name = aws_autoscaling_group.worker[each.value.environment].name
  desired_capacity       = 0
  max_size               = 3
  min_size               = 0
  recurrence             = each.value.recurrence
  scheduled_action_name  = "${var.cluster_name}-${each.value.environment}-${each.value.name}"
  time_zone              = "Asia/Jerusalem"
}
```

Update the runbook to state that scheduled scale-in begins at 15:45 and 23:45,
the existing hook drains workers, and startup remains manual.

- [ ] **Step 4: Validate and test**

Run: `terraform fmt -check -recursive infra/terraform`

Run: `make terraform-validate`

Run: `uv run pytest tests/infra/test_platform_plan.py -q`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/compute/main.tf tests/infra/test_platform_plan.py docs/runbooks/cost-and-shutdown.md
git commit -m "feat: schedule graceful worker shutdown"
```

### Task 2: Rotate the join token promptly after boot

**Files:**
- Modify: `infra/cluster/kubeadm-token-rotation.timer`
- Modify: `infra/cluster/kubeadm-token-rotation.service`
- Modify: `tests/infra/test_cluster_bootstrap.py`

**Interfaces:**
- Consumes: `/usr/local/sbin/stockai-rotate-join-token` and the existing Parameter Store sink.
- Produces: early boot rotation with at most five service starts in two minutes.

- [ ] **Step 1: Change the test first**

```python
assert "OnBootSec=1min" in timer
assert "OnUnitActiveSec=12h" in timer
assert "StartLimitIntervalSec=120" in service
assert "StartLimitBurst=5" in service
assert "Restart=on-failure" in service
assert "RestartSec=15s" in service
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `uv run pytest tests/infra/test_cluster_bootstrap.py -q`

Expected: FAIL because boot rotation is still delayed twelve hours and retry is absent.

- [ ] **Step 3: Apply the bounded systemd settings**

Set `OnBootSec=1min`, retain `OnUnitActiveSec=12h` and `Persistent=true`, and
add the exact bounded retry fields asserted above. Do not alter the rotation
script or token lifetime.

```ini
[Unit]
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Restart=on-failure
RestartSec=15s
```

- [ ] **Step 4: Run the focused and platform tests**

Run: `uv run pytest tests/infra/test_cluster_bootstrap.py tests/unit/infra/test_cluster_platform.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add infra/cluster/kubeadm-token-rotation.timer infra/cluster/kubeadm-token-rotation.service tests/infra/test_cluster_bootstrap.py
git commit -m "fix: rotate worker join token after boot"
```

### Task 3: Prevent recovery-time Job surprises

**Files:**
- Modify: `deploy/kubernetes/base/application-workloads.yaml`
- Modify: `tests/kubernetes/test_application_overlays.py`

**Interfaces:**
- Consumes: existing Argo `Force=true,Replace=true` bootstrap behavior and daily scan CronJob.
- Produces: a retained completed bootstrap Job and a five-minute missed-scan deadline.

- [ ] **Step 1: Update render tests first**

```python
assert "ttlSecondsAfterFinished" not in bootstrap_spec
assert cron["spec"]["startingDeadlineSeconds"] == 300
```

- [ ] **Step 2: Run the render test and confirm it fails**

Run: `uv run pytest tests/kubernetes/test_application_overlays.py -q`

Expected: FAIL on the old bootstrap TTL and missing CronJob deadline.

- [ ] **Step 3: Make the two manifest edits**

Remove only `stockai-odoo-bootstrap.spec.ttlSecondsAfterFinished`. Add
`stockai-daily-scan.spec.startingDeadlineSeconds: 300`. Preserve every other
deadline, retry, history, sync, and concurrency setting.

```yaml
kind: CronJob
spec:
  schedule: "0 5 * * *"
  timeZone: UTC
  startingDeadlineSeconds: 300
  concurrencyPolicy: Forbid
```

- [ ] **Step 4: Render and validate both environments**

Run: `uv run pytest tests/kubernetes/test_application_overlays.py -q`

Run: `make kubernetes-validate`

Expected: PASS with valid dev and prod overlays.

- [ ] **Step 5: Commit**

```bash
git add deploy/kubernetes/base/application-workloads.yaml tests/kubernetes/test_application_overlays.py
git commit -m "fix: make scheduled jobs recovery safe"
```

### Task 4: Scrape every API and MCP pod

**Files:**
- Modify: `deploy/kubernetes/base/application-services.yaml`
- Modify: `deploy/kubernetes/base/observability/configuration.yaml`
- Modify: `tests/kubernetes/test_observability_collectors.py`
- Modify if required by a failing aggregation assertion: `deploy/kubernetes/base/observability/dashboards/*.json`

**Interfaces:**
- Consumes: existing API and MCP pod selectors and `/metrics` ports.
- Produces: `api-metrics` and `procurement-mcp-metrics` headless DNS records consumed by Prometheus.

- [ ] **Step 1: Add failing render assertions**

Assert both Services have `clusterIP: None`, select their existing workload,
and expose the correct metrics port. Assert Prometheus contains:

```yaml
dns_sd_configs:
  - names: [api-metrics]
    type: A
    port: 8000
```

and the equivalent MCP configuration with port `9000`; assert the old
`static_configs` targets are absent.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `uv run pytest tests/kubernetes/test_observability_collectors.py tests/kubernetes/test_observability_content.py -q`

Expected: FAIL because metrics still use load-balanced service targets.

- [ ] **Step 3: Add headless Services and DNS discovery**

Add two selector-compatible headless Services to
`application-services.yaml`. Replace only the two application scrape jobs with
`dns_sd_configs`, using `refresh_interval: 30s`. Keep metric paths, job names,
and all other scrape jobs unchanged. Change dashboard JSON only if the new test
identifies a query that does not aggregate across replica labels.

- [ ] **Step 4: Run observability and Kubernetes validation**

Run: `uv run pytest tests/kubernetes/test_observability_collectors.py tests/kubernetes/test_observability_content.py -q`

Run: `make kubernetes-validate`

Expected: PASS; both overlays contain the two headless Services and DNS discovery.

- [ ] **Step 5: Commit**

```bash
git add deploy/kubernetes/base/application-services.yaml deploy/kubernetes/base/observability/configuration.yaml deploy/kubernetes/base/observability/dashboards tests/kubernetes/test_observability_collectors.py
git commit -m "fix: scrape metrics from every application pod"
```

### Task 5: Prove metrics came from the current smoke run

**Files:**
- Modify: `tests/smoke/test_dev_skeleton.py`
- Create: `tests/unit/smoke/test_metric_proof.py`

**Interfaces:**
- Consumes: Grafana's Prometheus proxy and five existing metric counter families.
- Produces: `_metric_total(...) -> float` and `_wait_for_metric_deltas(..., baselines) -> None`.

- [ ] **Step 1: Write unit tests for the metric helpers**

Cover an absent series returning `0.0`, summing multiple replica series,
success only when every current value exceeds its own baseline, and a bounded
timeout naming the missing queries.

```python
assert _metric_total({"data": {"result": []}}) == 0.0
assert _metric_total(payload_with_values("2", "3")) == 5.0
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `uv run pytest tests/unit/smoke/test_metric_proof.py -q`

Expected: FAIL because the delta helpers do not exist.

- [ ] **Step 3: Replace range-window assertions with baselines**

Use aggregate instant queries such as:

```promql
sum(procurement_llm_calls_total{status="success"})
```

Record all five totals before POSTing `/api/v1/scans`. After scan completion,
poll until every total is greater than its baseline. Also require both jobs to
have healthy targets using `min(up{job=~"stockai-agent-api|stockai-procurement-mcp"}) == 1`.

- [ ] **Step 4: Run unit and collected smoke tests**

Run: `uv run pytest tests/unit/smoke/test_metric_proof.py -q`

Run: `uv run pytest tests/smoke/test_dev_skeleton.py tests/smoke/test_prod_skeleton.py -q`

Expected: unit tests PASS; live smoke tests are collected and skipped without explicit authorization.

- [ ] **Step 5: Commit**

```bash
git add tests/smoke/test_dev_skeleton.py tests/unit/smoke/test_metric_proof.py
git commit -m "fix: prove current smoke metrics by counter delta"
```

### Task 6: Automate secure production smoke login and close T27

**Files:**
- Modify: `src/procurement/bootstrap/cognito.py`
- Modify: `tests/unit/bootstrap/test_cognito.py`
- Create: `scripts/smoke/authenticated_prod.py`
- Create: `tests/unit/smoke/test_authenticated_prod.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.github/workflows/main-promote.yml`
- Modify: `tests/config/test_ci_workflows.py`
- Modify: `docs/implementation-status.md`

**Interfaces:**
- Consumes: protected `STOCKAI_PROD_SMOKE_USERNAME`, `STOCKAI_PROD_SMOKE_PASSWORD`, `STOCKAI_PROD_SMOKE_EMAIL`, and `STOCKAI_PROD_COGNITO_USER_POOL_ID` values.
- Produces: one permanent dedicated officer identity and in-memory `stockai_session`/`stockai_csrf` values passed directly to `run_exact_walking_skeleton("prod")`.

- [ ] **Step 1: Write failing identity and browser-wrapper tests**

Extend the stateful Cognito fake to assert the smoke user is created once,
assigned only to `stockai-procurement-officer`, and receives
`admin_set_user_password(..., Permanent=True)` without credential output.
Test the wrapper with injected browser and smoke call boundaries: matching
cookies are required, secrets are never printed, environment values are
cleared in `finally`, and authentication failure prevents smoke execution.

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `uv run pytest tests/unit/bootstrap/test_cognito.py tests/unit/smoke/test_authenticated_prod.py tests/config/test_ci_workflows.py -q`

Expected: FAIL because the dedicated identity and browser wrapper are absent.

- [ ] **Step 3: Add the minimal secure automation**

Extend the existing bootstrap boundary with an optional dedicated smoke-user
settings object and `admin_set_user_password`. Add pinned Playwright to the dev
dependency group. In `authenticated_prod.py`:

```python
@dataclass(frozen=True, slots=True)
class CognitoSmokeUserSettings:
    user_pool_id: str
    username: str
    email: str
    password: str = field(repr=False)


def bootstrap_smoke_user(
    settings: CognitoSmokeUserSettings,
    *,
    client: CognitoAdminClient,
) -> None:
    _ensure_user(
        client,
        user_pool_id=settings.user_pool_id,
        username=settings.username,
        email=settings.email,
        temporary_password=settings.password,
    )
    client.admin_set_user_password(
        UserPoolId=settings.user_pool_id,
        Username=settings.username,
        Password=settings.password,
        Permanent=True,
    )
    client.admin_add_user_to_group(
        UserPoolId=settings.user_pool_id,
        Username=settings.username,
        GroupName=OFFICER_GROUP,
    )
```

1. Validate the four protected inputs without echoing them.
2. Idempotently ensure the user and officer-only group membership.
3. Launch headless Chromium and navigate to `/auth/login`.
4. Fill Cognito `username` and `password` inputs and submit.
5. Wait for the app origin, read exactly `stockai_session` and `stockai_csrf`
   from the browser context, and call the existing smoke function directly.
6. Clear cookie environment variables and close the context in `finally`.

Update `main-promote.yml` to install pinned Chromium, use the protected smoke
credentials, and run this module. Remove the two expiring cookie secrets. Do
not enable screenshots, traces, videos, shell tracing, or cookie artifacts.

- [ ] **Step 4: Run all required offline verification**

Run: `uv lock --check`

Run: `make format-check lint`

Run: `uv run pytest tests/unit/bootstrap/test_cognito.py tests/unit/smoke/test_authenticated_prod.py tests/config/test_ci_workflows.py -q`

Run: `make test-unit`

Run: `make test-integration`

Run: `make terraform-validate kubernetes-validate`

Expected: all PASS.

- [ ] **Step 5: Validate live and update actual status**

Deploy through the existing feature → dev → main promotion flow. Run
`make smoke-dev`, then let the protected main workflow execute the automated
production smoke. Record only the actual release ID, Argo revision, smoke run
ID, evidence digest, duration, and pass/fail results in
`docs/implementation-status.md`; mark T27 complete only when production smoke
passes.

- [ ] **Step 6: Commit documentation and final verification**

```bash
git add src/procurement/bootstrap/cognito.py scripts/smoke/authenticated_prod.py pyproject.toml uv.lock .github/workflows/main-promote.yml tests docs/implementation-status.md
git commit -m "feat: automate authenticated production smoke"
```

Run: `git diff --check main...HEAD`

Expected: no whitespace errors and a clean worktree after the final commit.
