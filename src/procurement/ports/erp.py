"""Framework-independent boundary for procurement ERP reads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference

_DRAFT_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_DRAFT_DECIMAL_MAXIMUM = Decimal("999999999.999999")
_DRAFT_DECIMAL_QUANTUM = Decimal("0.000001")


def _valid_draft_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and _DRAFT_IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _valid_draft_decimal(value: object, *, minimum: Decimal) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and minimum <= value <= _DRAFT_DECIMAL_MAXIMUM
        and value.quantize(_DRAFT_DECIMAL_QUANTUM) == value
    )


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


@dataclass(frozen=True, slots=True)
class PurchaseOrderDraftCommand:
    """Authoritative, already-validated inputs needed to create one draft PO.

    `origin` is the stable case ID, written to `purchase.order.origin` so a
    repeated call can find (rather than duplicate) the same draft.
    """

    origin: str
    vendor_id: str
    currency_code: str
    product_id: str
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    need_by_date: date

    def __post_init__(self) -> None:
        if not _valid_draft_identifier(self.origin):
            raise ValueError("origin must be a bounded identifier")
        if not _valid_draft_identifier(self.vendor_id):
            raise ValueError("vendor_id must be a bounded identifier")
        if not re.fullmatch(r"[A-Z]{3}", self.currency_code):
            raise ValueError("currency_code must be a three-letter ISO code")
        if not _valid_draft_identifier(self.product_id):
            raise ValueError("product_id must be a bounded identifier")
        if (
            not isinstance(self.product_name, str)
            or not self.product_name.strip()
            or len(self.product_name) > 200
        ):
            raise ValueError("product_name must be bounded normal text")
        if not _valid_draft_decimal(self.quantity, minimum=Decimal("0.000001")):
            raise ValueError("quantity must be a bounded positive decimal")
        if not _valid_draft_decimal(self.unit_price, minimum=Decimal("0")):
            raise ValueError("unit_price must be a bounded decimal")
        if type(self.need_by_date) is not date:
            raise ValueError("need_by_date must be a date")


@dataclass(frozen=True, slots=True)
class PurchaseOrderDraft:
    """Odoo purchase-order identity and optimistic-concurrency snapshot,
    mirroring the StockAI add-on's `_stockai_snapshot()` shape so it can be
    passed back unchanged as the `expected` revision for a later
    update/cancel/confirm action."""

    po_id: int
    write_date: str
    state: str
    partner_id: int
    currency_id: int
    amount_total: Decimal


class PurchaseOrderAction(StrEnum):
    """The only terminal purchase-order writes exposed to the agent."""

    CONFIRM = "confirm"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class PurchaseOrderActionResult:
    """Strict post-action or reconciliation snapshot returned by the ERP."""

    po_id: int
    po_reference: str | None
    write_date: str
    state: str
    partner_id: int
    currency_id: int
    amount_total: Decimal


class ApprovalStaleError(Exception):
    """The exact approved purchase-order revision is no longer current."""

    safe_message = "The approved purchase-order revision is stale."

    def __init__(self, private_detail: object = None) -> None:
        del private_detail
        super().__init__(self.safe_message)


class PurchaseOrderWriteAmbiguousError(Exception):
    """A terminal write may have committed and must be reconciled by reading."""

    safe_message = "The purchase-order action outcome could not be confirmed."

    def __init__(self, private_detail: object = None) -> None:
        del private_detail
        super().__init__(self.safe_message)


class DraftWriteAmbiguousError(Exception):
    """A create call's outcome is unknown; the caller must search before
    ever considering a retry -- this is never safe to retry blindly."""

    safe_message = "The purchase-order draft outcome could not be confirmed."

    def __init__(self, private_detail: object = None, *, retry_count: int = 0) -> None:
        del private_detail
        if type(retry_count) is not int or not 0 <= retry_count <= 2:
            raise ValueError("retry_count must be between zero and two")
        super().__init__(self.safe_message)
        self.retry_count = retry_count


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

    async def find_purchase_order_draft(
        self, *, origin: str
    ) -> PurchaseOrderDraft | None:
        """Return the existing draft bound to this origin, if any."""

    async def create_purchase_order_draft(
        self,
        command: PurchaseOrderDraftCommand,
    ) -> PurchaseOrderDraft:
        """Attempt exactly one non-retried creation.

        Raises `DraftWriteAmbiguousError` when the outcome is unknown (for
        example a timeout after Odoo may already have committed); the caller
        must search by origin before deciding whether to retry."""

    async def read_purchase_order(self, *, po_id: int) -> PurchaseOrderActionResult:
        """Read the current purchase-order lifecycle snapshot."""

    async def apply_purchase_order_action_once(
        self,
        *,
        po_id: int,
        expected: PurchaseOrderDraft,
        action: PurchaseOrderAction,
    ) -> PurchaseOrderActionResult:
        """Apply one revision-bound action without a blind write retry."""


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
