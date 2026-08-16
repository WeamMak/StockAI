"""Happy-path test across the actual local API and MCP processes."""

from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep

import httpx

from tests.support.local_identity import sign_in_sync
from tests.support.local_skeleton import run_local_skeleton


def _poll_scan(
    client: httpx.Client,
    location: str,
    *,
    headers: dict[str, str],
) -> httpx.Response:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(location, headers=headers)
        if response.json()["status"] not in {"queued", "running"}:
            return response
        sleep(0.01)
    raise AssertionError("The local scan did not reach a terminal state.")


def test_local_processes_run_langgraph_over_real_mcp_transport(
    tmp_path: Path,
) -> None:
    with run_local_skeleton(tmp_path) as skeleton:
        with httpx.Client(base_url=skeleton.api_url, timeout=5) as client:
            auth_headers = sign_in_sync(client)
            accepted = client.post("/api/v1/scans", headers=auth_headers)
            detail = _poll_scan(
                client,
                accepted.headers["location"],
                headers=auth_headers,
            )
            api_metrics = client.get("/metrics").text
        mcp_metrics = httpx.get(
            f"{skeleton.mcp_url}/metrics",
            timeout=5,
        ).text
        api_logs = skeleton.api_log_path.read_text(encoding="utf-8")
        mcp_logs = skeleton.mcp_log_path.read_text(encoding="utf-8")

    assert accepted.status_code == 202
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    result = detail.json()["result"]
    assert result["outcome"] == "approval_ready"
    assert result["product_id"] == "product-101"
    assert result["offer_id"] == "offer-101"
    assert result["quantity"] == "35.000000"
    assert result["normalized_cost"] == "437.500000"
    assert result["budget_status"] == "within_budget"
    assert result["preference_revision"] == 1
    assert result["read_only"] is True
    assert "agent_mcp_call_completed" in api_logs
    assert "llm_call_completed" in api_logs
    assert "mcp_tool_completed" in mcp_logs
    assert skeleton.bearer_token not in api_logs
    assert skeleton.bearer_token not in mcp_logs
    assert (
        'procurement_agent_mcp_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in api_metrics
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in mcp_metrics
