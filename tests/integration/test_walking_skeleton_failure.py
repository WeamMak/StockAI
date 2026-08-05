"""Timeout-path test across the actual local API and MCP processes."""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic, sleep

import httpx

from tests.support.local_skeleton import run_local_skeleton


def _poll_scan(client: httpx.Client, location: str) -> httpx.Response:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(location)
        if response.json()["status"] not in {"queued", "running"}:
            return response
        sleep(0.01)
    raise AssertionError("The timed-out local scan did not reach a terminal state.")


def _events(log_path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _event(
    events: list[dict[str, object]],
    event_name: str,
) -> dict[str, object]:
    return next(event for event in events if event.get("event") == event_name)


def test_local_timeout_is_bounded_observable_and_safe(tmp_path: Path) -> None:
    with run_local_skeleton(tmp_path, erp_mode="timeout") as skeleton:
        with httpx.Client(base_url=skeleton.api_url, timeout=5) as client:
            accepted = client.post("/api/v1/scans")
            detail = _poll_scan(client, accepted.headers["location"])
            api_metrics = client.get("/metrics").text
        mcp_metrics = httpx.get(
            f"{skeleton.mcp_url}/metrics",
            timeout=5,
        ).text
        api_log_text = skeleton.api_log_path.read_text(encoding="utf-8")
        mcp_log_text = skeleton.mcp_log_path.read_text(encoding="utf-8")
        api_events = _events(skeleton.api_log_path)
        mcp_events = _events(skeleton.mcp_log_path)

    assert accepted.status_code == 202
    assert detail.status_code == 200
    assert detail.json()["status"] == "failed"
    assert detail.json()["result"] is None
    assert detail.json()["error"] == {
        "error_code": "MCP_TIMEOUT",
        "message": "The procurement source timed out.",
        "retryable": True,
        "retry_count": 2,
    }
    api_mcp_event = _event(api_events, "agent_mcp_call_completed")
    assert api_mcp_event["error_code"] == "MCP_TIMEOUT"
    assert api_mcp_event["retry_count"] == 2
    assert api_mcp_event["status"] == "error"
    scan_event = _event(api_events, "scan_completed")
    assert scan_event["error_code"] == "MCP_TIMEOUT"
    assert scan_event["retry_count"] == 2
    assert scan_event["status"] == "failed"
    mcp_event = _event(mcp_events, "mcp_tool_completed")
    assert mcp_event["error_code"] == "MCP_TIMEOUT"
    assert mcp_event["retry_count"] == 2
    assert mcp_event["status"] == "error"
    assert skeleton.bearer_token not in api_log_text
    assert skeleton.bearer_token not in mcp_log_text
    assert (
        'procurement_agent_mcp_failures_total{error_code="MCP_TIMEOUT",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in api_metrics
    assert (
        'procurement_agent_mcp_timeouts_total{tool="list_replenishment_candidates"} 1.0'
    ) in api_metrics
    assert (
        'procurement_agent_retries_total{operation="list_replenishment_candidates"} 2.0'
    ) in api_metrics
    assert (
        'procurement_mcp_tool_failures_total{error_code="MCP_TIMEOUT",'
        'tool="list_replenishment_candidates"} 1.0'
    ) in mcp_metrics
    assert (
        'procurement_mcp_tool_timeouts_total{tool="list_replenishment_candidates"} 3.0'
    ) in mcp_metrics
    assert (
        'procurement_mcp_tool_retries_total{tool="list_replenishment_candidates"} 2.0'
    ) in mcp_metrics
