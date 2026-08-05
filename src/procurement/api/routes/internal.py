"""Internal scan trigger protected by a dedicated Cron credential."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response, status

from procurement.api.routes.scans import (
    ScanResponse,
    scan_response,
    scan_service_from,
)
from procurement.api.services.scans import ScanTrigger
from procurement.domain.errors import DomainError, ErrorCode

router = APIRouter(prefix="/internal/v1", tags=["internal"])


def _authorize_cron(request: Request) -> None:
    """Validate only the environment-scoped Cron bearer credential."""

    authorization = request.headers.get("Authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise DomainError(
            error_code=ErrorCode.AUTH_REQUIRED,
            safe_message="Cron authentication is required.",
        )
    supplied = authorization.removeprefix("Bearer ")
    expected = request.app.state.settings.cron_token
    if (
        not isinstance(expected, str)
        or not supplied
        or not supplied.isascii()
        or len(supplied) > 256
        or any(character.isspace() for character in supplied)
        or not secrets.compare_digest(supplied, expected)
    ):
        raise DomainError(
            error_code=ErrorCode.FORBIDDEN,
            safe_message="The Cron credential is not authorized.",
        )


@router.post("/scans", status_code=status.HTTP_202_ACCEPTED)
async def create_internal_scan(request: Request, response: Response) -> ScanResponse:
    """Schedule the same scan workflow using only the Cron authority."""

    _authorize_cron(request)
    snapshot = await scan_service_from(request).start_scan(trigger=ScanTrigger.CRON)
    response.headers["Location"] = f"/api/v1/scans/{snapshot.scan_id}"
    return scan_response(snapshot)
