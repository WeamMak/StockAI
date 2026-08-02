"""Tests for the approved procurement case state machine."""

import pytest

from procurement.domain.errors import DomainValidationError, ErrorCode
from procurement.domain.states import CaseState, parse_case_state, transition_case


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CaseState.DETECTED, CaseState.SKIPPED),
        (CaseState.DETECTED, CaseState.GATHERING_EVIDENCE),
        (CaseState.GATHERING_EVIDENCE, CaseState.MANUAL_REVIEW),
        (CaseState.GATHERING_EVIDENCE, CaseState.PENDING_APPROVAL),
        (CaseState.PENDING_APPROVAL, CaseState.CHANGE_REQUESTED),
        (CaseState.CHANGE_REQUESTED, CaseState.GATHERING_EVIDENCE),
        (CaseState.PENDING_APPROVAL, CaseState.REJECTED),
        (CaseState.REJECTED, CaseState.CANCELLED),
        (CaseState.REJECTED, CaseState.RECONCILIATION_REQUIRED),
        (CaseState.PENDING_APPROVAL, CaseState.APPROVED),
        (CaseState.APPROVED, CaseState.CONFIRMING),
        (CaseState.CONFIRMING, CaseState.CONFIRMED),
        (CaseState.CONFIRMING, CaseState.RECONCILIATION_REQUIRED),
        (CaseState.MANUAL_REVIEW, CaseState.GATHERING_EVIDENCE),
        (CaseState.RECONCILIATION_REQUIRED, CaseState.CONFIRMED),
        (CaseState.RECONCILIATION_REQUIRED, CaseState.MANUAL_REVIEW),
    ],
)
def test_approved_case_state_transitions_succeed(
    current: CaseState,
    target: CaseState,
) -> None:
    assert transition_case(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CaseState.DETECTED, CaseState.CONFIRMED),
        (CaseState.PENDING_APPROVAL, CaseState.CONFIRMING),
        (CaseState.GATHERING_EVIDENCE, CaseState.GATHERING_EVIDENCE),
        (CaseState.SKIPPED, CaseState.DETECTED),
        (CaseState.CANCELLED, CaseState.GATHERING_EVIDENCE),
        (CaseState.CONFIRMED, CaseState.MANUAL_REVIEW),
    ],
)
def test_unapproved_or_terminal_state_transitions_are_rejected(
    current: CaseState,
    target: CaseState,
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        transition_case(current, target)

    assert raised.value.error_code is ErrorCode.VALIDATION_FAILED
    assert raised.value.safe_message == "The case transition is not allowed."
    assert raised.value.field_errors[0].field == "state"


def test_case_state_parses_its_stable_serialized_value() -> None:
    assert parse_case_state("pending_approval") is CaseState.PENDING_APPROVAL


@pytest.mark.parametrize("unknown_state", ["unknown", "PendingApproval", "", 1])
def test_unknown_case_states_are_rejected(unknown_state: object) -> None:
    with pytest.raises(DomainValidationError) as raised:
        parse_case_state(unknown_state)

    assert raised.value.field_errors[0].field == "state"


def test_transition_rejects_values_that_are_not_case_states() -> None:
    with pytest.raises(DomainValidationError):
        transition_case("detected", CaseState.SKIPPED)  # type: ignore[arg-type]
