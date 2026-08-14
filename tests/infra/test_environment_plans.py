"""Plan contracts for isolated dev and prod application infrastructure."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from tests.infra.plan import TerraformPlan, create_plan, resources

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS_ROOT = PROJECT_ROOT / "infra" / "terraform" / "environments"
APP_MODULE = PROJECT_ROOT / "infra" / "terraform" / "modules" / "app-environment"
ACCOUNT_ID = "123456789012"
LOG_BUCKET_ARN = "arn:aws:s3:::weam-stockai-t17-operational-logs"


def _environment_variables(environment: str) -> dict[str, str]:
    availability_zone = "us-east-1a" if environment == "dev" else "us-east-1b"
    return {
        "aws_account_id": ACCOUNT_ID,
        "cluster_name": "weam-stockai",
        "control_plane_role_name": "weam-stockai-control-plane",
        "domain_name": "example.com",
        "loki_bucket_arn": LOG_BUCKET_ARN,
        "worker_availability_zone": availability_zone,
        "worker_role_name": f"weam-stockai-{environment}-worker",
    }


@pytest.fixture(scope="module")
def dev_plan(tmp_path_factory: pytest.TempPathFactory) -> TerraformPlan:
    plan_dir = tmp_path_factory.mktemp("terraform-dev-environment-plan")
    return create_plan(
        ENVIRONMENTS_ROOT / "dev",
        plan_dir / "dev.tfplan",
        _environment_variables("dev"),
    )


@pytest.fixture(scope="module")
def prod_plan(tmp_path_factory: pytest.TempPathFactory) -> TerraformPlan:
    plan_dir = tmp_path_factory.mktemp("terraform-prod-environment-plan")
    return create_plan(
        ENVIRONMENTS_ROOT / "prod",
        plan_dir / "prod.tfplan",
        _environment_variables("prod"),
    )


def _values(plan: TerraformPlan, resource_type: str) -> Iterator[dict[str, Any]]:
    return (resource["values"] for resource in resources(plan, resource_type))


def _configuration_modules(module: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield module
    for module_call in module.get("module_calls", {}).values():
        child = module_call.get("module")
        if child is not None:
            yield from _configuration_modules(child)


def _configurations(
    plan: TerraformPlan,
    resource_type: str,
    *,
    mode: str,
) -> list[dict[str, Any]]:
    root = plan["configuration"]["root_module"]
    return [
        resource
        for module in _configuration_modules(root)
        for resource in module.get("resources", [])
        if resource.get("mode", "managed") == mode
        and resource.get("type") == resource_type
    ]


def _policy_statements(
    plan: TerraformPlan,
    document_name: str,
) -> list[dict[str, Any]]:
    documents = {
        document["name"]: document
        for document in _configurations(
            plan,
            "aws_iam_policy_document",
            mode="data",
        )
    }
    return cast(
        list[dict[str, Any]],
        documents[document_name]["expressions"]["statement"],
    )


def _constant_list(expression: Mapping[str, Any]) -> list[str]:
    return list(expression["constant_value"])


def test_each_environment_has_isolated_tables_secrets_and_cognito(
    dev_plan: TerraformPlan,
    prod_plan: TerraformPlan,
) -> None:
    for environment, plan in (("dev", dev_plan), ("prod", prod_plan)):
        tables = list(_values(plan, "aws_dynamodb_table"))
        assert {table["name"] for table in tables} == {
            f"weam-stockai-{environment}-application",
            f"weam-stockai-{environment}-checkpoints",
        }
        assert all(table["billing_mode"] == "PAY_PER_REQUEST" for table in tables)
        assert all(table["server_side_encryption"][0]["enabled"] for table in tables)
        assert all(
            table["ttl"][0] == {"attribute_name": "ttl", "enabled": True}
            for table in tables
        )
        assert all(
            table["point_in_time_recovery"][0]["enabled"] is (environment == "prod")
            for table in tables
        )

        secrets = list(_values(plan, "aws_secretsmanager_secret"))
        assert {secret["name"] for secret in secrets} == {
            f"weam-stockai/{environment}/cron-token",
            f"weam-stockai/{environment}/grafana-admin-password",
            f"weam-stockai/{environment}/mcp-token",
            f"weam-stockai/{environment}/odoo-api-key",
            f"weam-stockai/{environment}/odoo-database-password",
            f"weam-stockai/{environment}/session-secret",
        }

        assert len(resources(plan, "aws_cognito_user_pool")) == 1
        assert len(resources(plan, "aws_cognito_user_pool_client")) == 1
        assert len(resources(plan, "aws_cognito_user_pool_domain")) == 1
        assert {group["name"] for group in _values(plan, "aws_cognito_user_group")} == {
            "stockai-procurement-manager",
            "stockai-procurement-officer",
        }
        client = next(_values(plan, "aws_cognito_user_pool_client"))
        assert client["allowed_oauth_flows"] == ["code"]
        assert set(client["allowed_oauth_scopes"]) == {"email", "openid", "profile"}
        assert client["generate_secret"] is False
        assert client["callback_urls"] == [
            f"https://app.{environment}.example.com/auth/callback"
        ]


def test_environment_retention_and_loki_prefixes_are_distinct(
    dev_plan: TerraformPlan,
    prod_plan: TerraformPlan,
) -> None:
    assert dev_plan["output_changes"]["checkpoint_retention_days"]["after"] == 30
    assert prod_plan["output_changes"]["checkpoint_retention_days"]["after"] == 365
    assert dev_plan["output_changes"]["loki_prefix"]["after"] == "dev/"
    assert prod_plan["output_changes"]["loki_prefix"]["after"] == "prod/"

    for environment, plan in (("dev", dev_plan), ("prod", prod_plan)):
        statements = _policy_statements(plan, "worker_application")
        object_statement = next(
            statement
            for statement in statements
            if "s3:GetObject" in _constant_list(statement["actions"])
        )
        assert object_statement["resources"]["references"] == [
            "var.loki_bucket_arn",
            "local.loki_prefix",
        ]
        assert plan["variables"]["loki_bucket_arn"]["value"] == LOG_BUCKET_ARN
        assert plan["output_changes"]["loki_prefix"]["after"] == f"{environment}/"


def test_normal_worker_policy_is_environment_scoped_and_cannot_write_secrets(
    dev_plan: TerraformPlan,
    prod_plan: TerraformPlan,
) -> None:
    for environment, plan in (("dev", dev_plan), ("prod", prod_plan)):
        assert len(resources(plan, "aws_iam_policy")) == 0
        assert len(resources(plan, "aws_iam_role_policy_attachment")) == 0
        statements = _policy_statements(plan, "worker_application")
        actions = {
            action
            for statement in statements
            for action in _constant_list(statement["actions"])
        }
        assert "secretsmanager:GetSecretValue" in actions
        assert "secretsmanager:PutSecretValue" not in actions
        assert "bedrock:InvokeModel" in actions

        bedrock = next(
            statement
            for statement in statements
            if "bedrock:InvokeModel" in _constant_list(statement["actions"])
        )
        assert bedrock["resources"]["references"] == ["local.bedrock_model_arn"]
        assert plan["output_changes"]["bedrock_model_arn"]["after"] == (
            "arn:aws:bedrock:us-east-1::foundation-model/openai.gpt-oss-20b-1:0"
        )

        dynamodb = next(
            statement
            for statement in statements
            if "dynamodb:GetItem" in _constant_list(statement["actions"])
        )
        references = set(dynamodb["resources"]["references"])
        assert {
            "aws_dynamodb_table.application.arn",
            "aws_dynamodb_table.checkpoint.arn",
        } <= references

        secret_read = next(
            statement
            for statement in statements
            if "secretsmanager:GetSecretValue" in _constant_list(statement["actions"])
        )
        assert secret_read["resources"]["references"] == [
            "aws_secretsmanager_secret.runtime"
        ]

        policy = next(
            policy
            for policy in _values(plan, "aws_iam_role_policy")
            if policy["role"] == f"weam-stockai-{environment}-worker"
        )
        assert policy["role"] == f"weam-stockai-{environment}-worker"


def test_explicit_bootstrap_policy_targets_only_its_odoo_key(tmp_path: Path) -> None:
    plan = create_plan(
        ENVIRONMENTS_ROOT / "dev",
        tmp_path / "dev-bootstrap.tfplan",
        {
            **_environment_variables("dev"),
            "enable_odoo_key_bootstrap": "true",
        },
    )

    assert len(resources(plan, "aws_iam_policy")) == 1
    assert len(resources(plan, "aws_iam_role_policy_attachment")) == 1
    attachment = next(_values(plan, "aws_iam_role_policy_attachment"))
    assert attachment["role"] == "weam-stockai-dev-worker"

    statement = _policy_statements(plan, "odoo_bootstrap")[0]
    assert _constant_list(statement["actions"]) == ["secretsmanager:PutSecretValue"]
    references = statement["resources"]["references"]
    assert 'aws_secretsmanager_secret.runtime["odoo-api-key"].arn' in references
    assert not any("prod" in reference for reference in references)


def test_six_encrypted_retained_volumes_match_environment_azs(
    dev_plan: TerraformPlan,
    prod_plan: TerraformPlan,
) -> None:
    expected = {
        ("dev", "odoo", "us-east-1a"),
        ("dev", "postgresql", "us-east-1a"),
        ("dev", "prometheus", "us-east-1a"),
        ("prod", "odoo", "us-east-1b"),
        ("prod", "postgresql", "us-east-1b"),
        ("prod", "prometheus", "us-east-1b"),
    }
    actual = {
        (
            volume["tags"]["Environment"],
            volume["tags"]["Workload"],
            volume["availability_zone"],
        )
        for plan in (dev_plan, prod_plan)
        for volume in _values(plan, "aws_ebs_volume")
    }
    assert actual == expected

    for plan in (dev_plan, prod_plan):
        for volume in _values(plan, "aws_ebs_volume"):
            assert volume["encrypted"] is True
            assert volume["size"] == 5
            assert volume["type"] == "gp3"
            assert volume["tags"]["Cluster"] == "weam-stockai"
            assert volume["tags"]["ManagedBy"] == "Terraform"

        output = plan["output_changes"]["data_volumes"]["after"]
        environment = next(iter(output))
        assert set(output[environment]) == {"odoo", "postgresql", "prometheus"}
        assert all(
            set(coordinates) == {"availability_zone"}
            for coordinates in output[environment].values()
        )
        unknown = plan["output_changes"]["data_volumes"]["after_unknown"]
        assert all(
            coordinates == {"volume_id": True}
            for coordinates in unknown[environment].values()
        )


def test_protected_destroy_can_plan_environment_data_volumes() -> None:
    source = (APP_MODULE / "main.tf").read_text(encoding="utf-8")
    volume_block = source.split('resource "aws_ebs_volume" "data" {', maxsplit=1)[
        1
    ].split('data "aws_iam_policy_document" "worker_application"', maxsplit=1)[0]

    assert "prevent_destroy" not in volume_block


def test_prod_cognito_deletion_protection_can_only_be_disabled_explicitly() -> None:
    root = (ENVIRONMENTS_ROOT / "prod" / "main.tf").read_text(encoding="utf-8")
    variables = (ENVIRONMENTS_ROOT / "prod" / "variables.tf").read_text(
        encoding="utf-8"
    )
    module = PROJECT_ROOT / "infra" / "terraform" / "modules" / "app-environment"
    module_main = (module / "main.tf").read_text(encoding="utf-8")
    module_variables = (module / "variables.tf").read_text(encoding="utf-8")

    assert (
        "enable_cognito_deletion_protection = var.enable_cognito_deletion_protection"
        in root
    )
    assert 'variable "enable_cognito_deletion_protection"' in variables
    assert "default     = true" in variables
    assert 'variable "enable_cognito_deletion_protection"' in module_variables
    assert "default     = false" in module_variables
    assert re.search(
        r"deletion_protection\s*=\s*var\.enable_cognito_deletion_protection\s*"
        r'\?\s*"ACTIVE"\s*:\s*"INACTIVE"',
        module_main,
    )


def test_only_prod_erp_volumes_receive_seven_daily_snapshots(
    dev_plan: TerraformPlan,
    prod_plan: TerraformPlan,
) -> None:
    assert len(resources(dev_plan, "aws_dlm_lifecycle_policy")) == 0
    policies = list(_values(prod_plan, "aws_dlm_lifecycle_policy"))
    assert len(policies) == 1
    details = policies[0]["policy_details"][0]
    assert details["resource_types"] == ["VOLUME"]
    assert details["target_tags"] == {"SnapshotPolicy": "prod-erp-daily"}
    schedule = details["schedule"][0]
    assert schedule["create_rule"][0]["interval"] == 24
    assert schedule["create_rule"][0]["interval_unit"] == "HOURS"
    assert schedule["retain_rule"][0]["count"] == 7


def test_environment_roots_exclude_unapproved_services(
    dev_plan: TerraformPlan,
    prod_plan: TerraformPlan,
) -> None:
    excluded = {
        "aws_db_instance",
        "aws_efs_file_system",
        "aws_eks_cluster",
        "aws_nat_gateway",
        "aws_ses_email_identity",
        "aws_sns_topic",
        "aws_sqs_queue",
    }
    for plan in (dev_plan, prod_plan):
        assert all(not resources(plan, resource_type) for resource_type in excluded)

        encoded = json.dumps(plan)
        assert "AdministratorAccess" not in encoded
        assert "PowerUserAccess" not in encoded
