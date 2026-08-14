"""Behavior contracts for local preparation of dev-validated prod digests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import scripts.release.promote_dev as promotion
from scripts.release.build_inputs import calculate_build_identities
from scripts.release.create_manifest import create_manifest
from scripts.release.promote_dev import (
    PromotionError,
    prepare_promotion,
    verify_validation_history,
)
from scripts.release.record_validation import record_validation
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
    pending = create_manifest(
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        images=IMAGE_DIGESTS,
        provenance=PROVENANCE_DIGESTS,
        application_identity=identities.application,
        build_inputs=identities.images,
        scout_status="findings",
        scout_report_digest=f"sha256:{'3' * 64}",
        dev_status="pending",
        dev_evidence_digest=None,
        created_at="2026-08-12T10:30:00Z",
    )
    return record_validation(
        pending,
        release_id=str(pending["releaseId"]),
        images=IMAGE_DIGESTS,
        argo_revision="a" * 40,
        smoke_run_id="dev-smoke-20260812-001",
        timestamp="2026-08-12T10:35:00Z",
        result="passed",
        evidence_digest=f"sha256:{'4' * 64}",
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
    attempts = validation["attempts"]
    assert isinstance(attempts, list)
    if status == "pending":
        attempts.clear()
    else:
        attempt = attempts[0]
        assert isinstance(attempt, dict)
        attempt["result"] = "failed"
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


def test_fetch_refreshes_origin_dev_remote_tracking_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_git(_root: Path, arguments: list[str]) -> str:
        calls.append(arguments)
        if arguments[0] == "symbolic-ref":
            return "feature/t24-fix\n"
        if arguments[0] in {"status", "fetch"}:
            return ""
        return pytest.fail(f"unexpected Git operation: {arguments}")

    monkeypatch.setattr(promotion, "_git", fake_git)
    monkeypatch.setattr(promotion, "_load_origin_dev", lambda _root: {})
    monkeypatch.setattr(
        promotion,
        "prepare_promotion",
        lambda *_args, **_kwargs: False,
    )

    assert promotion.promote(tmp_path) is False
    assert ["fetch", "--quiet", "origin", promotion.ORIGIN_DEV_REFSPEC] in calls


def test_missing_origin_dev_release_history_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_git(_root: Path, arguments: list[str]) -> str:
        if arguments[0] == "symbolic-ref":
            return "feature/t24\n"
        if arguments[0] in {"status", "fetch"}:
            return ""
        if arguments[0] == "log":
            return ""
        return pytest.fail(f"unexpected Git operation: {arguments}")

    monkeypatch.setattr(promotion, "_git", fake_git)

    with pytest.raises(PromotionError, match="history is missing"):
        promotion.promote(tmp_path)


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


def test_default_validation_renders_both_overlays_in_temporary_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_files(tmp_path)
    prod_overlay = tmp_path / promotion.PROD_OVERLAY
    prod_overlay.parent.mkdir(parents=True)
    prod_overlay.write_text(_overlay(), encoding="utf-8")
    dev_overlay = tmp_path / "deploy/kubernetes/overlays/dev/kustomization.yaml"
    dev_overlay.parent.mkdir(parents=True)
    dev_overlay.write_text(_overlay(), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr("scripts.release.promote_dev.subprocess.run", fake_run)

    prepare_promotion(
        _candidate(tmp_path),
        root=tmp_path,
        prod_overlay=prod_overlay,
        prod_release=tmp_path / promotion.PROD_RELEASE,
    )

    assert [call[:2] for call in calls] == [
        ["kubectl", "kustomize"],
        ["kubectl", "kustomize"],
    ]
    assert calls[0][-1].endswith("deploy/kubernetes/overlays/dev")
    assert calls[1][-1].endswith("deploy/kubernetes/overlays/prod")


def test_validation_history_accepts_only_append_only_evidence() -> None:
    _root = Path("unused")
    pending = create_manifest(
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        images=IMAGE_DIGESTS,
        provenance=PROVENANCE_DIGESTS,
        application_identity=f"sha256:{'a' * 64}",
        build_inputs={
            name: f"sha256:{index:064x}" for index, name in enumerate(IMAGE_DIGESTS, 1)
        },
        scout_status="passed",
        scout_report_digest=f"sha256:{'3' * 64}",
        dev_status="pending",
        dev_evidence_digest=None,
        created_at="2026-08-12T10:30:00Z",
    )
    passed = record_validation(
        pending,
        release_id=str(pending["releaseId"]),
        images=IMAGE_DIGESTS,
        argo_revision="a" * 40,
        smoke_run_id="dev-smoke-history",
        timestamp="2026-08-12T10:35:00Z",
        result="passed",
        evidence_digest=f"sha256:{'4' * 64}",
    )

    assert verify_validation_history([pending, passed]) == passed

    rewritten = json.loads(json.dumps(passed))
    validation = rewritten["devValidation"]
    integrity = rewritten["integrity"]
    assert isinstance(validation, dict)
    attempts = validation["attempts"]
    assert isinstance(attempts, list) and isinstance(attempts[0], dict)
    attempts[0]["evidenceDigest"] = f"sha256:{'5' * 64}"
    assert isinstance(integrity, dict)
    integrity["digest"] = calculate_integrity(rewritten)

    with pytest.raises(PromotionError, match="append-only"):
        verify_validation_history([pending, passed, rewritten])
