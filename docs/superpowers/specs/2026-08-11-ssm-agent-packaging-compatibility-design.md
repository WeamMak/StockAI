# SSM Agent Packaging Compatibility Recovery Design

## Context

`[Observed runtime evidence]` The first authorized T18A platform apply used an
approved x86-64 Ubuntu AMI whose Amazon SSM Agent is installed and active as a
Snap. Session Manager successfully opened a control-plane shell through
`snap.amazon-ssm-agent.amazon-ssm-agent.service`.

`[Observed runtime evidence]` `infra/cluster/install-node.sh` currently accepts
only the Debian-package unit `amazon-ssm-agent.service`. It therefore exited
after installing Kubernetes and containerd but before writing the node-install
marker or running `kubeadm init`. `/etc/kubernetes/admin.conf` was never
created, and the worker nodes failed at the same installation boundary.

`[Existing project decision]` StockAI uses SSM rather than managed SSH key
pairs for node administration and later lifecycle automation. This recovery
does not change that access architecture.

## Selected approach

`[Project decision]` The node installer will support both valid Ubuntu Amazon
SSM Agent packaging forms:

1. If `amazon-ssm-agent.service` exists, enable and start that systemd unit.
2. Otherwise, if the `amazon-ssm-agent` Snap and its generated systemd service
   exist, enable and start the Snap service using the Snap-supported command.
3. Fail closed when neither form exists or when the selected service does not
   become active.

The installer will continue only after an active agent is confirmed and will
write the existing node-install marker at the same final boundary. It will not
install a second SSM Agent, change the AMI, introduce SSH keys, or weaken the
requirement that the approved AMI provide the agent.

## Alternatives considered

- `[Rejected project decision]` Support only the Snap form. This would fix the
  observed AMI but unnecessarily reject approved Ubuntu images using the
  Debian package.
- `[Rejected project decision]` Replace the AMI or install a second Agent. The
  existing Agent is current and healthy, so replacement adds image coupling,
  duplicate software, and more failure modes without business value.
- `[Rejected project decision]` Repair the running nodes manually. That would
  bypass the Terraform/user-data contract and would not prove that a fresh ASG
  replacement can bootstrap reproducibly.

## Components and data flow

Only the node-install boundary changes. EC2 user data still embeds and invokes
the same installer. The installer detects one approved service form, starts
it, verifies active state, and proceeds to the marker. The existing control
plane initialization, encrypted SSM join-command rotation, strict worker
command validation, and Calico installation remain unchanged.

No credentials, decrypted join commands, tokens, or new Terraform outputs are
introduced. Both service paths retain sanitized failures.

## Tests

The T18A infrastructure contract tests will require:

- a Debian-package service path;
- a Snap-package service path;
- an active-service assertion before successful installation;
- fail-closed behavior when no supported Agent form is present; and
- continued absence of SSH key configuration and EKS permissions.

ShellCheck, Bash syntax validation, the focused T18A tests, the full
infrastructure suite, Terraform validation, and the repository check suite must
pass before another live plan.

## Controlled recovery

The failed instances will not be repaired manually. After the compatibility
change passes review:

1. Create a fresh platform plan that explicitly replaces
   `module.compute.aws_instance.control_plane`, because cloud-init user data is
   a once-per-instance bootstrap and the failed instance has no admin
   kubeconfig.
2. Review the plan for the intended control-plane replacement, updated worker
   launch-template versions, ASG refresh behavior, unchanged least-privilege
   IAM/SSM contracts, and no unrelated replacement.
3. Obtain separate authorization for the destructive replacement apply.
4. Apply the reviewed plan and verify the control plane becomes `Ready`, the
   rotation timer updates only the exact SecureString parameter, and fresh dev
   and prod workers join under private-DNS node names with their exact
   environment labels, taints, roles, subnets, and Availability Zones.
5. Repeat the controlled dev ASG replacement acceptance test and check that no
   decrypted token appears in user data, Terraform output, cloud-init output,
   or the system journal.

If the reviewed plan does not refresh a failed worker automatically, replace
that confirmed ASG member through the ASG without decrementing desired
capacity, after the new control plane is healthy. Never stop or manually patch
an ASG-managed worker.

## Scope boundary

This is a T18A bootstrap compatibility correction. T18B still owns automated
drain and stale-node cleanup. No application workload, Kubernetes manifest,
networking, IAM permission, secret design, or CI/CD behavior changes in this
recovery.
