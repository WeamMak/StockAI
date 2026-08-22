"""Durable explicit handoff from recommendation to Odoo draft creation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Protocol

from procurement.agent.state import ScanState
from procurement.api.services.scans import ScanStatus
from procurement.domain.audit import AuditEvent
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId, Environment
from procurement.domain.models import UtcTimestamp
from procurement.observability.metrics import AgentMetrics
from procurement.ports.mcp import PurchaseOrderDraft
from procurement.ports.repositories import (
    ApplicationRepository,
    CaseRecord,
    DraftRecord,
    FailureRecord,
    RevisionConflictError,
)

_RETENTION_DAYS = {Environment.DEV: 30, Environment.PROD: 365}


class DraftWorkflow(Protocol):
    """Only the exact-checkpoint draft operation needed by this service."""

    async def aensure_draft(self, workflow_thread_id: str) -> ScanState: ...


@dataclass(frozen=True, slots=True)
class AcceptedDraftSubmission:
    case_id: str
    status: ScanStatus
    created: bool


class DraftSubmissionService:
    """Reserve and complete one idempotent recommendation-to-draft handoff."""

    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        workflow: DraftWorkflow,
        environment: Environment,
        now: Callable[[], datetime] | None = None,
        metrics: AgentMetrics | None = None,
    ) -> None:
        self._repository = repository
        self._workflow = workflow
        self._environment = environment
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._metrics = metrics
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_cases: set[str] = set()

    async def drain(self) -> None:
        """Wait until all background work accepted by this service finishes."""

        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def submit(
        self,
        *,
        case_id: str,
        expected_revision: int,
        actor_subject: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> AcceptedDraftSubmission:
        started_at = perf_counter()
        try:
            accepted = await self._submit(
                case_id=case_id,
                expected_revision=expected_revision,
                actor_subject=actor_subject,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        except DomainError as error:
            result = (
                "conflict"
                if error.error_code is ErrorCode.REVISION_CONFLICT
                else "error"
            )
            self._observe_submission(result=result, started_at=started_at)
            raise
        except Exception:
            self._observe_submission(result="error", started_at=started_at)
            raise
        self._observe_submission(
            result="accepted" if accepted.created else "replay",
            started_at=started_at,
        )
        return accepted

    async def _submit(
        self,
        *,
        case_id: str,
        expected_revision: int,
        actor_subject: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> AcceptedDraftSubmission:
        identifier = self._case_id(case_id)
        case = await self._repository.get_case(identifier)
        if case is None:
            raise self._invalid()

        if case.draft_request_idempotency_key is not None:
            if case.draft_request_idempotency_key != idempotency_key:
                raise self._conflict()
            if case.status == ScanStatus.PENDING_APPROVAL.value and case.draft:
                if expected_revision != case.revision.value - 2:
                    raise self._conflict()
                return AcceptedDraftSubmission(
                    case_id=case_id,
                    status=ScanStatus.PENDING_APPROVAL,
                    created=False,
                )
            if (
                case.status == ScanStatus.CREATING_DRAFT.value
                and expected_revision == case.revision.value - 1
            ):
                self._schedule(case, actor_subject, correlation_id)
                return AcceptedDraftSubmission(
                    case_id=case_id,
                    status=ScanStatus.CREATING_DRAFT,
                    created=False,
                )
            raise self._conflict()

        if (
            case.status != ScanStatus.SUCCEEDED.value
            or case.revision.value != expected_revision
            or case.result is None
            or case.result.outcome != "approval_ready"
            or case.draft is not None
            or not case.workflow_thread_id
        ):
            raise self._conflict()

        requested_at = UtcTimestamp(self._now())
        reserved = replace(
            case,
            revision=case.revision.next(),
            status=ScanStatus.CREATING_DRAFT.value,
            draft_request_idempotency_key=idempotency_key,
            error=None,
            decision=None,
            updated_at=requested_at,
        )
        try:
            reserved = await self._repository.update_case(
                reserved,
                expected_revision=case.revision,
                expires_at=self._expires_at(case.created_at),
            )
        except RevisionConflictError:
            raise self._conflict() from None
        await self._append_audit(
            reserved,
            event_type="draft_requested",
            actor_subject=actor_subject,
            correlation_id=correlation_id,
        )
        self._schedule(reserved, actor_subject, correlation_id)
        return AcceptedDraftSubmission(
            case_id=case_id,
            status=ScanStatus.CREATING_DRAFT,
            created=True,
        )

    def _observe_submission(self, *, result: str, started_at: float) -> None:
        if self._metrics is not None:
            self._metrics.observe_draft_submission(
                result=result,
                duration_seconds=perf_counter() - started_at,
            )

    def _schedule(
        self, case: CaseRecord, actor_subject: str, correlation_id: str
    ) -> None:
        if case.case_id.value in self._active_cases:
            return
        self._active_cases.add(case.case_id.value)
        task = asyncio.create_task(
            self._create_draft(case, actor_subject, correlation_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _create_draft(
        self, reserved: CaseRecord, actor_subject: str, correlation_id: str
    ) -> None:
        try:
            assert reserved.workflow_thread_id is not None
            state = await self._workflow.aensure_draft(reserved.workflow_thread_id)
            draft = state.get("draft")
            if not isinstance(draft, PurchaseOrderDraft):
                raise ValueError("the workflow did not return a draft")
            current = await self._repository.get_case(reserved.case_id)
            if current is None:
                return
            if current.status == ScanStatus.PENDING_APPROVAL.value and current.draft:
                return
            if (
                current.status != ScanStatus.CREATING_DRAFT.value
                or current.draft_request_idempotency_key
                != reserved.draft_request_idempotency_key
            ):
                return
            created_at = UtcTimestamp(self._now())
            pending = replace(
                current,
                revision=current.revision.next(),
                status=ScanStatus.PENDING_APPROVAL.value,
                draft=DraftRecord(
                    po_id=draft.po_id,
                    write_date=draft.write_date,
                    state=draft.state,
                    partner_id=draft.partner_id,
                    currency_id=draft.currency_id,
                    amount_total=draft.amount_total,
                ),
                updated_at=created_at,
            )
            pending = await self._repository.update_case(
                pending,
                expected_revision=current.revision,
                expires_at=self._expires_at(current.created_at),
            )
            await self._append_audit(
                pending,
                event_type="draft_created",
                actor_subject=actor_subject,
                correlation_id=correlation_id,
            )
        except Exception:
            await self._record_failure(reserved, actor_subject, correlation_id)
        finally:
            self._active_cases.discard(reserved.case_id.value)

    async def _record_failure(
        self, reserved: CaseRecord, actor_subject: str, correlation_id: str
    ) -> None:
        current = await self._repository.get_case(reserved.case_id)
        if current is None or current.status != ScanStatus.CREATING_DRAFT.value:
            return
        failed_at = UtcTimestamp(self._now())
        failed = replace(
            current,
            revision=current.revision.next(),
            status=ScanStatus.FAILED.value,
            error=FailureRecord(
                error_code=ErrorCode.VALIDATION_FAILED.value,
                message="The saved recommendation could not create a draft.",
                retryable=False,
                retry_count=0,
            ),
            updated_at=failed_at,
        )
        try:
            failed = await self._repository.update_case(
                failed,
                expected_revision=current.revision,
                expires_at=self._expires_at(current.created_at),
            )
        except RevisionConflictError:
            return
        await self._append_audit(
            failed,
            event_type="draft_failed",
            actor_subject=actor_subject,
            correlation_id=correlation_id,
        )

    async def _append_audit(
        self,
        case: CaseRecord,
        *,
        event_type: str,
        actor_subject: str,
        correlation_id: str,
    ) -> None:
        await self._repository.append_audit(
            AuditEvent(
                event_id=f"{case.case_id.value}-{event_type}-r{case.revision.value}",
                case_id=case.case_id,
                event_type=event_type,
                actor_id=actor_subject,
                occurred_at=case.updated_at,
                correlation_id=correlation_id,
                source_revision=case.revision,
                outcome=case.status,
                evidence_digest=(
                    case.result.evidence_digest if case.result is not None else None
                ),
            ),
            expires_at=self._expires_at(case.created_at),
        )

    def _case_id(self, case_id: str) -> CaseId:
        try:
            return CaseId(self._environment, case_id)
        except DomainError:
            raise self._invalid() from None

    def _expires_at(self, created_at: UtcTimestamp) -> UtcTimestamp:
        return UtcTimestamp(
            created_at.value + timedelta(days=_RETENTION_DAYS[self._environment])
        )

    @staticmethod
    def _invalid() -> DomainError:
        return DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The requested recommendation cannot create a draft.",
        )

    @staticmethod
    def _conflict() -> DomainError:
        return DomainError(
            error_code=ErrorCode.REVISION_CONFLICT,
            safe_message="The recommendation changed before draft submission.",
        )
