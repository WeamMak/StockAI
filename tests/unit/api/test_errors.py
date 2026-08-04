"""Safe API error and request-correlation behavior."""

import pytest
from httpx2 import ASGITransport, AsyncClient

from procurement.api.app import create_app
from procurement.domain.errors import (
    DomainError,
    DomainValidationError,
    ErrorCode,
    FieldError,
)


@pytest.mark.anyio
async def test_safe_request_id_is_propagated_to_the_response() -> None:
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/health/live",
            headers={"X-Request-ID": "request-20260802-0001"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-20260802-0001"


@pytest.mark.anyio
async def test_domain_error_uses_the_safe_public_envelope() -> None:
    application = create_app()

    @application.get("/test/domain-error")
    async def raise_domain_error() -> None:
        raise DomainValidationError(
            "The request contains invalid fields.",
            field_errors=(
                FieldError(field="quantity", message="Quantity must be positive."),
            ),
        )

    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/test/domain-error",
            headers={"X-Request-ID": "request-20260802-0002"},
        )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "request-20260802-0002"
    assert response.json() == {
        "error_code": "VALIDATION_FAILED",
        "message": "The request contains invalid fields.",
        "correlation_id": "request-20260802-0002",
        "retryable": False,
        "field_errors": [
            {
                "field": "quantity",
                "message": "Quantity must be positive.",
            }
        ],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_code", "expected_status", "expected_retryable"),
    [
        (ErrorCode.AUTH_REQUIRED, 401, False),
        (ErrorCode.FORBIDDEN, 403, False),
        (ErrorCode.CSRF_INVALID, 403, False),
        (ErrorCode.VALIDATION_FAILED, 422, False),
        (ErrorCode.REVISION_CONFLICT, 409, False),
        (ErrorCode.SCAN_ALREADY_RUNNING, 409, False),
        (ErrorCode.ODOO_UNAVAILABLE, 503, True),
        (ErrorCode.MCP_TIMEOUT, 504, True),
        (ErrorCode.LLM_UNAVAILABLE, 503, True),
        (ErrorCode.LLM_OUTPUT_INVALID, 502, False),
        (ErrorCode.NO_VALID_OFFER, 422, False),
        (ErrorCode.APPROVAL_STALE, 409, False),
        (ErrorCode.BUDGET_JUSTIFICATION_REQUIRED, 422, False),
        (ErrorCode.RECONCILIATION_REQUIRED, 409, False),
    ],
)
async def test_domain_error_codes_have_stable_http_semantics(
    error_code: ErrorCode,
    expected_status: int,
    expected_retryable: bool,
) -> None:
    application = create_app()

    @application.get("/test/domain-error-code")
    async def raise_domain_error() -> None:
        raise DomainError(
            error_code=error_code,
            safe_message="The request could not be completed.",
        )

    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/test/domain-error-code")

    assert response.status_code == expected_status
    assert response.json()["error_code"] == error_code.value
    assert response.json()["retryable"] is expected_retryable


@pytest.mark.anyio
async def test_request_validation_uses_safe_field_errors() -> None:
    application = create_app()

    @application.get("/test/validation")
    async def validated_endpoint(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/test/validation?quantity=not-a-number",
            headers={"X-Request-ID": "request-20260802-0003"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error_code": "VALIDATION_FAILED",
        "message": "The request contains invalid fields.",
        "correlation_id": "request-20260802-0003",
        "retryable": False,
        "field_errors": [
            {
                "field": "quantity",
                "message": "Value is invalid.",
            }
        ],
    }
    assert "not-a-number" not in response.text
