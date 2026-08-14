"""Assemble one dev release from changed builds and verified prior images."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from .create_manifest import create_manifest, write_manifest
from .verify_manifest import (
    DIGEST_PATTERN,
    IMAGE_NAMES,
    ManifestError,
    load_manifest,
    verify_manifest,
)


class AssemblyError(ValueError):
    """A safe dev-release assembly failure."""


def _result(path: Path, name: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AssemblyError(f"{name} image result is invalid") from error
    keys = {"name", "digest", "provenance", "scoutStatus", "scoutReportDigest"}
    if (
        not isinstance(document, dict)
        or set(document) != keys
        or document.get("name") != name
        or not all(isinstance(value, str) for value in document.values())
        or document.get("scoutStatus") not in {"passed", "findings", "error"}
    ):
        raise AssemblyError(f"{name} image result is invalid")
    return document


def assemble_release(
    *,
    source_commit: str,
    source_tree: str,
    application_identity: str,
    build_inputs: Mapping[str, str],
    prior: Mapping[str, object] | None,
    results_directory: Path,
    created_at: str,
) -> dict[str, object]:
    """Use new results only for changed inputs; otherwise carry verified prior data."""

    if set(build_inputs) != set(IMAGE_NAMES) or not all(
        isinstance(value, str) and DIGEST_PATTERN.fullmatch(value)
        for value in build_inputs.values()
    ):
        raise AssemblyError("build identities are invalid")
    if DIGEST_PATTERN.fullmatch(application_identity) is None:
        raise AssemblyError("application identity is invalid")
    verified_prior = verify_manifest(prior) if prior is not None else None
    images: dict[str, str] = {}
    provenance: dict[str, str] = {}
    scout_inputs: dict[str, dict[str, str]] = {}
    statuses: list[str] = []
    for name in IMAGE_NAMES:
        result = _result(results_directory / f"{name}.json", name)
        if result is not None:
            images[name] = result["digest"]
            provenance[name] = result["provenance"]
            statuses.append(result["scoutStatus"])
            scout_inputs[name] = {
                "status": result["scoutStatus"],
                "reportDigest": result["scoutReportDigest"],
            }
            continue
        if verified_prior is None:
            raise AssemblyError(f"{name} image result is missing")
        prior_inputs = verified_prior["buildInputs"]
        prior_images = verified_prior["images"]
        prior_provenance = verified_prior["provenance"]
        prior_scout = verified_prior["scout"]
        if not isinstance(prior_inputs, Mapping):
            raise AssemblyError("prior release is invalid")
        if not isinstance(prior_images, Mapping):
            raise AssemblyError("prior release is invalid")
        if not isinstance(prior_provenance, Mapping):
            raise AssemblyError("prior release is invalid")
        if not isinstance(prior_scout, Mapping):
            raise AssemblyError("prior release is invalid")
        if prior_inputs[name] != build_inputs[name]:
            raise AssemblyError(f"{name} image result is missing for changed inputs")
        images[name] = str(prior_images[name])
        provenance[name] = str(prior_provenance[name])
        statuses.append(str(prior_scout["status"]))
        scout_inputs[name] = {
            "status": str(prior_scout["status"]),
            "reportDigest": str(prior_scout["reportDigest"]),
        }

    if "error" in statuses:
        scout_status = "error"
    elif "findings" in statuses:
        scout_status = "findings"
    else:
        scout_status = "passed"
    canonical_scout = json.dumps(
        scout_inputs, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    scout_digest = f"sha256:{hashlib.sha256(canonical_scout).hexdigest()}"
    try:
        return create_manifest(
            source_commit=source_commit,
            source_tree=source_tree,
            images=images,
            provenance=provenance,
            application_identity=application_identity,
            build_inputs=build_inputs,
            scout_status=scout_status,
            scout_report_digest=scout_digest,
            dev_status="pending",
            dev_evidence_digest=None,
            created_at=created_at,
        )
    except ManifestError as error:
        raise AssemblyError(str(error)) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--created-at", required=True)
    arguments = parser.parse_args()
    try:
        identities = json.loads(arguments.identities.read_text(encoding="utf-8"))
        if not isinstance(identities, dict):
            raise AssemblyError("build identities are invalid")
        build_inputs = identities.get("buildInputs")
        application_identity = identities.get("applicationIdentity")
        if not isinstance(build_inputs, dict) or not isinstance(
            application_identity, str
        ):
            raise AssemblyError("build identities are invalid")
        prior = load_manifest(arguments.prior) if arguments.prior else None
        manifest = assemble_release(
            source_commit=arguments.source_commit,
            source_tree=arguments.source_tree,
            application_identity=application_identity,
            build_inputs=build_inputs,
            prior=prior,
            results_directory=arguments.results,
            created_at=arguments.created_at,
        )
        write_manifest(manifest, arguments.output)
    except (AssemblyError, ManifestError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
