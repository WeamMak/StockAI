# SSM Agent Packaging Compatibility Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make T18A bootstrap accept either supported Ubuntu Amazon SSM Agent packaging form, then recover and validate the failed cluster without manual node repair.

**Architecture:** Keep the existing SSM-only access architecture and change only the final node-install service detection boundary. A reviewed Terraform replacement recreates the failed control plane and refreshes worker launch templates; live checks then prove fresh dev and prod joins and the required additional dev replacement.

**Tech Stack:** Bash, cloud-init, systemd, Snap, pytest, ShellCheck 0.10.0, Terraform 1.15.x, AWS EC2/Auto Scaling/SSM, kubeadm Kubernetes 1.35.5, Calico 3.30.2.

## Global Constraints

- Accept `amazon-ssm-agent.service` and the `amazon-ssm-agent` Snap service; install neither.
- Fail closed if neither supported form exists or the selected service is inactive.
- Keep SSM-only administration; do not add an EC2 SSH key pair or inbound access.
- Never print or retrieve the decrypted kubeadm join parameter during validation.
- Do not repair failed nodes manually; cloud-init bootstrap must pass on fresh instances.
- Do not apply a destructive replacement plan without a separate review and explicit approval.
- Preserve all existing pinned Kubernetes, containerd, runc, CNI, AWS CLI, and Calico versions.
- T18B drain and stale-node automation remain out of scope.

---

### Task 1: Accept Debian- and Snap-packaged SSM Agents

**Files:**
- Modify: `tests/infra/test_cluster_bootstrap.py:148-166`
- Modify: `infra/cluster/install-node.sh:175-187`
- Modify: `docs/runbooks/cluster-bootstrap.md:10-44,121-145`

**Interfaces:**
- Consumes: an approved Ubuntu AMI with either the Debian systemd service or the AWS-published Snap already installed.
- Produces: an active Amazon SSM Agent followed by the unchanged `/var/lib/stockai/node-install-complete` marker.

- [x] **Step 1: Write the failing packaging-contract test**

Add this focused test after `test_node_install_and_cni_are_pinned_for_the_approved_cluster`:

```python
def test_node_install_accepts_deb_or_snap_ssm_agent_and_fails_closed() -> None:
    install_script = _read(CLUSTER_ROOT / "install-node.sh")

    assert 'SSM_AGENT_DEB_SERVICE="amazon-ssm-agent.service"' in install_script
    assert (
        'SSM_AGENT_SNAP_SERVICE="snap.amazon-ssm-agent.amazon-ssm-agent.service"'
        in install_script
    )
    assert 'systemctl enable --now "$SSM_AGENT_DEB_SERVICE"' in install_script
    assert "snap list amazon-ssm-agent" in install_script
    assert "snap start --enable amazon-ssm-agent" in install_script
    assert 'systemctl is-active --quiet "$ssm_agent_service"' in install_script
    assert 'ssm_agent_service=""' in install_script
    assert "the approved Ubuntu AMI must include an active amazon-ssm-agent" in install_script
```

- [x] **Step 2: Run the test and verify the observed contract is red**

Run:

```bash
pytest tests/infra/test_cluster_bootstrap.py::test_node_install_accepts_deb_or_snap_ssm_agent_and_fails_closed -v
```

Expected: FAIL because the current script has no Snap service constant or Snap start path.

- [x] **Step 3: Implement the minimal dual-package service boundary**

Add these constants beside the existing version constants:

```bash
SSM_AGENT_DEB_SERVICE="amazon-ssm-agent.service"
SSM_AGENT_SNAP_SERVICE="snap.amazon-ssm-agent.amazon-ssm-agent.service"
```

Replace the current single-service check with:

```bash
ssm_agent_service=""
if systemctl cat "$SSM_AGENT_DEB_SERVICE" >/dev/null 2>&1; then
  ssm_agent_service="$SSM_AGENT_DEB_SERVICE"
  if ! systemctl enable --now "$SSM_AGENT_DEB_SERVICE" >/dev/null; then
    echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
    exit 1
  fi
elif command -v snap >/dev/null 2>&1 && \
  snap list amazon-ssm-agent >/dev/null 2>&1 && \
  systemctl cat "$SSM_AGENT_SNAP_SERVICE" >/dev/null 2>&1; then
  ssm_agent_service="$SSM_AGENT_SNAP_SERVICE"
  if ! snap start --enable amazon-ssm-agent >/dev/null; then
    echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
    exit 1
  fi
else
  echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
  exit 1
fi

if ! systemctl is-active --quiet "$ssm_agent_service"; then
  echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
  exit 1
fi
```

Do not move the node-install marker before this block.

- [x] **Step 4: Document the supported packaging boundary and sanitized diagnosis**

Update the runbook fixed contract and pre-apply check to state that the AMI may provide either the Debian unit or the AWS Snap, but the detected service must be active. Replace the diagnosis command for only `amazon-ssm-agent` with both explicit checks:

```bash
sudo systemctl status amazon-ssm-agent.service --no-pager || \
  sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service --no-pager
```

Add the observed failure signature: a missing node-install marker plus an active Snap agent means the image is valid but the bootstrap payload predates this compatibility correction.

- [x] **Step 5: Run focused syntax, lint, and contract checks**

Run:

```bash
bash -n infra/cluster/*.sh
docker run --rm --volume "$PWD:/mnt:ro" --workdir /mnt koalaman/shellcheck:v0.10.0 infra/cluster/*.sh
pytest tests/infra/test_cluster_bootstrap.py -v
```

Expected: Bash and ShellCheck exit `0`; all six existing tests plus the new packaging test pass.

- [x] **Step 6: Run full local regression checks**

Run:

```bash
terraform -chdir=infra/terraform/platform fmt -check
terraform -chdir=infra/terraform/platform validate
pytest tests/infra -v
make check
git diff --check
```

Expected: all infrastructure tests and repository checks pass without contacting or mutating AWS.

- [x] **Step 7: Commit the compatibility fix**

```bash
git add infra/cluster/install-node.sh tests/infra/test_cluster_bootstrap.py docs/runbooks/cluster-bootstrap.md
git commit -m "fix(infra): support snap-installed SSM agent"
```

---

### Task 2: Produce and review the account-specific recovery plan

**Files:**
- Read: `infra/terraform/platform/terraform.tfvars`
- Generated, ignored, and never committed: `infra/terraform/platform/t18a-ssm-recovery.tfplan`

**Interfaces:**
- Consumes: the committed compatibility payload, existing remote platform state, and the already approved account-specific variables.
- Produces: one saved Terraform plan whose destructive scope is reviewed before apply.

- [ ] **Step 1: Confirm caller, region, branch, and clean source state**

Run:

```bash
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output table
aws configure get region
git status --short --branch
git log -2 --oneline
```

Expected: the approved account, `us-east-1`, `feature/t18a-cluster-bootstrap`, and no uncommitted source change.

- [ ] **Step 2: Re-run the non-mutating Terraform checks**

```bash
terraform -chdir=infra/terraform/platform fmt -check
terraform -chdir=infra/terraform/platform validate
terraform -chdir=infra/terraform/platform state list
```

Expected: validation succeeds and state still tracks the existing platform resources.

- [ ] **Step 3: Create a fresh replacement plan**

```bash
terraform -chdir=infra/terraform/platform plan \
  -replace=module.compute.aws_instance.control_plane \
  -out=t18a-ssm-recovery.tfplan
terraform -chdir=infra/terraform/platform show t18a-ssm-recovery.tfplan
```

Expected reviewed scope:

- the failed control-plane instance is replaced;
- both worker launch templates receive new immutable versions containing the corrected installer;
- ASGs point at the new launch-template versions and retain `1/1/3` baseline capacity;
- the exact SSM parameter and least-privilege policies remain, with no decrypted value;
- no VPC, subnet, route, retained volume, DynamoDB table, secret, Cognito, edge, or observability resource is destroyed or replaced; and
- no EKS resource, SSH key pair, or unrelated permission is introduced.

- [ ] **Step 4: Stop for explicit destructive-action approval**

Do not apply in this task. Present the saved plan summary and replacement list to the user/course operator. The plan file may contain account metadata, remains ignored by Git, and must not be uploaded as a workflow artifact.

---

### Task 3: Apply the approved recovery and establish a healthy baseline

**Files:**
- Generated, ignored: `infra/terraform/platform/t18a-ssm-recovery.tfplan`
- Modify after successful verification: `docs/runbooks/cluster-bootstrap.md`

**Interfaces:**
- Consumes: the exact saved plan approved in Task 2.
- Produces: a fresh healthy control plane and fresh dev/prod workers using the corrected bootstrap payload.

- [ ] **Step 1: Apply only the exact approved saved plan**

After explicit approval, run:

```bash
terraform -chdir=infra/terraform/platform apply t18a-ssm-recovery.tfplan
```

Expected: apply succeeds. If Terraform reports plan staleness or any new replacement, stop and return to Task 2 rather than generating and auto-applying a new plan.

- [ ] **Step 2: Verify the fresh control-plane bootstrap boundary**

```bash
STOCKAI_CP_ID="$(terraform -chdir=infra/terraform/platform output -raw control_plane_instance_id)"
aws ec2 wait instance-status-ok --region us-east-1 --instance-ids "$STOCKAI_CP_ID"
aws ssm start-session --region us-east-1 --target "$STOCKAI_CP_ID"
```

Inside the control-plane session, run:

```bash
sudo test -s /etc/kubernetes/admin.conf
sudo cat /var/lib/stockai/node-install-complete
sudo cat /var/lib/stockai/control-plane-init-complete
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o wide
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods -n kube-system -o wide
sudo systemctl is-active containerd kubelet
sudo systemctl is-active kubeadm-token-rotation.timer
sudo systemctl list-timers kubeadm-token-rotation.timer --no-pager
```

Expected: both markers exist with the pinned versions, the control plane is `Ready`, Calico and CoreDNS are ready, and the rotation timer is active with a next run within 12 hours.

- [ ] **Step 3: Verify fresh dev and prod worker identity**

Inside the same session, run:

```bash
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o wide
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes \
  -o custom-columns=NAME:.metadata.name,ENV:.metadata.labels.stockai\.io/environment,TAINTS:.spec.taints
```

Expected: one dev and one prod worker use EC2 private-DNS names, are `Ready`, have only their matching environment value and matching `NoSchedule` taint, while the control plane retains its control-plane taint.

From the local shell, verify AWS placement and roles without reading secret values:

```bash
aws autoscaling describe-auto-scaling-groups --region us-east-1 \
  --auto-scaling-group-names \
  "$(terraform -chdir=infra/terraform/platform output -raw dev_worker_asg_name)" \
  "$(terraform -chdir=infra/terraform/platform output -raw prod_worker_asg_name)" \
  --query 'AutoScalingGroups[].{Name:AutoScalingGroupName,Instances:Instances[].{Id:InstanceId,AZ:AvailabilityZone,State:LifecycleState,Health:HealthStatus,TemplateVersion:LaunchTemplate.Version}}' \
  --output table
```

Expected: every baseline worker is `InService` and `HEALTHY`, dev is in the Terraform dev AZ, prod is in the Terraform prod AZ, and each uses the current launch-template version. If a failed pre-fix worker remains, stop and obtain approval for its exact ASG replacement; do not patch it manually.

- [ ] **Step 4: Verify parameter metadata only**

```bash
aws ssm describe-parameters --region us-east-1 \
  --parameter-filters 'Key=Name,Option=Equals,Values=/stockai/weam-stockai/kubeadm/join-command' \
  --query 'Parameters[0].{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}'
```

Expected: exact name, `SecureString`, and a modification time after the new control plane initialized. Never run `get-parameter --with-decryption` interactively.

- [ ] **Step 5: Record only sanitized recovery guidance**

Add a short runbook recovery note explaining that existing failed instances must be replaced after this correction because cloud-init scripts run once per instance. Do not record instance IDs, account IDs, tokens, CA hashes, or raw logs.

- [ ] **Step 6: Commit the recovery runbook clarification**

```bash
git add docs/runbooks/cluster-bootstrap.md
git commit -m "docs(infra): document failed-bootstrap replacement"
```

---

### Task 4: Execute the controlled dev replacement acceptance test

**Files:**
- Modify after successful acceptance: `docs/plan.md`
- Modify after successful acceptance: `docs/implementation-status.md`

**Interfaces:**
- Consumes: a healthy baseline cluster from Task 3 and a dev ASG at desired capacity one.
- Produces: live evidence that an ordinary dev ASG replacement joins automatically without leaking its finite token.

- [ ] **Step 1: Capture and verify the current dev ASG member**

```bash
STOCKAI_DEV_ASG="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_asg_name)"
STOCKAI_OLD_DEV_ID="$(aws autoscaling describe-auto-scaling-groups --region us-east-1 --auto-scaling-group-names "$STOCKAI_DEV_ASG" --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].[InstanceId] | [0][0]' --output text)"
STOCKAI_OLD_DEV_DNS="$(aws ec2 describe-instances --region us-east-1 --instance-ids "$STOCKAI_OLD_DEV_ID" --query 'Reservations[0].Instances[0].PrivateDnsName' --output text)"
aws autoscaling describe-auto-scaling-instances --region us-east-1 --instance-ids "$STOCKAI_OLD_DEV_ID" --query 'AutoScalingInstances[0].{ASG:AutoScalingGroupName,State:LifecycleState,Health:HealthStatus}'
aws ec2 describe-instances --region us-east-1 --instance-ids "$STOCKAI_OLD_DEV_ID" --query 'Reservations[0].Instances[0].{DNS:PrivateDnsName,Environment:Tags[?Key==`Environment`]|[0].Value,AZ:Placement.AvailabilityZone,Subnet:SubnetId,Profile:IamInstanceProfile.Arn}'
```

Expected: exact dev ASG, `InService`, `HEALTHY`, `Environment=dev`, expected dev AZ/subnet/profile, and a private-DNS name that is currently `Ready` in Kubernetes.

- [ ] **Step 2: Terminate the verified member without reducing desired capacity**

```bash
aws autoscaling terminate-instance-in-auto-scaling-group \
  --region us-east-1 \
  --instance-id "$STOCKAI_OLD_DEV_ID" \
  --no-should-decrement-desired-capacity
```

Expected: AWS accepts the activity and the ASG launches a replacement. Do not issue the command twice.

- [ ] **Step 3: Wait boundedly for a different healthy instance**

```bash
STOCKAI_NEW_DEV_ID=""
for STOCKAI_ATTEMPT in $(seq 1 60); do
  STOCKAI_NEW_DEV_ID="$(aws autoscaling describe-auto-scaling-groups --region us-east-1 --auto-scaling-group-names "$STOCKAI_DEV_ASG" --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].[InstanceId] | [0][0]' --output text)"
  if [[ "$STOCKAI_NEW_DEV_ID" == i-* && "$STOCKAI_NEW_DEV_ID" != "$STOCKAI_OLD_DEV_ID" ]]; then
    break
  fi
  sleep 10
done
test "$STOCKAI_NEW_DEV_ID" != "$STOCKAI_OLD_DEV_ID"
test "$STOCKAI_NEW_DEV_ID" != "None"
aws ec2 wait instance-status-ok --region us-east-1 --instance-ids "$STOCKAI_NEW_DEV_ID"
```

Expected: a different `i-*` ID becomes EC2 status-ok within the bounded wait.

- [ ] **Step 4: Verify AWS and Kubernetes identity of the replacement**

```bash
STOCKAI_NEW_DEV_DNS="$(aws ec2 describe-instances --region us-east-1 --instance-ids "$STOCKAI_NEW_DEV_ID" --query 'Reservations[0].Instances[0].PrivateDnsName' --output text)"
aws ec2 describe-instances --region us-east-1 --instance-ids "$STOCKAI_NEW_DEV_ID" --query 'Reservations[0].Instances[0].{DNS:PrivateDnsName,Environment:Tags[?Key==`Environment`]|[0].Value,AZ:Placement.AvailabilityZone,Subnet:SubnetId,Profile:IamInstanceProfile.Arn}' --output table
```

Connect to the control plane and run:

```bash
STOCKAI_K8S_NEW_DEV_DNS="$(sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -l stockai.io/environment=dev --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')"
printf 'Kubernetes replacement DNS: %s\n' "$STOCKAI_K8S_NEW_DEV_DNS"
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf wait --for=condition=Ready "node/$STOCKAI_K8S_NEW_DEV_DNS" --timeout=10m
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get node "$STOCKAI_K8S_NEW_DEV_DNS" \
  -o custom-columns=NAME:.metadata.name,ENV:.metadata.labels.stockai\.io/environment,TAINTS:.spec.taints
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods -n kube-system \
  --field-selector "spec.nodeName=$STOCKAI_K8S_NEW_DEV_DNS" -l k8s-app=calico-node -o wide
```

Expected: the printed Kubernetes replacement DNS exactly equals the AWS
`STOCKAI_NEW_DEV_DNS` printed before entering the session, environment is only
`dev`, the matching `NoSchedule` taint exists with no prod value, the node is
`Ready`, and its Calico pod is ready.

- [ ] **Step 5: Prove no concrete token appears without retrieving the parameter**

From the local shell, test user data and Terraform outputs using a boolean match only:

```bash
if aws ec2 describe-instance-attribute --region us-east-1 --instance-id "$STOCKAI_NEW_DEV_ID" --attribute userData --query 'UserData.Value' --output text | base64 --decode | grep -Eq -- 'kubeadm join [0-9.]+:6443 --token [a-z0-9]{6}\.[a-z0-9]{16}'; then echo 'FAIL: token-shaped value in user data'; exit 1; else echo 'PASS: no token-shaped value in user data'; fi
if terraform -chdir=infra/terraform/platform output -json | grep -Eq -- 'kubeadm join [0-9.]+:6443 --token [a-z0-9]{6}\.[a-z0-9]{16}'; then echo 'FAIL: token-shaped value in Terraform output'; exit 1; else echo 'PASS: no token-shaped value in Terraform output'; fi
```

Open a Session Manager shell on the replacement and run boolean scans that never print matching lines:

```bash
if sudo grep -Eq -- 'kubeadm join [0-9.]+:6443 --token [a-z0-9]{6}\.[a-z0-9]{16}' /var/log/cloud-init-output.log; then echo 'FAIL: token-shaped value in cloud-init output'; else echo 'PASS: no token-shaped value in cloud-init output'; fi
if sudo journalctl --since=-2h --no-pager | grep -Eq -- 'kubeadm join [0-9.]+:6443 --token [a-z0-9]{6}\.[a-z0-9]{16}'; then echo 'FAIL: token-shaped value in journal'; else echo 'PASS: no token-shaped value in journal'; fi
```

Expected: all four checks print `PASS`. Do not display matching content and do not retrieve the decrypted SSM value.

- [ ] **Step 6: Record successful T18A acceptance**

Only after every live check succeeds:

- mark T18A Step 7 complete in `docs/plan.md`;
- update the T18A row in `docs/implementation-status.md` with sanitized test, apply, node-health, replacement, and non-disclosure evidence; and
- retain T18B drain/stale-node automation as the remaining limitation.

- [ ] **Step 7: Validate and commit the evidence update**

```bash
git diff --check
git diff -- docs/plan.md docs/implementation-status.md
git add docs/plan.md docs/implementation-status.md
git commit -m "docs(infra): record t18a live acceptance"
```
