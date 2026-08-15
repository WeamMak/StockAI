"""Public behavior of the minimal coded LangGraph scan."""

from __future__ import annotations

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
from procurement.agent.state import ApprovalReadyResult, UnresolvedResult
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
from procurement.observability.metrics import create_agent_metrics
from procurement.ports.erp import ProcurementEvidenceQuery
from procurement.ports.llm import (
    RecommendationDecision,
    StructuredRecommendation,
)
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
            skip_reason_code=None,
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

    state = await graph.ainvoke({"scan_id": "scan-001", "environment": Environment.DEV})

    assert mcp.requests == [(Environment.DEV, 14, 50)]
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
        'tool="list_replenishment_candidates"} 1.0'
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
        {"scan_id": "scan-immutable-case-001", "environment": Environment.DEV},
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


@pytest.mark.anyio
async def test_mcp_timeout_returns_safe_unresolved_result_without_calling_llm() -> None:
    private_detail = "private-upstream-timeout-detail"
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(),
            next_cursor=None,
        ),
        error=McpTimeoutError(retry_count=2, private_detail=private_detail),
    )
    llm = FakeStructuredLlm(
        response=StructuredRecommendation(
            decision=RecommendationDecision.MANUAL_REVIEW,
            product_id=None,
            rationale="Manual review is required.",
            risk_flags=("MCP_UNAVAILABLE",),
            input_tokens=0,
            output_tokens=0,
        )
    )
    metrics = create_agent_metrics()
    graph = build_walking_skeleton_graph(mcp=mcp, llm=llm, metrics=metrics)

    state = await graph.ainvoke(
        {"scan_id": "scan-timeout", "environment": Environment.DEV}
    )

    assert llm.requests == []
    assert state["result"] == UnresolvedResult(
        error_code=ErrorCode.MCP_TIMEOUT,
        message="The procurement source timed out.",
        retryable=True,
        retry_count=2,
    )
    assert private_detail not in repr(state)
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
