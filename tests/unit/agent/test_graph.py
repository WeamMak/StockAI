"""Checkpoint-phase behavior of the procurement workflow adapter."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.support.fakes.llm import FakeStructuredLlm
from tests.support.recommendations import t27_recommendation
from tests.unit.agent.test_walking_skeleton import (
    DecisionReaderFake,
    FakeMcp,
    _candidate,
)

from procurement.agent.graph import (
    DraftCheckpointError,
    WalkingSkeletonWorkflow,
    build_walking_skeleton_workflow,
)
from procurement.agent.state import ApprovalReadyResult
from procurement.domain.identifiers import Environment
from procurement.ports.mcp import CandidatePage


def _workflow() -> tuple[WalkingSkeletonWorkflow, FakeMcp]:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    workflow = build_walking_skeleton_workflow(
        mcp=mcp,
        llm=FakeStructuredLlm(response=t27_recommendation()),
        checkpointer=InMemorySaver(),
        decisions=DecisionReaderFake({}),
    )
    return workflow, mcp


@pytest.mark.anyio
async def test_aensure_draft_resumes_only_once_and_returns_existing_draft() -> None:
    workflow, mcp = _workflow()
    thread_id = "scan-001:product-101"
    await workflow.ainvoke(
        {
            "scan_id": "scan-001",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    ready = await workflow._graph.aget_state({"configurable": {"thread_id": thread_id}})
    ready_result = ready.values.get("result")
    assert isinstance(ready_result, ApprovalReadyResult), ready
    assert ready_result.evidence is None
    assert ready.values["draft_command"].origin == thread_id

    first = await workflow.aensure_draft(thread_id)
    assert first.get("draft") is not None, (first, mcp.draft_requests)
    assert first["draft"].po_id == 41
    snapshot = await workflow._graph.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert snapshot.values.get("draft") is not None, snapshot
    assert tuple(snapshot.next) == ("load_decision",)

    second = await workflow.aensure_draft(thread_id)

    assert second["draft"].po_id == 41
    assert len(mcp.draft_requests) == 1


@pytest.mark.anyio
async def test_aensure_draft_rejects_an_unknown_checkpoint_without_writing() -> None:
    workflow, mcp = _workflow()

    with pytest.raises(DraftCheckpointError, match="not awaiting draft submission"):
        await workflow.aensure_draft("unknown-thread")

    assert mcp.draft_requests == []
