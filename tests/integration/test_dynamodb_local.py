"""Real DynamoDB Local persistence across an API process replacement."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any, cast
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
import httpx
from langchain_core.runnables import RunnableConfig

from procurement.adapters.aws.checkpointer import (
    DynamoCheckpointSettings,
    create_dynamodb_checkpointer,
)
from procurement.adapters.aws.dynamodb import DynamoApplicationRepository
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.ports.repositories import CaseRecord, IdempotencyConflictError
from tests.support.local_identity import sign_in_sync
from tests.support.local_skeleton import run_local_skeleton

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_TABLE = "stockai-dev-application"
CHECKPOINT_TABLE = "stockai-dev-checkpoints"
_DUMMY_ACCESS_KEY = "DUMMYIDEXAMPLE"
_DUMMY_SECRET_KEY = "DUMMYEXAMPLEKEY"


def _unused_port() -> int:
    for port in range(18_013, 18_113):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise AssertionError("No local DynamoDB test port is available")


def _compose_prefix(project_name: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--file",
        str(PROJECT_ROOT / "compose.yaml"),
        "--file",
        str(PROJECT_ROOT / "compose.test.yaml"),
        "--profile",
        "dynamodb",
    ]


def _run_compose(
    command: list[str],
    *,
    environment: Mapping[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _client(endpoint_url: str) -> Any:
    return boto3.client(
        "dynamodb",
        region_name="us-east-1",
        endpoint_url=endpoint_url,
        aws_access_key_id=_DUMMY_ACCESS_KEY,
        aws_secret_access_key=_DUMMY_SECRET_KEY,
    )


def _wait_for_dynamodb(client: Any) -> None:
    deadline = monotonic() + 60
    last_error: Exception | None = None
    while monotonic() < deadline:
        try:
            client.list_tables()
            return
        except Exception as error:
            last_error = error
            sleep(0.1)
    raise AssertionError("DynamoDB Local did not become ready") from last_error


def _create_table(client: Any, table_name: str) -> None:
    client.create_table(
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


@contextmanager
def _running_dynamodb_local() -> Iterator[tuple[str, Any]]:
    port = _unused_port()
    endpoint_url = f"http://127.0.0.1:{port}"
    project_name = f"stockai-t13-{uuid4().hex[:10]}"
    environment = {
        **os.environ,
        "PROCUREMENT_CRON_TOKEN": "fictional-t13-cron-token-at-least-32-characters",
        "PROCUREMENT_MCP_TOKEN": "fictional-t13-mcp-token-at-least-32-characters",
        "PROCUREMENT_DYNAMODB_LOCAL_PORT": str(port),
    }
    prefix = _compose_prefix(project_name)
    try:
        started = _run_compose(
            [*prefix, "up", "--detach", "dynamodb-local"],
            environment=environment,
            check=False,
        )
        if started.returncode != 0:
            raise AssertionError(
                f"DynamoDB Local failed to start:\n{started.stdout}\n{started.stderr}"
            )
        client = _client(endpoint_url)
        _wait_for_dynamodb(client)
        _create_table(client, APPLICATION_TABLE)
        _create_table(client, CHECKPOINT_TABLE)
        yield endpoint_url, client
    finally:
        _run_compose(
            [*prefix, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )


def _poll_scan(
    client: httpx.Client,
    location: str,
    *,
    headers: dict[str, str],
) -> dict[str, object]:
    deadline = monotonic() + 15
    while monotonic() < deadline:
        response = client.get(location, headers=headers)
        payload = cast(dict[str, object], response.json())
        if payload["status"] not in {"queued", "running"}:
            return payload
        sleep(0.05)
    raise AssertionError("DynamoDB-backed scan did not finish")


def test_scan_and_graph_state_survive_api_process_restart(
    tmp_path: Path,
) -> None:
    with _running_dynamodb_local() as (endpoint_url, client):
        with run_local_skeleton(
            tmp_path,
            persistence_mode="dynamodb",
            dynamodb_endpoint_url=endpoint_url,
            dynamodb_application_table=APPLICATION_TABLE,
            dynamodb_checkpoint_table=CHECKPOINT_TABLE,
        ) as skeleton:
            with httpx.Client(base_url=skeleton.api_url, timeout=5) as api:
                auth_headers = sign_in_sync(api)
                accepted = api.post("/api/v1/scans", headers=auth_headers)
                location = accepted.headers["location"]
                finished = _poll_scan(api, location, headers=auth_headers)

            scan_id = str(finished["scan_id"])
            results = cast(list[dict[str, object]], finished["results"])
            case_id = str(results[0]["case_id"])
            checkpoint_items = client.query(
                TableName=CHECKPOINT_TABLE,
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={
                    ":pk": {"S": f"CHECKPOINT_{case_id}"},
                },
            )["Items"]
            assert checkpoint_items
            audit_items = client.query(
                TableName=APPLICATION_TABLE,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": {"S": "ENV#dev"},
                    ":prefix": {"S": f"AUDIT#{case_id}#"},
                },
            )["Items"]
            assert {item["outcome"]["S"] for item in audit_items} == {
                "queued",
                "running",
                "succeeded",
            }
            session_items = client.query(
                TableName=APPLICATION_TABLE,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": {"S": "ENV#dev"},
                    ":prefix": {"S": "SESSION#"},
                },
            )["Items"]
            assert len(session_items) == 1
            assert "access_token" not in session_items[0]
            assert "id_token" not in session_items[0]

            skeleton.restart_api()
            with httpx.Client(base_url=skeleton.api_url, timeout=5) as restarted_api:
                restored = restarted_api.get(location, headers=auth_headers)
                listed = restarted_api.get("/api/v1/scans", headers=auth_headers)

            assert accepted.status_code == 202
            assert finished["status"] == "succeeded"
            assert restored.status_code == 200
            assert restored.json() == finished
            assert listed.json()["scans"][0]["scan_id"] == scan_id

            previous_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
            previous_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
            os.environ["AWS_ACCESS_KEY_ID"] = _DUMMY_ACCESS_KEY
            os.environ["AWS_SECRET_ACCESS_KEY"] = _DUMMY_SECRET_KEY
            try:
                saver = create_dynamodb_checkpointer(
                    DynamoCheckpointSettings(
                        environment=Environment.DEV,
                        table_name=CHECKPOINT_TABLE,
                        region_name="us-east-1",
                        endpoint_url=endpoint_url,
                    )
                )
                config: RunnableConfig = {"configurable": {"thread_id": case_id}}
                checkpoint = saver.get_tuple(config)
            finally:
                if previous_access_key is None:
                    os.environ.pop("AWS_ACCESS_KEY_ID", None)
                else:
                    os.environ["AWS_ACCESS_KEY_ID"] = previous_access_key
                if previous_secret_key is None:
                    os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
                else:
                    os.environ["AWS_SECRET_ACCESS_KEY"] = previous_secret_key

            assert checkpoint is not None
            values = checkpoint.checkpoint["channel_values"]
            assert values["scan_id"] == scan_id
            assert "result" in values
            assert "candidates" not in values
            assert "recommendation" not in values


def test_local_dynamodb_enforces_idempotent_conditional_case_creation() -> None:
    with _running_dynamodb_local() as (endpoint_url, client):
        repository = DynamoApplicationRepository(
            client=client,
            table_name=APPLICATION_TABLE,
            environment=Environment.DEV,
        )
        now = UtcTimestamp(datetime.now(tz=UTC))
        expiry = UtcTimestamp(now.value + timedelta(days=30))
        record = CaseRecord(
            case_id=CaseId(Environment.DEV, "scan-conditional-001"),
            revision=Revision(1),
            status="queued",
            trigger="manual",
            created_at=now,
            updated_at=now,
        )

        first = asyncio.run(
            repository.create_case(
                record,
                idempotency_key="request-conditional-001",
                expires_at=expiry,
            )
        )
        repeated = asyncio.run(
            repository.create_case(
                record,
                idempotency_key="request-conditional-001",
                expires_at=expiry,
            )
        )
        conflicting = CaseRecord(
            case_id=CaseId(Environment.DEV, "scan-conditional-002"),
            revision=Revision(1),
            status="queued",
            trigger="manual",
            created_at=now,
            updated_at=now,
        )

        try:
            asyncio.run(
                repository.create_case(
                    conflicting,
                    idempotency_key="request-conditional-001",
                    expires_at=expiry,
                )
            )
        except IdempotencyConflictError:
            pass
        else:
            raise AssertionError("idempotency key reuse created a duplicate case")

        assert first.created is True
        assert repeated.created is False
        cases = client.query(
            TableName=APPLICATION_TABLE,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": "ENV#dev"},
                ":prefix": {"S": "CASE#"},
            },
        )["Items"]
        assert len(cases) == 1
