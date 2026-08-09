"""Task 12 contracts for selecting the API's structured LLM implementation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, cast

import anyio
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from httpx2 import ASGITransport, AsyncClient

_FICTIONAL_MCP_TOKEN = "fictional-bootstrap-mcp-token-at-least-32-characters"
os.environ.setdefault("PROCUREMENT_MCP_TOKEN", _FICTIONAL_MCP_TOKEN)

from procurement.adapters.aws.bedrock import (  # noqa: E402
    APPROVED_MODEL_ID,
    BedrockRuntimeClient,
)
from procurement.agent.recommendation_schema import (  # noqa: E402
    load_procurement_system_prompt,
)
from procurement.api.config import ApiSettings  # noqa: E402
from procurement.bootstrap.api import (  # noqa: E402
    LlmMode,
    LocalApiSettings,
    StreamableHttpProcurementMcp,
    create_local_api_app,
)
from procurement.domain.identifiers import Environment  # noqa: E402
from procurement.ports.mcp import (  # noqa: E402
    CandidatePage,
    ReplenishmentCandidate,
)


class RecordingBedrockClient:
    """Return fixed Converse responses and retain only mocked provider requests."""

    def __init__(self, *responses: object) -> None:
        self._responses = iter(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return cast(dict[str, Any], response)


def _settings(mode: LlmMode) -> LocalApiSettings:
    return LocalApiSettings(
        api=ApiSettings(environment=Environment.DEV),
        mcp_url="http://mcp.example.invalid/mcp",
        mcp_token=_FICTIONAL_MCP_TOKEN,
        llm_mode=mode,
    )


def _bedrock_response(payload: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "output": {
            "message": {
                "content": [
                    {"text": json.dumps(payload, separators=(",", ":"))},
                ]
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 83, "outputTokens": 27, "totalTokens": 110},
    }


async def _candidate_page(
    _self: StreamableHttpProcurementMcp,
    *,
    environment: Environment,
    horizon_days: int,
    limit: int,
) -> CandidatePage:
    assert horizon_days == 14
    assert limit == 25
    return CandidatePage(
        environment=environment,
        candidates=(
            ReplenishmentCandidate(
                product_id="product-101",
                product_name="Fictional Safety Gloves",
                category_id="category-safety",
                reorder_minimum=Decimal("10.000000"),
                reorder_maximum=Decimal("40.000000"),
                projected_quantity=Decimal("8.000000"),
                projected_trigger_date=date(2026, 8, 9),
                skip_reason_code=None,
            ),
        ),
        next_cursor=None,
    )


async def _finished_scan(client: AsyncClient, scan_id: str) -> dict[str, object]:
    for _ in range(50):
        response = await client.get(f"/api/v1/scans/{scan_id}")
        body = cast(dict[str, object], response.json())
        if body["status"] not in {"queued", "running"}:
            return body
        await anyio.sleep(0.01)
    raise AssertionError("Bedrock-backed scan did not finish")


def test_api_settings_select_only_explicit_local_or_bedrock_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_MCP_TOKEN", _FICTIONAL_MCP_TOKEN)
    monkeypatch.delenv("PROCUREMENT_LLM_MODE", raising=False)

    default = LocalApiSettings.from_environment()

    assert default.llm_mode is LlmMode.LOCAL

    monkeypatch.setenv("PROCUREMENT_LLM_MODE", "bedrock")

    configured = LocalApiSettings.from_environment()

    assert configured.llm_mode is LlmMode.BEDROCK

    monkeypatch.setenv("PROCUREMENT_LLM_MODE", "unsupported")
    with pytest.raises(
        ValueError,
        match="PROCUREMENT_LLM_MODE must be local or bedrock",
    ):
        LocalApiSettings.from_environment()


def test_local_mode_never_constructs_an_aws_client() -> None:
    def unexpected_bedrock_client() -> BedrockRuntimeClient:
        raise AssertionError("local mode attempted to construct a Bedrock client")

    application = create_local_api_app(
        _settings(LlmMode.LOCAL),
        bedrock_client_factory=unexpected_bedrock_client,
    )

    assert application.state.scan_service is not None


@pytest.mark.anyio
async def test_bedrock_mode_runs_api_graph_schema_and_metrics_with_mocked_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingBedrockClient(
        _bedrock_response(
            {
                "decision": "recommend",
                "product_id": "product-101",
                "rationale": "Stock is below the configured reorder minimum.",
                "risk_flags": ["LIMITED_WALKING_SKELETON_EVIDENCE"],
                "budget_acknowledgement": "not_evaluated",
            }
        )
    )
    monkeypatch.setattr(
        StreamableHttpProcurementMcp,
        "list_replenishment_candidates",
        _candidate_page,
    )
    application = create_local_api_app(
        _settings(LlmMode.BEDROCK),
        bedrock_client_factory=lambda: provider,
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        accepted = await client.post("/api/v1/scans")
        finished = await _finished_scan(client, accepted.json()["scan_id"])
        metrics = await client.get("/metrics")

    assert accepted.status_code == 202
    assert finished["status"] == "succeeded"
    result = cast(dict[str, object], finished["result"])
    assert result["product_id"] == "product-101"
    assert result["read_only"] is True
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request["modelId"] == APPROVED_MODEL_ID
    assert request["system"] == [{"text": load_procurement_system_prompt()}]
    schema = request["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
    assert '"additionalProperties":false' in schema
    assert 'procurement_llm_calls_total{status="success"} 1.0' in metrics.text
    assert 'procurement_llm_tokens_total{direction="input"} 83.0' in metrics.text
    assert 'procurement_llm_tokens_total{direction="output"} 27.0' in metrics.text


@pytest.mark.anyio
async def test_bedrock_invalid_output_becomes_safe_observable_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingBedrockClient(
        _bedrock_response({"unsafe": "private malformed output"}),
        _bedrock_response({"unsafe": "private malformed output"}),
    )
    monkeypatch.setattr(
        StreamableHttpProcurementMcp,
        "list_replenishment_candidates",
        _candidate_page,
    )
    application = create_local_api_app(
        _settings(LlmMode.BEDROCK),
        bedrock_client_factory=lambda: provider,
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        accepted = await client.post("/api/v1/scans")
        finished = await _finished_scan(client, accepted.json()["scan_id"])
        metrics = await client.get("/metrics")

    assert finished["status"] == "failed"
    failure = cast(dict[str, object], finished["error"])
    assert failure == {
        "error_code": "LLM_OUTPUT_INVALID",
        "message": "The recommendation model returned an invalid result.",
        "retryable": False,
        "retry_count": 0,
    }
    assert "private malformed output" not in json.dumps(finished)
    assert len(provider.requests) == 2
    assert 'procurement_llm_calls_total{status="error"} 1.0' in metrics.text
    assert (
        'procurement_llm_failures_total{error_code="LLM_OUTPUT_INVALID"} 1.0'
        in metrics.text
    )


@pytest.mark.anyio
async def test_bedrock_unavailable_becomes_safe_retryable_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_detail = "private provider access detail"
    provider = RecordingBedrockClient(
        ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": private_detail}},
            "Converse",
        )
    )
    monkeypatch.setattr(
        StreamableHttpProcurementMcp,
        "list_replenishment_candidates",
        _candidate_page,
    )
    application = create_local_api_app(
        _settings(LlmMode.BEDROCK),
        bedrock_client_factory=lambda: provider,
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        accepted = await client.post("/api/v1/scans")
        finished = await _finished_scan(client, accepted.json()["scan_id"])
        metrics = await client.get("/metrics")

    assert finished["status"] == "failed"
    failure = cast(dict[str, object], finished["error"])
    assert failure == {
        "error_code": "LLM_UNAVAILABLE",
        "message": "The recommendation model is unavailable.",
        "retryable": True,
        "retry_count": 0,
    }
    assert private_detail not in json.dumps(finished)
    assert len(provider.requests) == 1
    assert 'procurement_llm_calls_total{status="error"} 1.0' in metrics.text
    assert (
        'procurement_llm_failures_total{error_code="LLM_UNAVAILABLE"} 1.0'
        in metrics.text
    )
