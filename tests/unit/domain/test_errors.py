"""Tests for stable, sanitized domain errors."""

import pytest

from procurement.domain.errors import (
    MAX_FIELD_ERRORS,
    MAX_SAFE_MESSAGE_LENGTH,
    DomainError,
    DomainValidationError,
    ErrorCode,
    FieldError,
)


def test_error_codes_match_the_approved_public_contract() -> None:
    assert {code.value for code in ErrorCode} == {
        "AUTH_REQUIRED",
        "FORBIDDEN",
        "CSRF_INVALID",
        "VALIDATION_FAILED",
        "REVISION_CONFLICT",
        "SCAN_ALREADY_RUNNING",
        "ODOO_UNAVAILABLE",
        "MCP_TIMEOUT",
        "LLM_UNAVAILABLE",
        "LLM_OUTPUT_INVALID",
        "NO_VALID_OFFER",
        "PREFERENCE_INVALID",
        "APPROVAL_STALE",
        "BUDGET_JUSTIFICATION_REQUIRED",
        "RECONCILIATION_REQUIRED",
        "REFINEMENT_LIMIT_REACHED",
    }


@pytest.mark.parametrize(
    "error_code",
    [ErrorCode.ODOO_UNAVAILABLE, ErrorCode.MCP_TIMEOUT, ErrorCode.LLM_UNAVAILABLE],
)
def test_transient_dependency_errors_are_retryable(error_code: ErrorCode) -> None:
    error = DomainError(
        error_code=error_code,
        safe_message="A required dependency is temporarily unavailable.",
    )

    assert error.retryable is True


@pytest.mark.parametrize(
    "error_code",
    [
        ErrorCode.AUTH_REQUIRED,
        ErrorCode.FORBIDDEN,
        ErrorCode.CSRF_INVALID,
        ErrorCode.VALIDATION_FAILED,
        ErrorCode.REVISION_CONFLICT,
        ErrorCode.SCAN_ALREADY_RUNNING,
        ErrorCode.LLM_OUTPUT_INVALID,
        ErrorCode.NO_VALID_OFFER,
        ErrorCode.PREFERENCE_INVALID,
        ErrorCode.APPROVAL_STALE,
        ErrorCode.BUDGET_JUSTIFICATION_REQUIRED,
        ErrorCode.RECONCILIATION_REQUIRED,
    ],
)
def test_permanent_policy_or_final_errors_are_not_retryable(
    error_code: ErrorCode,
) -> None:
    error = DomainError(
        error_code=error_code,
        safe_message="The request cannot be retried safely.",
    )

    assert error.retryable is False


def test_domain_error_builds_a_safe_stable_envelope() -> None:
    error = DomainValidationError(
        "The request contains invalid fields.",
        field_errors=(
            FieldError(field="quantity", message="Quantity must be positive."),
        ),
    )

    envelope = error.to_envelope(correlation_id="request-20260802-0001")

    assert envelope.error_code is ErrorCode.VALIDATION_FAILED
    assert envelope.message == "The request contains invalid fields."
    assert envelope.correlation_id == "request-20260802-0001"
    assert envelope.retryable is False
    assert envelope.field_errors == error.field_errors


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("", "Safe message."),
        ("invalid field", "Safe message."),
        ("x" * 65, "Safe message."),
        ("quantity", ""),
        ("quantity", "x" * 257),
        ("quantity", "unsafe\x00message"),
    ],
)
def test_field_error_rejects_unbounded_or_unsafe_text(
    field: str,
    message: str,
) -> None:
    with pytest.raises(ValueError):
        FieldError(field=field, message=message)


def test_domain_error_rejects_an_unbounded_safe_message() -> None:
    with pytest.raises(ValueError):
        DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="x" * 257,
        )

    assert MAX_SAFE_MESSAGE_LENGTH == 256


def test_domain_error_rejects_too_many_field_errors() -> None:
    field_errors = tuple(
        FieldError(field=f"field_{index}", message="Invalid value.")
        for index in range(21)
    )

    with pytest.raises(ValueError):
        DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="Too many invalid fields.",
            field_errors=field_errors,
        )

    assert MAX_FIELD_ERRORS == 20


@pytest.mark.parametrize(
    "invalid_correlation_id",
    ["", "request id", "request/id", "request\nsecond-line", "x" * 129],
)
def test_error_envelope_rejects_an_invalid_correlation_id(
    invalid_correlation_id: str,
) -> None:
    error = DomainValidationError("The request is invalid.")

    with pytest.raises(ValueError):
        error.to_envelope(correlation_id=invalid_correlation_id)
