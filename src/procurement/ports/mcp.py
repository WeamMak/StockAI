"""Consumer-owned boundary for Procurement MCP reads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from procurement.domain.decisions import DecisionType
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


@dataclass(frozen=True, slots=True)
class PurchaseOrderDraftCommand:
    """Authoritative, already-validated inputs needed to create one draft PO."""

    origin: str
    vendor_id: str
    currency_code: str
    product_id: str
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    need_by_date: date

    def __post_init__(self) -> None:
        if not _valid_identifier(self.origin):
            raise ValueError("origin must be a bounded identifier")
        if not _valid_identifier(self.vendor_id):
            raise ValueError("vendor_id must be a bounded identifier")
        if not re.fullmatch(r"[A-Z]{3}", self.currency_code):
            raise ValueError("currency_code must be a three-letter ISO code")
        if not _valid_identifier(self.product_id):
            raise ValueError("product_id must be a bounded identifier")
        if (
            not isinstance(self.product_name, str)
            or not self.product_name.strip()
            or len(self.product_name) > 200
            or _CONTROL_CHARACTER_PATTERN.search(self.product_name) is not None
        ):
            raise ValueError("product_name must be bounded normal text")
        if (
            not _valid_decimal(self.quantity, allow_negative=False)
            or self.quantity <= 0
        ):
            raise ValueError("quantity must be a bounded positive decimal")
        if not _valid_decimal(self.unit_price, allow_negative=False):
            raise ValueError("unit_price must be a bounded decimal")
        if type(self.need_by_date) is not date:
            raise ValueError("need_by_date must be a date")


@dataclass(frozen=True, slots=True)
class PurchaseOrderDraft:
    """Odoo purchase-order identity and optimistic-concurrency snapshot."""

    po_id: int
    write_date: str
    state: str
    partner_id: int
    currency_id: int
    amount_total: Decimal

    def __post_init__(self) -> None:
        if type(self.po_id) is not int or self.po_id <= 0:
            raise ValueError("po_id must be a positive integer")
        if not isinstance(self.write_date, str) or not self.write_date.strip():
            raise ValueError("write_date must be a non-empty string")
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("state must be a non-empty string")
        if type(self.partner_id) is not int or self.partner_id <= 0:
            raise ValueError("partner_id must be a positive integer")
        if type(self.currency_id) is not int or self.currency_id <= 0:
            raise ValueError("currency_id must be a positive integer")
        if not _valid_decimal(self.amount_total, allow_negative=False):
            raise ValueError("amount_total must be a bounded decimal")


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """Validated terminal result from a manager-authorized MCP action."""

    decision_id: str
    decision_type: DecisionType
    outcome: Literal["confirmed", "cancelled", "reconciliation_required"]
    po_id: int
    po_reference: str
    write_date: str
    odoo_state: str
    reconciled: bool

    def __post_init__(self) -> None:
        if not _valid_identifier(self.decision_id):
            raise ValueError("decision_id must be a bounded identifier")
        if not isinstance(self.decision_type, DecisionType):
            raise ValueError("decision_type must be approve or reject")
        expected = {
            DecisionType.APPROVE: ("confirmed", "purchase"),
            DecisionType.REJECT: ("cancelled", "cancel"),
        }[self.decision_type]
        if self.outcome != "reconciliation_required" and (
            self.outcome,
            self.odoo_state,
        ) != expected:
            raise ValueError("decision outcome and Odoo state do not match")
        if type(self.po_id) is not int or self.po_id <= 0:
            raise ValueError("po_id must be positive")
        for field in ("po_reference", "write_date"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{field} must be bounded text")
        if type(self.reconciled) is not bool:
            raise ValueError("reconciled must be boolean")

    @classmethod
    def confirmed(
        cls,
        *,
        decision_id: str,
        po_id: int,
        po_reference: str,
        write_date: str,
        reconciled: bool,
    ) -> DecisionOutcome:
        return cls(
            decision_id=decision_id,
            decision_type=DecisionType.APPROVE,
            outcome="confirmed",
            po_id=po_id,
            po_reference=po_reference,
            write_date=write_date,
            odoo_state="purchase",
            reconciled=reconciled,
        )

    @classmethod
    def cancelled(
        cls,
        *,
        decision_id: str,
        po_id: int,
        po_reference: str,
        write_date: str,
        reconciled: bool,
    ) -> DecisionOutcome:
        return cls(
            decision_id=decision_id,
            decision_type=DecisionType.REJECT,
            outcome="cancelled",
            po_id=po_id,
            po_reference=po_reference,
            write_date=write_date,
            odoo_state="cancel",
            reconciled=reconciled,
        )


class ProcurementMcpPort(Protocol):
    """Read and one bounded write operation the LangGraph workflow requests
    through MCP."""

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

    async def create_purchase_order_draft(
        self,
        *,
        environment: Environment,
        command: PurchaseOrderDraftCommand,
    ) -> PurchaseOrderDraft:
        """Idempotently create, or return the existing, draft PO for one case."""

    async def confirm_purchase_order(
        self,
        *,
        environment: Environment,
        decision_id: str,
        idempotency_key: str,
    ) -> DecisionOutcome:
        """Confirm the exact PO revision authorized by an immutable approval."""

    async def cancel_draft_purchase_order(
        self,
        *,
        environment: Environment,
        decision_id: str,
        idempotency_key: str,
    ) -> DecisionOutcome:
        """Cancel the exact PO revision authorized by an immutable rejection."""


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


class McpDraftReconciliationRequiredError(McpReadError):
    """Safe signal that a draft write's outcome could not be determined."""

    safe_message = "The purchase-order draft could not be safely reconciled."


class McpApprovalStaleError(McpReadError):
    """The immutable decision no longer authorizes the current PO revision."""

    safe_message = "The approved purchase-order revision is stale."


class McpDecisionReconciliationRequiredError(McpReadError):
    """A terminal PO action may have committed and remains unresolved."""

    safe_message = "The purchase-order action requires reconciliation."
