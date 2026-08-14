"""Calculate deterministic identities for the four StockAI image inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .verify_manifest import DIGEST_PATTERN, IMAGE_NAMES


@dataclass(frozen=True)
class BuildIdentities:
    """The complete application identity and each independently built image."""

    application: str
    images: dict[str, str]


_INPUTS: dict[str, tuple[str, ...]] = {
    "frontend": (
        "docker/frontend.Dockerfile",
        "docker/nginx.conf",
        "compose.yaml",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/index.html",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
        "frontend/src",
    ),
    "api": (
        "docker/api.Dockerfile",
        "compose.yaml",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "src",
    ),
    "mcp": (
        "docker/mcp.Dockerfile",
        "compose.yaml",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "src",
    ),
    "odoo": (
        "docker/odoo.Dockerfile",
        "docker/odoo-requirements.txt",
        "compose.odoo.yaml",
        "odoo/addons/stockai_procurement",
        "odoo/bootstrap",
    ),
}


def _files(root: Path, entries: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for entry in entries:
        candidate = root / entry
        if candidate.is_file():
            files.add(candidate)
        elif candidate.is_dir():
            files.update(
                path
                for path in candidate.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo", ".pyd"}
            )
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _identity(root: Path, entries: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for path in _files(root, entries):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def calculate_build_identities(root: Path) -> BuildIdentities:
    """Hash only the declared inputs consumed by each project image."""

    images = {name: _identity(root, _INPUTS[name]) for name in IMAGE_NAMES}
    canonical = json.dumps(images, sort_keys=True, separators=(",", ":")).encode()
    application = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return BuildIdentities(application=application, images=images)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    identities = calculate_build_identities(arguments.root.resolve())
    document = {
        "applicationIdentity": identities.application,
        "buildInputs": identities.images,
    }
    if not all(DIGEST_PATTERN.fullmatch(value) for value in identities.images.values()):
        print("error: invalid build identity", file=sys.stderr)
        return 2
    serialized = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
