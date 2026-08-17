"""Public behavior of the minimal coded LangGraph scan."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import generate_latest
from tests.support.fakes.llm import FakeStructuredLlm
from tests.support.recommendations import t27_recommendation

from procurement.agent.graph import build_walking_skeleton_graph
from procurement.agent.nodes.walking_skeleton import WalkingSkeletonNodes
from procurement.agent.state import (
    ApprovalReadyResult,
    NoValidOfferResult,
    UnresolvedResult,
)
from procurement.bootstrap.mcp import _fictional_evidence
from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
)
from procurement.observability.logging import configure_json_logging
from procurement.observability.metrics import AgentMetrics, create_agent_metrics
from procurement.ports.erp import ProcurementEvidenceQuery
from procurement.ports.mcp import (
    CandidatePage,
    McpTimeoutError,
    ProcurementMcpPort,
    ReplenishmentCandidate,
)


def _evidence(environment: Environment = Environment.DEV) -> ProcurementEvidence:
    return _fictional_evidence(ProcurementEvidenceQuery(environment, "product-101", 14))


def _candidate() -> ReplenishmentCandidate:
    return ReplenishmentCandidate(
        product_id="product-101",
        product_name="Fictional Safety Gloves",
        category_id="category-safety",
        reorder_minimum=Decimal("10.000000"),
        reorder_maximum=Decimal("40.000000"),
        projected_quantity=Decimal("8.000000"),
        projected_trigger_date=date(2026, 8, 8),
        skip_reason_code=None,
    )


@dataclass(slots=True)
class FakeMcp(ProcurementMcpPort):
    page: CandidatePage
    error: Exception | None = None
    evidence_skip_reason_code: str | None = None
    requests: list[tuple[Environment, int, int]] = field(default_factory=list)
    evidence_requests: list[tuple[Environment, str, int]] = field(default_factory=list)
    preference_requests: list[tuple[Environment, str, str, str]] = field(
        default_factory=list
    )

    async def list_replenishment_candidates(
        self,
        *,
        environment: Environment,
        horizon_days: int,
        limit: int,
    ) -> CandidatePage:
        self.requests.append((environment, horizon_days, limit))
        if self.error is not None:
            raise self.error
        return self.page

    async def get_procurement_evidence(
        self,
        *,
        environment: Environment,
        product_id: str,
        horizon_days: int,
    ) -> ProcurementEvidence:
        self.evidence_requests.append((environment, product_id, horizon_days))
        if self.error is not None:
            raise self.error
        return replace(
            _evidence(environment),
            evidence_id=f"{environment.value}:evidence-{product_id}",
            product_id=product_id,
            skip_reason_code=self.evidence_skip_reason_code,
        )

    async def get_procurement_preferences(
        self,
        *,
        environment: Environment,
        company_id: str,
        category_id: str,
        product_id: str,
    ) -> ProcurementPreference:
        self.preference_requests.append(
            (environment, company_id, category_id, product_id)
        )
        if self.error is not None:
            raise self.error
        return ProcurementPreference(
            profile_id="preference-1",
            company_id=company_id,
            category_id=category_id,
            product_id=product_id,
            scope=PreferenceScope.COMPANY,
            scope_id=company_id,
            revision=1,
            ordered_criteria=(
                PreferenceCriterion.RELIABILITY,
                PreferenceCriterion.DELIVERY,
                PreferenceCriterion.PRICE,
            ),
            max_price_premium_percent=Decimal("25.000000"),
            enforcement_mode=PremiumEnforcement.ADVISORY,
            precedence_source=PreferenceScope.COMPANY,
        )


@pytest.mark.anyio
async def test_graph_returns_one_approval_ready_read_only_result() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    metrics = create_agent_metrics()
    stream = StringIO()
    graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        metrics=metrics,
        logger=configure_json_logging(
            service="procurement-api",
            environment="dev",
            stream=stream,
            logger_name="procurement.test.agent.success",
        ),
    )

    state = await graph.ainvoke(
        {
            "scan_id": "scan-001",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        }
    )

    assert mcp.requests == []  # discovery runs once per scan, outside the graph
    assert mcp.evidence_requests == [(Environment.DEV, "product-101", 14)]
    assert mcp.preference_requests == [
        (Environment.DEV, "1", "category-safety", "product-101")
    ]
    assert len(llm.requests) == 1
    assert llm.requests[0].candidates == (_candidate(),)
    result = state["result"]
    assert isinstance(result, ApprovalReadyResult)
    assert result.product_id == "product-101"
    assert result.product_name == "Fictional Safety Gloves"
    assert result.evidence is not None
    assert result.evidence.preferences is not None
    assert result.evidence.preferences.profile.profile_id == "preference-1"
    assert result.read_only is True
    metric_text = generate_latest(metrics.registry).decode()
    assert (
        'procurement_agent_mcp_calls_total{status="success",'
        'tool="get_procurement_evidence"} 1.0'
    ) in metric_text
    assert 'procurement_llm_calls_total{status="success"} 1.0' in metric_text
    assert 'procurement_llm_tokens_total{direction="input"} 48.0' in metric_text
    assert 'procurement_llm_tokens_total{direction="output"} 19.0' in metric_text
    assert '"event":"agent_mcp_call_completed"' in stream.getvalue()
    assert '"event":"llm_call_completed"' in stream.getvalue()
    assert "Fictional Safety Gloves" not in stream.getvalue()


@pytest.mark.anyio
async def test_checkpoint_retains_result_but_not_transient_odoo_data() -> None:
    saver = InMemorySaver()
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    config: RunnableConfig = {"configurable": {"thread_id": "scan-immutable-case-001"}}
    first_graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        checkpointer=saver,
    )

    await first_graph.ainvoke(
        {
            "scan_id": "scan-immutable-case-001",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config=config,
    )
    restarted_graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        checkpointer=saver,
    )
    snapshot = await restarted_graph.aget_state(config)

    assert snapshot.values["scan_id"] == "scan-immutable-case-001"
    assert isinstance(snapshot.values["result"], ApprovalReadyResult)
    assert "candidates" not in snapshot.values
    assert "evidence" not in snapshot.values
    assert "recommendation" not in snapshot.values


def _nodes(
    mcp: ProcurementMcpPort, llm: FakeStructuredLlm, metrics: AgentMetrics
) -> WalkingSkeletonNodes:
    return WalkingSkeletonNodes(
        mcp=mcp,
        llm=llm,
        metrics=metrics,
        logger=logging.getLogger("procurement.test.agent"),
        company_id="1",
    )


@pytest.mark.anyio
async def test_discover_candidates_is_callable_directly_without_the_graph() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    metrics = create_agent_metrics()
    nodes = _nodes(mcp, FakeStructuredLlm(response=t27_recommendation()), metrics)

    candidates = await nodes.discover_candidates(
        environment=Environment.DEV, scan_id="scan-001"
    )

    assert candidates == (_candidate(),)
    assert mcp.requests == [(Environment.DEV, 14, 50)]


@pytest.mark.anyio
async def test_discover_candidates_returns_unresolved_when_empty() -> None:
    mcp = FakeMcp(
        page=CandidatePage(environment=Environment.DEV, candidates=(), next_cursor=None)
    )
    metrics = create_agent_metrics()
    nodes = _nodes(mcp, FakeStructuredLlm(response=t27_recommendation()), metrics)

    result = await nodes.discover_candidates(
        environment=Environment.DEV, scan_id="scan-001"
    )

    assert isinstance(result, UnresolvedResult)
    assert result.error_code is ErrorCode.NO_VALID_OFFER


@pytest.mark.anyio
async def test_discover_candidates_mcp_timeout_returns_safe_unresolved_result() -> None:
    private_detail = "private-upstream-timeout-detail"
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV, candidates=(), next_cursor=None
        ),
        error=McpTimeoutError(retry_count=2, private_detail=private_detail),
    )
    metrics = create_agent_metrics()
    nodes = _nodes(mcp, FakeStructuredLlm(response=t27_recommendation()), metrics)

    result = await nodes.discover_candidates(
        environment=Environment.DEV, scan_id="scan-timeout"
    )

    assert result == UnresolvedResult(
        error_code=ErrorCode.MCP_TIMEOUT,
        message="The procurement source timed out.",
        retryable=True,
        retry_count=2,
    )
    assert private_detail not in repr(result)
    metric_text = generate_latest(metrics.registry).decode()
    assert (
        'procurement_agent_mcp_failures_total{error_code="MCP_TIMEOUT",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in metric_text
    assert (
        'procurement_agent_mcp_timeouts_total{tool="list_replenishment_candidates"} 1.0'
    ) in metric_text
    assert (
        'procurement_agent_retries_total{operation="list_replenishment_candidates"} 2.0'
    ) in metric_text


@pytest.mark.anyio
async def test_graph_produces_no_valid_offer_result_for_zero_eligible_offers() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV, candidates=(_candidate(),), next_cursor=None
        ),
        evidence_skip_reason_code="NO_VALID_OFFER",
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(
        mcp=mcp, llm=llm, metrics=create_agent_metrics()
    )

    state = await graph.ainvoke(
        {
            "scan_id": "scan-1",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config={"configurable": {"thread_id": "scan-1:product-101"}},
    )

    result = state["result"]
    assert isinstance(result, NoValidOfferResult)
    assert result.product_id == "product-101"
    assert llm.requests == []  # deterministic skip must never reach the LLM


@pytest.mark.anyio
async def test_graph_skips_silently_when_fully_covered() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV, candidates=(_candidate(),), next_cursor=None
        ),
        evidence_skip_reason_code="FULLY_COVERED",
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(
        mcp=mcp, llm=llm, metrics=create_agent_metrics()
    )

    state = await graph.ainvoke(
        {
            "scan_id": "scan-1",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config={"configurable": {"thread_id": "scan-1:product-101"}},
    )

    assert "result" not in state
    assert state["skip_reason"] == "FULLY_COVERED"
    assert llm.requests == []


@pytest.mark.anyio
async def test_graph_treats_budget_unavailable_as_a_retryable_failure() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV, candidates=(_candidate(),), next_cursor=None
        ),
        evidence_skip_reason_code="BUDGET_UNAVAILABLE",
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(
        mcp=mcp, llm=llm, metrics=create_agent_metrics()
    )

    state = await graph.ainvoke(
        {
            "scan_id": "scan-1",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config={"configurable": {"thread_id": "scan-1:product-101"}},
    )

    result = state["result"]
    assert isinstance(result, UnresolvedResult)
    assert result.error_code is ErrorCode.ODOO_UNAVAILABLE
    assert result.retryable is True
    assert llm.requests == []
