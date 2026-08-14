"""Behavior contracts for atomic dev desired-state updates."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.release.create_manifest import create_manifest
from scripts.release.update_dev_overlay import DesiredStateError, update_desired_state

from .test_manifest import IMAGE_DIGESTS, PROVENANCE_DIGESTS, SOURCE_COMMIT, SOURCE_TREE


def _manifest() -> dict[str, object]:
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


def _overlay() -> str:
    return """images:
  - name: stockai/frontend
    newName: docker.io/weammakhoul/stockai-frontend
    digest: sha256:{frontend}
  - name: stockai/api
    newName: docker.io/weammakhoul/stockai-api
    digest: sha256:{api}
  - name: stockai/mcp
    newName: docker.io/weammakhoul/stockai-mcp
    digest: sha256:{mcp}
  - name: stockai/odoo
    newName: docker.io/weammakhoul/stockai-odoo
    digest: sha256:{odoo}
""".format(frontend="1" * 64, api="2" * 64, mcp="3" * 64, odoo="4" * 64)


def test_update_replaces_exactly_four_digests_and_release(tmp_path: Path) -> None:
    overlay = tmp_path / "kustomization.yaml"
    release = tmp_path / "dev.json"
    overlay.write_text(_overlay(), encoding="utf-8")

    update_desired_state(_manifest(), overlay=overlay, release=release)

    rendered = overlay.read_text(encoding="utf-8")
    assert rendered.count("digest: sha256:") == 4
    for digest in IMAGE_DIGESTS.values():
        assert f"digest: {digest}" in rendered
    assert release.exists()


def test_update_failure_leaves_both_targets_unchanged(tmp_path: Path) -> None:
    overlay = tmp_path / "kustomization.yaml"
    release = tmp_path / "dev.json"
    overlay.write_text(
        _overlay().replace("stockai/odoo", "stockai/extra"), encoding="utf-8"
    )
    release.write_text("original\n", encoding="utf-8")

    with pytest.raises(DesiredStateError, match="exactly once"):
        update_desired_state(_manifest(), overlay=overlay, release=release)

    assert "stockai/extra" in overlay.read_text(encoding="utf-8")
    assert release.read_text(encoding="utf-8") == "original\n"
