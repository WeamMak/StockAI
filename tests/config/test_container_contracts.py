"""Contracts for the three immutable StockAI service images."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKER_ROOT = PROJECT_ROOT / "docker"
DOCKERFILES = {
    "api": DOCKER_ROOT / "api.Dockerfile",
    "mcp": DOCKER_ROOT / "mcp.Dockerfile",
    "frontend": DOCKER_ROOT / "frontend.Dockerfile",
}
SHA256_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")


def _dockerfile(service: str) -> str:
    return DOCKERFILES[service].read_text(encoding="utf-8")


def _base_images(dockerfile: str) -> list[str]:
    return re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)


def _runtime_stage(dockerfile: str) -> str:
    stages = re.split(r"^FROM\s+", dockerfile, flags=re.MULTILINE)
    return stages[-1]


def test_images_use_pinned_multi_stage_bases_and_non_root_runtimes() -> None:
    for service in DOCKERFILES:
        dockerfile = _dockerfile(service)
        base_images = _base_images(dockerfile)
        runtime_stage = _runtime_stage(dockerfile)

        assert len(base_images) >= 2, service
        assert all(SHA256_PATTERN.search(image) for image in base_images), service
        assert re.search(
            r"^USER\s+(?!0(?::0)?$|root$)\S+", runtime_stage, re.MULTILINE
        ), service


def test_backend_images_use_only_their_fixed_process_entrypoints() -> None:
    configuration = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert configuration["project"]["scripts"] == {
        "stockai-api": "procurement.bootstrap.api:run",
        "stockai-mcp": "procurement.bootstrap.mcp:run",
    }
    assert 'ENTRYPOINT ["stockai-api"]' in _runtime_stage(_dockerfile("api"))
    assert 'ENTRYPOINT ["stockai-mcp"]' in _runtime_stage(_dockerfile("mcp"))


def test_images_have_health_checks_and_one_explicit_writable_path() -> None:
    expected_health_paths = {
        "api": "/health/live",
        "mcp": "/metrics",
        "frontend": "/health/live",
    }

    for service, health_path in expected_health_paths.items():
        runtime_stage = _runtime_stage(_dockerfile(service))
        assert "HEALTHCHECK" in runtime_stage, service
        assert health_path in runtime_stage, service
        assert 'VOLUME ["/tmp"]' in runtime_stage, service


def test_frontend_serves_compiled_assets_and_proxies_same_origin() -> None:
    dockerfile = _dockerfile("frontend")
    runtime_stage = _runtime_stage(dockerfile)
    nginx_configuration = (DOCKER_ROOT / "nginx.conf").read_text(encoding="utf-8")

    assert "/frontend/dist" in runtime_stage
    assert "npm run build" not in runtime_stage
    assert "vite" not in runtime_stage.lower()
    assert "listen 8080;" in nginx_configuration
    assert "location /api/" in nginx_configuration
    assert "location /auth/" in nginx_configuration
    assert "proxy_pass http://api:8000;" in nginx_configuration
    assert "try_files $uri $uri/ /index.html;" in nginx_configuration
    assert "pid /tmp/nginx.pid;" in nginx_configuration
    assert "_temp_path /tmp/" in nginx_configuration


def test_build_context_is_minimal_and_excludes_secret_material() -> None:
    ignored = set(
        (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    )
    required_ignores = {
        ".git",
        ".env*",
        "**/*.key",
        "**/*.pem",
        ".venv",
        "frontend/node_modules",
        "frontend/dist",
        "tests",
        "docs",
        "reports",
    }

    assert required_ignores <= ignored
    for service in DOCKERFILES:
        assert "COPY . ." not in _dockerfile(service), service
