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
