"""Contracts for synchronizing reviewed Terraform outputs into Git state."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from scripts.config.sync_terraform_outputs import sync_from_deployment
from scripts.infra.provision import DEFAULT_DESCRIPTOR, load_descriptor


def _environment_outputs(environment: str) -> dict[str, object]:
    prefix = (
        "arn:aws:secretsmanager:us-east-1:228281126655:secret:"
        f"weam-stockai/{environment}"
    )
    return {
        "application_table_name": f"weam-stockai-{environment}-application",
        "checkpoint_table_name": f"weam-stockai-{environment}-checkpoints",
        "cognito_client_id": f"client-{environment}",
        "cognito_domain": f"weam-stockai-{environment}",
        "cognito_user_pool_id": f"us-east-1_{environment}",
        "data_volumes": {
            environment: {
                workload: {
                    "availability_zone": (
                        "us-east-1a" if environment == "dev" else "us-east-1b"
                    ),
                    "volume_id": f"vol-{environment}-{workload}",
                }
                for workload in ("odoo", "postgresql", "prometheus")
            }
        },
        "loki_prefix": f"{environment}/",
        "secret_arns": {
            name: f"{prefix}/{name}-AbCdEf"
            for name in (
                "cron-token",
                "grafana-admin-password",
                "mcp-token",
                "odoo-api-key",
                "odoo-database-password",
                "session-secret",
            )
        },
    }


def test_syncs_all_reviewed_non_secret_coordinates_and_is_idempotent(
    tmp_path: Path,
) -> None:
    overlays = tmp_path / "overlays"
    shutil.copytree(DEFAULT_DESCRIPTOR.parents[1] / "kubernetes" / "overlays", overlays)
    descriptor = copy.deepcopy(load_descriptor(DEFAULT_DESCRIPTOR))
    descriptor["outputs"]["dev"] = _environment_outputs("dev")
    descriptor["outputs"]["prod"] = _environment_outputs("prod")

    sync_from_deployment(descriptor, overlays_root=overlays)
    first = {
        path.relative_to(overlays): path.read_text(encoding="utf-8")
        for path in overlays.rglob("*.yaml")
    }
    sync_from_deployment(descriptor, overlays_root=overlays)
    second = {
        path.relative_to(overlays): path.read_text(encoding="utf-8")
        for path in overlays.rglob("*.yaml")
    }

    assert first == second
    dev_config = yaml.safe_load(
        (overlays / "dev" / "environment-config.yaml").read_text(encoding="utf-8")
    )["data"]
    assert dev_config["PROCUREMENT_COGNITO_CLIENT_ID"] == "client-dev"
    assert dev_config["STOCKAI_APPLICATION_HOST"] == ("app.dev.stockai.fursa.click")
    assert dev_config["STOCKAI_ODOO_BOOTSTRAP_SECRET_ARN"].endswith(
        "/odoo-api-key-AbCdEf"
    )
    dev_storage = yaml.safe_load(
        (overlays / "dev" / "storage-coordinates.yaml").read_text(encoding="utf-8")
    )["data"]
    assert dev_storage["odooVolumeHandle"] == "vol-dev-odoo"
    observability = list(
        yaml.safe_load_all(
            (overlays / "dev" / "observability" / "environment.yaml").read_text(
                encoding="utf-8"
            )
        )
    )
    assert observability[0]["data"]["LOKI_S3_BUCKET"] == (
        "weam-stockai-loki-228281126655-us-east-1"
    )
