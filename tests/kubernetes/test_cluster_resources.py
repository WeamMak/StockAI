"""Render-level contracts for the shared Kubernetes controllers."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLUSTER_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "cluster"


@pytest.fixture(scope="module")
def resources() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(CLUSTER_ROOT)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"cluster resources did not render:\n{result.stderr.strip()}")
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def _resource(
    resources: list[dict[str, Any]],
    kind: str,
    name: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    matches = [
        resource
        for resource in resources
        if resource["kind"] == kind
        and resource["metadata"]["name"] == name
        and (namespace is None or resource["metadata"].get("namespace") == namespace)
    ]
    assert len(matches) == 1, f"expected one {kind}/{name}, found {len(matches)}"
    return matches[0]


def _pod_spec(resource: dict[str, Any]) -> dict[str, Any]:
    template = resource["spec"].get("template", resource["spec"])
    return cast(dict[str, Any], template["spec"])


def _workloads(resources: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    return (
        resource
        for resource in resources
        if resource["kind"] in {"DaemonSet", "Deployment", "Job", "StatefulSet"}
    )


def _has_control_plane_placement(pod_spec: dict[str, Any]) -> bool:
    selector = pod_spec.get("nodeSelector", {})
    tolerations = pod_spec.get("tolerations", [])
    return selector.get("node-role.kubernetes.io/control-plane") == "" and any(
        toleration.get("key") == "node-role.kubernetes.io/control-plane"
        and toleration.get("effect") == "NoSchedule"
        and toleration.get("operator") == "Exists"
        for toleration in tolerations
    )


def _has_environment_affinity(pod_spec: dict[str, Any]) -> bool:
    terms = (
        pod_spec.get("affinity", {})
        .get("nodeAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms", [])
    )
    return any(
        expression.get("key") == "stockai.io/environment"
        and expression.get("operator") == "Exists"
        for term in terms
        for expression in term.get("matchExpressions", [])
    )


def test_external_secrets_v1_crds_are_pinned_with_the_cluster(
    resources: list[dict[str, Any]],
) -> None:
    crds = {
        resource["metadata"]["name"]: resource
        for resource in resources
        if resource["kind"] == "CustomResourceDefinition"
        and resource["metadata"]["name"].endswith(".external-secrets.io")
    }
    assert {
        "externalsecrets.external-secrets.io",
        "secretstores.external-secrets.io",
    } <= (set(crds))
    for name in (
        "externalsecrets.external-secrets.io",
        "secretstores.external-secrets.io",
    ):
        versions = crds[name]["spec"]["versions"]
        assert any(
            version["name"] == "v1" and version["served"] and version["storage"]
            for version in versions
        )


def test_all_controller_images_are_immutable_release_pins(
    resources: list[dict[str, Any]],
) -> None:
    images = {
        container["image"]
        for workload in _workloads(resources)
        for container in _pod_spec(workload).get("containers", [])
        + _pod_spec(workload).get("initContainers", [])
    }

    assert any("ingress-nginx/controller:v1.15.1@sha256:" in image for image in images)
    assert "public.ecr.aws/ebs-csi-driver/aws-ebs-csi-driver:v1.63.1" in images
    assert "registry.k8s.io/metrics-server/metrics-server:v0.9.0" in images
    assert "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.19.1" in images
    assert "quay.io/argoproj/argocd:v3.5.0" in images
    assert all(
        "latest" not in image
        and ("@sha256:" in image or ":" in image.rsplit("/", maxsplit=1)[-1])
        for image in images
    )


def test_nginx_runs_once_on_each_environment_worker_at_the_fixed_nodeport(
    resources: list[dict[str, Any]],
) -> None:
    controller = _resource(
        resources, "DaemonSet", "ingress-nginx-controller", "ingress-nginx"
    )
    pod_spec = _pod_spec(controller)
    assert _has_environment_affinity(pod_spec)
    assert {
        (toleration.get("key"), toleration.get("value"), toleration.get("effect"))
        for toleration in pod_spec["tolerations"]
    } >= {
        ("stockai.io/environment", "dev", "NoSchedule"),
        ("stockai.io/environment", "prod", "NoSchedule"),
    }

    service = _resource(
        resources, "Service", "ingress-nginx-controller", "ingress-nginx"
    )
    assert service["spec"]["type"] == "NodePort"
    assert service["spec"]["externalTrafficPolicy"] == "Local"
    assert service["spec"]["ports"] == [
        {
            "appProtocol": "http",
            "name": "http",
            "nodePort": 32080,
            "port": 80,
            "protocol": "TCP",
            "targetPort": "http",
        }
    ]


def test_ebs_csi_is_static_only_and_split_between_control_plane_and_workers(
    resources: list[dict[str, Any]],
) -> None:
    controller = _resource(resources, "Deployment", "ebs-csi-controller", "kube-system")
    assert controller["spec"]["replicas"] == 1
    controller_spec = _pod_spec(controller)
    assert _has_control_plane_placement(controller_spec)
    assert controller_spec["hostNetwork"] is True
    assert {container["name"] for container in controller_spec["containers"]} == {
        "csi-attacher",
        "ebs-plugin",
        "liveness-probe",
    }

    node = _resource(resources, "DaemonSet", "ebs-csi-node", "kube-system")
    node_spec = _pod_spec(node)
    assert _has_environment_affinity(node_spec)
    assert node_spec["hostNetwork"] is True

    assert not any(resource["kind"] == "StorageClass" for resource in resources)
    assert not any(
        resource["kind"] in {"ClusterRole", "ClusterRoleBinding"}
        and any(
            component in resource["metadata"]["name"]
            for component in ("provisioner", "resizer", "snapshotter")
        )
        for resource in resources
    )
    _resource(resources, "CSIDriver", "ebs.csi.aws.com")


def test_metrics_api_and_kube_state_metrics_run_on_the_control_plane(
    resources: list[dict[str, Any]],
) -> None:
    metrics_server = _resource(resources, "Deployment", "metrics-server", "kube-system")
    kube_state_metrics = _resource(
        resources, "Deployment", "kube-state-metrics", "kube-system"
    )
    metrics_server_spec = _pod_spec(metrics_server)
    assert _has_control_plane_placement(metrics_server_spec)
    assert _has_control_plane_placement(_pod_spec(kube_state_metrics))

    metrics_server_container = next(
        container
        for container in metrics_server_spec["containers"]
        if container["name"] == "metrics-server"
    )
    assert metrics_server_container["args"].count("--kubelet-insecure-tls") == 1

    metrics_api = _resource(resources, "APIService", "v1beta1.metrics.k8s.io")
    assert metrics_api["spec"]["group"] == "metrics.k8s.io"
    assert metrics_api["spec"]["service"] == {
        "name": "metrics-server",
        "namespace": "kube-system",
    }


def test_argocd_is_declarative_and_controller_namespaces_are_isolated(
    resources: list[dict[str, Any]],
) -> None:
    namespaces = {
        resource["metadata"]["name"]
        for resource in resources
        if resource["kind"] == "Namespace"
    }
    assert {"argocd", "ingress-nginx"} <= namespaces

    argocd_workloads = [
        workload
        for workload in _workloads(resources)
        if workload["metadata"].get("namespace") == "argocd"
    ]
    assert argocd_workloads
    assert all(
        _has_control_plane_placement(_pod_spec(item)) for item in argocd_workloads
    )

    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    workflows = "\n".join(
        path.read_text(encoding="utf-8") for path in workflow_root.glob("*.y*ml")
    )
    assert "kubectl apply" not in workflows
    assert "kubectl create" not in workflows


def test_cluster_controllers_do_not_add_cert_manager_or_business_workloads(
    resources: list[dict[str, Any]],
) -> None:
    assert not any(
        "cert-manager" in resource["metadata"]["name"] for resource in resources
    )
    assert not any(
        "cert-manager" in container["image"]
        for workload in _workloads(resources)
        for container in _pod_spec(workload).get("containers", [])
    )
    assert "stockai.io/environment" not in {
        toleration.get("key")
        for workload in _workloads(resources)
        if workload["metadata"].get("namespace") not in {"ingress-nginx", "kube-system"}
        for toleration in _pod_spec(workload).get("tolerations", [])
    }


def test_non_argocd_cluster_rbac_does_not_use_wildcards_or_default_accounts(
    resources: list[dict[str, Any]],
) -> None:
    roles = [
        resource
        for resource in resources
        if resource["kind"] in {"ClusterRole", "Role"}
        and not resource["metadata"]["name"].startswith("argocd-")
    ]
    assert roles
    assert all(
        "*" not in rule.get("verbs", [])
        and "*" not in rule.get("resources", [])
        and "*" not in rule.get("apiGroups", [])
        for role in roles
        for rule in role.get("rules", [])
    )

    bindings = [
        resource
        for resource in resources
        if resource["kind"] in {"ClusterRoleBinding", "RoleBinding"}
    ]
    assert all(
        subject.get("name") != "default"
        for binding in bindings
        for subject in binding.get("subjects", [])
    )
