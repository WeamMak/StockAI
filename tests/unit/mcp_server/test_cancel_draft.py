"""Rejection-only authorization for draft purchase-order cancellation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.unit.domain.test_decisions import CASE_ID, NOW, PO_ID, PO_WRITE_DATE
from tests.unit.mcp_server.test_confirm import Erp, Reader, _snapshot

from procurement.domain.decisions import (
    DecisionText,
    DecisionType,
    RejectionRecord,
    decision_id_for,
)
from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment, Revision
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.schemas import ApplyDecisionInput
from procurement.mcp_server.tools.cancel_draft import cancel_draft_purchase_order
from procurement.mcp_server.tools.create_draft import SafeMcpToolError
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import PurchaseOrderAction


def _rejection() -> RejectionRecord:
    return RejectionRecord(
        decision_id=decision_id_for(
            environment=Environment.DEV,
            case_id=CASE_ID,
            decision_type=DecisionType.REJECT,
            po_id=PO_ID,
            po_write_date=PO_WRITE_DATE,
        ),
        case_id=CASE_ID,
        manager_subject="manager-001",
        manager_role="manager",
        case_revision=Revision(3),
        po_id=PO_ID,
        po_write_date=PO_WRITE_DATE,
        po_state="draft",
        partner_id=17,
        currency_id=1,
        amount_total=Decimal("312.500000"),
        reason=DecisionText("Vendor risk requires manual handling."),
        evidence_digest="sha256:" + "a" * 64,
        idempotency_key="reject-001",
        decided_at=NOW,
    )


@pytest.mark.anyio
async def test_cancel_accepts_only_rejection_and_calls_only_cancel() -> None:
    rejection = _rejection()
    erp = Erp([_snapshot()])

    result = await cancel_draft_purchase_order(
        request=ApplyDecisionInput(
            environment="dev",
            decision_id=rejection.decision_id.value,
            idempotency_key=rejection.idempotency_key,
        ),
        decisions=Reader(rejection),
        erp=erp,  # type: ignore[arg-type]
        server_environment=Environment.DEV,
        metrics=create_mcp_metrics(),
        logger=configure_json_logging(
            service="procurement-mcp",
            environment="dev",
            logger_name="procurement.test.cancel",
        ),
        now=lambda: datetime(2026, 8, 21, 12, 5, tzinfo=UTC),
    )

    assert result.state == "cancel"
    assert erp.actions == [PurchaseOrderAction.CANCEL]


@pytest.mark.anyio
async def test_cancel_rejects_approval_without_erp_access() -> None:
    from tests.unit.domain.test_decisions import _approval

    approval = _approval()
    erp = Erp([])
    with pytest.raises(SafeMcpToolError) as raised:
        await cancel_draft_purchase_order(
            request=ApplyDecisionInput(
                environment="dev",
                decision_id=approval.decision_id.value,
                idempotency_key=approval.idempotency_key,
            ),
            decisions=Reader(approval),
            erp=erp,  # type: ignore[arg-type]
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.cancel.wrong_type",
            ),
            now=lambda: datetime(2026, 8, 21, 12, 5, tzinfo=UTC),
        )
    assert raised.value.error_code is ErrorCode.APPROVAL_STALE
    assert erp.actions == []
