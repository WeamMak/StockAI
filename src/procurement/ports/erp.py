"""Framework-independent boundary for procurement ERP reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


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


class ErpPort(Protocol):
    """Operations the Procurement MCP server may request from an ERP."""

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        """Return one bounded page of candidate records."""


class ErpUnavailableError(Exception):
    """Safe adapter signal for a temporarily unavailable ERP read."""
