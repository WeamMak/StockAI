"""Persistence boundary for immutable manager decisions."""

from dataclasses import dataclass
from typing import Protocol

from procurement.domain.decisions import DecisionId, DecisionRecord
from procurement.domain.models import UtcTimestamp


class DecisionConflictError(Exception):
    """A decision, guard, or idempotency binding conflicts with prior data."""


@dataclass(frozen=True, slots=True)
class DecisionCreateResult:
    """Result of conditionally creating one immutable decision."""

    record: DecisionRecord
    created: bool


class DecisionReader(Protocol):
    """Strong-read boundary shared by the API and MCP processes."""

    async def get_decision(self, decision_id: DecisionId) -> DecisionRecord | None:
        """Strongly read one immutable decision by ID."""


class DecisionRepository(DecisionReader, Protocol):
    """Conditional manager-decision persistence."""

    async def create_decision(
        self,
        record: DecisionRecord,
        *,
        retention_expires_at: UtcTimestamp,
    ) -> DecisionCreateResult:
        """Create one decision, case/revision guard, and idempotency binding."""
