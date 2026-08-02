"""Procurement case states and approved transitions."""

from enum import StrEnum

from procurement.domain.errors import DomainValidationError, FieldError


class CaseState(StrEnum):
    """Stable serialized states for one procurement case."""

    DETECTED = "detected"
    SKIPPED = "skipped"
    GATHERING_EVIDENCE = "gathering_evidence"
    MANUAL_REVIEW = "manual_review"
    PENDING_APPROVAL = "pending_approval"
    CHANGE_REQUESTED = "change_requested"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    APPROVED = "approved"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


_ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.DETECTED: frozenset({CaseState.SKIPPED, CaseState.GATHERING_EVIDENCE}),
    CaseState.GATHERING_EVIDENCE: frozenset({CaseState.MANUAL_REVIEW, CaseState.PENDING_APPROVAL}),
    CaseState.PENDING_APPROVAL: frozenset({CaseState.CHANGE_REQUESTED, CaseState.REJECTED, CaseState.APPROVED}),
    CaseState.CHANGE_REQUESTED: frozenset({CaseState.GATHERING_EVIDENCE}),
    CaseState.REJECTED: frozenset({CaseState.CANCELLED, CaseState.RECONCILIATION_REQUIRED}),
    CaseState.APPROVED: frozenset({CaseState.CONFIRMING}),
    CaseState.CONFIRMING: frozenset({CaseState.CONFIRMED, CaseState.RECONCILIATION_REQUIRED}),
    CaseState.MANUAL_REVIEW: frozenset({CaseState.GATHERING_EVIDENCE}),
    CaseState.RECONCILIATION_REQUIRED: frozenset({CaseState.CONFIRMED, CaseState.MANUAL_REVIEW}),
    CaseState.SKIPPED: frozenset(),
    CaseState.CANCELLED: frozenset(),
    CaseState.CONFIRMED: frozenset(),
}


def parse_case_state(value: object) -> CaseState:
    """Parse a stable state value without accepting unknown strings."""

    if isinstance(value, CaseState):
        return value
    if isinstance(value, str):
        try:
            return CaseState(value)
        except ValueError:
            pass
    raise DomainValidationError(
        "The case state is invalid.",
        field_errors=(
            FieldError(field="state", message="State must be a known case state."),
        ),
    )


def transition_case(current: CaseState, target: CaseState) -> CaseState:
    """Return the target when the approved state graph allows the transition."""

    if not isinstance(current, CaseState) or not isinstance(target, CaseState):
        raise DomainValidationError(
            "The case state is invalid.",
            field_errors=(
                FieldError(field="state", message="State must be a CaseState."),
            ),
        )
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise DomainValidationError(
            "The case transition is not allowed.",
            field_errors=(
                FieldError(
                    field="state",
                    message="The requested state transition is not allowed.",
                ),
            ),
        )
    return target
