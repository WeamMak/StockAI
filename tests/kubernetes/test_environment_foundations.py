"""Render-level contracts for the isolated dev and prod foundations."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OVERLAYS_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "overlays"
SYNC_SCRIPT = PROJECT_ROOT / "scripts" / "config" / "sync_terraform_outputs.py"
CLAIMS = ("odoo-filestore", "postgresql-data", "prometheus-data")
RUNTIME_SECRET_NAMES = {
    "cron-token",
    "grafana-admin-password",
    "mcp-token",
    "odoo-api-key",
    "odoo-database-password",
    "session-secret",
}
SERVICE_ACCOUNTS = {
    "default",
    "stockai-agent-api",
    "stockai-automation",
    "stockai-frontend",
    "stockai-odoo",
    "stockai-postgresql",
    "stockai-procurement-mcp",
}


def _render(
    environment: str, overlays_root: Path = OVERLAYS_ROOT
) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(overlays_root / environment)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"{environment} foundation did not render:\n{result.stderr.strip()}"
        )
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def _load_yaml(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _copy_kubernetes_tree(tmp_path: Path) -> Path:
    destination = tmp_path / "kubernetes"
    shutil.copytree(PROJECT_ROOT / "deploy" / "kubernetes", destination)
    return destination / "overlays"


def _run_sync(overlays_root: Path, payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SYNC_SCRIPT),
            "--overlays-root",
            str(overlays_root),
        ],
        check=False,
        capture_output=True,
        input=json.dumps(payload),
        text=True,
    )


def _resources_of_kind(
    resources: list[dict[str, Any]], kind: str
) -> list[dict[str, Any]]:
    return [resource for resource in resources if resource["kind"] == kind]


@pytest.mark.parametrize(
    ("environment", "expected_zone"),
    (("dev", "us-east-1a"), ("prod", "us-east-1b")),
)
def test_static_retained_volumes_bind_exact_environment_claims(
    environment: str, expected_zone: str
) -> None:
    resources = _render(environment)
    coordinate_data = _load_yaml(
        OVERLAYS_ROOT / environment / "storage-coordinates.yaml"
    )["data"]
    persistent_volumes = _resources_of_kind(resources, "PersistentVolume")
    claims = _resources_of_kind(resources, "PersistentVolumeClaim")

    assert len(persistent_volumes) == 3
    assert {claim["metadata"]["name"] for claim in claims} == set(CLAIMS)

    volume_key_by_claim = {
        "odoo-filestore": "odooVolumeHandle",
        "postgresql-data": "postgresqlVolumeHandle",
        "prometheus-data": "prometheusVolumeHandle",
    }
    for claim_name in CLAIMS:
        expected_pv_name = f"stockai-{environment}-{claim_name}"
        persistent_volume = next(
            item
            for item in persistent_volumes
            if item["metadata"]["name"] == expected_pv_name
        )
        claim = next(item for item in claims if item["metadata"]["name"] == claim_name)

        assert persistent_volume["spec"]["capacity"] == {"storage": "5Gi"}
        assert persistent_volume["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert persistent_volume["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
        assert persistent_volume["spec"]["storageClassName"] == ""
        assert persistent_volume["spec"]["csi"]["driver"] == "ebs.csi.aws.com"
        assert (
            persistent_volume["spec"]["csi"]["volumeHandle"]
            == coordinate_data[volume_key_by_claim[claim_name]]
        )
        assert persistent_volume["spec"]["claimRef"] == {
            "name": claim_name,
            "namespace": environment,
        }
        assert persistent_volume["spec"]["nodeAffinity"] == {
            "required": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "topology.kubernetes.io/zone",
                                "operator": "In",
                                "values": [expected_zone],
                            }
                        ]
                    }
                ]
            }
        }
        assert "kubernetes.io/hostname" not in str(
            persistent_volume["spec"]["nodeAffinity"]
        )

        assert claim["metadata"]["namespace"] == environment
        assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert claim["spec"]["storageClassName"] == ""
        assert claim["spec"]["volumeName"] == expected_pv_name
        assert claim["spec"]["resources"]["requests"] == {"storage": "5Gi"}

    assert not _resources_of_kind(resources, "StorageClass")
    assert not any("grafana" in claim["metadata"]["name"] for claim in claims)


@pytest.mark.parametrize("environment", ("dev", "prod"))
def test_namespace_configuration_and_secret_contracts_are_environment_scoped(
    environment: str,
) -> None:
    resources = _render(environment)
    other_environment = "prod" if environment == "dev" else "dev"

    namespace = _resources_of_kind(resources, "Namespace")
    assert len(namespace) == 1
    assert namespace[0]["metadata"]["name"] == environment
    assert {
        "stockai.io/environment": environment,
        "pod-security.kubernetes.io/enforce": "restricted",
        "pod-security.kubernetes.io/enforce-version": "v1.35",
    }.items() <= namespace[0]["metadata"]["labels"].items()

    config_maps = {
        item["metadata"]["name"]: item
        for item in _resources_of_kind(resources, "ConfigMap")
    }
    environment_config = config_maps["stockai-environment"]["data"]
    assert {
        "PROCUREMENT_ENVIRONMENT": environment,
        "PROCUREMENT_AWS_REGION": "us-east-1",
        "PROCUREMENT_LLM_MODE": "bedrock",
        "PROCUREMENT_PERSISTENCE_MODE": "dynamodb",
        "PROCUREMENT_AUTHENTICATION_MODE": "cognito",
        "PROCUREMENT_DYNAMODB_APPLICATION_TABLE": (
            f"weam-stockai-{environment}-application"
        ),
        "PROCUREMENT_DYNAMODB_CHECKPOINT_TABLE": (
            f"weam-stockai-{environment}-checkpoints"
        ),
        "PROCUREMENT_ODOO_DATABASE": f"stockai_{environment}",
        "STOCKAI_ODOO_SEED_ENVIRONMENT": environment,
        "STOCKAI_APPLICATION_HOST": f"app.{environment}.stockai.fursa.click",
        "STOCKAI_ODOO_HOST": f"odoo.{environment}.stockai.fursa.click",
        "STOCKAI_GRAFANA_HOST": f"grafana.{environment}.stockai.fursa.click",
    }.items() <= environment_config.items()
    assert other_environment not in "\n".join(environment_config.values())

    service_accounts = _resources_of_kind(resources, "ServiceAccount")
    assert {item["metadata"]["name"] for item in service_accounts} == SERVICE_ACCOUNTS
    assert all(
        item["metadata"]["namespace"] == environment
        and item["automountServiceAccountToken"] is False
        for item in service_accounts
    )

    secret_stores = _resources_of_kind(resources, "SecretStore")
    assert len(secret_stores) == 1
    assert secret_stores[0]["metadata"]["namespace"] == environment
    assert secret_stores[0]["spec"]["provider"]["aws"] == {
        "region": "us-east-1",
        "service": "SecretsManager",
    }

    external_secrets = _resources_of_kind(resources, "ExternalSecret")
    assert {item["metadata"]["name"] for item in external_secrets} == (
        RUNTIME_SECRET_NAMES
    )
    for external_secret in external_secrets:
        secret_name = external_secret["metadata"]["name"]
        assert external_secret["metadata"]["namespace"] == environment
        assert external_secret["spec"]["secretStoreRef"] == {
            "kind": "SecretStore",
            "name": "aws-secrets-manager",
        }
        assert external_secret["spec"]["target"] == {
            "creationPolicy": "Owner",
            "deletionPolicy": "Retain",
            "name": secret_name,
        }
        assert external_secret["spec"]["data"] == [
            {
                "remoteRef": {
                    "key": f"weam-stockai/{environment}/{secret_name}",
                },
                "secretKey": "value",
            }
        ]

    assert not _resources_of_kind(resources, "Secret")
    rendered_text = yaml.safe_dump_all(resources)
    assert f"weam-stockai/{other_environment}/" not in rendered_text
    assert f".{other_environment}.stockai.fursa.click" not in rendered_text


@pytest.mark.parametrize("environment", ("dev", "prod"))
def test_environment_keeps_default_deny_with_a_fixed_storage_budget(
    environment: str,
) -> None:
    resources = _render(environment)
    policies = {
        item["metadata"]["name"]: item
        for item in _resources_of_kind(resources, "NetworkPolicy")
    }
    assert policies["default-deny-all"] == {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "labels": {"app.kubernetes.io/part-of": "stockai"},
            "name": "default-deny-all",
            "namespace": environment,
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
        },
    }

    quotas = _resources_of_kind(resources, "ResourceQuota")
    assert len(quotas) == 1
    assert quotas[0]["spec"]["hard"] == {
        "persistentvolumeclaims": "3",
        "requests.storage": "15Gi",
    }


def test_sync_updates_only_six_handles_and_two_zone_values(tmp_path: Path) -> None:
    overlays_root = _copy_kubernetes_tree(tmp_path)
    payload = {
        "dev": {
            "az": "us-east-1c",
            "odoo": "vol-dev-odoo-reviewed",
            "postgresql": "vol-dev-postgresql-reviewed",
            "prometheus": "vol-dev-prometheus-reviewed",
        },
        "prod": {
            "az": "us-east-1d",
            "odoo": "vol-prod-odoo-reviewed",
            "postgresql": "vol-prod-postgresql-reviewed",
            "prometheus": "vol-prod-prometheus-reviewed",
        },
    }
    before = {
        environment: _load_yaml(
            overlays_root / environment / "storage-coordinates.yaml"
        )
        for environment in ("dev", "prod")
    }

    result = _run_sync(overlays_root, payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "updated storage coordinates for dev and prod\n"
    key_by_workload = {
        "odoo": "odooVolumeHandle",
        "postgresql": "postgresqlVolumeHandle",
        "prometheus": "prometheusVolumeHandle",
    }
    for environment in ("dev", "prod"):
        expected = copy.deepcopy(before[environment])
        expected["data"]["availabilityZone"] = payload[environment]["az"]
        for workload, key in key_by_workload.items():
            expected["data"][key] = payload[environment][workload]
        assert (
            _load_yaml(overlays_root / environment / "storage-coordinates.yaml")
            == expected
        )

        rendered = _render(environment, overlays_root)
        persistent_volumes = _resources_of_kind(rendered, "PersistentVolume")
        for workload, claim_name in {
            "odoo": "odoo-filestore",
            "postgresql": "postgresql-data",
            "prometheus": "prometheus-data",
        }.items():
            persistent_volume = next(
                item
                for item in persistent_volumes
                if item["metadata"]["name"] == f"stockai-{environment}-{claim_name}"
            )
            assert (
                persistent_volume["spec"]["csi"]["volumeHandle"]
                == (payload[environment][workload])
            )
            zone_values = persistent_volume["spec"]["nodeAffinity"]["required"][
                "nodeSelectorTerms"
            ][0]["matchExpressions"][0]["values"]
            assert zone_values == [payload[environment]["az"]]


@pytest.mark.parametrize(
    "payload",
    (
        {
            "dev": {
                "az": "us-east-1a",
                "odoo": "vol-dev-odoo",
                "postgresql": "vol-dev-postgresql",
                "prometheus": "vol-dev-prometheus",
                "secret_arns": "must-not-appear-in-output",
            },
            "prod": {
                "az": "us-east-1b",
                "odoo": "vol-prod-odoo",
                "postgresql": "vol-prod-postgresql",
                "prometheus": "vol-prod-prometheus",
            },
        },
        {
            "dev": {
                "az": "us-east-1a",
                "odoo": "vol-dev-odoo",
                "postgresql": "vol-dev-postgresql",
                "prometheus": "vol-dev-prometheus",
            }
        },
        {
            "dev": {
                "az": "us-east-1a",
                "odoo": "vol-dev-odoo",
                "postgresql": "vol-dev-postgresql",
                "prometheus": "vol-dev-prometheus",
            },
            "prod": {
                "az": "us-east-1b",
                "odoo": "vol-prod-odoo",
                "postgresql": "vol-prod-postgresql",
                "prometheus": "vol-prod-prometheus",
            },
            "staging": {},
        },
        {
            "dev": {
                "az": "us-east-1a",
                "odoo": "vol-dev-odoo",
                "postgresql": "vol-dev-postgresql",
            },
            "prod": {
                "az": "us-east-1b",
                "odoo": "vol-prod-odoo",
                "postgresql": "vol-prod-postgresql",
                "prometheus": "vol-prod-prometheus",
            },
        },
    ),
)
def test_sync_rejects_missing_or_extra_coordinates_without_mutating_files(
    tmp_path: Path, payload: object
) -> None:
    overlays_root = _copy_kubernetes_tree(tmp_path)
    coordinate_paths = [
        overlays_root / environment / "storage-coordinates.yaml"
        for environment in ("dev", "prod")
    ]
    before = {path: path.read_bytes() for path in coordinate_paths}
    unsafe_value = "must-not-appear-in-output"

    result = _run_sync(overlays_root, payload)

    assert result.returncode == 2
    assert result.stderr.startswith("error: ")
    assert unsafe_value not in result.stdout
    assert unsafe_value not in result.stderr
    assert all(path.read_bytes() == before[path] for path in coordinate_paths)
