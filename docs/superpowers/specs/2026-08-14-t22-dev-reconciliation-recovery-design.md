# T22 Dev Reconciliation Recovery Design

## Context

`[Observed runtime evidence]` Argo CD successfully tracks the `dev` branch and
applies the `stockai-dev` Application, but the Application is `OutOfSync` and
`Degraded` rather than healthy.

`[Observed runtime evidence]` The three bound dev PersistentVolumes were first
created with the sample handles `vol-dev-odoo`, `vol-dev-pg`, and
`vol-dev-prom`. Git now contains the reviewed Terraform-created EBS volume IDs,
but `spec.csi.volumeHandle` is immutable, so Argo cannot update the existing PV
objects. None of the three volumes has ever mounted successfully or received
application data. Their reclaim policy is `Retain`.

`[Observed runtime evidence]` The namespace-scoped External Secrets controller
exits while discovering Kubernetes API resources because
`https://10.96.0.1:443/api` times out. Its live NetworkPolicy already permits
TCP 443 and the post-DNAT API port 6443. The cluster uses the fixed Calico pod
CIDR `192.168.0.0/16`, while the control-plane security group has no CIDR rule
allowing those routed pod addresses to TCP 6443.

`[Evidence-backed inference]` The remaining External Secrets failure is at the
AWS control-plane firewall boundary rather than the namespace NetworkPolicy.
The selected rule is narrow and directly testable; if the live timeout remains
after its reviewed apply, recovery stops rather than adding host networking or
broad access.

`[Observed runtime evidence]` Loki reaches configuration validation but exits
because retention is enabled without
`compactor.delete-request-store`. The existing design intentionally retains
logs in the environment-prefixed S3 store.

## Selected approach

`[Project decision]` Use one narrow infrastructure correction, one Loki
configuration correction, and one authorized initial-deployment cleanup:

1. Add control-plane security-group ingress for TCP 6443 from only the fixed
   pod CIDR `192.168.0.0/16`. Keep the existing administrator and worker-node
   rules and the External Secrets NetworkPolicy unchanged.
2. Configure Loki `compactor.delete_request_store` as `s3`; do not disable
   retention or introduce another storage backend.
3. After Git contains the real EBS coordinates, temporarily pause automated
   reconciliation for `stockai-dev`, remove only the affected dev workloads,
   their three never-mounted PVCs, and the three invalid PV objects, then
   restore automated reconciliation. Argo recreates the workloads, claims, and
   PVs from Git with the real volume handles.

The cleanup is a one-time operator recovery through the existing SSM access
path. It is not added to GitHub Actions and does not become normal deployment
behavior.

## Alternatives considered

- `[Rejected project decision]` Rename the PVs, PVCs, and every workload claim
  reference. This would avoid one-time deletion but permanently complicate the
  shared base and environment overlays solely to recover three objects created
  from sample coordinates.
- `[Rejected project decision]` Run External Secrets with `hostNetwork: true`
  or on the control plane. Host networking weakens namespace isolation, while
  control-plane placement would use the wrong environment IAM role.
- `[Rejected project decision]` Broaden control-plane ingress beyond the pod
  CIDR or allow all External Secrets egress. Neither is required by the
  observed API path.
- `[Rejected project decision]` Disable Loki retention. This would hide the
  validation error by removing an approved observability requirement.
- `[Rejected project decision]` Apply replacement PV manifests manually. The
  operator removes only invalid objects; Argo remains the workload deployment
  authority and recreates desired state.

## Components and data flow

Terraform remains the authority for the control-plane security group. A saved
platform plan must show only the new pod-CIDR API ingress rule, or expected
no-op effects in already synchronized roots. The protected provision workflow
applies the reviewed plan; GitHub Actions still runs no `kubectl` command.

External Secrets remains on the dev worker and uses its existing service
account, namespace-scoped RBAC, controller class, NetworkPolicy, and worker IAM
role. Its Kubernetes API traffic flows from the Calico pod CIDR to the
control-plane API on TCP 6443. After startup, it reads only the exact
Terraform-synchronized dev Secrets Manager ARNs and materializes the six
namespace Secrets.

Loki continues to use the reviewed shared bucket, `dev/` object prefix, local
bounded scratch space, and existing retention period. Delete-request metadata
uses the same S3 object store required by the compactor.

During storage recovery, automated Argo reconciliation is paused only long
enough to prevent deleted workloads from racing with PVC/PV cleanup. The Odoo,
PostgreSQL, and Prometheus workloads and the finite Odoo bootstrap Job are
removed before their claims. The three PVCs and invalid PVs are then removed
and observed absent. Restoring automated reconciliation lets Argo create the
real PVs and claims before the dependent workloads become healthy.

## Safety and failure handling

- Confirm the three Git volume handles are real `vol-` identifiers, exist in
  AWS, and match the dev worker Availability Zone before cleanup.
- Confirm no `VolumeAttachment` exists for the invalid handles and that the
  affected pods have never mounted them.
- Delete no EBS volume, snapshot, Terraform resource, namespace, unrelated
  workload, or production object.
- Stop if a PVC/PV deletion does not terminate cleanly; do not strip finalizers
  without separate evidence and approval.
- Stop if the reviewed Terraform plan contains a replacement, deletion, or an
  unrelated network change.
- Stop if External Secrets still reports Kubernetes API timeout after the
  narrow rule is live; do not fall back to host networking or broader ingress.
- Keep Argo automated prune and self-heal enabled after recovery.

## Tests and verification

Test-first implementation will add or update contracts that require:

- a control-plane TCP 6443 ingress rule scoped exactly to
  `192.168.0.0/16`, without broadening worker or administrator access;
- the existing External Secrets egress ports and namespace isolation to remain
  intact;
- Loki retention to use `delete_request_store: s3` with its existing S3
  storage and environment prefix; and
- the recovery documentation to name only the three dev PV/PVC sets and to
  preserve the `Retain` AWS-volume boundary.

Run the focused Terraform/network and Kubernetes render tests, Terraform
formatting and validation, `make kubernetes-validate`, relevant repository
checks, and `git diff --check`.

Live acceptance requires:

- the platform plan/apply contains the narrow API rule and no unrelated
  infrastructure mutation;
- all three recreated PVs expose the reviewed real EBS handles and all three
  PVCs are `Bound`;
- `aws-secrets-manager` and all six ExternalSecrets report `Ready=True`;
- Loki, PostgreSQL, Odoo, Prometheus, Grafana, API, MCP, and the remaining dev
  workloads converge without configuration, attachment, or secret errors; and
- Argo reports `stockai-dev` as `Synced` and `Healthy`.

## Scope boundary

This is a T22 live-reconciliation recovery. It does not rebuild or promote
images, change `make promote-dev`, start T23 or T24, deploy production, add an
AWS service, change secret values, or add routine imperative deployment to
GitHub Actions.
