"""Versioned manual scan creation and polling routes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from procurement.agent.state import (
    ApprovalReadyResult,
    LegacyApprovalReadyResult,
    ManualReviewResult,
    NoValidOfferResult,
)
from procurement.api.auth.rbac import require_csrf, require_officer
from procurement.api.services.scans import (
    ScanAggregateSnapshot,
    ScanFailure,
    ScanService,
    ScanSnapshot,
    ScanTrigger,
)
from procurement.domain.errors import DomainError, ErrorCode
from procurement.ports.repositories import CaseSummary

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
    validation_level: Literal["t27"] = "t27"
    product_id: str
    product_name: str
    offer_id: str
    rationale: str
    trade_offs: tuple[str, ...]
    risk_flags: tuple[str, ...]
    uncertainty: str
    evidence_limitations: tuple[str, ...]
    evidence_digest: str
    quantity: str
    unit_price: str
    normalized_cost: str
    budget_status: str
    preference_profile_id: str
    preference_scope: str
    preference_revision: int
    priority_order: tuple[str, ...]
    premium_outcome: str
    read_only: Literal[True] = True


class LegacyApprovalReadyResponse(BaseModel):
    """Historical success without unavailable T27 validation metadata."""

    model_config = _RESPONSE_CONFIG

    outcome: Literal["approval_ready"] = "approval_ready"
    validation_level: Literal["legacy"] = "legacy"
    product_id: str
    product_name: str
    offer_id: None = None
    rationale: str
    trade_offs: tuple[str, ...]
    risk_flags: tuple[str, ...]
    uncertainty: str
    evidence_limitations: tuple[str, ...]
    read_only: Literal[True] = True


class ManualReviewResponse(BaseModel):
    """Safe model-declined or deterministic fallback result."""

    model_config = _RESPONSE_CONFIG

    outcome: Literal["manual_review"] = "manual_review"
    rationale: str
    trade_offs: tuple[str, ...]
    risk_flags: tuple[str, ...]
    uncertainty: str
    evidence_limitations: tuple[str, ...]
    read_only: Literal[True] = True


class NoValidOfferResponse(BaseModel):
    """A candidate correctly evaluated with zero eligible vendor offers."""

    model_config = _RESPONSE_CONFIG

    outcome: Literal["no_valid_offer"] = "no_valid_offer"
    product_id: str
    product_name: str
    rationale: str
    evidence_limitations: tuple[str, ...]
    read_only: Literal[True] = True


class ConfirmedResponse(BaseModel):
    """Placeholder shape for a manager-confirmed purchase order.

    No code path in this repository produces this outcome yet -- it
    exists so a future manager-decision task only needs to start
    returning it, with no API contract change required then.
    """

    model_config = _RESPONSE_CONFIG

    outcome: Literal["confirmed"] = "confirmed"
    product_id: str
    product_name: str
    po_reference: str
    po_amount: str
    read_only: Literal[True] = True


class ScanErrorResponse(BaseModel):
    """Safe terminal failure returned by polling."""

    model_config = _RESPONSE_CONFIG

    error_code: str
    message: str
    retryable: bool
    retry_count: int


class RefineCaseRequest(BaseModel):
    """Bounded officer note submitted to re-evaluate one case."""

    model_config = _RESPONSE_CONFIG

    note: str = Field(min_length=1, max_length=280)


class CaseResponse(BaseModel):
    """Public representation of one case within a scan."""

    model_config = _RESPONSE_CONFIG

    scan_id: str
    case_id: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    evidence: tuple[dict[str, object], ...]
    result: (
        ApprovalReadyResponse
        | LegacyApprovalReadyResponse
        | ManualReviewResponse
        | NoValidOfferResponse
        | None
    )
    error: ScanErrorResponse | None
    refinement_count: int


class CaseSummaryResponse(BaseModel):
    """One case's result, enough to render a scan's results table."""

    model_config = _RESPONSE_CONFIG

    case_id: str
    product_id: str
    product_name: str
    outcome: str
    amount: str | None
    need_by_date: date | None
    scan_id: str
    budget_status: str
    completed_at: datetime | None


class ScanAggregateResponse(BaseModel):
    """Public representation of one scan and every case it produced."""

    model_config = _RESPONSE_CONFIG

    scan_id: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    results: tuple[CaseSummaryResponse, ...]
    outcome_counts: dict[str, int]
    error: ScanErrorResponse | None


class ScanListResponse(BaseModel):
    """Bounded newest-first scan list."""

    model_config = _RESPONSE_CONFIG

    scans: tuple[ScanAggregateResponse, ...]


def scan_service_from(request: Request) -> ScanService:
    service = request.app.state.scan_service
    if not isinstance(service, ScanService):  # pragma: no cover - app invariant
        raise RuntimeError("scan service is not configured")
    return service


def _error_response(error: ScanFailure | None) -> ScanErrorResponse | None:
    if error is None:
        return None
    return ScanErrorResponse(
        error_code=error.error_code.value,
        message=error.message,
        retryable=error.retryable,
        retry_count=error.retry_count,
    )


def case_response(snapshot: ScanSnapshot) -> CaseResponse:
    """Map an internal case snapshot to the filtered public response model."""

    result: (
        ApprovalReadyResponse
        | LegacyApprovalReadyResponse
        | ManualReviewResponse
        | NoValidOfferResponse
        | None
    ) = None
    if isinstance(snapshot.result, ApprovalReadyResult):
        result = ApprovalReadyResponse(
            product_id=snapshot.result.product_id,
            product_name=snapshot.result.product_name,
            offer_id=snapshot.result.offer_id,
            rationale=snapshot.result.rationale,
            trade_offs=snapshot.result.trade_offs,
            risk_flags=snapshot.result.risk_flags,
            uncertainty=snapshot.result.uncertainty,
            evidence_limitations=snapshot.result.evidence_limitations,
            evidence_digest=snapshot.result.evidence_digest,
            quantity=format(snapshot.result.quantity, "f"),
            unit_price=format(snapshot.result.unit_price, "f"),
            normalized_cost=format(snapshot.result.normalized_cost, "f"),
            budget_status=snapshot.result.budget_status,
            preference_profile_id=snapshot.result.preference_profile_id,
            preference_scope=snapshot.result.preference_scope,
            preference_revision=snapshot.result.preference_revision,
            priority_order=snapshot.result.priority_order,
            premium_outcome=snapshot.result.premium_outcome,
        )
    elif isinstance(snapshot.result, LegacyApprovalReadyResult):
        result = LegacyApprovalReadyResponse(
            product_id=snapshot.result.product_id,
            product_name=snapshot.result.product_name,
            rationale=snapshot.result.rationale,
            trade_offs=snapshot.result.trade_offs,
            risk_flags=snapshot.result.risk_flags,
            uncertainty=snapshot.result.uncertainty,
            evidence_limitations=snapshot.result.evidence_limitations,
        )
    elif isinstance(snapshot.result, ManualReviewResult):
        result = ManualReviewResponse(
            rationale=snapshot.result.rationale,
            trade_offs=snapshot.result.trade_offs,
            risk_flags=snapshot.result.risk_flags,
            uncertainty=snapshot.result.uncertainty,
            evidence_limitations=snapshot.result.evidence_limitations,
        )
    elif isinstance(snapshot.result, NoValidOfferResult):
        result = NoValidOfferResponse(
            product_id=snapshot.result.product_id,
            product_name=snapshot.result.product_name,
            rationale=snapshot.result.rationale,
            evidence_limitations=snapshot.result.evidence_limitations,
        )
    return CaseResponse(
        scan_id=snapshot.scan_id,
        case_id=snapshot.case_id,
        status=snapshot.status.value,
        trigger=snapshot.trigger.value,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
        result=result,
        error=_error_response(snapshot.error),
        refinement_count=snapshot.refinement_count,
    )


def case_summary_response(row: CaseSummary) -> CaseSummaryResponse:
    """Map one internal case summary to its filtered public response model."""

    return CaseSummaryResponse(
        case_id=row.case_id,
        product_id=row.product_id,
        product_name=row.product_name,
        outcome=row.outcome,
        amount=format(row.amount, "f") if row.amount is not None else None,
        need_by_date=row.need_by_date,
        scan_id=row.scan_id,
        budget_status=row.budget_status,
        completed_at=row.completed_at.value if row.completed_at is not None else None,
    )


def scan_aggregate_response(snapshot: ScanAggregateSnapshot) -> ScanAggregateResponse:
    """Map an internal scan snapshot to the filtered public response model."""

    outcome_counts: dict[str, int] = {}
    for row in snapshot.results:
        outcome_counts[row.outcome] = outcome_counts.get(row.outcome, 0) + 1
    return ScanAggregateResponse(
        scan_id=snapshot.scan_id,
        status=snapshot.status.value,
        trigger=snapshot.trigger.value,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        results=tuple(case_summary_response(row) for row in snapshot.results),
        outcome_counts=outcome_counts,
        error=_error_response(snapshot.error),
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def create_manual_scan(
    request: Request, response: Response
) -> ScanAggregateResponse:
    """Schedule an authorized manual scan without holding the request open."""

    snapshot = await scan_service_from(request).start_scan(trigger=ScanTrigger.MANUAL)
    response.headers["Location"] = f"/api/v1/scans/{snapshot.scan_id}"
    return scan_aggregate_response(snapshot)


@router.get("")
async def list_scans(request: Request) -> ScanListResponse:
    """List the bounded durable walking-skeleton scan history."""

    return ScanListResponse(
        scans=tuple(
            scan_aggregate_response(snapshot)
            for snapshot in await scan_service_from(request).list_scans()
        )
    )


@router.get("/{scan_id}")
async def get_scan(scan_id: str, request: Request) -> ScanAggregateResponse:
    """Return current progress or the terminal result for one scan."""

    return scan_aggregate_response(await scan_service_from(request).get_scan(scan_id))


@router.get("/{scan_id}/cases/{case_id}")
async def get_case(scan_id: str, case_id: str, request: Request) -> CaseResponse:
    """Return one case's full detail, scoped to its owning scan."""

    if not case_id.startswith(f"{scan_id}:"):
        raise DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The requested case was not found.",
        )
    return case_response(await scan_service_from(request).get_case(case_id))


@router.post(
    "/{scan_id}/cases/{case_id}/refine",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def refine_case(
    scan_id: str,
    case_id: str,
    body: RefineCaseRequest,
    request: Request,
) -> CaseResponse:
    """Reserve one bounded refinement attempt for an approval-ready case."""

    if not case_id.startswith(f"{scan_id}:"):
        raise DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The requested case was not found.",
        )
    snapshot = await scan_service_from(request).refine_case(
        case_id=case_id, note=body.note
    )
    return case_response(snapshot)
