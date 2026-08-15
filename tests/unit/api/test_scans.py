"""Asynchronous scan creation and polling API behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import anyio
import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in
from tests.support.recommendations import t27_approval_result

from procurement.agent.state import ScanState
from procurement.api.app import create_app
from procurement.api.auth.session import UserRole
from procurement.api.config import ApiSettings
from procurement.api.services.scans import ScanService, ScanTrigger
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.ports.repositories import (
    CaseRecord,
    InMemoryApplicationRepository,
    RevisionConflictError,
)


class SuccessfulWorkflow:
    """Complete one fictional read-only scan without external dependencies."""

    def __init__(self) -> None:
        self.configs: list[Mapping[str, object]] = []

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.configs.append(config)
        return {
            **state,
            "result": t27_approval_result(),
        }


class BlockingWorkflow(SuccessfulWorkflow):
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.started.set()
        await self.release.wait()
        return await super().ainvoke(state, config=config)


class NeverFinishesWorkflow:
    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        del state
        del config
        await anyio.sleep_forever()
        raise AssertionError("unreachable")


class FailFirstUpdateRepository(InMemoryApplicationRepository):
    """Simulate one conditional persistence failure before workflow entry."""

    def __init__(self) -> None:
        super().__init__(environment=Environment.DEV)
        self._failures_remaining = 1

    async def update_case(
        self,
        record: CaseRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> CaseRecord:
        if self._failures_remaining:
            self._failures_remaining -= 1
            raise RevisionConflictError("simulated revision conflict")
        return await super().update_case(
            record,
            expected_revision=expected_revision,
            expires_at=expires_at,
        )


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
    workflow = SuccessfulWorkflow()
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
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
    result = cast(dict[str, object], finished["result"])
    assert result["outcome"] == "approval_ready"
    assert result["product_id"] == "product-101"
    assert result["offer_id"] == "offer-101"
    assert result["quantity"] == "35.000000"
    assert result["normalized_cost"] == "437.500000"
    assert result["budget_status"] == "within_budget"
    assert result["preference_revision"] == 1
    assert result["read_only"] is True
    assert finished["error"] is None
    assert listed.status_code == 200
    assert listed.json()["scans"][0]["scan_id"] == scan_id
    assert (
        'procurement_scans_total{status="success",trigger="manual"} 1.0' in metrics.text
    )
    assert (
        'procurement_scan_results_total{error_code="none",outcome="approval_ready"} 1.0'
    ) in metrics.text
    assert workflow.configs == [
        {"configurable": {"thread_id": scan_id}},
    ]


@pytest.mark.anyio
async def test_scan_record_survives_a_new_api_service_instance() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    first_service = ScanService(
        workflow=SuccessfulWorkflow(),
        environment=Environment.DEV,
        repository=repository,
    )
    first_application = create_app(
        scan_service=first_service,
        identity_provider=LocalIdentityProvider(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=first_application),
        base_url="https://first-process",
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)

    second_service = ScanService(
        workflow=SuccessfulWorkflow(),
        environment=Environment.DEV,
        repository=repository,
    )
    second_application = create_app(
        scan_service=second_service,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=second_application),
        base_url="https://second-process",
    ) as client:
        await sign_in(client)
        restored = await client.get(f"/api/v1/scans/{scan_id}")
        listed = await client.get("/api/v1/scans")

    assert finished["status"] == "succeeded"
    assert restored.status_code == 200
    assert restored.json() == finished
    assert listed.json()["scans"][0]["scan_id"] == scan_id


@pytest.mark.anyio
async def test_persistence_failure_releases_the_local_scan_slot() -> None:
    service = ScanService(
        workflow=SuccessfulWorkflow(),
        environment=Environment.DEV,
        repository=FailFirstUpdateRepository(),
    )

    first = await service.start_scan(trigger=ScanTrigger.MANUAL)
    for _ in range(50):
        try:
            second = await service.start_scan(trigger=ScanTrigger.MANUAL)
        except DomainError as error:
            assert error.error_code is ErrorCode.SCAN_ALREADY_RUNNING
            await anyio.sleep(0.01)
            continue
        break
    else:
        raise AssertionError("persistence failure did not release the scan slot")

    assert second.scan_id != first.scan_id
    for _ in range(50):
        finished = await service.get_scan(second.scan_id)
        if finished.status.value not in {"queued", "running"}:
            break
        await anyio.sleep(0.01)
    else:
        raise AssertionError("replacement scan did not finish")
    assert finished.status.value == "succeeded"


@pytest.mark.anyio
async def test_only_one_local_scan_can_run_at_a_time() -> None:
    workflow = BlockingWorkflow()
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        first = await client.post("/api/v1/scans", headers=csrf_headers)
        await workflow.started.wait()
        duplicate = await client.post("/api/v1/scans", headers=csrf_headers)
        workflow.release.set()
        await _poll_until_finished(client, first.json()["scan_id"])

    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "SCAN_ALREADY_RUNNING"
    assert duplicate.json()["retryable"] is False


@pytest.mark.anyio
async def test_unknown_scan_returns_the_safe_error_envelope() -> None:
    application = create_app(
        scan_workflow=SuccessfulWorkflow(),
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        await sign_in(client)
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
        identity_provider=LocalIdentityProvider(role=UserRole.MANAGER),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        await sign_in(client)
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
    application = create_app(
        scan_service=service,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        finished = await _poll_until_finished(client, accepted.json()["scan_id"])

    assert finished["status"] == "failed"
    assert finished["result"] is None
    assert finished["error"] == {
        "error_code": ErrorCode.MCP_TIMEOUT.value,
        "message": "The procurement scan exceeded its workflow deadline.",
        "retryable": True,
        "retry_count": 0,
    }
