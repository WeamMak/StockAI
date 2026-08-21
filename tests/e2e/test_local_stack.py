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
from typing import Any, cast
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
import httpx
import pytest

from tests.support.local_identity import sign_in_sync

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (PROJECT_ROOT / "compose.yaml", PROJECT_ROOT / "compose.test.yaml")
FICTIONAL_MCP_TOKEN = "fictional-compose-mcp-token-at-least-32-characters"
FICTIONAL_CRON_TOKEN = "fictional-compose-cron-token-at-least-32-characters"
TERMINAL_SCAN_STATUSES = frozenset({"succeeded", "failed"})
DOCKER_PORT_EXPOSURE_ATTEMPTS = 3


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _environment(
    *,
    scenario: str,
    frontend_port: int,
    user_role: str = "officer",
    dynamodb_port: int | None = None,
) -> dict[str, str]:
    environment = {
        **os.environ,
        "PROCUREMENT_ENVIRONMENT": "dev",
        "PROCUREMENT_FRONTEND_PORT": str(frontend_port),
        "PROCUREMENT_CRON_TOKEN": FICTIONAL_CRON_TOKEN,
        "PROCUREMENT_MCP_TOKEN": FICTIONAL_MCP_TOKEN,
        "PROCUREMENT_FAKE_ODOO_SCENARIO": scenario,
        "PROCUREMENT_TEST_USER_ROLE": user_role,
    }
    if dynamodb_port is not None:
        environment["PROCUREMENT_PERSISTENCE_MODE"] = "dynamodb"
        environment["PROCUREMENT_DYNAMODB_LOCAL_PORT"] = str(dynamodb_port)
        environment["PROCUREMENT_DYNAMODB_ENDPOINT_URL"] = "http://dynamodb-local:8000"
    return environment


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


def _is_transient_docker_port_exposure_failure(
    completed: subprocess.CompletedProcess[str],
) -> bool:
    output = f"{completed.stdout}\n{completed.stderr}"
    return "ports are not available" in output and "/forwards/expose" in output


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
def _running_stack(
    scenario: str, *, user_role: str = "officer", durable: bool = False
) -> Iterator[RunningComposeStack]:
    frontend_port = _unused_port()
    dynamodb_port = _unused_port() if durable else None
    while dynamodb_port == frontend_port:
        dynamodb_port = _unused_port()
    project_name = (
        f"stockai-t09-{scenario.replace('_', '-')}-{user_role}-{uuid4().hex[:8]}"
    )
    environment = _environment(
        scenario=scenario,
        frontend_port=frontend_port,
        user_role=user_role,
        dynamodb_port=dynamodb_port,
    )
    prefix = _compose_prefix(project_name)
    profile = ["--profile", "dynamodb"] if durable else []
    logs = ""
    failure: BaseException | None = None
    try:
        if dynamodb_port is not None:
            database_started = _run(
                [
                    *prefix,
                    *profile,
                    "up",
                    "--detach",
                    "--wait",
                    "--wait-timeout",
                    "60",
                    "dynamodb-local",
                ],
                environment=environment,
                check=False,
            )
            if database_started.returncode != 0:
                raise AssertionError(
                    "DynamoDB Local failed to become healthy:\n"
                    f"{database_started.stdout}\n{database_started.stderr}"
                )
            dynamodb = boto3.client(
                "dynamodb",
                region_name="us-east-1",
                endpoint_url=f"http://127.0.0.1:{dynamodb_port}",
                aws_access_key_id="DUMMYIDEXAMPLE",
                aws_secret_access_key="DUMMYEXAMPLEKEY",
            )
            for table_name in (
                "stockai-dev-application",
                "stockai-dev-checkpoints",
            ):
                dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
        for attempt in range(DOCKER_PORT_EXPOSURE_ATTEMPTS):
            started = _run(
                [
                    *prefix,
                    *profile,
                    "up",
                    "--build",
                    "--detach",
                    "--wait",
                    "--wait-timeout",
                    "180",
                ],
                environment=environment,
                check=False,
            )
            if started.returncode == 0:
                break
            failed_logs = _run(
                [*prefix, *profile, "logs", "--no-color"],
                environment=environment,
                check=False,
            )
            can_retry = (
                dynamodb_port is None
                and attempt + 1 < DOCKER_PORT_EXPOSURE_ATTEMPTS
                and _is_transient_docker_port_exposure_failure(started)
            )
            if not can_retry:
                raise AssertionError(
                    "Compose stack failed to become healthy:\n"
                    f"{started.stdout}\n{started.stderr}\n{failed_logs.stdout}"
                )
            _run(
                [*prefix, *profile, "down", "--volumes", "--remove-orphans"],
                environment=environment,
                check=False,
            )
            sleep(1)
            frontend_port = _unused_port()
            project_name = (
                f"stockai-t09-{scenario.replace('_', '-')}-{user_role}-"
                f"{uuid4().hex[:8]}"
            )
            environment = _environment(
                scenario=scenario,
                frontend_port=frontend_port,
                user_role=user_role,
            )
            prefix = _compose_prefix(project_name)
        yield RunningComposeStack(
            public_url=f"http://127.0.0.1:{frontend_port}",
        )
    except BaseException as error:
        failure = error
        raise
    finally:
        captured = _run(
            [*prefix, *profile, "logs", "--no-color"],
            environment=environment,
            check=False,
        )
        logs = f"{captured.stdout}\n{captured.stderr}"
        stopped = _run(
            [*prefix, *profile, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )
        if stopped.returncode != 0:
            raise AssertionError(
                f"Compose cleanup failed:\n{stopped.stdout}\n{stopped.stderr}"
            )
        assert FICTIONAL_MCP_TOKEN not in logs
        assert FICTIONAL_CRON_TOKEN not in logs
        if failure is not None:
            failure.add_note(f"Compose logs (tail):\n{logs[-12_000:]}")


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


def _poll_case(
    client: httpx.Client,
    location: str,
    *,
    headers: dict[str, str],
    expected: str,
) -> dict[str, Any]:
    deadline = monotonic() + 15
    payload: dict[str, Any] = {}
    while monotonic() < deadline:
        response = client.get(location, headers=headers)
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        if payload["status"] == expected:
            return payload
        sleep(0.05)
    raise AssertionError(f"The Compose case did not reach {expected}: {payload}")


def _approval_payload(case: dict[str, Any]) -> dict[str, object]:
    result = case["result"]
    evidence = next(
        item for item in case["evidence"] if item["product_id"] == result["product_id"]
    )
    offer = next(
        item for item in evidence["offers"] if item["offer_id"] == result["offer_id"]
    )
    return {
        "environment": "dev",
        "case_revision": case["revision"],
        "po_id": case["draft"]["po_id"],
        "po_revision": case["draft"]["write_date"],
        "vendor_id": offer["vendor_id"],
        "quantity": result["quantity"],
        "amount": result["normalized_cost"],
        "currency": offer["currency"],
        "budget_status": result["budget_status"],
        "overage": evidence["budget"]["overage"],
        "evidence_digest": result["evidence_digest"],
        "budget_exception": False,
        "justification": None,
    }


def _rejection_payload(case: dict[str, Any]) -> dict[str, object]:
    return {
        "environment": "dev",
        "case_revision": case["revision"],
        "po_id": case["draft"]["po_id"],
        "po_revision": case["draft"]["write_date"],
        "evidence_digest": case["result"]["evidence_digest"],
        "reason": "The delivery plan changed.",
    }


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
    case_payload: dict[str, Any] | None = None
    user_role = "manager" if expected_error_code is None else "officer"
    with _running_stack(
        scenario,
        user_role=user_role,
        durable=expected_error_code is None,
    ) as stack:
        with httpx.Client(base_url=stack.public_url, timeout=15) as client:
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
                assert case_payload["status"] == "succeeded"
                assert case_payload["draft"] is None
                submitted = client.post(
                    f"{accepted.headers['location']}/cases/{case_id}/draft",
                    headers={
                        **auth_headers,
                        "Idempotency-Key": "draft-submit-e2e-001",
                    },
                    json={"case_revision": case_payload["revision"]},
                )
                submitted.raise_for_status()
                pending = _poll_case(
                    client,
                    f"{accepted.headers['location']}/cases/{case_id}",
                    headers=auth_headers,
                    expected="pending_approval",
                )
                approved = client.post(
                    f"/api/v1/cases/{case_id}/approve",
                    headers={
                        **auth_headers,
                        "Idempotency-Key": "approve-e2e-001",
                    },
                    json=_approval_payload(pending),
                )
                approved.raise_for_status()
                case_payload = _poll_case(
                    client,
                    f"{accepted.headers['location']}/cases/{case_id}",
                    headers=auth_headers,
                    expected="confirmed",
                )

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
        assert case_payload["decision"]["status"] == "confirmed"
    else:
        assert payload["results"] == []
        assert payload["error"]["error_code"] == expected_error_code


def test_officer_cannot_approve_or_reject_a_pending_draft() -> None:
    with _running_stack("success", user_role="officer") as stack:
        with httpx.Client(base_url=stack.public_url, timeout=5) as client:
            auth_headers = sign_in_sync(client)
            accepted = client.post("/api/v1/scans", headers=auth_headers)
            detail = _poll_scan(
                client,
                accepted.headers["location"],
                headers=auth_headers,
            )
            case_id = detail.json()["results"][0]["case_id"]
            case_location = f"{accepted.headers['location']}/cases/{case_id}"
            ready = _poll_case(
                client,
                case_location,
                headers=auth_headers,
                expected="succeeded",
            )
            submitted = client.post(
                f"{case_location}/draft",
                headers={
                    **auth_headers,
                    "Idempotency-Key": "draft-submit-officer-e2e-001",
                },
                json={"case_revision": ready["revision"]},
            )
            submitted.raise_for_status()
            pending = _poll_case(
                client,
                case_location,
                headers=auth_headers,
                expected="pending_approval",
            )
            approve = client.post(
                f"/api/v1/cases/{case_id}/approve",
                headers={
                    **auth_headers,
                    "Idempotency-Key": "officer-approve-e2e-001",
                },
                json=_approval_payload(pending),
            )
            reject = client.post(
                f"/api/v1/cases/{case_id}/reject",
                headers={
                    **auth_headers,
                    "Idempotency-Key": "officer-reject-e2e-001",
                },
                json=_rejection_payload(pending),
            )

    assert approve.status_code == 403
    assert reject.status_code == 403
