"""Terraform plan contracts for worker termination lifecycle automation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.infra.plan import (
    TerraformPlan,
    create_plan,
    resource_configurations,
    resources,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = PROJECT_ROOT / "infra" / "terraform" / "platform"


@pytest.fixture(scope="module")
def platform_plan(tmp_path_factory: pytest.TempPathFactory) -> TerraformPlan:
    plan_dir = tmp_path_factory.mktemp("terraform-worker-lifecycle-plan")
    return create_plan(
        PLATFORM_ROOT,
        plan_dir / "platform.tfplan",
        {
            "administrator_cidr": "203.0.113.10/32",
            "ami_id": "ami-0123456789abcdef0",
            "aws_account_id": "123456789012",
        },
    )


def _values(plan: TerraformPlan, resource_type: str) -> Iterator[dict[str, Any]]:
    return (resource["values"] for resource in resources(plan, resource_type))


def test_two_hooks_fail_open_with_bounded_cleanup(platform_plan: TerraformPlan) -> None:
    hooks = list(_values(platform_plan, "aws_autoscaling_lifecycle_hook"))

    assert len(hooks) == 2
    assert {hook["autoscaling_group_name"] for hook in hooks} == {
        "weam-stockai-dev-workers",
        "weam-stockai-prod-workers",
    }
    assert all(hook["default_result"] == "CONTINUE" for hook in hooks)
    assert all(hook["heartbeat_timeout"] == 300 for hook in hooks)
    assert all(
        hook["lifecycle_transition"] == "autoscaling:EC2_INSTANCE_TERMINATING"
        for hook in hooks
    )


def test_eventbridge_targets_one_bounded_lambda(platform_plan: TerraformPlan) -> None:
    rules = list(_values(platform_plan, "aws_cloudwatch_event_rule"))
    targets = list(_values(platform_plan, "aws_cloudwatch_event_target"))
    functions = list(_values(platform_plan, "aws_lambda_function"))
    permissions = list(_values(platform_plan, "aws_lambda_permission"))

    assert len(rules) == len(targets) == len(functions) == len(permissions) == 1
    pattern = rules[0]["event_pattern"]
    assert "EC2 Instance-terminate Lifecycle Action" in pattern
    assert "weam-stockai-dev-workers" in pattern
    assert "weam-stockai-prod-workers" in pattern
    assert functions[0]["timeout"] == 240
    assert functions[0]["runtime"] == "python3.12"
    assert functions[0]["handler"] == "node_cleanup.handler"


def test_logs_metrics_and_alarms_are_bounded(platform_plan: TerraformPlan) -> None:
    log_groups = list(_values(platform_plan, "aws_cloudwatch_log_group"))
    alarms = list(_values(platform_plan, "aws_cloudwatch_metric_alarm"))

    assert len(log_groups) == 1
    assert log_groups[0]["retention_in_days"] == 14
    assert {alarm["metric_name"] for alarm in alarms} == {
        "Errors",
        "WorkerCleanupOutcome",
    }
    cleanup_alarms = [
        alarm for alarm in alarms if alarm["metric_name"] == "WorkerCleanupOutcome"
    ]
    assert len(cleanup_alarms) == 2
    assert all(
        alarm["namespace"] == "StockAI/WorkerLifecycle" for alarm in cleanup_alarms
    )
    assert all(alarm["threshold"] == 1 for alarm in cleanup_alarms)


def test_lambda_iam_excludes_application_access(platform_plan: TerraformPlan) -> None:
    policies = [
        value
        for value in _values(platform_plan, "aws_iam_role_policy")
        if value["name"] == "weam-stockai-worker-lifecycle"
    ]
    configurations = [
        resource
        for resource in resource_configurations(platform_plan, "aws_iam_role_policy")
        if resource["name"] == "node_cleanup"
    ]
    module_main = (
        PROJECT_ROOT
        / "infra"
        / "terraform"
        / "modules"
        / "worker-lifecycle"
        / "main.tf"
    ).read_text(encoding="utf-8")

    assert len(policies) == len(configurations) == 1
    assert (
        "data.aws_iam_policy_document.node_cleanup"
        in configurations[0]["expressions"]["policy"]["references"]
    )
    policy = module_main
    assert "ssm:SendCommand" in policy
    assert "AWS-RunShellScript" in policy
    assert "control_plane_instance_id" in policy
    assert "autoscaling:CompleteLifecycleAction" in policy
    assert "autoscaling:RecordLifecycleActionHeartbeat" in policy
    assert "resources = values(local.worker_asg_arns)" in policy
    assert "cloudwatch:PutMetricData" in policy
    assert "StockAI/WorkerLifecycle" in policy
    assert "ec2:DescribeInstances" in policy
    for forbidden in (
        "bedrock:",
        "dynamodb:",
        "secretsmanager:",
        "s3:",
        "ssm:GetParameter",
    ):
        assert forbidden not in policy
