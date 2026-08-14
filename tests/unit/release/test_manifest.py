"""Contracts for deterministic, tamper-evident release metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.release.create_manifest import create_manifest, write_manifest
from scripts.release.verify_manifest import ManifestError, verify_manifest

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
        dev_status="passed",
        dev_evidence_digest=f"sha256:{'4' * 64}",
        created_at="2026-08-12T10:30:00Z",
    )


def test_complete_manifest_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    first = _manifest()
    second = _manifest()

    assert first == second
    assert first["images"] == IMAGE_DIGESTS
    assert first["provenance"] == PROVENANCE_DIGESTS
    assert first["applicationIdentity"] == f"sha256:{'5' * 64}"
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
            dev_status="passed",
            dev_evidence_digest=f"sha256:{'4' * 64}",
            created_at="2026-08-12T10:30:00Z",
        )


def test_manifest_rejects_tampered_artifact_identity() -> None:
    manifest = _manifest()
    images = manifest["images"]
    assert isinstance(images, dict)
    images["api"] = f"sha256:{'9' * 64}"

    with pytest.raises(ManifestError, match="integrity digest does not match"):
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
    assert set(images["required"]) == set(IMAGE_DIGESTS)
    assert images["additionalProperties"] is False
