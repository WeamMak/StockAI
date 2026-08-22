"""Rejection-record-authorized draft purchase-order cancellation tool."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter

from procurement.domain.decisions import DecisionId, RejectionRecord
from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.observability import McpMetrics
from procurement.mcp_server.schemas import ApplyDecisionInput, PurchaseOrderActionOutput
from procurement.mcp_server.tools._decision_action import apply_decision_action, fail
from procurement.ports.decisions import DecisionReader
from procurement.ports.erp import ErpPort, PurchaseOrderAction

TOOL_NAME = "cancel_draft_purchase_order"
Clock = Callable[[], datetime]


async def cancel_draft_purchase_order(
    *,
    request: ApplyDecisionInput,
    decisions: DecisionReader,
    erp: ErpPort,
    server_environment: Environment,
    metrics: McpMetrics,
    logger: logging.Logger,
    now: Clock = lambda: datetime.now(tz=UTC),
) -> PurchaseOrderActionOutput:
    """Strongly read and independently validate one immutable rejection."""

    del now
    started_at = perf_counter()
    if request.environment != server_environment.value:
        fail(
            tool_name=TOOL_NAME,
            code=ErrorCode.FORBIDDEN,
            message="The requested environment is not allowed.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    try:
        decision_id = DecisionId(server_environment, request.decision_id)
        decision = await decisions.get_decision(decision_id)
    except (TypeError, ValueError):
        decision = None
    if (
        not isinstance(decision, RejectionRecord)
        or decision.idempotency_key != request.idempotency_key
    ):
        fail(
            tool_name=TOOL_NAME,
            code=ErrorCode.APPROVAL_STALE,
            message="The rejection is missing or no longer valid.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    return await apply_decision_action(
        tool_name=TOOL_NAME,
        decision=decision,
        erp=erp,
        action=PurchaseOrderAction.CANCEL,
        metrics=metrics,
        logger=logger,
        started_at=started_at,
    )
