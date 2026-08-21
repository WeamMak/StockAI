"""Idempotent draft-creation orchestration shared by the create_draft tool.

Coordinates the ERP boundary's two draft primitives so that repeat calls,
concurrent calls, and calls that follow a lost or timed-out response all
resolve to the same one draft instead of creating a duplicate purchase
order. A non-idempotent write (`create_purchase_order_draft`) is never
retried blindly: any ambiguous outcome is resolved by searching for the
draft that may already exist, and only reported as needing manual
reconciliation when that search still cannot tell.
"""

from __future__ import annotations

import asyncio

from procurement.ports.erp import (
    DraftWriteAmbiguousError,
    ErpPort,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
)

DEFAULT_CREATE_TIMEOUT_SECONDS = 8.0


class DraftReconciliationRequiredError(Exception):
    """Neither an initial nor a post-ambiguity search resolved this draft."""

    safe_message = "The purchase-order draft could not be safely reconciled."

    def __init__(self) -> None:
        super().__init__(self.safe_message)


async def resolve_idempotent_draft(
    *,
    erp: ErpPort,
    command: PurchaseOrderDraftCommand,
    create_timeout_seconds: float = DEFAULT_CREATE_TIMEOUT_SECONDS,
) -> PurchaseOrderDraft:
    """Return the one draft bound to `command.origin`, creating it at most once.

    Always searches before creating, so repeat calls -- retries, a graph node
    re-running from the top on interrupt resume, a process restart -- return
    the existing draft instead of duplicating it. If the create attempt's
    outcome is ambiguous (it raises, or a bounded timeout cancels it while a
    write may already be in flight), searches once more before giving up and
    requiring manual reconciliation. It never treats an ambiguous write as
    safe to retry.
    """

    if create_timeout_seconds <= 0:
        raise ValueError("create_timeout_seconds must be positive")

    existing = await erp.find_purchase_order_draft(origin=command.origin)
    if existing is not None:
        return existing

    try:
        async with asyncio.timeout(create_timeout_seconds):
            return await erp.create_purchase_order_draft(command)
    except (TimeoutError, DraftWriteAmbiguousError):
        existing = await erp.find_purchase_order_draft(origin=command.origin)
        if existing is not None:
            return existing
        raise DraftReconciliationRequiredError() from None
