"""Plan-level contracts for the self-managed Kubernetes EC2 foundation."""

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
    plan_dir = tmp_path_factory.mktemp("terraform-platform-plan")
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


def test_resources_finds_managed_resources_in_nested_modules() -> None:
    plan: TerraformPlan = {
        "planned_values": {
            "root_module": {
                "resources": [
                    {
                        "mode": "managed",
                        "type": "aws_vpc",
                        "values": {"cidr_block": "10.0.0.0/16"},
                    },
                    {"mode": "data", "type": "aws_vpc", "values": {}},
                ],
                "child_modules": [
                    {
                        "resources": [
                            {
                                "mode": "managed",
                                "type": "aws_vpc",
                                "values": {"cidr_block": "10.1.0.0/16"},
                            }
                        ]
                    }
                ],
            }
        }
    }

    assert [
        resource["values"]["cidr_block"] for resource in resources(plan, "aws_vpc")
    ] == [
        "10.0.0.0/16",
        "10.1.0.0/16",
    ]


def test_plan_has_one_control_plane_and_two_fixed_capacity_worker_groups(
    platform_plan: TerraformPlan,
) -> None:
    assert len(resources(platform_plan, "aws_instance")) == 1
    assert len(resources(platform_plan, "aws_launch_template")) == 2
    assert len(resources(platform_plan, "aws_autoscaling_group")) == 2
    assert {
        (
            values["min_size"],
            values["desired_capacity"],
            values["max_size"],
        )
        for values in _values(platform_plan, "aws_autoscaling_group")
    } == {(1, 1, 3)}
    assert len(resources(platform_plan, "aws_autoscaling_policy")) == 0
    assert len(resources(platform_plan, "aws_eks_cluster")) == 0


def test_default_resource_names_and_tags_identify_weam(
    platform_plan: TerraformPlan,
) -> None:
    named_resource_types = {
        "aws_autoscaling_group": "name",
        "aws_iam_instance_profile": "name",
        "aws_iam_role": "name",
        "aws_security_group": "name",
    }
    for resource_type, name_attribute in named_resource_types.items():
        assert all(
            values[name_attribute].startswith("weam-stockai-")
            for values in _values(platform_plan, resource_type)
        )

    name_tagged_resource_types = {
        "aws_instance",
        "aws_internet_gateway",
        "aws_route_table",
        "aws_security_group",
        "aws_subnet",
        "aws_vpc",
    }
    for resource_type in name_tagged_resource_types:
        assert all(
            values["tags_all"]["Name"].startswith("weam-stockai-")
            for values in _values(platform_plan, resource_type)
        )

    assert all(
        values["name_prefix"].startswith("weam-stockai-")
        for values in _values(platform_plan, "aws_launch_template")
    )

    tagged_resource_types = {
        "aws_iam_instance_profile",
        "aws_iam_role",
        "aws_instance",
        "aws_internet_gateway",
        "aws_launch_template",
        "aws_route_table",
        "aws_security_group",
        "aws_subnet",
        "aws_vpc",
        "aws_vpc_security_group_egress_rule",
        "aws_vpc_security_group_ingress_rule",
    }
    for resource_type in tagged_resource_types:
        assert all(
            values["tags_all"]["Owner"] == "weam"
            for values in _values(platform_plan, resource_type)
        )

    for group in _values(platform_plan, "aws_autoscaling_group"):
        tags = {tag["key"]: tag["value"] for tag in group["tag"]}
        assert tags["Owner"] == "weam"

    control_plane = next(_values(platform_plan, "aws_instance"))
    assert control_plane["volume_tags"]["Owner"] == "weam"
    for launch_template in _values(platform_plan, "aws_launch_template"):
        assert all(
            specification["tags"]["Owner"] == "weam"
            for specification in launch_template["tag_specifications"]
        )


def test_plan_has_two_public_azs_without_a_nat_gateway(
    platform_plan: TerraformPlan,
) -> None:
    assert len(resources(platform_plan, "aws_vpc")) == 1
    assert len(resources(platform_plan, "aws_subnet")) == 2
    assert len(resources(platform_plan, "aws_internet_gateway")) == 1
    assert len(resources(platform_plan, "aws_nat_gateway")) == 0
    assert {
        values["availability_zone"] for values in _values(platform_plan, "aws_subnet")
    } == {
        "us-east-1a",
        "us-east-1b",
    }
    assert all(
        values["map_public_ip_on_launch"]
        for values in _values(platform_plan, "aws_subnet")
    )
    assert any(
        values.get("destination_cidr_block") == "0.0.0.0/0"
        for values in _values(platform_plan, "aws_route")
    )


def test_nodes_are_t3_medium_with_encrypted_bounded_root_volumes(
    platform_plan: TerraformPlan,
) -> None:
    control_plane = next(_values(platform_plan, "aws_instance"))
    assert control_plane["instance_type"] == "t3.medium"
    assert control_plane["root_block_device"] == [
        {
            **control_plane["root_block_device"][0],
            "encrypted": True,
            "volume_size": 30,
            "volume_type": "gp3",
        }
    ]

    for launch_template in _values(platform_plan, "aws_launch_template"):
        assert launch_template["instance_type"] == "t3.medium"
        root_volume = launch_template["block_device_mappings"][0]["ebs"][0]
        assert root_volume["encrypted"] in (True, "true")
        assert root_volume["volume_size"] == 30
        assert root_volume["volume_type"] == "gp3"


def test_worker_groups_are_environment_isolated_and_refresh_safely(
    platform_plan: TerraformPlan,
) -> None:
    groups = list(_values(platform_plan, "aws_autoscaling_group"))
    assert {group["tag"][0]["value"] for group in groups} == {"dev", "prod"}
    assert platform_plan["output_changes"]["dev_worker_az"]["after"] == "us-east-1a"
    assert platform_plan["output_changes"]["prod_worker_az"]["after"] == "us-east-1b"

    group_configuration = resource_configurations(
        platform_plan, "aws_autoscaling_group"
    )
    assert len(group_configuration) == 1
    assert group_configuration[0]["expressions"]["vpc_zone_identifier"][
        "references"
    ] == ["each.value.subnet_id", "each.value"]
    version_expression = group_configuration[0]["expressions"]["launch_template"][0][
        "version"
    ]
    assert version_expression.get("constant_value") not in {"$Latest", "$Default"}
    assert "aws_launch_template.worker" in version_expression["references"]

    for group in groups:
        refresh = group["instance_refresh"][0]
        assert refresh["strategy"] == "Rolling"
        assert refresh["preferences"][0]["min_healthy_percentage"] == 100
        assert refresh["preferences"][0]["max_healthy_percentage"] == 200


def test_node_roles_are_separate_and_only_attach_ssm_channels(
    platform_plan: TerraformPlan,
) -> None:
    node_role_names = {
        values["name"]
        for values in _values(platform_plan, "aws_iam_role")
        if values["name"].endswith(("control-plane", "dev-worker", "prod-worker"))
    }
    assert node_role_names == {
        "weam-stockai-control-plane",
        "weam-stockai-dev-worker",
        "weam-stockai-prod-worker",
    }
    assert len(resources(platform_plan, "aws_iam_instance_profile")) == 3
    attachments = list(_values(platform_plan, "aws_iam_role_policy_attachment"))
    assert len(attachments) == 3
    assert {attachment["policy_arn"] for attachment in attachments} == {
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    }
    assert all(
        "AmazonEKSClusterPolicy" not in attachment["policy_arn"]
        for attachment in attachments
    )


def test_public_ingress_is_limited_to_admin_ssh_and_api(
    platform_plan: TerraformPlan,
) -> None:
    ingress_rules = list(_values(platform_plan, "aws_vpc_security_group_ingress_rule"))
    public_rules = [rule for rule in ingress_rules if rule.get("cidr_ipv4")]

    assert {rule["cidr_ipv4"] for rule in public_rules} == {"203.0.113.10/32"}
    assert {(rule["from_port"], rule["to_port"]) for rule in public_rules} == {
        (22, 22),
        (6443, 6443),
    }
    assert all(rule.get("cidr_ipv4") != "0.0.0.0/0" for rule in ingress_rules)


def test_platform_exports_the_approved_t16_interface(
    platform_plan: TerraformPlan,
) -> None:
    expected_outputs = {
        "alb_subnet_ids",
        "control_plane_instance_id",
        "control_plane_private_ip",
        "control_plane_role_name",
        "dev_worker_asg_name",
        "dev_worker_az",
        "dev_worker_role_name",
        "prod_worker_asg_name",
        "prod_worker_az",
        "prod_worker_role_name",
        "worker_security_group_id",
    }

    assert set(platform_plan["output_changes"]) == expected_outputs


def test_inactive_worker_capacity_is_the_only_supported_zero_state(
    tmp_path: Path,
) -> None:
    plan = create_plan(
        PLATFORM_ROOT,
        tmp_path / "inactive.tfplan",
        {
            "administrator_cidr": "203.0.113.10/32",
            "ami_id": "ami-0123456789abcdef0",
            "aws_account_id": "123456789012",
            "cluster_name": "stockai-test",
            "worker_capacity": "{ min = 0, desired = 0, max = 3 }",
        },
    )

    assert {
        (
            values["min_size"],
            values["desired_capacity"],
            values["max_size"],
        )
        for values in _values(plan, "aws_autoscaling_group")
    } == {(0, 0, 3)}


def test_temporary_capacity_can_change_only_the_selected_environment(
    tmp_path: Path,
) -> None:
    plan = create_plan(
        PLATFORM_ROOT,
        tmp_path / "dev-capacity.tfplan",
        {
            "administrator_cidr": "203.0.113.10/32",
            "ami_id": "ami-0123456789abcdef0",
            "aws_account_id": "123456789012",
            "cluster_name": "stockai-test",
            "worker_capacity_overrides": (
                "{ dev = { min = 1, desired = 2, max = 3 } }"
            ),
        },
    )
    capacities = {
        values["tag"][0]["value"]: (
            values["min_size"],
            values["desired_capacity"],
            values["max_size"],
        )
        for values in _values(plan, "aws_autoscaling_group")
    }

    assert capacities == {"dev": (1, 2, 3), "prod": (1, 1, 3)}


def test_worker_capacity_rejects_desired_below_minimum(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="use inactive 0/0/3"):
        create_plan(
            PLATFORM_ROOT,
            tmp_path / "invalid.tfplan",
            {
                "administrator_cidr": "203.0.113.10/32",
                "ami_id": "ami-0123456789abcdef0",
                "aws_account_id": "123456789012",
                "cluster_name": "stockai-test",
                "worker_capacity": "{ min = 1, desired = 0, max = 3 }",
            },
        )
