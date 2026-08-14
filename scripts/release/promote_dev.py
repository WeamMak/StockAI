"""Prepare exact dev-validated digests for prod on a local feature branch."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .build_inputs import calculate_build_identities
from .update_dev_overlay import DesiredStateError, _render_overlay
from .verify_manifest import ManifestError, load_manifest, verify_manifest

PROD_OVERLAY = Path("deploy/kubernetes/overlays/prod/kustomization.yaml")
PROD_RELEASE = Path("deploy/releases/prod.json")
DEV_RELEASE = "deploy/releases/dev.json"


class PromotionError(ValueError):
    """A safe local-promotion failure."""


def verify_validation_history(
    manifests: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Verify one release's Git history only appends validation evidence."""

    if not manifests:
        raise PromotionError("candidate validation history is missing")
    try:
        verified = [verify_manifest(manifest) for manifest in manifests]
    except ManifestError as error:
        raise PromotionError(str(error)) from error
    release_id = verified[0]["releaseId"]
    if any(manifest["releaseId"] != release_id for manifest in verified):
        raise PromotionError("candidate validation history changed release ID")
    for previous, current in zip(verified, verified[1:], strict=False):
        previous_validation = previous["devValidation"]
        current_validation = current["devValidation"]
        if not isinstance(previous_validation, Mapping) or not isinstance(
            current_validation, Mapping
        ):
            raise PromotionError("candidate validation history is invalid")
        previous_attempts = previous_validation["attempts"]
        current_attempts = current_validation["attempts"]
        if not isinstance(previous_attempts, list) or not isinstance(
            current_attempts, list
        ):
            raise PromotionError("candidate validation history is invalid")
        if current_attempts[: len(previous_attempts)] != previous_attempts:
            raise PromotionError("candidate validation history is not append-only")
        if previous_validation["status"] == "passed" and current != previous:
            raise PromotionError("passed validation history is not append-only")
    return verified[-1]


def _stage(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def _default_validate(overlay: Path, release: Path) -> None:
    verified = load_manifest(release)
    images = verified["images"]
    if not isinstance(images, Mapping):
        raise PromotionError("candidate image map is invalid")
    rendered = overlay.read_text(encoding="utf-8")
    try:
        rerendered = _render_overlay(rendered, images)
    except DesiredStateError as error:
        raise PromotionError(str(error)) from error
    if rerendered != rendered:
        raise PromotionError(
            "staged prod overlay does not contain the candidate digests"
        )
    workspace = overlay.parents[4]
    command = shlex.split(os.environ.get("KUSTOMIZE", "kubectl kustomize"))
    if command not in (["kubectl", "kustomize"], ["kustomize", "build"]):
        raise PromotionError("KUSTOMIZE command is not allowlisted")
    for environment in ("dev", "prod"):
        path = workspace / "deploy/kubernetes/overlays" / environment
        try:
            result = subprocess.run(
                [*command, str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PromotionError("Kustomize validation could not run") from error
        if result.returncode != 0:
            raise PromotionError(f"{environment} Kustomize validation failed")


def prepare_promotion(
    candidate: Mapping[str, object],
    *,
    root: Path,
    prod_overlay: Path,
    prod_release: Path,
    validate: Callable[[Path, Path], None] | None = None,
) -> bool:
    """Validate and transactionally prepare prod files without staging them."""

    try:
        verified = verify_manifest(candidate)
    except ManifestError as error:
        raise PromotionError(str(error)) from error
    validation = verified["devValidation"]
    if not isinstance(validation, Mapping) or validation.get("status") != "passed":
        raise PromotionError("candidate must have passed dev validation")

    identities = calculate_build_identities(root)
    if (
        verified["applicationIdentity"] != identities.application
        or verified["buildInputs"] != identities.images
    ):
        raise PromotionError("candidate build inputs do not match the feature branch")

    images = verified["images"]
    if not isinstance(images, Mapping):
        raise PromotionError("candidate image map is invalid")
    try:
        current_overlay = prod_overlay.read_text(encoding="utf-8")
        rendered_overlay = _render_overlay(current_overlay, images)
    except (OSError, DesiredStateError) as error:
        raise PromotionError(str(error)) from error
    rendered_release = json.dumps(verified, indent=2, sort_keys=True) + "\n"

    staged: list[Path] = []
    try:
        validator = validate
        if validator is None:
            with tempfile.TemporaryDirectory(
                dir=root, prefix=".stockai-promotion-"
            ) as directory:
                workspace = Path(directory)
                shutil.copytree(
                    root / "deploy/kubernetes",
                    workspace / "deploy/kubernetes",
                )
                candidate_overlay = workspace / PROD_OVERLAY
                candidate_release = workspace / PROD_RELEASE
                candidate_release.parent.mkdir(parents=True, exist_ok=True)
                candidate_overlay.write_text(rendered_overlay, encoding="utf-8")
                candidate_release.write_text(rendered_release, encoding="utf-8")
                _default_validate(candidate_overlay, candidate_release)
        else:
            validation_overlay = _stage(prod_overlay, rendered_overlay)
            validation_release = _stage(prod_release, rendered_release)
            staged.extend((validation_overlay, validation_release))
            validator(validation_overlay, validation_release)
            for path in staged:
                path.unlink(missing_ok=True)
            staged.clear()

        staged = [
            _stage(prod_overlay, rendered_overlay),
            _stage(prod_release, rendered_release),
        ]
        release_matches = (
            prod_release.exists()
            and prod_release.read_text(encoding="utf-8") == rendered_release
        )
        if current_overlay == rendered_overlay and release_matches:
            return False
        staged[0].replace(prod_overlay)
        staged[1].replace(prod_release)
        return True
    except OSError as error:
        raise PromotionError("cannot prepare prod desired state") from error
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def _git(root: Path, arguments: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise PromotionError("required Git operation failed")
    return result.stdout


def _load_raw_manifest(raw: str) -> dict[str, object]:
    descriptor, name = tempfile.mkstemp(prefix="stockai-dev-release-", suffix=".json")
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(raw)
        return load_manifest(path)
    finally:
        path.unlink(missing_ok=True)


def _load_origin_dev(root: Path) -> dict[str, object]:
    commits = _git(
        root, ["log", "--format=%H", "origin/dev", "--", DEV_RELEASE]
    ).splitlines()
    if not commits:
        raise PromotionError("origin/dev release history is missing")
    current = _load_raw_manifest(_git(root, ["show", f"origin/dev:{DEV_RELEASE}"]))
    lineage: list[dict[str, object]] = []
    for commit in commits:
        manifest = _load_raw_manifest(_git(root, ["show", f"{commit}:{DEV_RELEASE}"]))
        if manifest["releaseId"] != current["releaseId"]:
            break
        lineage.append(manifest)
    lineage.reverse()
    return verify_validation_history(lineage)


def promote(root: Path, *, fetch: bool = True) -> bool:
    """Enforce branch safety, read origin/dev, and prepare local prod files."""

    root = root.resolve()
    branch = _git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"]).strip()
    if branch in {"dev", "main"} or not branch:
        raise PromotionError("promotion requires a feature branch, not dev or main")
    dirty = {
        line[3:]
        for line in _git(
            root, ["status", "--porcelain", "--untracked-files=all"]
        ).splitlines()
        if line
    }
    allowed = {PROD_OVERLAY.as_posix(), PROD_RELEASE.as_posix()}
    if dirty - allowed:
        raise PromotionError("promotion requires a clean feature branch")
    if fetch:
        _git(root, ["fetch", "--quiet", "origin", "dev"])
    candidate = _load_origin_dev(root)
    return prepare_promotion(
        candidate,
        root=root,
        prod_overlay=root / PROD_OVERLAY,
        prod_release=root / PROD_RELEASE,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="read the existing origin/dev remote-tracking ref without network access",
    )
    arguments = parser.parse_args()
    try:
        changed = promote(arguments.root, fetch=not arguments.no_fetch)
    except (PromotionError, ManifestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    message = (
        "prepared prod desired state" if changed else "prod desired state unchanged"
    )
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
