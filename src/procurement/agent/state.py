"""Typed state and terminal results for the minimal procurement graph."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, TypedDict

from langgraph.channels import UntrackedValue

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.ports.llm import StructuredRecommendation
from procurement.ports.mcp import (
    DecisionOutcome,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
    ReplenishmentCandidate,
)


@dataclass(frozen=True, slots=True)
class ApprovalReadyResult:
    """One fictional recommendation that performs no external write."""

    product_id: str
    product_name: str
    offer_id: str
    rationale: str
    trade_offs: tuple[str, ...]
    risk_flags: tuple[str, ...]
    uncertainty: str
    evidence_limitations: tuple[str, ...]
    evidence_digest: str
    quantity: Decimal
    unit_price: Decimal
    normalized_cost: Decimal
    budget_status: str
    preference_profile_id: str
    preference_scope: str
    preference_revision: int
    priority_order: tuple[str, ...]
    premium_outcome: str
    evidence: ProcurementEvidence | None = None

    @property
    def read_only(self) -> bool:
        """Make the advisory-only boundary explicit without checkpoint state."""

        return True


@dataclass(frozen=True, slots=True)
class LegacyApprovalReadyResult:
    """Historical success retained without claiming T27 validation."""

    product_id: str
    product_name: str
    rationale: str
    trade_offs: tuple[str, ...]
    risk_flags: tuple[str, ...]
    uncertainty: str
    evidence_limitations: tuple[str, ...]
    evidence: ProcurementEvidence | None = None

    @property
    def read_only(self) -> bool:
        """Historical recommendations remain advisory only."""

        return True


@dataclass(frozen=True, slots=True)
class UnresolvedResult:
    """Safe terminal result when the walking skeleton cannot recommend."""

    error_code: ErrorCode
    message: str
    retryable: bool
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class NoValidOfferResult:
    """A candidate correctly evaluated with zero eligible vendor offers."""

    product_id: str
    product_name: str
    rationale: str
    evidence_limitations: tuple[str, ...] = ()

    @property
    def read_only(self) -> bool:
        """No draft can be created without an eligible offer."""

        return True


@dataclass(frozen=True, slots=True)
class ManualReviewResult:
    """Safe deterministic comparison when model judgment cannot be accepted."""

    rationale: str
    trade_offs: tuple[str, ...]
    risk_flags: tuple[str, ...]
    uncertainty: str
    evidence_limitations: tuple[str, ...]

    @property
    def read_only(self) -> bool:
        """Manual review never grants ERP write authority."""

        return True


ScanResult = (
    ApprovalReadyResult
    | LegacyApprovalReadyResult
    | ManualReviewResult
    | NoValidOfferResult
    | UnresolvedResult
)


class ScanState(TypedDict, total=False):
    """Shared state passed between walking-skeleton LangGraph nodes."""

    scan_id: str
    environment: Environment
    candidates: Annotated[tuple[ReplenishmentCandidate, ...], UntrackedValue]
    evidence: Annotated[tuple[ProcurementEvidence, ...], UntrackedValue]
    recommendation: Annotated[StructuredRecommendation, UntrackedValue]
    result: ScanResult
    draft_command: PurchaseOrderDraftCommand
    skip_reason: str
    officer_note: str
    draft: PurchaseOrderDraft
    manager_decision_id: str
    decision_type: str
    decision_outcome: DecisionOutcome
