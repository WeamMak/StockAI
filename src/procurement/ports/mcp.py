"""Consumer-owned boundary for Procurement MCP reads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_SKIP_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$", re.ASCII)
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DECIMAL_MINIMUM = Decimal("-999999999.999999")
_DECIMAL_MAXIMUM = Decimal("999999999.999999")
_DECIMAL_QUANTUM = Decimal("0.000001")


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and _IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _valid_decimal(value: object, *, allow_negative: bool) -> bool:
    minimum = _DECIMAL_MINIMUM if allow_negative else Decimal("0")
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and minimum <= value <= _DECIMAL_MAXIMUM
        and value.quantize(_DECIMAL_QUANTUM) == value
    )


@dataclass(frozen=True, slots=True)
class ReplenishmentCandidate:
    """Validated candidate data received through the MCP client adapter."""

    product_id: str
    product_name: str
    category_id: str
    reorder_minimum: Decimal
    reorder_maximum: Decimal
    projected_quantity: Decimal
    projected_trigger_date: date
    skip_reason_code: str | None

    def __post_init__(self) -> None:
        if not _valid_identifier(self.product_id):
            raise ValueError("product_id must be a bounded identifier")
        if not _valid_identifier(self.category_id):
            raise ValueError("category_id must be a bounded identifier")
        if (
            not isinstance(self.product_name, str)
            or not self.product_name.strip()
            or len(self.product_name) > 200
            or _CONTROL_CHARACTER_PATTERN.search(self.product_name) is not None
        ):
            raise ValueError("product_name must be bounded normal text")
        if not _valid_decimal(self.reorder_minimum, allow_negative=False):
            raise ValueError("reorder_minimum must be a bounded decimal")
        if not _valid_decimal(self.reorder_maximum, allow_negative=False):
            raise ValueError("reorder_maximum must be a bounded decimal")
        if self.reorder_maximum < self.reorder_minimum:
            raise ValueError("reorder_maximum must be at least reorder_minimum")
        if not _valid_decimal(self.projected_quantity, allow_negative=True):
            raise ValueError("projected_quantity must be a bounded decimal")
        if type(self.projected_trigger_date) is not date:
            raise ValueError("projected_trigger_date must be a date")
        if self.skip_reason_code is not None and (
            not isinstance(self.skip_reason_code, str)
            or not 1 <= len(self.skip_reason_code) <= 64
            or _SKIP_CODE_PATTERN.fullmatch(self.skip_reason_code) is None
        ):
            raise ValueError("skip_reason_code must be a bounded stable code")


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """One validated, environment-bound page returned through MCP."""

    environment: Environment
    candidates: tuple[ReplenishmentCandidate, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("environment must be dev or prod")
        if (
            not isinstance(self.candidates, tuple)
            or len(self.candidates) > 100
            or not all(
                isinstance(candidate, ReplenishmentCandidate)
                for candidate in self.candidates
            )
        ):
            raise ValueError("candidates must contain at most 100 candidates")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str)
            or not 1 <= len(self.next_cursor) <= 256
            or _IDENTIFIER_PATTERN.fullmatch(self.next_cursor) is None
        ):
            raise ValueError("next_cursor must be a bounded opaque cursor")


class ProcurementMcpPort(Protocol):
    """Read operations the LangGraph workflow requests through MCP."""

    async def list_replenishment_candidates(
        self,
        *,
        environment: Environment,
        horizon_days: int,
        limit: int,
    ) -> CandidatePage:
        """Return one validated candidate page over the configured transport."""

    async def get_procurement_evidence(
        self,
        *,
        environment: Environment,
        product_id: str,
        horizon_days: int,
    ) -> ProcurementEvidence:
        """Return one validated authoritative evidence record."""

    async def get_procurement_preferences(
        self,
        *,
        environment: Environment,
        company_id: str,
        category_id: str,
        product_id: str,
    ) -> ProcurementPreference:
        """Return one independently validated effective preference profile."""


class McpReadError(Exception):
    """Safe MCP-client failure that discards private upstream detail."""

    safe_message = "The procurement source is unavailable."

    def __init__(self, *, retry_count: int, private_detail: object = None) -> None:
        del private_detail
        if type(retry_count) is not int or not 0 <= retry_count <= 2:
            raise ValueError("retry_count must be between zero and two")
        super().__init__(self.safe_message)
        self.retry_count = retry_count


class McpTimeoutError(McpReadError):
    """Safe signal that the bounded MCP read timed out."""

    safe_message = "The procurement source timed out."


class McpUnavailableError(McpReadError):
    """Safe signal that MCP returned no usable candidate data."""
