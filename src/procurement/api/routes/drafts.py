"""Officer-or-manager endpoint for explicit draft creation handoff."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from procurement.api.auth.rbac import OfficerPrincipalDep, require_csrf
from procurement.api.services.drafts import DraftSubmissionService
from procurement.domain.errors import DomainError, ErrorCode

_CONFIG = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class DraftSubmissionRequest(BaseModel):
    model_config = _CONFIG

    case_revision: int = Field(strict=True, ge=1)


class AcceptedDraftSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["creating_draft", "pending_approval"]
    created: bool


IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER,
    ),
]

router = APIRouter(
    prefix="/api/v1/scans",
    tags=["draft-submissions"],
    dependencies=[Depends(require_csrf)],
)


def _service(request: Request) -> DraftSubmissionService:
    service = request.app.state.draft_submission_service
    if not isinstance(service, DraftSubmissionService):
        raise RuntimeError("draft submission service is not configured")
    return service


@router.post(
    "/{scan_id}/cases/{case_id}/draft",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_case_for_draft(
    scan_id: str,
    case_id: str,
    body: DraftSubmissionRequest,
    request: Request,
    principal: OfficerPrincipalDep,
    idempotency_key: IdempotencyKey,
) -> AcceptedDraftSubmissionResponse:
    if not case_id.startswith(f"{scan_id}:"):
        raise DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The case does not belong to the requested scan.",
        )
    accepted = await _service(request).submit(
        case_id=case_id,
        expected_revision=body.case_revision,
        actor_subject=principal.user_id,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    response_status: Literal["creating_draft", "pending_approval"] = (
        "pending_approval"
        if accepted.status.value == "pending_approval"
        else "creating_draft"
    )
    return AcceptedDraftSubmissionResponse(
        case_id=accepted.case_id,
        status=response_status,
        created=accepted.created,
    )
