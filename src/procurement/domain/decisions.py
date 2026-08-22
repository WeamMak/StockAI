"""Immutable manager decisions bound to one exact procurement draft revision."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from procurement.domain.errors import DomainValidationError, FieldError
from procurement.domain.identifiers import (
    CaseId,
    Environment,
    EnvironmentBoundIdentifier,
    Revision,
)
from procurement.domain.models import UtcTimestamp

APPROVAL_VALIDITY = timedelta(minutes=30)
MAX_DECISION_TEXT_LENGTH = 280
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)
_BUDGET_STATUSES = frozenset(
    {"within_budget", "exception_required", "unavailable", "not_evaluated"}
)


class DecisionId(EnvironmentBoundIdentifier):
    """Stable environment-bound identity for one immutable manager decision."""

    __slots__ = ()


class DecisionType(StrEnum):
    """The only manager decisions supported by the MVP."""

    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DecisionText:
    """Bounded untrusted human text retained only for authorized audit."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip() if isinstance(self.value, str) else ""
        if (
            not normalized
            or len(normalized) > MAX_DECISION_TEXT_LENGTH
            or any(ord(character) < 32 for character in normalized)
        ):
            raise _validation_error(
                "manager_text",
                "Manager text must be 1 to 280 characters without controls.",
            )
        object.__setattr__(self, "value", normalized)


def decision_id_for(
    *,
    environment: Environment,
    case_id: CaseId,
    decision_type: DecisionType,
    po_id: int,
    po_write_date: str,
) -> DecisionId:
    """Derive a stable opaque identifier without incorporating human text."""

    if not isinstance(environment, Environment) or not isinstance(case_id, CaseId):
        raise _validation_error("case_id", "Use an environment-bound CaseId.")
    if case_id.environment is not environment:
        raise _validation_error("environment", "Case and decision must match.")
    if not isinstance(decision_type, DecisionType):
        raise _validation_error("decision_type", "Use approve or reject.")
    _validate_positive_int(po_id, field="po_id")
    _validate_bounded_text(po_write_date, field="po_write_date")
    source = "\x1f".join(
        (
            environment.value,
            case_id.value,
            decision_type.value,
            str(po_id),
            po_write_date,
        )
    ).encode("utf-8")
    return DecisionId(environment, f"decision-{hashlib.sha256(source).hexdigest()}")


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """One immutable authorization to confirm an exact current draft."""

    decision_id: DecisionId
    case_id: CaseId
    manager_subject: str
    manager_role: str
    case_revision: Revision
    po_id: int
    po_write_date: str
    po_state: str
    partner_id: int
    currency_id: int
    amount_total: Decimal
    offer_id: str
    vendor_id: str
    quantity: Decimal
    unit_price: Decimal
    currency: str
    normalized_cost: Decimal
    budget_status: str
    budget_amount: Decimal
    confirmed_commitment: Decimal
    remaining_before: Decimal
    remaining_after: Decimal
    overage: Decimal
    exception_required: bool
    budget_exception: bool
    justification: DecisionText | None
    evidence_digest: str
    idempotency_key: str
    decided_at: UtcTimestamp
    expires_at: UtcTimestamp

    @property
    def decision_type(self) -> DecisionType:
        return DecisionType.APPROVE

    def __post_init__(self) -> None:
        _validate_common(self)
        for name in ("amount_total", "quantity", "unit_price", "normalized_cost"):
            _validate_decimal(getattr(self, name), field=name, positive=True)
        for name in (
            "budget_amount",
            "confirmed_commitment",
            "remaining_before",
            "remaining_after",
            "overage",
        ):
            _validate_decimal(
                getattr(self, name),
                field=name,
                positive=False,
                nonnegative=name != "remaining_after",
            )
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise _validation_error("currency", "Use a three-letter currency code.")
        if self.budget_status not in _BUDGET_STATUSES:
            raise _validation_error("budget_status", "Use a known budget status.")
        if (
            type(self.exception_required) is not bool
            or type(self.budget_exception) is not bool
        ):
            raise _validation_error("budget_exception", "Use boolean exception flags.")
        if self.exception_required:
            if not self.budget_exception or not isinstance(
                self.justification, DecisionText
            ):
                raise _validation_error(
                    "justification",
                    "An over-budget approval requires an explicit justification.",
                )
        elif self.budget_exception or self.justification is not None:
            raise _validation_error(
                "budget_exception",
                "An in-budget approval cannot add a budget exception.",
            )
        if self.expires_at.value != self.decided_at.value + APPROVAL_VALIDITY:
            raise _validation_error(
                "expires_at", "Approval authorization must expire after 30 minutes."
            )


@dataclass(frozen=True, slots=True)
class RejectionRecord:
    """One immutable rejection authorizing cancellation of an exact draft."""

    decision_id: DecisionId
    case_id: CaseId
    manager_subject: str
    manager_role: str
    case_revision: Revision
    po_id: int
    po_write_date: str
    po_state: str
    partner_id: int
    currency_id: int
    amount_total: Decimal
    reason: DecisionText
    evidence_digest: str
    idempotency_key: str
    decided_at: UtcTimestamp

    @property
    def decision_type(self) -> DecisionType:
        return DecisionType.REJECT

    def __post_init__(self) -> None:
        _validate_common(self)
        _validate_decimal(self.amount_total, field="amount_total", positive=True)
        if not isinstance(self.reason, DecisionText):
            raise _validation_error("reason", "Use bounded manager text.")


DecisionRecord = ApprovalRecord | RejectionRecord


def _validate_common(record: ApprovalRecord | RejectionRecord) -> None:
    if not isinstance(record.decision_id, DecisionId):
        raise _validation_error("decision_id", "Use a DecisionId.")
    if not isinstance(record.case_id, CaseId):
        raise _validation_error("case_id", "Use a CaseId.")
    if record.decision_id.environment is not record.case_id.environment:
        raise _validation_error("environment", "Decision and case must match.")
    expected = decision_id_for(
        environment=record.case_id.environment,
        case_id=record.case_id,
        decision_type=record.decision_type,
        po_id=record.po_id,
        po_write_date=record.po_write_date,
    )
    if record.decision_id != expected:
        raise _validation_error("decision_id", "Decision binding is invalid.")
    if not isinstance(record.case_revision, Revision):
        raise _validation_error("case_revision", "Use a Revision.")
    for field in (
        "manager_subject",
        "manager_role",
        "po_write_date",
        "po_state",
        "idempotency_key",
    ):
        _validate_bounded_text(getattr(record, field), field=field)
    if record.manager_role != "manager":
        raise _validation_error("manager_role", "Only a manager may decide.")
    for field in ("po_id", "partner_id", "currency_id"):
        _validate_positive_int(getattr(record, field), field=field)
    if not isinstance(record.decided_at, UtcTimestamp):
        raise _validation_error("decided_at", "Use a UTC timestamp.")
    if _DIGEST_PATTERN.fullmatch(record.evidence_digest) is None:
        raise _validation_error("evidence_digest", "Use a lowercase SHA-256 digest.")
    if isinstance(record, ApprovalRecord):
        for field in ("offer_id", "vendor_id"):
            _validate_identifier(getattr(record, field), field=field)


def _validate_identifier(value: object, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or _SAFE_IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise _validation_error(field, f"{field} must be bounded safe identifier text.")


def _validate_bounded_text(value: object, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or any(ord(character) < 32 for character in value)
    ):
        raise _validation_error(field, f"{field} must be bounded safe text.")


def _validate_positive_int(value: object, *, field: str) -> None:
    if type(value) is not int or value <= 0:
        raise _validation_error(field, f"{field} must be a positive integer.")


def _validate_decimal(
    value: object,
    *,
    field: str,
    positive: bool,
    nonnegative: bool = True,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _validation_error(field, f"{field} must be an exact Decimal.")
    if positive and value <= 0:
        raise _validation_error(field, f"{field} must be positive.")
    if nonnegative and value < 0:
        raise _validation_error(field, f"{field} must not be negative.")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -6:
        raise _validation_error(field, f"{field} supports at most six decimals.")


def _validation_error(field: str, message: str) -> DomainValidationError:
    return DomainValidationError(
        "The manager decision is invalid.",
        field_errors=(FieldError(field=field, message=message),),
    )
