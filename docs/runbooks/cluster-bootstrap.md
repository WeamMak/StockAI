# Cluster bootstrap and worker join runbook

This runbook covers T18A's reproducible kubeadm bootstrap for the single
StockAI cluster. Terraform creates the encrypted join parameter and exact IAM
permissions; EC2 user data installs the pinned node software, initializes the
control plane, installs Calico, and joins each environment worker.

No command in this runbook prints or retrieves the decrypted join command.

## Fixed contract

- Kubernetes packages: `1.35.5-1.1` from the Kubernetes `v1.35` repository.
- containerd: `2.3.1`; runc: `1.5.1`; CNI plugins: `v1.9.1`.
- Calico: `v3.30.2`, matching the course tutorial, with pod CIDR
  `192.168.0.0/16`.
- Join parameter: `/stockai/<cluster-name>/kubeadm/join-command`, stored as an
  SSM `SecureString`.
- Join tokens live for 24 hours and rotate every 12 hours.
- Worker node names are their EC2 private DNS names. Workers carry exactly one
  `stockai.io/environment=dev|prod` label and matching `NoSchedule` taint.
- The approved Ubuntu AMI supplies an active Amazon SSM Agent through either
  the Debian-package systemd unit or the AWS-published Snap service.
- `/etc/kubernetes/admin.conf` remains root-owned with mode `0600`. Business
  workloads must not remove or tolerate the control-plane taint.

## Before an authorized apply

1. Use an approved x86-64 Ubuntu AMI that includes an active
   `amazon-ssm-agent` Debian service or AWS Snap.
2. Confirm the account ID, administrator CIDR, AMI ID, region, cost, and EC2
   quota in the reviewed platform plan.
3. Confirm the plan contains one `SecureString`, three exact inline policies,
   no EKS resources or policies, and no plaintext token in any output or user
   data.
4. Run the local checks:

   ```bash
   terraform -chdir=infra/terraform/platform fmt -check
   terraform -chdir=infra/terraform/platform init -backend=false
   terraform -chdir=infra/terraform/platform validate
   shellcheck infra/cluster/*.sh
   pytest tests/infra/test_cluster_bootstrap.py -v
   ```

Do not apply until the infrastructure plan and expected replacement actions
have explicit approval. Terraform owns all AWS resource creation; do not create
or repair these resources in the AWS Console.

## Bootstrap sequence

On an approved fresh apply, the control plane and worker ASGs can start in any
order:

1. Every node installs the pinned runtime and kubeadm packages.
2. The control plane initializes against its private IP and private DNS name.
3. The control plane starts the rotation service, which replaces Terraform's
   non-secret placeholder with a finite encrypted join command.
4. The control plane applies the pinned Calico manifest.
5. Workers poll only the exact parameter with bounded backoff. They split the
   decrypted value into exactly seven Bash arguments. Horizontal spaces and
   tabs may vary, but the command, private API endpoint, token flag and format,
   CA-hash flag and format, and total argument count must match the fixed
   contract. Empty, multiline, carriage-return, missing-field, and extra-field
   values are rejected. The validated array is executed without shell
   evaluation and joins with the private DNS name and environment scheduling
   identity.

The bootstrap scripts write only version markers and sanitized errors. They do
not print the SSM value, join command, token, or CA hash.

## Verify cluster health

Use SSM Session Manager or the already restricted administrative path to run
these commands on the control plane:

```bash
sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide
sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pods -n kube-system
sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes \
  -o custom-columns=NAME:.metadata.name,ENV:.metadata.labels.stockai\.io/environment,TAINTS:.spec.taints
sudo systemctl status kubeadm-token-rotation.timer --no-pager
sudo systemctl list-timers kubeadm-token-rotation.timer --no-pager
```

Expected results:

- the control plane and active workers become `Ready`;
- each worker name equals its EC2 private DNS name;
- dev and prod workers have only their matching StockAI environment value;
- the control plane retains `node-role.kubernetes.io/control-plane:NoSchedule`;
- Calico node pods are ready and CoreDNS becomes ready;
- the rotation timer has a next run within 12 hours.

Check only parameter metadata, never its value:

```bash
aws ssm describe-parameters \
  --region us-east-1 \
  --parameter-filters \
  'Key=Name,Option=Equals,Values=/stockai/weam-stockai/kubeadm/join-command' \
  --query 'Parameters[0].{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}'
```

## Controlled dev replacement test

Perform this only after the platform apply is authorized and the dev worker is
healthy. Record the current dev instance ID and node name, then terminate the
instance through its ASG. Do not stop an ASG-managed instance.

T18A acceptance requires the replacement to:

- use the dev instance profile and dev subnet/AZ;
- join automatically without changing Terraform state or user data;
- appear under its EC2 private DNS name;
- carry the dev label and taint and no prod value;
- become `Ready` with Calico healthy; and
- leave no decrypted parameter value in cloud-init output or the system journal.

T18B adds bounded drain and stale-node deletion. Until T18B is implemented,
remove an old confirmed `NotReady` node only after matching it to the terminated
EC2 instance's private DNS name:

```bash
sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl delete node <verified-old-private-dns>
```

## Failure diagnosis

Inspect sanitized status without using `set -x` or running `aws ssm
get-parameter --with-decryption` interactively:

```bash
sudo systemctl status containerd kubelet --no-pager
sudo systemctl status amazon-ssm-agent.service --no-pager || \
  sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service --no-pager
sudo journalctl -u cloud-final -u kubelet -u containerd --since=-30m --no-pager
sudo systemctl status kubeadm-token-rotation.service --no-pager
sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pods -n kube-system -o wide
```

- Placeholder remains: verify the control-plane role, SSM agent, initialized
  admin kubeconfig, and rotation service.
- A missing node-install marker with an active Snap agent means the instance
  received a bootstrap payload from before Snap packaging was supported;
  replace the instance with the corrected payload instead of repairing it
  manually.
- `ssm-read-failed`: verify the exact worker role, parameter ARN, region, and AWS
  API reachability.
- `invalid-command-shape`, `endpoint-mismatch`, `invalid-token-format`, or
  `invalid-hash-format`: stop and inspect control-plane rotation configuration;
  never print or retrieve the value manually.
- `kubeadm-join-failed`: verify API reachability, clock, containerd, kubelet,
  Kubernetes versions, and sanitized service journals before designing another
  correction.
- Node exists but is not Ready: verify Calico pods, kernel modules, forwarding,
  and the pod CIDR.
- A partial join is retried: the worker script performs a bounded
  `kubeadm reset` before the next validated attempt. Replacing the worker is the
  recovery path after the bounded attempt is exhausted.

Never paste decrypted join material into a terminal transcript, issue, log, or
chat. If disclosure is suspected, run the rotation service on the control plane
and allow the previous token to expire within its 24-hour TTL.
