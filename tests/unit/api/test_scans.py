"""Asynchronous scan creation and polling API behavior."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import anyio
import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in
from tests.support.recommendations import t27_approval_result, t27_request

from procurement.agent.state import ManualReviewResult, ScanState
from procurement.api.app import create_app
from procurement.api.auth.session import UserRole
from procurement.api.config import ApiSettings
from procurement.api.routes.scans import case_response
from procurement.api.services.scans import ScanService, ScanTrigger
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.ports.mcp import ReplenishmentCandidate
from procurement.ports.repositories import (
    CaseRecord,
    InMemoryApplicationRepository,
    RecommendationRecord,
    RevisionConflictError,
)


def _one_candidate() -> tuple[ReplenishmentCandidate, ...]:
    return t27_request().candidates


class SuccessfulWorkflow:
    """Complete one fictional read-only scan producing one approval-ready case."""

    def __init__(self) -> None:
        self.configs: list[Mapping[str, object]] = []

    async def discover_candidates(
        self, *, environment: Environment, scan_id: str
    ) -> tuple[ReplenishmentCandidate, ...]:
        del environment, scan_id
        return _one_candidate()

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


class MultiCandidateWorkflow:
    """Complete one scan producing several independent per-product cases."""

    def __init__(self, *, candidate_count: int) -> None:
        self.candidate_count = candidate_count
        self.configs: list[Mapping[str, object]] = []

    async def discover_candidates(
        self, *, environment: Environment, scan_id: str
    ) -> tuple[ReplenishmentCandidate, ...]:
        del environment, scan_id
        base = _one_candidate()[0]
        return tuple(
            ReplenishmentCandidate(
                product_id=f"product-{index}",
                product_name=f"Fictional Product {index}",
                category_id=base.category_id,
                reorder_minimum=base.reorder_minimum,
                reorder_maximum=base.reorder_maximum,
                projected_quantity=base.projected_quantity,
                projected_trigger_date=base.projected_trigger_date,
                skip_reason_code=None,
            )
            for index in range(self.candidate_count)
        )

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.configs.append(config)
        candidate = state["candidates"][0]
        return {
            **state,
            "result": replace(
                t27_approval_result(),
                product_id=candidate.product_id,
                product_name=candidate.product_name,
            ),
        }


class RefinableWorkflow(SuccessfulWorkflow):
    """Record officer notes and let a test control the returned result."""

    def __init__(self) -> None:
        super().__init__()
        self.officer_notes: list[str | None] = []

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.configs.append(config)
        self.officer_notes.append(state.get("officer_note"))
        return {
            **state,
            "result": replace(
                t27_approval_result(),
                rationale=f"Refined: {state.get('officer_note')}",
            ),
        }


class FailingRefinementWorkflow(SuccessfulWorkflow):
    """Succeed on the initial scan, then raise on every refinement attempt."""

    def __init__(self) -> None:
        super().__init__()
        self._invocations = 0

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.configs.append(config)
        self._invocations += 1
        if self._invocations == 1:
            return {**state, "result": t27_approval_result()}
        raise RuntimeError("simulated workflow failure during refinement")


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
    async def discover_candidates(
        self, *, environment: Environment, scan_id: str
    ) -> tuple[ReplenishmentCandidate, ...]:
        del environment, scan_id
        return _one_candidate()

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


class ConflictingUpdateRepository(InMemoryApplicationRepository):
    """Force the next update_case call to fail as a real race would."""

    def __init__(self) -> None:
        super().__init__(environment=Environment.DEV)
        self.raise_next_update = False

    async def update_case(
        self,
        record: CaseRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> CaseRecord:
        if self.raise_next_update:
            self.raise_next_update = False
            raise RevisionConflictError("simulated concurrent refinement")
        return await super().update_case(
            record, expected_revision=expected_revision, expires_at=expires_at
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


async def _poll_case_until_finished(
    client: AsyncClient, scan_id: str, case_id: str
) -> dict[str, object]:
    for _ in range(500):
        response = await client.get(f"/api/v1/scans/{scan_id}/cases/{case_id}")
        body = cast(dict[str, object], response.json())
        if body["status"] not in {"queued", "running"}:
            return body
        await anyio.sleep(0.01)
    raise AssertionError("case did not finish")


async def _approval_ready_case(
    client: AsyncClient, csrf_headers: dict[str, str]
) -> tuple[str, str]:
    accepted = await client.post("/api/v1/scans", headers=csrf_headers)
    scan_id = accepted.json()["scan_id"]
    finished = await _poll_until_finished(client, scan_id)
    case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]
    return scan_id, case_id


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
        case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]
        case = await client.get(f"/api/v1/scans/{scan_id}/cases/{case_id}")
        listed = await client.get("/api/v1/scans")
        metrics = await client.get("/metrics")

    assert accepted.status_code == 202
    assert accepted.headers["location"] == f"/api/v1/scans/{scan_id}"
    assert accepted_body["status"] == "queued"
    assert finished["status"] == "succeeded"
    assert finished["trigger"] == "manual"
    assert finished["error"] is None
    results = cast(list[dict[str, object]], finished["results"])
    assert len(results) == 1
    assert results[0]["outcome"] == "approval_ready"
    assert results[0]["scan_id"] == scan_id
    assert results[0]["budget_status"] == "within_budget"
    assert results[0]["completed_at"] is not None
    assert finished["outcome_counts"] == {"approval_ready": 1}
    result = cast(dict[str, object], case.json()["result"])
    assert result["outcome"] == "approval_ready"
    assert result["validation_level"] == "t27"
    assert result["product_id"] == "product-101"
    assert result["offer_id"] == "offer-101"
    assert result["quantity"] == "35.000000"
    assert result["normalized_cost"] == "437.500000"
    assert result["budget_status"] == "within_budget"
    assert result["preference_revision"] == 1
    assert result["read_only"] is True
    assert case.json()["error"] is None
    assert listed.status_code == 200
    assert listed.json()["scans"][0]["scan_id"] == scan_id
    assert (
        'procurement_scans_total{status="success",trigger="manual"} 1.0' in metrics.text
    )
    assert (
        'procurement_scan_results_total{error_code="none",outcome="approval_ready"} 1.0'
    ) in metrics.text
    assert workflow.configs == [
        {"configurable": {"thread_id": f"{scan_id}:product-101"}},
    ]


@pytest.mark.anyio
async def test_a_completed_case_persists_its_candidate_snapshot() -> None:
    workflow = SuccessfulWorkflow()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]

    record = await repository.get_case(CaseId(Environment.DEV, case_id))
    candidate = _one_candidate()[0]
    assert record is not None
    assert record.candidate_snapshot is not None
    assert record.candidate_snapshot.category_id == candidate.category_id
    assert record.candidate_snapshot.reorder_minimum == candidate.reorder_minimum
    assert record.candidate_snapshot.reorder_maximum == candidate.reorder_maximum
    assert record.candidate_snapshot.projected_quantity == candidate.projected_quantity
    assert (
        record.candidate_snapshot.projected_trigger_date
        == candidate.projected_trigger_date
    )
    assert record.refinement_count == 0


@pytest.mark.anyio
async def test_refine_case_reruns_the_workflow_with_a_fresh_thread_id() -> None:
    workflow = RefinableWorkflow()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]

        refined = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "Prioritize delivery speed this time."},
        )
        assert refined.status_code == 202
        assert refined.json()["status"] == "running"

        completed = await _poll_case_until_finished(client, scan_id, case_id)

    assert completed["refinement_count"] == 1
    assert completed["result"]["rationale"] == (
        "Refined: Prioritize delivery speed this time."
    )
    assert workflow.officer_notes == [None, "Prioritize delivery speed this time."]
    assert workflow.configs[0] == {"configurable": {"thread_id": case_id}}
    assert workflow.configs[1] == {"configurable": {"thread_id": f"{case_id}:refine-1"}}


@pytest.mark.anyio
async def test_refine_case_is_capped_at_three_attempts() -> None:
    workflow = RefinableWorkflow()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)
        for _ in range(3):
            await client.post(
                f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
                headers=csrf_headers,
                json={"note": "Try again."},
            )
            await _poll_case_until_finished(client, scan_id, case_id)

        rejected = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "One more time."},
        )

    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "REFINEMENT_LIMIT_REACHED"


@pytest.mark.anyio
async def test_refine_case_rejects_a_manual_review_case() -> None:
    class ManualReviewWorkflow(SuccessfulWorkflow):
        async def ainvoke(
            self, state: ScanState, *, config: Mapping[str, object]
        ) -> ScanState:
            self.configs.append(config)
            return {
                **state,
                "result": ManualReviewResult(
                    rationale="Evidence is insufficient.",
                    trade_offs=(),
                    risk_flags=("MANUAL_REVIEW_REQUIRED",),
                    uncertainty="No model selection is available.",
                    evidence_limitations=(),
                ),
            }

    application = create_app(
        scan_workflow=ManualReviewWorkflow(),
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)

        rejected = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "Try again."},
        )

    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "VALIDATION_FAILED"


@pytest.mark.anyio
async def test_concurrent_refinement_attempts_conflict() -> None:
    """A second writer racing the same expected_revision loses, deterministically.

    Two genuinely concurrent HTTP requests would race unpredictably in this
    sandbox; ConflictingUpdateRepository instead forces the exact interleaving
    a real race would produce -- the second update_case call for this case
    sees a stale expected_revision -- so the resulting REVISION_CONFLICT
    translation is tested deterministically.
    """

    workflow = SuccessfulWorkflow()
    repository = ConflictingUpdateRepository()
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)

        repository.raise_next_update = True
        rejected = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "This attempt loses the simulated race."},
        )

    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "REVISION_CONFLICT"


@pytest.mark.anyio
async def test_a_failed_refinement_still_counts_against_the_cap() -> None:
    workflow = FailingRefinementWorkflow()
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)

        refined = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "Try again."},
        )
        assert refined.status_code == 202
        completed = await _poll_case_until_finished(client, scan_id, case_id)

    assert completed["status"] == "failed"
    assert completed["refinement_count"] == 1
    assert completed["error"]["error_code"] == "LLM_UNAVAILABLE"


@pytest.mark.anyio
async def test_manual_scan_produces_one_independent_case_per_candidate() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=3)
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
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)

    results = cast(list[dict[str, object]], finished["results"])
    assert len(results) == 3
    assert {row["product_id"] for row in results} == {
        "product-0",
        "product-1",
        "product-2",
    }
    assert len({row["case_id"] for row in results}) == 3
    assert finished["outcome_counts"] == {"approval_ready": 3}
    assert len(workflow.configs) == 3


def test_historical_success_remains_approval_ready_without_t27_claims() -> None:
    timestamp = UtcTimestamp(datetime(2026, 8, 15, 19, 5, tzinfo=UTC))
    record = CaseRecord(
        case_id=CaseId(Environment.DEV, "scan-legacy:product-legacy"),
        revision=Revision(2),
        status="succeeded",
        trigger="manual",
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
        result=RecommendationRecord(
            product_id="product-legacy",
            product_name="Legacy Product",
            rationale="One eligible candidate was available.",
            risk_flags=(),
        ),
    )

    response = case_response(ScanService._snapshot(record)).model_dump()
    result = cast(dict[str, object], response["result"])

    assert response["scan_id"] == "scan-legacy"
    assert result["outcome"] == "approval_ready"
    assert result["validation_level"] == "legacy"
    assert result["offer_id"] is None
    assert result["product_name"] == "Legacy Product"
    assert result["risk_flags"] == ("LEGACY_RECOMMENDATION",)


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
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        results = cast(list[dict[str, object]], finished["results"])
        case_id = results[0]["case_id"]
        case = await client.get(f"/api/v1/scans/{scan_id}/cases/{case_id}")

    assert finished["status"] == "succeeded"
    assert len(results) == 1
    assert results[0]["outcome"] == "error"
    assert case.json()["error"] == {
        "error_code": ErrorCode.MCP_TIMEOUT.value,
        "message": "The procurement scan exceeded its workflow deadline.",
        "retryable": True,
        "retry_count": 0,
    }
