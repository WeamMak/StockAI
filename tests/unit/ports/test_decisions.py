"""Decision repository behavior at the application persistence seam."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from procurement.domain.audit import AuditEvent
from procurement.domain.decisions import (
    APPROVAL_VALIDITY,
    ApprovalRecord,
    DecisionText,
    DecisionType,
    RejectionRecord,
    decision_id_for,
)
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.ports.decisions import DecisionConflictError, DecisionCreateResult
from procurement.ports.repositories import InMemoryApplicationRepository

CASE_ID = CaseId(Environment.DEV, "scan-001:product-101")
NOW = UtcTimestamp(datetime(2026, 8, 21, 12, tzinfo=UTC))
RETENTION = UtcTimestamp(NOW.value + timedelta(days=30))


def _approval() -> ApprovalRecord:
    return ApprovalRecord(
        decision_id=decision_id_for(
            environment=Environment.DEV,
            case_id=CASE_ID,
            decision_type=DecisionType.APPROVE,
            po_id=41,
            po_write_date="2026-08-21 12:00:00",
        ),
        case_id=CASE_ID,
        manager_subject="manager-001",
        manager_role="manager",
        case_revision=Revision(3),
        po_id=41,
        po_write_date="2026-08-21 12:00:00",
        po_state="draft",
        partner_id=17,
        currency_id=1,
        amount_total=Decimal("312.500000"),
        offer_id="offer-101",
        vendor_id="17",
        quantity=Decimal("25.000000"),
        unit_price=Decimal("12.500000"),
        currency="USD",
        normalized_cost=Decimal("312.500000"),
        budget_status="within_budget",
        budget_amount=Decimal("1000.000000"),
        confirmed_commitment=Decimal("100.000000"),
        remaining_before=Decimal("900.000000"),
        remaining_after=Decimal("587.500000"),
        overage=Decimal("0.000000"),
        exception_required=False,
        budget_exception=False,
        justification=None,
        evidence_digest="sha256:" + "a" * 64,
        idempotency_key="approve-001",
        decided_at=NOW,
        expires_at=UtcTimestamp(NOW.value + APPROVAL_VALIDITY),
    )


def _rejection() -> RejectionRecord:
    return RejectionRecord(
        decision_id=decision_id_for(
            environment=Environment.DEV,
            case_id=CASE_ID,
            decision_type=DecisionType.REJECT,
            po_id=41,
            po_write_date="2026-08-21 12:00:00",
        ),
        case_id=CASE_ID,
        manager_subject="manager-002",
        manager_role="manager",
        case_revision=Revision(3),
        po_id=41,
        po_write_date="2026-08-21 12:00:00",
        po_state="draft",
        partner_id=17,
        currency_id=1,
        amount_total=Decimal("312.500000"),
        reason=DecisionText("Use another procurement route."),
        evidence_digest="sha256:" + "a" * 64,
        idempotency_key="reject-001",
        decided_at=NOW,
    )


@pytest.mark.anyio
async def test_one_decision_wins_and_compatible_replay_is_idempotent() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    approval = _approval()

    first = await repository.create_decision(approval, retention_expires_at=RETENTION)
    replay = await repository.create_decision(approval, retention_expires_at=RETENTION)

    assert first == DecisionCreateResult(record=approval, created=True)
    assert replay == DecisionCreateResult(record=approval, created=False)
    assert await repository.get_decision(approval.decision_id) == approval
    with pytest.raises(DecisionConflictError):
        await repository.create_decision(_rejection(), retention_expires_at=RETENTION)


@pytest.mark.anyio
async def test_idempotency_key_cannot_bind_a_changed_decision() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    approval = _approval()
    rejection = replace(_rejection(), idempotency_key=approval.idempotency_key)

    await repository.create_decision(approval, retention_expires_at=RETENTION)
    with pytest.raises(DecisionConflictError):
        await repository.create_decision(rejection, retention_expires_at=RETENTION)


@pytest.mark.anyio
async def test_audit_is_oldest_first_with_event_id_tie_breaker() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    for event_id in ("b", "a"):
        await repository.append_audit(
            AuditEvent(
                event_id=event_id,
                case_id=CASE_ID,
                event_type="manager_approved",
                actor_id="manager-001",
                occurred_at=NOW,
                correlation_id="correlation-001",
                source_revision=Revision(3),
                outcome="approved",
                decision_id="decision-" + "a" * 64,
            ),
            expires_at=RETENTION,
        )

    rows = await repository.list_audit(CASE_ID, limit=20)
    assert [row.event_id for row in rows] == ["a", "b"]
