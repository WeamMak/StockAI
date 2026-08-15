"""Render-level contracts for the T19B application workloads."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OVERLAYS_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "overlays"
ENVIRONMENTS = ("dev", "prod")
APPLICATION_DEPLOYMENTS = {
    "stockai-agent-api",
    "stockai-frontend",
    "stockai-odoo",
    "stockai-postgresql",
    "stockai-procurement-mcp",
}
STATELESS = {
    "stockai-agent-api",
    "stockai-frontend",
    "stockai-procurement-mcp",
}
IMAGE_PATTERN = re.compile(r"^[^:@]+(?:/[^:@]+)*@sha256:[0-9a-f]{64}$")


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
    if resource["kind"] == "CronJob":
        return cast(
            dict[str, Any],
            resource["spec"]["jobTemplate"]["spec"]["template"]["spec"],
        )
    return cast(dict[str, Any], resource["spec"]["template"]["spec"])


def _containers(resource: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _pod_spec(resource)["containers"])


def _cpu_millicores(value: str) -> int:
    return int(value.removesuffix("m")) if value.endswith("m") else int(value) * 1000


def _memory_mib(value: str) -> int:
    if value.endswith("Mi"):
        return int(value.removesuffix("Mi"))
    if value.endswith("Gi"):
        return int(value.removesuffix("Gi")) * 1024
    raise AssertionError(f"unsupported memory quantity: {value}")


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_complete_application_inventory_and_immutable_images(environment: str) -> None:
    resources = _render(environment)
    deployments = {
        resource["metadata"]["name"]: resource
        for resource in _by_kind(resources, "Deployment")
    }

    assert APPLICATION_DEPLOYMENTS <= set(deployments)
    assert {
        resource["metadata"]["name"] for resource in _by_kind(resources, "Job")
    } == {"stockai-odoo-bootstrap", "stockai-odoo-seed"}
    assert {
        resource["metadata"]["name"] for resource in _by_kind(resources, "CronJob")
    } == {"stockai-daily-scan"}

    project_images = {
        name: _containers(deployment)[0]["image"]
        for name, deployment in deployments.items()
        if name in APPLICATION_DEPLOYMENTS and name != "stockai-postgresql"
    }
    project_images["stockai-odoo-bootstrap"] = _containers(
        _named(resources, "Job", "stockai-odoo-bootstrap")
    )[0]["image"]
    project_images["stockai-odoo-seed"] = _containers(
        _named(resources, "Job", "stockai-odoo-seed")
    )[0]["image"]
    project_images["stockai-daily-scan"] = _containers(
        _named(resources, "CronJob", "stockai-daily-scan")
    )[0]["image"]
    assert all(IMAGE_PATTERN.fullmatch(image) for image in project_images.values())
    assert project_images["stockai-odoo"] == project_images["stockai-odoo-bootstrap"]
    assert project_images["stockai-odoo"] == project_images["stockai-odoo-seed"]
    assert project_images["stockai-agent-api"] == project_images["stockai-daily-scan"]
    assert IMAGE_PATTERN.fullmatch(
        _containers(deployments["stockai-postgresql"])[0]["image"]
    )


def test_dev_and_prod_have_the_same_project_image_inventory() -> None:
    images_by_environment: dict[str, dict[str, str]] = {}
    for environment in ENVIRONMENTS:
        resources = _render(environment)
        images_by_environment[environment] = {
            resource["metadata"]["name"]: _containers(resource)[0]["image"]
            for resource in [
                *_by_kind(resources, "Deployment"),
                *_by_kind(resources, "Job"),
                *_by_kind(resources, "CronJob"),
            ]
            if resource["metadata"]["name"]
            in APPLICATION_DEPLOYMENTS
            | {
                "stockai-odoo-bootstrap",
                "stockai-odoo-seed",
                "stockai-daily-scan",
            }
            and resource["metadata"]["name"] != "stockai-postgresql"
        }

    assert set(images_by_environment["dev"]) == set(images_by_environment["prod"])


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_stateful_storage_and_finite_odoo_bootstrap(environment: str) -> None:
    resources = _render(environment)
    odoo = _named(resources, "Deployment", "stockai-odoo")
    postgresql = _named(resources, "Deployment", "stockai-postgresql")
    bootstrap = _named(resources, "Job", "stockai-odoo-bootstrap")

    assert odoo["spec"]["replicas"] == 1
    assert postgresql["spec"]["replicas"] == 1
    assert odoo["spec"]["strategy"]["type"] == "Recreate"
    assert postgresql["spec"]["strategy"]["type"] == "Recreate"
    assert _pod_spec(odoo)["volumes"] == [
        {
            "name": "odoo-filestore",
            "persistentVolumeClaim": {"claimName": "odoo-filestore"},
        },
        {"emptyDir": {"sizeLimit": "64Mi"}, "name": "tmp"},
    ]
    assert _pod_spec(postgresql)["volumes"] == [
        {
            "name": "postgresql-data",
            "persistentVolumeClaim": {"claimName": "postgresql-data"},
        },
        {"emptyDir": {"sizeLimit": "8Mi"}, "name": "postgresql-run"},
        {"emptyDir": {"sizeLimit": "32Mi"}, "name": "tmp"},
    ]
    assert "--proxy-mode" in _containers(odoo)[0]["args"]

    bootstrap_spec = bootstrap["spec"]
    bootstrap_pod = _pod_spec(bootstrap)
    bootstrap_container = _containers(bootstrap)[0]
    assert bootstrap["metadata"]["annotations"] == {
        "argocd.argoproj.io/sync-options": "Force=true,Replace=true"
    }
    assert bootstrap_spec["activeDeadlineSeconds"] == 300
    assert bootstrap_spec["backoffLimit"] == 6
    assert bootstrap_spec["ttlSecondsAfterFinished"] == 86400
    assert bootstrap_pod["restartPolicy"] == "OnFailure"
    assert bootstrap_pod["volumes"] == [
        {"emptyDir": {"sizeLimit": "64Mi"}, "name": "tmp"}
    ]
    assert bootstrap_container["volumeMounts"] == [{"mountPath": "/tmp", "name": "tmp"}]
    assert bootstrap_container["command"] == ["bash", "-lc"]
    assert "/opt/stockai/bootstrap.py" in bootstrap_container["args"][0]
    env = {item["name"]: item for item in bootstrap_container["env"]}
    assert env["STOCKAI_ODOO_BOOTSTRAP_SINK"]["value"] == "secretsmanager"
    assert env["STOCKAI_ODOO_BOOTSTRAP_SECRET_ARN"]["valueFrom"] == {
        "configMapKeyRef": {
            "key": "STOCKAI_ODOO_BOOTSTRAP_SECRET_ARN",
            "name": "stockai-environment",
        }
    }

    seed = _named(resources, "Job", "stockai-odoo-seed")
    seed_spec = seed["spec"]
    seed_pod = _pod_spec(seed)
    seed_container = _containers(seed)[0]
    assert seed["metadata"]["annotations"] == {
        "argocd.argoproj.io/sync-options": "Force=true,Replace=true",
        "argocd.argoproj.io/sync-wave": "1",
    }
    assert seed_spec["activeDeadlineSeconds"] == 300
    assert seed_spec["backoffLimit"] == 6
    assert "ttlSecondsAfterFinished" not in seed_spec
    assert seed_pod["restartPolicy"] == "OnFailure"
    seed_command = seed_container["args"][0]
    assert "odoo server --stop-after-init" in seed_command
    assert "--update=stockai_procurement" in seed_command
    assert seed_command.index("--update=stockai_procurement") < seed_command.index(
        "/opt/stockai/seed.py"
    )
    assert "/opt/stockai/verify_seed.py" in seed_command
    seed_env = {item["name"]: item for item in seed_container["env"]}
    assert seed_env["STOCKAI_ODOO_SEED_ENVIRONMENT"]["valueFrom"] == {
        "configMapKeyRef": {
            "key": "STOCKAI_ODOO_SEED_ENVIRONMENT",
            "name": "stockai-environment",
        }
    }


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_daily_scan_is_private_bounded_and_non_overlapping(environment: str) -> None:
    resources = _render(environment)
    cron = _named(resources, "CronJob", "stockai-daily-scan")
    container = _containers(cron)[0]

    assert cron["spec"]["schedule"] == "0 5 * * *"
    assert cron["spec"]["timeZone"] == "UTC"
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"
    assert cron["spec"]["successfulJobsHistoryLimit"] == 1
    assert cron["spec"]["failedJobsHistoryLimit"] == 2
    assert cron["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 120
    assert _pod_spec(cron)["restartPolicy"] == "Never"
    assert container["env"] == [
        {
            "name": "PROCUREMENT_CRON_TOKEN",
            "valueFrom": {"secretKeyRef": {"key": "value", "name": "cron-token"}},
        }
    ]
    assert "http://api:8000/internal/v1/scans" in container["args"][0]
    assert "Authorization" in container["args"][0]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_workloads_have_health_resources_security_and_environment_placement(
    environment: str,
) -> None:
    resources = _render(environment)
    workloads = [
        *[
            deployment
            for deployment in _by_kind(resources, "Deployment")
            if deployment["metadata"]["name"] in APPLICATION_DEPLOYMENTS
        ],
        *_by_kind(resources, "Job"),
        *_by_kind(resources, "CronJob"),
    ]

    for workload in workloads:
        pod_spec = _pod_spec(workload)
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
        assert pod_spec["automountServiceAccountToken"] is False
        assert pod_spec["securityContext"]["seccompProfile"] == {
            "type": "RuntimeDefault"
        }
        for container in _containers(workload):
            assert set(container["resources"]) == {"limits", "requests"}
            assert set(container["resources"]["requests"]) == {"cpu", "memory"}
            assert set(container["resources"]["limits"]) == {"cpu", "memory"}
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["capabilities"] == {"drop": ["ALL"]}
            assert security["runAsNonRoot"] is True

    deployments = _by_kind(resources, "Deployment")
    for deployment in deployments:
        container = _containers(deployment)[0]
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["revisionHistoryLimit"] == 2
        assert (
            deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] > 0
        )
        assert "livenessProbe" in container
        assert "readinessProbe" in container
        assert "startupProbe" in container

    for name in STATELESS:
        deployment = _named(resources, "Deployment", name)
        assert deployment["spec"]["strategy"] == {
            "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
            "type": "RollingUpdate",
        }


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_stateless_hpas_and_one_worker_request_budget(environment: str) -> None:
    resources = _render(environment)
    hpas = _by_kind(resources, "HorizontalPodAutoscaler")

    assert {hpa["metadata"]["name"] for hpa in hpas} == {
        f"{name}-cpu" for name in STATELESS
    }
    for hpa in hpas:
        assert hpa["apiVersion"] == "autoscaling/v2"
        assert hpa["spec"]["minReplicas"] == 1
        assert hpa["spec"]["maxReplicas"] == 3
        assert hpa["spec"]["metrics"] == [
            {
                "resource": {
                    "name": "cpu",
                    "target": {
                        "averageUtilization": 50,
                        "type": "Utilization",
                    },
                },
                "type": "Resource",
            }
        ]
        assert hpa["spec"]["scaleTargetRef"]["name"] in STATELESS

    steady_containers = [
        container
        for deployment in _by_kind(resources, "Deployment")
        if deployment["metadata"]["name"] in APPLICATION_DEPLOYMENTS
        for container in _containers(deployment)
    ]
    total_cpu = sum(
        _cpu_millicores(container["resources"]["requests"]["cpu"])
        for container in steady_containers
    )
    total_memory = sum(
        _memory_mib(container["resources"]["requests"]["memory"])
        for container in steady_containers
    )
    assert total_cpu <= 800
    assert total_memory <= 1600
    assert not any(
        resource["kind"] in {"ClusterAutoscaler", "NodePool", "Provisioner"}
        for resource in resources
    )


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_services_ingress_and_network_policies_expose_only_approved_flows(
    environment: str,
) -> None:
    resources = _render(environment)
    services = {
        service["metadata"]["name"]: service
        for service in _by_kind(resources, "Service")
    }
    application_services = {
        "api",
        "frontend",
        "odoo",
        "postgresql",
        "procurement-mcp",
    }
    assert application_services <= set(services)
    assert all(
        service["spec"].get("type", "ClusterIP") == "ClusterIP"
        for service in services.values()
    )
    assert all(
        "nodePort" not in port
        for service in services.values()
        for port in service["spec"]["ports"]
    )
    assert services["procurement-mcp"]["spec"]["sessionAffinity"] == "ClientIP"

    ingress = _named(resources, "Ingress", "stockai-public")
    assert ingress["spec"]["ingressClassName"] == "nginx"
    assert [rule["host"] for rule in ingress["spec"]["rules"]] == [
        f"app.{environment}.stockai.fursa.click",
        f"odoo.{environment}.stockai.fursa.click",
        f"grafana.{environment}.stockai.fursa.click",
    ]
    ingress_services = {
        path["backend"]["service"]["name"]
        for rule in ingress["spec"]["rules"]
        for path in rule["http"]["paths"]
    }
    assert ingress_services == {"frontend", "odoo", "grafana"}
    assert not {"procurement-mcp", "postgresql"} & ingress_services

    policies = {
        policy["metadata"]["name"]: policy
        for policy in _by_kind(resources, "NetworkPolicy")
    }
    assert {
        "allow-api",
        "allow-cron-to-api",
        "allow-dns",
        "allow-frontend",
        "allow-mcp",
        "allow-odoo",
        "allow-postgresql",
        "default-deny-all",
    } <= set(policies)
    cron_policy = policies["allow-cron-to-api"]
    assert cron_policy["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "stockai-agent-api"
    }
    assert cron_policy["spec"]["ingress"] == [
        {
            "from": [
                {
                    "podSelector": {
                        "matchLabels": {"app.kubernetes.io/name": "stockai-daily-scan"}
                    }
                }
            ],
            "ports": [{"port": 8000, "protocol": "TCP"}],
        }
    ]

    for policy_name in ("allow-api", "allow-automation-egress"):
        assert {
            "ports": [{"port": 80, "protocol": "TCP"}],
            "to": [{"ipBlock": {"cidr": "169.254.169.254/32"}}],
        } in policies[policy_name]["spec"]["egress"]

    assert not _by_kind(resources, "Secret")
    rendered = yaml.safe_dump_all(resources)
    assert "password:" not in rendered.lower()
