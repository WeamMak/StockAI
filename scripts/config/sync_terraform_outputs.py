"""Sync reviewed non-secret EBS coordinates into the dev and prod overlays."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OVERLAYS_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "overlays"
ENVIRONMENTS = ("dev", "prod")
INPUT_KEYS = {"az", "odoo", "postgresql", "prometheus"}
COORDINATE_KEYS = {
    "availabilityZone",
    "environment",
    "odooPersistentVolume",
    "odooVolumeHandle",
    "postgresqlPersistentVolume",
    "postgresqlVolumeHandle",
    "prometheusPersistentVolume",
    "prometheusVolumeHandle",
}
HANDLE_KEYS = {
    "odoo": "odooVolumeHandle",
    "postgresql": "postgresqlVolumeHandle",
    "prometheus": "prometheusVolumeHandle",
}
MAX_INPUT_BYTES = 16 * 1024
AZ_PATTERN = re.compile(r"^us-east-1[a-f]$")
VOLUME_PATTERN = re.compile(r"^vol-[A-Za-z0-9-]{3,124}$")


class SyncError(ValueError):
    """A safe validation failure without input values."""


def _parse_input(raw_input: str) -> dict[str, dict[str, str]]:
    if len(raw_input.encode("utf-8")) > MAX_INPUT_BYTES:
        raise SyncError("input exceeds the 16 KiB limit")
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError as error:
        raise SyncError("input must be one valid JSON object") from error
    if not isinstance(payload, dict) or set(payload) != set(ENVIRONMENTS):
        raise SyncError("environments must be exactly dev and prod")

    validated: dict[str, dict[str, str]] = {}
    for environment in ENVIRONMENTS:
        values = payload[environment]
        if not isinstance(values, dict) or set(values) != INPUT_KEYS:
            raise SyncError(
                f"{environment} keys must be exactly az, odoo, postgresql, prometheus"
            )
        if not all(
            isinstance(value, str) and value.isascii() for value in values.values()
        ):
            raise SyncError(f"{environment} coordinates must be ASCII strings")
        az = values["az"]
        if AZ_PATTERN.fullmatch(az) is None:
            raise SyncError(f"{environment} az must be a us-east-1 Availability Zone")
        for workload in HANDLE_KEYS:
            if VOLUME_PATTERN.fullmatch(values[workload]) is None:
                raise SyncError(f"{environment} {workload} volume ID is invalid")
        validated[environment] = {
            key: values[key] for key in ("az", "odoo", "postgresql", "prometheus")
        }
    return validated


def _load_coordinate_document(path: Path, environment: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SyncError(f"cannot read the reviewed {environment} overlay") from error
    if not isinstance(document, dict):
        raise SyncError(f"the reviewed {environment} overlay is malformed")
    metadata = document.get("metadata")
    data = document.get("data")
    if (
        document.get("apiVersion") != "v1"
        or document.get("kind") != "ConfigMap"
        or not isinstance(metadata, Mapping)
        or metadata.get("name") != "stockai-storage-coordinates"
        or not isinstance(data, dict)
        or set(data) != COORDINATE_KEYS
        or data.get("environment") != environment
    ):
        raise SyncError(f"the reviewed {environment} overlay is malformed")
    expected_names = {
        "odooPersistentVolume": f"stockai-{environment}-odoo-filestore",
        "postgresqlPersistentVolume": f"stockai-{environment}-postgresql-data",
        "prometheusPersistentVolume": f"stockai-{environment}-prometheus-data",
    }
    if any(data.get(key) != value for key, value in expected_names.items()):
        raise SyncError(f"the reviewed {environment} overlay has unexpected PV names")
    return document


def sync_coordinates(
    coordinates: dict[str, dict[str, str]], *, overlays_root: Path
) -> None:
    """Validate both reviewed overlays, then update their non-secret coordinates."""

    documents: dict[Path, dict[str, Any]] = {}
    for environment in ENVIRONMENTS:
        path = overlays_root / environment / "storage-coordinates.yaml"
        document = _load_coordinate_document(path, environment)
        data = document["data"]
        data["availabilityZone"] = coordinates[environment]["az"]
        for workload, key in HANDLE_KEYS.items():
            data[key] = coordinates[environment][workload]
        documents[path] = document

    rendered_documents = {
        path: yaml.safe_dump(document, sort_keys=False)
        for path, document in documents.items()
    }
    for path, rendered_document in rendered_documents.items():
        path.write_text(rendered_document, encoding="utf-8")


def _terraform_root_outputs(
    deployment: Mapping[str, Any], root: str
) -> Mapping[str, Any]:
    outputs = deployment.get("outputs")
    value = outputs.get(root) if isinstance(outputs, Mapping) else None
    if not isinstance(value, Mapping):
        raise SyncError(f"reviewed {root} Terraform outputs are unavailable")
    return value


def _required_string(values: Mapping[str, Any], key: str, root: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise SyncError(f"reviewed {root} output {key} is invalid")
    return value


def sync_from_deployment(
    deployment: Mapping[str, Any], *, overlays_root: Path = DEFAULT_OVERLAYS_ROOT
) -> None:
    """Synchronize reviewed environment outputs into Git desired state."""

    inputs = deployment.get("inputs")
    generated = deployment.get("generated")
    if not isinstance(inputs, Mapping) or not isinstance(generated, Mapping):
        raise SyncError("deployment descriptor is malformed")
    domain = _required_string(inputs, "domain_name", "deployment")
    _required_string(generated, "cluster_name", "deployment")
    edge = _terraform_root_outputs(deployment, "edge")
    loki_arn = _required_string(edge, "loki_bucket_arn", "edge")
    if not loki_arn.startswith("arn:aws:s3:::"):
        raise SyncError("reviewed edge output loki_bucket_arn is invalid")
    loki_bucket = loki_arn.removeprefix("arn:aws:s3:::")

    coordinates: dict[str, dict[str, str]] = {}
    environment_outputs: dict[str, Mapping[str, Any]] = {}
    for environment in ENVIRONMENTS:
        values = _terraform_root_outputs(deployment, environment)
        environment_outputs[environment] = values
        data_volumes = values.get("data_volumes")
        scoped = (
            data_volumes.get(environment) if isinstance(data_volumes, Mapping) else None
        )
        if not isinstance(scoped, Mapping) or set(scoped) != set(HANDLE_KEYS):
            raise SyncError(f"reviewed {environment} data volumes are invalid")
        environment_coordinates: dict[str, str] = {}
        availability_zones: set[str] = set()
        for workload in HANDLE_KEYS:
            volume = scoped.get(workload)
            if not isinstance(volume, Mapping):
                raise SyncError(f"reviewed {environment} data volumes are invalid")
            volume_id = _required_string(volume, "volume_id", environment)
            availability_zone = _required_string(
                volume, "availability_zone", environment
            )
            environment_coordinates[workload] = volume_id
            availability_zones.add(availability_zone)
        if len(availability_zones) != 1:
            raise SyncError(f"reviewed {environment} volume zones do not match")
        environment_coordinates["az"] = availability_zones.pop()
        coordinates[environment] = environment_coordinates
    _parse_input(json.dumps(coordinates))

    documents: dict[Path, str] = {}
    for environment in ENVIRONMENTS:
        values = environment_outputs[environment]
        config_path = overlays_root / environment / "environment-config.yaml"
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise SyncError(
                f"cannot read the reviewed {environment} overlay"
            ) from error
        if not isinstance(config, dict) or not isinstance(config.get("data"), dict):
            raise SyncError(
                f"the reviewed {environment} environment config is malformed"
            )
        data = config["data"]
        data.update(
            {
                "PROCUREMENT_COGNITO_CLIENT_ID": _required_string(
                    values, "cognito_client_id", environment
                ),
                "PROCUREMENT_COGNITO_DOMAIN_URL": (
                    f"https://{_required_string(values, 'cognito_domain', environment)}"
                    ".auth.us-east-1.amazoncognito.com"
                ),
                "PROCUREMENT_COGNITO_REDIRECT_URI": (
                    f"https://app.{environment}.{domain}/auth/callback"
                ),
                "PROCUREMENT_COGNITO_USER_POOL_ID": _required_string(
                    values, "cognito_user_pool_id", environment
                ),
                "PROCUREMENT_DYNAMODB_APPLICATION_TABLE": _required_string(
                    values, "application_table_name", environment
                ),
                "PROCUREMENT_DYNAMODB_CHECKPOINT_TABLE": _required_string(
                    values, "checkpoint_table_name", environment
                ),
                "STOCKAI_APPLICATION_HOST": f"app.{environment}.{domain}",
                "STOCKAI_GRAFANA_HOST": f"grafana.{environment}.{domain}",
                "STOCKAI_ODOO_HOST": f"odoo.{environment}.{domain}",
            }
        )
        secret_arns = values.get("secret_arns")
        if not isinstance(secret_arns, Mapping):
            raise SyncError(f"reviewed {environment} secret ARNs are invalid")
        data["STOCKAI_ODOO_BOOTSTRAP_SECRET_ARN"] = _required_string(
            secret_arns, "odoo-api-key", environment
        )
        documents[config_path] = yaml.safe_dump(config, sort_keys=False)

        secrets_path = overlays_root / environment / "external-secrets.yaml"
        try:
            secrets = list(yaml.safe_load_all(secrets_path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError) as error:
            raise SyncError(
                f"cannot read the reviewed {environment} secrets"
            ) from error
        for document in secrets:
            if isinstance(document, dict) and document.get("kind") == "ExternalSecret":
                name = document.get("metadata", {}).get("name")
                if not isinstance(name, str):
                    raise SyncError(f"the reviewed {environment} secrets are malformed")
                document["spec"]["data"][0]["remoteRef"]["key"] = _required_string(
                    secret_arns, name, environment
                )
        documents[secrets_path] = yaml.safe_dump_all(secrets, sort_keys=False)

        observability_path = (
            overlays_root / environment / "observability" / "environment.yaml"
        )
        try:
            observability = list(
                yaml.safe_load_all(observability_path.read_text(encoding="utf-8"))
            )
        except (OSError, yaml.YAMLError) as error:
            raise SyncError(
                f"cannot read reviewed {environment} observability"
            ) from error
        for document in observability:
            if not isinstance(document, dict):
                continue
            if (
                document.get("kind") == "ConfigMap"
                and document.get("metadata", {}).get("name")
                == "stockai-observability-environment"
            ):
                document["data"]["LOKI_S3_BUCKET"] = loki_bucket
                document["data"]["LOKI_S3_PREFIX"] = _required_string(
                    values, "loki_prefix", environment
                )
                document["data"]["blackbox-targets.yaml"] = "\n".join(
                    [
                        "- targets:",
                        f"    - https://app.{environment}.{domain}",
                        f"    - https://odoo.{environment}.{domain}",
                        f"    - https://grafana.{environment}.{domain}",
                        "  labels:",
                        f"    environment: {environment}",
                        "",
                    ]
                )
            if (
                document.get("kind") == "Deployment"
                and document.get("metadata", {}).get("name") == "stockai-loki"
            ):
                for variable in document["spec"]["template"]["spec"]["containers"][0][
                    "env"
                ]:
                    if variable["name"] == "LOKI_S3_BUCKET":
                        variable["value"] = loki_bucket
                    elif variable["name"] == "LOKI_S3_PREFIX":
                        variable["value"] = _required_string(
                            values, "loki_prefix", environment
                        )
        documents[observability_path] = yaml.safe_dump_all(
            observability, sort_keys=False
        )

    storage_documents: dict[Path, dict[str, Any]] = {}
    for environment in ENVIRONMENTS:
        path = overlays_root / environment / "storage-coordinates.yaml"
        document = _load_coordinate_document(path, environment)
        data = document["data"]
        data["availabilityZone"] = coordinates[environment]["az"]
        for workload, key in HANDLE_KEYS.items():
            data[key] = coordinates[environment][workload]
        storage_documents[path] = document
    documents.update(
        {
            path: yaml.safe_dump(document, sort_keys=False)
            for path, document in storage_documents.items()
        }
    )
    for path, rendered in documents.items():
        if path.read_text(encoding="utf-8") != rendered:
            path.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overlays-root",
        type=Path,
        default=DEFAULT_OVERLAYS_ROOT,
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args()
    raw_input = sys.stdin.read(MAX_INPUT_BYTES + 1)
    try:
        coordinates = _parse_input(raw_input)
        sync_coordinates(coordinates, overlays_root=arguments.overlays_root)
    except (OSError, SyncError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print("updated storage coordinates for dev and prod")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
