# T18A Worker Join Validation Recovery Design

## Context

`[Observed runtime evidence]` The authorized T18A recovery apply produced a
healthy Kubernetes 1.35.5 control plane at `10.0.1.191`. Its admin kubeconfig,
node-install marker, control-plane marker, Calico pods, CoreDNS pods,
containerd, kubelet, and token-rotation timer are healthy.

`[Observed runtime evidence]` Fresh dev and prod launch-template version 2
instances became EC2 healthy and `InService`, but neither registered as a
Kubernetes Node. The dev worker completed the pinned node installation and
received the correct API endpoint `10.0.1.191:6443`. That endpoint was
reachable from the worker.

`[Observed runtime evidence]` The dev and prod worker roles repeatedly called
the exact SSM parameter with decryption. CloudTrail recorded no SSM or KMS
error. The parameter is a version 2 `SecureString` updated by the new control
plane. The worker spent its retry period in the join loop, produced no kubelet
journal entry, then failed with only the existing sanitized message:
`worker could not obtain and use a valid kubeadm join command`.

`[Reference implementation evidence]` The PolyAI project has successfully
joined a real worker using the same broad SSM/private-DNS/kubeadm flow. Its
worker accepts any value beginning with `kubeadm join` and executes it with
`eval`. This comparison strengthens the command-validation boundary as the
leading StockAI failure seam, but PolyAI's tracing, `eval`, non-expiring token,
and unbounded loop are incompatible with the approved StockAI security and
reliability contracts.

`[Assumption to validate]` Because the decrypted value has deliberately not
been inspected, the evidence does not prove whether the current rejection is a
harmless text-format difference or an immediate pre-kubelet kubeadm failure.
The selected approach therefore both accepts semantically identical horizontal
whitespace and makes the remaining failure boundary observable without
disclosing the value.

## Selected approach

`[Project decision]` Replace the monolithic whole-string regular expression in
`infra/cluster/join-worker.sh` with exact argument-by-argument validation.

The worker will:

1. Retrieve the same exact SSM `SecureString` value using its existing role.
2. Reject an empty value or any value containing a newline or carriage return.
3. Split the value into a Bash argument array without shell evaluation.
4. Require exactly seven arguments in this order:
   `kubeadm`, `join`, the expected private API endpoint, `--token`, a valid
   kubeadm token, `--discovery-token-ca-cert-hash`, and a valid SHA-256 hash.
5. Append only the existing trusted private-DNS node name and containerd CRI
   socket arguments.
6. Execute the validated array without `eval`.

Argument parsing may normalize harmless horizontal whitespace, but it will not
accept another endpoint, extra arguments, missing arguments, multiline input,
or different command semantics.

## Alternatives considered

- `[Rejected project decision]` Add diagnostics around the current monolithic
  expression without changing validation. This would preserve behavior but
  likely require one replacement to diagnose the mismatch and a second
  replacement to correct it.
- `[Rejected project decision]` Copy PolyAI's `kubeadm join` prefix check and
  `eval`. This is known to work operationally, but it permits additional shell
  content, weakens endpoint and credential-shape validation, and risks token
  disclosure.
- `[Rejected project decision]` Run a one-time manual join or diagnostic using
  decrypted parameter material. This would bypass the reproducible ASG
  bootstrap contract and create an unnecessary secret-handling risk.

## Components and data flow

Only the worker join boundary changes. The existing control plane, SSM
parameter, IAM policies, token rotation, worker user-data template, private-DNS
node identity, label and taint contract, containerd runtime, Calico resources,
networking, and Terraform outputs remain unchanged.

The changed script remains embedded in both worker launch templates. Terraform
therefore creates new immutable dev and prod launch-template versions and
updates the ASGs to use them. No control-plane replacement is required by this
recovery.

The worker still exits idempotently when
`/etc/kubernetes/kubelet.conf` already exists. A successful fresh worker join
is proved by Kubernetes Node registration and readiness rather than a manual
node repair.

## Error handling and secret safety

The existing 40-attempt bounded retry loop, capped backoff, AWS CLI timeouts,
five-minute kubeadm timeout, and bounded reset remain.

Each failed attempt records only a categorical reason in memory, such as:

- `ssm-read-failed`;
- `invalid-command-shape`;
- `endpoint-mismatch`;
- `invalid-token-format`;
- `invalid-hash-format`; or
- `kubeadm-join-failed`.

The script emits only the last category and attempt count after exhausting its
retries. It never emits the decrypted command, endpoint received from SSM,
token, CA hash, AWS response, or kubeadm arguments. Kubeadm output remains
suppressed, sensitive variables are unset after use, and command tracing stays
disabled.

After an actual kubeadm failure, the script retains its bounded `kubeadm reset`
before retrying. It will not add PolyAI's container-runtime restart because the
current evidence does not identify containerd as the failure and the smallest
approved correction should not introduce an unrelated recovery action.

## Tests

The existing T18A contract suite will be extended to require:

- Bash array parsing with exactly seven arguments;
- independent validation of command, endpoint, flags, token, and hash fields;
- explicit multiline, carriage-return, and extra-argument rejection;
- array execution without `eval`;
- bounded retries, kubeadm timeout, and reset behavior remaining intact; and
- sanitized failure categories with no command, token, hash, or AWS response
  output.

Before a live plan, run Bash syntax checks, ShellCheck, the focused T18A tests,
the complete infrastructure suite, Terraform formatting and validation, the
repository check suite, and `git diff --check`.

## Controlled rollout and acceptance

After the implementation passes review:

1. Produce a fresh saved Terraform plan.
2. Confirm that it updates only the worker launch-template and ASG path, with
   no control-plane, IAM, SSM, networking, CNI, retained-data, or unrelated
   replacement.
3. Obtain explicit authorization before applying the reviewed plan.
4. Apply the exact saved plan and verify fresh dev and prod workers join under
   their EC2 private DNS names, become `Ready`, receive only their matching
   environment label and `NoSchedule` taint, use the correct role/subnet/AZ,
   and run healthy Calico.
5. Inspect user data, cloud-init output, relevant journals, and Terraform
   outputs for disclosure without manually retrieving the decrypted parameter.
6. From the healthy baseline, terminate the confirmed dev instance through its
   ASG without decrementing desired capacity. Verify the second fresh dev
   worker satisfies the same identity, scheduling, role, network, readiness,
   Calico, and non-disclosure checks.
7. Mark T18A Step 7 complete only after the real controlled replacement passes.

If the plan includes a control-plane replacement or any unrelated destructive
action, stop and investigate rather than applying it.

If a fresh worker still fails, stop after recording its sanitized category. Do
not repeat replacements, broaden validation, retrieve the decrypted value
manually, or add PolyAI's `eval` path. A remaining semantic mismatch or
`kubeadm-join-failed` category is new evidence for a separately reviewed
correction.

## Scope boundary

This is a focused T18A worker-bootstrap correction. T18B still owns automated
drain and stale-node deletion. This recovery does not add SSH, EKS, new AWS
services, new IAM permissions, Kubernetes workload changes, CNI changes,
CI/CD changes, or manual node repair.
