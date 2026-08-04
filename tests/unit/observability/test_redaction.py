"""Structured logging and sensitive-data redaction behavior."""

import json
from datetime import datetime
from io import StringIO

import pytest
from httpx2 import ASGITransport, AsyncClient

from procurement.api.app import create_app
from procurement.api.config import ApiSettings
from procurement.domain.identifiers import Environment
from procurement.observability.logging import (
    REDACTED,
    configure_json_logging,
    log_event,
    sanitize_log_fields,
)


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
