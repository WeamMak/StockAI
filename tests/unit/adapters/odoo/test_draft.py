"""Idempotent draft-creation behavior of the Odoo JSON-2 client and adapter."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from procurement.adapters.odoo.client import OdooErpAdapter, OdooJson2Client
from procurement.adapters.odoo.draft import OdooDraftMappingError
from procurement.ports.erp import (
    DraftWriteAmbiguousError,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
)


def _client(respond: httpx.MockTransport) -> OdooJson2Client:
    return OdooJson2Client(
        base_url="https://odoo.example.invalid",
        database="stockai_dev",
        api_key="private-odoo-key",
        transport=respond,
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


@pytest.mark.anyio
async def test_create_purchase_order_once_never_retries_a_transient_status() -> None:
    """A write is not a safely-retryable read: even a transient 503 must
    raise ambiguity after exactly one attempt, never retry automatically."""

    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    client = _client(httpx.MockTransport(respond))

    with pytest.raises(DraftWriteAmbiguousError):
        await client.create_purchase_order_once(vals={"origin": "case-1"})

    assert calls == 1


@pytest.mark.anyio
async def test_create_purchase_order_once_timeout_is_ambiguous_not_retried() -> None:
    calls = 0

    def time_out(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private-network-detail", request=request)

    client = _client(httpx.MockTransport(time_out))

    with pytest.raises(DraftWriteAmbiguousError) as raised:
        await client.create_purchase_order_once(vals={"origin": "case-1"})

    assert calls == 1
    assert "private-network-detail" not in str(raised.value)


@pytest.mark.anyio
async def test_create_purchase_order_once_returns_the_new_id() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[42])

    client = _client(httpx.MockTransport(respond))

    result = await client.create_purchase_order_once(
        vals={"origin": "case-1", "partner_id": 7}
    )

    assert result == [42]
    assert requests[0].url.path == "/json/2/purchase.order/create"
    assert json.loads(requests[0].content) == {
        "vals_list": [{"origin": "case-1", "partner_id": 7}]
    }


@pytest.mark.anyio
async def test_find_purchase_order_draft_returns_none_when_absent() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=[])))
    adapter = OdooErpAdapter(client=client, company_id=7)

    result = await adapter.find_purchase_order_draft(origin="scan-001:product-101")

    assert result is None


@pytest.mark.anyio
async def test_find_purchase_order_draft_maps_the_existing_row() -> None:
    row = {
        "id": 42,
        "write_date": "2026-08-20 00:00:00",
        "state": "draft",
        "partner_id": [7, "Fictional Vendor"],
        "currency_id": [1, "USD"],
        "amount_total": 125.0,
    }
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=[row])))
    adapter = OdooErpAdapter(client=client, company_id=7)

    result = await adapter.find_purchase_order_draft(origin="scan-001:product-101")

    assert result == PurchaseOrderDraft(
        po_id=42,
        write_date="2026-08-20 00:00:00",
        state="draft",
        partner_id=7,
        currency_id=1,
        amount_total=Decimal("125.0"),
    )


@pytest.mark.anyio
async def test_find_purchase_order_draft_rejects_more_than_one_match() -> None:
    row = {
        "id": 1,
        "write_date": "2026-08-20 00:00:00",
        "state": "draft",
        "partner_id": [7, "Fictional Vendor"],
        "currency_id": [1, "USD"],
        "amount_total": 1.0,
    }
    client = _client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=[row, row]))
    )
    adapter = OdooErpAdapter(client=client, company_id=7)

    with pytest.raises(OdooDraftMappingError):
        await adapter.find_purchase_order_draft(origin="scan-001:product-101")


@pytest.mark.anyio
async def test_create_purchase_order_draft_resolves_currency_and_uom_then_creates() -> (
    None
):
    responses = iter(
        (
            [{"id": 2, "name": "USD"}],  # search_currency_by_code
            [{"id": 5, "replenishment_uom_id": [9, "Units"]}],  # replenishment UoM
            [99],  # create
            [
                {
                    "id": 99,
                    "write_date": "2026-08-20 00:00:00",
                    "state": "draft",
                    "partner_id": [7, "Fictional Vendor"],
                    "currency_id": [2, "USD"],
                    "amount_total": 125.0,
                }
            ],  # read_purchase_order
        )
    )
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=next(responses))

    client = _client(httpx.MockTransport(respond))
    adapter = OdooErpAdapter(client=client, company_id=7)

    result = await adapter.create_purchase_order_draft(_command())

    assert result == PurchaseOrderDraft(
        po_id=99,
        write_date="2026-08-20 00:00:00",
        state="draft",
        partner_id=7,
        currency_id=2,
        amount_total=Decimal("125.0"),
    )
    assert [request.url.path for request in requests] == [
        "/json/2/res.currency/search_read",
        "/json/2/stock.warehouse.orderpoint/search_read",
        "/json/2/purchase.order/create",
        "/json/2/purchase.order/read",
    ]
    create_payload = json.loads(requests[2].content)
    order_line = create_payload["vals_list"][0]["order_line"][0][2]
    assert order_line["product_id"] == 31
    assert order_line["product_uom_id"] == 9
    assert order_line["product_qty"] == 10.0
    assert order_line["price_unit"] == 12.5


@pytest.mark.anyio
async def test_create_purchase_order_draft_confirmation_read_failure_is_ambiguous() -> (
    None
):
    """Once a PO is created (its id is known), any failure confirming its
    snapshot must still be treated as ambiguous -- never surfaced as an
    unrelated internal error -- so the caller resolves it via search."""

    responses = iter(
        (
            [{"id": 2, "name": "USD"}],
            [{"id": 5, "replenishment_uom_id": [9, "Units"]}],
            [99],
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(200, json=next(responses))
        except StopIteration:
            return httpx.Response(500, json={"error": "boom"})

    client = _client(httpx.MockTransport(respond))
    adapter = OdooErpAdapter(client=client, company_id=7)

    with pytest.raises(DraftWriteAmbiguousError):
        await adapter.create_purchase_order_draft(_command())
