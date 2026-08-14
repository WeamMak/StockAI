# T23 Argo CD/HPA Replica Ownership Correction

## Status and scope

This is a bounded operational correction required for T23 exact-release
validation. The live dev application is healthy and runs the expected image
digests, but Argo CD and the Horizontal Pod Autoscaler repeatedly write
different values to `Deployment.spec.replicas`. That loop keeps the
application mostly `OutOfSync` and repeatedly creates and removes an API pod.

The correction changes only the dev Argo CD Application. It does not change an
application workload, HPA threshold, replica bound, image digest, release
manifest, application behavior, or production configuration.

## Selected design

Add one `ignoreDifferences` entry for each HPA-controlled dev Deployment:

- `stockai-frontend`;
- `stockai-agent-api`; and
- `stockai-procurement-mcp`.

Each entry ignores only the JSON pointer `/spec/replicas`. Add
`RespectIgnoreDifferences=true` to the existing sync options so automated
self-heal does not overwrite the HPA-owned field during synchronization. Argo
CD continues to compare and reconcile every other field, including immutable
image digests.

## Alternatives rejected

Removing the HPAs would violate the approved autoscaling design. Ignoring
`/spec/replicas` for every Deployment would also hide unauthorized scaling of
non-HPA workloads. Disabling self-heal would weaken GitOps reconciliation for
the entire application. The selected per-name entries are therefore the
smallest safe ownership boundary.

## Verification

The Kubernetes application tests must assert the three exact resource names,
the single ignored pointer, and the required sync option. Existing Argo policy
tests must continue to pass. After the manifest-only commit reaches `dev`, the
image workflow must skip rebuilding images, and live verification must show
Argo stably `Synced` and `Healthy` at the same release commit and four image
digests before T23 smoke validation resumes.

