"""Explicit officer-or-manager draft submission behavior."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from httpx2 import ASGITransport, AsyncClient, Response
from tests.support.local_identity import LocalIdentityProvider, sign_in
from tests.support.recommendations import t27_approval_result

from procurement.api.app import create_app
from procurement.api.auth.session import UserRole
from procurement.api.services.scans import ScanService
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.ports.mcp import PurchaseOrderDraft
from procurement.ports.repositories import CaseRecord, InMemoryApplicationRepository

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
CASE_ID = CaseId(Environment.DEV, "scan-001:product-101")


class DraftWorkflow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def aensure_draft(self, workflow_thread_id: str) -> dict[str, object]:
        self.calls.append(workflow_thread_id)
        return {
            "draft": PurchaseOrderDraft(
                po_id=41,
                write_date="2026-08-21 12:00:00",
                state="draft",
                partner_id=7,
                currency_id=1,
                amount_total=t27_approval_result().normalized_cost,
            )
        }


async def _ready(repository: InMemoryApplicationRepository) -> CaseRecord:
    result = t27_approval_result()
    record = CaseRecord(
        case_id=CASE_ID,
        revision=Revision(3),
        status="succeeded",
        trigger="manual",
        created_at=UtcTimestamp(NOW),
        updated_at=UtcTimestamp(NOW),
        evidence=(result.evidence,) if result.evidence is not None else (),
        result=ScanService._recommendation_record(result),
        workflow_thread_id=CASE_ID.value,
    )
    await repository.create_case(
        record,
        idempotency_key="case-001",
        expires_at=UtcTimestamp(NOW + timedelta(days=30)),
    )
    return record


@pytest.mark.parametrize("role", [UserRole.OFFICER, UserRole.MANAGER])
@pytest.mark.anyio
async def test_operator_can_submit_the_exact_recommendation_for_draft(
    role: UserRole,
) -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    await _ready(repository)
    workflow = DraftWorkflow()
    application = create_app(
        scan_workflow=workflow,  # type: ignore[arg-type]
        application_repository=repository,
        identity_provider=LocalIdentityProvider(role=role),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)
        response = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 3},
        )
        await asyncio.sleep(0)

    assert response.status_code == 202
    assert response.json() == {
        "case_id": CASE_ID.value,
        "status": "creating_draft",
        "created": True,
    }
    latest = await repository.get_case(CASE_ID)
    assert latest is not None
    assert latest.status == "pending_approval"
    assert latest.draft is not None
    assert latest.draft.po_id == 41
    assert workflow.calls == [CASE_ID.value]


@pytest.mark.anyio
async def test_compatible_replay_returns_the_existing_pending_draft() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    await _ready(repository)
    workflow = DraftWorkflow()
    application = create_app(
        scan_workflow=workflow,  # type: ignore[arg-type]
        application_repository=repository,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)
        first = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 3},
        )
        await asyncio.sleep(0)
        replay = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 3},
        )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["status"] == "pending_approval"
    assert replay.json()["created"] is False
    assert workflow.calls == [CASE_ID.value]


@pytest.mark.anyio
async def test_changed_key_or_stale_revision_conflicts() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    await _ready(repository)
    application = create_app(
        scan_workflow=DraftWorkflow(),  # type: ignore[arg-type]
        application_repository=repository,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)
        stale = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-stale"},
            json={"case_revision": 2},
        )
        accepted = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 3},
        )
        await asyncio.sleep(0)
        changed = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-002"},
            json={"case_revision": 3},
        )

    assert stale.status_code == 409
    assert accepted.status_code == 202
    assert changed.status_code == 409
    assert changed.json()["error_code"] == "REVISION_CONFLICT"


@pytest.mark.anyio
async def test_draft_route_requires_csrf_key_and_exact_body() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    await _ready(repository)
    application = create_app(
        scan_workflow=DraftWorkflow(),  # type: ignore[arg-type]
        application_repository=repository,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)
        no_csrf = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={"Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 3},
        )
        no_key = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers=headers,
            json={"case_revision": 3},
        )
        extra = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 3, "amount": "1.00"},
        )

    assert no_csrf.status_code == 403
    assert no_key.status_code == 422
    assert extra.status_code == 422


@pytest.mark.anyio
async def test_scan_binding_and_missing_checkpoint_are_rejected() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    record = await _ready(repository)
    without_checkpoint = replace(
        record,
        revision=record.revision.next(),
        workflow_thread_id=None,
    )
    await repository.update_case(
        without_checkpoint,
        expected_revision=record.revision,
        expires_at=UtcTimestamp(NOW + timedelta(days=30)),
    )
    application = create_app(
        scan_workflow=DraftWorkflow(),  # type: ignore[arg-type]
        application_repository=repository,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)
        wrong_scan = await client.post(
            f"/api/v1/scans/scan-999/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 4},
        )
        missing_checkpoint = await client.post(
            f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
            headers={**headers, "Idempotency-Key": "draft-submit-001"},
            json={"case_revision": 4},
        )

    assert wrong_scan.status_code == 422
    assert missing_checkpoint.status_code == 409


@pytest.mark.anyio
async def test_concurrent_changed_keys_allow_only_one_reservation() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    await _ready(repository)
    application = create_app(
        scan_workflow=DraftWorkflow(),  # type: ignore[arg-type]
        application_repository=repository,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)

        async def submit(key: str) -> Response:
            return await client.post(
                f"/api/v1/scans/scan-001/cases/{CASE_ID.value}/draft",
                headers={**headers, "Idempotency-Key": key},
                json={"case_revision": 3},
            )

        responses = await asyncio.gather(submit("draft-a"), submit("draft-b"))

    assert sorted(response.status_code for response in responses) == [202, 409]
