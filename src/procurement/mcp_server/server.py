"""Composition root for the authenticated Procurement MCP server."""

from __future__ import annotations

import logging
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ConfigDict
from starlette.requests import Request
from starlette.responses import Response

from procurement.domain.decisions import DecisionId, DecisionRecord
from procurement.domain.identifiers import Environment
from procurement.mcp_server.auth import (
    StaticBearerTokenVerifier,
    create_auth_settings,
)
from procurement.mcp_server.observability import McpMetrics, create_mcp_metrics
from procurement.mcp_server.schemas import (
    ApplyDecisionInput,
    CandidateCursor,
    CandidateLimit,
    CreateDraftInput,
    EnvironmentValue,
    GetProcurementEvidenceInput,
    GetProcurementPreferencesInput,
    HorizonDays,
    ListReplenishmentCandidatesInput,
    ListReplenishmentCandidatesOutput,
    ProcurementEvidenceOutput,
    ProcurementPreferenceOutput,
    PurchaseOrderActionOutput,
    PurchaseOrderDraftOutput,
)
from procurement.mcp_server.tools import (
    cancel_draft,
    candidates,
    confirm,
    create_draft,
    evidence,
    preferences,
)
from procurement.observability.logging import configure_json_logging
from procurement.ports.decisions import DecisionReader
from procurement.ports.erp import ErpPort

SERVICE_NAME = "procurement-mcp"


class _MissingDecisionReader:
    async def get_decision(self, decision_id: DecisionId) -> DecisionRecord | None:
        del decision_id
        return None


def _harden_tool_argument_validation(server: FastMCP) -> None:
    """Make SDK-generated validation strict, extra-forbid, and non-echoing."""

    for tool_name in (
        candidates.TOOL_NAME,
        evidence.TOOL_NAME,
        preferences.TOOL_NAME,
        create_draft.TOOL_NAME,
        confirm.TOOL_NAME,
        cancel_draft.TOOL_NAME,
    ):
        tool = server._tool_manager.get_tool(tool_name)
        if tool is None:  # pragma: no cover - construction invariant
            raise RuntimeError(f"The {tool_name} tool was not registered.")
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
    decisions: DecisionReader | None = None,
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
    resolved_decisions = decisions or _MissingDecisionReader()
    server_environment = environment
    resolved_logger = logger or configure_json_logging(
        service=SERVICE_NAME,
        environment=environment.value,
    )
    server = FastMCP(
        name="StockAI Procurement MCP",
        instructions=(
            "Expose bounded procurement evidence, and one idempotent draft "
            "purchase-order creation, through strict schemas."
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
            "List one bounded page of ERP products that may need replenishment."
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

    @server.tool(
        name=evidence.TOOL_NAME,
        title="Get procurement evidence",
        description="Get complete deterministic evidence for one candidate product.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def get_procurement_evidence(
        environment: EnvironmentValue,
        product_id: str,
        horizon_days: Literal[14] = 14,
    ) -> ProcurementEvidenceOutput:
        request = GetProcurementEvidenceInput(
            environment=environment,
            product_id=product_id,
            horizon_days=horizon_days,
        )
        return await evidence.get_procurement_evidence(
            request=request,
            erp=erp,
            server_environment=server_environment,
            metrics=resolved_metrics,
            logger=resolved_logger,
            read_timeout_seconds=read_timeout_seconds,
        )

    @server.tool(
        name=preferences.TOOL_NAME,
        title="Get procurement preferences",
        description="Resolve one typed current company, category, or product profile.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def get_procurement_preferences(
        environment: EnvironmentValue,
        company_id: str,
        category_id: str,
        product_id: str,
    ) -> ProcurementPreferenceOutput:
        request = GetProcurementPreferencesInput(
            environment=environment,
            company_id=company_id,
            category_id=category_id,
            product_id=product_id,
        )
        return await preferences.get_procurement_preferences(
            request=request,
            erp=erp,
            server_environment=server_environment,
            metrics=resolved_metrics,
            logger=resolved_logger,
            read_timeout_seconds=read_timeout_seconds,
        )

    @server.tool(
        name=create_draft.TOOL_NAME,
        title="Create a draft purchase order",
        description=(
            "Idempotently create, or return the existing, draft purchase order "
            "bound to one case."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def create_purchase_order_draft(
        environment: EnvironmentValue,
        origin: str,
        vendor_id: str,
        currency_code: str,
        product_id: str,
        product_name: str,
        quantity: str,
        unit_price: str,
        need_by_date: str,
    ) -> PurchaseOrderDraftOutput:
        request = CreateDraftInput(
            environment=environment,
            origin=origin,
            vendor_id=vendor_id,
            currency_code=currency_code,
            product_id=product_id,
            product_name=product_name,
            quantity=quantity,
            unit_price=unit_price,
            need_by_date=need_by_date,
        )
        return await create_draft.create_purchase_order_draft(
            request=request,
            erp=erp,
            server_environment=server_environment,
            metrics=resolved_metrics,
            logger=resolved_logger,
        )

    @server.tool(
        name=confirm.TOOL_NAME,
        title="Confirm an approved purchase order",
        description="Confirm only the exact draft authorized by an immutable approval.",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def confirm_purchase_order(
        environment: EnvironmentValue,
        decision_id: str,
        idempotency_key: str,
    ) -> PurchaseOrderActionOutput:
        request = ApplyDecisionInput(
            environment=environment,
            decision_id=decision_id,
            idempotency_key=idempotency_key,
        )
        return await confirm.confirm_purchase_order(
            request=request,
            decisions=resolved_decisions,
            erp=erp,
            server_environment=server_environment,
            metrics=resolved_metrics,
            logger=resolved_logger,
        )

    @server.tool(
        name=cancel_draft.TOOL_NAME,
        title="Cancel a rejected draft purchase order",
        description="Cancel only the exact draft authorized by an immutable rejection.",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def cancel_draft_purchase_order(
        environment: EnvironmentValue,
        decision_id: str,
        idempotency_key: str,
    ) -> PurchaseOrderActionOutput:
        request = ApplyDecisionInput(
            environment=environment,
            decision_id=decision_id,
            idempotency_key=idempotency_key,
        )
        return await cancel_draft.cancel_draft_purchase_order(
            request=request,
            decisions=resolved_decisions,
            erp=erp,
            server_environment=server_environment,
            metrics=resolved_metrics,
            logger=resolved_logger,
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
