# Worker Termination Cleanup Runbook

## Purpose

Use this runbook when a dev or prod worker ASG terminates an instance. T18B
holds termination for at most 300 seconds while a Lambda asks the fixed control
plane, through SSM Run Command, to cordon, drain for at most 120 seconds, and
delete the matching Kubernetes Node. The Lambda runs for at most 240 seconds
and always attempts `CONTINUE`; the lifecycle hook also defaults to `CONTINUE`.
An unavailable cleanup path therefore cannot hold an EC2 instance indefinitely.

The Lambda validates the exact ASG, environment tag, instance ID, EC2 private
DNS node name and private IPv4 address, Kubernetes Node `InternalIP`, and
Kubernetes environment label before cleanup. Self-managed kubeadm nodes in
this cluster have no `.spec.providerID`, so cleanup does not depend on that
unavailable field. The Lambda has no procurement, Bedrock, DynamoDB, S3,
Secrets Manager, Odoo, or worker-role permissions. After sending the cleanup
command, it treats only SSM's transient `InvocationDoesNotExist` read-after-write
response as pending and retries it within the existing heartbeat and timeout
bounds.

## Outcomes

| Outcome | Meaning | Required action |
|---|---|---|
| `clean` | The node drained and was deleted, or it was already absent. | Verify the replacement becomes `Ready`. |
| `forced` | Drain failed or timed out, but node deletion was attempted. | Inspect workload disruption and retained-volume attachment before accepting recovery. |
| `failed` | EC2 identity, SSM, or control-plane access prevented cleanup. | Verify and remove only the stale node described below, then resolve the SSM/control-plane fault. |

`forced`, `failed`, and Lambda invocation errors place a CloudWatch alarm in
`ALARM`. Logs and metrics contain only infrastructure identifiers, status,
duration, heartbeat count, outcome, and a sanitized error code.

## Inspect an event

Set the region and environment explicitly. Do not paste SSM command output or
credentials into tickets.

```bash
export AWS_REGION=us-east-1
export STOCKAI_ENVIRONMENT=dev
export STOCKAI_ASG_NAME=weam-stockai-dev-workers

aws autoscaling describe-auto-scaling-groups \
  --region "$AWS_REGION" \
  --auto-scaling-group-names "$STOCKAI_ASG_NAME" \
  --query 'AutoScalingGroups[0].Instances[].{Id:InstanceId,State:LifecycleState,Health:HealthStatus}'

aws cloudwatch describe-alarms \
  --region "$AWS_REGION" \
  --alarm-name-prefix "weam-stockai-worker-lifecycle"

aws logs tail "/aws/lambda/weam-stockai-worker-lifecycle" \
  --region "$AWS_REGION" \
  --since 30m
```

On the control plane, verify the Kubernetes view:

```bash
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl get nodes -o wide
kubectl get pods --all-namespaces -o wide
```

Healthy recovery means the old private-DNS node is absent, the replacement is
`Ready`, and it has only the expected environment identity:

```bash
kubectl get node "<replacement-private-dns>" \
  -o jsonpath='{.metadata.labels.stockai\.io/environment}{" "}{.spec.taints}{"\n"}'
```

## Remediate a stale node

Delete a stale Node only after all four checks succeed:

1. The cleanup event identifies an allowlisted dev or prod ASG.
2. EC2 shows the old instance is terminated or absent.
3. The Node name and `InternalIP` equal the old instance's EC2 private DNS name
   and private IPv4 address recorded before termination.
4. The Node's `stockai.io/environment` label matches the event environment.

```bash
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl get node "<old-private-dns>" \
  -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}{" "}{.metadata.labels.stockai\.io/environment}{"\n"}'

aws ec2 describe-instances \
  --region "$AWS_REGION" \
  --instance-ids "<old-instance-id>" \
  --query 'Reservations[].Instances[].State.Name'

kubectl delete node "<old-private-dns>"
```

If a retained EBS volume cannot attach after T19 workloads exist, first verify
that no live node or pod is using it. Inspect the Kubernetes VolumeAttachment
and the EC2 volume attachment before making any detach decision. Do not force
detach a mounted volume; escalate for a reviewed recovery action.

## Controlled dev acceptance drill

Run only after reviewing and applying the saved Terraform plan. Record the old
dev instance and node, then ask the ASG to replace it without reducing desired
capacity:

```bash
export AWS_REGION=us-east-1
export STOCKAI_ASG_NAME=weam-stockai-dev-workers
export OLD_INSTANCE_ID="<reviewed-dev-worker-instance-id>"

aws autoscaling terminate-instance-in-auto-scaling-group \
  --region "$AWS_REGION" \
  --instance-id "$OLD_INSTANCE_ID" \
  --no-should-decrement-desired-capacity
```

Verify lifecycle heartbeats, a `clean` log outcome, disappearance of the old
Node, automatic replacement join, the exact dev label/taint/role, and the ASG
returning to its reviewed desired capacity. Application and retained-volume
recovery acceptance remains T23.

The required fail-open drill must be separately approved because it changes a
live control-plane dependency. Temporarily make control-plane SSM unavailable
through the reviewed test procedure, terminate only the dev worker, and verify
that termination leaves `Terminating:Wait` within 300 seconds, the outcome is
`failed` or the Lambda error alarm fires, and the ASG launches a replacement.
Restore SSM immediately, confirm it is online, and remediate the stale Node
using the identity checks above.

## Rollback

Do not delete lifecycle resources manually. Revert the reviewed T18B Terraform
change, create and inspect a saved plan, and apply it through the authorized
infrastructure workflow. Until rollback completes, the hook remains bounded
and fail-open. A rollback restores manual stale-node deletion; it does not
terminate or replace workers by itself.
