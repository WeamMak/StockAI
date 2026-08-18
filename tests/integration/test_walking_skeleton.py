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
            scan_id = detail.json()["scan_id"]
            case_id = detail.json()["results"][0]["case_id"]
            case = client.get(
                f"/api/v1/scans/{scan_id}/cases/{case_id}",
                headers=auth_headers,
            )
            recent = client.get("/api/v1/cases", headers=auth_headers)
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
    assert case.status_code == 200
    assert recent.status_code == 200
    recent_cases = recent.json()["cases"]
    assert any(row["case_id"] == case_id for row in recent_cases)
    matching = next(row for row in recent_cases if row["case_id"] == case_id)
    assert matching["scan_id"] == scan_id
    assert matching["budget_status"] == "within_budget"
    result = case.json()["result"]
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


def test_local_scan_evaluates_multiple_candidates_as_isolated_cases(
    tmp_path: Path,
) -> None:
    with run_local_skeleton(tmp_path, erp_mode="multi") as skeleton:
        with httpx.Client(base_url=skeleton.api_url, timeout=5) as client:
            auth_headers = sign_in_sync(client)
            accepted = client.post("/api/v1/scans", headers=auth_headers)
            detail = _poll_scan(
                client,
                accepted.headers["location"],
                headers=auth_headers,
            )
            scan_id = detail.json()["scan_id"]
            cases = {
                row["product_id"]: client.get(
                    f"/api/v1/scans/{scan_id}/cases/{row['case_id']}",
                    headers=auth_headers,
                ).json()
                for row in detail.json()["results"]
            }

    assert accepted.status_code == 202
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "succeeded"
    assert len(body["results"]) == 2
    assert body["outcome_counts"] == {"approval_ready": 1, "no_valid_offer": 1}
    assert cases["product-101"]["result"]["outcome"] == "approval_ready"
    assert cases["product-102"]["result"]["outcome"] == "no_valid_offer"
    assert cases["product-102"]["result"]["product_id"] == "product-102"


def test_local_case_can_be_refined_once_with_an_officer_note(
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
            scan_id = detail.json()["scan_id"]
            case_id = detail.json()["results"][0]["case_id"]

            refined = client.post(
                f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
                headers=auth_headers,
                json={"note": "Prioritize delivery speed this time."},
            )
            refined_detail = _poll_scan(
                client,
                f"/api/v1/scans/{scan_id}/cases/{case_id}",
                headers=auth_headers,
            )

    assert refined.status_code == 202
    assert refined.json()["status"] == "running"
    assert refined_detail.status_code == 200
    assert refined_detail.json()["status"] == "succeeded"
    assert refined_detail.json()["refinement_count"] == 1
    assert refined_detail.json()["result"]["outcome"] == "approval_ready"
