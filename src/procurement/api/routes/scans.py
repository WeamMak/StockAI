"""Versioned manual scan creation and polling routes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict

from procurement.api.auth.rbac import require_csrf, require_officer
from procurement.api.services.scans import (
    ScanService,
    ScanSnapshot,
    ScanTrigger,
)

router = APIRouter(
    prefix="/api/v1/scans",
    tags=["scans"],
    dependencies=[Depends(require_officer)],
)
_RESPONSE_CONFIG = ConfigDict(extra="forbid")


class ApprovalReadyResponse(BaseModel):
    """Public walking-skeleton result with no write capability."""

    model_config = _RESPONSE_CONFIG

    outcome: Literal["approval_ready"] = "approval_ready"
    product_id: str
    product_name: str
    rationale: str
    risk_flags: tuple[str, ...]
    read_only: Literal[True] = True


class ScanErrorResponse(BaseModel):
    """Safe terminal failure returned by polling."""

    model_config = _RESPONSE_CONFIG

    error_code: str
    message: str
    retryable: bool
    retry_count: int


class ScanResponse(BaseModel):
    """Public representation of one asynchronous scan."""

    model_config = _RESPONSE_CONFIG

    scan_id: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result: ApprovalReadyResponse | None
    error: ScanErrorResponse | None


class ScanListResponse(BaseModel):
    """Bounded newest-first scan list."""

    model_config = _RESPONSE_CONFIG

    scans: tuple[ScanResponse, ...]


def scan_service_from(request: Request) -> ScanService:
    service = request.app.state.scan_service
    if not isinstance(service, ScanService):  # pragma: no cover - app invariant
        raise RuntimeError("scan service is not configured")
    return service


def scan_response(snapshot: ScanSnapshot) -> ScanResponse:
    """Map an internal snapshot to the filtered public response model."""

    result = (
        ApprovalReadyResponse(
            product_id=snapshot.result.product_id,
            product_name=snapshot.result.product_name,
            rationale=snapshot.result.rationale,
            risk_flags=snapshot.result.risk_flags,
        )
        if snapshot.result is not None
        else None
    )
    error = (
        ScanErrorResponse(
            error_code=snapshot.error.error_code.value,
            message=snapshot.error.message,
            retryable=snapshot.error.retryable,
            retry_count=snapshot.error.retry_count,
        )
        if snapshot.error is not None
        else None
    )
    return ScanResponse(
        scan_id=snapshot.scan_id,
        status=snapshot.status.value,
        trigger=snapshot.trigger.value,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        result=result,
        error=error,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def create_manual_scan(request: Request, response: Response) -> ScanResponse:
    """Schedule an authorized manual scan without holding the request open."""

    snapshot = await scan_service_from(request).start_scan(trigger=ScanTrigger.MANUAL)
    response.headers["Location"] = f"/api/v1/scans/{snapshot.scan_id}"
    return scan_response(snapshot)


@router.get("")
async def list_scans(request: Request) -> ScanListResponse:
    """List the bounded durable walking-skeleton scan history."""

    return ScanListResponse(
        scans=tuple(
            scan_response(snapshot)
            for snapshot in await scan_service_from(request).list_scans()
        )
    )


@router.get("/{scan_id}")
async def get_scan(scan_id: str, request: Request) -> ScanResponse:
    """Return current progress or the terminal result for one scan."""

    return scan_response(await scan_service_from(request).get_scan(scan_id))
