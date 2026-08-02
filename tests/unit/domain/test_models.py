"""Tests for procurement domain value objects."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from procurement.domain.errors import DomainValidationError, ErrorCode
from procurement.domain.identifiers import (
    MAX_IDENTIFIER_LENGTH,
    MAX_REVISION,
    CaseId,
    Environment,
    EvidenceId,
    Revision,
)
from procurement.domain.models import (
    MAX_EVIDENCE_REFERENCES,
    MAX_MANAGER_NOTE_LENGTH,
    CaseEvidence,
    CurrencyCode,
    DateRange,
    EvidenceReference,
    ManagerNote,
    Money,
    Quantity,
    UtcTimestamp,
)


def test_case_id_accepts_a_safe_bounded_value() -> None:
    case_id = CaseId(environment=Environment.DEV, value="case-20260802-0001")

    assert case_id.environment is Environment.DEV
    assert case_id.value == "case-20260802-0001"


def test_identifier_accepts_the_documented_maximum_length() -> None:
    case_id = CaseId(environment=Environment.PROD, value="a" * 128)

    assert MAX_IDENTIFIER_LENGTH == 128
    assert len(case_id.value) == 128


@pytest.mark.parametrize(
    "invalid_value",
    ["", "a" * 129, "case id", "case/id", "case\nsecond-line"],
)
def test_identifier_rejects_empty_unbounded_or_unsafe_values(
    invalid_value: str,
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        CaseId(environment=Environment.DEV, value=invalid_value)

    assert raised.value.error_code is ErrorCode.VALIDATION_FAILED
    assert raised.value.field_errors[0].field == "identifier"


def test_identifier_rejects_an_unknown_environment() -> None:
    with pytest.raises(DomainValidationError) as raised:
        CaseId(environment="qa", value="case-0001")  # type: ignore[arg-type]

    assert raised.value.field_errors[0].field == "environment"


def test_money_preserves_decimal_amount_and_currency() -> None:
    money = Money(amount=Decimal("125.500000"), currency=CurrencyCode("USD"))

    assert money.amount == Decimal("125.500000")
    assert money.currency.value == "USD"


@pytest.mark.parametrize("invalid_code", ["", "US", "EURO", "usd", "U1D"])
def test_currency_rejects_invalid_codes(invalid_code: str) -> None:
    with pytest.raises(DomainValidationError) as raised:
        CurrencyCode(invalid_code)

    assert raised.value.field_errors[0].field == "currency"


def test_money_accepts_zero_and_the_documented_maximum() -> None:
    currency = CurrencyCode("EUR")

    assert Money(Decimal("0"), currency).amount == Decimal("0")
    assert Money(Decimal("999999999999.999999"), currency).amount == Decimal(
        "999999999999.999999"
    )


@pytest.mark.parametrize(
    "invalid_amount",
    [
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.0000001"),
        Decimal("1000000000000"),
    ],
)
def test_money_rejects_negative_non_finite_or_out_of_range_amounts(
    invalid_amount: Decimal,
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        Money(invalid_amount, CurrencyCode("USD"))

    assert raised.value.field_errors[0].field == "amount"


def test_money_rejects_float_arithmetic() -> None:
    with pytest.raises(DomainValidationError):
        Money(10.5, CurrencyCode("USD"))  # type: ignore[arg-type]


def test_quantity_accepts_positive_values_at_its_boundaries() -> None:
    assert Quantity(Decimal("0.000001")).value == Decimal("0.000001")
    assert Quantity(Decimal("999999999.999999")).value == Decimal("999999999.999999")


@pytest.mark.parametrize(
    "invalid_quantity",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.0000001"),
        Decimal("1000000000"),
    ],
)
def test_quantity_rejects_non_positive_non_finite_or_out_of_range_values(
    invalid_quantity: Decimal,
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        Quantity(invalid_quantity)

    assert raised.value.field_errors[0].field == "quantity"


def test_quantity_rejects_float_arithmetic() -> None:
    with pytest.raises(DomainValidationError):
        Quantity(1.5)  # type: ignore[arg-type]


def test_date_range_accepts_ordered_calendar_dates() -> None:
    date_range = DateRange(start=date(2026, 8, 2), end=date(2026, 8, 16))

    assert date_range.start == date(2026, 8, 2)
    assert date_range.end == date(2026, 8, 16)


def test_date_range_rejects_an_end_before_its_start() -> None:
    with pytest.raises(DomainValidationError) as raised:
        DateRange(start=date(2026, 8, 16), end=date(2026, 8, 2))

    assert raised.value.field_errors[0].field == "end"


def test_date_range_rejects_datetime_values() -> None:
    with pytest.raises(DomainValidationError):
        DateRange(
            start=datetime(2026, 8, 2, tzinfo=UTC),
            end=date(2026, 8, 16),
        )


def test_utc_timestamp_accepts_an_aware_utc_datetime() -> None:
    value = datetime(2026, 8, 2, 9, 30, tzinfo=UTC)

    assert UtcTimestamp(value).value == value


@pytest.mark.parametrize(
    "invalid_timestamp",
    [
        datetime(2026, 8, 2, 9, 30),
        datetime(
            2026,
            8,
            2,
            11,
            30,
            tzinfo=timezone(timedelta(hours=2)),
        ),
    ],
)
def test_utc_timestamp_rejects_naive_or_non_utc_datetimes(
    invalid_timestamp: datetime,
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        UtcTimestamp(invalid_timestamp)

    assert raised.value.field_errors[0].field == "timestamp"


def test_manager_note_accepts_bounded_multiline_text() -> None:
    note = ManagerNote("Use the later delivery date.\nRecheck current stock.")

    assert note.value == "Use the later delivery date.\nRecheck current stock."
    assert MAX_MANAGER_NOTE_LENGTH == 2_000


@pytest.mark.parametrize("invalid_note", ["", "   ", "x" * 2_001, "unsafe\x00text"])
def test_manager_note_rejects_empty_unbounded_or_control_text(
    invalid_note: str,
) -> None:
    with pytest.raises(DomainValidationError) as raised:
        ManagerNote(invalid_note)

    assert raised.value.field_errors[0].field == "note"


def test_revision_is_positive_and_increases_explicitly() -> None:
    revision = Revision(1)

    assert revision.value == 1
    assert revision.next() == Revision(2)
    assert MAX_REVISION == 9_223_372_036_854_775_807


@pytest.mark.parametrize("invalid_revision", [0, -1, MAX_REVISION + 1, True, "1"])
def test_revision_rejects_invalid_values(invalid_revision: object) -> None:
    with pytest.raises(DomainValidationError) as raised:
        Revision(invalid_revision)  # type: ignore[arg-type]

    assert raised.value.field_errors[0].field == "revision"


def test_revision_rejects_increment_past_its_maximum() -> None:
    with pytest.raises(DomainValidationError):
        Revision(MAX_REVISION).next()


def test_case_evidence_keeps_references_in_the_case_environment() -> None:
    captured_at = UtcTimestamp(datetime(2026, 8, 2, 9, 30, tzinfo=UTC))
    reference = EvidenceReference(
        evidence_id=EvidenceId(Environment.DEV, "evidence-inventory-001"),
        captured_at=captured_at,
    )

    evidence = CaseEvidence(
        case_id=CaseId(Environment.DEV, "case-001"),
        revision=Revision(1),
        references=(reference,),
    )

    assert evidence.environment is Environment.DEV
    assert evidence.references == (reference,)


def test_case_evidence_rejects_a_cross_environment_reference() -> None:
    reference = EvidenceReference(
        evidence_id=EvidenceId(Environment.PROD, "evidence-offer-001"),
        captured_at=UtcTimestamp(datetime(2026, 8, 2, 9, 30, tzinfo=UTC)),
    )

    with pytest.raises(DomainValidationError) as raised:
        CaseEvidence(
            case_id=CaseId(Environment.DEV, "case-001"),
            revision=Revision(1),
            references=(reference,),
        )

    assert raised.value.field_errors[0].field == "environment"


def test_case_evidence_rejects_an_empty_reference_set() -> None:
    with pytest.raises(DomainValidationError) as raised:
        CaseEvidence(
            case_id=CaseId(Environment.DEV, "case-001"),
            revision=Revision(1),
            references=(),
        )

    assert raised.value.field_errors[0].field == "references"


def test_case_evidence_rejects_more_than_one_hundred_references() -> None:
    captured_at = UtcTimestamp(datetime(2026, 8, 2, 9, 30, tzinfo=UTC))
    references = tuple(
        EvidenceReference(
            evidence_id=EvidenceId(Environment.DEV, f"evidence-{index:03d}"),
            captured_at=captured_at,
        )
        for index in range(101)
    )

    with pytest.raises(DomainValidationError):
        CaseEvidence(
            case_id=CaseId(Environment.DEV, "case-001"),
            revision=Revision(1),
            references=references,
        )

    assert MAX_EVIDENCE_REFERENCES == 100
