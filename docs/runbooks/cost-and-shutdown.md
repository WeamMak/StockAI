# Cost, shutdown, and retained-data runbook

**Scope:** T17 AWS edge and environment services

This runbook describes planning and operator actions. It does not authorize an
AWS apply, shutdown, restore, or deletion. Every real Terraform plan must be
reviewed with the account, quota, and cost assumptions before the user gives
the separate infrastructure approval required by `docs/plan.md`.

## Normal cost boundary

The approved operating target is below **$70 per month** and the review ceiling
is **$90 per month**. Terraform creates two account-level monthly cost budgets:

- `weam-stockai-monthly-target` at $70; and
- `weam-stockai-monthly-review-ceiling` at $90.

Both send actual-cost notifications to the configured operator email. A budget
notification is evidence to review or stop resources; it is not an automatic
shutdown and cannot interrupt a running demo.

The main fixed or retained cost drivers are the shared ALB, three EC2 root
volumes while nodes exist, six 5 GiB retained data volumes, public DNS, and the
fixed control-plane/worker instance hours. DynamoDB on-demand traffic, S3 Loki
objects, Secrets Manager, Cognito, DLM snapshots, and low-volume CloudWatch
metric reads should remain secondary for the fictional course workload. Before
every authorized apply:

1. Refresh the AWS Pricing Calculator estimate for `us-east-1`.
2. Include the exact worker desired capacities under review, including any
   temporary capacity override.
3. Confirm EC2 vCPU, ALB, EBS, Elastic IP, Cognito, and Bedrock quotas.
4. Confirm that the budget recipient is the intended operator.
5. Review the Terraform plan for unexpected services, replacement of retained
   volumes, or environment-crossing IAM resources.

The specification's estimate of roughly $60–85 assumes about 176 active EC2
hours per month, low request volume, one continuously provisioned ALB, and the
initial storage sizes. It is an assumption to refresh, not a billing guarantee.

## Terraform root order and inputs

T16's `platform` root remains the source of VPC, subnet, worker-ASG, worker-AZ,
worker-role, control-plane-role, and worker-security-group coordinates. Pass
reviewed `terraform output` values into T17 through ignored, account-specific
`.tfvars` files; never commit account IDs, email addresses, credentials, or
secret values.

Use separate backend keys:

```text
stockai/platform/terraform.tfstate
stockai/edge/terraform.tfstate
stockai/environments/dev/terraform.tfstate
stockai/environments/prod/terraform.tfstate
```

Plan the roots in this order:

1. `infra/terraform/platform` — obtain the approved T16 outputs.
2. `infra/terraform/edge` — provide the existing Route 53 zone ID, registered
   domain, globally unique Loki bucket name, operator email, and T16 network and
   ASG outputs.
3. `infra/terraform/environments/dev` and `prod` — provide the account ID,
   matching T16 worker AZ and role, control-plane role, domain, and the edge
   root's `loki_bucket_arn` output.

Each root uses the T15 encrypted S3 backend and a distinct key. Initialization
follows `docs/runbooks/terraform-bootstrap.md`; do not put credentials in
backend arguments.

## Temporary Odoo key bootstrap permission

Normal environment plans set:

```hcl
enable_odoo_key_bootstrap = false
```

That state contains no Secrets Manager write grant for workers. For the finite
T19B bootstrap or later approved key rotation:

1. Prepare and review a plan for only the intended environment with the flag
   set to `true`.
2. Confirm the added policy has only `secretsmanager:PutSecretValue` and
   references that environment's exact `odoo-api-key` secret ARN.
3. Apply only after explicit approval, run the bounded Job, and verify the
   secret without printing its value.
4. Immediately return the flag to `false`, review the detach plan, apply it,
   and verify the policy and attachment are absent.

Never enable both environments as a convenience and never leave the temporary
attachment in place during normal worker operation.

## Normal shutdown

Do not stop an ASG-managed worker instance directly: its ASG will replace it.
The supported inactive worker state is `min = 0`, `desired = 0`, `max = 3` in
the T16 platform root.

After T18A/T18B bootstrap and termination cleanup are deployed and healthy:

1. Confirm there is no scan, approval, Odoo write, bootstrap Job, rollout, or
   volume operation in progress.
2. Confirm the latest prod Odoo/PostgreSQL DLM recovery point and record the
   current six volume IDs and AZs from Terraform outputs.
3. Plan the platform root with both worker ASGs at `0/0/3`. Review that only
   capacity changes and the approved lifecycle cleanup are involved.
4. After explicit approval, apply the reviewed plan. Wait for clean drain,
   Node deletion, pod termination, and EBS detachment evidence for both
   environments.
5. Stop the fixed control-plane EC2 instance only after both workers are at
   zero and retained volumes are detached. Record the instance ID and stop
   time. Starting or stopping an existing instance is an operational action;
   instance creation remains Terraform-only.

Before T18A/T18B exists, do not claim automated drain or safe stateful
shutdown. There is no production workload to preserve until the later
deployment tasks, and any real capacity change still needs a reviewed plan.

The ALB, ACM certificate, Route 53 records, S3 bucket, DynamoDB tables,
Secrets Manager entries, Cognito pools, and retained EBS volumes remain during
a normal shutdown. Their availability or storage charges can continue.

## Restart

1. Start the fixed control-plane instance and wait for SSM plus the Kubernetes
   API and control-plane health checks.
2. Plan the T16 platform root at the normal `1/1/3` capacity for dev and prod.
3. After explicit approval, apply the reviewed capacity plan.
4. Verify each replacement worker joins with only its environment labels,
   taint, role, subnet/AZ, and target group.
5. Verify the three matching retained volumes reattach, stateful workloads are
   Ready, both ALB target groups become healthy, and all six HTTPS host checks
   pass.

## Extended edge shutdown

The shared ALB is a fixed cost even while EC2 is stopped. For an extended idle
period, use a separately reviewed Terraform change that disables the ALB,
listeners, rules, target groups, ASG attachments, and their Route 53 aliases.
Do not use ad-hoc console deletion or an unreviewed targeted destroy. Retain the
ACM certificate, operational-log bucket, environment services, and all six data
volumes unless a separately approved design says otherwise. Re-enable the same
configuration and verify DNS, certificate validation, redirect behavior, host
routing, and target health before the next demo.

## Retention and recovery limits

- All six data volumes are encrypted `gp3`, AZ-bound to the matching worker
  ASG, tagged for ownership, and protected from ordinary Terraform destroy.
- DLM selects only tagged prod Odoo and PostgreSQL volumes and retains seven
  daily crash-consistent snapshots.
- Dev Odoo/PostgreSQL recovery uses the reproducible fictional seed rather
  than snapshot claims.
- Prometheus survives pod and worker replacement through its retained volume,
  but volume loss is an accepted MVP limitation; neither environment's
  Prometheus volume is snapshotted.
- Grafana is reconstructed from Git-managed provisioning and has no retained
  volume.

A snapshot restore is not an in-place repair. Create a replacement encrypted
volume from the selected snapshot through a reviewed Terraform change in the
same environment AZ, preserve the original volume, update the later static PV
to the replacement ID, and verify Odoo/PostgreSQL consistency before resuming
traffic. Never remove `prevent_destroy`, delete the original volume, or mutate
the Kubernetes PV during incident triage without separate destructive-action
approval.

## Final verification

After any authorized start, stop, or restore, record sanitized evidence for:

- worker desired/InService capacity versus correctly labeled Ready nodes;
- old-node removal and retained-volume detachment/reattachment;
- both target groups and all six public HTTPS checks;
- DynamoDB, Cognito, Secrets Manager, and Loki dependency health;
- current month actual cost versus the $70 target and $90 review ceiling; and
- unexpected resources or IAM changes in a fresh Terraform plan.

Do not print Terraform state, secret values, bootstrap credentials, procurement
records, or raw sensitive logs while collecting evidence.
