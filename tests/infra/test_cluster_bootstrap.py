"""Contracts for the self-managed kubeadm cluster bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.infra.plan import (
    TerraformPlan,
    create_plan,
    resources,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = PROJECT_ROOT / "infra" / "terraform" / "platform"
CLUSTER_ROOT = PROJECT_ROOT / "infra" / "cluster"
NETWORK_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "cluster" / "network"
COMPUTE_ROOT = PROJECT_ROOT / "infra" / "terraform" / "modules" / "compute"


@pytest.fixture(scope="module")
def platform_plan(tmp_path_factory: pytest.TempPathFactory) -> TerraformPlan:
    plan_dir = tmp_path_factory.mktemp("terraform-cluster-bootstrap-plan")
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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_worker_pods_can_use_imdsv2_without_weakening_the_control_plane(
    platform_plan: TerraformPlan,
) -> None:
    control_plane = next(_values(platform_plan, "aws_instance"))
    workers = list(_values(platform_plan, "aws_launch_template"))

    assert control_plane["metadata_options"][0] == {
        "http_endpoint": "enabled",
        "http_protocol_ipv6": "disabled",
        "http_put_response_hop_limit": 1,
        "http_tokens": "required",
        "instance_metadata_tags": "disabled",
    }
    assert len(workers) == 2
    assert all(
        worker["metadata_options"][0]["http_put_response_hop_limit"] == 2
        and worker["metadata_options"][0]["http_tokens"] == "required"
        and worker["metadata_options"][0]["instance_metadata_tags"] == "disabled"
        for worker in workers
    )


def test_plan_creates_one_encrypted_runtime_owned_join_parameter(
    platform_plan: TerraformPlan,
) -> None:
    parameters = list(_values(platform_plan, "aws_ssm_parameter"))

    assert len(parameters) == 1
    assert parameters[0]["name"] == ("/stockai/weam-stockai/kubeadm/join-command")
    assert parameters[0]["type"] == "SecureString"
    assert parameters[0]["value"] == "pending-control-plane-initialization"

    module_main = _read(
        PROJECT_ROOT
        / "infra"
        / "terraform"
        / "modules"
        / "cluster-bootstrap"
        / "main.tf"
    )
    assert "ignore_changes = [value]" in module_main
    assert all("join" not in name for name in platform_plan["output_changes"])


def test_join_parameter_iam_is_role_specific_and_exactly_scoped(
    platform_plan: TerraformPlan,
) -> None:
    policies = list(_values(platform_plan, "aws_iam_role_policy"))
    bootstrap_policies = {
        policy["name"]: policy
        for policy in policies
        if "kubeadm-join" in policy["name"]
    }

    assert set(bootstrap_policies) == {
        "weam-stockai-control-plane-kubeadm-join-write",
        "weam-stockai-dev-worker-kubeadm-join-read",
        "weam-stockai-prod-worker-kubeadm-join-read",
    }
    parameter_arn = (
        "arn:aws:ssm:us-east-1:123456789012:parameter/stockai/"
        "weam-stockai/kubeadm/join-command"
    )

    control_plane = bootstrap_policies["weam-stockai-control-plane-kubeadm-join-write"]
    assert control_plane["role"] == "weam-stockai-control-plane"
    assert '"Action":"ssm:PutParameter"' in control_plane["policy"]
    assert parameter_arn in control_plane["policy"]
    assert "ssm:GetParameter" not in control_plane["policy"]

    for environment in ("dev", "prod"):
        worker = bootstrap_policies[
            f"weam-stockai-{environment}-worker-kubeadm-join-read"
        ]
        assert worker["role"] == f"weam-stockai-{environment}-worker"
        assert '"Action":"ssm:GetParameter"' in worker["policy"]
        assert parameter_arn in worker["policy"]
        assert "ssm:PutParameter" not in worker["policy"]

    attachments = list(_values(platform_plan, "aws_iam_role_policy_attachment"))
    assert all(
        "AmazonEKSClusterPolicy" not in attachment["policy_arn"]
        for attachment in attachments
    )


def test_rotation_uses_finite_tokens_without_disclosing_the_command() -> None:
    script = _read(CLUSTER_ROOT / "rotate-join-token.sh")
    service = _read(CLUSTER_ROOT / "kubeadm-token-rotation.service")
    timer = _read(CLUSTER_ROOT / "kubeadm-token-rotation.timer")

    assert "kubeadm token create --ttl 24h --print-join-command" in script
    assert "aws ssm put-parameter" in script
    assert "--type SecureString" in script
    assert "--overwrite" in script
    assert "set -x" not in script
    assert 'echo "$join_command"' not in script
    assert "StandardOutput=journal" in service
    assert "StartLimitIntervalSec=120" in service
    assert "StartLimitBurst=5" in service
    assert "Restart=on-failure" in service
    assert "RestartSec=15s" in service
    assert "OnBootSec=1min" in timer
    assert "OnUnitActiveSec=12h" in timer
    assert "Persistent=true" in timer


def test_worker_join_rejects_shell_text_and_hard_binds_node_identity() -> None:
    script = _read(CLUSTER_ROOT / "join-worker.sh")

    assert '[[ "$environment" =~ ^(dev|prod)$ ]]' in script
    assert "MAX_ATTEMPTS=" in script
    assert "aws ssm get-parameter" in script
    assert "--with-decryption" in script
    assert "--query Parameter.Value" in script
    assert "--output text" in script
    assert "--cli-connect-timeout 5" in script
    assert "--cli-read-timeout 10" in script
    assert 'IMDS_ENDPOINT="http://169.254.169.254/latest"' in script
    assert '"${IMDS_ENDPOINT}/meta-data/local-hostname"' in script
    assert "--node-name" in script
    assert "stockai.io/environment=" in script
    assert "--register-with-taints=stockai.io/environment=" in script
    assert "read -r -a join_args" in script
    assert 'timeout 5m "${join_args[@]}"' in script
    assert "eval" not in script
    assert "sha256:[[:xdigit:]]{64}" in script


def test_worker_join_validates_exact_arguments_and_reports_safe_categories() -> None:
    script = _read(CLUSTER_ROOT / "join-worker.sh")

    assert 'last_failure_reason="ssm-read-failed"' in script
    assert "$'\\n'" in script
    assert "$'\\r'" in script
    assert "join_args=()" in script
    assert 'read -r -a join_args <<<"$join_command"' in script
    assert "if ((${#join_args[@]} != 7)); then" in script
    assert '"${join_args[0]}" != "kubeadm"' in script
    assert '"${join_args[1]}" != "join"' in script
    assert '"${join_args[2]}" != "$expected_api_endpoint"' in script
    assert '"${join_args[3]}" != "--token"' in script
    assert '"${join_args[5]}" != "--discovery-token-ca-cert-hash"' in script
    assert "^[a-z0-9]{6}\\.[a-z0-9]{16}$" in script
    assert "^sha256:[[:xdigit:]]{64}$" in script

    for category in (
        "ssm-read-failed",
        "invalid-command-shape",
        "endpoint-mismatch",
        "invalid-token-format",
        "invalid-hash-format",
        "kubeadm-join-failed",
    ):
        assert f'last_failure_reason="{category}"' in script

    assert (
        'echo "worker join failed after ${MAX_ATTEMPTS} attempts: '
        '${last_failure_reason}" >&2'
    ) in script
    assert 'echo "$join_command"' not in script
    assert "eval" not in script


def test_node_install_and_cni_are_pinned_for_the_approved_cluster() -> None:
    install_script = _read(CLUSTER_ROOT / "install-node.sh")
    init_script = _read(CLUSTER_ROOT / "init-control-plane.sh")
    network = _read(NETWORK_ROOT / "kustomization.yaml")

    assert 'KUBERNETES_MINOR="v1.35"' in install_script
    assert 'KUBERNETES_VERSION="1.35.5-1.1"' in install_script
    assert 'CONTAINERD_VERSION="2.3.1"' in install_script
    assert 'RUNC_VERSION="1.5.1"' in install_script
    assert 'CNI_PLUGINS_VERSION="v1.9.1"' in install_script
    assert "SystemdCgroup = true" in install_script
    assert "apt-mark hold kubelet kubeadm kubectl" in install_script
    assert "--pod-network-cidr=192.168.0.0/16" in init_script
    assert "--control-plane-endpoint" in init_script
    assert "chmod 0600 /etc/kubernetes/admin.conf" in init_script
    assert "v3.30.2" in init_script
    assert "v3.30.2" in network
    assert "latest" not in network


def test_node_install_accepts_deb_or_snap_ssm_agent_and_fails_closed() -> None:
    install_script = _read(CLUSTER_ROOT / "install-node.sh")

    assert 'SSM_AGENT_DEB_SERVICE="amazon-ssm-agent.service"' in install_script
    assert (
        'SSM_AGENT_SNAP_SERVICE="snap.amazon-ssm-agent.amazon-ssm-agent.service"'
        in install_script
    )
    assert 'systemctl enable --now "$SSM_AGENT_DEB_SERVICE"' in install_script
    assert "snap list amazon-ssm-agent" in install_script
    assert "snap start --enable amazon-ssm-agent" in install_script
    assert 'systemctl is-active --quiet "$ssm_agent_service"' in install_script
    assert 'ssm_agent_service=""' in install_script
    assert (
        "the approved Ubuntu AMI must include an active amazon-ssm-agent"
        in install_script
    )


def test_compute_bootstraps_control_plane_and_environment_workers() -> None:
    compute_main = _read(COMPUTE_ROOT / "main.tf")
    control_plane_template = _read(COMPUTE_ROOT / "control-plane-user-data.sh.tftpl")
    worker_template = _read(COMPUTE_ROOT / "worker-user-data.sh.tftpl")

    assert 'user_data = templatefile("${path.module}/control-plane' in compute_main
    assert (
        'user_data = base64encode(templatefile("${path.module}/worker' in compute_main
    )
    assert "control-plane-user-data.sh.tftpl" in compute_main
    assert "base64gzip" in compute_main
    assert "stockai-init-control-plane" in control_plane_template
    assert "kubeadm-token-rotation.timer" in control_plane_template
    assert "stockai-join-worker" in worker_template
    assert "expected_api_endpoint" in worker_template
    assert "join_parameter_name" in worker_template
