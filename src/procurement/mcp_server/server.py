"""Composition root for the authenticated Procurement MCP server."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ConfigDict
from starlette.requests import Request
from starlette.responses import Response

from procurement.domain.identifiers import Environment
from procurement.mcp_server.auth import (
    StaticBearerTokenVerifier,
    create_auth_settings,
)
from procurement.mcp_server.schemas import (
    CandidateCursor,
    CandidateLimit,
    EnvironmentValue,
    HorizonDays,
    ListReplenishmentCandidatesInput,
    ListReplenishmentCandidatesOutput,
)
from procurement.mcp_server.tools import candidates
from procurement.observability.logging import configure_json_logging
from procurement.observability.metrics import McpMetrics, create_mcp_metrics
from procurement.ports.erp import ErpPort

SERVICE_NAME = "procurement-mcp"


def _harden_tool_argument_validation(server: FastMCP) -> None:
    """Make SDK-generated validation strict, extra-forbid, and non-echoing."""

    tool = server._tool_manager.get_tool(candidates.TOOL_NAME)
    if tool is None:  # pragma: no cover - construction invariant
        raise RuntimeError("The candidate tool was not registered.")
    argument_model = tool.fn_metadata.arg_model
    argument_model.model_config = ConfigDict(
        **argument_model.model_config,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )
    argument_model.model_rebuild(force=True)
    tool.parameters = argument_model.model_json_schema(by_alias=True)


def create_mcp_server(
    *,
    erp: ErpPort,
    environment: Environment,
    bearer_token: str,
    host: str = "127.0.0.1",
    port: int = 9000,
    metrics: McpMetrics | None = None,
    logger: logging.Logger | None = None,
    read_timeout_seconds: float = 10,
    max_retries: int = 2,
    retry_delay_seconds: float = 0.05,
) -> FastMCP:
    """Create one independently configured MCP service instance."""

    resolved_metrics = metrics or create_mcp_metrics()
    server_environment = environment
    resolved_logger = logger or configure_json_logging(
        service=SERVICE_NAME,
        environment=environment.value,
    )
    server = FastMCP(
        name="StockAI Procurement MCP",
        instructions=(
            "Expose bounded, read-only procurement evidence through strict schemas."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        token_verifier=StaticBearerTokenVerifier(bearer_token),
        auth=create_auth_settings(),
    )

    @server.tool(
        name=candidates.TOOL_NAME,
        title="List replenishment candidates",
        description=(
            "List one bounded page of fictional ERP products that may need "
            "replenishment."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def list_replenishment_candidates(
        environment: EnvironmentValue,
        horizon_days: HorizonDays,
        limit: CandidateLimit = 25,
        cursor: CandidateCursor | None = None,
    ) -> ListReplenishmentCandidatesOutput:
        """Discover a bounded candidate page from the configured ERP port."""

        request = ListReplenishmentCandidatesInput(
            environment=environment,
            horizon_days=horizon_days,
            limit=limit,
            cursor=cursor,
        )
        return await candidates.list_replenishment_candidates(
            request=request,
            erp=erp,
            server_environment=server_environment,
            metrics=resolved_metrics,
            logger=resolved_logger,
            read_timeout_seconds=read_timeout_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
        )

    _harden_tool_argument_validation(server)

    @server.custom_route(
        "/metrics",
        methods=["GET"],
        include_in_schema=False,
    )  # type: ignore[untyped-decorator]
    async def metrics_endpoint(_: Request) -> Response:
        """Expose this MCP process's isolated Prometheus registry."""

        return Response(
            content=generate_latest(resolved_metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    return server
