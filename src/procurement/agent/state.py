"""Typed state and terminal results for the minimal procurement graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.channels import UntrackedValue

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.ports.llm import StructuredRecommendation
from procurement.ports.mcp import ReplenishmentCandidate


@dataclass(frozen=True, slots=True)
class ApprovalReadyResult:
    """One fictional recommendation that performs no external write."""

    product_id: str
    product_name: str
    rationale: str
    risk_flags: tuple[str, ...]
    evidence: ProcurementEvidence | None = None

    @property
    def read_only(self) -> bool:
        """Make the advisory-only boundary explicit without checkpoint state."""

        return True


@dataclass(frozen=True, slots=True)
class UnresolvedResult:
    """Safe terminal result when the walking skeleton cannot recommend."""

    error_code: ErrorCode
    message: str
    retryable: bool
    retry_count: int = 0


ScanResult = ApprovalReadyResult | UnresolvedResult


class ScanState(TypedDict, total=False):
    """Shared state passed between walking-skeleton LangGraph nodes."""

    scan_id: str
    environment: Environment
    candidates: Annotated[tuple[ReplenishmentCandidate, ...], UntrackedValue]
    evidence: Annotated[tuple[ProcurementEvidence, ...], UntrackedValue]
    recommendation: Annotated[StructuredRecommendation, UntrackedValue]
    result: ScanResult
