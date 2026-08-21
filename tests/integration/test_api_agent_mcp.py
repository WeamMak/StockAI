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
from langgraph.checkpoint.memory import InMemorySaver
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from procurement.agent.graph import build_walking_skeleton_workflow
from procurement.api.app import create_app
from procurement.api.auth.session import UserRole
from procurement.api.observability import create_http_metrics
from procurement.api.services.scans import ScanWorkflow
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import (
    ProcurementEvidence,
    procurement_evidence_from_dict,
)
from procurement.domain.policy.preferences import (
    ProcurementPreference,
    preference_from_dict,
)
from procurement.mcp_server.server import create_mcp_server
from procurement.observability.metrics import create_agent_metrics
from procurement.ports.erp import CandidatePage as ErpCandidatePage
from procurement.ports.erp import ReplenishmentCandidateRecord
from procurement.ports.mcp import (
    CandidatePage,
    DecisionOutcome,
    McpApprovalStaleError,
    McpDecisionReconciliationRequiredError,
    McpDraftReconciliationRequiredError,
    McpUnavailableError,
    ProcurementMcpPort,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
    ReplenishmentCandidate,
)
from procurement.ports.repositories import InMemoryApplicationRepository
from tests.support.fake_odoo.adapter import FakeOdooAdapter
from tests.support.fakes.llm import EvidenceAwareFakeStructuredLlm
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
async def _running_mcp_server(
    repository: InMemoryApplicationRepository | None = None,
) -> AsyncIterator[tuple[str, str]]:
    mcp = create_mcp_server(
        erp=_erp_adapter(),
        decisions=repository,
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

    async def get_procurement_preferences(
        self,
        *,
        environment: Environment,
        company_id: str,
        category_id: str,
        product_id: str,
    ) -> ProcurementPreference:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bearer_token}"}
        ) as http_client:
            async with streamable_http_client(self._url, http_client=http_client) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "get_procurement_preferences",
                        arguments={
                            "environment": environment.value,
                            "company_id": company_id,
                            "category_id": category_id,
                            "product_id": product_id,
                        },
                    )
        if result.isError or not isinstance(result.structuredContent, Mapping):
            raise McpUnavailableError(retry_count=0)
        try:
            return preference_from_dict(dict(result.structuredContent))
        except (TypeError, ValueError) as error:
            raise McpUnavailableError(retry_count=0, private_detail=error) from None

    async def create_purchase_order_draft(
        self,
        *,
        environment: Environment,
        command: PurchaseOrderDraftCommand,
    ) -> PurchaseOrderDraft:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bearer_token}"}
        ) as http_client:
            async with streamable_http_client(self._url, http_client=http_client) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "create_purchase_order_draft",
                        arguments={
                            "environment": environment.value,
                            "origin": command.origin,
                            "vendor_id": command.vendor_id,
                            "currency_code": command.currency_code,
                            "product_id": command.product_id,
                            "product_name": command.product_name,
                            "quantity": format(command.quantity, "f"),
                            "unit_price": format(command.unit_price, "f"),
                            "need_by_date": command.need_by_date.isoformat(),
                        },
                    )
        if result.isError:
            payload = result.structuredContent
            if (
                isinstance(payload, Mapping)
                and payload.get("error_code") == "RECONCILIATION_REQUIRED"
            ):
                raise McpDraftReconciliationRequiredError(retry_count=0)
            raise McpUnavailableError(retry_count=0)
        if not isinstance(result.structuredContent, Mapping):
            raise McpUnavailableError(retry_count=0)
        try:
            return _purchase_order_draft(result.structuredContent)
        except (TypeError, ValueError) as error:
            raise McpUnavailableError(retry_count=0, private_detail=error) from None

    async def confirm_purchase_order(
        self, *, environment: Environment, decision_id: str, idempotency_key: str
    ) -> DecisionOutcome:
        return await self._decision_action(
            tool="confirm_purchase_order",
            environment=environment,
            decision_id=decision_id,
            idempotency_key=idempotency_key,
        )

    async def cancel_draft_purchase_order(
        self, *, environment: Environment, decision_id: str, idempotency_key: str
    ) -> DecisionOutcome:
        return await self._decision_action(
            tool="cancel_draft_purchase_order",
            environment=environment,
            decision_id=decision_id,
            idempotency_key=idempotency_key,
        )

    async def _decision_action(
        self,
        *,
        tool: str,
        environment: Environment,
        decision_id: str,
        idempotency_key: str,
    ) -> DecisionOutcome:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bearer_token}"}
        ) as http_client:
            async with streamable_http_client(self._url, http_client=http_client) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        tool,
                        arguments={
                            "environment": environment.value,
                            "decision_id": decision_id,
                            "idempotency_key": idempotency_key,
                        },
                    )
        payload = result.structuredContent
        if result.isError:
            if isinstance(payload, Mapping):
                if payload.get("error_code") == "APPROVAL_STALE":
                    raise McpApprovalStaleError(retry_count=0)
                if payload.get("error_code") == "RECONCILIATION_REQUIRED":
                    raise McpDecisionReconciliationRequiredError(retry_count=0)
            raise McpUnavailableError(retry_count=0)
        if not isinstance(payload, Mapping):
            raise McpUnavailableError(retry_count=0)
        if tool == "confirm_purchase_order":
            return DecisionOutcome.confirmed(
                decision_id=decision_id,
                po_id=int(cast(int, payload["po_id"])),
                po_reference=str(payload["po_reference"]),
                write_date=str(payload["write_date"]),
                reconciled=bool(payload["reconciled"]),
            )
        return DecisionOutcome.cancelled(
            decision_id=decision_id,
            po_id=int(cast(int, payload["po_id"])),
            po_reference=str(payload["po_reference"]),
            write_date=str(payload["write_date"]),
            reconciled=bool(payload["reconciled"]),
        )


def _purchase_order_draft(payload: Mapping[str, object]) -> PurchaseOrderDraft:
    if set(payload) != {
        "po_id",
        "write_date",
        "state",
        "partner_id",
        "currency_id",
        "amount_total",
    }:
        raise ValueError("purchase-order draft payload is invalid")
    return PurchaseOrderDraft(
        po_id=int(cast(int, payload["po_id"])),
        write_date=str(payload["write_date"]),
        state=str(payload["state"]),
        partner_id=int(cast(int, payload["partner_id"])),
        currency_id=int(cast(int, payload["currency_id"])),
        amount_total=Decimal(str(payload["amount_total"])),
    )


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
    llm = EvidenceAwareFakeStructuredLlm()

    async with _running_mcp_server() as (mcp_url, mcp_metrics_url):
        workflow = build_walking_skeleton_workflow(
            mcp=RealTransportMcpClient(
                url=mcp_url,
                bearer_token=BEARER_TOKEN,
            ),
            llm=llm,
            checkpointer=InMemorySaver(),
            metrics=agent_metrics,
        )
        application = create_app(
            http_metrics=http_metrics,
            agent_metrics=agent_metrics,
            scan_workflow=cast(ScanWorkflow, workflow),
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
            case_id = detail.json()["results"][0]["case_id"]
            case = await client.get(f"/api/v1/scans/{scan_id}/cases/{case_id}")
            api_metrics = await client.get("/metrics")
        async with httpx.AsyncClient() as client:
            mcp_metrics = await client.get(mcp_metrics_url)

    assert accepted.status_code == 202
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert case.status_code == 200
    case_body = case.json()
    assert case_body["status"] == "pending_approval"
    assert case_body["result"]["product_id"] == "product-101"
    assert len(llm.requests) == 1
    assert (
        'procurement_agent_mcp_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in api_metrics.text
    assert (
        'procurement_agent_mcp_calls_total{status="success",'
        'tool="create_purchase_order_draft"} 1.0'
    ) in api_metrics.text
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in mcp_metrics.text
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="create_purchase_order_draft"} 1.0'
    ) in mcp_metrics.text


@pytest.mark.anyio
async def test_manager_approval_resumes_exact_graph_thread_and_confirms_over_mcp() -> (
    None
):
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    http_metrics = create_http_metrics()
    agent_metrics = create_agent_metrics(http_metrics.registry)

    async with _running_mcp_server(repository) as (mcp_url, _):
        workflow = build_walking_skeleton_workflow(
            mcp=RealTransportMcpClient(url=mcp_url, bearer_token=BEARER_TOKEN),
            llm=EvidenceAwareFakeStructuredLlm(),
            checkpointer=InMemorySaver(),
            metrics=agent_metrics,
            decisions=repository,
        )
        application = create_app(
            http_metrics=http_metrics,
            agent_metrics=agent_metrics,
            scan_workflow=cast(ScanWorkflow, workflow),
            application_repository=repository,
            identity_provider=LocalIdentityProvider(role=UserRole.MANAGER),
        )
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="https://testserver",
        ) as client:
            csrf = await sign_in(client)
            accepted = await client.post("/api/v1/scans", headers=csrf)
            scan_id = accepted.json()["scan_id"]
            for _ in range(100):
                aggregate = await client.get(f"/api/v1/scans/{scan_id}")
                if aggregate.json()["status"] not in {"queued", "running"}:
                    break
                await anyio.sleep(0.01)
            case_id = aggregate.json()["results"][0]["case_id"]
            for _ in range(100):
                case_response = await client.get(
                    f"/api/v1/scans/{scan_id}/cases/{case_id}"
                )
                case = case_response.json()
                if case["status"] == "pending_approval":
                    break
                await anyio.sleep(0.01)
            result = case["result"]
            evidence = next(
                item
                for item in case["evidence"]
                if item["product_id"] == result["product_id"]
            )
            offer = next(
                item
                for item in evidence["offers"]
                if item["offer_id"] == result["offer_id"]
            )
            decision = await client.post(
                f"/api/v1/cases/{case_id}/approve",
                headers={**csrf, "Idempotency-Key": "approve-integration-001"},
                json={
                    "environment": "dev",
                    "case_revision": case["revision"],
                    "po_id": case["draft"]["po_id"],
                    "po_revision": case["draft"]["write_date"],
                    "vendor_id": offer["vendor_id"],
                    "quantity": result["quantity"],
                    "amount": result["normalized_cost"],
                    "currency": offer["currency"],
                    "budget_status": result["budget_status"],
                    "overage": evidence["budget"]["overage"],
                    "evidence_digest": result["evidence_digest"],
                    "budget_exception": False,
                    "justification": None,
                },
            )
            for _ in range(100):
                terminal_response = await client.get(
                    f"/api/v1/scans/{scan_id}/cases/{case_id}"
                )
                terminal = terminal_response.json()
                if terminal["status"] == "confirmed":
                    break
                await anyio.sleep(0.01)
            audit = await client.get(f"/api/v1/cases/{case_id}/audit")

    assert decision.status_code == 202, decision.text
    assert terminal["result"]["outcome"] == "approval_ready"
    assert terminal["decision"]["status"] == "confirmed"
    assert terminal["decision"]["po_reference"] == "P00001"
    assert [event["event_type"] for event in audit.json()["events"]][-3:] == [
        "manager_approved",
        "confirming",
        "confirmed",
    ]
