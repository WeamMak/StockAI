"""Static contracts for the Terraform state and GitHub OIDC bootstrap."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = PROJECT_ROOT / "infra" / "terraform" / "bootstrap"
RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "terraform-bootstrap.md"


def _read(name: str) -> str:
    return (BOOTSTRAP_ROOT / name).read_text(encoding="utf-8")


def _block(source: str, start: str, end: str) -> str:
    return source.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def test_bootstrap_has_the_approved_review_surface() -> None:
    expected = {
        "main.tf",
        "outputs.tf",
        "terraform.tfvars.example",
        "variables.tf",
        "versions.tf",
    }

    assert {
        path.name for path in BOOTSTRAP_ROOT.iterdir() if path.is_file()
    } >= expected
    assert RUNBOOK.is_file()


def test_state_storage_is_encrypted_versioned_private_and_protected() -> None:
    main = _read("main.tf")

    assert 'resource "aws_s3_bucket" "terraform_state"' in main
    assert "force_destroy = false" in main
    assert 'resource "aws_s3_bucket_ownership_controls" "terraform_state"' in main
    assert "BucketOwnerEnforced" in main
    assert 'resource "aws_s3_bucket_server_side_encryption_configuration"' in main
    assert re.search(r'sse_algorithm\s*=\s*"AES256"', main)
    assert 'resource "aws_s3_bucket_versioning" "terraform_state"' in main
    assert 'status = "Enabled"' in main
    assert 'resource "aws_s3_bucket_public_access_block" "terraform_state"' in main
    for setting in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert re.search(rf"{setting}\s*=\s*true", main)

    assert "aws:SecureTransport" in main
    assert "prevent_destroy = true" in main


def test_lock_table_is_encrypted_on_demand_and_retention_protected() -> None:
    main = _read("main.tf")
    lock_table = _block(
        main,
        'resource "aws_dynamodb_table" "terraform_lock" {',
        'resource "aws_iam_openid_connect_provider"',
    )

    assert 'billing_mode = "PAY_PER_REQUEST"' in lock_table
    assert 'hash_key     = "LockID"' in lock_table
    assert 'name = "LockID"' in lock_table
    assert 'type = "S"' in lock_table
    assert re.search(r"server_side_encryption\s*{\s*enabled\s*=\s*true", lock_table)
    assert re.search(r"lifecycle\s*{\s*prevent_destroy\s*=\s*true", lock_table)


def test_state_bootstrap_is_not_application_log_storage() -> None:
    main = _read("main.tf")

    assert len(re.findall(r'resource "aws_s3_bucket"', main)) == 1
    assert 'resource "aws_s3_bucket" "loki"' not in main
    assert "application_log" not in main.lower()


def test_github_oidc_trust_uses_exact_audience_and_immutable_subjects() -> None:
    main = _read("main.tf")
    plan_trust = _block(
        main,
        'data "aws_iam_policy_document" "github_plan_trust" {',
        'data "aws_iam_policy_document" "github_apply_trust" {',
    )
    apply_trust = _block(
        main,
        'data "aws_iam_policy_document" "github_apply_trust" {',
        'resource "aws_iam_role" "github_plan" {',
    )

    for trust in (plan_trust, apply_trust):
        assert 'test     = "StringEquals"' in trust
        assert 'variable = "token.actions.githubusercontent.com:aud"' in trust
        assert 'values   = ["sts.amazonaws.com"]' in trust
        assert 'variable = "token.actions.githubusercontent.com:sub"' in trust
        assert "StringLike" not in trust

    assert "repo:${var.github_repository_subject}:pull_request" in plan_trust
    assert "repo:${var.github_repository_subject}:ref:refs/heads/main" in plan_trust
    assert (
        "repo:${var.github_repository_subject}:environment:${environment}"
        in apply_trust
    )

    variables = _read("variables.tf")
    assert "owner@123456/repository@789012" in variables
    assert '"^[A-Za-z0-9_.-]+@[0-9]+/[A-Za-z0-9_.-]+@[0-9]+$"' in variables
    assert "(@[0-9]+)?" not in variables
    assert (
        "Repository subject must use immutable GitHub owner and repository IDs"
        in variables
    )


def test_oidc_roles_preserve_scoped_state_permissions() -> None:
    main = _read("main.tf")
    plan_access = _block(
        main,
        'data "aws_iam_policy_document" "github_plan_state_access" {',
        'data "aws_iam_policy_document" "github_apply_state_access" {',
    )
    apply_access = _block(
        main,
        'data "aws_iam_policy_document" "github_apply_state_access" {',
        'resource "aws_iam_policy" "github_plan_state_access" {',
    )

    assert '"s3:GetObject"' in plan_access
    assert '"s3:PutObject"' not in plan_access
    assert '"s3:PutObject"' in apply_access
    for action in (
        "dynamodb:DescribeTable",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
    ):
        assert action in plan_access
        assert action in apply_access

    assert "aws_iam_policy_attachment" not in main
    assert 'resource "aws_iam_role_policy_attachment"' in main
    assert "AdministratorAccess" not in main
    assert "PowerUserAccess" not in main


def test_apply_lifecycle_is_scoped_and_plan_role_stays_read_only() -> None:
    main = _read("main.tf")
    plan_access = _block(
        main,
        'data "aws_iam_policy_document" "github_plan_state_access" {',
        'data "aws_iam_policy_document" "github_apply_state_access" {',
    )
    lifecycle = _block(
        main,
        'data "aws_iam_policy_document" "github_apply_lifecycle" {',
        'resource "aws_iam_policy" "github_apply_lifecycle" {',
    )

    assert "Create" not in plan_access
    assert "Update" not in plan_access
    assert "Delete" not in plan_access.replace("dynamodb:DeleteItem", "")
    assert "iam:PassRole" in lifecycle
    assert "${var.cluster_name}-control-plane" in lifecycle
    assert "ssm:SendCommand" in lifecycle
    assert "AWS-RunShellScript" in lifecycle
    assert "ec2:ResourceTag/Role" in lifecycle
    assert 'values   = ["control-plane"]' in lifecycle
    assert "aws:ResourceTag/Owner" in lifecycle
    assert "aws:RequestTag/Owner" in lifecycle
    assert "AdministratorAccess" not in lifecycle
    assert "PowerUserAccess" not in lifecycle
    assert "github_apply_lifecycle_statement_groups" in main
    assert "for_each = local.github_apply_lifecycle_statement_groups" in main
    assert 'resource "aws_iam_policy" "github_plan_discovery"' in main
    assert '"ReadOnlyDiscoveryAndCommandStatus"' in main

    bootstrap_resources = (
        "aws_s3_bucket.terraform_state.arn",
        "aws_dynamodb_table.terraform_lock.arn",
        "aws_iam_openid_connect_provider.github_actions.arn",
        "aws_iam_role.github_plan.arn",
        "aws_iam_role.github_apply.arn",
    )
    for resource in bootstrap_resources:
        assert resource not in lifecycle


def test_plan_role_can_refresh_worker_lifecycle_resources() -> None:
    main = _read("main.tf")
    lifecycle = _block(
        main,
        'data "aws_iam_policy_document" "github_apply_lifecycle" {',
        'resource "aws_iam_policy" "github_apply_lifecycle" {',
    )
    plan_discovery = _block(
        main,
        'resource "aws_iam_policy" "github_plan_discovery" {',
        'resource "aws_iam_role_policy_attachment" "github_plan_discovery" {',
    )

    assert '"cloudwatch:ListTagsForResource"' in lifecycle
    assert '"events:ListTargetsByRule"' in lifecycle
    assert '"lambda:ListVersionsByFunction"' in lifecycle
    assert '"logs:ListTagsForResource"' in lifecycle
    assert (
        "arn:aws:cloudwatch:${var.aws_region}:${var.aws_account_id}:alarm:"
        "${var.cluster_name}-worker-lifecycle-dev-non-clean"
    ) in lifecycle
    assert (
        "arn:aws:cloudwatch:${var.aws_region}:${var.aws_account_id}:alarm:"
        "${var.cluster_name}-worker-lifecycle-prod-non-clean"
    ) in lifecycle
    assert (
        "arn:aws:cloudwatch:${var.aws_region}:${var.aws_account_id}:alarm:"
        "${var.cluster_name}-worker-lifecycle-errors"
    ) in lifecycle
    assert (
        "arn:aws:events:${var.aws_region}:${var.aws_account_id}:rule/"
        "${var.cluster_name}-worker-termination"
    ) in lifecycle
    assert (
        "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:"
        "/aws/lambda/${var.cluster_name}-worker-lifecycle"
    ) in lifecycle
    assert (
        "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:"
        "${var.cluster_name}-worker-lifecycle"
    ) in lifecycle
    assert "${var.project_name}-worker-lifecycle" not in lifecycle
    assert '"ReadOnlyDiscoveryAndCommandStatus"' in plan_discovery
    assert '"ReadOnlyStockAIObservabilityTags"' in plan_discovery


def test_launch_template_versions_mutate_only_owner_tagged_templates() -> None:
    main = _read("main.tf")
    create_only = _block(
        main,
        'sid    = "CreateOnlyTaggedStockAIResources"',
        "  statement {",
    )
    mutate_owned = _block(
        main,
        'sid    = "MutateOnlyOwnedStockAIResources"',
        "  statement {",
    )

    assert '"ec2:CreateLaunchTemplateVersion"' not in create_only
    assert 'variable = "aws:RequestTag/Owner"' in create_only
    assert '"ec2:CreateLaunchTemplateVersion"' in mutate_owned
    assert 'variable = "aws:ResourceTag/Owner"' in mutate_owned


def test_account_repository_cidr_and_state_names_are_parameterized() -> None:
    variables = _read("variables.tf")
    outputs = _read("outputs.tf")
    example = _read("terraform.tfvars.example")

    for name in (
        "administrator_cidr",
        "aws_account_id",
        "aws_region",
        "cluster_name",
        "github_apply_environments",
        "github_repository_subject",
        "loki_bucket_name",
        "owner_name",
        "route53_zone_id",
        "state_bucket_name",
        "state_key_prefix",
        "state_lock_table_name",
    ):
        assert f'variable "{name}"' in variables

    for output in (
        "administrator_cidr",
        "github_apply_role_arn",
        "github_oidc_provider_arn",
        "github_plan_role_arn",
        "state_bucket_name",
        "state_key_prefix",
        "state_lock_table_name",
    ):
        assert f'output "{output}"' in outputs

    assert "WeamMak" not in example
    assert "StockAI" not in example
    assert not re.search(r'aws_account_id\s*=\s*"\d{12}"', example)
    assert "replace-with-your" in example


def test_runbook_is_cli_only_and_preserves_the_apply_gate() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for command in (
        "terraform fmt -check",
        "terraform init",
        "terraform validate",
        "terraform plan",
        "terraform apply",
        "aws s3api get-bucket-encryption",
        "aws s3api get-bucket-versioning",
        "aws dynamodb describe-table",
        "aws iam get-role",
    ):
        assert command in runbook

    assert "AWS Console" in runbook
    assert "separate explicit approval" in runbook
    assert "Do not run `terraform apply`" in runbook
    assert "application log" in runbook.lower()
