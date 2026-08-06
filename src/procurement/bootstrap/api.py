"""Local composition root for the runnable procurement API process."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from procurement.agent.graph import build_walking_skeleton_graph
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

_MCP_TOOL_NAME = "list_replenishment_candidates"
_MIN_TOKEN_LENGTH = 32
_MAX_TOKEN_LENGTH = 512


@dataclass(frozen=True, slots=True)
class LocalApiSettings:
    """Validated local-only dependencies added by the API composition root."""

    api: ApiSettings
    mcp_url: str
    mcp_token: str = field(repr=False)
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

    @classmethod
    def from_environment(cls) -> LocalApiSettings:
        """Load the local MCP client boundary from process environment."""

        mcp_token = os.environ.get("PROCUREMENT_MCP_TOKEN")
        if mcp_token is None:
            raise ValueError("PROCUREMENT_MCP_TOKEN is required")
        return cls(
            api=ApiSettings.from_environment(),
            mcp_url=os.environ.get(
                "PROCUREMENT_MCP_URL",
                "http://127.0.0.1:9000/mcp",
            ),
            mcp_token=mcp_token,
            mcp_timeout_seconds=float(
                os.environ.get("PROCUREMENT_MCP_CLIENT_TIMEOUT_SECONDS", "5")
            ),
        )


class LocalStructuredLlm(StructuredLlmPort):
    """Deterministic local reasoning substitute until the Bedrock task."""

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
    graph = build_walking_skeleton_graph(
        mcp=StreamableHttpProcurementMcp(
            url=resolved.mcp_url,
            bearer_token=resolved.mcp_token,
            timeout_seconds=resolved.mcp_timeout_seconds,
        ),
        llm=LocalStructuredLlm(),
        metrics=agent_metrics,
        logger=logger,
    )
    return create_app(
        settings=resolved.api,
        logger=logger,
        http_metrics=http_metrics,
        agent_metrics=agent_metrics,
        scan_workflow=cast(ScanWorkflow, graph),
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
