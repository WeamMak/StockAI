"""Public-contract tests for the narrow Odoo JSON-2 client."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date
from io import StringIO

import httpx
import pytest
from prometheus_client import generate_latest

from procurement.adapters.odoo.client import (
    OdooErpAdapter,
    OdooJson2Client,
    OdooReadTimeoutError,
    create_odoo_metrics,
)
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    ErpUnavailableError,
    ReplenishmentCandidatesQuery,
)


@pytest.mark.anyio
async def test_candidate_reads_use_fixed_json2_operations_and_private_headers() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    client = OdooJson2Client(
        base_url="https://odoo.example.invalid",
        database="stockai_dev",
        api_key="private-odoo-key",
        transport=httpx.MockTransport(respond),
    )

    orderpoints = await client.search_candidate_orderpoints(
        company_id=7,
        after_id=11,
        limit=26,
    )
    products = await client.read_candidate_products(product_ids=(31, 32))

    assert orderpoints == []
    assert products == []
    assert [request.url.path for request in requests] == [
        "/json/2/stock.warehouse.orderpoint/search_read",
        "/json/2/product.product/read",
    ]
    assert all(
        request.headers["Authorization"] == "bearer private-odoo-key"
        and request.headers["X-Odoo-Database"] == "stockai_dev"
        and request.extensions["timeout"]["read"] == 10.0
        for request in requests
    )
    assert json.loads(requests[0].content) == {
        "domain": [
            ["active", "=", True],
            ["company_id", "=", 7],
            ["product_id.active", "=", True],
            ["product_id.is_storable", "=", True],
            ["product_id.purchase_ok", "=", True],
            ["id", ">", 11],
        ],
        "fields": [
            "id",
            "active",
            "trigger",
            "product_id",
            "product_min_qty",
            "product_max_qty",
            "company_id",
            "qty_forecast",
            "qty_to_order",
            "write_date",
        ],
        "limit": 26,
        "order": "id asc",
    }
    assert json.loads(requests[1].content) == {
        "ids": [31, 32],
        "fields": [
            "id",
            "name",
            "categ_id",
            "active",
            "is_storable",
            "purchase_ok",
        ],
    }
    assert "private-odoo-key" not in repr(client)


@pytest.mark.anyio
async def test_transient_failures_retry_twice_with_bounded_backoff() -> None:
    responses = iter((503, 429, 200))
    calls = 0
    delays: list[float] = []

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(next(responses), json=[])

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    client = OdooJson2Client(
        base_url="https://odoo.example.invalid",
        database="stockai_dev",
        api_key="private-odoo-key",
        retry_delay_seconds=0.01,
        sleep=record_delay,
        jitter=lambda _lower, upper: upper,
        transport=httpx.MockTransport(respond),
    )

    result = await client.search_candidate_orderpoints(
        company_id=7,
        after_id=0,
        limit=26,
    )

    assert result == []
    assert calls == 3
    assert delays == [0.01, 0.02]


@pytest.mark.anyio
async def test_permanent_odoo_error_is_not_retried_or_exposed() -> None:
    calls = 0
    unsafe_detail = "private-odoo-error-body"

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"debug": unsafe_detail})

    client = OdooJson2Client(
        base_url="https://odoo.example.invalid",
        database="stockai_dev",
        api_key="private-odoo-key",
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(ErpUnavailableError) as raised:
        await client.search_candidate_orderpoints(
            company_id=7,
            after_id=0,
            limit=26,
        )

    assert calls == 1
    assert raised.value.retry_count == 0
    assert str(raised.value) == "The procurement source is unavailable."
    assert unsafe_detail not in str(raised.value)
    assert "private-odoo-key" not in str(raised.value)


@pytest.mark.anyio
async def test_timeout_retries_twice_then_returns_a_safe_timeout() -> None:
    calls = 0

    def time_out(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private-network-detail", request=request)

    client = OdooJson2Client(
        base_url="https://odoo.example.invalid",
        database="stockai_dev",
        api_key="private-odoo-key",
        retry_delay_seconds=0,
        transport=httpx.MockTransport(time_out),
    )

    with pytest.raises(OdooReadTimeoutError) as raised:
        await client.search_candidate_orderpoints(
            company_id=7,
            after_id=0,
            limit=26,
        )

    assert calls == 3
    assert raised.value.retry_count == 2
    assert str(raised.value) == "The procurement source timed out."
    assert "private-network-detail" not in str(raised.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "respond",
    [
        lambda _request: httpx.Response(200, content=b"x" * 33),
        lambda _request: httpx.Response(200, content=b'{"incomplete":'),
    ],
)
async def test_oversized_or_malformed_json_is_rejected_without_retry(
    respond: Callable[[httpx.Request], httpx.Response],
) -> None:
    calls = 0

    def counted(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return respond(request)

    async def no_sleep(_delay: float) -> None:
        raise AssertionError("permanent response failures must not retry")

    typed_sleep: Callable[[float], Awaitable[None]] = no_sleep
    client = OdooJson2Client(
        base_url="https://odoo.example.invalid",
        database="stockai_dev",
        api_key="private-odoo-key",
        max_response_bytes=32,
        sleep=typed_sleep,
        transport=httpx.MockTransport(counted),
    )

    with pytest.raises(ErpUnavailableError):
        await client.search_candidate_orderpoints(
            company_id=7,
            after_id=0,
            limit=26,
        )

    assert calls == 1


@pytest.mark.anyio
async def test_real_adapter_maps_candidates_and_emits_bounded_odoo_signals() -> None:
    stream = StringIO()
    logger = configure_json_logging(
        service="procurement-mcp",
        environment="dev",
        stream=stream,
        logger_name="procurement.test.odoo.adapter",
    )
    metrics = create_odoo_metrics()
    responses = iter(
        (
            [
                {
                    "id": 11,
                    "active": True,
                    "trigger": "manual",
                    "product_id": [31, "DEV Fictional Component"],
                    "product_min_qty": 5.0,
                    "product_max_qty": 20.0,
                    "company_id": [7, "Fictional Dev Company"],
                    "qty_forecast": -2.25,
                    "qty_to_order": 22.25,
                    "write_date": "2026-08-09 10:30:00",
                }
            ],
            [
                {
                    "id": 31,
                    "name": "DEV Fictional Component",
                    "categ_id": [41, "DEV Components"],
                    "active": True,
                    "is_storable": True,
                    "purchase_ok": True,
                }
            ],
        )
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    adapter = OdooErpAdapter(
        client=OdooJson2Client(
            base_url="https://odoo.example.invalid",
            database="stockai_dev",
            api_key="private-odoo-key",
            metrics=metrics,
            logger=logger,
            transport=httpx.MockTransport(respond),
        ),
        company_id=7,
        today=lambda: date(2026, 8, 9),
    )

    page = await adapter.list_replenishment_candidates(
        ReplenishmentCandidatesQuery(horizon_days=14, limit=25)
    )

    assert [item.product_id for item in page.items] == ["31"]
    metric_text = generate_latest(metrics.registry).decode()
    assert (
        'procurement_odoo_calls_total{operation="search_candidate_orderpoints",'
        'status="success"} 1.0'
    ) in metric_text
    assert (
        'procurement_odoo_calls_total{operation="read_candidate_products",'
        'status="success"} 1.0'
    ) in metric_text
    logs = stream.getvalue()
    assert logs.count('"event":"odoo_call_completed"') == 2
    assert "private-odoo-key" not in logs
