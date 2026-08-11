# Terraform state and GitHub OIDC bootstrap

## Purpose and safety boundary

This root creates only the dedicated Terraform state bucket, DynamoDB lock
table, GitHub Actions OIDC provider, and state-access roles. The state bucket
must never be reused for Loki or any other application log storage. Later
Terraform roots receive separate state keys below the configured prefix.

All AWS resources are created through Terraform CLI commands. No AWS Console
creation is part of this procedure. The bootstrap root starts with local state
because its remote backend does not exist yet; treat that state as sensitive,
keep it out of Git, and retain an encrypted operator backup after the approved
apply.

Do not run `terraform apply` without the separate explicit approval required
for infrastructure creation. A successful local plan is not apply approval.

## Prerequisites

- Terraform `1.15.x` and AWS CLI v2.
- GitHub CLI authenticated to the repository.
- Short-lived AWS credentials for the intended account; do not create or store
  access keys in this repository.
- AWS quota and cost review completed for the separately approved apply.

Confirm the caller and region before doing anything else:

```bash
aws sts get-caller-identity
aws configure get region
```

The caller account must match `aws_account_id`, and the approved region is
`us-east-1`.

## Prepare account-specific input

From the repository root:

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars` is ignored by Git. Replace every placeholder, use a trusted
administrator `/32` CIDR where practical, choose a globally unique state bucket
name, and keep the state and lock names distinct from all application storage.

GitHub repositories created after the immutable-subject rollout include stable
owner and repository IDs in the OIDC `sub` claim. Resolve the required segment
without hard-coding it in the repository:

```bash
gh api repos/{owner}/{repository} --jq '"\(.owner.login)@\(.owner.id)/\(.name)@\(.id)"'
```

Place that result in `github_repository_subject`. Do not use a wildcard or the
renameable `owner/repository` form. The apply role trusts only the configured
`dev` and `prod` environment subjects; protect those environments before T21
wires the workflow.

## Format, initialize, validate, and plan

Run the checks from `infra/terraform/bootstrap`:

```bash
terraform fmt -check -recursive
terraform init
terraform validate
terraform plan -out=bootstrap.tfplan
terraform show bootstrap.tfplan
```

Review the plan for exactly one state S3 bucket, one DynamoDB lock table, one
GitHub OIDC provider, two roles, two state policies, and two attachments. Check
that no application log bucket, long-lived credential, wildcard OIDC subject,
`AdministratorAccess`, or `PowerUserAccess` is present.

The plan file can contain sensitive values and is ignored by Git. Remove it
after review or keep it only in approved encrypted local storage.

## Apply only after explicit approval

After the user separately approves the reviewed infrastructure plan:

```bash
terraform apply bootstrap.tfplan
terraform output
```

This is the only creation step. Do not replace it with manual AWS Console
creation. Keep the resulting local `terraform.tfstate` encrypted and outside
the repository. Back it up to approved encrypted operator storage; if it is
lost, recover by importing the existing resources rather than creating
duplicates.

The two GitHub roles intentionally receive only state-bucket and lock-table
permissions in T15. Later infrastructure tasks must add reviewed,
resource-scoped workload permissions before their plan/apply workflows are
enabled; do not attach broad AWS managed administrator policies.

## Configure later Terraform roots

Each later root declares an empty S3 backend block and initializes with a
unique key below `state_key_prefix`:

```hcl
terraform {
  backend "s3" {}
}
```

Example CLI initialization for a later platform root:

```bash
terraform init \
  -backend-config="bucket=<state_bucket_name>" \
  -backend-config="key=<state_key_prefix>/platform/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=<state_lock_table_name>" \
  -backend-config="encrypt=true"
```

Use a different reviewed key for every Terraform root. Never put backend
credentials in `-backend-config`; use the AWS credential chain or GitHub OIDC.

The approved course plan and tutorial require the DynamoDB lock table shown
above. Terraform `1.15.x` still supports that backend argument, but HashiCorp
now deprecates DynamoDB-based locking in favor of S3 lockfiles. Keep the
approved contract for T15; replacing it requires a reviewed spec/plan revision
rather than an untracked operational change.

## Verify the authorized apply

Read the output names, then verify the resources through APIs:

```bash
aws s3api get-bucket-encryption --bucket <state_bucket_name>
aws s3api get-bucket-versioning --bucket <state_bucket_name>
aws s3api get-public-access-block --bucket <state_bucket_name>
aws s3api get-bucket-policy --bucket <state_bucket_name>
aws dynamodb describe-table --table-name <state_lock_table_name>
aws iam get-role --role-name <github_plan_role_name>
aws iam get-role --role-name <github_apply_role_name>
```

Confirm `AES256` default encryption, versioning `Enabled`, all four public
access flags, TLS-only bucket access, DynamoDB encryption and `LockID` string
partition key, exact OIDC audience, exact pull-request subject, and exact
protected-environment subjects. Do not print credentials or Terraform state.

## Recovery and teardown

Both state resources use `prevent_destroy`. Any intentional teardown therefore
requires a separately reviewed code change, a current encrypted state backup,
and explicit destructive-action approval. Never bypass retention protection to
fix an ordinary plan failure.
