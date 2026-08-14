"""Render-level contracts for the T20A observability collectors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OVERLAYS_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "overlays"
ENVIRONMENTS = ("dev", "prod")
DEPLOYMENT = json.loads(
    (PROJECT_ROOT / "deploy" / "config" / "deployment.json").read_text(encoding="utf-8")
)
LOKI_BUCKETS = {
    "replace-after-edge-terraform-apply",
    DEPLOYMENT["generated"]["loki_bucket_name"],
}
OBSERVABILITY_DEPLOYMENTS = {
    "stockai-alertmanager",
    "stockai-blackbox-exporter",
    "stockai-external-secrets",
    "stockai-grafana",
    "stockai-loki",
    "stockai-prometheus",
}


def _render(environment: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(OVERLAYS_ROOT / environment)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"{environment} overlay did not render:\n{result.stderr.strip()}")
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def _by_kind(resources: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [resource for resource in resources if resource["kind"] == kind]


def _named(resources: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(
        resource
        for resource in resources
        if resource["kind"] == kind and resource["metadata"]["name"] == name
    )


def _pod_spec(resource: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], resource["spec"]["template"]["spec"])


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_environment_observability_inventory_and_placement(environment: str) -> None:
    resources = _render(environment)
    deployments = {
        resource["metadata"]["name"]: resource
        for resource in _by_kind(resources, "Deployment")
    }
    assert OBSERVABILITY_DEPLOYMENTS <= set(deployments)

    for name in OBSERVABILITY_DEPLOYMENTS:
        pod_spec = _pod_spec(deployments[name])
        assert pod_spec["nodeSelector"] == {"stockai.io/environment": environment}
        assert pod_spec["tolerations"] == [
            {
                "effect": "NoSchedule",
                "key": "stockai.io/environment",
                "operator": "Equal",
                "value": environment,
            }
        ]
        assert pod_spec["serviceAccountName"] != "default"
        container = pod_spec["containers"][0]
        assert ":latest" not in container["image"]
        assert set(container["resources"]) == {"requests", "limits"}
        assert "livenessProbe" in container
        assert "readinessProbe" in container
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_prometheus_uses_retained_claim_and_bounded_retention(environment: str) -> None:
    resources = _render(environment)
    prometheus = _named(resources, "Deployment", "stockai-prometheus")
    pod_spec = _pod_spec(prometheus)
    container = pod_spec["containers"][0]

    assert prometheus["spec"]["replicas"] == 1
    assert prometheus["spec"]["strategy"]["type"] == "Recreate"
    assert "--storage.tsdb.retention.time=7d" in container["args"]
    assert "--storage.tsdb.retention.size=4GB" in container["args"]
    assert {
        "name": "data",
        "persistentVolumeClaim": {"claimName": "prometheus-data"},
    } in pod_spec["volumes"]
    assert any(
        mount["name"] == "data" and mount["mountPath"] == "/prometheus"
        for mount in container["volumeMounts"]
    )


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_grafana_is_git_provisioned_and_disposable(environment: str) -> None:
    resources = _render(environment)
    grafana = _named(resources, "Deployment", "stockai-grafana")
    pod_spec = _pod_spec(grafana)
    container = pod_spec["containers"][0]
    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}

    assert volumes["grafana-data"] == {
        "name": "grafana-data",
        "emptyDir": {"sizeLimit": "256Mi"},
    }
    assert not any("persistentVolumeClaim" in volume for volume in volumes.values())
    assert container["env"] == [
        {
            "name": "GF_SECURITY_ADMIN_PASSWORD",
            "valueFrom": {
                "secretKeyRef": {"key": "value", "name": "grafana-admin-password"}
            },
        },
        {"name": "AWS_REGION", "value": "us-east-1"},
    ]
    config = _named(resources, "ConfigMap", "stockai-observability-config")["data"]
    assert "allowUiUpdates: false" in config["dashboards.yaml"]
    assert "type: prometheus" in config["datasources.yaml"]
    assert "type: loki" in config["datasources.yaml"]
    assert "type: cloudwatch" in config["datasources.yaml"]
    assert "authType: default" in config["datasources.yaml"]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_loki_is_s3_prefixed_and_locally_bounded(environment: str) -> None:
    resources = _render(environment)
    loki = _named(resources, "Deployment", "stockai-loki")
    pod_spec = _pod_spec(loki)
    container = pod_spec["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}
    config = _named(resources, "ConfigMap", "stockai-observability-config")["data"]

    assert env["LOKI_S3_PREFIX"] == f"{environment}/"
    assert env["LOKI_S3_BUCKET"] in LOKI_BUCKETS
    assert env["LOKI_RETENTION_PERIOD"] == ("336h" if environment == "dev" else "2160h")
    assert "object_prefix: ${LOKI_S3_PREFIX}" in config["loki.yaml"]
    assert "bucketnames: ${LOKI_S3_BUCKET}" in config["loki.yaml"]
    assert "delete_request_store: s3" in config["loki.yaml"]
    assert "retention_period: ${LOKI_RETENTION_PERIOD}" in config["loki.yaml"]
    scratch = next(
        volume for volume in pod_spec["volumes"] if volume["name"] == "loki-data"
    )
    assert scratch["emptyDir"]["sizeLimit"] == "1Gi"
    assert "-config.expand-env=true" in container["args"]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_blackbox_targets_and_public_exposure_are_bounded(environment: str) -> None:
    resources = _render(environment)
    environment_config = _named(
        resources, "ConfigMap", "stockai-observability-environment"
    )["data"]
    targets = yaml.safe_load(environment_config["blackbox-targets.yaml"])
    assert targets == [
        {
            "labels": {"environment": environment},
            "targets": [
                f"https://app.{environment}.stockai.fursa.click",
                f"https://odoo.{environment}.stockai.fursa.click",
                f"https://grafana.{environment}.stockai.fursa.click",
            ],
        }
    ]

    ingress = _named(resources, "Ingress", "stockai-public")
    assert [rule["host"] for rule in ingress["spec"]["rules"]] == [
        f"app.{environment}.stockai.fursa.click",
        f"odoo.{environment}.stockai.fursa.click",
        f"grafana.{environment}.stockai.fursa.click",
    ]
    assert ingress["spec"]["rules"][2]["http"]["paths"][0]["backend"]["service"] == {
        "name": "grafana",
        "port": {"number": 3000},
    }
    private_services = {"prometheus", "loki", "alertmanager", "blackbox-exporter"}
    exposed = {
        path["backend"]["service"]["name"]
        for rule in ingress["spec"]["rules"]
        for path in rule["http"]["paths"]
    }
    assert not private_services & exposed


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_external_secrets_is_namespace_and_controller_class_scoped(
    environment: str,
) -> None:
    resources = _render(environment)
    controller = _named(resources, "Deployment", "stockai-external-secrets")
    args = _pod_spec(controller)["containers"][0]["args"]
    assert f"--namespace={environment}" in args
    assert f"--controller-class=stockai-{environment}" in args
    assert "--enable-cluster-store-reconciler=false" in args
    assert "--enable-cluster-external-secret-reconciler=false" in args
    assert "--enable-cluster-push-secret-reconciler=false" in args
    assert "--enable-push-secret-reconciler=false" in args

    store = _named(resources, "SecretStore", "aws-secrets-manager")
    assert store["spec"]["controller"] == f"stockai-{environment}"
    assert store["spec"]["retrySettings"] == {
        "maxRetries": 3,
        "retryInterval": "5s",
    }


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_fluent_bit_is_namespace_filtered_without_weakening_application_pss(
    environment: str,
) -> None:
    resources = _render(environment)
    collector = _named(resources, "DaemonSet", "stockai-fluent-bit")
    pod_spec = _pod_spec(collector)
    log_namespace = _named(resources, "Namespace", f"stockai-logs-{environment}")
    config = _named(resources, "ConfigMap", "stockai-fluent-bit-config")

    assert collector["metadata"]["namespace"] == f"stockai-logs-{environment}"
    assert config["metadata"]["namespace"] == f"stockai-logs-{environment}"
    assert (
        log_namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"]
        == "baseline"
    )
    app_namespace = _named(resources, "Namespace", environment)
    assert (
        app_namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"]
        == "restricted"
    )
    assert pod_spec["nodeSelector"] == {"stockai.io/environment": environment}
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["containers"][0]["env"] == [
        {"name": "ENVIRONMENT", "value": environment},
        {"name": "LOKI_HOST", "value": f"loki.{environment}.svc.cluster.local"},
        {"name": "TAIL_PATH", "value": f"/var/log/containers/*_{environment}_*.log"},
    ]
    assert pod_spec["volumes"][0] == {
        "name": "varlog",
        "hostPath": {"path": "/var/log", "type": "Directory"},
    }
    rendered = yaml.safe_dump_all(resources)
    assert "secretAccessKey" not in rendered
    assert "accessKeyId" not in rendered


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_observability_requests_fit_the_small_worker_budget(environment: str) -> None:
    resources = _render(environment)
    deployments = [
        resource
        for resource in _by_kind(resources, "Deployment")
        if resource["metadata"]["name"] in OBSERVABILITY_DEPLOYMENTS
    ]
    fluent_bit = _named(resources, "DaemonSet", "stockai-fluent-bit")
    containers = [
        *[
            container
            for deployment in deployments
            for container in _pod_spec(deployment)["containers"]
        ],
        *_pod_spec(fluent_bit)["containers"],
    ]

    def cpu_millicores(value: str) -> int:
        return (
            int(value.removesuffix("m")) if value.endswith("m") else int(value) * 1000
        )

    def memory_mib(value: str) -> int:
        return int(value.removesuffix("Mi"))

    assert (
        sum(
            cpu_millicores(container["resources"]["requests"]["cpu"])
            for container in containers
        )
        <= 300
    )
    assert (
        sum(
            memory_mib(container["resources"]["requests"]["memory"])
            for container in containers
        )
        <= 768
    )
