"""Atomically update the approved dev image digests and release record."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .verify_manifest import IMAGE_NAMES, ManifestError, load_manifest, verify_manifest


class DesiredStateError(ValueError):
    """A safe desired-state update failure."""


def _render_overlay(source: str, images: Mapping[str, object]) -> str:
    rendered = source
    for name in IMAGE_NAMES:
        digest = images[name]
        block_pattern = re.compile(
            rf"(?ms)^\s*- name: stockai/{re.escape(name)}\s*$"
            rf"(?P<body>.*?)(?=^\s*- name:|^replacements:|\Z)"
        )
        blocks = list(block_pattern.finditer(rendered))
        if len(blocks) != 1:
            raise DesiredStateError(f"stockai/{name} digest must occur exactly once")
        block = blocks[0].group(0)
        expected_repository = f"newName: docker.io/weammakhoul/stockai-{name}"
        if block.count(expected_repository) != 1:
            raise DesiredStateError(f"stockai/{name} repository is not approved")
        digest_pattern = re.compile(r"(?m)(^\s+digest: )sha256:[0-9a-f]{64}(?=\s*$)")
        updated_block, count = digest_pattern.subn(rf"\g<1>{digest}", block)
        if count != 1:
            raise DesiredStateError(f"stockai/{name} digest must occur exactly once")
        rendered = (
            rendered[: blocks[0].start()] + updated_block + rendered[blocks[0].end() :]
        )
    return rendered


def _stage(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def update_desired_state(
    manifest: Mapping[str, object], *, overlay: Path, release: Path
) -> None:
    """Validate and replace both desired-state files after all staging succeeds."""

    verified = verify_manifest(manifest)
    try:
        source = overlay.read_text(encoding="utf-8")
    except OSError as error:
        raise DesiredStateError("cannot read dev overlay") from error
    images = verified["images"]
    if not isinstance(images, Mapping):
        raise DesiredStateError("release images are invalid")
    rendered = _render_overlay(source, images)
    serialized = json.dumps(verified, indent=2, sort_keys=True) + "\n"
    staged: list[Path] = []
    try:
        staged = [_stage(overlay, rendered), _stage(release, serialized)]
        staged[0].replace(overlay)
        staged[1].replace(release)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--overlay",
        type=Path,
        default=Path("deploy/kubernetes/overlays/dev/kustomization.yaml"),
    )
    parser.add_argument(
        "--release", type=Path, default=Path("deploy/releases/dev.json")
    )
    arguments = parser.parse_args()
    try:
        update_desired_state(
            load_manifest(arguments.manifest),
            overlay=arguments.overlay,
            release=arguments.release,
        )
    except (DesiredStateError, ManifestError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
