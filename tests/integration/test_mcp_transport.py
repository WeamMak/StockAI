"""Authenticated MCP discovery and invocation over real Streamable HTTP."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from procurement.domain.identifiers import Environment
from procurement.mcp_server.server import create_mcp_server
from procurement.ports.erp import CandidatePage, ReplenishmentCandidateRecord
from tests.support.fake_odoo.adapter import FakeOdooAdapter

BEARER_TOKEN = "fictional-dev-mcp-token-at-least-32-characters"


def _adapter() -> FakeOdooAdapter:
    return FakeOdooAdapter(
        page=CandidatePage(
            items=(
                ReplenishmentCandidateRecord(
                    product_id="product-101",
                    product_name="Fictional Safety Gloves",
                    category_id="category-safety",
                    reorder_minimum=Decimal("10.000000"),
                    reorder_maximum=Decimal("40.000000"),
                    projected_quantity=Decimal("8.000000"),
                    projected_trigger_date=date(2026, 8, 8),
                    skip_reason_code=None,
                ),
            ),
            next_cursor=None,
        )
    )


@asynccontextmanager
async def _running_server() -> AsyncIterator[str]:
    mcp = create_mcp_server(
        erp=_adapter(),
        environment=Environment.DEV,
        bearer_token=BEARER_TOKEN,
        host="127.0.0.1",
        port=0,
        retry_delay_seconds=0,
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app=mcp.streamable_http_app(),
            log_level="critical",
            access_log=False,
        )
    )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(server.serve, [listener])
        with anyio.fail_after(5):
            while not server.started:
                await anyio.sleep(0.01)
        try:
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            server.should_exit = True


def _initialize_payload() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "stockai-integration-test", "version": "1.0"},
        },
    }


@pytest.mark.anyio
async def test_missing_and_wrong_bearer_credentials_are_rejected() -> None:
    async with _running_server() as url:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            missing = await client.post(
                url,
                headers=headers,
                json=_initialize_payload(),
            )
            wrong = await client.post(
                url,
                headers={**headers, "Authorization": "Bearer wrong-token"},
                json=_initialize_payload(),
            )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Bearer")
    assert BEARER_TOKEN not in missing.text
    assert BEARER_TOKEN not in wrong.text


@pytest.mark.anyio
async def test_real_client_discovers_and_calls_the_tool_over_streamable_http() -> None:
    async with _running_server() as url:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {BEARER_TOKEN}"}
        ) as http_client:
            async with streamable_http_client(
                url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    result = await session.call_tool(
                        "list_replenishment_candidates",
                        arguments={
                            "environment": "dev",
                            "horizon_days": 14,
                            "limit": 25,
                            "cursor": None,
                        },
                    )
            metrics_response = await http_client.get(
                url.removesuffix("/mcp") + "/metrics"
            )

    assert [tool.name for tool in listed.tools] == ["list_replenishment_candidates"]
    assert listed.tools[0].inputSchema["additionalProperties"] is False
    assert listed.tools[0].outputSchema is not None
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["environment"] == "dev"
    assert result.structuredContent["candidates"][0]["product_id"] == "product-101"
    assert metrics_response.status_code == 200
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in metrics_response.text


@pytest.mark.anyio
async def test_malformed_tool_arguments_return_a_safe_error() -> None:
    unsafe_input = "secret-request-value-that-must-not-be-echoed"

    async with _running_server() as url:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {BEARER_TOKEN}"}
        ) as http_client:
            async with streamable_http_client(
                url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "list_replenishment_candidates",
                        arguments={
                            "environment": "dev",
                            "horizon_days": unsafe_input,
                            "limit": 25,
                            "cursor": None,
                        },
                    )

    assert result.isError is True
    response_text = " ".join(
        block.text for block in result.content if block.type == "text"
    )
    assert "validation error" in response_text.lower()
    assert unsafe_input not in response_text
