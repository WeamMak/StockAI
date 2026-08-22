"""Live exact-release walking-skeleton proof shared by dev and prod."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
import httpx
import pytest
from scripts.release.verify_manifest import IMAGE_NAMES, load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_STATUSES = {"succeeded", "failed"}
METRIC_QUERIES = (
    'sum(procurement_llm_calls_total{status="success"})',
    'sum(procurement_agent_mcp_calls_total{status="success"})',
    'sum(procurement_agent_mcp_calls_total{tool="get_procurement_preferences",status="success"})',
    'sum(procurement_agent_mcp_calls_total{tool="create_purchase_order_draft",status="success"})',
    'sum(procurement_mcp_tool_calls_total{status="success"})',
    'sum(procurement_mcp_tool_calls_total{tool="create_purchase_order_draft",status="success"})',
    'sum(procurement_odoo_calls_total{status="success"})',
    'sum(procurement_odoo_calls_total{operation="create_purchase_order",status="success"})',
)
EXPECTED_METRIC_JOBS = {"stockai-agent-api", "stockai-procurement-mcp"}
TARGET_HEALTH_QUERY = (
    'min by (job) (up{job=~"stockai-agent-api|stockai-procurement-mcp"})'
)


def _required(name: str, *, environment: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise AssertionError(
            f"{name} is required for the live {environment} smoke test"
        )
    return value


def _aws_client(service: str) -> Any:
    return boto3.client(service, region_name="us-east-1")


def _control_plane_id() -> str:
    response = _aws_client("ec2").describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": ["weam-stockai-control-plane"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    assert len(instances) == 1
    return str(instances[0]["InstanceId"])


def _deployed_release(environment: str) -> tuple[str, dict[str, str]]:
    commands = [
        "set -eu",
        "export KUBECONFIG=/etc/kubernetes/admin.conf",
        (
            f"kubectl -n argocd get application stockai-{environment} "
            "-o jsonpath='{.status.sync.revision}{\"|\"}'"
        ),
        (
            f"kubectl -n argocd get application stockai-{environment} "
            '-o jsonpath=\'{.status.sync.status}{"|"}{.status.health.status}{"\\n"}\''
        ),
    ]
    deployments = {
        "frontend": ("stockai-frontend", "frontend"),
        "api": ("stockai-agent-api", "api"),
        "mcp": ("stockai-procurement-mcp", "procurement-mcp"),
        "odoo": ("stockai-odoo", "odoo"),
    }
    for name in IMAGE_NAMES:
        deployment, container = deployments[name]
        image_path = f'.spec.template.spec.containers[?(@.name=="{container}")].image'
        commands.append(
            f"kubectl -n {environment} get deployment "
            f"{deployment} -o jsonpath='{{{image_path}}}{{\"\\n\"}}'"
        )
    ssm = _aws_client("ssm")
    control_plane_id = _control_plane_id()
    sent = ssm.send_command(
        InstanceIds=[control_plane_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
        TimeoutSeconds=60,
    )
    command_id = str(sent["Command"]["CommandId"])
    deadline = time.monotonic() + 90
    invocation: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=control_plane_id,
            )
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue
        if invocation.get("Status") in {
            "Success",
            "Cancelled",
            "Failed",
            "TimedOut",
        }:
            break
        time.sleep(1)
    assert invocation.get("Status") == "Success"
    lines = str(invocation.get("StandardOutputContent", "")).splitlines()
    assert len(lines) == 5
    revision, sync, health = lines[0].split("|")
    assert sync == "Synced"
    assert health == "Healthy"
    return revision, dict(zip(IMAGE_NAMES, lines[1:], strict=True))


def _poll_scan(
    client: httpx.Client, location: str, *, environment: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        response = client.get(location)
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict)
        if payload.get("status") in TERMINAL_STATUSES:
            return payload
        time.sleep(2)
    raise AssertionError(f"the live {environment} scan did not reach a terminal state")


def _poll_case(
    client: httpx.Client,
    location: str,
    *,
    expected: str,
    environment: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(location)
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict)
        if payload.get("status") == expected:
            return payload
        time.sleep(2)
    raise AssertionError(
        f"the live {environment} case did not reach {expected}: {payload}"
    )


def _approval_ready_case_summary(completed: dict[str, Any]) -> dict[str, Any]:
    """Return one approval-ready case summary from a terminal scan aggregate."""

    results = completed.get("results")
    assert isinstance(results, list)
    approval_ready = [
        result
        for result in results
        if isinstance(result, dict) and result.get("outcome") == "approval_ready"
    ]
    assert approval_ready, "the live scan produced no approval-ready case"
    case_id = approval_ready[0].get("case_id")
    assert isinstance(case_id, str) and case_id
    return approval_ready[0]


def _grafana_password(environment: str) -> str:
    response = _aws_client("secretsmanager").get_secret_value(
        SecretId=f"weam-stockai/{environment}/grafana-admin-password"
    )
    value = response.get("SecretString")
    assert isinstance(value, str) and value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    assert isinstance(parsed, dict) and isinstance(parsed.get("value"), str)
    return str(parsed["value"])


def _grafana_query(
    client: httpx.Client, datasource: str, path: str, params: dict[str, str]
) -> dict[str, Any]:
    response = client.get(
        f"/api/datasources/proxy/uid/{datasource}/{path}", params=params
    )
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict) and payload.get("status") == "success"
    return payload


def _metric_total(payload: dict[str, Any]) -> float:
    results = payload.get("data", {}).get("result", [])
    return sum(float(result["value"][1]) for result in results)


def _targets_are_up(payload: dict[str, Any]) -> bool:
    results = payload.get("data", {}).get("result", [])
    jobs = {
        str(result.get("metric", {}).get("job")): float(result["value"][1])
        for result in results
    }
    return set(jobs) == EXPECTED_METRIC_JOBS and all(
        value == 1 for value in jobs.values()
    )


def _wait_for_metric_deltas(
    client: httpx.Client,
    baselines: dict[str, float],
    *,
    timeout_seconds: float = 90,
    poll_seconds: float = 5,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    missing = set(baselines)
    targets_up = False
    while time.monotonic() < deadline:
        missing = {
            query
            for query, baseline in baselines.items()
            if _metric_total(
                _grafana_query(
                    client,
                    "stockai-prometheus",
                    "api/v1/query",
                    {"query": query},
                )
            )
            <= baseline
        }
        targets_up = _targets_are_up(
            _grafana_query(
                client,
                "stockai-prometheus",
                "api/v1/query",
                {"query": TARGET_HEALTH_QUERY},
            )
        )
        if not missing and targets_up:
            return
        time.sleep(poll_seconds)
    raise AssertionError(
        "missing current-run metric deltas: "
        f"{sorted(missing)}; all application targets up: {targets_up}"
    )


def _wait_for_logs(
    client: httpx.Client,
    *,
    environment: str,
    scan_id: str,
    started_at: datetime,
) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = _grafana_query(
            client,
            "stockai-loki",
            "loki/api/v1/query_range",
            {
                "query": f'{{environment="{environment}"}} |= "{scan_id}"',
                "start": str(
                    int((started_at - timedelta(minutes=2)).timestamp() * 1e9)
                ),
                "end": str(
                    int((datetime.now(UTC) + timedelta(minutes=1)).timestamp() * 1e9)
                ),
                "limit": "1000",
            },
        )
        if scan_id in json.dumps(payload, sort_keys=True):
            return payload
        time.sleep(5)
    raise AssertionError("matching scan logs did not arrive in Loki")


def run_exact_walking_skeleton(environment: str) -> None:
    """Exercise one exact deployed release through its public critical path."""

    assert environment in {"dev", "prod"}
    upper_environment = environment.upper()
    manifest = load_manifest(PROJECT_ROOT / f"deploy/releases/{environment}.json")
    release_id = str(manifest["releaseId"])
    expected_images = manifest["images"]
    assert isinstance(expected_images, dict)
    argo_revision, deployed_images = _deployed_release(environment)
    for name in IMAGE_NAMES:
        assert deployed_images[name].endswith(f"@{expected_images[name]}")

    base_url = os.environ.get(
        f"STOCKAI_{upper_environment}_BASE_URL",
        f"https://app.{environment}.stockai.fursa.click",
    )
    assert httpx.URL(base_url).scheme == "https"
    smoke_run_id = _required("STOCKAI_SMOKE_RUN_ID", environment=environment)
    session_token = _required(
        f"STOCKAI_{upper_environment}_SESSION_TOKEN", environment=environment
    )
    csrf_token = _required(
        f"STOCKAI_{upper_environment}_CSRF_TOKEN", environment=environment
    )
    started_at = datetime.now(UTC)
    grafana_password = _grafana_password(environment)
    with httpx.Client(
        base_url=f"https://grafana.{environment}.stockai.fursa.click",
        auth=("admin", grafana_password),
        timeout=30,
    ) as grafana:
        health = grafana.get("/api/health")
        health.raise_for_status()
        metric_baselines = {
            query: _metric_total(
                _grafana_query(
                    grafana,
                    "stockai-prometheus",
                    "api/v1/query",
                    {"query": query},
                )
            )
            for query in METRIC_QUERIES
        }
    cookies = {
        "stockai_session": session_token,
        "stockai_csrf": csrf_token,
    }
    with httpx.Client(
        base_url=base_url,
        cookies=cookies,
        timeout=30,
        follow_redirects=True,
    ) as client:
        frontend = client.get("/")
        frontend.raise_for_status()
        assert "StockAI" in frontend.text
        session = client.get("/api/v1/session")
        session.raise_for_status()
        principal = session.json()
        assert principal["role"] in {"officer", "manager"}
        accepted = client.post(
            "/api/v1/scans",
            headers={"X-CSRF-Token": csrf_token, "X-Request-ID": smoke_run_id},
        )
        assert accepted.status_code == 202
        location = accepted.headers["location"]
        completed = _poll_scan(client, location, environment=environment)
        assert completed["status"] == "succeeded", completed.get("error")
        approval_summary = _approval_ready_case_summary(completed)
        assert approval_summary["status"] == "succeeded"
        case_id = str(approval_summary["case_id"])
        case_location = f"{location}/cases/{case_id}"
        case_response = client.get(case_location)
        case_response.raise_for_status()
        case_detail = case_response.json()
        assert isinstance(case_detail, dict)
        assert case_detail["status"] == "succeeded", case_detail.get("error")
        assert case_detail["case_id"] == case_id
        assert case_detail["draft"] is None
        submitted = client.post(
            f"{case_location}/draft",
            headers={
                "X-CSRF-Token": csrf_token,
                "X-Request-ID": smoke_run_id,
                "Idempotency-Key": f"{smoke_run_id}-draft",
            },
            json={"case_revision": case_detail["revision"]},
        )
        assert submitted.status_code == 202, submitted.text
        case_detail = _poll_case(
            client,
            case_location,
            expected="pending_approval",
            environment=environment,
        )
        listed = client.get("/api/v1/scans")
        listed.raise_for_status()
        assert any(
            scan.get("scan_id") == completed.get("scan_id")
            for scan in listed.json()["scans"]
        )

    assert case_detail["result"]["outcome"] == "approval_ready"
    draft = case_detail["draft"]
    assert isinstance(draft, dict)
    assert isinstance(draft.get("po_id"), int) and draft["po_id"] > 0
    assert isinstance(draft.get("write_date"), str) and draft["write_date"]
    assert draft.get("state") == "draft"
    assert isinstance(draft.get("partner_id"), int) and draft["partner_id"] > 0
    assert isinstance(draft.get("currency_id"), int) and draft["currency_id"] > 0
    assert isinstance(draft.get("amount_total"), str) and draft["amount_total"]
    applied_preferences = [
        evidence.get("preferences")
        for evidence in case_detail["evidence"]
        if evidence.get("preferences") is not None
    ]
    assert applied_preferences
    for preferences in applied_preferences:
        assert preferences["scope"] in {"company", "category", "product"}
        assert preferences["revision"] > 0
        assert sorted(preferences["ordered_criteria"]) == [
            "delivery",
            "price",
            "reliability",
        ]
        assert preferences["enforcement_mode"] in {"advisory", "hard"}
    scan_id = str(completed["scan_id"])

    dynamodb = _aws_client("dynamodb")
    scan_item = dynamodb.get_item(
        TableName=f"weam-stockai-{environment}-application",
        Key={
            "PK": {"S": f"ENV#{environment}"},
            "SK": {"S": f"SCAN#{scan_id}"},
        },
        ConsistentRead=True,
    ).get("Item")
    assert scan_item is not None
    assert scan_item["status"]["S"] == "succeeded"
    assert scan_item["scan_id"]["S"] == scan_id
    case_item = dynamodb.get_item(
        TableName=f"weam-stockai-{environment}-application",
        Key={
            "PK": {"S": f"ENV#{environment}"},
            "SK": {"S": f"CASE#{case_id}"},
        },
        ConsistentRead=True,
    ).get("Item")
    assert case_item is not None
    assert case_item["status"]["S"] == "pending_approval"
    assert case_item["case_id"]["S"] == case_id
    stored_draft = case_item["draft"]["M"]
    assert int(stored_draft["po_id"]["N"]) == draft["po_id"]
    assert stored_draft["write_date"]["S"] == draft["write_date"]
    assert stored_draft["state"]["S"] == draft["state"]
    assert int(stored_draft["partner_id"]["N"]) == draft["partner_id"]
    assert int(stored_draft["currency_id"]["N"]) == draft["currency_id"]
    assert stored_draft["amount_total"]["S"] == draft["amount_total"]
    audit_items = dynamodb.query(
        TableName=f"weam-stockai-{environment}-application",
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={
            ":pk": {"S": f"ENV#{environment}"},
            ":prefix": {"S": f"AUDIT#{case_id}#"},
        },
        ConsistentRead=True,
    )["Items"]
    assert {entry["outcome"]["S"] for entry in audit_items} == {
        "queued",
        "running",
        "succeeded",
        "creating_draft",
        "pending_approval",
    }
    assert any("preferences" in entry for entry in audit_items)
    checkpoints = dynamodb.query(
        TableName=f"weam-stockai-{environment}-checkpoints",
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": f"CHECKPOINT_{case_id}"}},
        ConsistentRead=True,
    )["Items"]
    assert checkpoints

    with httpx.Client(
        base_url=f"https://grafana.{environment}.stockai.fursa.click",
        auth=("admin", grafana_password),
        timeout=30,
    ) as grafana:
        health = grafana.get("/api/health")
        health.raise_for_status()
        _wait_for_metric_deltas(grafana, metric_baselines)
        logs = _wait_for_logs(
            grafana,
            environment=environment,
            scan_id=scan_id,
            started_at=started_at,
        )
    serialized_logs = json.dumps(logs, sort_keys=True)
    assert scan_id in serialized_logs
    for secret in (session_token, csrf_token, grafana_password):
        assert secret not in serialized_logs
    for unsafe in ("Authorization", "stockai_session", "stockai_csrf"):
        assert unsafe not in serialized_logs

    objects = _aws_client("s3").list_objects_v2(
        Bucket="weam-stockai-loki-228281126655-us-east-1",
        Prefix=f"{environment}/",
        MaxKeys=1,
    )
    assert objects.get("KeyCount", 0) > 0

    completed_at = datetime.now(UTC)
    evidence = {
        "schemaVersion": 1,
        "releaseId": release_id,
        "images": expected_images,
        "argoRevision": argo_revision,
        "smokeRunId": smoke_run_id,
        "correlationId": scan_id,
        "startedAt": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "completedAt": completed_at.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "result": "passed",
        "checks": {
            "https": True,
            "cognitoSession": True,
            "frontendPolling": True,
            "fastApiLangGraphBedrock": True,
            "mcpOdooRead": True,
            "mcpOdooDraftCreate": True,
            "dynamoDbPersistence": True,
            "prometheusMetrics": True,
            "sanitizedLokiLogs": True,
            "lokiS3Objects": True,
        },
    }
    output = Path(_required("STOCKAI_SMOKE_EVIDENCE", environment=environment))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@pytest.mark.skipif(
    os.environ.get("STOCKAI_RUN_DEV_SMOKE") != "1",
    reason="requires explicit live dev authorization and session input",
)
def test_exact_dev_walking_skeleton() -> None:
    run_exact_walking_skeleton("dev")
