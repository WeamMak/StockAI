"""Safe HTTP error handling and request correlation."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from procurement.domain.errors import (
    MAX_CORRELATION_ID_LENGTH,
    MAX_FIELD_ERRORS,
    DomainError,
    DomainValidationError,
    ErrorCode,
    FieldError,
)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_HTTP_STATUS_BY_ERROR_CODE = {
    ErrorCode.AUTH_REQUIRED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ErrorCode.CSRF_INVALID: status.HTTP_403_FORBIDDEN,
    ErrorCode.VALIDATION_FAILED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.REVISION_CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.SCAN_ALREADY_RUNNING: status.HTTP_409_CONFLICT,
    ErrorCode.ODOO_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.MCP_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    ErrorCode.LLM_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.LLM_OUTPUT_INVALID: status.HTTP_502_BAD_GATEWAY,
    ErrorCode.NO_VALID_OFFER: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.APPROVAL_STALE: status.HTTP_409_CONFLICT,
    ErrorCode.BUDGET_JUSTIFICATION_REQUIRED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.RECONCILIATION_REQUIRED: status.HTTP_409_CONFLICT,
}


def _request_id(raw_request_id: str | None) -> str:
    if (
        raw_request_id is not None
        and len(raw_request_id) <= MAX_CORRELATION_ID_LENGTH
        and _REQUEST_ID_PATTERN.fullmatch(raw_request_id) is not None
    ):
        return raw_request_id
    return uuid4().hex


async def add_request_correlation(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Propagate a safe request ID across the HTTP boundary."""

    correlation_id = _request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = correlation_id
    return response


async def domain_error_response(request: Request, error: Exception) -> Response:
    """Translate a domain error into the stable public JSON envelope."""

    if not isinstance(error, DomainError):
        raise TypeError("domain_error_response requires a DomainError")
    envelope = error.to_envelope(correlation_id=request.state.correlation_id)
    return JSONResponse(
        status_code=_HTTP_STATUS_BY_ERROR_CODE[envelope.error_code],
        content={
            "error_code": envelope.error_code.value,
            "message": envelope.message,
            "correlation_id": envelope.correlation_id,
            "retryable": envelope.retryable,
            "field_errors": [
                {"field": field_error.field, "message": field_error.message}
                for field_error in envelope.field_errors
            ],
        },
    )


def _safe_field_errors(error: RequestValidationError) -> tuple[FieldError, ...]:
    field_errors: list[FieldError] = []
    for issue in error.errors()[:MAX_FIELD_ERRORS]:
        location = issue.get("loc", ())
        parts = location[1:] if len(location) > 1 else location
        field_name = ".".join(str(part) for part in parts) or "request"
        try:
            field_errors.append(
                FieldError(field=field_name, message="Value is invalid.")
            )
        except ValueError:
            field_errors.append(
                FieldError(field="request", message="Value is invalid.")
            )
    return tuple(field_errors)


async def request_validation_error_response(
    request: Request,
    error: Exception,
) -> Response:
    """Return framework validation failures without echoing invalid input."""

    if not isinstance(error, RequestValidationError):
        raise TypeError(
            "request_validation_error_response requires a RequestValidationError"
        )
    domain_error = DomainValidationError(
        "The request contains invalid fields.",
        field_errors=_safe_field_errors(error),
    )
    return await domain_error_response(request, domain_error)


def install_error_handling(application: FastAPI) -> None:
    """Install shared request-correlation and error behavior."""

    application.middleware("http")(add_request_correlation)
    application.add_exception_handler(DomainError, domain_error_response)
    application.add_exception_handler(
        RequestValidationError,
        request_validation_error_response,
    )
