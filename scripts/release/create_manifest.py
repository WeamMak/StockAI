"""Create deterministic StockAI release metadata for four project images."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from .verify_manifest import (
        IMAGE_NAMES,
        ManifestError,
        calculate_integrity,
        verify_manifest,
    )
else:
    from verify_manifest import (  # type: ignore[import-not-found, no-redef]
        IMAGE_NAMES,
        ManifestError,
        calculate_integrity,
        verify_manifest,
    )


def create_manifest(
    *,
    source_commit: str,
    source_tree: str,
    images: Mapping[str, str],
    provenance: Mapping[str, str],
    scout_status: str,
    scout_report_digest: str,
    dev_status: str,
    dev_evidence_digest: str | None,
    created_at: str | None = None,
) -> dict[str, object]:
    """Build and validate one canonical release manifest."""

    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "source": {"commit": source_commit, "tree": source_tree},
        "images": dict(images),
        "provenance": dict(provenance),
        "scout": {
            "status": scout_status,
            "reportDigest": scout_report_digest,
        },
        "devValidation": {
            "status": dev_status,
            "evidenceDigest": dev_evidence_digest,
        },
        "createdAt": created_at
        or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    manifest["integrity"] = {
        "algorithm": "sha256",
        "digest": calculate_integrity(manifest),
    }
    return verify_manifest(manifest)


def write_manifest(manifest: Mapping[str, object], output: Path) -> None:
    """Atomically write validated, stable JSON with a final newline."""

    verified = verify_manifest(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(verified, indent=2, sort_keys=True) + "\n"
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(output)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _pairs(values: Sequence[str], *, name: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, digest = value.partition("=")
        if not separator or not key or key in parsed:
            raise ManifestError(
                f"{name} values must use unique name=sha256:digest pairs"
            )
        parsed[key] = digest
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="NAME=DIGEST",
        help=f"required once for each of: {', '.join(IMAGE_NAMES)}",
    )
    parser.add_argument(
        "--provenance",
        action="append",
        default=[],
        metavar="NAME=DIGEST",
        help=f"required once for each of: {', '.join(IMAGE_NAMES)}",
    )
    parser.add_argument("--scout-status", choices=("passed", "failed"), required=True)
    parser.add_argument("--scout-report-digest", required=True)
    parser.add_argument(
        "--dev-status", choices=("pending", "passed", "failed"), required=True
    )
    parser.add_argument("--dev-evidence-digest")
    parser.add_argument("--created-at")
    arguments = parser.parse_args()
    try:
        manifest = create_manifest(
            source_commit=arguments.source_commit,
            source_tree=arguments.source_tree,
            images=_pairs(arguments.image, name="image"),
            provenance=_pairs(arguments.provenance, name="provenance"),
            scout_status=arguments.scout_status,
            scout_report_digest=arguments.scout_report_digest,
            dev_status=arguments.dev_status,
            dev_evidence_digest=arguments.dev_evidence_digest,
            created_at=arguments.created_at,
        )
        write_manifest(manifest, arguments.output)
    except (ManifestError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"created release manifest: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
