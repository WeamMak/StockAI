"""Real API to LangGraph to authenticated Streamable HTTP MCP interaction."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import cast

import anyio
import httpx
import pytest
import uvicorn
from httpx2 import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from procurement.agent.graph import build_walking_skeleton_graph
from procurement.api.app import create_app
from procurement.api.observability import create_http_metrics
from procurement.api.services.scans import ScanWorkflow
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import (
    ProcurementEvidence,
    procurement_evidence_from_dict,
)
from procurement.mcp_server.server import create_mcp_server
from procurement.observability.metrics import create_agent_metrics
from procurement.ports.erp import CandidatePage as ErpCandidatePage
from procurement.ports.erp import ReplenishmentCandidateRecord
from procurement.ports.llm import (
    RecommendationDecision,
    StructuredRecommendation,
)
from procurement.ports.mcp import (
    CandidatePage,
    McpUnavailableError,
    ProcurementMcpPort,
    ReplenishmentCandidate,
)
from tests.support.fake_odoo.adapter import FakeOdooAdapter
from tests.support.fakes.llm import FakeStructuredLlm
from tests.support.local_identity import LocalIdentityProvider, sign_in

BEARER_TOKEN = "fictional-dev-mcp-token-at-least-32-characters"


def _erp_adapter() -> FakeOdooAdapter:
    return FakeOdooAdapter(
        page=ErpCandidatePage(
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
async def _running_mcp_server() -> AsyncIterator[tuple[str, str]]:
    mcp = create_mcp_server(
        erp=_erp_adapter(),
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
            base_url = f"http://127.0.0.1:{port}"
            yield f"{base_url}/mcp", f"{base_url}/metrics"
        finally:
            server.should_exit = True


class RealTransportMcpClient(ProcurementMcpPort):
    """Integration-only port adapter using the real Python MCP client."""

    def __init__(self, *, url: str, bearer_token: str) -> None:
        self._url = url
        self._bearer_token = bearer_token

    async def list_replenishment_candidates(
        self,
        *,
        environment: Environment,
        horizon_days: int,
        limit: int,
    ) -> CandidatePage:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bearer_token}"}
        ) as http_client:
            async with streamable_http_client(
                self._url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "list_replenishment_candidates",
                        arguments={
                            "environment": environment.value,
                            "horizon_days": horizon_days,
                            "limit": limit,
                            "cursor": None,
                        },
                    )
        if result.isError or not isinstance(result.structuredContent, Mapping):
            raise McpUnavailableError(retry_count=0)
        return _candidate_page(result.structuredContent)

    async def get_procurement_evidence(
        self,
        *,
        environment: Environment,
        product_id: str,
        horizon_days: int,
    ) -> ProcurementEvidence:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bearer_token}"}
        ) as http_client:
            async with streamable_http_client(
                self._url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "get_procurement_evidence",
                        arguments={
                            "environment": environment.value,
                            "product_id": product_id,
                            "horizon_days": horizon_days,
                        },
                    )
        if result.isError or not isinstance(result.structuredContent, Mapping):
            raise McpUnavailableError(retry_count=0)
        try:
            return procurement_evidence_from_dict(dict(result.structuredContent))
        except (TypeError, ValueError) as error:
            raise McpUnavailableError(retry_count=0, private_detail=error) from None


def _candidate_page(payload: Mapping[str, object]) -> CandidatePage:
    if set(payload) != {"environment", "candidates", "next_cursor"}:
        raise McpUnavailableError(retry_count=0)
    raw_candidates = payload["candidates"]
    if not isinstance(raw_candidates, list):
        raise McpUnavailableError(retry_count=0)
    candidates: list[ReplenishmentCandidate] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, Mapping):
            raise McpUnavailableError(retry_count=0)
        skip_metadata = raw_candidate.get("skip_metadata")
        skip_reason = (
            skip_metadata.get("reason_code")
            if isinstance(skip_metadata, Mapping)
            else None
        )
        candidates.append(
            ReplenishmentCandidate(
                product_id=str(raw_candidate.get("product_id", "")),
                product_name=str(raw_candidate.get("product_name", "")),
                category_id=str(raw_candidate.get("category_id", "")),
                reorder_minimum=Decimal(str(raw_candidate.get("reorder_minimum", ""))),
                reorder_maximum=Decimal(str(raw_candidate.get("reorder_maximum", ""))),
                projected_quantity=Decimal(
                    str(raw_candidate.get("projected_quantity", ""))
                ),
                projected_trigger_date=date.fromisoformat(
                    str(raw_candidate.get("projected_trigger_date", ""))
                ),
                skip_reason_code=(
                    str(skip_reason) if skip_reason is not None else None
                ),
            )
        )
    return CandidatePage(
        environment=Environment(str(payload["environment"])),
        candidates=tuple(candidates),
        next_cursor=(
            str(payload["next_cursor"]) if payload["next_cursor"] is not None else None
        ),
    )


@pytest.mark.anyio
async def test_api_scan_runs_langgraph_and_real_mcp_transport() -> None:
    http_metrics = create_http_metrics()
    agent_metrics = create_agent_metrics(http_metrics.registry)
    llm = FakeStructuredLlm(
        response=StructuredRecommendation(
            decision=RecommendationDecision.RECOMMEND,
            product_id="product-101",
            rationale="Projected stock is below the configured reorder minimum.",
            risk_flags=("LIMITED_WALKING_SKELETON_EVIDENCE",),
            input_tokens=48,
            output_tokens=19,
        )
    )

    async with _running_mcp_server() as (mcp_url, mcp_metrics_url):
        graph = build_walking_skeleton_graph(
            mcp=RealTransportMcpClient(
                url=mcp_url,
                bearer_token=BEARER_TOKEN,
            ),
            llm=llm,
            metrics=agent_metrics,
        )
        application = create_app(
            http_metrics=http_metrics,
            agent_metrics=agent_metrics,
            scan_workflow=cast(ScanWorkflow, graph),
            identity_provider=LocalIdentityProvider(),
        )
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport,
            base_url="https://testserver",
        ) as client:
            csrf_headers = await sign_in(client)
            accepted = await client.post("/api/v1/scans", headers=csrf_headers)
            scan_id = accepted.json()["scan_id"]
            for _ in range(100):
                detail = await client.get(f"/api/v1/scans/{scan_id}")
                if detail.json()["status"] not in {"queued", "running"}:
                    break
                await anyio.sleep(0.01)
            api_metrics = await client.get("/metrics")
        async with httpx.AsyncClient() as client:
            mcp_metrics = await client.get(mcp_metrics_url)

    assert accepted.status_code == 202
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["result"]["product_id"] == "product-101"
    assert len(llm.requests) == 1
    assert (
        'procurement_agent_mcp_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in api_metrics.text
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in mcp_metrics.text
