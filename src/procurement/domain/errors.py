"""Stable, sanitized errors exposed by the procurement domain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_SAFE_MESSAGE_LENGTH = 256
MAX_FIELD_NAME_LENGTH = 64
MAX_FIELD_ERRORS = 20
MAX_CORRELATION_ID_LENGTH = 128
_FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$", re.ASCII)
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)


class ErrorCode(StrEnum):
    """Machine-readable error codes shared across service boundaries."""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    CSRF_INVALID = "CSRF_INVALID"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    SCAN_ALREADY_RUNNING = "SCAN_ALREADY_RUNNING"
    ODOO_UNAVAILABLE = "ODOO_UNAVAILABLE"
    MCP_TIMEOUT = "MCP_TIMEOUT"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"
    NO_VALID_OFFER = "NO_VALID_OFFER"
    APPROVAL_STALE = "APPROVAL_STALE"
    BUDGET_JUSTIFICATION_REQUIRED = "BUDGET_JUSTIFICATION_REQUIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


_RETRYABLE_ERROR_CODES = frozenset(
    {
        ErrorCode.ODOO_UNAVAILABLE,
        ErrorCode.MCP_TIMEOUT,
        ErrorCode.LLM_UNAVAILABLE,
    }
)


def _contains_unsafe_control_character(value: str) -> bool:
    return any(ord(character) < 32 and character not in "\n\r\t" for character in value)


def _validate_safe_message(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_SAFE_MESSAGE_LENGTH
        or _contains_unsafe_control_character(value)
    ):
        raise ValueError(f"{name} must be bounded safe text")
    return value


@dataclass(frozen=True, slots=True)
class FieldError:
    """Safe validation feedback for one public field."""

    field: str
    message: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.field, str)
            or not 1 <= len(self.field) <= MAX_FIELD_NAME_LENGTH
            or _FIELD_NAME_PATTERN.fullmatch(self.field) is None
        ):
            raise ValueError("field must be a bounded safe field name")
        _validate_safe_message(self.message, name="field error message")


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """Transport-neutral public error response without raw upstream details."""

    error_code: ErrorCode
    message: str
    correlation_id: str
    retryable: bool
    field_errors: tuple[FieldError, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.error_code, ErrorCode):
            raise ValueError("error_code must be an ErrorCode")
        _validate_safe_message(self.message, name="error message")
        if (
            not isinstance(self.correlation_id, str)
            or not 1 <= len(self.correlation_id) <= MAX_CORRELATION_ID_LENGTH
            or _CORRELATION_ID_PATTERN.fullmatch(self.correlation_id) is None
        ):
            raise ValueError("correlation_id must be a bounded safe identifier")
        if type(self.retryable) is not bool:
            raise ValueError("retryable must be a boolean")
        if (
            not isinstance(self.field_errors, tuple)
            or len(self.field_errors) > MAX_FIELD_ERRORS
            or not all(
                isinstance(field_error, FieldError) for field_error in self.field_errors
            )
        ):
            raise ValueError("field_errors must contain at most 20 field errors")


class DomainError(Exception):
    """Base exception containing only safe, stable error information."""

    def __init__(
        self,
        *,
        error_code: ErrorCode,
        safe_message: str,
        field_errors: tuple[FieldError, ...] = (),
    ) -> None:
        if not isinstance(error_code, ErrorCode):
            raise ValueError("error_code must be an ErrorCode")
        _validate_safe_message(safe_message, name="safe message")
        if (
            not isinstance(field_errors, tuple)
            or len(field_errors) > MAX_FIELD_ERRORS
            or not all(
                isinstance(field_error, FieldError) for field_error in field_errors
            )
        ):
            raise ValueError("field_errors must contain at most 20 field errors")
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message
        self.field_errors = field_errors

    @property
    def retryable(self) -> bool:
        """Whether repeating the failed operation is safe after backoff."""

        return self.error_code in _RETRYABLE_ERROR_CODES

    def to_envelope(self, *, correlation_id: str) -> ErrorEnvelope:
        """Create the stable public envelope for this safe domain error."""

        return ErrorEnvelope(
            error_code=self.error_code,
            message=self.safe_message,
            correlation_id=correlation_id,
            retryable=self.retryable,
            field_errors=self.field_errors,
        )


class DomainValidationError(DomainError):
    """Permanent error raised when a domain value cannot be constructed."""

    def __init__(
        self,
        safe_message: str,
        *,
        field_errors: tuple[FieldError, ...] = (),
    ) -> None:
        super().__init__(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message=safe_message,
            field_errors=field_errors,
        )
