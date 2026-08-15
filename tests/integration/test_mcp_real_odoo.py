"""Real Odoo JSON-2 to MCP to LangGraph walking-skeleton integration."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from scripts.odoo.probe_contract import Json2Client

from procurement.agent.graph import build_walking_skeleton_graph
from procurement.agent.state import ApprovalReadyResult
from procurement.bootstrap.mcp import LocalMcpSettings, create_local_mcp_app
from procurement.domain.identifiers import Environment
from procurement.ports.llm import (
    RecommendationDecision,
    StructuredRecommendation,
)
from tests.contract.conftest import OdooContractStack, _run, running_odoo_contract
from tests.integration.test_api_agent_mcp import RealTransportMcpClient
from tests.support.fakes.llm import FakeStructuredLlm

BEARER_TOKEN = "fictional-real-odoo-mcp-token-at-least-32-characters"


def _seed(stack: OdooContractStack) -> None:
    seeded = _run(
        [
            *stack.compose_command,
            "exec",
            "-T",
            "-e",
            "STOCKAI_ODOO_SEED_ENVIRONMENT=dev",
            "odoo",
            "bash",
            "-lc",
            (
                'odoo shell --no-http --database="$ODOO_CONTRACT_DATABASE" '
                '--db_host="$HOST" --db_port="$PORT" --db_user="$USER" '
                '--db_password="$PASSWORD" --log-level=error '
                "< /opt/stockai/seed.py"
            ),
        ],
        environment=stack.environment,
        check=False,
    )
    assert seeded.returncode == 0, seeded.stderr


@asynccontextmanager
async def _running_real_odoo_mcp(
    stack: OdooContractStack,
) -> AsyncIterator[tuple[str, str]]:
    fixture = stack.first_bootstrap["fixture"]
    assert isinstance(fixture, Mapping)
    company_id = fixture["company_id"]
    assert type(company_id) is int
    application = create_local_mcp_app(
        LocalMcpSettings(
            environment=Environment.DEV,
            bearer_token=BEARER_TOKEN,
            erp_mode="odoo",
            read_timeout_seconds=10,
            max_retries=2,
            retry_delay_seconds=0,
            odoo_url=stack.base_url,
            odoo_database=stack.database,
            odoo_api_key=stack.api_key,
            odoo_company_id=company_id,
        )
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app=application, log_level="critical", access_log=False)
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


async def _call_candidate_tool(url: str) -> Mapping[str, object]:
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
                        "horizon_days": 14,
                        "limit": 25,
                        "cursor": None,
                    },
                )
    assert result.isError is False
    assert isinstance(result.structuredContent, Mapping)
    return result.structuredContent


@pytest.mark.anyio
async def test_seeded_odoo_candidate_reaches_the_walking_skeleton_over_real_mcp(
    running_odoo_contract: OdooContractStack,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(running_odoo_contract)
    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, Mapping)
    company_id = fixture["company_id"]
    assert type(company_id) is int
    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
    ) as odoo:
        seeded_products = odoo.call(
            "product.product",
            "search_read",
            {
                "domain": [["default_code", "like", "STOCKAI-DEV-%"]],
                "fields": ["id", "categ_id", "default_code"],
                "limit": 10,
            },
        )
    assert isinstance(seeded_products, list)

    async with _running_real_odoo_mcp(running_odoo_contract) as (
        mcp_url,
        metrics_url,
    ):
        payload = await _call_candidate_tool(mcp_url)
        candidates = payload["candidates"]
        assert isinstance(candidates, list)
        seeded = next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, Mapping)
                and "Happy" in str(candidate.get("product_name", ""))
            ),
            None,
        )
        assert seeded is not None, candidates
        product_id = seeded["product_id"]
        assert isinstance(product_id, str)

        transport = RealTransportMcpClient(
            url=mcp_url,
            bearer_token=BEARER_TOKEN,
        )
        resolved_scopes = {}
        for product in seeded_products:
            assert isinstance(product, Mapping)
            candidate_product_id = product["id"]
            candidate_category = product["categ_id"]
            candidate_code = product["default_code"]
            assert type(candidate_product_id) is int
            assert (
                isinstance(candidate_category, list)
                and type(candidate_category[0]) is int
            )
            assert isinstance(candidate_code, str)
            profile = await transport.get_procurement_preferences(
                environment=Environment.DEV,
                company_id=str(company_id),
                category_id=str(candidate_category[0]),
                product_id=str(candidate_product_id),
            )
            resolved_scopes[candidate_code] = profile.scope.value
        assert resolved_scopes == {
            "STOCKAI-DEV-HAPPY": "product",
            "STOCKAI-DEV-NO-OFFER": "company",
            "STOCKAI-DEV-OVER": "category",
        }
        evidence = await transport.get_procurement_evidence(
            environment=Environment.DEV,
            product_id=product_id,
            horizon_days=14,
        )
        assert evidence.skip_reason_code is None, evidence.to_dict()
        assert evidence.offers

        llm = FakeStructuredLlm(
            response=StructuredRecommendation(
                decision=RecommendationDecision.RECOMMEND,
                product_id=product_id,
                rationale="Projected stock is below the configured reorder minimum.",
                risk_flags=("LIMITED_WALKING_SKELETON_EVIDENCE",),
                input_tokens=48,
                output_tokens=19,
            )
        )
        graph = build_walking_skeleton_graph(
            mcp=transport,
            llm=llm,
            company_id=str(company_id),
        )
        state = await graph.ainvoke(
            {"scan_id": "scan-real-odoo-001", "environment": Environment.DEV}
        )
        async with httpx.AsyncClient() as client:
            metrics = await client.get(metrics_url)

    result = state["result"]
    assert isinstance(result, ApprovalReadyResult)
    assert result.product_id == product_id
    assert result.product_name == seeded["product_name"]
    assert len(llm.requests) == 1
    assert metrics.status_code == 200
    assert (
        'procurement_odoo_calls_total{operation="search_candidate_orderpoints",'
        'status="success"} 2.0'
    ) in metrics.text
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 2.0'
    ) in metrics.text
    logs = capsys.readouterr().out
    assert running_odoo_contract.api_key not in logs
    assert "fictional-t10-postgres-password" not in logs


__all__ = ["running_odoo_contract"]
