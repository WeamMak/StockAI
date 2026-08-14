"""Contracts for append-only validation of one exact dev release."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from scripts.release.create_manifest import create_manifest, write_manifest
from scripts.release.record_validation import ValidationError, record_validation
from scripts.release.verify_manifest import calculate_integrity, load_manifest

from .test_manifest import IMAGE_DIGESTS, PROVENANCE_DIGESTS, SOURCE_COMMIT, SOURCE_TREE

ARGO_REVISION = "a" * 40
RELEASE_ID = f"sha256:{'0' * 64}"
EVIDENCE_DIGEST = f"sha256:{'4' * 64}"


def _pending() -> dict[str, object]:
    return create_manifest(
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        images=IMAGE_DIGESTS,
        provenance=PROVENANCE_DIGESTS,
        application_identity=f"sha256:{'5' * 64}",
        build_inputs={
            name: f"sha256:{index:064x}" for index, name in enumerate(IMAGE_DIGESTS, 5)
        },
        scout_status="passed",
        scout_report_digest=f"sha256:{'3' * 64}",
        dev_status="pending",
        dev_evidence_digest=None,
        created_at="2026-08-12T10:30:00Z",
    )


def _record(
    manifest: dict[str, object],
    *,
    result: str,
    smoke_run_id: str = "dev-smoke-20260814-001",
    evidence_digest: str = EVIDENCE_DIGEST,
    release_id: str | None = None,
    images: dict[str, str] | None = None,
    argo_revision: str = ARGO_REVISION,
) -> dict[str, object]:
    return record_validation(
        manifest,
        release_id=release_id or str(manifest["releaseId"]),
        images=images or IMAGE_DIGESTS,
        argo_revision=argo_revision,
        smoke_run_id=smoke_run_id,
        timestamp="2026-08-14T12:00:00Z",
        result=result,
        evidence_digest=evidence_digest,
    )


@pytest.mark.parametrize("result", ["passed", "failed"])
def test_pending_records_one_exact_release_attempt(result: str) -> None:
    original = _pending()
    recorded = _record(original, result=result)

    assert original["devValidation"] == {"status": "pending", "attempts": []}
    assert recorded["releaseId"] == original["releaseId"]
    assert recorded["images"] == original["images"]
    assert recorded["devValidation"] == {
        "status": result,
        "attempts": [
            {
                "releaseId": original["releaseId"],
                "images": IMAGE_DIGESTS,
                "argoRevision": ARGO_REVISION,
                "smokeRunId": "dev-smoke-20260814-001",
                "timestamp": "2026-08-14T12:00:00Z",
                "result": result,
                "evidenceDigest": EVIDENCE_DIGEST,
            }
        ],
    }


def test_failed_attempt_can_be_followed_by_explicit_pass_for_same_release() -> None:
    failed = _record(_pending(), result="failed")
    passed = _record(
        failed,
        result="passed",
        smoke_run_id="dev-smoke-20260814-002",
        evidence_digest=f"sha256:{'6' * 64}",
    )

    validation = passed["devValidation"]
    assert isinstance(validation, dict)
    assert validation["status"] == "passed"
    attempts = validation["attempts"]
    assert isinstance(attempts, list)
    assert [attempt["result"] for attempt in attempts] == ["failed", "passed"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("release", "release ID"),
        ("images", "image map"),
        ("argo", "Argo revision"),
        ("evidence", "evidence digest"),
    ],
)
def test_mismatched_or_missing_bound_evidence_is_rejected(
    change: str, message: str
) -> None:
    manifest = _pending()
    first = _record(manifest, result="failed") if change == "argo" else manifest
    arguments: dict[str, object] = {}
    if change == "release":
        arguments["release_id"] = RELEASE_ID
    elif change == "images":
        arguments["images"] = IMAGE_DIGESTS | {"api": f"sha256:{'9' * 64}"}
    elif change == "argo":
        arguments["argo_revision"] = "b" * 40
        arguments["smoke_run_id"] = "dev-smoke-20260814-002"
    else:
        arguments["evidence_digest"] = ""

    with pytest.raises(ValidationError, match=message):
        _record(first, result="passed", **arguments)  # type: ignore[arg-type]


def test_existing_attempts_are_not_rewritten_and_passed_evidence_is_locked() -> None:
    failed = _record(_pending(), result="failed")
    snapshot = deepcopy(failed["devValidation"])
    passed = _record(
        failed,
        result="passed",
        smoke_run_id="dev-smoke-20260814-002",
        evidence_digest=f"sha256:{'6' * 64}",
    )
    validation = passed["devValidation"]
    assert isinstance(validation, dict)
    attempts = validation["attempts"]
    assert isinstance(attempts, list)
    assert attempts[0] == snapshot["attempts"][0]  # type: ignore[index]

    with pytest.raises(ValidationError, match="already passed"):
        _record(
            passed,
            result="passed",
            smoke_run_id="dev-smoke-20260814-003",
        )
    with pytest.raises(ValidationError, match="already passed"):
        _record(
            passed,
            result="failed",
            smoke_run_id="dev-smoke-20260814-003",
        )


def test_atomic_file_recording_leaves_original_on_staged_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "dev.json"
    write_manifest(_pending(), path)
    original = path.read_bytes()

    def reject(_manifest: object) -> dict[str, object]:
        raise ValueError("staged validation failed")

    monkeypatch.setattr("scripts.release.create_manifest.verify_manifest", reject)
    with pytest.raises(ValueError, match="staged validation failed"):
        write_manifest(_record(_pending(), result="passed"), path)

    assert path.read_bytes() == original


def test_release_id_and_core_survive_validation_integrity_changes(
    tmp_path: Path,
) -> None:
    pending = _pending()
    passed = _record(pending, result="passed")

    assert pending["releaseId"] == passed["releaseId"]
    for field in (
        "source",
        "images",
        "provenance",
        "applicationIdentity",
        "buildInputs",
        "scout",
        "createdAt",
    ):
        assert passed[field] == pending[field]
    assert passed["integrity"] != pending["integrity"]

    path = tmp_path / "dev.json"
    write_manifest(passed, path)
    assert load_manifest(path) == passed


def test_tampered_attempt_is_rejected_even_with_recomputed_integrity() -> None:
    passed = _record(_pending(), result="passed")
    validation = passed["devValidation"]
    assert isinstance(validation, dict)
    attempts = validation["attempts"]
    assert isinstance(attempts, list)
    attempt = attempts[0]
    assert isinstance(attempt, dict)
    attempt["releaseId"] = RELEASE_ID
    integrity = passed["integrity"]
    assert isinstance(integrity, dict)
    integrity["digest"] = calculate_integrity(passed)

    with pytest.raises(ValueError, match="attempt release ID"):
        record_validation(
            passed,
            release_id=str(passed["releaseId"]),
            images=IMAGE_DIGESTS,
            argo_revision=ARGO_REVISION,
            smoke_run_id="dev-smoke-20260814-002",
            timestamp="2026-08-14T12:01:00Z",
            result="passed",
            evidence_digest=EVIDENCE_DIGEST,
        )
