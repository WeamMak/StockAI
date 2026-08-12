"""Validate StockAI release metadata and its canonical integrity digest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

IMAGE_NAMES = ("api", "frontend", "mcp", "odoo")
MANIFEST_KEYS = {
    "schemaVersion",
    "source",
    "images",
    "provenance",
    "scout",
    "devValidation",
    "createdAt",
    "integrity",
}
MAX_MANIFEST_BYTES = 64 * 1024
GIT_OBJECT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ManifestError(ValueError):
    """A safe release-manifest validation failure."""


def _canonical_payload(manifest: Mapping[str, object]) -> bytes:
    payload = {key: value for key, value in manifest.items() if key != "integrity"}
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def calculate_integrity(manifest: Mapping[str, object]) -> str:
    """Return the SHA-256 identity of the canonical manifest payload."""

    return f"sha256:{hashlib.sha256(_canonical_payload(manifest)).hexdigest()}"


def _exact_object(value: object, *, name: str, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        expected = ", ".join(sorted(keys))
        raise ManifestError(f"{name} must contain exactly: {expected}")
    if not all(isinstance(key, str) for key in value):
        raise ManifestError(f"{name} keys must be strings")
    return value


def _digest(value: object, *, name: str) -> str:
    if not isinstance(value, str) or DIGEST_PATTERN.fullmatch(value) is None:
        raise ManifestError(f"{name} must be a lowercase sha256 digest")
    return value


def _timestamp(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ManifestError(f"{name} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ManifestError(f"{name} must be an RFC 3339 UTC timestamp") from error
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ManifestError(f"{name} must be an RFC 3339 UTC timestamp")
    return value


def verify_manifest(manifest: object) -> dict[str, object]:
    """Validate a manifest and return it with a precise dictionary type."""

    document = _exact_object(manifest, name="manifest", keys=MANIFEST_KEYS)
    if document["schemaVersion"] != 1:
        raise ManifestError("schemaVersion must be 1")

    source = _exact_object(document["source"], name="source", keys={"commit", "tree"})
    for name in ("commit", "tree"):
        value = source[name]
        if not isinstance(value, str) or GIT_OBJECT_PATTERN.fullmatch(value) is None:
            raise ManifestError(f"source {name} must be a lowercase Git object ID")

    required_images = set(IMAGE_NAMES)
    images = _exact_object(document["images"], name="images", keys=required_images)
    provenance = _exact_object(
        document["provenance"], name="provenance", keys=required_images
    )
    for name in IMAGE_NAMES:
        _digest(images[name], name=f"images.{name}")
        _digest(provenance[name], name=f"provenance.{name}")

    scout = _exact_object(
        document["scout"], name="scout", keys={"status", "reportDigest"}
    )
    if scout["status"] not in {"passed", "failed"}:
        raise ManifestError("scout status must be passed or failed")
    _digest(scout["reportDigest"], name="scout.reportDigest")

    dev_validation = _exact_object(
        document["devValidation"],
        name="devValidation",
        keys={"status", "evidenceDigest"},
    )
    status = dev_validation["status"]
    if status not in {"pending", "passed", "failed"}:
        raise ManifestError("dev validation status must be pending, passed, or failed")
    evidence = dev_validation["evidenceDigest"]
    if status == "pending":
        if evidence is not None:
            raise ManifestError("pending dev validation must not have evidence")
    else:
        _digest(evidence, name="devValidation.evidenceDigest")

    _timestamp(document["createdAt"], name="createdAt")
    integrity = _exact_object(
        document["integrity"],
        name="integrity",
        keys={"algorithm", "digest"},
    )
    if integrity["algorithm"] != "sha256":
        raise ManifestError("integrity algorithm must be sha256")
    digest = _digest(integrity["digest"], name="integrity.digest")
    if digest != calculate_integrity(document):
        raise ManifestError("integrity digest does not match the manifest payload")

    return dict(document)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ManifestError("manifest contains duplicate object keys")
        document[key] = value
    return document


def load_manifest(path: Path) -> dict[str, object]:
    """Read and validate one bounded JSON release manifest."""

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ManifestError("cannot read release manifest") from error
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("release manifest exceeds the 64 KiB limit")
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError("release manifest must be valid UTF-8 JSON") from error
    return verify_manifest(document)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    try:
        load_manifest(arguments.manifest)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print("release manifest verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
