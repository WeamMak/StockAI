"""Task 09 contracts for the reproducible local Compose stack."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest

from tests.support.local_identity import sign_in_sync

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (PROJECT_ROOT / "compose.yaml", PROJECT_ROOT / "compose.test.yaml")
FICTIONAL_MCP_TOKEN = "fictional-compose-mcp-token-at-least-32-characters"
FICTIONAL_CRON_TOKEN = "fictional-compose-cron-token-at-least-32-characters"
TERMINAL_SCAN_STATUSES = frozenset({"succeeded", "failed"})


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _environment(*, scenario: str, frontend_port: int) -> dict[str, str]:
    return {
        **os.environ,
        "PROCUREMENT_ENVIRONMENT": "dev",
        "PROCUREMENT_FRONTEND_PORT": str(frontend_port),
        "PROCUREMENT_CRON_TOKEN": FICTIONAL_CRON_TOKEN,
        "PROCUREMENT_MCP_TOKEN": FICTIONAL_MCP_TOKEN,
        "PROCUREMENT_FAKE_ODOO_SCENARIO": scenario,
    }


def _compose_prefix(project_name: str) -> list[str]:
    command = ["docker", "compose", "--project-name", project_name]
    for compose_file in COMPOSE_FILES:
        command.extend(("--file", str(compose_file)))
    return command


def _run(
    command: list[str],
    *,
    environment: Mapping[str, str],
    timeout: float = 600,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rendered_compose(*, dynamodb: bool = False) -> dict[str, object]:
    environment = _environment(scenario="success", frontend_port=18080)
    profile = ["--profile", "dynamodb"] if dynamodb else []
    completed = _run(
        [
            *_compose_prefix("stockai-t09-config"),
            *profile,
            "config",
            "--format",
            "json",
        ],
        environment=environment,
    )
    rendered = json.loads(completed.stdout)
    assert isinstance(rendered, dict)
    return rendered


def test_compose_defines_the_bounded_core_and_persistence_test_topology() -> None:
    rendered = _rendered_compose()
    services = rendered["services"]
    networks = rendered["networks"]

    assert isinstance(services, dict)
    assert set(services) == {"frontend", "api", "mcp", "fake-odoo"}
    assert isinstance(networks, dict)
    assert set(networks) == {"edge", "backend"}
    assert networks["backend"]["internal"] is True
    assert "volumes" not in rendered

    expected_networks = {
        "frontend": {"edge"},
        "api": {"edge", "backend"},
        "mcp": {"backend"},
        "fake-odoo": {"backend"},
    }
    for service_name, expected in expected_networks.items():
        service = services[service_name]
        assert service["read_only"] is True
        assert service["healthcheck"]["test"]
        assert set(service["networks"]) == expected
        assert service["deploy"]["resources"]["limits"]["cpus"]
        assert service["deploy"]["resources"]["limits"]["memory"]
        assert any(mount.startswith("/tmp") for mount in service["tmpfs"])

    assert services["api"]["depends_on"]["mcp"]["condition"] == "service_healthy"
    assert services["mcp"]["depends_on"]["fake-odoo"]["condition"] == "service_healthy"
    assert services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"

    profiled = _rendered_compose(dynamodb=True)
    profiled_services = profiled["services"]
    assert isinstance(profiled_services, dict)
    assert set(profiled_services) == {
        "frontend",
        "api",
        "mcp",
        "fake-odoo",
        "dynamodb-local",
    }
    dynamodb = profiled_services["dynamodb-local"]
    assert dynamodb["image"] == "amazon/dynamodb-local:3.3.0"
    assert dynamodb["read_only"] is True
    assert set(dynamodb["networks"]) == {"backend", "edge"}
    assert dynamodb["healthcheck"]["test"]
    assert dynamodb["ports"][0]["host_ip"] == "127.0.0.1"
    assert profiled_services["api"]["depends_on"]["dynamodb-local"] == {
        "condition": "service_healthy",
        "required": False,
    }


@dataclass(frozen=True, slots=True)
class RunningComposeStack:
    public_url: str


@contextmanager
def _running_stack(scenario: str) -> Iterator[RunningComposeStack]:
    frontend_port = _unused_port()
    project_name = f"stockai-t09-{scenario.replace('_', '-')}-{uuid4().hex[:8]}"
    environment = _environment(scenario=scenario, frontend_port=frontend_port)
    prefix = _compose_prefix(project_name)
    logs = ""
    try:
        started = _run(
            [*prefix, "up", "--build", "--detach", "--wait", "--wait-timeout", "180"],
            environment=environment,
            check=False,
        )
        if started.returncode != 0:
            failed_logs = _run(
                [*prefix, "logs", "--no-color"],
                environment=environment,
                check=False,
            )
            raise AssertionError(
                "Compose stack failed to become healthy:\n"
                f"{started.stdout}\n{started.stderr}\n{failed_logs.stdout}"
            )
        yield RunningComposeStack(
            public_url=f"http://127.0.0.1:{frontend_port}",
        )
    finally:
        captured = _run(
            [*prefix, "logs", "--no-color"],
            environment=environment,
            check=False,
        )
        logs = f"{captured.stdout}\n{captured.stderr}"
        stopped = _run(
            [*prefix, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )
        if stopped.returncode != 0:
            raise AssertionError(
                f"Compose cleanup failed:\n{stopped.stdout}\n{stopped.stderr}"
            )
        assert FICTIONAL_MCP_TOKEN not in logs
        assert FICTIONAL_CRON_TOKEN not in logs


def _poll_scan(
    client: httpx.Client,
    location: str,
    *,
    headers: dict[str, str],
) -> httpx.Response:
    deadline = monotonic() + 15
    while monotonic() < deadline:
        response = client.get(location, headers=headers)
        if response.json()["status"] in TERMINAL_SCAN_STATUSES:
            return response
        sleep(0.05)
    raise AssertionError("The Compose scan did not reach a terminal state.")


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_error_code"),
    [
        ("success", "succeeded", None),
        ("no_valid_response", "failed", "NO_VALID_OFFER"),
        ("malformed", "failed", "ODOO_UNAVAILABLE"),
        ("timeout", "failed", "MCP_TIMEOUT"),
    ],
)
def test_local_stack_scenarios_cross_frontend_api_mcp_and_fake_odoo(
    scenario: str,
    expected_status: str,
    expected_error_code: str | None,
) -> None:
    case_payload: dict[str, object] | None = None
    with _running_stack(scenario) as stack:
        with httpx.Client(base_url=stack.public_url, timeout=5) as client:
            frontend = client.get("/")
            auth_headers = sign_in_sync(client)
            accepted = client.post("/api/v1/scans", headers=auth_headers)
            detail = _poll_scan(
                client,
                accepted.headers["location"],
                headers=auth_headers,
            )
            payload = detail.json()
            if expected_error_code is None:
                results = payload["results"]
                assert isinstance(results, list) and len(results) == 1
                case_id = results[0]["case_id"]
                case_detail = client.get(
                    f"{accepted.headers['location']}/cases/{case_id}",
                    headers=auth_headers,
                )
                case_detail.raise_for_status()
                case_payload = case_detail.json()

    assert frontend.status_code == 200
    assert "StockAI" in frontend.text
    assert accepted.status_code == 202
    assert detail.status_code == 200
    assert payload["status"] == expected_status
    assert "result" not in payload
    if expected_error_code is None:
        assert payload["results"][0]["outcome"] == "approval_ready"
        assert payload["results"][0]["product_id"] == "product-101"
        assert payload["error"] is None
        assert case_payload is not None
        result = case_payload["result"]
        assert isinstance(result, dict)
        assert result["outcome"] == "approval_ready"
        assert result["product_id"] == "product-101"
        assert case_payload["error"] is None
    else:
        assert payload["results"] == []
        assert payload["error"]["error_code"] == expected_error_code
