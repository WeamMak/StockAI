# Guided infrastructure provisioning

## Purpose

`make infra-provision` prepares the approved fixed `us-east-1` StockAI
infrastructure without asking an operator to copy Terraform outputs or maintain
four JSON GitHub variables. It remains deliberately approval-gated: every root
creates and displays a saved plan, and only the exact saved plan can be applied
after its root-specific phrase is typed.

## Prerequisites

- Terraform 1.15.x, Python 3.12, `uv`, AWS CLI, and GitHub CLI.
- A short-lived AWS session authorized to inspect prerequisites and manage the
  approved StockAI resources.
- `gh auth login` completed with administration permission for the current
  repository.
- A public Route 53 hosted zone already owned by the AWS account.
- At least six standard-instance vCPUs of EC2 quota and availability of
  `openai.gpt-oss-20b-1:0` in `us-east-1`.
- For the existing StockAI deployment, the matching local bootstrap state or
  its encrypted backup. Never bootstrap existing resources from empty local
  state.

Docker Hub credentials are not accepted by this command. Add
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` as GitHub repository secrets after
provisioning.

## Normal fresh-account flow

```bash
aws sts get-caller-identity
gh auth status
make infra-provision
```

For an independent AWS account, enter only:

1. The lowercase domain, for example `example.com`.
2. Its public hosted-zone ID, for example `Z123456789`.
3. The displayed administrator-CIDR confirmation.

The command verifies Route 53 authority, account/repository identity, EC2
quota, the exact Bedrock model, the controlled Canonical Ubuntu AMI, and two
available zones before Terraform. It writes only non-secret configuration to
`deploy/config/deployment.json`; root `*.auto.tfvars.json`, plans, state, and
the resumable checkpoint stay ignored.

The roots run in this fixed order:

1. `bootstrap` locally establishes state infrastructure and GitHub OIDC trust.
2. `platform` creates the VPC and self-managed EC2 Kubernetes nodes.
3. `edge` creates the shared HTTPS/DNS/log-storage edge.
4. `dev` creates environment-specific AWS services and retained volumes.
5. `prod` creates the separate production services and volumes.

For every root, inspect the complete displayed plan. Type `apply <root>` only
when the plan contains exactly the expected root changes. Any unexpected
replacement or deletion is a stop condition. Interrupting before approval is
safe; rerunning resumes after the last successfully applied root.

After bootstrap, the command creates GitHub `dev` and `prod` environments and
sets these non-secret variables automatically:

- `AWS_TERRAFORM_PLAN_ROLE_ARN`
- `AWS_TERRAFORM_APPLY_ROLE_ARN`
- `TERRAFORM_STATE_BUCKET`
- `TERRAFORM_STATE_KEY_PREFIX`
- `TERRAFORM_LOCK_TABLE`

It deletes the obsolete four `TERRAFORM_*_TFVARS_JSON` variables when present.
After all roots apply, it synchronizes reviewed EBS, Cognito, DynamoDB, Loki,
hostname, and exact Secrets Manager ARN coordinates into the dev/prod
Kustomize overlays. No secret value is written to Git.

## Existing deployment and Budget removal

The committed descriptor pins the existing `weam-stockai` identities. The
command fails if the authenticated account, repository, or confirmed CIDR does
not match; it does not silently rename existing resources.

The edge Terraform now omits the two email-backed AWS Budget resources. For
the existing deployment, do not approve its edge plan unless the destructive
section contains exactly:

```text
aws_budgets_budget.monthly["monthly_target"]
aws_budgets_budget.monthly["monthly_review_ceiling"]
```

Both must be deletions. Any ALB, ACM, Route 53, S3, ASG, IAM, DynamoDB,
Cognito, Secrets Manager, EBS, backend, replacement, or additional deletion is
a stop condition. Preserve the saved plan for review and apply it only after
separate explicit approval.

## OIDC provider already exists

An AWS account can have only one GitHub Actions OIDC provider. If bootstrap
reports that the provider already exists and it is not owned by another
Terraform state, stop and import it into the matching bootstrap state before
creating a new saved plan:

```bash
terraform -chdir=infra/terraform/bootstrap import \
  aws_iam_openid_connect_provider.github_actions \
  arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com
```

Never import a provider owned by another Terraform state without reconciling
that ownership first.

## Recovery

- A rejected plan changes nothing; rerun the command after correcting the
  cause.
- After an interruption, inspect `.stockai-provision-checkpoint.json` and rerun
  `make infra-provision`. Do not edit the checkpoint to skip an unapplied root.
- If an apply fails, resolve the reported provider error, inspect state, and
  rerun. Terraform will create a new plan; approval is never reused.
- Do not commit Terraform state, saved plans, generated root inputs, the
  checkpoint, or AWS/GitHub credentials.
