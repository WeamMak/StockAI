# T18A Worker Join Validation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve StockAI's strict no-`eval` worker join boundary while accepting semantically identical horizontal whitespace, reporting only secret-safe failure categories, and completing T18A's real ASG replacement acceptance.

**Architecture:** Keep the existing control plane, encrypted SSM parameter, IAM, rotation, networking, runtime, and CNI unchanged. Replace only the worker's monolithic command-string regular expression with exact seven-field Bash-array validation; the changed embedded script creates new dev and prod launch-template versions, after which a separately approved apply and controlled dev replacement prove reproducible joins.

**Tech Stack:** Bash, cloud-init, AWS CLI 2, AWS EC2 Auto Scaling and Systems Manager, Terraform 1.15.x, pytest, ShellCheck 0.10.0, kubeadm/Kubernetes 1.35.5, containerd 2.3.1, Calico 3.30.2.

## Global Constraints

- Preserve exactly seven trusted join fields: `kubeadm`, `join`, the Terraform-provided private IPv4 endpoint on port `6443`, `--token`, a valid kubeadm token, `--discovery-token-ca-cert-hash`, and one `sha256:` hash with 64 hexadecimal characters.
- Normalize horizontal space or tab separation only; reject empty, multiline, carriage-return, missing-field, extra-field, different-endpoint, and different-command input.
- Never use `eval`, `set -x`, shell interpretation, or raw command output.
- Never print the decrypted SSM value, token, CA hash, AWS response, or kubeadm arguments.
- Never run `aws ssm get-parameter --with-decryption` interactively during validation; only the worker bootstrap script may retrieve the runtime value.
- Preserve `MAX_ATTEMPTS=40`, the 30-second backoff cap, AWS CLI timeouts, the five-minute kubeadm timeout, and the two-minute reset timeout.
- Preserve EC2 private-DNS node names and the exact `stockai.io/environment=dev|prod` label and matching `NoSchedule` taint.
- Do not change the control plane, IAM, SSM parameter, token rotation, networking, container runtime, CNI, Kubernetes workloads, CI/CD, or Terraform outputs.
- Do not add PolyAI's prefix-only validation, `eval`, command tracing, non-expiring token, unbounded retry, or unconditional runtime restart.
- Do not apply any Terraform plan until its exact saved contents receive separate explicit approval.
- If a fresh worker still fails, stop after recording its sanitized category; do not broaden validation, retrieve the decrypted value manually, repeat replacements, or repair an ASG node by hand.
- T18B continues to own automated drain and stale-node deletion.

---

## File map

- `infra/cluster/join-worker.sh`: retrieve, validate, and execute the worker join command while keeping all secret material in memory.
- `tests/infra/test_cluster_bootstrap.py`: enforce the exact argument-array and non-disclosure contracts.
- `docs/runbooks/cluster-bootstrap.md`: explain accepted whitespace, sanitized categories, and live recovery checks.
- `docs/plan.md`: mark T18A Step 7 complete only after the controlled replacement succeeds.
- `docs/implementation-status.md`: replace the stale "no live AWS call" statement with evidence actually observed after acceptance.
- `infra/terraform/platform/t18a-worker-join-recovery.tfplan`: ignored, generated saved plan; never commit or upload it.

---

### Task 1: Validate exact join arguments and expose only safe categories

**Files:**
- Modify: `tests/infra/test_cluster_bootstrap.py:126-146`
- Modify: `infra/cluster/join-worker.sh:72-113`
- Modify: `docs/runbooks/cluster-bootstrap.md:49-66,124-154`

**Interfaces:**
- Consumes: the existing four script arguments, EC2 IMDSv2 private DNS, and the exact SSM `SecureString` value returned to the worker role.
- Produces: either a successful kubeadm join using a validated `join_args` Bash array or one final error containing the attempt count and one of the six fixed safe categories, with no secret value.

- [ ] **Step 1: Write the failing array-validation contract test**

Add this test immediately after `test_worker_join_rejects_shell_text_and_hard_binds_node_identity`:

```python
def test_worker_join_validates_exact_arguments_and_reports_safe_categories() -> None:
    script = _read(CLUSTER_ROOT / "join-worker.sh")

    assert 'last_failure_reason="ssm-read-failed"' in script
    assert "$'\\n'" in script
    assert "$'\\r'" in script
    assert "join_args=()" in script
    assert 'read -r -a join_args <<<"$join_command"' in script
    assert 'if ((${#join_args[@]} != 7)); then' in script
    assert '"${join_args[0]}" != "kubeadm"' in script
    assert '"${join_args[1]}" != "join"' in script
    assert '"${join_args[2]}" != "$expected_api_endpoint"' in script
    assert '"${join_args[3]}" != "--token"' in script
    assert '"${join_args[5]}" != "--discovery-token-ca-cert-hash"' in script
    assert "^[a-z0-9]{6}\\.[a-z0-9]{16}$" in script
    assert "^sha256:[[:xdigit:]]{64}$" in script

    for category in (
        "ssm-read-failed",
        "invalid-command-shape",
        "endpoint-mismatch",
        "invalid-token-format",
        "invalid-hash-format",
        "kubeadm-join-failed",
    ):
        assert f'last_failure_reason="{category}"' in script

    assert (
        'echo "worker join failed after ${MAX_ATTEMPTS} attempts: '
        '${last_failure_reason}" >&2'
    ) in script
    assert 'echo "$join_command"' not in script
    assert "eval" not in script
```

- [ ] **Step 2: Run the new test and verify the contract is red**

Run:

```bash
pytest tests/infra/test_cluster_bootstrap.py::test_worker_join_validates_exact_arguments_and_reports_safe_categories -v
```

Expected: FAIL on the missing `last_failure_reason`, exact array length, or categorical failure assertions. The existing whole-string expression must not satisfy the new contract.

- [ ] **Step 3: Replace the whole-string expression with the minimal exact array validator**

Replace `escaped_endpoint`, `join_pattern`, and the current retry loop/final error in `infra/cluster/join-worker.sh` with:

```bash
backoff_seconds=2
last_failure_reason="ssm-read-failed"

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
  join_command=""
  join_args=()

  if ! join_command="$(aws ssm get-parameter \
    --cli-connect-timeout 5 \
    --cli-read-timeout 10 \
    --region "$aws_region" \
    --name "$parameter_name" \
    --with-decryption \
    --query Parameter.Value \
    --output text 2>/dev/null)"; then
    last_failure_reason="ssm-read-failed"
  elif [[ -z "$join_command" || "$join_command" == *$'\n'* ||
    "$join_command" == *$'\r'* ]]; then
    last_failure_reason="invalid-command-shape"
  else
    read -r -a join_args <<<"$join_command"

    if ((${#join_args[@]} != 7)); then
      last_failure_reason="invalid-command-shape"
    elif [[ "${join_args[0]}" != "kubeadm" ||
      "${join_args[1]}" != "join" ||
      "${join_args[3]}" != "--token" ||
      "${join_args[5]}" != "--discovery-token-ca-cert-hash" ]]; then
      last_failure_reason="invalid-command-shape"
    elif [[ "${join_args[2]}" != "$expected_api_endpoint" ]]; then
      last_failure_reason="endpoint-mismatch"
    elif ! [[ "${join_args[4]}" =~ ^[a-z0-9]{6}\.[a-z0-9]{16}$ ]]; then
      last_failure_reason="invalid-token-format"
    elif ! [[ "${join_args[6]}" =~ ^sha256:[[:xdigit:]]{64}$ ]]; then
      last_failure_reason="invalid-hash-format"
    elif timeout 5m "${join_args[@]}" \
      --node-name "$private_dns" \
      --cri-socket "$CRI_SOCKET" \
      >/dev/null 2>&1; then
      unset join_command join_args
      exit 0
    else
      last_failure_reason="kubeadm-join-failed"
      timeout 2m kubeadm reset --force --cri-socket "$CRI_SOCKET" \
        >/dev/null 2>&1 || true
    fi
  fi

  unset join_command
  join_args=()
  if ((attempt < MAX_ATTEMPTS)); then
    sleep "$backoff_seconds"
    if ((backoff_seconds < MAX_BACKOFF_SECONDS)); then
      backoff_seconds=$((backoff_seconds * 2))
      if ((backoff_seconds > MAX_BACKOFF_SECONDS)); then
        backoff_seconds=$MAX_BACKOFF_SECONDS
      fi
    fi
  fi
done

echo "worker join failed after ${MAX_ATTEMPTS} attempts: ${last_failure_reason}" >&2
exit 1
```

Do not add a helper that accepts the token through process arguments, do not log per-attempt values, and do not restart containerd.

- [ ] **Step 4: Document the exact argument contract and safe categories**

In the bootstrap sequence, replace the current whole-command grammar paragraph with this behavior:

```markdown
Workers split the decrypted value into exactly seven Bash arguments. Horizontal
spaces and tabs may vary, but the command, private API endpoint, token flag and
format, CA-hash flag and format, and total argument count must match the fixed
contract. Empty, multiline, carriage-return, missing-field, and extra-field
values are rejected. The validated array is executed without shell evaluation.
```

Add these failure-diagnosis bullets without adding commands that decrypt the parameter:

```markdown
- `ssm-read-failed`: verify the exact worker role, parameter ARN, region, and AWS
  API reachability.
- `invalid-command-shape`, `endpoint-mismatch`, `invalid-token-format`, or
  `invalid-hash-format`: stop and inspect control-plane rotation configuration;
  never print or retrieve the value manually.
- `kubeadm-join-failed`: verify API reachability, clock, containerd, kubelet,
  Kubernetes versions, and sanitized service journals before designing another
  correction.
```

- [ ] **Step 5: Run the focused T18A test file**

Run:

```bash
pytest tests/infra/test_cluster_bootstrap.py -v
```

Expected: all eight T18A contract tests pass, including the new array-validation test and existing SSM Agent compatibility contract.

- [ ] **Step 6: Run Bash syntax and pinned ShellCheck**

Run:

```bash
bash -n infra/cluster/*.sh
docker run --rm \
  --volume "$PWD:/mnt:ro" \
  --workdir /mnt \
  koalaman/shellcheck:v0.10.0 \
  infra/cluster/*.sh
```

Expected: both commands exit `0`; ShellCheck reports no warnings. Use a locally installed ShellCheck 0.10.0 only if it is already available; do not install it as part of this task.

- [ ] **Step 7: Run Terraform and repository regressions**

Run:

```bash
terraform -chdir=infra/terraform/platform fmt -check
terraform -chdir=infra/terraform/platform validate
pytest tests/infra -v
make check
git diff --check
```

Expected: Terraform validation succeeds, the complete infrastructure suite passes, `make check` passes, and Git reports no whitespace errors. These commands must not contact or mutate AWS.

- [ ] **Step 8: Review the implementation diff for scope and secret safety**

Run:

```bash
git diff -- infra/cluster/join-worker.sh tests/infra/test_cluster_bootstrap.py docs/runbooks/cluster-bootstrap.md
git status --short
```

Expected: only the three declared files changed; the diff contains no token value, CA hash value, `eval`, `set -x`, IAM change, Terraform resource change, or unrelated edit.

- [ ] **Step 9: Commit the validated parser correction**

```bash
git add infra/cluster/join-worker.sh tests/infra/test_cluster_bootstrap.py docs/runbooks/cluster-bootstrap.md
git commit -m "fix(infra): validate worker join arguments"
```

---

### Task 2: Produce and review the non-destructive worker recovery plan

**Files:**
- Read: `infra/terraform/platform/terraform.tfvars`
- Generated and ignored: `infra/terraform/platform/t18a-worker-join-recovery.tfplan`

**Interfaces:**
- Consumes: the committed Task 1 script embedded by `module.compute.aws_launch_template.worker`, existing remote Terraform state, and approved account variables.
- Produces: one saved Terraform plan whose exact worker-only update scope is reviewed before any apply.

- [ ] **Step 1: Confirm caller, region, branch, commit, and clean source state**

Run:

```bash
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output table
aws configure get region
git status --short --branch
git log -3 --oneline
```

Expected: the approved AWS account, `us-east-1`, branch `feature/t18a-cluster-bootstrap`, the Task 1 commit at `HEAD`, and no uncommitted source changes.

- [ ] **Step 2: Re-run non-mutating Terraform checks against the initialized platform root**

Run:

```bash
terraform -chdir=infra/terraform/platform fmt -check
terraform -chdir=infra/terraform/platform validate
terraform -chdir=infra/terraform/platform state list
```

Expected: formatting and validation succeed; state still contains the existing control plane, two worker launch templates, two ASGs, SSM parameter, and existing platform resources.

- [ ] **Step 3: Create a fresh saved plan without forcing any replacement**

Run:

```bash
terraform -chdir=infra/terraform/platform plan \
  -out=t18a-worker-join-recovery.tfplan
terraform -chdir=infra/terraform/platform show \
  t18a-worker-join-recovery.tfplan
```

Expected reviewed scope:

- dev and prod worker launch templates receive new immutable versions because their embedded join script changed;
- dev and prod ASGs point to those versions and retain their approved capacity and instance-refresh settings;
- the control-plane EC2 instance is not replaced or modified;
- IAM, SSM, networking, edge, retained volumes, DynamoDB, Cognito, secrets, budgets, CNI, and Terraform outputs do not change;
- no EKS resource, SSH key pair, token, CA hash, or plaintext SSM value appears; and
- the plan contains no unrelated destruction or replacement.

- [ ] **Step 4: Stop for explicit plan approval**

Present the saved plan summary and exact resource actions. Do not run `terraform apply`, do not generate a second plan automatically, and do not use `-target`. The ignored `.tfplan` file stays local and is never committed or uploaded.

---

### Task 3: Apply the approved worker recovery and verify the healthy baseline

**Files:**
- Consumes locally: `infra/terraform/platform/t18a-worker-join-recovery.tfplan`
- No source file changes in this task.

**Interfaces:**
- Consumes: the exact Task 2 plan after separate explicit approval.
- Produces: healthy dev and prod Kubernetes workers using the corrected launch-template versions, plus recorded runtime evidence for the final replacement test.

- [ ] **Step 1: Apply only the exact approved saved plan**

After explicit approval, run:

```bash
terraform -chdir=infra/terraform/platform apply \
  t18a-worker-join-recovery.tfplan
```

Expected: apply succeeds and starts the worker ASG refreshes. If Terraform reports a stale plan, a changed action, or a control-plane replacement, stop and return to Task 2.

- [ ] **Step 2: Wait for both worker instance refreshes to succeed**

Run from the repository root:

```bash
STOCKAI_DEV_ASG="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_asg_name)"
STOCKAI_PROD_ASG="$(terraform -chdir=infra/terraform/platform output -raw prod_worker_asg_name)"

for stockai_asg in "$STOCKAI_DEV_ASG" "$STOCKAI_PROD_ASG"; do
  stockai_refresh_status=""
  for stockai_attempt in $(seq 1 90); do
    stockai_refresh_status="$(aws autoscaling describe-instance-refreshes \
      --region us-east-1 \
      --auto-scaling-group-name "$stockai_asg" \
      --max-records 1 \
      --query 'InstanceRefreshes[0].Status' \
      --output text)"
    printf '%s refresh=%s\n' "$stockai_asg" "$stockai_refresh_status"
    if [[ "$stockai_refresh_status" == "Successful" ]]; then
      break
    fi
    if [[ "$stockai_refresh_status" =~ ^(Failed|Cancelled|RollbackFailed|RollbackSuccessful)$ ]]; then
      exit 1
    fi
    sleep 10
  done
  [[ "$stockai_refresh_status" == "Successful" ]] || exit 1
done
```

Expected: both latest refreshes reach `Successful`. EC2 `InService` alone is not Kubernetes acceptance.

- [ ] **Step 3: Resolve the fresh worker IDs and wait for EC2 health**

Run:

```bash
STOCKAI_DEV_ID="$(aws autoscaling describe-auto-scaling-groups \
  --region us-east-1 \
  --auto-scaling-group-names "$STOCKAI_DEV_ASG" \
  --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService` && HealthStatus==`HEALTHY`]|[0].InstanceId' \
  --output text)"
STOCKAI_PROD_ID="$(aws autoscaling describe-auto-scaling-groups \
  --region us-east-1 \
  --auto-scaling-group-names "$STOCKAI_PROD_ASG" \
  --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService` && HealthStatus==`HEALTHY`]|[0].InstanceId' \
  --output text)"

printf 'dev=%s prod=%s\n' "$STOCKAI_DEV_ID" "$STOCKAI_PROD_ID"
aws ec2 wait instance-status-ok \
  --region us-east-1 \
  --instance-ids "$STOCKAI_DEV_ID" "$STOCKAI_PROD_ID"
```

Expected: both IDs are nonempty, different from the failed version 2 IDs, and pass EC2 status checks.

- [ ] **Step 4: Verify cloud-init and the join boundary on each fresh worker**

Open the dev worker session:

```bash
aws ssm start-session --region us-east-1 --target "$STOCKAI_DEV_ID"
```

Inside the dev session, run:

```bash
sudo cloud-init status --wait --long
sudo test -s /var/lib/stockai/node-install-complete
sudo test -s /etc/kubernetes/kubelet.conf
sudo systemctl is-active containerd kubelet
sudo grep -nE 'worker join failed|scripts_user' /var/log/cloud-init-output.log || true
```

Expected: cloud-init is `done` without errors, both files exist, both services are `active`, and the final grep prints nothing. Exit the dev session, then open the prod worker session:

```bash
aws ssm start-session --region us-east-1 --target "$STOCKAI_PROD_ID"
```

Inside the prod session, run:

```bash
sudo cloud-init status --wait --long
sudo test -s /var/lib/stockai/node-install-complete
sudo test -s /etc/kubernetes/kubelet.conf
sudo systemctl is-active containerd kubelet
sudo grep -nE 'worker join failed|scripts_user' /var/log/cloud-init-output.log || true
```

Expected: prod cloud-init is `done` without errors, both files exist, both services are `active`, and the final grep prints nothing. Exit the prod session. Do not inspect process arguments or retrieve the parameter.

- [ ] **Step 5: Verify both workers from the control plane**

Run locally:

```bash
STOCKAI_CP_ID="$(terraform -chdir=infra/terraform/platform output -raw control_plane_instance_id)"
aws ssm start-session --region us-east-1 --target "$STOCKAI_CP_ID"
```

Inside the control-plane session, run:

```bash
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o wide
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes \
  -o 'custom-columns=NAME:.metadata.name,ENV:.metadata.labels.stockai\.io/environment,TAINTS:.spec.taints'
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods \
  -n kube-system \
  -l k8s-app=calico-node \
  -o wide
```

Expected: control plane, dev worker, and prod worker are `Ready`; worker names equal their EC2 private DNS names; dev has only the dev StockAI label/taint; prod has only the prod StockAI label/taint; and one ready Calico node pod runs on every node.

- [ ] **Step 6: Verify worker role, ASG subnet, and Availability Zone**

After exiting the control-plane session, run:

```bash
STOCKAI_DEV_ROLE="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_role_name)"
STOCKAI_PROD_ROLE="$(terraform -chdir=infra/terraform/platform output -raw prod_worker_role_name)"
STOCKAI_DEV_AZ="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_az)"
STOCKAI_PROD_AZ="$(terraform -chdir=infra/terraform/platform output -raw prod_worker_az)"

aws autoscaling describe-auto-scaling-groups \
  --region us-east-1 \
  --auto-scaling-group-names "$STOCKAI_DEV_ASG" "$STOCKAI_PROD_ASG" \
  --query 'AutoScalingGroups[].{ASG:AutoScalingGroupName,Subnets:VPCZoneIdentifier}' \
  --output table

aws ec2 describe-instances \
  --region us-east-1 \
  --instance-ids "$STOCKAI_DEV_ID" "$STOCKAI_PROD_ID" \
  --query 'Reservations[].Instances[].{Id:InstanceId,PrivateDns:PrivateDnsName,Profile:IamInstanceProfile.Arn,Subnet:SubnetId,AZ:Placement.AvailabilityZone,LaunchTemplateVersion:LaunchTemplate.Version}' \
  --output table

printf 'expected dev role=%s az=%s\n' "$STOCKAI_DEV_ROLE" "$STOCKAI_DEV_AZ"
printf 'expected prod role=%s az=%s\n' "$STOCKAI_PROD_ROLE" "$STOCKAI_PROD_AZ"
```

Expected: each instance profile ARN ends with its matching environment role/profile name, each subnet appears in its matching ASG `VPCZoneIdentifier`, and each AZ equals the matching Terraform output.

- [ ] **Step 7: Prove no token-shaped value appears in user data, logs, journals, or Terraform output**

From the local shell, check both user-data payloads and Terraform output:

```bash
for stockai_instance_id in "$STOCKAI_DEV_ID" "$STOCKAI_PROD_ID"; do
  if aws ec2 describe-instance-attribute \
    --region us-east-1 \
    --instance-id "$stockai_instance_id" \
    --attribute userData \
    --query 'UserData.Value' \
    --output text |
    base64 --decode |
    grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}'; then
    printf 'potential secret pattern in user data for %s\n' "$stockai_instance_id" >&2
    exit 1
  fi
done

if terraform -chdir=infra/terraform/platform output -json |
  grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}'; then
  echo 'potential secret pattern in Terraform output' >&2
  exit 1
fi
```

Inside each worker SSM session, run:

```bash
if sudo grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}' \
  /var/log/cloud-init-output.log; then
  echo 'potential secret pattern in cloud-init output' >&2
  exit 1
fi

if sudo journalctl --since=-2h --no-pager |
  grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}'; then
  echo 'potential secret pattern in journal' >&2
  exit 1
fi
```

Expected: every check exits `0` without printing a potential-secret warning. Do not display matching lines and do not use `--with-decryption`.

- [ ] **Step 8: Stop if the baseline is not fully healthy**

Do not proceed to Task 4 unless both workers are Kubernetes `Ready` with correct identity and scheduling metadata and every non-disclosure check passes. On failure, preserve only the sanitized category and return to design review; do not replace another worker.

---

### Task 4: Complete the controlled dev ASG replacement acceptance

**Files:**
- Modify after successful live acceptance: `docs/plan.md:1499-1507`
- Modify after successful live acceptance: `docs/implementation-status.md:35`

**Interfaces:**
- Consumes: the healthy Task 3 dev worker and its ASG, current Terraform outputs, and control-plane administrative kubeconfig.
- Produces: a second fresh dev worker that independently proves replacement bootstrap, plus committed T18A completion evidence.

- [ ] **Step 1: Record and verify the healthy dev instance before termination**

Run locally:

```bash
STOCKAI_DEV_ASG="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_asg_name)"
STOCKAI_DEV_ROLE="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_role_name)"
STOCKAI_DEV_AZ="$(terraform -chdir=infra/terraform/platform output -raw dev_worker_az)"
STOCKAI_CP_ID="$(terraform -chdir=infra/terraform/platform output -raw control_plane_instance_id)"
STOCKAI_OLD_DEV_ID="$(aws autoscaling describe-auto-scaling-groups \
  --region us-east-1 \
  --auto-scaling-group-names "$STOCKAI_DEV_ASG" \
  --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService` && HealthStatus==`HEALTHY`]|[0].InstanceId' \
  --output text)"
STOCKAI_OLD_DEV_DNS="$(aws ec2 describe-instances \
  --region us-east-1 \
  --instance-ids "$STOCKAI_OLD_DEV_ID" \
  --query 'Reservations[0].Instances[0].PrivateDnsName' \
  --output text)"
printf 'old-dev-id=%s old-dev-dns=%s\n' "$STOCKAI_OLD_DEV_ID" "$STOCKAI_OLD_DEV_DNS"
```

Open the control-plane session:

```bash
aws ssm start-session --region us-east-1 --target "$STOCKAI_CP_ID"
```

Inside it, list the current nodes:

```bash
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o wide
```

Expected: the row whose `NAME` equals the exact `old-dev-dns` printed by the local command is `Ready`. The EC2 instance belongs to `STOCKAI_DEV_ASG` and is not the prod instance. Exit the session before continuing.

- [ ] **Step 2: Terminate the verified dev member through its ASG**

Run locally:

```bash
aws autoscaling terminate-instance-in-auto-scaling-group \
  --region us-east-1 \
  --instance-id "$STOCKAI_OLD_DEV_ID" \
  --no-should-decrement-desired-capacity
```

Expected: AWS reports the termination activity and keeps desired capacity unchanged. Do not stop the instance and do not decrement capacity.

- [ ] **Step 3: Resolve and wait for the replacement dev instance**

Run:

```bash
STOCKAI_NEW_DEV_ID=""
for stockai_attempt in $(seq 1 90); do
  STOCKAI_NEW_DEV_ID="$(aws autoscaling describe-auto-scaling-groups \
    --region us-east-1 \
    --auto-scaling-group-names "$STOCKAI_DEV_ASG" \
    --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService` && HealthStatus==`HEALTHY`]|[0].InstanceId' \
    --output text)"
  if [[ -n "$STOCKAI_NEW_DEV_ID" && "$STOCKAI_NEW_DEV_ID" != "None" &&
    "$STOCKAI_NEW_DEV_ID" != "$STOCKAI_OLD_DEV_ID" ]]; then
    break
  fi
  sleep 10
done

[[ -n "$STOCKAI_NEW_DEV_ID" && "$STOCKAI_NEW_DEV_ID" != "None" &&
  "$STOCKAI_NEW_DEV_ID" != "$STOCKAI_OLD_DEV_ID" ]] || exit 1

aws ec2 wait instance-status-ok \
  --region us-east-1 \
  --instance-ids "$STOCKAI_NEW_DEV_ID"

STOCKAI_NEW_DEV_DNS="$(aws ec2 describe-instances \
  --region us-east-1 \
  --instance-ids "$STOCKAI_NEW_DEV_ID" \
  --query 'Reservations[0].Instances[0].PrivateDnsName' \
  --output text)"
printf 'new-dev-id=%s new-dev-dns=%s\n' "$STOCKAI_NEW_DEV_ID" "$STOCKAI_NEW_DEV_DNS"
```

Expected: a different healthy instance appears in the same dev ASG with a new private DNS name.

- [ ] **Step 4: Verify replacement cloud-init and Kubernetes readiness**

Open the replacement worker session:

```bash
aws ssm start-session --region us-east-1 --target "$STOCKAI_NEW_DEV_ID"
```

Inside it, run:

```bash
sudo cloud-init status --wait --long
sudo test -s /var/lib/stockai/node-install-complete
sudo test -s /etc/kubernetes/kubelet.conf
sudo systemctl is-active containerd kubelet
sudo grep -nE 'worker join failed|scripts_user' /var/log/cloud-init-output.log || true
```

Expected: cloud-init completes without errors, both marker/config files exist, both services are active, and no join failure appears.

Exit the worker session, open a control-plane session, and run:

```bash
aws ssm start-session --region us-east-1 --target "$STOCKAI_CP_ID"
```

Inside the control-plane session:

```bash
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o wide
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes \
  -o 'custom-columns=NAME:.metadata.name,ENV:.metadata.labels.stockai\.io/environment,TAINTS:.spec.taints'
sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get pods \
  -n kube-system \
  -l k8s-app=calico-node \
  -o wide
```

Expected: the row whose `NAME` equals the exact `new-dev-dns` printed in Step 3 reaches `Ready`, reports `ENV=dev`, has only `stockai.io/environment=dev:NoSchedule`, has no prod value, and has one `1/1 Running` Calico node pod. Exit the session before continuing.

- [ ] **Step 5: Verify replacement AWS placement and role**

Run locally:

```bash
aws autoscaling describe-auto-scaling-instances \
  --region us-east-1 \
  --instance-ids "$STOCKAI_NEW_DEV_ID" \
  --query 'AutoScalingInstances[0].{ASG:AutoScalingGroupName,State:LifecycleState,Health:HealthStatus,AZ:AvailabilityZone,LaunchTemplateVersion:LaunchTemplate.Version}' \
  --output table

aws ec2 describe-instances \
  --region us-east-1 \
  --instance-ids "$STOCKAI_NEW_DEV_ID" \
  --query 'Reservations[0].Instances[0].{PrivateDns:PrivateDnsName,Profile:IamInstanceProfile.Arn,Subnet:SubnetId,AZ:Placement.AvailabilityZone}' \
  --output table
```

Expected: ASG equals `STOCKAI_DEV_ASG`, lifecycle is `InService`, health is `HEALTHY`, profile ends with `STOCKAI_DEV_ROLE`, subnet matches the dev ASG subnet, AZ equals `STOCKAI_DEV_AZ`, and the launch-template version matches the corrected baseline.

- [ ] **Step 6: Repeat the replacement non-disclosure checks**

Run locally:

```bash
if aws ec2 describe-instance-attribute \
  --region us-east-1 \
  --instance-id "$STOCKAI_NEW_DEV_ID" \
  --attribute userData \
  --query 'UserData.Value' \
  --output text |
  base64 --decode |
  grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}'; then
  echo 'potential secret pattern in replacement user data' >&2
  exit 1
fi

if terraform -chdir=infra/terraform/platform output -json |
  grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}'; then
  echo 'potential secret pattern in Terraform output' >&2
  exit 1
fi

aws ssm start-session --region us-east-1 --target "$STOCKAI_NEW_DEV_ID"
```

Inside the replacement worker session, run:

```bash
if sudo grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}' \
  /var/log/cloud-init-output.log; then
  echo 'potential secret pattern in cloud-init output' >&2
  exit 1
fi

if sudo journalctl --since=-2h --no-pager |
  grep -Eq '[a-z0-9]{6}\.[a-z0-9]{16}|sha256:[[:xdigit:]]{64}'; then
  echo 'potential secret pattern in journal' >&2
  exit 1
fi
```

Expected: no token-shaped or hash-shaped value appears. Do not display matching content and do not retrieve the parameter.

- [ ] **Step 7: Record T18A completion without claiming T18B behavior**

In `docs/plan.md`, change only T18A Step 7 from `[ ]` to `[x]`.

In `docs/implementation-status.md`, replace the T18A status and stale "no live AWS call" text with evidence actually observed in Tasks 1, 3, and 4:

- local Bash, ShellCheck, focused infrastructure, full infrastructure, Terraform, and repository check outcomes using their actual counts;
- the healthy control plane and dev/prod baseline;
- the second automatic dev ASG replacement using private DNS, exact dev label/taint, dev role/subnet/AZ, readiness, and healthy Calico; and
- successful user-data, cloud-init, journal, and Terraform-output non-disclosure checks.

Keep T18B drain/stale-node automation explicitly incomplete. Do not claim the old Kubernetes Node was automatically removed.

- [ ] **Step 8: Review and commit only the acceptance documentation**

Run:

```bash
git diff --check
git diff -- docs/plan.md docs/implementation-status.md
git status --short
```

Expected: only the T18A checkbox and evidence row changed; every statement corresponds to a command actually run successfully.

Commit:

```bash
git add docs/plan.md docs/implementation-status.md
git commit -m "docs(infra): record T18a replacement acceptance"
```

The ignored Terraform plan and any local command output remain uncommitted.
