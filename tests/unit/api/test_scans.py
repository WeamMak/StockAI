"""Asynchronous scan creation and polling API behavior."""

from __future__ import annotations

from typing import cast

import anyio
import pytest
from httpx2 import ASGITransport, AsyncClient

from procurement.agent.state import ApprovalReadyResult, ScanState
from procurement.api.app import create_app
from procurement.api.config import ApiSettings
from procurement.api.services.scans import ScanService
from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment


class SuccessfulWorkflow:
    """Complete one fictional read-only scan without external dependencies."""

    async def ainvoke(self, state: ScanState) -> ScanState:
        return {
            **state,
            "result": ApprovalReadyResult(
                product_id="product-101",
                product_name="Fictional Safety Gloves",
                rationale="Projected stock is below the reorder minimum.",
                risk_flags=("LIMITED_WALKING_SKELETON_EVIDENCE",),
            ),
        }


class BlockingWorkflow(SuccessfulWorkflow):
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()

    async def ainvoke(self, state: ScanState) -> ScanState:
        self.started.set()
        await self.release.wait()
        return await super().ainvoke(state)


class NeverFinishesWorkflow:
    async def ainvoke(self, state: ScanState) -> ScanState:
        del state
        await anyio.sleep_forever()
        raise AssertionError("unreachable")


async def _poll_until_finished(
    client: AsyncClient,
    scan_id: str,
) -> dict[str, object]:
    for _ in range(50):
        response = await client.get(f"/api/v1/scans/{scan_id}")
        body = cast(dict[str, object], response.json())
        if body["status"] not in {"queued", "running"}:
            return body
        await anyio.sleep(0.01)
    raise AssertionError("scan did not finish")


@pytest.mark.anyio
async def test_manual_scan_returns_202_and_can_be_polled_to_completion() -> None:
    application = create_app(scan_workflow=SuccessfulWorkflow())
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        accepted = await client.post("/api/v1/scans")
        accepted_body = accepted.json()
        scan_id = accepted_body["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        listed = await client.get("/api/v1/scans")
        metrics = await client.get("/metrics")

    assert accepted.status_code == 202
    assert accepted.headers["location"] == f"/api/v1/scans/{scan_id}"
    assert accepted_body["status"] == "queued"
    assert finished["status"] == "succeeded"
    assert finished["trigger"] == "manual"
    assert finished["result"] == {
        "outcome": "approval_ready",
        "product_id": "product-101",
        "product_name": "Fictional Safety Gloves",
        "rationale": "Projected stock is below the reorder minimum.",
        "risk_flags": ["LIMITED_WALKING_SKELETON_EVIDENCE"],
        "read_only": True,
    }
    assert finished["error"] is None
    assert listed.status_code == 200
    assert listed.json()["scans"][0]["scan_id"] == scan_id
    assert (
        'procurement_scans_total{status="success",trigger="manual"} 1.0' in metrics.text
    )
    assert (
        'procurement_scan_results_total{error_code="none",outcome="approval_ready"} 1.0'
    ) in metrics.text


@pytest.mark.anyio
async def test_only_one_local_scan_can_run_at_a_time() -> None:
    workflow = BlockingWorkflow()
    application = create_app(scan_workflow=workflow)
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        first = await client.post("/api/v1/scans")
        await workflow.started.wait()
        duplicate = await client.post("/api/v1/scans")
        workflow.release.set()
        await _poll_until_finished(client, first.json()["scan_id"])

    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "SCAN_ALREADY_RUNNING"
    assert duplicate.json()["retryable"] is False


@pytest.mark.anyio
async def test_unknown_scan_returns_the_safe_error_envelope() -> None:
    application = create_app(scan_workflow=SuccessfulWorkflow())
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/v1/scans/scan-does-not-exist")

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_FAILED"
    assert response.json()["message"] == "The requested scan was not found."


@pytest.mark.anyio
async def test_internal_scan_requires_its_separate_narrow_cron_credential() -> None:
    cron_token = "fictional-dev-cron-token-at-least-32-characters"
    application = create_app(
        settings=ApiSettings(cron_token=cron_token),
        scan_workflow=SuccessfulWorkflow(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        missing = await client.post("/internal/v1/scans")
        wrong = await client.post(
            "/internal/v1/scans",
            headers={"Authorization": "Bearer wrong-token"},
        )
        malformed = await client.post(
            "/internal/v1/scans",
            headers=[(b"Authorization", b"Bearer \xffunsafe")],
        )
        accepted = await client.post(
            "/internal/v1/scans",
            headers={"Authorization": f"Bearer {cron_token}"},
        )
        finished = await _poll_until_finished(client, accepted.json()["scan_id"])

    assert missing.status_code == 401
    assert missing.json()["error_code"] == "AUTH_REQUIRED"
    assert wrong.status_code == 403
    assert wrong.json()["error_code"] == "FORBIDDEN"
    assert malformed.status_code == 403
    assert malformed.json()["error_code"] == "FORBIDDEN"
    assert cron_token not in missing.text
    assert cron_token not in wrong.text
    assert "unsafe" not in malformed.text
    assert accepted.status_code == 202
    assert finished["trigger"] == "cron"


@pytest.mark.anyio
async def test_non_human_workflow_has_a_bounded_deadline() -> None:
    service = ScanService(
        workflow=NeverFinishesWorkflow(),
        environment=Environment.DEV,
        workflow_timeout_seconds=0.01,
    )
    application = create_app(scan_service=service)
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        accepted = await client.post("/api/v1/scans")
        finished = await _poll_until_finished(client, accepted.json()["scan_id"])

    assert finished["status"] == "failed"
    assert finished["result"] is None
    assert finished["error"] == {
        "error_code": ErrorCode.MCP_TIMEOUT.value,
        "message": "The procurement scan exceeded its workflow deadline.",
        "retryable": True,
        "retry_count": 0,
    }
