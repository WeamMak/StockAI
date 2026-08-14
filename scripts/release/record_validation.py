"""Atomically append exact-release dev validation evidence."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from .create_manifest import write_manifest
from .verify_manifest import (
    DIGEST_PATTERN,
    GIT_OBJECT_PATTERN,
    IMAGE_NAMES,
    MAX_VALIDATION_ATTEMPTS,
    SMOKE_RUN_PATTERN,
    ManifestError,
    calculate_integrity,
    load_manifest,
    verify_manifest,
)


class ValidationError(ValueError):
    """A safe exact-release validation-recording failure."""


def record_validation(
    manifest: Mapping[str, object],
    *,
    release_id: str,
    images: Mapping[str, str],
    argo_revision: str,
    smoke_run_id: str,
    timestamp: str,
    result: str,
    evidence_digest: str,
) -> dict[str, object]:
    """Return a verified copy with one attempt appended and no core changes."""

    try:
        verified = deepcopy(verify_manifest(manifest))
    except ManifestError as error:
        raise ValidationError(str(error)) from error
    validation = verified["devValidation"]
    if not isinstance(validation, dict):  # pragma: no cover - verifier invariant
        raise ValidationError("dev validation is invalid")
    if validation["status"] == "passed":
        raise ValidationError("dev validation has already passed")
    if release_id != verified["releaseId"]:
        raise ValidationError("release ID does not match the manifest")
    if dict(images) != verified["images"]:
        raise ValidationError("image map does not match the manifest")
    if GIT_OBJECT_PATTERN.fullmatch(argo_revision) is None:
        raise ValidationError("Argo revision is invalid")
    if SMOKE_RUN_PATTERN.fullmatch(smoke_run_id) is None:
        raise ValidationError("smoke-run identity is invalid")
    if result not in {"passed", "failed"}:
        raise ValidationError("result must be passed or failed")
    if DIGEST_PATTERN.fullmatch(evidence_digest) is None:
        raise ValidationError("evidence digest is invalid or missing")

    attempts = validation["attempts"]
    if not isinstance(attempts, list):  # pragma: no cover - verifier invariant
        raise ValidationError("dev validation attempts are invalid")
    if len(attempts) >= MAX_VALIDATION_ATTEMPTS:
        raise ValidationError("dev validation attempt limit reached")
    if attempts:
        prior_revision = attempts[0]
        if not isinstance(prior_revision, Mapping):  # pragma: no cover
            raise ValidationError("dev validation attempts are invalid")
        if prior_revision["argoRevision"] != argo_revision:
            raise ValidationError("Argo revision does not match prior attempts")
        if any(
            isinstance(attempt, Mapping) and attempt.get("smokeRunId") == smoke_run_id
            for attempt in attempts
        ):
            raise ValidationError("smoke-run identity was already recorded")

    appended = {
        "releaseId": release_id,
        "images": dict(images),
        "argoRevision": argo_revision,
        "smokeRunId": smoke_run_id,
        "timestamp": timestamp,
        "result": result,
        "evidenceDigest": evidence_digest,
    }
    validation["attempts"] = [*attempts, appended]
    validation["status"] = result
    integrity = verified["integrity"]
    if not isinstance(integrity, dict):  # pragma: no cover - verifier invariant
        raise ValidationError("manifest integrity is invalid")
    integrity["digest"] = calculate_integrity(verified)
    try:
        return verify_manifest(verified)
    except ManifestError as error:
        raise ValidationError(str(error)) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--argo-revision", required=True)
    parser.add_argument("--smoke-run-id", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--result", choices=("passed", "failed"), required=True)
    parser.add_argument("--evidence-digest", required=True)
    arguments = parser.parse_args()
    try:
        manifest = load_manifest(arguments.manifest)
        images = manifest["images"]
        if not isinstance(images, Mapping):  # pragma: no cover - verifier invariant
            raise ValidationError("manifest image map is invalid")
        recorded = record_validation(
            manifest,
            release_id=arguments.release_id,
            images={name: str(images[name]) for name in IMAGE_NAMES},
            argo_revision=arguments.argo_revision,
            smoke_run_id=arguments.smoke_run_id,
            timestamp=arguments.timestamp,
            result=arguments.result,
            evidence_digest=arguments.evidence_digest,
        )
        write_manifest(recorded, arguments.manifest)
    except (ManifestError, ValidationError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print("dev validation evidence appended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
