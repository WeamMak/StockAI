"""Plan contracts for the shared HTTPS edge and operational cost controls."""

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
EDGE_ROOT = PROJECT_ROOT / "infra" / "terraform" / "edge"
HOSTNAMES = {
    "app.dev.example.com",
    "odoo.dev.example.com",
    "grafana.dev.example.com",
    "app.prod.example.com",
    "odoo.prod.example.com",
    "grafana.prod.example.com",
}


@pytest.fixture(scope="module")
def edge_plan(tmp_path_factory: pytest.TempPathFactory) -> TerraformPlan:
    plan_dir = tmp_path_factory.mktemp("terraform-edge-plan")
    return create_plan(
        EDGE_ROOT,
        plan_dir / "edge.tfplan",
        {
            "alb_subnet_ids": '["subnet-11111111","subnet-22222222"]',
            "domain_name": "example.com",
            "loki_bucket_name": "weam-stockai-t17-operational-logs",
            "route53_zone_id": "Z0123456789ABCDEFGHIJ",
            "vpc_id": "vpc-0123456789abcdef0",
            "worker_asg_names": (
                '{ dev = "weam-stockai-dev-workers", '
                'prod = "weam-stockai-prod-workers" }'
            ),
            "worker_security_group_id": "sg-0123456789abcdef0",
        },
    )


def _values(plan: TerraformPlan, resource_type: str) -> Iterator[dict[str, Any]]:
    return (resource["values"] for resource in resources(plan, resource_type))


def test_shared_alb_has_https_redirect_and_exact_host_rules(
    edge_plan: TerraformPlan,
) -> None:
    load_balancers = list(_values(edge_plan, "aws_lb"))
    assert len(load_balancers) == 1
    assert load_balancers[0]["internal"] is False
    assert load_balancers[0]["load_balancer_type"] == "application"
    assert set(load_balancers[0]["subnets"]) == {
        "subnet-11111111",
        "subnet-22222222",
    }

    listeners = list(_values(edge_plan, "aws_lb_listener"))
    assert {(listener["port"], listener["protocol"]) for listener in listeners} == {
        (80, "HTTP"),
        (443, "HTTPS"),
    }
    http = next(listener for listener in listeners if listener["port"] == 80)
    redirect = http["default_action"][0]["redirect"][0]
    assert redirect["port"] == "443"
    assert redirect["protocol"] == "HTTPS"
    assert redirect["status_code"] == "HTTP_301"

    rules = list(_values(edge_plan, "aws_lb_listener_rule"))
    assert len(rules) == 2
    routed_hostnames = {
        hostname
        for rule in rules
        for condition in rule["condition"]
        for host_header in condition["host_header"]
        for hostname in host_header["values"]
    }
    assert routed_hostnames == HOSTNAMES


def test_target_groups_use_fixed_nodeport_and_exact_asg_membership(
    edge_plan: TerraformPlan,
) -> None:
    target_groups = list(_values(edge_plan, "aws_lb_target_group"))
    assert len(target_groups) == 2
    assert {group["port"] for group in target_groups} == {32080}
    assert {group["protocol"] for group in target_groups} == {"HTTP"}
    assert {group["target_type"] for group in target_groups} == {"instance"}
    assert {group["health_check"][0]["path"] for group in target_groups} == {"/healthz"}

    attachments = list(_values(edge_plan, "aws_autoscaling_attachment"))
    assert {attachment["autoscaling_group_name"] for attachment in attachments} == {
        "weam-stockai-dev-workers",
        "weam-stockai-prod-workers",
    }
    assert len(resources(edge_plan, "aws_lb_target_group_attachment")) == 0


def test_only_alb_is_public_and_worker_nodeport_trusts_only_alb(
    edge_plan: TerraformPlan,
) -> None:
    ingress_rules = list(_values(edge_plan, "aws_vpc_security_group_ingress_rule"))
    public_rules = [rule for rule in ingress_rules if rule.get("cidr_ipv4")]
    assert {
        (rule["cidr_ipv4"], rule["from_port"], rule["to_port"]) for rule in public_rules
    } == {
        ("0.0.0.0/0", 80, 80),
        ("0.0.0.0/0", 443, 443),
    }

    worker_rule = next(
        rule
        for rule in ingress_rules
        if rule.get("security_group_id") == "sg-0123456789abcdef0"
    )
    assert worker_rule["from_port"] == 32080
    assert worker_rule["to_port"] == 32080
    worker_config = next(
        configuration
        for configuration in resource_configurations(
            edge_plan,
            "aws_vpc_security_group_ingress_rule",
        )
        if configuration["name"] == "worker_ingress"
    )
    assert worker_config["expressions"]["referenced_security_group_id"][
        "references"
    ] == ["aws_security_group.alb.id", "aws_security_group.alb"]
    assert worker_rule.get("cidr_ipv4") is None


def test_acm_dns_and_aliases_cover_exactly_six_hostnames(
    edge_plan: TerraformPlan,
) -> None:
    certificates = list(_values(edge_plan, "aws_acm_certificate"))
    assert len(certificates) == 1
    certificate_names = {
        certificates[0]["domain_name"],
        *certificates[0]["subject_alternative_names"],
    }
    assert certificate_names == HOSTNAMES
    assert certificates[0]["validation_method"] == "DNS"
    assert len(resources(edge_plan, "aws_acm_certificate_validation")) == 1

    aliases = list(_values(edge_plan, "aws_route53_record"))
    alias_names = {record["name"] for record in aliases if record.get("alias")}
    assert alias_names == HOSTNAMES


def test_loki_bucket_is_encrypted_private_versioned_and_retained_by_prefix(
    edge_plan: TerraformPlan,
) -> None:
    assert len(resources(edge_plan, "aws_s3_bucket")) == 1
    encryption = next(
        _values(edge_plan, "aws_s3_bucket_server_side_encryption_configuration")
    )
    assert (
        encryption["rule"][0]["apply_server_side_encryption_by_default"][0][
            "sse_algorithm"
        ]
        == "AES256"
    )
    access = next(_values(edge_plan, "aws_s3_bucket_public_access_block"))
    assert all(
        access[name]
        for name in (
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
        )
    )
    versioning = next(_values(edge_plan, "aws_s3_bucket_versioning"))
    assert versioning["versioning_configuration"][0]["status"] == "Enabled"

    lifecycle = next(_values(edge_plan, "aws_s3_bucket_lifecycle_configuration"))
    rules = {rule["id"]: rule for rule in lifecycle["rule"]}
    assert rules["dev-retention"]["filter"][0]["prefix"] == "dev/"
    assert rules["dev-retention"]["expiration"][0]["days"] == 14
    assert rules["prod-retention"]["filter"][0]["prefix"] == "prod/"
    assert rules["prod-retention"]["expiration"][0]["days"] == 90


def test_email_backed_aws_budgets_are_not_provisioned(
    edge_plan: TerraformPlan,
) -> None:
    assert not resources(edge_plan, "aws_budgets_budget")
    source = (EDGE_ROOT / "variables.tf").read_text(encoding="utf-8")
    assert "budget_notification_email" not in source


def test_edge_outputs_and_excluded_services_match_t17(
    edge_plan: TerraformPlan,
) -> None:
    assert set(edge_plan["output_changes"]) == {
        "alb_dns_name",
        "alb_zone_id",
        "certificate_arn",
        "dev_target_group_arn",
        "hostnames",
        "loki_bucket_arn",
        "prod_target_group_arn",
    }
    assert set(edge_plan["output_changes"]["hostnames"]["after"].values()) == HOSTNAMES

    for resource_type in (
        "aws_autoscaling_policy",
        "aws_db_instance",
        "aws_efs_file_system",
        "aws_eks_cluster",
        "aws_nat_gateway",
        "aws_ses_email_identity",
        "aws_sns_topic",
        "aws_sqs_queue",
    ):
        assert not resources(edge_plan, resource_type)
