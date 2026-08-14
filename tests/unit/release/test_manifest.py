"""Contracts for deterministic, tamper-evident release metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.release.create_manifest import create_manifest, write_manifest
from scripts.release.record_validation import record_validation
from scripts.release.verify_manifest import (
    ManifestError,
    calculate_integrity,
    calculate_release_id,
    verify_manifest,
    verify_promotion,
)

SOURCE_COMMIT = "1" * 40
SOURCE_TREE = "2" * 40
IMAGE_DIGESTS = {
    "frontend": f"sha256:{'a' * 64}",
    "api": f"sha256:{'b' * 64}",
    "mcp": f"sha256:{'c' * 64}",
    "odoo": f"sha256:{'d' * 64}",
}
PROVENANCE_DIGESTS = {
    "frontend": f"sha256:{'e' * 64}",
    "api": f"sha256:{'f' * 64}",
    "mcp": f"sha256:{'0' * 64}",
    "odoo": f"sha256:{'1' * 64}",
}


def _manifest() -> dict[str, object]:
    return create_manifest(
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        images=IMAGE_DIGESTS,
        provenance=PROVENANCE_DIGESTS,
        application_identity=f"sha256:{'5' * 64}",
        build_inputs={
            "frontend": f"sha256:{'6' * 64}",
            "api": f"sha256:{'7' * 64}",
            "mcp": f"sha256:{'8' * 64}",
            "odoo": f"sha256:{'9' * 64}",
        },
        scout_status="passed",
        scout_report_digest=f"sha256:{'3' * 64}",
        dev_status="pending",
        dev_evidence_digest=None,
        created_at="2026-08-12T10:30:00Z",
    )


def test_complete_manifest_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    first = _manifest()
    second = _manifest()

    assert first == second
    assert first["images"] == IMAGE_DIGESTS
    assert first["provenance"] == PROVENANCE_DIGESTS
    assert first["applicationIdentity"] == f"sha256:{'5' * 64}"
    assert first["releaseId"] == calculate_release_id(first)
    assert verify_manifest(first) == first

    output = tmp_path / "release.json"
    write_manifest(first, output)
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(output.read_text(encoding="utf-8")) == first


@pytest.mark.parametrize("missing_image", IMAGE_DIGESTS)
def test_manifest_rejects_a_missing_required_image(missing_image: str) -> None:
    images = IMAGE_DIGESTS | {}
    del images[missing_image]

    with pytest.raises(ManifestError, match="images must contain exactly"):
        create_manifest(
            source_commit=SOURCE_COMMIT,
            source_tree=SOURCE_TREE,
            images=images,
            provenance=PROVENANCE_DIGESTS,
            application_identity=f"sha256:{'5' * 64}",
            build_inputs={
                name: f"sha256:{index:064x}"
                for index, name in enumerate(IMAGE_DIGESTS, 5)
            },
            scout_status="passed",
            scout_report_digest=f"sha256:{'3' * 64}",
            dev_status="pending",
            dev_evidence_digest=None,
            created_at="2026-08-12T10:30:00Z",
        )


def test_manifest_rejects_tampered_artifact_identity() -> None:
    manifest = _manifest()
    images = manifest["images"]
    assert isinstance(images, dict)
    images["api"] = f"sha256:{'9' * 64}"

    with pytest.raises(ManifestError, match="releaseId"):
        verify_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("createdAt", "2026-08-12 10:30:00", "createdAt"),
        ("scout.status", "unknown", "scout status"),
        ("devValidation.status", "unknown", "dev validation status"),
    ],
)
def test_manifest_rejects_malformed_release_metadata(
    field: str, value: str, message: str
) -> None:
    manifest = _manifest()
    target: dict[str, object] = manifest
    parts = field.split(".")
    for part in parts[:-1]:
        nested = target[part]
        assert isinstance(nested, dict)
        target = nested
    target[parts[-1]] = value

    with pytest.raises(ManifestError, match=message):
        verify_manifest(manifest)


def test_schema_requires_exactly_the_four_project_images() -> None:
    schema_path = Path(__file__).parents[3] / "deploy" / "releases" / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    images = schema["$defs"]["imageMap"]

    assert schema["properties"]["images"] == {"$ref": "#/$defs/imageMap"}
    assert schema["properties"]["buildInputs"] == {"$ref": "#/$defs/imageMap"}
    assert schema["properties"]["releaseId"] == {"$ref": "#/$defs/digest"}
    assert schema["properties"]["devValidation"]["properties"]["attempts"] == {
        "type": "array",
        "maxItems": 20,
        "items": {"$ref": "#/$defs/validationAttempt"},
    }
    assert set(images["required"]) == set(IMAGE_DIGESTS)
    assert images["additionalProperties"] is False


def test_release_id_rejects_core_tampering_even_with_new_document_integrity() -> None:
    manifest = _manifest()
    source = manifest["source"]
    integrity = manifest["integrity"]
    assert isinstance(source, dict)
    assert isinstance(integrity, dict)
    source["commit"] = "9" * 40
    integrity["digest"] = calculate_integrity(manifest)

    with pytest.raises(ManifestError, match="releaseId"):
        verify_manifest(manifest)


def _passed_manifest(*, created_at: str, argo_revision: str) -> dict[str, object]:
    manifest = _manifest()
    manifest["createdAt"] = created_at
    manifest["releaseId"] = calculate_release_id(manifest)
    integrity = manifest["integrity"]
    assert isinstance(integrity, dict)
    integrity["digest"] = calculate_integrity(manifest)
    return record_validation(
        manifest,
        release_id=str(manifest["releaseId"]),
        images=IMAGE_DIGESTS,
        argo_revision=argo_revision,
        smoke_run_id=f"dev-smoke-{argo_revision[:8]}",
        timestamp="2026-08-12T10:35:00Z",
        result="passed",
        evidence_digest=f"sha256:{'4' * 64}",
    )


def test_prod_promotion_must_equal_the_complete_passed_dev_release() -> None:
    dev = _passed_manifest(created_at="2026-08-12T10:30:00Z", argo_revision="a" * 40)
    other = _passed_manifest(created_at="2026-08-12T10:31:00Z", argo_revision="b" * 40)

    assert verify_promotion(dev, dev.copy()) == dev
    with pytest.raises(ManifestError, match="exact passed dev release"):
        verify_promotion(dev, other)


def test_prod_promotion_rejects_a_pending_dev_source() -> None:
    pending = _manifest()

    with pytest.raises(ManifestError, match="passed dev validation"):
        verify_promotion(pending, pending.copy())
