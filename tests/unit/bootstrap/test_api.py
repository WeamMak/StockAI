"""Task 12 contracts for selecting the API's structured LLM implementation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, cast

import anyio
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from httpx2 import ASGITransport, AsyncClient
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

_FICTIONAL_MCP_TOKEN = "fictional-bootstrap-mcp-token-at-least-32-characters"
os.environ.setdefault("PROCUREMENT_MCP_TOKEN", _FICTIONAL_MCP_TOKEN)

from tests.support.local_identity import (  # noqa: E402
    LocalIdentityProvider,
    sign_in,
)

from procurement.adapters.aws.bedrock import (  # noqa: E402
    APPROVED_MODEL_ID,
    BedrockRuntimeClient,
)
from procurement.adapters.aws.checkpointer import (  # noqa: E402
    DynamoCheckpointSettings,
)
from procurement.adapters.aws.dynamodb import DynamoClient  # noqa: E402
from procurement.agent.recommendation_schema import (  # noqa: E402
    load_procurement_system_prompt,
)
from procurement.api.auth.cognito import CognitoSettings, IdentityProvider  # noqa: E402
from procurement.api.config import ApiSettings  # noqa: E402
from procurement.bootstrap.api import (  # noqa: E402
    AuthenticationMode,
    LlmMode,
    LocalApiSettings,
    PersistenceMode,
    StreamableHttpProcurementMcp,
    create_local_api_app,
)
from procurement.bootstrap.mcp import LocalFictionalErp  # noqa: E402
from procurement.domain.identifiers import Environment  # noqa: E402
from procurement.domain.policy.evidence import ProcurementEvidence  # noqa: E402
from procurement.domain.policy.preferences import ProcurementPreference  # noqa: E402
from procurement.ports.erp import (  # noqa: E402
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
)
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
    assert limit == 50
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


async def _procurement_evidence(
    _self: StreamableHttpProcurementMcp,
    *,
    environment: Environment,
    product_id: str,
    horizon_days: int,
) -> ProcurementEvidence:
    return await LocalFictionalErp(mode="success").get_procurement_evidence(
        ProcurementEvidenceQuery(
            environment=environment,
            product_id=product_id,
            horizon_days=horizon_days,
        )
    )


async def _procurement_preferences(
    _self: StreamableHttpProcurementMcp,
    *,
    environment: Environment,
    company_id: str,
    category_id: str,
    product_id: str,
) -> ProcurementPreference:
    return await LocalFictionalErp(mode="success").get_procurement_preferences(
        ProcurementPreferenceQuery(
            environment=environment,
            company_id=company_id,
            category_id=category_id,
            product_id=product_id,
        )
    )


def _patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        StreamableHttpProcurementMcp,
        "list_replenishment_candidates",
        _candidate_page,
    )
    monkeypatch.setattr(
        StreamableHttpProcurementMcp,
        "get_procurement_evidence",
        _procurement_evidence,
    )
    monkeypatch.setattr(
        StreamableHttpProcurementMcp,
        "get_procurement_preferences",
        _procurement_preferences,
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


def test_api_settings_select_only_explicit_memory_or_dynamodb_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_MCP_TOKEN", _FICTIONAL_MCP_TOKEN)
    monkeypatch.delenv("PROCUREMENT_PERSISTENCE_MODE", raising=False)

    default = LocalApiSettings.from_environment()

    assert default.persistence_mode is PersistenceMode.MEMORY

    monkeypatch.setenv("PROCUREMENT_PERSISTENCE_MODE", "dynamodb")
    configured = LocalApiSettings.from_environment()

    assert configured.persistence_mode is PersistenceMode.DYNAMODB
    assert configured.dynamodb_application_table == "stockai-dev-application"
    assert configured.dynamodb_checkpoint_table == "stockai-dev-checkpoints"

    monkeypatch.setenv("PROCUREMENT_PERSISTENCE_MODE", "unsupported")
    with pytest.raises(
        ValueError,
        match="PROCUREMENT_PERSISTENCE_MODE must be memory or dynamodb",
    ):
        LocalApiSettings.from_environment()


def test_authentication_mode_has_no_runtime_local_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_MCP_TOKEN", _FICTIONAL_MCP_TOKEN)
    monkeypatch.delenv("PROCUREMENT_AUTHENTICATION_MODE", raising=False)

    assert (
        LocalApiSettings.from_environment().authentication_mode
        is AuthenticationMode.DISABLED
    )

    monkeypatch.setenv("PROCUREMENT_AUTHENTICATION_MODE", "local")
    with pytest.raises(
        ValueError,
        match="PROCUREMENT_AUTHENTICATION_MODE must be disabled or cognito",
    ):
        LocalApiSettings.from_environment()


def test_cognito_mode_requires_dynamodb_backed_sessions() -> None:
    with pytest.raises(
        ValueError,
        match="Cognito authentication requires DynamoDB persistence",
    ):
        replace(
            _settings(LlmMode.LOCAL),
            authentication_mode=AuthenticationMode.COGNITO,
            cognito_domain_url=("https://stockai-dev.auth.us-east-1.amazoncognito.com"),
            cognito_user_pool_id="us-east-1_fictional",
            cognito_client_id="fictional-client-id",
            cognito_redirect_uri=("https://dev.stockai.example.invalid/auth/callback"),
        )


def test_memory_persistence_never_constructs_a_dynamodb_dependency() -> None:
    def unexpected_client(**_kwargs: object) -> DynamoClient:
        raise AssertionError("memory mode attempted to construct DynamoDB")

    def unexpected_checkpointer(
        _settings: DynamoCheckpointSettings,
    ) -> BaseCheckpointSaver[Any]:
        raise AssertionError("memory mode attempted to construct a DynamoDB saver")

    application = create_local_api_app(
        _settings(LlmMode.LOCAL),
        dynamodb_client_factory=unexpected_client,
        checkpoint_factory=unexpected_checkpointer,
    )

    assert application.state.scan_service is not None


def test_dynamodb_persistence_and_cognito_are_both_runtime_reachable() -> None:
    settings = replace(
        _settings(LlmMode.LOCAL),
        persistence_mode=PersistenceMode.DYNAMODB,
        authentication_mode=AuthenticationMode.COGNITO,
        aws_region="us-east-1",
        dynamodb_endpoint_url="http://dynamodb-local:8000",
        dynamodb_application_table="stockai-dev-application",
        dynamodb_checkpoint_table="stockai-dev-checkpoints",
        cognito_domain_url="https://stockai-dev.auth.us-east-1.amazoncognito.com",
        cognito_user_pool_id="us-east-1_fictional",
        cognito_client_id="fictional-client-id",
        cognito_redirect_uri="https://dev.stockai.example.invalid/auth/callback",
    )
    client_calls: list[dict[str, object]] = []
    checkpoint_calls: list[DynamoCheckpointSettings] = []
    identity_calls: list[CognitoSettings] = []

    class UnusedDynamoClient:
        """Construction-only fake; no repository operation belongs in this test."""

        def transact_write_items(self, **_request: Any) -> Mapping[str, Any]:
            raise AssertionError("unexpected repository operation")

        def get_item(self, **_request: Any) -> Mapping[str, Any]:
            raise AssertionError("unexpected repository operation")

        def update_item(self, **_request: Any) -> Mapping[str, Any]:
            raise AssertionError("unexpected repository operation")

        def put_item(self, **_request: Any) -> Mapping[str, Any]:
            raise AssertionError("unexpected repository operation")

        def query(self, **_request: Any) -> Mapping[str, Any]:
            raise AssertionError("unexpected repository operation")

        def delete_item(self, **_request: Any) -> Mapping[str, Any]:
            raise AssertionError("unexpected repository operation")

    def client_factory(**kwargs: object) -> DynamoClient:
        client_calls.append(kwargs)
        return UnusedDynamoClient()

    def checkpoint_factory(
        checkpoint_settings: DynamoCheckpointSettings,
    ) -> InMemorySaver:
        checkpoint_calls.append(checkpoint_settings)
        return InMemorySaver()

    def identity_factory(settings: CognitoSettings) -> IdentityProvider:
        identity_calls.append(settings)
        return LocalIdentityProvider()

    application = create_local_api_app(
        settings,
        dynamodb_client_factory=client_factory,
        checkpoint_factory=checkpoint_factory,
        identity_provider_factory=identity_factory,
    )

    assert application.state.scan_service is not None
    assert client_calls == [
        {
            "region_name": "us-east-1",
            "endpoint_url": "http://dynamodb-local:8000",
        }
    ]
    assert len(checkpoint_calls) == 1
    checkpoint_settings = checkpoint_calls[0]
    assert checkpoint_settings.table_name == "stockai-dev-checkpoints"
    assert checkpoint_settings.endpoint_url == "http://dynamodb-local:8000"
    assert len(identity_calls) == 1
    assert identity_calls[0].user_pool_id == "us-east-1_fictional"
    assert identity_calls[0].redirect_uri == (
        "https://dev.stockai.example.invalid/auth/callback"
    )


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
    _patch_mcp(monkeypatch)
    application = create_local_api_app(
        _settings(LlmMode.BEDROCK),
        bedrock_client_factory=lambda: provider,
        identity_provider_override=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport, base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        finished = await _finished_scan(client, accepted.json()["scan_id"])
        metrics = await client.get("/metrics")

    assert accepted.status_code == 202
    assert finished["status"] == "succeeded"
    result = cast(dict[str, object], finished["result"])
    assert result["product_id"] == "product-101"
    assert result["read_only"] is True
    evidence = cast(list[dict[str, object]], finished["evidence"])
    preferences = cast(dict[str, object], evidence[0]["preferences"])
    assert preferences["scope"] == "company"
    assert preferences["revision"] == 1
    assert preferences["ordered_criteria"] == [
        "reliability",
        "delivery",
        "price",
    ]
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
    _patch_mcp(monkeypatch)
    application = create_local_api_app(
        _settings(LlmMode.BEDROCK),
        bedrock_client_factory=lambda: provider,
        identity_provider_override=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport, base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
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
    _patch_mcp(monkeypatch)
    application = create_local_api_app(
        _settings(LlmMode.BEDROCK),
        bedrock_client_factory=lambda: provider,
        identity_provider_override=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport, base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
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
