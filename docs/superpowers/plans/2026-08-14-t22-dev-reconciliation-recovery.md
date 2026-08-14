# T22 Dev Reconciliation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the T22 dev GitOps deployment to `Synced` and `Healthy` by allowing Calico pods to reach the Kubernetes API, completing Loki's S3 retention configuration, and safely recreating only the three never-mounted dev storage objects with their Terraform-provisioned EBS IDs.

**Architecture:** Terraform remains authoritative for the control-plane security group, Git/Kustomize remains authoritative for workloads, and Argo CD remains the workload deployer. A narrowly scoped operator runbook pauses only the `stockai-dev` Application's automated reconciliation for the immutable PV recovery, deletes no AWS resources, and restores Argo automation for convergence.

**Tech Stack:** Terraform/AWS EC2 security groups, Kubernetes/Kustomize, Argo CD, External Secrets Operator, Loki, pytest, AWS CLI, SSM.

## Global Constraints

- Implement only T22; do not start T23 or T24.
- Treat the T22 section of `simplified-plan.txt` as the source of truth.
- Reuse the existing T21/T21A/T21B implementation; do not redesign working infrastructure.
- Argo CD deploys workloads; GitHub Actions must not run `kubectl`.
- Do not rebuild images during recovery or promotion.
- Docker Scout remains report-only.
- `make promote-dev` must not commit, push, merge, call AWS, or contact Kubernetes.
- The control-plane rule must allow TCP 6443 from exactly `192.168.0.0/16`.
- Loki retention stays enabled and uses the existing S3 object store.
- The one-time cleanup is limited to dev and must not delete EBS volumes, snapshots, namespaces, Terraform resources, unrelated workloads, or production objects.
- Stop on an unexpected Terraform replacement/deletion, an attached target volume, a stuck PVC/PV deletion, or a continuing Kubernetes API timeout.

---

### Task 1: Permit pod-CIDR access to the Kubernetes API

**Files:**
- Modify: `tests/infra/test_platform_plan.py:307`
- Modify: `infra/terraform/modules/network/main.tf:105`

**Interfaces:**
- Consumes: fixed Calico pod CIDR `192.168.0.0/16` and control-plane API port `6443`.
- Produces: Terraform resource `aws_vpc_security_group_ingress_rule.control_plane_api_pods`.

- [ ] **Step 1: Tighten the Terraform plan contract before changing HCL**

Replace `test_public_ingress_is_limited_to_admin_ssh_and_api` with:

```python
def test_cidr_ingress_is_limited_to_admin_and_pod_api_access(
    platform_plan: TerraformPlan,
) -> None:
    ingress_rules = list(_values(platform_plan, "aws_vpc_security_group_ingress_rule"))
    cidr_rules = [rule for rule in ingress_rules if rule.get("cidr_ipv4")]

    assert {
        (rule["cidr_ipv4"], rule["from_port"], rule["to_port"])
        for rule in cidr_rules
    } == {
        ("203.0.113.10/32", 22, 22),
        ("203.0.113.10/32", 6443, 6443),
        ("192.168.0.0/16", 6443, 6443),
    }
    assert all(rule["ip_protocol"] == "tcp" for rule in cidr_rules)
    assert all(rule.get("cidr_ipv4") != "0.0.0.0/0" for rule in ingress_rules)


def test_control_plane_pod_api_rule_is_narrow(
    platform_plan: TerraformPlan,
) -> None:
    rules = list(_values(platform_plan, "aws_vpc_security_group_ingress_rule"))
    pod_api_rule = next(
        rule
        for rule in rules
        if rule.get("description") == "Kubernetes API from the Calico pod CIDR"
    )

    assert pod_api_rule["cidr_ipv4"] == "192.168.0.0/16"
    assert pod_api_rule["from_port"] == 6443
    assert pod_api_rule["to_port"] == 6443
    assert pod_api_rule["ip_protocol"] == "tcp"
```

- [ ] **Step 2: Run the focused tests and confirm the new contract fails**

Run:

```bash
uv run pytest tests/infra/test_platform_plan.py \
  -k 'cidr_ingress or control_plane_pod_api' -v
```

Expected: FAIL because no ingress rule has the Calico description/CIDR.

- [ ] **Step 3: Add the minimal Terraform rule**

Insert after `control_plane_api_admin` in `infra/terraform/modules/network/main.tf`:

```hcl
resource "aws_vpc_security_group_ingress_rule" "control_plane_api_pods" {
  security_group_id = aws_security_group.control_plane.id
  cidr_ipv4         = "192.168.0.0/16"
  description       = "Kubernetes API from the Calico pod CIDR"
  from_port         = 6443
  ip_protocol       = "tcp"
  to_port           = 6443
}
```

- [ ] **Step 4: Format and rerun the network tests**

Run:

```bash
terraform fmt -check -recursive infra/terraform
uv run pytest tests/infra/test_platform_plan.py \
  -k 'cidr_ingress or control_plane_pod_api' -v
```

Expected: formatting succeeds and both focused tests PASS.

- [ ] **Step 5: Commit the network correction**

```bash
git add infra/terraform/modules/network/main.tf tests/infra/test_platform_plan.py
git commit -m "fix(terraform): allow pod access to Kubernetes API"
```

### Task 2: Complete Loki's S3 retention configuration

**Files:**
- Modify: `tests/kubernetes/test_observability_collectors.py:112`
- Modify: `deploy/kubernetes/base/observability/configuration.yaml:88`

**Interfaces:**
- Consumes: existing Loki `loki.yaml` ConfigMap data, S3 bucket, and per-environment prefix.
- Produces: Loki compactor setting `delete_request_store: s3`.

- [ ] **Step 1: Add the failing render assertion**

In `test_loki_is_s3_prefixed_and_locally_bounded`, add:

```python
assert "delete_request_store: s3" in config["loki.yaml"]
```

- [ ] **Step 2: Verify the render contract fails for both environments**

Run:

```bash
uv run pytest \
  tests/kubernetes/test_observability_collectors.py::test_loki_is_s3_prefixed_and_locally_bounded \
  -v
```

Expected: two failures because the setting is absent from dev and prod renders.

- [ ] **Step 3: Add the minimal Loki setting**

Add this line under `retention_enabled: true` in the compactor block:

```yaml
      delete_request_store: s3
```

- [ ] **Step 4: Verify both rendered environments**

Run:

```bash
uv run pytest \
  tests/kubernetes/test_observability_collectors.py::test_loki_is_s3_prefixed_and_locally_bounded \
  -v
```

Expected: both parameterized cases PASS.

- [ ] **Step 5: Commit the Loki correction**

```bash
git add deploy/kubernetes/base/observability/configuration.yaml \
  tests/kubernetes/test_observability_collectors.py
git commit -m "fix(observability): configure Loki retention store"
```

### Task 3: Document and test the bounded dev storage recovery

**Files:**
- Create: `docs/runbooks/dev-reconciliation-recovery.md`
- Create: `tests/config/test_dev_reconciliation_recovery_runbook.py`

**Interfaces:**
- Consumes: real EBS IDs `vol-051d6c42ca98f0b15`, `vol-0491b34550d11b018`, and `vol-01ab986773724a6b1`; Argo Application `stockai-dev`.
- Produces: an operator-only, stop-on-error runbook that pauses and restores automated reconciliation without becoming pipeline behavior.

- [ ] **Step 1: Create the failing documentation contract**

Create `tests/config/test_dev_reconciliation_recovery_runbook.py`:

```python
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "dev-reconciliation-recovery.md"


def test_runbook_is_bounded_to_the_three_dev_storage_sets() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for name in (
        "stockai-dev-odoo-filestore",
        "stockai-dev-postgresql-data",
        "stockai-dev-prometheus-data",
        "odoo-filestore",
        "postgresql-data",
        "prometheus-data",
    ):
        assert name in text
    for volume_id in (
        "vol-051d6c42ca98f0b15",
        "vol-0491b34550d11b018",
        "vol-01ab986773724a6b1",
    ):
        assert volume_id in text
    assert "pause automated reconciliation" in text
    assert "restore automated reconciliation" in text
    assert "Retain" in text


def test_runbook_forbids_destructive_or_production_operations() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for forbidden in (
        "kubectl delete namespace",
        "aws ec2 delete-volume",
        "stockai-prod-",
        "--force",
    ):
        assert forbidden not in text


def test_runbook_contains_required_preflight_and_acceptance_checks() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "aws ec2 describe-volumes",
        "kubectl get volumeattachments",
        "kubectl -n dev get secretstore,externalsecret",
        "kubectl -n argocd get application stockai-dev",
        "Synced",
        "Healthy",
    ):
        assert required in text
```

- [ ] **Step 2: Confirm the runbook test fails**

Run:

```bash
uv run pytest tests/config/test_dev_reconciliation_recovery_runbook.py -v
```

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the preflight and stop conditions**

Create `docs/runbooks/dev-reconciliation-recovery.md` with:

```markdown
# Dev reconciliation recovery

This is a one-time T22 operator procedure. Run AWS checks locally, then run
Kubernetes commands through the control-plane SSM session. The PersistentVolume
reclaim policy is Retain: this procedure deletes Kubernetes objects only and
never deletes an EBS volume.

## Stop conditions

Stop if any target EBS volume is absent, attached, outside us-east-1a, or differs
from Git. Stop if any target has a VolumeAttachment. Stop on stuck deletion; do
not remove finalizers. Stop if the Terraform apply includes replacement,
deletion, or unrelated network changes. Stop if External Secrets still times out
to the Kubernetes API after the rule is live.

## AWS preflight from the local workstation

```bash
aws ec2 describe-volumes --region us-east-1 --volume-ids \
  vol-051d6c42ca98f0b15 \
  vol-0491b34550d11b018 \
  vol-01ab986773724a6b1 \
  --query 'Volumes[].{Id:VolumeId,State:State,AZ:AvailabilityZone,Attachments:Attachments}'
aws ssm start-session --region us-east-1 --target i-02ca9a315122c8c77
```

All three volumes must be available, unattached, and in us-east-1a.

## Kubernetes preflight in the SSM session

```bash
sudo -i
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl get volumeattachments
kubectl get pv stockai-dev-odoo-filestore \
  stockai-dev-postgresql-data stockai-dev-prometheus-data \
  -o custom-columns='NAME:.metadata.name,HANDLE:.spec.csi.volumeHandle,RECLAIM:.spec.persistentVolumeReclaimPolicy,STATUS:.status.phase'
```

Confirm there is no target VolumeAttachment and all three reclaim policies are
Retain.

## Recreate the invalid Kubernetes storage objects

First pause automated reconciliation:

```bash
kubectl -n argocd patch application stockai-dev --type=json \
  -p='[{"op":"remove","path":"/spec/syncPolicy/automated"}]'
kubectl -n dev delete deployment stockai-odoo stockai-postgresql stockai-prometheus
kubectl -n dev delete job stockai-odoo-bootstrap --ignore-not-found
kubectl -n dev wait --for=delete deployment/stockai-odoo \
  deployment/stockai-postgresql deployment/stockai-prometheus --timeout=120s
kubectl -n dev delete pvc odoo-filestore postgresql-data prometheus-data
kubectl -n dev wait --for=delete pvc/odoo-filestore \
  pvc/postgresql-data pvc/prometheus-data --timeout=120s
kubectl delete pv stockai-dev-odoo-filestore \
  stockai-dev-postgresql-data stockai-dev-prometheus-data
kubectl wait --for=delete pv/stockai-dev-odoo-filestore \
  pv/stockai-dev-postgresql-data pv/stockai-dev-prometheus-data --timeout=120s
```

Then restore automated reconciliation:

```bash
kubectl -n argocd patch application stockai-dev --type=merge \
  -p='{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
```

Argo CD, not this runbook, recreates the desired workloads and storage objects.

## Acceptance

```bash
kubectl get pv stockai-dev-odoo-filestore \
  stockai-dev-postgresql-data stockai-dev-prometheus-data \
  -o custom-columns='NAME:.metadata.name,HANDLE:.spec.csi.volumeHandle,STATUS:.status.phase'
kubectl -n dev get pvc odoo-filestore postgresql-data prometheus-data
kubectl -n dev get secretstore,externalsecret
kubectl -n dev get pods
kubectl -n argocd get application stockai-dev
```

The PV handles must respectively be vol-051d6c42ca98f0b15,
vol-0491b34550d11b018, and vol-01ab986773724a6b1; all PVCs and ExternalSecrets
must be ready, workloads must be healthy, and Argo must report Synced and
Healthy.
```

- [ ] **Step 4: Run the documentation contract**

Run:

```bash
uv run pytest tests/config/test_dev_reconciliation_recovery_runbook.py -v
```

Expected: all three tests PASS.

- [ ] **Step 5: Commit the guarded runbook**

```bash
git add docs/runbooks/dev-reconciliation-recovery.md \
  tests/config/test_dev_reconciliation_recovery_runbook.py
git commit -m "docs: add bounded dev reconciliation recovery"
```

### Task 4: Run offline T22 verification and record readiness

**Files:**
- Modify: `docs/implementation-status.md`

**Interfaces:**
- Consumes: Tasks 1-3 and existing repository validation targets.
- Produces: an evidence-backed pre-apply status entry with exact command results.

- [ ] **Step 1: Run focused regression tests**

```bash
uv run pytest tests/infra/test_platform_plan.py -v
uv run pytest tests/kubernetes/test_observability_collectors.py \
  tests/config/test_dev_reconciliation_recovery_runbook.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run infrastructure and Kubernetes validation**

```bash
terraform fmt -check -recursive infra/terraform
make terraform-validate
make kubernetes-validate
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Update implementation status with only actual results**

In the T22 section of `docs/implementation-status.md`, record the date, each
command run, its actual pass/fail count, and that live recovery remains pending.
Do not write `Synced` or `Healthy` until the live acceptance commands prove it.

- [ ] **Step 4: Commit offline verification status**

```bash
git add docs/implementation-status.md
git commit -m "docs: record T22 recovery verification"
```

### Task 5: Apply only the reviewed control-plane rule

**Files:**
- Verify: `.github/workflows/terraform-provision.yml`
- Verify: `docs/runbooks/dev-reconciliation-recovery.md`

**Interfaces:**
- Consumes: protected provision workflow inputs and reviewed Terraform plan.
- Produces: live TCP 6443 ingress from `192.168.0.0/16`, with no workload deployment from GitHub Actions.

- [ ] **Step 1: Integrate the recovery commits through the existing branch flow**

Merge the recovery branch into `dev` and push `dev`. This change should not
trigger image builds because no image input changed. Open the normal PR to
`main` and merge it only after required checks pass; do not run
`make promote-dev` because no image digest is being promoted.

- [ ] **Step 2: Run the protected provision workflow from `main`**

Use these existing workflow inputs:

```text
deployment: weam-stockai
aws_account_id: 228281126655
confirmation: provision weam-stockai in 228281126655
```

- [ ] **Step 3: Review the saved Terraform plan before approval**

Approve only if the plan adds
`aws_vpc_security_group_ingress_rule.control_plane_api_pods` with TCP 6443 and
`192.168.0.0/16`, plus expected no-op effects in already synchronized roots.
Stop on any replacement, deletion, or unrelated network mutation.

- [ ] **Step 4: Verify External Secrets API access before storage recovery**

In the SSM session run:

```bash
sudo KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n dev logs deployment/stockai-external-secrets --tail=100
sudo KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n dev get secretstore,externalsecret
```

Expected: no new `10.96.0.1:443` timeout, the SecretStore is ready, and all six
ExternalSecrets become `Ready=True`. Stop if the API timeout remains.

### Task 6: Execute the one-time recovery and close T22

**Files:**
- Follow: `docs/runbooks/dev-reconciliation-recovery.md`
- Modify: `docs/implementation-status.md`

**Interfaces:**
- Consumes: the live rule from Task 5 and the guarded operator runbook.
- Produces: real bound dev PVs/PVCs, healthy workloads, and `stockai-dev` at `Synced`/`Healthy`.

- [ ] **Step 1: Execute the runbook exactly**

Follow `docs/runbooks/dev-reconciliation-recovery.md` from preflight through
restoring automated reconciliation. Do not add commands ad hoc when a stop
condition is reached.

- [ ] **Step 2: Capture live acceptance evidence**

Run:

```bash
sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pv \
  stockai-dev-odoo-filestore stockai-dev-postgresql-data \
  stockai-dev-prometheus-data \
  -o custom-columns='NAME:.metadata.name,HANDLE:.spec.csi.volumeHandle,STATUS:.status.phase'
sudo KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n dev get pvc,secretstore,externalsecret,pods
sudo KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n argocd get application stockai-dev \
  -o jsonpath='{.status.sync.status}{" | "}{.status.health.status}{"\n"}'
```

Expected: the three real IDs are `Bound`, all three PVCs are `Bound`, all six
ExternalSecrets are ready, the workloads have no configuration/attachment
errors, and the final line is `Synced | Healthy`.

- [ ] **Step 3: Run final relevant regression checks**

```bash
uv run pytest tests/infra/test_platform_plan.py \
  tests/kubernetes/test_observability_collectors.py \
  tests/config/test_dev_reconciliation_recovery_runbook.py -v
make terraform-validate
make kubernetes-validate
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 4: Record actual live and regression results**

Update the T22 section of `docs/implementation-status.md` with the exact
Terraform apply result, real PV handles/statuses, SecretStore and
ExternalSecret readiness, workload status, Argo status, and final test command
results. Record any remaining failure honestly instead of declaring T22 done.

- [ ] **Step 5: Commit the final status only**

```bash
git add docs/implementation-status.md
git commit -m "docs: record T22 live reconciliation"
```

T22 is complete only after the live evidence shows `Synced | Healthy`. Stop
there; do not start T23 or T24.
