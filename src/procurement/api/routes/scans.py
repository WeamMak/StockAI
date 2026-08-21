"""Versioned manual scan creation and polling routes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, cast

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


class DraftResponse(BaseModel):
    """Public reference to the one Odoo draft PO bound to a pending case."""

    model_config = _RESPONSE_CONFIG

    po_id: int
    write_date: str
    state: str
    partner_id: int
    currency_id: int
    amount_total: str


class CaseDecisionResponse(BaseModel):
    """Safe terminal Odoo outcome without replacing recommendation evidence."""

    model_config = _RESPONSE_CONFIG

    decision_id: str
    decision_type: Literal["approve", "reject"]
    status: str
    po_id: int
    po_reference: str
    write_date: str
    odoo_state: str
    reconciled: bool


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
    revision: int
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
    draft: DraftResponse | None
    decision: CaseDecisionResponse | None


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
    status: str


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
    draft = (
        DraftResponse(
            po_id=snapshot.draft.po_id,
            write_date=snapshot.draft.write_date,
            state=snapshot.draft.state,
            partner_id=snapshot.draft.partner_id,
            currency_id=snapshot.draft.currency_id,
            amount_total=format(snapshot.draft.amount_total, "f"),
        )
        if snapshot.draft is not None
        else None
    )
    decision = (
        CaseDecisionResponse(
            decision_id=snapshot.decision.decision_id,
            decision_type=cast(
                Literal["approve", "reject"], snapshot.decision.decision_type
            ),
            status=snapshot.decision.status,
            po_id=snapshot.decision.po_id,
            po_reference=snapshot.decision.po_reference,
            write_date=snapshot.decision.write_date,
            odoo_state=snapshot.decision.odoo_state,
            reconciled=snapshot.decision.reconciled,
        )
        if snapshot.decision is not None
        else None
    )
    return CaseResponse(
        scan_id=snapshot.scan_id,
        case_id=snapshot.case_id,
        revision=snapshot.revision,
        status=snapshot.status.value,
        trigger=snapshot.trigger.value,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
        result=result,
        error=_error_response(snapshot.error),
        refinement_count=snapshot.refinement_count,
        draft=draft,
        decision=decision,
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
        status=row.status,
    )


def _outcome_breakdown_label(row: CaseSummary) -> str:
    """Distinguish a pending draft from its underlying approval_ready outcome
    for the scan-level breakdown, without changing the case's own outcome."""

    if row.status not in {"succeeded", "skipped", "failed"}:
        return row.status
    return row.outcome


def scan_aggregate_response(snapshot: ScanAggregateSnapshot) -> ScanAggregateResponse:
    """Map an internal scan snapshot to the filtered public response model."""

    outcome_counts: dict[str, int] = {}
    for row in snapshot.results:
        label = _outcome_breakdown_label(row)
        outcome_counts[label] = outcome_counts.get(label, 0) + 1
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
