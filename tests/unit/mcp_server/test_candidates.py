"""Unit behavior for the first read-only Procurement MCP tool."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from prometheus_client import generate_latest
from pydantic import ValidationError
from tests.support.fake_odoo.adapter import FakeOdooAdapter

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.auth import (
    READ_SCOPE,
    StaticBearerTokenVerifier,
    create_auth_settings,
    validate_bearer_token,
)
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.schemas import ListReplenishmentCandidatesInput
from procurement.mcp_server.server import create_mcp_server
from procurement.mcp_server.tools.candidates import (
    SafeMcpToolError,
    list_replenishment_candidates,
)
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    CandidatePage,
    ErpUnavailableError,
    ReplenishmentCandidateRecord,
    ReplenishmentCandidatesQuery,
)


def _candidate(
    *, product_name: str = "Fictional Safety Gloves"
) -> ReplenishmentCandidateRecord:
    return ReplenishmentCandidateRecord(
        product_id="product-101",
        product_name=product_name,
        category_id="category-safety",
        reorder_minimum=Decimal("10.000000"),
        reorder_maximum=Decimal("40.000000"),
        projected_quantity=Decimal("8.000000"),
        projected_trigger_date=date(2026, 8, 8),
        skip_reason_code=None,
    )


def _request(**overrides: object) -> ListReplenishmentCandidatesInput:
    values: dict[str, object] = {
        "environment": "dev",
        "horizon_days": 14,
        "limit": 25,
        "cursor": None,
    }
    values.update(overrides)
    return ListReplenishmentCandidatesInput.model_validate(values)


@pytest.mark.anyio
async def test_list_candidates_returns_a_bounded_typed_page() -> None:
    adapter = FakeOdooAdapter(
        page=CandidatePage(items=(_candidate(),), next_cursor="page-002")
    )
    metrics = create_mcp_metrics()
    stream = StringIO()
    logger = configure_json_logging(
        service="procurement-mcp",
        environment="dev",
        stream=stream,
        logger_name="procurement.test.mcp.success",
    )

    response = await list_replenishment_candidates(
        request=_request(),
        erp=adapter,
        server_environment=Environment.DEV,
        metrics=metrics,
        logger=logger,
        max_retries=2,
        retry_delay_seconds=0,
    )

    assert adapter.queries == [
        ReplenishmentCandidatesQuery(
            horizon_days=14,
            limit=25,
            cursor=None,
        )
    ]
    assert response.environment == "dev"
    assert response.next_cursor == "page-002"
    assert len(response.candidates) == 1
    assert response.candidates[0].product_id == "product-101"
    assert response.candidates[0].reorder_maximum == Decimal("40.000000")
    assert response.candidates[0].skip_metadata is None

    metric_text = generate_latest(metrics.registry).decode()
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in metric_text
    assert (
        "procurement_mcp_tool_duration_seconds_count{"
        'tool="list_replenishment_candidates"} 1.0'
    ) in metric_text

    log_payload = json.loads(stream.getvalue())
    assert log_payload["event"] == "mcp_tool_completed"
    assert log_payload["tool_name"] == "list_replenishment_candidates"
    assert log_payload["status"] == "success"
    assert log_payload["retry_count"] == 0
    assert isinstance(log_payload["duration_ms"], float)
    assert "product_name" not in log_payload


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"environment": "qa"},
        {"horizon_days": 0},
        {"horizon_days": 91},
        {"horizon_days": "14"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"cursor": "x" * 257},
        {"cursor": "unsafe cursor"},
        {"unexpected": "field"},
    ],
)
def test_candidate_input_is_strict_and_bounded(
    invalid_values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _request(**invalid_values)


@pytest.mark.anyio
async def test_invalid_erp_output_maps_to_a_safe_error() -> None:
    unsafe_value = "private-price-and-upstream-details-" + ("x" * 220)
    adapter = FakeOdooAdapter(
        page=CandidatePage(
            items=(_candidate(product_name=unsafe_value),),
            next_cursor=None,
        )
    )
    stream = StringIO()

    with pytest.raises(SafeMcpToolError) as raised:
        await list_replenishment_candidates(
            request=_request(),
            erp=adapter,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                stream=stream,
                logger_name="procurement.test.mcp.invalid-output",
            ),
            max_retries=0,
            retry_delay_seconds=0,
        )

    assert raised.value.error_code is ErrorCode.ODOO_UNAVAILABLE
    assert raised.value.retryable is True
    assert unsafe_value not in str(raised.value)
    assert unsafe_value not in stream.getvalue()


@pytest.mark.anyio
async def test_timeout_retries_twice_then_maps_and_observes_a_safe_error() -> None:
    adapter = FakeOdooAdapter(
        page=CandidatePage(items=(_candidate(),), next_cursor=None),
        failures=(
            TimeoutError("upstream-secret-one"),
            TimeoutError("upstream-secret-two"),
            TimeoutError("upstream-secret-three"),
        ),
    )
    metrics = create_mcp_metrics()
    stream = StringIO()

    with pytest.raises(SafeMcpToolError) as raised:
        await list_replenishment_candidates(
            request=_request(),
            erp=adapter,
            server_environment=Environment.DEV,
            metrics=metrics,
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                stream=stream,
                logger_name="procurement.test.mcp.timeout",
            ),
            read_timeout_seconds=0.1,
            max_retries=2,
            retry_delay_seconds=0,
        )

    assert raised.value.error_code is ErrorCode.MCP_TIMEOUT
    assert raised.value.retryable is True
    assert len(adapter.queries) == 3
    assert "upstream-secret" not in str(raised.value)
    assert "upstream-secret" not in stream.getvalue()

    metric_text = generate_latest(metrics.registry).decode()
    assert (
        'procurement_mcp_tool_calls_total{status="error",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in metric_text
    assert (
        'procurement_mcp_tool_failures_total{error_code="MCP_TIMEOUT",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in metric_text
    assert (
        'procurement_mcp_tool_timeouts_total{tool="list_replenishment_candidates"} 3.0'
    ) in metric_text
    assert (
        'procurement_mcp_tool_retries_total{tool="list_replenishment_candidates"} 2.0'
    ) in metric_text

    log_payload = json.loads(stream.getvalue())
    assert log_payload["status"] == "error"
    assert log_payload["error_code"] == "MCP_TIMEOUT"
    assert log_payload["retry_count"] == 2


@pytest.mark.anyio
async def test_transient_erp_unavailability_retries_without_timeout_metric() -> None:
    adapter = FakeOdooAdapter(
        page=CandidatePage(items=(_candidate(),), next_cursor=None),
        failures=(ErpUnavailableError("private-upstream-detail"),),
    )
    metrics = create_mcp_metrics()

    response = await list_replenishment_candidates(
        request=_request(),
        erp=adapter,
        server_environment=Environment.DEV,
        metrics=metrics,
        logger=configure_json_logging(
            service="procurement-mcp",
            environment="dev",
            logger_name="procurement.test.mcp.unavailable",
        ),
        max_retries=2,
        retry_delay_seconds=0,
    )

    assert response.candidates[0].product_id == "product-101"
    assert len(adapter.queries) == 2
    metric_text = generate_latest(metrics.registry).decode()
    assert (
        'procurement_mcp_tool_retries_total{tool="list_replenishment_candidates"} 1.0'
    ) in metric_text
    assert 'procurement_mcp_tool_timeouts_total{tool="' not in metric_text


@pytest.mark.anyio
async def test_adapter_owned_retries_are_reported_without_a_second_retry_loop() -> None:
    adapter = FakeOdooAdapter(
        page=CandidatePage(items=(_candidate(),), next_cursor=None),
        failures=(ErpUnavailableError(retry_count=2),),
    )

    with pytest.raises(SafeMcpToolError) as raised:
        await list_replenishment_candidates(
            request=_request(),
            erp=adapter,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.mcp.adapter-retries",
            ),
            max_retries=0,
            retry_delay_seconds=0,
        )

    assert len(adapter.queries) == 1
    assert raised.value.error_code is ErrorCode.ODOO_UNAVAILABLE
    assert raised.value.retry_count == 2


@pytest.mark.anyio
async def test_request_environment_must_match_the_server_environment() -> None:
    adapter = FakeOdooAdapter(
        page=CandidatePage(items=(_candidate(),), next_cursor=None)
    )

    with pytest.raises(SafeMcpToolError) as raised:
        await list_replenishment_candidates(
            request=_request(environment="prod"),
            erp=adapter,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.mcp.environment",
            ),
            max_retries=0,
            retry_delay_seconds=0,
        )

    assert raised.value.error_code is ErrorCode.FORBIDDEN
    assert raised.value.retryable is False
    assert adapter.queries == []


@pytest.mark.anyio
async def test_static_bearer_verifier_grants_only_the_read_scope() -> None:
    token = "fictional-dev-mcp-token-at-least-32-characters"
    verifier = StaticBearerTokenVerifier(token)

    access = await verifier.verify_token(token)

    assert access is not None
    assert access.client_id == "stockai-agent"
    assert access.scopes == [READ_SCOPE]
    assert await verifier.verify_token("wrong-token") is None
    assert await verifier.verify_token("é" * 40) is None


@pytest.mark.parametrize(
    "invalid_token",
    ["short", "contains unsafe spaces " + ("x" * 32), "é" * 32],
)
def test_configured_bearer_token_is_strict_and_never_normalized(
    invalid_token: str,
) -> None:
    with pytest.raises(ValueError) as raised:
        validate_bearer_token(invalid_token)

    assert invalid_token not in str(raised.value)


@pytest.mark.anyio
async def test_server_discovery_publishes_strict_read_only_contracts() -> None:
    server = create_mcp_server(
        erp=FakeOdooAdapter(
            page=CandidatePage(items=(_candidate(),), next_cursor=None)
        ),
        environment=Environment.DEV,
        bearer_token="fictional-dev-mcp-token-at-least-32-characters",
        retry_delay_seconds=0,
    )

    tools = await server.list_tools()
    settings = create_auth_settings()

    read_only_tool_names = {
        "list_replenishment_candidates",
        "get_procurement_evidence",
        "get_procurement_preferences",
    }
    assert {tool.name for tool in tools} == read_only_tool_names | {
        "create_purchase_order_draft",
        "confirm_purchase_order",
        "cancel_draft_purchase_order",
    }
    for tool in tools:
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is (
            tool.name == "cancel_draft_purchase_order"
        )
        is_read_only = tool.name in read_only_tool_names
        assert tool.annotations.readOnlyHint is is_read_only
    assert settings.required_scopes == [READ_SCOPE]
