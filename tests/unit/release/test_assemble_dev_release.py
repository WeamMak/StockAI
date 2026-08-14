"""Contracts for verified changed-image assembly and prior digest carry-forward."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.release.assemble_dev_release import AssemblyError, assemble_release
from scripts.release.create_manifest import create_manifest

from .test_manifest import IMAGE_DIGESTS, PROVENANCE_DIGESTS, SOURCE_COMMIT, SOURCE_TREE

BUILD_INPUTS = {
    name: f"sha256:{index:064x}" for index, name in enumerate(IMAGE_DIGESTS, 5)
}


def _prior() -> dict[str, object]:
    return create_manifest(
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        images=IMAGE_DIGESTS,
        provenance=PROVENANCE_DIGESTS,
        application_identity=f"sha256:{'a' * 64}",
        build_inputs=BUILD_INPUTS,
        scout_status="passed",
        scout_report_digest=f"sha256:{'3' * 64}",
        dev_status="pending",
        dev_evidence_digest=None,
        created_at="2026-08-12T10:30:00Z",
    )


def test_one_changed_image_carries_only_verified_matching_prior_images(
    tmp_path: Path,
) -> None:
    result = {
        "name": "frontend",
        "digest": f"sha256:{'9' * 64}",
        "provenance": f"sha256:{'8' * 64}",
        "scoutStatus": "findings",
        "scoutReportDigest": f"sha256:{'7' * 64}",
    }
    (tmp_path / "frontend.json").write_text(json.dumps(result), encoding="utf-8")

    release = assemble_release(
        source_commit="a" * 40,
        source_tree="b" * 40,
        application_identity=f"sha256:{'c' * 64}",
        build_inputs=BUILD_INPUTS,
        prior=_prior(),
        results_directory=tmp_path,
        created_at="2026-08-14T10:00:00Z",
    )

    images = release["images"]
    assert isinstance(images, dict)
    assert images["frontend"] == result["digest"]
    assert images["api"] == IMAGE_DIGESTS["api"]
    scout = release["scout"]
    assert isinstance(scout, dict)
    assert scout["status"] == "findings"
    assert str(scout["reportDigest"]).startswith("sha256:")


def test_missing_changed_result_cannot_carry_stale_prior_digest(tmp_path: Path) -> None:
    changed_inputs = BUILD_INPUTS | {"api": f"sha256:{'f' * 64}"}

    with pytest.raises(AssemblyError, match="api image result is missing"):
        assemble_release(
            source_commit="a" * 40,
            source_tree="b" * 40,
            application_identity=f"sha256:{'c' * 64}",
            build_inputs=changed_inputs,
            prior=_prior(),
            results_directory=tmp_path,
            created_at="2026-08-14T10:00:00Z",
        )


def test_no_change_path_carries_all_four_verified_images(tmp_path: Path) -> None:
    release = assemble_release(
        source_commit="a" * 40,
        source_tree="b" * 40,
        application_identity=f"sha256:{'c' * 64}",
        build_inputs=BUILD_INPUTS,
        prior=_prior(),
        results_directory=tmp_path,
        created_at="2026-08-14T10:00:00Z",
    )

    assert release["images"] == IMAGE_DIGESTS
    assert release["provenance"] == PROVENANCE_DIGESTS


def test_four_image_path_records_scout_tool_error_without_rejecting_release(
    tmp_path: Path,
) -> None:
    for index, name in enumerate(IMAGE_DIGESTS):
        result = {
            "name": name,
            "digest": f"sha256:{index + 1:064x}",
            "provenance": f"sha256:{index + 5:064x}",
            "scoutStatus": "error" if name == "mcp" else "passed",
            "scoutReportDigest": f"sha256:{index + 9:064x}",
        }
        (tmp_path / f"{name}.json").write_text(json.dumps(result), encoding="utf-8")

    release = assemble_release(
        source_commit="a" * 40,
        source_tree="b" * 40,
        application_identity=f"sha256:{'c' * 64}",
        build_inputs=BUILD_INPUTS,
        prior=None,
        results_directory=tmp_path,
        created_at="2026-08-14T10:00:00Z",
    )

    scout = release["scout"]
    assert isinstance(scout, dict)
    assert scout["status"] == "error"
