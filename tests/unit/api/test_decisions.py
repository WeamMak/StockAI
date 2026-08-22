"""Durable-first manager decision service behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in
from tests.support.recommendations import t27_approval_result

from procurement.api.app import create_app
from procurement.api.auth.session import UserRole
from procurement.api.routes.scans import case_response
from procurement.api.services.decisions import ApprovalCommand, DecisionService
from procurement.api.services.scans import ScanService
from procurement.domain.decisions import DecisionId
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.ports.mcp import DecisionOutcome
from procurement.ports.repositories import (
    CaseRecord,
    DraftRecord,
    InMemoryApplicationRepository,
)

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
CASE_ID = CaseId(Environment.DEV, "scan-001:product-101")


class Workflow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def aresume_decision(
        self, workflow_thread_id: str, decision_id: str
    ) -> dict[str, object]:
        self.calls.append((workflow_thread_id, decision_id))
        return {
            "decision_outcome": DecisionOutcome.confirmed(
                decision_id=decision_id,
                po_id=41,
                po_reference="P00041",
                write_date="2026-08-21 12:01:00",
                reconciled=False,
            )
        }


async def _pending(repository: InMemoryApplicationRepository) -> CaseRecord:
    result = t27_approval_result()
    record = CaseRecord(
        case_id=CASE_ID,
        revision=Revision(3),
        status="pending_approval",
        trigger="manual",
        created_at=UtcTimestamp(NOW),
        updated_at=UtcTimestamp(NOW),
        evidence=(result.evidence,) if result.evidence is not None else (),
        result=ScanService._recommendation_record(result),
        draft=DraftRecord(
            po_id=41,
            write_date="2026-08-21 12:00:00",
            state="draft",
            partner_id=7,
            currency_id=1,
            amount_total=result.normalized_cost,
        ),
        workflow_thread_id="scan-001:product-101:refine-2",
    )
    await repository.create_case(
        record,
        idempotency_key="case-001",
        expires_at=UtcTimestamp(NOW + timedelta(days=30)),
    )
    return record


def _command(record: CaseRecord, **overrides: object) -> ApprovalCommand:
    assert record.result is not None and record.result.evidence is not None
    evidence = record.result.evidence
    offer = next(
        item for item in evidence.offers if item.offer_id == record.result.offer_id
    )
    values: dict[str, object] = {
        "environment": "dev",
        "case_revision": 3,
        "po_id": 41,
        "po_revision": "2026-08-21 12:00:00",
        "vendor_id": offer.vendor_id,
        "quantity": record.result.quantity,
        "amount": record.result.normalized_cost,
        "currency": offer.currency,
        "budget_status": record.result.budget_status,
        "overage": evidence.budget.overage if evidence.budget else Decimal("0"),
        "evidence_digest": record.result.evidence_digest,
        "budget_exception": False,
        "justification": None,
    }
    values.update(overrides)
    return ApprovalCommand(**values)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_approval_persists_before_resuming_exact_refinement_thread() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    record = await _pending(repository)
    workflow = Workflow()
    service = DecisionService(
        repository=repository,
        workflow=workflow,
        environment=Environment.DEV,
        now=lambda: NOW,
    )

    accepted = await service.approve(
        case_id=CASE_ID.value,
        command=_command(record),
        manager_subject="manager-001",
        idempotency_key="approve-001",
        correlation_id="request-001",
    )
    decision = await repository.get_decision(
        DecisionId(Environment.DEV, accepted.decision_id)
    )
    await asyncio.sleep(0)

    assert decision is not None
    assert workflow.calls == [("scan-001:product-101:refine-2", accepted.decision_id)]
    latest = await repository.get_case(CASE_ID)
    assert latest is not None
    assert latest.status == "confirmed"
    assert latest.result == record.result
    assert latest.decision is not None
    assert latest.decision.po_reference == "P00041"
    projection = case_response(
        await ScanService(
            workflow=workflow,  # type: ignore[arg-type]
            environment=Environment.DEV,
            repository=repository,
        ).get_case(CASE_ID.value)
    ).model_dump()
    assert projection["result"]["outcome"] == "approval_ready"
    assert projection["decision"]["status"] == "confirmed"
    assert [
        event.event_type for event in await repository.list_audit(CASE_ID, limit=20)
    ] == ["manager_approved", "confirming", "confirmed"]


@pytest.mark.anyio
async def test_compatible_replay_returns_terminal_state_without_second_resume() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    record = await _pending(repository)
    workflow = Workflow()
    service = DecisionService(
        repository=repository,
        workflow=workflow,
        environment=Environment.DEV,
        now=lambda: NOW,
    )
    first = await service.approve(
        case_id=CASE_ID.value,
        command=_command(record),
        manager_subject="manager-001",
        idempotency_key="approve-001",
        correlation_id="request-001",
    )
    await asyncio.sleep(0)

    replay = await service.approve(
        case_id=CASE_ID.value,
        command=_command(record),
        manager_subject="manager-001",
        idempotency_key="approve-001",
        correlation_id="request-002",
    )
    await asyncio.sleep(0)

    assert replay.decision_id == first.decision_id
    assert replay.created is False
    assert replay.status == "confirmed"
    assert len(workflow.calls) == 1


@pytest.mark.anyio
async def test_altered_binding_is_rejected_without_resume() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    record = await _pending(repository)
    workflow = Workflow()
    service = DecisionService(
        repository=repository,
        workflow=workflow,
        environment=Environment.DEV,
        now=lambda: NOW,
    )

    with pytest.raises(DomainError) as raised:
        await service.approve(
            case_id=CASE_ID.value,
            command=_command(record, vendor_id="vendor-999"),
            manager_subject="manager-001",
            idempotency_key="approve-altered",
            correlation_id="request-002",
        )

    assert raised.value.error_code is ErrorCode.VALIDATION_FAILED
    assert workflow.calls == []


@pytest.mark.anyio
async def test_officer_cannot_approve_pending_case() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    record = await _pending(repository)
    application = create_app(
        application_repository=repository,
        scan_workflow=Workflow(),  # type: ignore[arg-type]
        identity_provider=LocalIdentityProvider(role=UserRole.OFFICER),
    )
    command = _command(record)
    payload = {
        "environment": command.environment,
        "case_revision": command.case_revision,
        "po_id": command.po_id,
        "po_revision": command.po_revision,
        "vendor_id": command.vendor_id,
        "quantity": format(command.quantity, "f"),
        "amount": format(command.amount, "f"),
        "currency": command.currency,
        "budget_status": command.budget_status,
        "overage": format(command.overage, "f"),
        "evidence_digest": command.evidence_digest,
        "budget_exception": command.budget_exception,
        "justification": command.justification,
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        headers = await sign_in(client)
        response = await client.post(
            f"/api/v1/cases/{CASE_ID.value}/approve",
            headers={**headers, "Idempotency-Key": "approve-001"},
            json=payload,
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"
