"""Typed state and terminal results for the minimal procurement graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.ports.llm import StructuredRecommendation
from procurement.ports.mcp import ReplenishmentCandidate


@dataclass(frozen=True, slots=True)
class ApprovalReadyResult:
    """One fictional recommendation that performs no external write."""

    product_id: str
    product_name: str
    rationale: str
    risk_flags: tuple[str, ...]
    read_only: bool = field(default=True, init=False)


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
    candidates: tuple[ReplenishmentCandidate, ...]
    recommendation: StructuredRecommendation
    result: ScanResult
