"""Mechanical, read-before-write execution shared by manager decision tools."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import NoReturn

from procurement.domain.decisions import ApprovalRecord, RejectionRecord
from procurement.domain.errors import ErrorCode
from procurement.mcp_server.observability import McpMetrics
from procurement.mcp_server.schemas import PurchaseOrderActionOutput
from procurement.mcp_server.tools.create_draft import SafeMcpToolError
from procurement.observability.logging import log_event
from procurement.ports.erp import (
    ApprovalStaleError,
    ErpPort,
    PurchaseOrderAction,
    PurchaseOrderActionResult,
    PurchaseOrderDraft,
    PurchaseOrderWriteAmbiguousError,
)

DecisionWithDraft = ApprovalRecord | RejectionRecord


def fail(
    *,
    tool_name: str,
    code: ErrorCode,
    message: str,
    metrics: McpMetrics,
    logger: logging.Logger,
    started_at: float,
) -> NoReturn:
    duration = perf_counter() - started_at
    metrics.observe_call(
        tool=tool_name,
        status="error",
        duration_seconds=duration,
        error_code=code,
    )
    if tool_name in {"confirm_purchase_order", "cancel_draft_purchase_order"}:
        metrics.observe_purchase_order_action(
            action="confirm" if tool_name == "confirm_purchase_order" else "cancel",
            result=(
                "stale"
                if code is ErrorCode.APPROVAL_STALE
                else (
                    "reconciliation_required"
                    if code is ErrorCode.RECONCILIATION_REQUIRED
                    else "error"
                )
            ),
            duration_seconds=duration,
        )
    log_event(
        logger,
        "mcp_tool_completed",
        level=logging.ERROR,
        tool_name=tool_name,
        status="error",
        error_code=code.value,
        retry_count=0,
    )
    raise SafeMcpToolError(code, message, 0) from None


def _same_bound_order(
    snapshot: PurchaseOrderActionResult,
    decision: DecisionWithDraft,
    *,
    include_revision: bool,
) -> bool:
    return (
        snapshot.po_id == decision.po_id
        and snapshot.partner_id == decision.partner_id
        and snapshot.currency_id == decision.currency_id
        and snapshot.amount_total == decision.amount_total
        and (not include_revision or snapshot.write_date == decision.po_write_date)
    )


def _output(
    snapshot: PurchaseOrderActionResult, *, reconciled: bool
) -> PurchaseOrderActionOutput:
    return PurchaseOrderActionOutput(
        po_id=snapshot.po_id,
        po_reference=snapshot.po_reference or str(snapshot.po_id),
        state=snapshot.state,  # type: ignore[arg-type]
        write_date=snapshot.write_date,
        reconciled=reconciled,
    )


async def apply_decision_action(
    *,
    tool_name: str,
    decision: DecisionWithDraft,
    erp: ErpPort,
    action: PurchaseOrderAction,
    metrics: McpMetrics,
    logger: logging.Logger,
    started_at: float,
    write_timeout_seconds: float = 15,
) -> PurchaseOrderActionOutput:
    """Read, apply once, and reconcile ambiguity without resending a write."""

    terminal = "purchase" if action is PurchaseOrderAction.CONFIRM else "cancel"
    try:
        current = await erp.read_purchase_order(po_id=decision.po_id)
    except Exception:
        fail(
            tool_name=tool_name,
            code=ErrorCode.ODOO_UNAVAILABLE,
            message="The purchase order could not be read safely.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    if current.state == terminal and _same_bound_order(
        current, decision, include_revision=False
    ):
        return _output(current, reconciled=True)
    if (
        current.state not in {"draft", "sent"}
        or current.state != decision.po_state
        or not _same_bound_order(current, decision, include_revision=True)
    ):
        fail(
            tool_name=tool_name,
            code=ErrorCode.APPROVAL_STALE,
            message="The approved purchase-order revision is stale.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    expected = PurchaseOrderDraft(
        po_id=decision.po_id,
        write_date=decision.po_write_date,
        state=decision.po_state,
        partner_id=decision.partner_id,
        currency_id=decision.currency_id,
        amount_total=decision.amount_total,
    )
    try:
        async with asyncio.timeout(write_timeout_seconds):
            result = await erp.apply_purchase_order_action_once(
                po_id=decision.po_id,
                expected=expected,
                action=action,
            )
    except ApprovalStaleError:
        fail(
            tool_name=tool_name,
            code=ErrorCode.APPROVAL_STALE,
            message="The approved purchase-order revision is stale.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    except (PurchaseOrderWriteAmbiguousError, TimeoutError):
        try:
            reconciled = await erp.read_purchase_order(po_id=decision.po_id)
        except Exception:
            reconciled = None
        if (
            reconciled is None
            or reconciled.state != terminal
            or not _same_bound_order(reconciled, decision, include_revision=False)
        ):
            fail(
                tool_name=tool_name,
                code=ErrorCode.RECONCILIATION_REQUIRED,
                message="The purchase-order action requires reconciliation.",
                metrics=metrics,
                logger=logger,
                started_at=started_at,
            )
        result = reconciled
        was_reconciled = True
    except Exception:
        fail(
            tool_name=tool_name,
            code=ErrorCode.ODOO_UNAVAILABLE,
            message="The purchase-order action failed safely.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    else:
        was_reconciled = False
    if result.state != terminal or not _same_bound_order(
        result, decision, include_revision=False
    ):
        fail(
            tool_name=tool_name,
            code=ErrorCode.RECONCILIATION_REQUIRED,
            message="The purchase-order action returned an invalid result.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
        )
    output = _output(result, reconciled=was_reconciled)
    metrics.observe_call(
        tool=tool_name,
        status="success",
        duration_seconds=perf_counter() - started_at,
    )
    metrics.observe_purchase_order_action(
        action="confirm" if action is PurchaseOrderAction.CONFIRM else "cancel",
        result="success",
        duration_seconds=perf_counter() - started_at,
        reconciled=was_reconciled,
    )
    log_event(
        logger,
        "mcp_tool_completed",
        tool_name=tool_name,
        status="success",
        retry_count=0,
        reconciled=was_reconciled,
    )
    return output
