"""Read-only procurement-case evidence and cross-scan listing routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from procurement.api.auth.rbac import require_officer
from procurement.api.routes.scans import (
    CaseSummaryResponse,
    case_summary_response,
    scan_service_from,
)

router = APIRouter(
    prefix="/api/v1/cases",
    tags=["cases"],
    dependencies=[Depends(require_officer)],
)


class CaseEvidenceResponse(BaseModel):
    """Bounded authoritative evidence for one scan-created case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: str
    evidence: tuple[dict[str, object], ...]


class RecentCasesResponse(BaseModel):
    """Bounded newest-first list of cases spanning every scan."""

    model_config = ConfigDict(extra="forbid")

    cases: tuple[CaseSummaryResponse, ...]


@router.get("")
async def list_recent_cases(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> RecentCasesResponse:
    """Return the most recent cases across every scan, newest first."""

    service = scan_service_from(request)
    summaries = await service.list_recent_cases(limit=limit)
    return RecentCasesResponse(
        cases=tuple(case_summary_response(row) for row in summaries)
    )


@router.get("/{case_id}")
async def get_case(case_id: str, request: Request) -> CaseEvidenceResponse:
    """Return immutable deterministic evidence, including skipped reasons."""

    snapshot = await scan_service_from(request).get_case(case_id)
    return CaseEvidenceResponse(
        case_id=snapshot.case_id,
        status=snapshot.status.value,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
    )
