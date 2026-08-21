"""Idempotency and ambiguous-write behavior for draft-creation orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference
from procurement.mcp_server.idempotency import (
    DraftReconciliationRequiredError,
    resolve_idempotent_draft,
)
from procurement.ports.erp import (
    CandidatePage,
    DraftWriteAmbiguousError,
    ErpUnavailableError,
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
    ReplenishmentCandidatesQuery,
)


def _command() -> PurchaseOrderDraftCommand:
    return PurchaseOrderDraftCommand(
        origin="scan-001:product-101",
        vendor_id="7",
        currency_code="USD",
        product_id="31",
        product_name="Fictional Safety Gloves",
        quantity=Decimal("10.000000"),
        unit_price=Decimal("12.500000"),
        need_by_date=date(2026, 8, 30),
    )


def _draft(*, po_id: int = 1) -> PurchaseOrderDraft:
    return PurchaseOrderDraft(
        po_id=po_id,
        write_date="2026-08-20 00:00:00",
        state="draft",
        partner_id=7,
        currency_id=1,
        amount_total=Decimal("125.00"),
    )


@dataclass
class FakeErp:
    """Programmable ERP double isolating each idempotency scenario."""

    existing: PurchaseOrderDraft | None = None
    create_result: PurchaseOrderDraft | Exception | None = None
    existing_after_ambiguity: PurchaseOrderDraft | None = None
    find_calls: int = field(default=0, init=False)
    create_calls: int = field(default=0, init=False)

    async def list_replenishment_candidates(
        self, query: ReplenishmentCandidatesQuery
    ) -> CandidatePage:
        raise NotImplementedError("not exercised by these idempotency tests")

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        raise NotImplementedError("not exercised by these idempotency tests")

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        raise NotImplementedError("not exercised by these idempotency tests")

    async def find_purchase_order_draft(
        self, *, origin: str
    ) -> PurchaseOrderDraft | None:
        del origin
        self.find_calls += 1
        if self.find_calls > 1:
            return self.existing_after_ambiguity
        return self.existing

    async def create_purchase_order_draft(
        self, command: PurchaseOrderDraftCommand
    ) -> PurchaseOrderDraft:
        del command
        self.create_calls += 1
        if isinstance(self.create_result, Exception):
            raise self.create_result
        assert self.create_result is not None
        return self.create_result


@pytest.mark.anyio
async def test_repeat_call_returns_the_existing_draft_without_creating() -> None:
    erp = FakeErp(existing=_draft())

    result = await resolve_idempotent_draft(erp=erp, command=_command())

    assert result == _draft()
    assert erp.create_calls == 0
    assert erp.find_calls == 1


@pytest.mark.anyio
async def test_no_existing_draft_creates_exactly_once() -> None:
    erp = FakeErp(existing=None, create_result=_draft())

    result = await resolve_idempotent_draft(erp=erp, command=_command())

    assert result == _draft()
    assert erp.create_calls == 1


@pytest.mark.anyio
async def test_ambiguous_write_resolves_by_searching_again() -> None:
    """Response loss after an Odoo commit: the create call raises ambiguity,
    but a second search finds the draft that was, in fact, created."""

    erp = FakeErp(
        existing=None,
        create_result=DraftWriteAmbiguousError(),
        existing_after_ambiguity=_draft(po_id=42),
    )

    result = await resolve_idempotent_draft(erp=erp, command=_command())

    assert result == _draft(po_id=42)
    assert erp.create_calls == 1
    assert erp.find_calls == 2


@pytest.mark.anyio
async def test_ambiguous_write_with_no_resolution_requires_reconciliation() -> None:
    """Process termination after a write whose outcome cannot be confirmed
    must never be retried blindly -- it must surface for reconciliation."""

    erp = FakeErp(
        existing=None,
        create_result=DraftWriteAmbiguousError(),
        existing_after_ambiguity=None,
    )

    with pytest.raises(DraftReconciliationRequiredError):
        await resolve_idempotent_draft(erp=erp, command=_command())

    assert erp.create_calls == 1
    assert erp.find_calls == 2


@pytest.mark.anyio
async def test_timeout_during_create_is_treated_as_ambiguous_not_retried() -> None:
    class HangingErp(FakeErp):
        async def create_purchase_order_draft(
            self, command: PurchaseOrderDraftCommand
        ) -> PurchaseOrderDraft:
            self.create_calls += 1
            import anyio

            await anyio.sleep_forever()
            raise AssertionError("unreachable")

    erp = HangingErp(existing=None, existing_after_ambiguity=_draft(po_id=9))

    result = await resolve_idempotent_draft(
        erp=erp, command=_command(), create_timeout_seconds=0.01
    )

    assert result == _draft(po_id=9)
    assert erp.create_calls == 1
    assert erp.find_calls == 2


@pytest.mark.anyio
async def test_a_read_failure_before_any_write_is_safely_retryable() -> None:
    """Failures before the create attempt are ordinary safe reads -- they
    must propagate with their normal retryable semantics, not be treated as
    write ambiguity."""

    class RaisingErp(FakeErp):
        async def find_purchase_order_draft(
            self, *, origin: str
        ) -> PurchaseOrderDraft | None:
            del origin
            self.find_calls += 1
            raise ErpUnavailableError(retry_count=1)

    erp = RaisingErp()

    with pytest.raises(ErpUnavailableError):
        await resolve_idempotent_draft(erp=erp, command=_command())

    assert erp.create_calls == 0
