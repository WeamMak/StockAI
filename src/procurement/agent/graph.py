"""Construction of the minimal coded LangGraph workflow."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from procurement.agent.nodes.walking_skeleton import WalkingSkeletonNodes
from procurement.agent.state import ApprovalReadyResult, ScanState, UnresolvedResult
from procurement.domain.decisions import DecisionType
from procurement.domain.identifiers import Environment
from procurement.observability.metrics import AgentMetrics, create_agent_metrics
from procurement.ports.decisions import DecisionReader
from procurement.ports.llm import StructuredLlmPort
from procurement.ports.mcp import ProcurementMcpPort, ReplenishmentCandidate


def _build_nodes(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    metrics: AgentMetrics | None,
    logger: logging.Logger | None,
    company_id: str,
    decisions: DecisionReader | None,
    pause_for_decision: bool,
) -> WalkingSkeletonNodes:
    return WalkingSkeletonNodes(
        mcp=mcp,
        llm=llm,
        metrics=metrics or create_agent_metrics(),
        logger=logger or logging.getLogger(__name__),
        company_id=company_id,
        decisions=decisions,
        pause_for_decision=pause_for_decision,
    )


def build_walking_skeleton_graph(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics: AgentMetrics | None = None,
    logger: logging.Logger | None = None,
    company_id: str = "1",
    decisions: DecisionReader | None = None,
) -> CompiledStateGraph[ScanState, None, ScanState, ScanState]:
    """Compile the smallest explicit MCP-to-LLM procurement workflow.

    Evaluates exactly one candidate per invocation: the caller seeds
    ``candidates`` with one item before calling ``ainvoke``.
    """

    nodes = _build_nodes(
        mcp=mcp,
        llm=llm,
        metrics=metrics,
        logger=logger,
        company_id=company_id,
        decisions=decisions,
        pause_for_decision=checkpointer is not None,
    )
    builder = StateGraph(ScanState)
    builder.add_node("gather_evidence", nodes.gather_evidence)
    builder.add_node("resolve_preferences", nodes.resolve_preferences)
    builder.add_node("reason", nodes.reason_about_candidate)
    builder.add_node("create_draft", nodes.create_draft)
    builder.add_edge(START, "gather_evidence")
    builder.add_edge("gather_evidence", "resolve_preferences")
    builder.add_edge("resolve_preferences", "reason")
    builder.add_conditional_edges("reason", _route_after_reason, ["create_draft", END])
    if decisions is None:
        builder.add_edge("create_draft", END)
    else:
        builder.add_node("load_decision", nodes.load_decision)
        builder.add_node("confirm", nodes.confirm)
        builder.add_node("cancel", nodes.cancel)
        builder.add_edge("create_draft", "load_decision")
        builder.add_conditional_edges(
            "load_decision", _route_decision, ["confirm", "cancel", END]
        )
        builder.add_edge("confirm", END)
        builder.add_edge("cancel", END)
    return builder.compile(checkpointer=checkpointer)


async def _route_after_reason(state: ScanState) -> str:
    """Only a validated approval-ready recommendation may create a draft."""

    if isinstance(state.get("result"), ApprovalReadyResult):
        return "create_draft"
    return END


async def _route_decision(state: ScanState) -> str:
    decision_type = state.get("decision_type")
    if decision_type == DecisionType.APPROVE.value:
        return "confirm"
    if decision_type == DecisionType.REJECT.value:
        return "cancel"
    return END


@dataclass(frozen=True, slots=True)
class WalkingSkeletonWorkflow:
    """Adapts the compiled graph and candidate discovery to one scan-service
    facing object: one scan-wide discovery call plus one graph invocation
    per candidate."""

    _graph: CompiledStateGraph[ScanState, None, ScanState, ScanState]
    _nodes: WalkingSkeletonNodes

    async def ainvoke(
        self, state: ScanState, *, config: Mapping[str, object]
    ) -> ScanState:
        result = await self._graph.ainvoke(state, config=cast(RunnableConfig, config))
        return cast(ScanState, result)

    async def discover_candidates(
        self, *, environment: Environment, scan_id: str
    ) -> tuple[ReplenishmentCandidate, ...] | UnresolvedResult:
        return await self._nodes.discover_candidates(
            environment=environment, scan_id=scan_id
        )

    async def aresume_decision(
        self, workflow_thread_id: str, decision_id: str
    ) -> ScanState:
        from langgraph.types import Command

        result = await self._graph.ainvoke(
            Command(resume=decision_id),
            config={"configurable": {"thread_id": workflow_thread_id}},
        )
        return cast(ScanState, result)


def build_walking_skeleton_workflow(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics: AgentMetrics | None = None,
    logger: logging.Logger | None = None,
    company_id: str = "1",
    decisions: DecisionReader | None = None,
) -> WalkingSkeletonWorkflow:
    """Build the full discovery-plus-per-candidate-graph workflow for
    ScanService, satisfying its ScanWorkflow protocol."""

    nodes = _build_nodes(
        mcp=mcp,
        llm=llm,
        metrics=metrics,
        logger=logger,
        company_id=company_id,
        decisions=decisions,
        pause_for_decision=False,
    )
    graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        checkpointer=checkpointer,
        metrics=metrics,
        logger=logger,
        company_id=company_id,
        decisions=decisions,
    )
    return WalkingSkeletonWorkflow(_graph=graph, _nodes=nodes)
