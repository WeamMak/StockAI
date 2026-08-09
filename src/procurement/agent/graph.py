"""Construction of the minimal coded LangGraph workflow."""

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from procurement.agent.nodes.walking_skeleton import WalkingSkeletonNodes
from procurement.agent.state import ScanState
from procurement.observability.metrics import AgentMetrics, create_agent_metrics
from procurement.ports.llm import StructuredLlmPort
from procurement.ports.mcp import ProcurementMcpPort


def build_walking_skeleton_graph(
    *,
    mcp: ProcurementMcpPort,
    llm: StructuredLlmPort,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics: AgentMetrics | None = None,
    logger: logging.Logger | None = None,
) -> CompiledStateGraph[ScanState, None, ScanState, ScanState]:
    """Compile the smallest explicit MCP-to-LLM procurement workflow."""

    nodes = WalkingSkeletonNodes(
        mcp=mcp,
        llm=llm,
        metrics=metrics or create_agent_metrics(),
        logger=logger or logging.getLogger(__name__),
    )
    builder = StateGraph(ScanState)
    builder.add_node("discover_candidates", nodes.discover_candidates)
    builder.add_node("reason", nodes.reason_about_candidate)
    builder.add_edge(START, "discover_candidates")
    builder.add_edge("discover_candidates", "reason")
    builder.add_edge("reason", END)
    return builder.compile(checkpointer=checkpointer)
