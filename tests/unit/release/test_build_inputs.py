"""Behavior contracts for StockAI image build-input identities."""

from __future__ import annotations

from pathlib import Path

from scripts.release.build_inputs import calculate_build_identities


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_identities_change_only_for_images_that_consume_the_file(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "docker/api.Dockerfile", "api")
    _write(tmp_path, "docker/mcp.Dockerfile", "mcp")
    _write(tmp_path, "docker/frontend.Dockerfile", "frontend")
    _write(tmp_path, "docker/nginx.conf", "nginx")
    _write(tmp_path, "docker/odoo.Dockerfile", "odoo")
    _write(tmp_path, "docker/odoo-requirements.txt", "requirements")
    _write(tmp_path, "pyproject.toml", "project")
    _write(tmp_path, "uv.lock", "lock")
    _write(tmp_path, "README.md", "readme")
    _write(tmp_path, "src/procurement/api/app.py", "api source")
    _write(tmp_path, "frontend/package.json", "frontend package")
    _write(tmp_path, "frontend/src/App.tsx", "frontend source")
    _write(tmp_path, "odoo/addons/stockai_procurement/__init__.py", "addon")
    _write(tmp_path, "odoo/bootstrap/bootstrap.py", "bootstrap")

    before = calculate_build_identities(tmp_path)
    _write(tmp_path, "frontend/src/App.tsx", "changed frontend source")
    after = calculate_build_identities(tmp_path)

    assert before.application != after.application
    assert before.images["frontend"] != after.images["frontend"]
    assert before.images["api"] == after.images["api"]
    assert before.images["mcp"] == after.images["mcp"]
    assert before.images["odoo"] == after.images["odoo"]


def test_generated_release_and_overlay_edits_do_not_change_build_identity(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "docker/api.Dockerfile", "api")
    _write(tmp_path, "docker/mcp.Dockerfile", "mcp")
    _write(tmp_path, "docker/frontend.Dockerfile", "frontend")
    _write(tmp_path, "docker/nginx.conf", "nginx")
    _write(tmp_path, "docker/odoo.Dockerfile", "odoo")
    _write(tmp_path, "docker/odoo-requirements.txt", "requirements")
    _write(tmp_path, "pyproject.toml", "project")
    _write(tmp_path, "uv.lock", "lock")
    _write(tmp_path, "README.md", "readme")

    before = calculate_build_identities(tmp_path)
    _write(tmp_path, "deploy/releases/dev.json", "generated")
    _write(tmp_path, "deploy/kubernetes/overlays/dev/kustomization.yaml", "digest edit")

    assert calculate_build_identities(tmp_path) == before


def test_docker_ignored_python_bytecode_does_not_change_build_identity(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "src/procurement/api/app.py", "api source")
    _write(tmp_path, "odoo/bootstrap/bootstrap.py", "bootstrap source")

    before = calculate_build_identities(tmp_path)
    _write(tmp_path, "src/procurement/api/__pycache__/app.cpython-312.pyc", "cache")
    _write(tmp_path, "odoo/bootstrap/bootstrap.pyo", "optimized cache")

    assert calculate_build_identities(tmp_path) == before
