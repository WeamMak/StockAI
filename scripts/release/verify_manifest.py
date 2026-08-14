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
    "releaseId",
    "source",
    "images",
    "provenance",
    "applicationIdentity",
    "buildInputs",
    "scout",
    "devValidation",
    "createdAt",
    "integrity",
}
RELEASE_CORE_KEYS = (
    "schemaVersion",
    "source",
    "images",
    "provenance",
    "applicationIdentity",
    "buildInputs",
    "scout",
    "createdAt",
)
MAX_MANIFEST_BYTES = 64 * 1024
MAX_VALIDATION_ATTEMPTS = 20
GIT_OBJECT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SMOKE_RUN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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


def calculate_release_id(manifest: Mapping[str, object]) -> str:
    """Bind only the immutable T22 release core to one stable identifier."""

    core = {key: manifest[key] for key in RELEASE_CORE_KEYS if key in manifest}
    payload = json.dumps(
        core,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


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
    build_inputs = _exact_object(
        document["buildInputs"], name="buildInputs", keys=required_images
    )
    for name in IMAGE_NAMES:
        _digest(images[name], name=f"images.{name}")
        _digest(provenance[name], name=f"provenance.{name}")
        _digest(build_inputs[name], name=f"buildInputs.{name}")
    _digest(document["applicationIdentity"], name="applicationIdentity")

    scout = _exact_object(
        document["scout"], name="scout", keys={"status", "reportDigest"}
    )
    if scout["status"] not in {"passed", "findings", "error"}:
        raise ManifestError("scout status must be passed, findings, or error")
    _digest(scout["reportDigest"], name="scout.reportDigest")
    _timestamp(document["createdAt"], name="createdAt")

    release_id = _digest(document["releaseId"], name="releaseId")
    if release_id != calculate_release_id(document):
        raise ManifestError("releaseId does not match the immutable release core")

    dev_validation = _exact_object(
        document["devValidation"],
        name="devValidation",
        keys={"status", "attempts"},
    )
    status = dev_validation["status"]
    if status not in {"pending", "passed", "failed"}:
        raise ManifestError("dev validation status must be pending, passed, or failed")
    attempts = dev_validation["attempts"]
    if not isinstance(attempts, list) or len(attempts) > MAX_VALIDATION_ATTEMPTS:
        raise ManifestError("dev validation attempts must be a bounded array")
    seen_runs: set[str] = set()
    argo_revision: str | None = None
    results: list[object] = []
    for index, raw_attempt in enumerate(attempts):
        attempt = _exact_object(
            raw_attempt,
            name=f"devValidation.attempts[{index}]",
            keys={
                "releaseId",
                "images",
                "argoRevision",
                "smokeRunId",
                "timestamp",
                "result",
                "evidenceDigest",
            },
        )
        if attempt["releaseId"] != release_id:
            raise ManifestError("validation attempt release ID does not match")
        attempt_images = _exact_object(
            attempt["images"],
            name=f"devValidation.attempts[{index}].images",
            keys=set(IMAGE_NAMES),
        )
        if attempt_images != images:
            raise ManifestError("validation attempt image map does not match")
        revision = attempt["argoRevision"]
        if (
            not isinstance(revision, str)
            or GIT_OBJECT_PATTERN.fullmatch(revision) is None
        ):
            raise ManifestError("validation attempt Argo revision is invalid")
        if argo_revision is not None and revision != argo_revision:
            raise ManifestError("validation attempt Argo revision changed")
        argo_revision = revision
        smoke_run_id = attempt["smokeRunId"]
        if (
            not isinstance(smoke_run_id, str)
            or SMOKE_RUN_PATTERN.fullmatch(smoke_run_id) is None
            or smoke_run_id in seen_runs
        ):
            raise ManifestError("validation attempt smoke-run identity is invalid")
        seen_runs.add(smoke_run_id)
        _timestamp(
            attempt["timestamp"], name=f"devValidation.attempts[{index}].timestamp"
        )
        if attempt["result"] not in {"passed", "failed"}:
            raise ManifestError("validation attempt result must be passed or failed")
        results.append(attempt["result"])
        _digest(
            attempt["evidenceDigest"],
            name=f"devValidation.attempts[{index}].evidenceDigest",
        )
    if status == "pending" and attempts:
        raise ManifestError("pending dev validation must not have attempts")
    if status == "failed" and (not results or results[-1] != "failed"):
        raise ManifestError("failed dev validation must end with failed evidence")
    if status == "passed" and (not results or results[-1] != "passed"):
        raise ManifestError("passed dev validation must end with passed evidence")
    if "passed" in results[:-1]:
        raise ManifestError("passed validation evidence cannot be replaced")

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
