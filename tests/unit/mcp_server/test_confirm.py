"""Independent authorization and reconciliation for PO confirmation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.domain.test_decisions import NOW, _approval

from procurement.domain.decisions import DecisionId, DecisionRecord
from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.schemas import ApplyDecisionInput, PurchaseOrderActionOutput
from procurement.mcp_server.tools.confirm import confirm_purchase_order
from procurement.mcp_server.tools.create_draft import SafeMcpToolError
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    PurchaseOrderAction,
    PurchaseOrderActionResult,
    PurchaseOrderDraft,
    PurchaseOrderWriteAmbiguousError,
)


@dataclass
class Reader:
    record: DecisionRecord | None

    async def get_decision(self, decision_id: DecisionId) -> DecisionRecord | None:
        assert decision_id.environment is Environment.DEV
        return self.record


class Erp:
    def __init__(self, snapshots: list[PurchaseOrderActionResult]) -> None:
        self.snapshots = snapshots
        self.actions: list[PurchaseOrderAction] = []
        self.action_error: Exception | None = None

    async def read_purchase_order(self, *, po_id: int) -> PurchaseOrderActionResult:
        assert po_id == 41
        return self.snapshots.pop(0)

    async def apply_purchase_order_action_once(
        self, *, po_id: int, expected: PurchaseOrderDraft, action: PurchaseOrderAction
    ) -> PurchaseOrderActionResult:
        assert po_id == expected.po_id == 41
        self.actions.append(action)
        if self.action_error is not None:
            raise self.action_error
        terminal = "purchase" if action is PurchaseOrderAction.CONFIRM else "cancel"
        return _snapshot(state=terminal, write_date="2026-08-21 12:01:00")


def _snapshot(
    *, state: str = "draft", write_date: str = "2026-08-21 12:00:00"
) -> PurchaseOrderActionResult:
    approval = _approval()
    return PurchaseOrderActionResult(
        po_id=approval.po_id,
        po_reference="P00041",
        write_date=write_date,
        state=state,
        partner_id=approval.partner_id,
        currency_id=approval.currency_id,
        amount_total=approval.amount_total,
    )


def _request(**overrides: object) -> ApplyDecisionInput:
    values: dict[str, object] = {
        "environment": "dev",
        "decision_id": _approval().decision_id.value,
        "idempotency_key": "approve-001",
    }
    values.update(overrides)
    return ApplyDecisionInput.model_validate(values)


async def _confirm(
    *, reader: Reader, erp: Erp, request: ApplyDecisionInput | None = None
) -> PurchaseOrderActionOutput:
    return await confirm_purchase_order(
        request=request or _request(),
        decisions=reader,
        erp=erp,  # type: ignore[arg-type]
        server_environment=Environment.DEV,
        metrics=create_mcp_metrics(),
        logger=configure_json_logging(
            service="procurement-mcp",
            environment="dev",
            logger_name="procurement.test.confirm",
        ),
        now=lambda: datetime(2026, 8, 21, 12, 5, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_confirm_reads_decision_and_exact_draft_then_calls_only_confirm() -> None:
    erp = Erp([_snapshot()])

    result = await _confirm(reader=Reader(_approval()), erp=erp)

    assert result.state == "purchase"
    assert result.po_reference == "P00041"
    assert result.reconciled is False
    assert erp.actions == [PurchaseOrderAction.CONFIRM]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("record", "input_request", "code"),
    [
        (None, _request(), ErrorCode.APPROVAL_STALE),
        (_approval(), _request(environment="prod"), ErrorCode.FORBIDDEN),
        (
            replace(
                _approval(),
                decided_at=NOW,
                expires_at=replace(NOW, value=NOW.value + timedelta(minutes=30)),
            ),
            _request(idempotency_key="different"),
            ErrorCode.APPROVAL_STALE,
        ),
    ],
)
async def test_confirm_rejects_invalid_authorization_without_erp_write(
    record: DecisionRecord | None,
    input_request: ApplyDecisionInput,
    code: ErrorCode,
) -> None:
    erp = Erp([])

    with pytest.raises(SafeMcpToolError) as raised:
        await _confirm(reader=Reader(record), erp=erp, request=input_request)

    assert raised.value.error_code is code
    assert erp.actions == []


@pytest.mark.anyio
async def test_confirm_rejects_expired_or_changed_snapshot_without_write() -> None:
    expired = _approval()
    erp = Erp([replace(_snapshot(), partner_id=99)])

    with pytest.raises(SafeMcpToolError) as changed:
        await _confirm(reader=Reader(expired), erp=erp)
    assert changed.value.error_code is ErrorCode.APPROVAL_STALE
    assert erp.actions == []

    with pytest.raises(SafeMcpToolError) as stale:
        await confirm_purchase_order(
            request=_request(),
            decisions=Reader(expired),
            erp=Erp([]),  # type: ignore[arg-type]
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.confirm.expired",
            ),
            now=lambda: expired.expires_at.value,
        )
    assert stale.value.error_code is ErrorCode.APPROVAL_STALE


@pytest.mark.anyio
async def test_ambiguous_confirm_reconciles_by_read_without_resending() -> None:
    erp = Erp([_snapshot(), _snapshot(state="purchase")])
    erp.action_error = PurchaseOrderWriteAmbiguousError()

    result = await _confirm(reader=Reader(_approval()), erp=erp)

    assert result.reconciled is True
    assert erp.actions == [PurchaseOrderAction.CONFIRM]


@pytest.mark.anyio
async def test_unresolved_ambiguous_confirm_requires_reconciliation() -> None:
    erp = Erp([_snapshot(), _snapshot()])
    erp.action_error = PurchaseOrderWriteAmbiguousError()

    with pytest.raises(SafeMcpToolError) as raised:
        await _confirm(reader=Reader(_approval()), erp=erp)

    assert raised.value.error_code is ErrorCode.RECONCILIATION_REQUIRED
    assert erp.actions == [PurchaseOrderAction.CONFIRM]
