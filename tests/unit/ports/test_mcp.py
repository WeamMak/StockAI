"""Consumer-side manager action results remain strictly bounded."""

import pytest

from procurement.domain.decisions import DecisionType
from procurement.ports.mcp import DecisionOutcome


def test_confirmed_decision_outcome_is_typed_and_bound() -> None:
    outcome = DecisionOutcome.confirmed(
        decision_id="decision-abc",
        po_id=41,
        po_reference="P00041",
        write_date="2026-08-21 12:01:00",
        reconciled=False,
    )

    assert outcome.decision_type is DecisionType.APPROVE
    assert outcome.outcome == "confirmed"
    assert outcome.odoo_state == "purchase"


def test_decision_outcome_rejects_mismatched_terminal_state() -> None:
    with pytest.raises(ValueError):
        DecisionOutcome(
            decision_id="decision-abc",
            decision_type=DecisionType.APPROVE,
            outcome="confirmed",
            po_id=41,
            po_reference="P00041",
            write_date="2026-08-21 12:01:00",
            odoo_state="cancel",
            reconciled=False,
        )
