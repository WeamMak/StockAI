"""Manager-only approval and rejection HTTP endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from procurement.api.auth.rbac import ManagerPrincipalDep, require_csrf
from procurement.api.services.decisions import (
    ApprovalCommand,
    DecisionService,
    RejectionCommand,
)

_CONFIG = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_DECIMAL = r"^\d{1,15}(\.\d{1,6})?$"


class ApprovalRequest(BaseModel):
    model_config = _CONFIG

    environment: Literal["dev", "prod"]
    case_revision: int = Field(strict=True, ge=1)
    po_id: int = Field(strict=True, gt=0)
    po_revision: str = Field(min_length=1, max_length=32)
    vendor_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER)
    quantity: str = Field(pattern=_DECIMAL)
    amount: str = Field(pattern=_DECIMAL)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    budget_status: str = Field(min_length=1, max_length=32)
    overage: str = Field(pattern=_DECIMAL)
    evidence_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    budget_exception: bool = False
    justification: str | None = Field(default=None, min_length=1, max_length=280)


class RejectionRequest(BaseModel):
    model_config = _CONFIG

    environment: Literal["dev", "prod"]
    case_revision: int = Field(strict=True, ge=1)
    po_id: int = Field(strict=True, gt=0)
    po_revision: str = Field(min_length=1, max_length=32)
    evidence_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=280)


class AcceptedDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    decision_type: Literal["approve", "reject"]
    status: str
    created: bool


router = APIRouter(
    prefix="/api/v1/cases",
    tags=["manager-decisions"],
    dependencies=[Depends(require_csrf)],
)


def _service(request: Request) -> DecisionService:
    service = request.app.state.decision_service
    if not isinstance(service, DecisionService):
        raise RuntimeError("decision service is not configured")
    return service


IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER,
    ),
]


@router.post(
    "/{case_id}/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve_case(
    case_id: str,
    body: ApprovalRequest,
    request: Request,
    principal: ManagerPrincipalDep,
    idempotency_key: IdempotencyKey,
) -> AcceptedDecisionResponse:
    accepted = await _service(request).approve(
        case_id=case_id,
        command=ApprovalCommand(
            environment=body.environment,
            case_revision=body.case_revision,
            po_id=body.po_id,
            po_revision=body.po_revision,
            vendor_id=body.vendor_id,
            quantity=Decimal(body.quantity),
            amount=Decimal(body.amount),
            currency=body.currency,
            budget_status=body.budget_status,
            overage=Decimal(body.overage),
            evidence_digest=body.evidence_digest,
            budget_exception=body.budget_exception,
            justification=body.justification,
        ),
        manager_subject=principal.user_id,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    return AcceptedDecisionResponse(
        decision_id=accepted.decision_id,
        decision_type=accepted.decision_type.value,
        status=accepted.status,
        created=accepted.created,
    )


@router.post(
    "/{case_id}/reject",
    status_code=status.HTTP_202_ACCEPTED,
)
async def reject_case(
    case_id: str,
    body: RejectionRequest,
    request: Request,
    principal: ManagerPrincipalDep,
    idempotency_key: IdempotencyKey,
) -> AcceptedDecisionResponse:
    accepted = await _service(request).reject(
        case_id=case_id,
        command=RejectionCommand(
            environment=body.environment,
            case_revision=body.case_revision,
            po_id=body.po_id,
            po_revision=body.po_revision,
            evidence_digest=body.evidence_digest,
            reason=body.reason,
        ),
        manager_subject=principal.user_id,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    return AcceptedDecisionResponse(
        decision_id=accepted.decision_id,
        decision_type=accepted.decision_type.value,
        status=accepted.status,
        created=accepted.created,
    )
