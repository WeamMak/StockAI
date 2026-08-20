"""Unit behavior for the idempotent draft-creation MCP tool."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

import pytest
from tests.support.fake_odoo.adapter import FakeOdooAdapter

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.schemas import CreateDraftInput
from procurement.mcp_server.tools.create_draft import (
    SafeMcpToolError,
    create_purchase_order_draft,
)
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    CandidatePage,
    ErpUnavailableError,
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
    ReplenishmentCandidatesQuery,
)


def _request(**overrides: object) -> CreateDraftInput:
    values: dict[str, object] = {
        "environment": "dev",
        "origin": "scan-001:product-101",
        "vendor_id": "7",
        "currency_code": "USD",
        "product_id": "31",
        "product_name": "Fictional Safety Gloves",
        "quantity": "10.000000",
        "unit_price": "12.500000",
        "need_by_date": "2026-08-30",
    }
    values.update(overrides)
    return CreateDraftInput.model_validate(values)


@dataclass
class FailingErp:
    """Minimal ErpPort double that always fails draft creation one way."""

    error: Exception

    async def list_replenishment_candidates(
        self, query: ReplenishmentCandidatesQuery
    ) -> CandidatePage:
        raise NotImplementedError

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        raise NotImplementedError

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        raise NotImplementedError

    async def find_purchase_order_draft(
        self, *, origin: str
    ) -> PurchaseOrderDraft | None:
        del origin
        raise self.error

    async def create_purchase_order_draft(
        self, command: PurchaseOrderDraftCommand
    ) -> PurchaseOrderDraft:
        raise self.error


@pytest.mark.anyio
async def test_create_draft_returns_the_idempotent_snapshot() -> None:
    adapter = FakeOdooAdapter(page=CandidatePage(items=(), next_cursor=None))
    metrics = create_mcp_metrics()
    stream = StringIO()
    logger = configure_json_logging(
        service="procurement-mcp",
        environment="dev",
        stream=stream,
        logger_name="procurement.test.mcp.create_draft.success",
    )

    response = await create_purchase_order_draft(
        request=_request(),
        erp=adapter,
        server_environment=Environment.DEV,
        metrics=metrics,
        logger=logger,
    )

    assert response.po_id == 1
    assert response.amount_total == 125
    assert '"event":"mcp_tool_completed"' in stream.getvalue()


@pytest.mark.anyio
async def test_create_draft_rejects_a_foreign_environment() -> None:
    adapter = FakeOdooAdapter(page=CandidatePage(items=(), next_cursor=None))

    with pytest.raises(SafeMcpToolError) as raised:
        await create_purchase_order_draft(
            request=_request(environment="prod"),
            erp=adapter,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.mcp.create_draft.forbidden",
            ),
        )

    assert raised.value.error_code == ErrorCode.FORBIDDEN


@pytest.mark.anyio
async def test_create_draft_rejects_a_zero_quantity() -> None:
    """Well-formed but non-positive: passes the wire-format regex but must
    still fail `PurchaseOrderDraftCommand`'s own business validation."""

    adapter = FakeOdooAdapter(page=CandidatePage(items=(), next_cursor=None))

    with pytest.raises(SafeMcpToolError) as raised:
        await create_purchase_order_draft(
            request=_request(quantity="0.000000"),
            erp=adapter,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.mcp.create_draft.invalid",
            ),
        )

    assert raised.value.error_code == ErrorCode.VALIDATION_FAILED


@pytest.mark.anyio
async def test_create_draft_maps_reconciliation_required() -> None:
    from procurement.mcp_server.idempotency import DraftReconciliationRequiredError

    class RaisingOnCreate(FailingErp):
        async def find_purchase_order_draft(
            self, *, origin: str
        ) -> PurchaseOrderDraft | None:
            del origin
            return None

    erp = RaisingOnCreate(error=DraftReconciliationRequiredError())

    with pytest.raises(SafeMcpToolError) as raised:
        await create_purchase_order_draft(
            request=_request(),
            erp=erp,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.mcp.create_draft.reconciliation",
            ),
        )

    assert raised.value.error_code == ErrorCode.RECONCILIATION_REQUIRED
    assert raised.value.retryable is False


@pytest.mark.anyio
async def test_create_draft_maps_erp_unavailable() -> None:
    class RaisingOnFind(FailingErp):
        pass

    erp = RaisingOnFind(error=ErpUnavailableError(retry_count=1))

    with pytest.raises(SafeMcpToolError) as raised:
        await create_purchase_order_draft(
            request=_request(),
            erp=erp,
            server_environment=Environment.DEV,
            metrics=create_mcp_metrics(),
            logger=configure_json_logging(
                service="procurement-mcp",
                environment="dev",
                logger_name="procurement.test.mcp.create_draft.unavailable",
            ),
        )

    assert raised.value.error_code == ErrorCode.ODOO_UNAVAILABLE
    assert raised.value.retryable is True
