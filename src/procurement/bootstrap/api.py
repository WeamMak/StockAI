"""Local composition root for the runnable procurement API process."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast

import httpx
import uvicorn
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from procurement.adapters.aws.bedrock import (
    BedrockRuntimeClient,
    BedrockStructuredLlm,
    create_bedrock_runtime_client,
)
from procurement.adapters.aws.checkpointer import (
    DynamoCheckpointSettings,
    create_dynamodb_checkpointer,
)
from procurement.adapters.aws.dynamodb import (
    DynamoApplicationRepository,
    DynamoClient,
    create_dynamodb_client,
)
from procurement.agent.graph import build_walking_skeleton_graph
from procurement.agent.recommendation_schema import (
    RECOMMENDATION_JSON_SCHEMA,
    load_procurement_system_prompt,
    validate_recommendation_payload,
)
from procurement.api.app import create_app
from procurement.api.config import ApiSettings
from procurement.api.observability import create_http_metrics
from procurement.api.services.scans import ScanWorkflow
from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.observability.logging import configure_json_logging
from procurement.observability.metrics import create_agent_metrics
from procurement.ports.llm import (
    RecommendationDecision,
    RecommendationRequest,
    StructuredLlmPort,
    StructuredRecommendation,
)
from procurement.ports.mcp import (
    CandidatePage,
    McpTimeoutError,
    McpUnavailableError,
    ProcurementMcpPort,
    ReplenishmentCandidate,
)
from procurement.ports.repositories import (
    ApplicationRepository,
    InMemoryApplicationRepository,
)

_MCP_TOOL_NAME = "list_replenishment_candidates"
_MIN_TOKEN_LENGTH = 32
_MAX_TOKEN_LENGTH = 512


class LlmMode(StrEnum):
    """Explicit structured-LLM implementation selected by the API process."""

    LOCAL = "local"
    BEDROCK = "bedrock"


class PersistenceMode(StrEnum):
    """Explicit application and graph persistence implementation."""

    MEMORY = "memory"
    DYNAMODB = "dynamodb"


@dataclass(frozen=True, slots=True)
class LocalApiSettings:
    """Validated dependencies selected by the API composition root."""

    api: ApiSettings
    mcp_url: str
    mcp_token: str = field(repr=False)
    llm_mode: LlmMode = LlmMode.LOCAL
    persistence_mode: PersistenceMode = PersistenceMode.MEMORY
    aws_region: str = "us-east-1"
    dynamodb_application_table: str | None = None
    dynamodb_checkpoint_table: str | None = None
    dynamodb_endpoint_url: str | None = None
    mcp_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        parsed_url = httpx.URL(self.mcp_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.host is None:
            raise ValueError("PROCUREMENT_MCP_URL must be an absolute HTTP URL")
        if (
            not _MIN_TOKEN_LENGTH <= len(self.mcp_token) <= _MAX_TOKEN_LENGTH
            or not self.mcp_token.isascii()
            or any(character.isspace() for character in self.mcp_token)
        ):
            raise ValueError("PROCUREMENT_MCP_TOKEN must be a bounded opaque token")
        if not 0 < self.mcp_timeout_seconds <= 120:
            raise ValueError(
                "PROCUREMENT_MCP_CLIENT_TIMEOUT_SECONDS must be between 0 and 120"
            )
        if not isinstance(self.llm_mode, LlmMode):
            raise ValueError("PROCUREMENT_LLM_MODE must be local or bedrock")
        if not isinstance(self.persistence_mode, PersistenceMode):
            raise ValueError("PROCUREMENT_PERSISTENCE_MODE must be memory or dynamodb")
        if not self.aws_region.strip():
            raise ValueError("PROCUREMENT_AWS_REGION is required")
        if self.dynamodb_endpoint_url is not None:
            endpoint = httpx.URL(self.dynamodb_endpoint_url)
            if endpoint.scheme not in {"http", "https"} or endpoint.host is None:
                raise ValueError(
                    "PROCUREMENT_DYNAMODB_ENDPOINT_URL must be an absolute HTTP URL"
                )
        if self.persistence_mode is PersistenceMode.DYNAMODB and (
            not self.dynamodb_application_table or not self.dynamodb_checkpoint_table
        ):
            raise ValueError(
                "DynamoDB persistence requires application and checkpoint tables"
            )

    @classmethod
    def from_environment(cls) -> LocalApiSettings:
        """Load the local MCP client boundary from process environment."""

        mcp_token = os.environ.get("PROCUREMENT_MCP_TOKEN")
        if mcp_token is None:
            raise ValueError("PROCUREMENT_MCP_TOKEN is required")
        api = ApiSettings.from_environment()
        try:
            llm_mode = LlmMode(os.environ.get("PROCUREMENT_LLM_MODE", "local"))
        except ValueError as error:
            raise ValueError("PROCUREMENT_LLM_MODE must be local or bedrock") from error
        try:
            persistence_mode = PersistenceMode(
                os.environ.get("PROCUREMENT_PERSISTENCE_MODE", "memory")
            )
        except ValueError as error:
            raise ValueError(
                "PROCUREMENT_PERSISTENCE_MODE must be memory or dynamodb"
            ) from error
        environment_prefix = f"stockai-{api.environment.value}"
        return cls(
            api=api,
            mcp_url=os.environ.get(
                "PROCUREMENT_MCP_URL",
                "http://127.0.0.1:9000/mcp",
            ),
            mcp_token=mcp_token,
            llm_mode=llm_mode,
            persistence_mode=persistence_mode,
            aws_region=os.environ.get("PROCUREMENT_AWS_REGION", "us-east-1"),
            dynamodb_application_table=os.environ.get(
                "PROCUREMENT_DYNAMODB_APPLICATION_TABLE",
                f"{environment_prefix}-application",
            ),
            dynamodb_checkpoint_table=os.environ.get(
                "PROCUREMENT_DYNAMODB_CHECKPOINT_TABLE",
                f"{environment_prefix}-checkpoints",
            ),
            dynamodb_endpoint_url=os.environ.get("PROCUREMENT_DYNAMODB_ENDPOINT_URL"),
            mcp_timeout_seconds=float(
                os.environ.get("PROCUREMENT_MCP_CLIENT_TIMEOUT_SECONDS", "5")
            ),
        )


class LocalStructuredLlm(StructuredLlmPort):
    """Deterministic structured-LLM substitute for local and test runs."""

    async def recommend(
        self,
        request: RecommendationRequest,
    ) -> StructuredRecommendation:
        candidate = request.candidates[0]
        return StructuredRecommendation(
            decision=RecommendationDecision.RECOMMEND,
            product_id=candidate.product_id,
            rationale="Projected stock is below the configured reorder minimum.",
            risk_flags=("LIMITED_WALKING_SKELETON_EVIDENCE",),
            input_tokens=48,
            output_tokens=19,
        )


BedrockClientFactory = Callable[[], BedrockRuntimeClient]
DynamoClientFactory = Callable[..., DynamoClient]
CheckpointFactory = Callable[[DynamoCheckpointSettings], BaseCheckpointSaver[Any]]


def _structured_llm(
    settings: LocalApiSettings,
    *,
    bedrock_client_factory: BedrockClientFactory,
) -> StructuredLlmPort:
    """Construct exactly the structured-LLM implementation selected by settings."""

    if settings.llm_mode is LlmMode.LOCAL:
        return LocalStructuredLlm()
    return BedrockStructuredLlm(
        client=bedrock_client_factory(),
        system_prompt=load_procurement_system_prompt(),
        output_schema=RECOMMENDATION_JSON_SCHEMA,
        validator=validate_recommendation_payload,
    )


def _safe_error_payload(result: CallToolResult) -> Mapping[str, object] | None:
    decoder = json.JSONDecoder()
    for block in result.content:
        if not isinstance(block, TextContent):
            continue
        for index, character in enumerate(block.text):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(block.text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, Mapping):
                return payload
    return None


def _raise_mcp_error(result: CallToolResult) -> None:
    payload = _safe_error_payload(result)
    retry_count = payload.get("retry_count", 0) if payload is not None else 0
    if type(retry_count) is not int or not 0 <= retry_count <= 2:
        retry_count = 0
    if payload is not None and payload.get("error_code") == ErrorCode.MCP_TIMEOUT.value:
        raise McpTimeoutError(retry_count=retry_count)
    raise McpUnavailableError(retry_count=retry_count)


def _candidate(raw: object) -> ReplenishmentCandidate:
    if not isinstance(raw, Mapping) or set(raw) != {
        "product_id",
        "product_name",
        "category_id",
        "reorder_minimum",
        "reorder_maximum",
        "projected_quantity",
        "projected_trigger_date",
        "skip_metadata",
    }:
        raise ValueError("candidate payload is invalid")
    skip_metadata = raw["skip_metadata"]
    if skip_metadata is None:
        skip_reason_code = None
    elif isinstance(skip_metadata, Mapping) and set(skip_metadata) == {"reason_code"}:
        skip_reason_code = str(skip_metadata["reason_code"])
    else:
        raise ValueError("candidate skip metadata is invalid")
    return ReplenishmentCandidate(
        product_id=str(raw["product_id"]),
        product_name=str(raw["product_name"]),
        category_id=str(raw["category_id"]),
        reorder_minimum=Decimal(str(raw["reorder_minimum"])),
        reorder_maximum=Decimal(str(raw["reorder_maximum"])),
        projected_quantity=Decimal(str(raw["projected_quantity"])),
        projected_trigger_date=date.fromisoformat(str(raw["projected_trigger_date"])),
        skip_reason_code=skip_reason_code,
    )


def _candidate_page(payload: Mapping[str, object]) -> CandidatePage:
    if set(payload) != {"environment", "candidates", "next_cursor"}:
        raise ValueError("candidate page payload is invalid")
    raw_candidates = payload["candidates"]
    if not isinstance(raw_candidates, list):
        raise ValueError("candidate page items are invalid")
    return CandidatePage(
        environment=Environment(str(payload["environment"])),
        candidates=tuple(_candidate(item) for item in raw_candidates),
        next_cursor=(
            str(payload["next_cursor"]) if payload["next_cursor"] is not None else None
        ),
    )


@dataclass(frozen=True, slots=True)
class StreamableHttpProcurementMcp(ProcurementMcpPort):
    """Authenticated MCP port adapter using the real Streamable HTTP transport."""

    url: str
    bearer_token: str = field(repr=False)
    timeout_seconds: float = 5.0

    async def list_replenishment_candidates(
        self,
        *,
        environment: Environment,
        horizon_days: int,
        limit: int,
    ) -> CandidatePage:
        try:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.bearer_token}"},
                timeout=self.timeout_seconds,
            ) as http_client:
                async with streamable_http_client(
                    self.url,
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            _MCP_TOOL_NAME,
                            arguments={
                                "environment": environment.value,
                                "horizon_days": horizon_days,
                                "limit": limit,
                                "cursor": None,
                            },
                        )
        except httpx.TimeoutException:
            raise McpTimeoutError(retry_count=0) from None
        except Exception as error:
            raise McpUnavailableError(retry_count=0, private_detail=error) from None

        if result.isError:
            _raise_mcp_error(result)
        if not isinstance(result.structuredContent, Mapping):
            raise McpUnavailableError(retry_count=0)
        try:
            return _candidate_page(result.structuredContent)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise McpUnavailableError(retry_count=0, private_detail=error) from None


def create_local_api_app(
    settings: LocalApiSettings | None = None,
    *,
    bedrock_client_factory: BedrockClientFactory = create_bedrock_runtime_client,
    dynamodb_client_factory: DynamoClientFactory = create_dynamodb_client,
    checkpoint_factory: CheckpointFactory = create_dynamodb_checkpointer,
) -> FastAPI:
    """Build the local API with only API-owned dependencies and adapters."""

    resolved = settings or LocalApiSettings.from_environment()
    logger = configure_json_logging(
        service=resolved.api.service_name,
        environment=resolved.api.environment.value,
        level=resolved.api.log_level,
    )
    http_metrics = create_http_metrics()
    agent_metrics = create_agent_metrics(http_metrics.registry)
    application_repository: ApplicationRepository
    checkpointer: BaseCheckpointSaver[Any]
    if resolved.persistence_mode is PersistenceMode.MEMORY:
        application_repository = InMemoryApplicationRepository(
            environment=resolved.api.environment
        )
        checkpointer = InMemorySaver()
    else:
        application_table = resolved.dynamodb_application_table
        checkpoint_table = resolved.dynamodb_checkpoint_table
        if application_table is None or checkpoint_table is None:
            raise RuntimeError("DynamoDB table settings were not validated")
        application_repository = DynamoApplicationRepository(
            client=dynamodb_client_factory(
                region_name=resolved.aws_region,
                endpoint_url=resolved.dynamodb_endpoint_url,
            ),
            table_name=application_table,
            environment=resolved.api.environment,
        )
        checkpointer = checkpoint_factory(
            DynamoCheckpointSettings(
                environment=resolved.api.environment,
                table_name=checkpoint_table,
                region_name=resolved.aws_region,
                endpoint_url=resolved.dynamodb_endpoint_url,
            )
        )
    graph = build_walking_skeleton_graph(
        mcp=StreamableHttpProcurementMcp(
            url=resolved.mcp_url,
            bearer_token=resolved.mcp_token,
            timeout_seconds=resolved.mcp_timeout_seconds,
        ),
        llm=_structured_llm(
            resolved,
            bedrock_client_factory=bedrock_client_factory,
        ),
        checkpointer=checkpointer,
        metrics=agent_metrics,
        logger=logger,
    )
    return create_app(
        settings=resolved.api,
        logger=logger,
        http_metrics=http_metrics,
        agent_metrics=agent_metrics,
        scan_workflow=cast(ScanWorkflow, graph),
        application_repository=application_repository,
    )


app = create_local_api_app()


def run() -> None:
    """Run only the configured API composition root."""

    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - required inside the container boundary
        port=8000,
        log_level="warning",
        access_log=False,
        server_header=False,
    )
