"""Framework-independent procurement value objects."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Self

from procurement.domain.errors import DomainValidationError, FieldError
from procurement.domain.identifiers import CaseId, Environment, EvidenceId, Revision

MAX_MONEY_AMOUNT = Decimal("999999999999.999999")
MAX_QUANTITY = Decimal("999999999.999999")
MAX_MANAGER_NOTE_LENGTH = 2_000
MAX_EVIDENCE_REFERENCES = 100
_DECIMAL_QUANTUM = Decimal("0.000001")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class CurrencyCode:
    """Three-letter currency code used for procurement amounts."""

    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or _CURRENCY_PATTERN.fullmatch(self.value) is None
        ):
            raise DomainValidationError(
                "The currency is invalid.",
                field_errors=(
                    FieldError(
                        field="currency",
                        message="Currency must be three uppercase ASCII letters.",
                    ),
                ),
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Money:
    """An exact, non-negative amount in one currency."""

    amount: Decimal
    currency: CurrencyCode

    def __post_init__(self) -> None:
        amount_is_valid = (
            isinstance(self.amount, Decimal)
            and self.amount.is_finite()
            and Decimal("0") <= self.amount <= MAX_MONEY_AMOUNT
            and self.amount.quantize(_DECIMAL_QUANTUM) == self.amount
        )
        if not amount_is_valid:
            raise DomainValidationError(
                "The monetary amount is invalid.",
                field_errors=(
                    FieldError(
                        field="amount",
                        message=(
                            "Amount must be a finite, non-negative Decimal less than "
                            "1 trillion, with at most six fractional digits."
                        ),
                    ),
                ),
            )

        if not isinstance(self.currency, CurrencyCode):
            raise DomainValidationError(
                "The currency is invalid.",
                field_errors=(
                    FieldError(
                        field="currency",
                        message="Currency must be a CurrencyCode.",
                    ),
                ),
            )


@dataclass(frozen=True, slots=True)
class Quantity:
    """An exact, positive procurement quantity."""

    value: Decimal

    def __post_init__(self) -> None:
        value_is_valid = (
            isinstance(self.value, Decimal)
            and self.value.is_finite()
            and Decimal("0") < self.value <= MAX_QUANTITY
            and self.value.quantize(_DECIMAL_QUANTUM) == self.value
        )
        if not value_is_valid:
            raise DomainValidationError(
                "The quantity is invalid.",
                field_errors=(
                    FieldError(
                        field="quantity",
                        message=(
                            "Quantity must be a finite, positive Decimal less than "
                            "1 billion, with at most six fractional digits."
                        ),
                    ),
                ),
            )


@dataclass(frozen=True, slots=True)
class DateRange:
    """An inclusive ordered range of calendar dates."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if type(self.start) is not date:
            raise DomainValidationError(
                "The start date is invalid.",
                field_errors=(
                    FieldError(field="start", message="Start must be a date."),
                ),
            )
        if type(self.end) is not date or self.end < self.start:
            raise DomainValidationError(
                "The end date is invalid.",
                field_errors=(
                    FieldError(
                        field="end",
                        message="End must be a date on or after start.",
                    ),
                ),
            )


@dataclass(frozen=True, slots=True)
class UtcTimestamp:
    """A timezone-aware timestamp with a zero UTC offset."""

    value: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, datetime)
            or self.value.tzinfo is None
            or self.value.utcoffset() != timedelta(0)
        ):
            raise DomainValidationError(
                "The timestamp is invalid.",
                field_errors=(
                    FieldError(
                        field="timestamp",
                        message="Timestamp must be timezone-aware UTC.",
                    ),
                ),
            )

    @classmethod
    def from_value(cls, value: str) -> Self:
        """Parse the repository's ISO-8601 UTC representation."""

        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise DomainValidationError(
                "The timestamp is invalid.",
                field_errors=(
                    FieldError(
                        field="timestamp",
                        message="Timestamp must be ISO-8601 UTC.",
                    ),
                ),
            ) from error
        return cls(parsed)


@dataclass(frozen=True, slots=True)
class ManagerNote:
    """Bounded, untrusted free text supplied with a manager decision."""

    value: str

    def __post_init__(self) -> None:
        contains_control_character = isinstance(self.value, str) and any(
            ord(character) < 32 and character not in "\n\r\t"
            for character in self.value
        )
        if (
            not isinstance(self.value, str)
            or not self.value.strip()
            or len(self.value) > MAX_MANAGER_NOTE_LENGTH
            or contains_control_character
        ):
            raise DomainValidationError(
                "The manager note is invalid.",
                field_errors=(
                    FieldError(
                        field="note",
                        message=(
                            "Note must contain 1 to 2,000 characters. "
                            "Use normal text only."
                        ),
                    ),
                ),
            )


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Environment-bound pointer to evidence captured at a known time."""

    evidence_id: EvidenceId
    captured_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, EvidenceId):
            raise DomainValidationError(
                "The evidence identifier is invalid.",
                field_errors=(
                    FieldError(
                        field="evidence_id",
                        message="Evidence identifier must be an EvidenceId.",
                    ),
                ),
            )
        if not isinstance(self.captured_at, UtcTimestamp):
            raise DomainValidationError(
                "The evidence timestamp is invalid.",
                field_errors=(
                    FieldError(
                        field="captured_at",
                        message="Evidence capture time must be a UTC timestamp.",
                    ),
                ),
            )

    @property
    def environment(self) -> Environment:
        return self.evidence_id.environment


@dataclass(frozen=True, slots=True)
class CaseEvidence:
    """Bounded evidence snapshot attached to one exact case revision."""

    case_id: CaseId
    revision: Revision
    references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, CaseId):
            raise DomainValidationError(
                "The case identifier is invalid.",
                field_errors=(
                    FieldError(field="case_id", message="Case ID must be a CaseId."),
                ),
            )
        if not isinstance(self.revision, Revision):
            raise DomainValidationError(
                "The revision is invalid.",
                field_errors=(
                    FieldError(
                        field="revision",
                        message="Revision must be a Revision.",
                    ),
                ),
            )
        if (
            not isinstance(self.references, tuple)
            or not 1 <= len(self.references) <= MAX_EVIDENCE_REFERENCES
            or not all(
                isinstance(reference, EvidenceReference)
                for reference in self.references
            )
        ):
            raise DomainValidationError(
                "The evidence references are invalid.",
                field_errors=(
                    FieldError(
                        field="references",
                        message="A case requires 1 to 100 evidence references.",
                    ),
                ),
            )
        if any(
            reference.environment is not self.case_id.environment
            for reference in self.references
        ):
            raise DomainValidationError(
                "Evidence belongs to a different environment.",
                field_errors=(
                    FieldError(
                        field="environment",
                        message="Evidence environment must match the case.",
                    ),
                ),
            )

    @property
    def environment(self) -> Environment:
        return self.case_id.environment
