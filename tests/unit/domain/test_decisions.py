"""Immutable manager-decision domain contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from procurement.domain.decisions import (
    APPROVAL_VALIDITY,
    ApprovalRecord,
    DecisionText,
    DecisionType,
    RejectionRecord,
    decision_id_for,
)
from procurement.domain.errors import DomainValidationError
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp

NOW = UtcTimestamp(datetime(2026, 8, 21, 12, tzinfo=UTC))
CASE_ID = CaseId(Environment.DEV, "scan-001:product-101")
PO_ID = 41
PO_WRITE_DATE = "2026-08-21 12:00:00"


def _approval(**overrides: object) -> ApprovalRecord:
    values: dict[str, object] = {
        "decision_id": decision_id_for(
            environment=Environment.DEV,
            case_id=CASE_ID,
            decision_type=DecisionType.APPROVE,
            po_id=PO_ID,
            po_write_date=PO_WRITE_DATE,
        ),
        "case_id": CASE_ID,
        "manager_subject": "manager-001",
        "manager_role": "manager",
        "case_revision": Revision(3),
        "po_id": PO_ID,
        "po_write_date": PO_WRITE_DATE,
        "po_state": "draft",
        "partner_id": 17,
        "currency_id": 1,
        "amount_total": Decimal("312.500000"),
        "offer_id": "offer-101",
        "vendor_id": "17",
        "quantity": Decimal("25.000000"),
        "unit_price": Decimal("12.500000"),
        "currency": "USD",
        "normalized_cost": Decimal("312.500000"),
        "budget_status": "within_budget",
        "budget_amount": Decimal("1000.000000"),
        "confirmed_commitment": Decimal("100.000000"),
        "remaining_before": Decimal("900.000000"),
        "remaining_after": Decimal("587.500000"),
        "overage": Decimal("0.000000"),
        "exception_required": False,
        "budget_exception": False,
        "justification": None,
        "evidence_digest": "sha256:" + "a" * 64,
        "idempotency_key": "approve-001",
        "decided_at": NOW,
        "expires_at": UtcTimestamp(NOW.value + APPROVAL_VALIDITY),
    }
    values.update(overrides)
    return ApprovalRecord(**values)  # type: ignore[arg-type]


def test_approval_binds_exact_facts_and_expires_after_thirty_minutes() -> None:
    record = _approval()

    assert APPROVAL_VALIDITY == timedelta(minutes=30)
    assert record.expires_at == UtcTimestamp(NOW.value + timedelta(minutes=30))
    assert record.quantity == Decimal("25.000000")
    assert record.normalized_cost == Decimal("312.500000")
    assert record.evidence_digest == "sha256:" + "a" * 64
    with pytest.raises(FrozenInstanceError):
        record.vendor_id = "rewritten"  # type: ignore[misc]


@pytest.mark.parametrize("text", ["", "   ", "x" * 281, "bad\x07text"])
def test_decision_text_rejects_blank_oversized_or_control_text(text: str) -> None:
    with pytest.raises(DomainValidationError):
        DecisionText(text)


def test_decision_text_trims_bounded_human_text() -> None:
    assert DecisionText("  Avoid a projected stockout.  ").value == (
        "Avoid a projected stockout."
    )


def test_over_budget_approval_requires_an_explicit_justification() -> None:
    with pytest.raises(DomainValidationError):
        _approval(
            budget_status="exception_required",
            exception_required=True,
            overage=Decimal("25.000000"),
            remaining_after=Decimal("-25.000000"),
        )

    approved = _approval(
        budget_status="exception_required",
        exception_required=True,
        budget_exception=True,
        justification=DecisionText("Avoid a projected stockout."),
        overage=Decimal("25.000000"),
        remaining_after=Decimal("-25.000000"),
    )
    assert approved.justification == DecisionText("Avoid a projected stockout.")


def test_in_budget_approval_rejects_an_invented_exception() -> None:
    with pytest.raises(DomainValidationError):
        _approval(
            budget_exception=True,
            justification=DecisionText("Not needed."),
        )


def test_decision_id_is_environment_and_revision_bound() -> None:
    first = decision_id_for(
        environment=Environment.DEV,
        case_id=CASE_ID,
        decision_type=DecisionType.APPROVE,
        po_id=41,
        po_write_date="2026-08-21 12:00:00",
    )
    changed_revision = decision_id_for(
        environment=Environment.DEV,
        case_id=CASE_ID,
        decision_type=DecisionType.APPROVE,
        po_id=41,
        po_write_date="2026-08-21 12:00:01",
    )
    assert first.environment is Environment.DEV
    assert first != changed_revision
    assert first.value.startswith("decision-")


def test_rejection_is_immutable_audit_evidence_without_authorization_expiry() -> None:
    decision_id = decision_id_for(
        environment=Environment.DEV,
        case_id=CASE_ID,
        decision_type=DecisionType.REJECT,
        po_id=41,
        po_write_date=PO_WRITE_DATE,
    )
    record = RejectionRecord(
        decision_id=decision_id,
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

    assert record.decision_type is DecisionType.REJECT
    assert not hasattr(record, "expires_at")
