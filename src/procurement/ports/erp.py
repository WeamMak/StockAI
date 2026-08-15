"""Framework-independent boundary for procurement ERP reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference


@dataclass(frozen=True, slots=True)
class ReplenishmentCandidatesQuery:
    """Bounded paging query understood by an ERP adapter."""

    horizon_days: int
    limit: int
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ReplenishmentCandidateRecord:
    """ERP-neutral fields needed to discover a replenishment candidate."""

    product_id: str
    product_name: str
    category_id: str
    reorder_minimum: Decimal
    reorder_maximum: Decimal
    projected_quantity: Decimal
    projected_trigger_date: date
    skip_reason_code: str | None


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """One page returned by the ERP boundary."""

    items: tuple[ReplenishmentCandidateRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ProcurementEvidenceQuery:
    """Environment-bound request for one product's authoritative evidence."""

    environment: Environment
    product_id: str
    horizon_days: int = 14


@dataclass(frozen=True, slots=True)
class ProcurementPreferenceQuery:
    """Environment-bound identifiers used for preference resolution."""

    environment: Environment
    company_id: str
    category_id: str
    product_id: str


class ErpPort(Protocol):
    """Operations the Procurement MCP server may request from an ERP."""

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        """Return one bounded page of candidate records."""

    async def get_procurement_evidence(
        self,
        query: ProcurementEvidenceQuery,
    ) -> ProcurementEvidence:
        """Return one complete deterministic evidence record."""

    async def get_procurement_preferences(
        self,
        query: ProcurementPreferenceQuery,
    ) -> ProcurementPreference:
        """Return the effective current typed preference profile."""


class ErpUnavailableError(Exception):
    """Safe adapter signal for a temporarily unavailable ERP read."""

    safe_message = "The procurement source is unavailable."

    def __init__(
        self,
        private_detail: object = None,
        *,
        retry_count: int = 0,
    ) -> None:
        del private_detail
        if type(retry_count) is not int or not 0 <= retry_count <= 2:
            raise ValueError("retry_count must be between zero and two")
        super().__init__(self.safe_message)
        self.retry_count = retry_count
