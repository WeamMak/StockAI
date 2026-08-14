"""Behavior contracts for local preparation of dev-validated prod digests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import scripts.release.promote_dev as promotion
from scripts.release.build_inputs import calculate_build_identities
from scripts.release.create_manifest import create_manifest
from scripts.release.promote_dev import PromotionError, prepare_promotion
from scripts.release.verify_manifest import calculate_integrity

from .test_manifest import IMAGE_DIGESTS, PROVENANCE_DIGESTS, SOURCE_COMMIT, SOURCE_TREE
from .test_update_dev_overlay import _overlay


def _build_files(root: Path) -> None:
    files = {
        "docker/api.Dockerfile": "api",
        "docker/mcp.Dockerfile": "mcp",
        "docker/frontend.Dockerfile": "frontend",
        "docker/nginx.conf": "nginx",
        "docker/odoo.Dockerfile": "odoo",
        "docker/odoo-requirements.txt": "requirements",
        "pyproject.toml": "project",
        "uv.lock": "lock",
        "README.md": "readme",
        "src/procurement/api/app.py": "api source",
        "frontend/package.json": "frontend package",
        "frontend/src/App.tsx": "frontend source",
        "odoo/addons/stockai_procurement/__init__.py": "addon",
        "odoo/bootstrap/bootstrap.py": "bootstrap",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _candidate(root: Path) -> dict[str, object]:
    identities = calculate_build_identities(root)
    return create_manifest(
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        images=IMAGE_DIGESTS,
        provenance=PROVENANCE_DIGESTS,
        application_identity=identities.application,
        build_inputs=identities.images,
        scout_status="findings",
        scout_report_digest=f"sha256:{'3' * 64}",
        dev_status="passed",
        dev_evidence_digest=f"sha256:{'4' * 64}",
        created_at="2026-08-12T10:30:00Z",
    )


def test_success_copies_exact_release_and_four_digests(tmp_path: Path) -> None:
    _build_files(tmp_path)
    overlay = tmp_path / "prod.yaml"
    release = tmp_path / "prod.json"
    overlay.write_text(_overlay(), encoding="utf-8")
    candidate = _candidate(tmp_path)

    changed = prepare_promotion(
        candidate,
        root=tmp_path,
        prod_overlay=overlay,
        prod_release=release,
        validate=lambda _overlay, _release: None,
    )

    assert changed is True
    assert json.loads(release.read_text(encoding="utf-8")) == candidate
    for digest in IMAGE_DIGESTS.values():
        assert f"digest: {digest}" in overlay.read_text(encoding="utf-8")
    assert (
        prepare_promotion(
            candidate,
            root=tmp_path,
            prod_overlay=overlay,
            prod_release=release,
            validate=lambda _overlay, _release: None,
        )
        is False
    )


def test_mismatched_feature_content_stops_without_mutation(tmp_path: Path) -> None:
    _build_files(tmp_path)
    candidate = _candidate(tmp_path)
    (tmp_path / "frontend/src/App.tsx").write_text("different", encoding="utf-8")
    overlay = tmp_path / "prod.yaml"
    release = tmp_path / "prod.json"
    overlay.write_text(_overlay(), encoding="utf-8")
    release.write_text("original\n", encoding="utf-8")

    with pytest.raises(PromotionError, match="build inputs do not match"):
        prepare_promotion(
            candidate,
            root=tmp_path,
            prod_overlay=overlay,
            prod_release=release,
            validate=lambda _overlay, _release: None,
        )

    assert release.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_release_must_have_passed_dev_evidence(tmp_path: Path, status: str) -> None:
    _build_files(tmp_path)
    candidate = _candidate(tmp_path)
    validation = candidate["devValidation"]
    assert isinstance(validation, dict)
    validation["status"] = status
    validation["evidenceDigest"] = None if status == "pending" else f"sha256:{'4' * 64}"
    integrity = candidate["integrity"]
    assert isinstance(integrity, dict)
    from scripts.release.verify_manifest import calculate_integrity

    integrity["digest"] = calculate_integrity(candidate)
    overlay = tmp_path / "prod.yaml"
    overlay.write_text(_overlay(), encoding="utf-8")

    with pytest.raises(PromotionError, match="passed dev validation"):
        prepare_promotion(
            candidate,
            root=tmp_path,
            prod_overlay=overlay,
            prod_release=tmp_path / "prod.json",
            validate=lambda _overlay, _release: None,
        )


def test_validation_failure_never_partially_replaces_targets(tmp_path: Path) -> None:
    _build_files(tmp_path)
    overlay = tmp_path / "prod.yaml"
    release = tmp_path / "prod.json"
    overlay.write_text(_overlay(), encoding="utf-8")
    release.write_text("original\n", encoding="utf-8")
    original_overlay = overlay.read_text(encoding="utf-8")

    def reject(_overlay: Path, _release: Path) -> None:
        raise PromotionError("render failed")

    with pytest.raises(PromotionError, match="render failed"):
        prepare_promotion(
            _candidate(tmp_path),
            root=tmp_path,
            prod_overlay=overlay,
            prod_release=release,
            validate=reject,
        )

    assert overlay.read_text(encoding="utf-8") == original_overlay
    assert release.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("branch", ["dev", "main"])
def test_protected_branches_are_rejected_before_reading_origin_dev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    monkeypatch.setattr(
        promotion,
        "_git",
        lambda _root, arguments: (
            f"{branch}\n"
            if arguments[0] == "symbolic-ref"
            else pytest.fail("no later Git operation is allowed")
        ),
    )

    with pytest.raises(PromotionError, match="feature branch"):
        promotion.promote(tmp_path, fetch=False)


def test_dirty_feature_branch_is_rejected_before_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_git(_root: Path, arguments: list[str]) -> str:
        if arguments[0] == "symbolic-ref":
            return "feature/t25\n"
        if arguments[0] == "status":
            return " M README.md\n"
        return pytest.fail("dirty promotion must stop before fetch or show")

    monkeypatch.setattr(promotion, "_git", fake_git)

    with pytest.raises(PromotionError, match="clean feature branch"):
        promotion.promote(tmp_path, fetch=True)


@pytest.mark.parametrize("failure", ["mutable", "missing", "tampered"])
def test_invalid_candidate_stops_before_prod_mutation(
    tmp_path: Path, failure: str
) -> None:
    _build_files(tmp_path)
    candidate = _candidate(tmp_path)
    images = candidate["images"]
    provenance = candidate["provenance"]
    integrity = candidate["integrity"]
    assert isinstance(images, dict)
    assert isinstance(provenance, dict)
    assert isinstance(integrity, dict)
    if failure == "mutable":
        images["frontend"] = "latest"
        integrity["digest"] = calculate_integrity(candidate)
    elif failure == "missing":
        del images["frontend"]
        integrity["digest"] = calculate_integrity(candidate)
    else:
        provenance["frontend"] = f"sha256:{'0' * 64}"
    overlay = tmp_path / "prod.yaml"
    overlay.write_text(_overlay(), encoding="utf-8")
    original = overlay.read_text(encoding="utf-8")

    with pytest.raises(PromotionError):
        prepare_promotion(
            candidate,
            root=tmp_path,
            prod_overlay=overlay,
            prod_release=tmp_path / "prod.json",
            validate=lambda _overlay, _release: None,
        )

    assert overlay.read_text(encoding="utf-8") == original
    assert not (tmp_path / "prod.json").exists()
