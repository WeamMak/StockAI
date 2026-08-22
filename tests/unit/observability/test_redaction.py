"""Structured logging and sensitive-data redaction behavior."""

import json
from datetime import datetime
from io import StringIO

import pytest
from httpx2 import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry, generate_latest

from procurement.api.app import create_app
from procurement.api.config import ApiSettings
from procurement.domain.identifiers import Environment
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.observability.logging import (
    REDACTED,
    configure_json_logging,
    log_event,
    sanitize_log_fields,
)
from procurement.observability.metrics import create_agent_metrics


def _sample_value(
    registry: CollectorRegistry,
    name: str,
    labels: dict[str, str] | None = None,
) -> float | None:
    return registry.get_sample_value(name, labels or {})


def test_sensitive_log_fields_are_recursively_redacted() -> None:
    raw_fields = {
        "request_id": "request-20260802-0004",
        "status": "failed",
        "authorization": "Bearer secret-token",
        "system_prompt": "hidden system instructions",
        "model_output": "untrusted model response",
        "vendor_price": "199.99",
        "budget": "5000.00",
        "manager_note": "confidential manager note",
        "upstream_error": RuntimeError("database password leaked"),
        "nested": {"api_key": "odoo-secret-key"},
    }

    sanitized = sanitize_log_fields(raw_fields)

    assert sanitized == {
        "request_id": "request-20260802-0004",
        "status": "failed",
        "authorization": REDACTED,
        "system_prompt": REDACTED,
        "model_output": REDACTED,
        "vendor_price": REDACTED,
        "budget": REDACTED,
        "manager_note": REDACTED,
        "upstream_error": REDACTED,
        "nested": {"api_key": REDACTED},
    }
    serialized = json.dumps(sanitized)
    assert "secret-token" not in serialized
    assert "hidden system instructions" not in serialized
    assert "untrusted model response" not in serialized
    assert "199.99" not in serialized
    assert "5000.00" not in serialized
    assert "confidential manager note" not in serialized
    assert "database password leaked" not in serialized
    assert "odoo-secret-key" not in serialized


def test_manager_and_purchase_order_metrics_use_only_bounded_labels() -> None:
    agent = create_agent_metrics()
    agent.observe_manager_decision(
        decision="approve", result="accepted", duration_seconds=0.25
    )
    mcp = create_mcp_metrics()
    mcp.observe_purchase_order_action(
        action="confirm",
        result="reconciliation_required",
        duration_seconds=0.5,
    )

    agent_text = generate_latest(agent.registry).decode()
    mcp_text = generate_latest(mcp.registry).decode()
    assert (
        'procurement_manager_decisions_total{decision="approve",result="accepted"}'
        in agent_text
    )
    assert (
        'procurement_decision_completion_seconds_count{decision="approve"}'
        in agent_text
    )
    assert (
        'procurement_purchase_order_actions_total{action="confirm",result="reconciliation_required"}'
        in mcp_text
    )
    assert (
        'procurement_purchase_order_reconciliation_seconds_count{action="confirm"}'
        in mcp_text
    )
    for forbidden in (
        "case_id",
        "decision_id",
        "manager_id",
        "vendor_id",
        "amount",
        "evidence_digest",
        "reason",
        "justification",
    ):
        assert f'{forbidden}="' not in agent_text + mcp_text


def test_draft_submission_metrics_bound_results_and_successful_latency() -> None:
    registry = CollectorRegistry(auto_describe=True)
    metrics = create_agent_metrics(registry)

    metrics.observe_draft_submission(result="accepted", duration_seconds=0.2)
    metrics.observe_draft_submission(result="replay", duration_seconds=0.1)
    metrics.observe_draft_submission(result="conflict", duration_seconds=0.3)
    metrics.observe_draft_submission(result="untrusted-value", duration_seconds=0.4)

    assert _sample_value(
        registry,
        "procurement_draft_submissions_total",
        {"result": "accepted"},
    ) == pytest.approx(1)
    assert _sample_value(
        registry,
        "procurement_draft_submissions_total",
        {"result": "replay"},
    ) == pytest.approx(1)
    assert _sample_value(
        registry,
        "procurement_draft_submissions_total",
        {"result": "conflict"},
    ) == pytest.approx(1)
    assert _sample_value(
        registry,
        "procurement_draft_submissions_total",
        {"result": "error"},
    ) == pytest.approx(1)
    assert _sample_value(
        registry,
        "procurement_draft_submission_seconds_count",
    ) == pytest.approx(2)

    metric_text = generate_latest(registry).decode()
    for forbidden in (
        "case_id",
        "actor_subject",
        "environment",
        "vendor_id",
        "reason",
    ):
        assert f'{forbidden}="' not in metric_text


def test_log_event_emits_the_required_json_fields() -> None:
    stream = StringIO()
    logger = configure_json_logging(
        service="procurement-api",
        environment="dev",
        stream=stream,
        logger_name="procurement.test.json",
    )

    log_event(
        logger,
        "request_completed",
        request_id="request-20260802-0005",
        duration_ms=12.5,
        status="success",
    )

    payload = json.loads(stream.getvalue())
    assert datetime.fromisoformat(payload.pop("timestamp").replace("Z", "+00:00"))
    assert payload == {
        "level": "INFO",
        "service": "procurement-api",
        "environment": "dev",
        "event": "request_completed",
        "request_id": "request-20260802-0005",
        "duration_ms": 12.5,
        "status": "success",
    }


def test_log_event_cannot_override_reserved_metadata() -> None:
    stream = StringIO()
    logger = configure_json_logging(
        service="procurement-api",
        environment="dev",
        stream=stream,
        logger_name="procurement.test.reserved",
    )

    log_event(
        logger,
        "request_completed",
        service="spoofed-service",
        environment="prod",
        timestamp="spoofed-time",
    )

    payload = json.loads(stream.getvalue())
    assert payload["service"] == "procurement-api"
    assert payload["environment"] == "dev"
    assert payload["timestamp"] != "spoofed-time"


@pytest.mark.anyio
async def test_api_request_emits_one_correlated_json_log() -> None:
    stream = StringIO()
    logger = configure_json_logging(
        service="procurement-api",
        environment="prod",
        stream=stream,
        logger_name="procurement.test.request",
    )
    application = create_app(
        settings=ApiSettings(environment=Environment.PROD),
        logger=logger,
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/health/live",
            headers={"X-Request-ID": "request-20260802-0006"},
        )

    assert response.status_code == 200
    payload = json.loads(stream.getvalue())
    assert datetime.fromisoformat(payload.pop("timestamp").replace("Z", "+00:00"))
    duration_ms = payload.pop("duration_ms")
    assert isinstance(duration_ms, float)
    assert duration_ms >= 0
    assert payload == {
        "level": "INFO",
        "service": "procurement-api",
        "environment": "prod",
        "event": "http_request_completed",
        "request_id": "request-20260802-0006",
        "method": "GET",
        "route": "/health/live",
        "http_status": 200,
        "status": "success",
    }
