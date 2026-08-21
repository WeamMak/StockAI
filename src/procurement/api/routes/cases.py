"""Read-only procurement-case evidence and cross-scan listing routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from procurement.api.auth.rbac import require_officer
from procurement.api.routes.scans import (
    CaseSummaryResponse,
    case_summary_response,
    scan_service_from,
)
from procurement.domain.decisions import ApprovalRecord, DecisionId, RejectionRecord
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId
from procurement.ports.repositories import ApplicationRepository

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


class AuditEventResponse(BaseModel):
    """Authorized immutable case event with optional joined decision text."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    actor_id: str
    occurred_at: datetime
    correlation_id: str
    source_revision: int
    outcome: str
    evidence_digest: str | None
    decision_id: str | None
    decision_type: Literal["approve", "reject"] | None
    justification: str | None
    reason: str | None


class CaseAuditResponse(BaseModel):
    """At most 100 deterministic oldest-first events for one case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    events: tuple[AuditEventResponse, ...]


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


@router.get("/{case_id}/audit")
async def get_case_audit(case_id: str, request: Request) -> CaseAuditResponse:
    """Return the bounded audit timeline and authorized manager text."""

    environment = request.app.state.settings.environment
    try:
        identifier = CaseId(environment, case_id)
    except DomainError:
        raise _case_not_found() from None
    repository: ApplicationRepository = request.app.state.application_repository
    if await repository.get_case(identifier) is None:
        raise _case_not_found()
    responses: list[AuditEventResponse] = []
    for event in await repository.list_audit(identifier, limit=100):
        decision = None
        if event.decision_id is not None:
            try:
                decision = await repository.get_decision(
                    DecisionId(environment, event.decision_id)
                )
            except (DomainError, ValueError):
                decision = None
        responses.append(
            AuditEventResponse(
                event_id=event.event_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                occurred_at=event.occurred_at.value,
                correlation_id=event.correlation_id,
                source_revision=event.source_revision.value,
                outcome=event.outcome,
                evidence_digest=event.evidence_digest,
                decision_id=event.decision_id,
                decision_type=(
                    decision.decision_type.value if decision is not None else None
                ),
                justification=(
                    decision.justification.value
                    if isinstance(decision, ApprovalRecord)
                    and decision.justification is not None
                    else None
                ),
                reason=(
                    decision.reason.value
                    if isinstance(decision, RejectionRecord)
                    else None
                ),
            )
        )
    return CaseAuditResponse(case_id=case_id, events=tuple(responses))


@router.get("/{case_id}")
async def get_case(case_id: str, request: Request) -> CaseEvidenceResponse:
    """Return immutable deterministic evidence, including skipped reasons."""

    snapshot = await scan_service_from(request).get_case(case_id)
    return CaseEvidenceResponse(
        case_id=snapshot.case_id,
        status=snapshot.status.value,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
    )


def _case_not_found() -> DomainError:
    return DomainError(
        error_code=ErrorCode.VALIDATION_FAILED,
        safe_message="The requested case was not found.",
    )
