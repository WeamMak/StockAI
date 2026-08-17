"""Read-only procurement-case evidence routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from procurement.api.auth.rbac import require_officer
from procurement.api.routes.scans import scan_service_from

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


@router.get("/{case_id}")
async def get_case(case_id: str, request: Request) -> CaseEvidenceResponse:
    """Return immutable deterministic evidence, including skipped reasons."""

    snapshot = await scan_service_from(request).get_case(case_id)
    return CaseEvidenceResponse(
        case_id=snapshot.case_id,
        status=snapshot.status.value,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
    )
