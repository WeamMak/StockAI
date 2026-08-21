"""Idempotent draft purchase-order creation tool behavior."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from time import perf_counter

from pydantic import ValidationError

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.idempotency import (
    DraftErpPort,
    DraftReconciliationRequiredError,
    resolve_idempotent_draft,
)
from procurement.mcp_server.observability import McpMetrics
from procurement.mcp_server.schemas import CreateDraftInput, PurchaseOrderDraftOutput
from procurement.observability.logging import log_event
from procurement.ports.erp import (
    ErpUnavailableError,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
)

TOOL_NAME = "create_purchase_order_draft"
_RETRYABLE_CODES = frozenset({ErrorCode.MCP_TIMEOUT, ErrorCode.ODOO_UNAVAILABLE})


class SafeMcpToolError(Exception):
    """Stable MCP tool failure containing no raw request or upstream data."""

    def __init__(
        self,
        error_code: ErrorCode,
        safe_message: str,
        retry_count: int,
    ) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message
        self.retry_count = retry_count

    @property
    def retryable(self) -> bool:
        """Whether a caller may safely retry this call after backoff."""

        return self.error_code in _RETRYABLE_CODES

    def __str__(self) -> str:
        return json.dumps(
            {
                "error_code": self.error_code.value,
                "message": self.safe_message,
                "retry_count": self.retry_count,
                "retryable": self.retryable,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


def _validated_output(draft: PurchaseOrderDraft) -> PurchaseOrderDraftOutput:
    if not isinstance(draft, PurchaseOrderDraft):
        raise TypeError("ERP response must be a PurchaseOrderDraft")
    return PurchaseOrderDraftOutput(
        po_id=draft.po_id,
        write_date=draft.write_date,
        state=draft.state,
        partner_id=draft.partner_id,
        currency_id=draft.currency_id,
        amount_total=draft.amount_total,
    )


def _record_completion(
    *,
    metrics: McpMetrics,
    logger: logging.Logger,
    started_at: float,
    status: str,
    retry_count: int,
    error_code: ErrorCode | None = None,
) -> None:
    duration_seconds = perf_counter() - started_at
    metrics.observe_call(
        tool=TOOL_NAME,
        status=status,
        duration_seconds=duration_seconds,
        error_code=error_code,
    )
    fields: dict[str, object] = {
        "tool_name": TOOL_NAME,
        "duration_ms": round(duration_seconds * 1000, 3),
        "status": status,
        "retry_count": retry_count,
    }
    if error_code is not None:
        fields["error_code"] = error_code.value
    log_event(
        logger,
        "mcp_tool_completed",
        level=logging.ERROR if error_code is not None else logging.INFO,
        **fields,
    )


def _raise_safe_error(
    *,
    error_code: ErrorCode,
    safe_message: str,
    metrics: McpMetrics,
    logger: logging.Logger,
    started_at: float,
    retry_count: int,
) -> None:
    _record_completion(
        metrics=metrics,
        logger=logger,
        started_at=started_at,
        status="error",
        retry_count=retry_count,
        error_code=error_code,
    )
    raise SafeMcpToolError(error_code, safe_message, retry_count) from None


async def create_purchase_order_draft(
    *,
    request: CreateDraftInput,
    erp: DraftErpPort,
    server_environment: Environment,
    metrics: McpMetrics,
    logger: logging.Logger,
    write_timeout_seconds: float = 20,
) -> PurchaseOrderDraftOutput:
    """Idempotently create, validate, observe, and safely expose one draft."""

    if write_timeout_seconds <= 0:
        raise ValueError("write_timeout_seconds must be positive")

    started_at = perf_counter()
    retry_count = 0
    if request.environment != server_environment.value:
        _raise_safe_error(
            error_code=ErrorCode.FORBIDDEN,
            safe_message="The requested environment is not allowed.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )

    try:
        command = PurchaseOrderDraftCommand(
            origin=request.origin,
            vendor_id=request.vendor_id,
            currency_code=request.currency_code,
            product_id=request.product_id,
            product_name=request.product_name,
            quantity=Decimal(request.quantity),
            unit_price=Decimal(request.unit_price),
            need_by_date=date.fromisoformat(request.need_by_date),
        )
    except (InvalidOperation, ValueError):
        _raise_safe_error(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The draft request is invalid.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )

    try:
        async with asyncio.timeout(write_timeout_seconds):
            draft = await resolve_idempotent_draft(erp=erp, command=command)
    except TimeoutError:
        _raise_safe_error(
            error_code=ErrorCode.RECONCILIATION_REQUIRED,
            safe_message="The purchase-order draft could not be safely reconciled.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )
    except DraftReconciliationRequiredError:
        _raise_safe_error(
            error_code=ErrorCode.RECONCILIATION_REQUIRED,
            safe_message="The purchase-order draft could not be safely reconciled.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )
    except ErpUnavailableError as error:
        _raise_safe_error(
            error_code=ErrorCode.ODOO_UNAVAILABLE,
            safe_message="The procurement source is unavailable.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=error.retry_count,
        )
    except Exception:
        _raise_safe_error(
            error_code=ErrorCode.ODOO_UNAVAILABLE,
            safe_message="The procurement source is unavailable.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )

    try:
        response = _validated_output(draft)
    except (TypeError, ValueError, ValidationError):
        _raise_safe_error(
            error_code=ErrorCode.ODOO_UNAVAILABLE,
            safe_message="The procurement source returned an invalid response.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )

    _record_completion(
        metrics=metrics,
        logger=logger,
        started_at=started_at,
        status="success",
        retry_count=retry_count,
    )
    return response
