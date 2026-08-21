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
from procurement.domain.identifiers import Environment
from procurement.observability.metrics import AgentMetrics, create_agent_metrics
from procurement.ports.llm import StructuredLlmPort
from procurement.ports.mcp import ProcurementMcpPort, ReplenishmentCandidate


def _build_nodes(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    metrics: AgentMetrics | None,
    logger: logging.Logger | None,
    company_id: str,
) -> WalkingSkeletonNodes:
    return WalkingSkeletonNodes(
        mcp=mcp,
        llm=llm,
        metrics=metrics or create_agent_metrics(),
        logger=logger or logging.getLogger(__name__),
        company_id=company_id,
    )


def build_walking_skeleton_graph(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics: AgentMetrics | None = None,
    logger: logging.Logger | None = None,
    company_id: str = "1",
) -> CompiledStateGraph[ScanState, None, ScanState, ScanState]:
    """Compile the smallest explicit MCP-to-LLM procurement workflow.

    Evaluates exactly one candidate per invocation: the caller seeds
    ``candidates`` with one item before calling ``ainvoke``.
    """

    nodes = _build_nodes(
        mcp=mcp, llm=llm, metrics=metrics, logger=logger, company_id=company_id
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
    builder.add_edge("create_draft", END)
    return builder.compile(checkpointer=checkpointer)


def _route_after_reason(state: ScanState) -> str:
    """Only a validated approval-ready recommendation may create a draft."""

    if isinstance(state.get("result"), ApprovalReadyResult):
        return "create_draft"
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


def build_walking_skeleton_workflow(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics: AgentMetrics | None = None,
    logger: logging.Logger | None = None,
    company_id: str = "1",
) -> WalkingSkeletonWorkflow:
    """Build the full discovery-plus-per-candidate-graph workflow for
    ScanService, satisfying its ScanWorkflow protocol."""

    nodes = _build_nodes(
        mcp=mcp, llm=llm, metrics=metrics, logger=logger, company_id=company_id
    )
    graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        checkpointer=checkpointer,
        metrics=metrics,
        logger=logger,
        company_id=company_id,
    )
    return WalkingSkeletonWorkflow(_graph=graph, _nodes=nodes)
