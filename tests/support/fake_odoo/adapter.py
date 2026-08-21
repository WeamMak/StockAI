"""Deterministic fake for the narrow T04 ERP port."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from procurement.bootstrap.mcp import _fictional_evidence, _fictional_preference
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference
from procurement.ports.erp import (
    CandidatePage,
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
    PurchaseOrderAction,
    PurchaseOrderActionResult,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
    ReplenishmentCandidatesQuery,
)


class FakeOdooAdapter:
    """Return queued fictional pages or failures without a live Odoo service."""

    def __init__(
        self,
        *,
        page: CandidatePage,
        failures: Sequence[Exception] = (),
    ) -> None:
        self._page = page
        self._failures = list(failures)
        self.queries: list[ReplenishmentCandidatesQuery] = []
        self._purchase_order: PurchaseOrderActionResult | None = None
        self._draft_origin: str | None = None
        self.create_draft_calls = 0

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        """Record the query, then return or fail in a deterministic order."""

        self.queries.append(query)
        if self._failures:
            raise self._failures.pop(0)
        return self._page

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        """Return policy-built evidence for the requested fictional product."""

        return _fictional_evidence(query)

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        return _fictional_preference(query)

    async def find_purchase_order_draft(
        self, *, origin: str
    ) -> PurchaseOrderDraft | None:
        if (
            self._purchase_order is None
            or self._draft_origin != origin
            or self._purchase_order.state != "draft"
        ):
            return None
        return PurchaseOrderDraft(
            po_id=self._purchase_order.po_id,
            write_date=self._purchase_order.write_date,
            state=self._purchase_order.state,
            partner_id=self._purchase_order.partner_id,
            currency_id=self._purchase_order.currency_id,
            amount_total=self._purchase_order.amount_total,
        )

    async def create_purchase_order_draft(
        self, command: PurchaseOrderDraftCommand
    ) -> PurchaseOrderDraft:
        self.create_draft_calls += 1
        draft = PurchaseOrderDraft(
            po_id=1,
            write_date="2026-08-20 00:00:00",
            state="draft",
            partner_id=1,
            currency_id=1,
            amount_total=(command.quantity * command.unit_price).quantize(
                Decimal("0.000001")
            ),
        )
        self._purchase_order = PurchaseOrderActionResult(
            po_id=draft.po_id,
            po_reference="P00001",
            write_date=draft.write_date,
            state=draft.state,
            partner_id=draft.partner_id,
            currency_id=draft.currency_id,
            amount_total=draft.amount_total,
        )
        self._draft_origin = command.origin
        return draft

    async def read_purchase_order(self, *, po_id: int) -> PurchaseOrderActionResult:
        if self._purchase_order is None or self._purchase_order.po_id != po_id:
            raise ValueError("unknown fictional purchase order")
        return self._purchase_order

    async def apply_purchase_order_action_once(
        self,
        *,
        po_id: int,
        expected: PurchaseOrderDraft,
        action: PurchaseOrderAction,
    ) -> PurchaseOrderActionResult:
        current = await self.read_purchase_order(po_id=po_id)
        if (
            current.write_date != expected.write_date
            or current.state != expected.state
            or current.partner_id != expected.partner_id
            or current.currency_id != expected.currency_id
            or current.amount_total != expected.amount_total
        ):
            raise ValueError("stale fictional purchase order")
        self._purchase_order = PurchaseOrderActionResult(
            po_id=current.po_id,
            po_reference=current.po_reference,
            write_date="2026-08-21 12:01:00",
            state="purchase" if action is PurchaseOrderAction.CONFIRM else "cancel",
            partner_id=current.partner_id,
            currency_id=current.currency_id,
            amount_total=current.amount_total,
        )
        return self._purchase_order
