"""Durable-first manager decision acceptance and workflow resumption."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from procurement.agent.state import UnresolvedResult
from procurement.api.services.scans import ScanStatus
from procurement.domain.audit import AuditEvent
from procurement.domain.decisions import (
    APPROVAL_VALIDITY,
    ApprovalRecord,
    DecisionRecord,
    DecisionText,
    DecisionType,
    RejectionRecord,
    decision_id_for,
)
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId, Environment
from procurement.domain.models import UtcTimestamp
from procurement.observability.metrics import AgentMetrics
from procurement.ports.decisions import DecisionConflictError
from procurement.ports.mcp import DecisionOutcome
from procurement.ports.repositories import (
    ApplicationRepository,
    CaseRecord,
    DecisionOutcomeRecord,
    FailureRecord,
    RevisionConflictError,
)

_RETENTION_DAYS = {Environment.DEV: 30, Environment.PROD: 365}


@dataclass(frozen=True, slots=True)
class ApprovalCommand:
    environment: str
    case_revision: int
    po_id: int
    po_revision: str
    vendor_id: str
    quantity: Decimal
    amount: Decimal
    currency: str
    budget_status: str
    overage: Decimal
    evidence_digest: str
    budget_exception: bool
    justification: str | None


@dataclass(frozen=True, slots=True)
class RejectionCommand:
    environment: str
    case_revision: int
    po_id: int
    po_revision: str
    evidence_digest: str
    reason: str


@dataclass(frozen=True, slots=True)
class AcceptedDecision:
    decision_id: str
    decision_type: DecisionType
    status: str
    created: bool


class DecisionWorkflow(Protocol):
    """Only the exact-checkpoint resume operation needed by decisions."""

    async def aresume_decision(
        self, workflow_thread_id: str, decision_id: str
    ) -> dict[str, object]: ...


class DecisionService:
    """Validate, persist, transition, and resume manager decisions."""

    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        workflow: DecisionWorkflow,
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

    async def drain(self) -> None:
        """Wait until all background work accepted by this service finishes."""

        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def approve(
        self,
        *,
        case_id: str,
        command: ApprovalCommand,
        manager_subject: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> AcceptedDecision:
        started_at = perf_counter()
        case = await self._decision_case(case_id, command.environment)
        result = case.result
        draft = case.draft
        assert result is not None and draft is not None and result.evidence is not None
        evidence = result.evidence
        offer = next(
            (item for item in evidence.offers if item.offer_id == result.offer_id), None
        )
        if offer is None or any(
            (
                command.po_id != draft.po_id,
                command.po_revision != draft.write_date,
                command.vendor_id != offer.vendor_id,
                command.quantity != result.quantity,
                command.amount != result.normalized_cost,
                command.currency != offer.currency,
                command.budget_status != result.budget_status,
                command.evidence_digest != result.evidence_digest,
            )
        ):
            raise self._invalid_binding()
        budget = evidence.budget
        overage = budget.overage if budget is not None else Decimal("0")
        if command.overage != overage:
            raise self._invalid_binding()
        exception_required = budget.exception_required if budget is not None else False
        if exception_required and (
            not command.budget_exception
            or command.justification is None
            or not command.justification.strip()
        ):
            raise DomainError(
                error_code=ErrorCode.BUDGET_JUSTIFICATION_REQUIRED,
                safe_message="An over-budget approval requires justification.",
            )
        if not exception_required and (
            command.budget_exception or command.justification is not None
        ):
            raise self._invalid_binding()
        decision_id = decision_id_for(
            environment=self._environment,
            case_id=case.case_id,
            decision_type=DecisionType.APPROVE,
            po_id=draft.po_id,
            po_write_date=draft.write_date,
        )
        existing = await self._repository.get_decision(decision_id)
        if existing is not None:
            if not self._approval_replay_matches(
                existing,
                command=command,
                manager_subject=manager_subject,
                idempotency_key=idempotency_key,
            ):
                raise self._revision_conflict()
            assert isinstance(existing, ApprovalRecord)
            if self._now() >= existing.expires_at.value:
                raise DomainError(
                    error_code=ErrorCode.APPROVAL_STALE,
                    safe_message="The approval authorization has expired.",
                )
            accepted = await self._accept(
                case=case,
                decision=existing,
                accepted_status=ScanStatus.APPROVED,
                correlation_id=correlation_id,
            )
            self._observe_decision(accepted, started_at=started_at)
            return accepted
        self._require_pending_revision(case, command.case_revision)
        if any(
            value is None
            for value in (
                result.offer_id,
                result.quantity,
                result.unit_price,
                result.normalized_cost,
                result.evidence_digest,
            )
        ):
            raise self._invalid_binding()
        decided_at = UtcTimestamp(self._now())
        decision = ApprovalRecord(
            decision_id=decision_id,
            case_id=case.case_id,
            manager_subject=manager_subject,
            manager_role="manager",
            case_revision=case.revision,
            po_id=draft.po_id,
            po_write_date=draft.write_date,
            po_state=draft.state,
            partner_id=draft.partner_id,
            currency_id=draft.currency_id,
            amount_total=draft.amount_total,
            offer_id=result.offer_id or "",
            vendor_id=offer.vendor_id,
            quantity=result.quantity or Decimal("0"),
            unit_price=result.unit_price or Decimal("0"),
            currency=offer.currency,
            normalized_cost=result.normalized_cost or Decimal("0"),
            budget_status=result.budget_status,
            budget_amount=budget.budget_amount if budget is not None else Decimal("0"),
            confirmed_commitment=(
                budget.confirmed_commitment if budget is not None else Decimal("0")
            ),
            remaining_before=(
                budget.remaining_before if budget is not None else Decimal("0")
            ),
            remaining_after=(
                budget.remaining_after if budget is not None else Decimal("0")
            ),
            overage=overage,
            exception_required=exception_required,
            budget_exception=command.budget_exception,
            justification=(
                DecisionText(command.justification)
                if command.justification is not None
                else None
            ),
            evidence_digest=result.evidence_digest or "",
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            expires_at=UtcTimestamp(decided_at.value + APPROVAL_VALIDITY),
        )
        accepted = await self._accept(
            case=case,
            decision=decision,
            accepted_status=ScanStatus.APPROVED,
            correlation_id=correlation_id,
        )
        self._observe_decision(accepted, started_at=started_at)
        return accepted

    async def reject(
        self,
        *,
        case_id: str,
        command: RejectionCommand,
        manager_subject: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> AcceptedDecision:
        started_at = perf_counter()
        case = await self._decision_case(case_id, command.environment)
        draft = case.draft
        result = case.result
        assert draft is not None and result is not None
        if any(
            (
                command.po_id != draft.po_id,
                command.po_revision != draft.write_date,
                command.evidence_digest != result.evidence_digest,
            )
        ):
            raise self._invalid_binding()
        decision_id = decision_id_for(
            environment=self._environment,
            case_id=case.case_id,
            decision_type=DecisionType.REJECT,
            po_id=draft.po_id,
            po_write_date=draft.write_date,
        )
        existing = await self._repository.get_decision(decision_id)
        if existing is not None:
            if not self._rejection_replay_matches(
                existing,
                command=command,
                manager_subject=manager_subject,
                idempotency_key=idempotency_key,
            ):
                raise self._revision_conflict()
            accepted = await self._accept(
                case=case,
                decision=existing,
                accepted_status=ScanStatus.REJECTED,
                correlation_id=correlation_id,
            )
            self._observe_decision(accepted, started_at=started_at)
            return accepted
        self._require_pending_revision(case, command.case_revision)
        decision = RejectionRecord(
            decision_id=decision_id,
            case_id=case.case_id,
            manager_subject=manager_subject,
            manager_role="manager",
            case_revision=case.revision,
            po_id=draft.po_id,
            po_write_date=draft.write_date,
            po_state=draft.state,
            partner_id=draft.partner_id,
            currency_id=draft.currency_id,
            amount_total=draft.amount_total,
            reason=DecisionText(command.reason),
            evidence_digest=result.evidence_digest or "",
            idempotency_key=idempotency_key,
            decided_at=UtcTimestamp(self._now()),
        )
        accepted = await self._accept(
            case=case,
            decision=decision,
            accepted_status=ScanStatus.REJECTED,
            correlation_id=correlation_id,
        )
        self._observe_decision(accepted, started_at=started_at)
        return accepted

    async def _decision_case(self, case_id: str, environment: str) -> CaseRecord:
        if environment != self._environment.value:
            raise self._invalid_binding()
        try:
            identifier = CaseId(self._environment, case_id)
        except DomainError:
            raise self._invalid_binding() from None
        case = await self._repository.get_case(identifier)
        if (
            case is None
            or case.result is None
            or case.result.outcome != "approval_ready"
            or case.result.evidence is None
            or case.draft is None
            or not case.workflow_thread_id
        ):
            raise self._invalid_binding()
        return case

    @staticmethod
    def _require_pending_revision(case: CaseRecord, revision: int) -> None:
        if (
            case.status != ScanStatus.PENDING_APPROVAL.value
            or revision != case.revision.value
        ):
            raise DecisionService._revision_conflict()

    async def _accept(
        self,
        *,
        case: CaseRecord,
        decision: ApprovalRecord | RejectionRecord,
        accepted_status: ScanStatus,
        correlation_id: str,
    ) -> AcceptedDecision:
        retention = UtcTimestamp(
            decision.decided_at.value
            + timedelta(days=_RETENTION_DAYS[self._environment])
        )
        try:
            created = await self._repository.create_decision(
                decision, retention_expires_at=retention
            )
        except DecisionConflictError:
            raise DomainError(
                error_code=ErrorCode.REVISION_CONFLICT,
                safe_message="Another manager decision already won.",
            ) from None
        current = await self._repository.get_case(case.case_id)
        if current is None:
            raise self._invalid_binding()
        if current.status == ScanStatus.PENDING_APPROVAL.value:
            transitioned = replace(
                current,
                revision=current.revision.next(),
                status=accepted_status.value,
                updated_at=decision.decided_at,
            )
            try:
                await self._repository.update_case(
                    transitioned,
                    expected_revision=current.revision,
                    expires_at=retention,
                )
            except RevisionConflictError:
                raise DomainError(
                    error_code=ErrorCode.REVISION_CONFLICT,
                    safe_message="The case revision changed concurrently.",
                ) from None
            await self._repository.append_audit(
                AuditEvent(
                    event_id=self._audit_event_id(current),
                    case_id=case.case_id,
                    event_type=(
                        "manager_approved"
                        if decision.decision_type is DecisionType.APPROVE
                        else "manager_rejected"
                    ),
                    actor_id=decision.manager_subject,
                    occurred_at=decision.decided_at,
                    correlation_id=correlation_id,
                    source_revision=current.revision,
                    outcome=accepted_status.value,
                    evidence_digest=decision.evidence_digest,
                    decision_id=decision.decision_id.value,
                ),
                expires_at=retention,
            )
        latest = await self._repository.get_case(case.case_id)
        terminal = {
            ScanStatus.CONFIRMED.value,
            ScanStatus.CANCELLED.value,
            ScanStatus.RECONCILIATION_REQUIRED.value,
        }
        if latest is None or latest.status not in terminal:
            task = asyncio.create_task(
                self._resume(
                    case.case_id,
                    case.workflow_thread_id or "",
                    decision.decision_id.value,
                    decision.decision_type,
                    retention,
                    correlation_id,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return AcceptedDecision(
            decision_id=decision.decision_id.value,
            decision_type=decision.decision_type,
            status=latest.status if latest is not None else accepted_status.value,
            created=created.created,
        )

    async def _resume(
        self,
        case_id: CaseId,
        workflow_thread_id: str,
        decision_id: str,
        decision_type: DecisionType,
        retention: UtcTimestamp,
        correlation_id: str,
    ) -> None:
        current = await self._repository.get_case(case_id)
        if (
            decision_type is DecisionType.APPROVE
            and current is not None
            and current.status == ScanStatus.APPROVED.value
        ):
            current = await self._transition(
                current,
                status=ScanStatus.CONFIRMING,
                decision_id=decision_id,
                correlation_id=correlation_id,
                retention=retention,
            )
            if current is None:
                return
        try:
            state = await self._workflow.aresume_decision(
                workflow_thread_id, decision_id
            )
        except Exception:
            state = {
                "result": UnresolvedResult(
                    error_code=ErrorCode.ODOO_UNAVAILABLE,
                    message="The purchase-order workflow could not be resumed safely.",
                    retryable=True,
                )
            }
        outcome = state.get("decision_outcome")
        unresolved = state.get("result")
        current = await self._repository.get_case(case_id)
        if current is None:
            return
        if isinstance(outcome, DecisionOutcome):
            status = ScanStatus(outcome.outcome)
            decision_record = DecisionOutcomeRecord(
                decision_id=outcome.decision_id,
                decision_type=decision_type.value,
                status=status.value,
                po_id=outcome.po_id,
                po_reference=outcome.po_reference,
                write_date=outcome.write_date,
                odoo_state=outcome.odoo_state,
                reconciled=outcome.reconciled,
            )
        else:
            status = (
                ScanStatus.RECONCILIATION_REQUIRED
                if isinstance(unresolved, UnresolvedResult)
                and unresolved.error_code is ErrorCode.RECONCILIATION_REQUIRED
                else ScanStatus.FAILED
            )
            decision_record = None
        failure = (
            FailureRecord(
                error_code=unresolved.error_code.value,
                message=unresolved.message,
                retryable=unresolved.retryable,
                retry_count=unresolved.retry_count,
            )
            if status is ScanStatus.FAILED and isinstance(unresolved, UnresolvedResult)
            else None
        )
        if current.status == status.value and current.decision == decision_record:
            return
        await self._transition(
            current,
            status=status,
            decision_id=decision_id,
            correlation_id=correlation_id,
            retention=retention,
            decision=decision_record,
            error=failure,
            completed=True,
        )

    async def _transition(
        self,
        current: CaseRecord,
        *,
        status: ScanStatus,
        decision_id: str,
        correlation_id: str,
        retention: UtcTimestamp,
        decision: DecisionOutcomeRecord | None = None,
        error: FailureRecord | None = None,
        completed: bool = False,
    ) -> CaseRecord | None:
        occurred_at = UtcTimestamp(self._now())
        transitioned = replace(
            current,
            revision=current.revision.next(),
            status=status.value,
            decision=decision,
            error=error,
            updated_at=occurred_at,
            completed_at=occurred_at if completed else current.completed_at,
        )
        try:
            await self._repository.update_case(
                transitioned,
                expected_revision=current.revision,
                expires_at=retention,
            )
        except RevisionConflictError:
            return None
        await self._repository.append_audit(
            AuditEvent(
                event_id=self._audit_event_id(current),
                case_id=current.case_id,
                event_type=status.value,
                actor_id="system:decision-workflow",
                occurred_at=occurred_at,
                correlation_id=correlation_id,
                source_revision=current.revision,
                outcome=status.value,
                evidence_digest=(
                    current.result.evidence_digest
                    if current.result is not None
                    else None
                ),
                decision_id=decision_id,
            ),
            expires_at=retention,
        )
        return transitioned

    @staticmethod
    def _approval_replay_matches(
        existing: DecisionRecord,
        *,
        command: ApprovalCommand,
        manager_subject: str,
        idempotency_key: str,
    ) -> bool:
        return isinstance(existing, ApprovalRecord) and all(
            (
                existing.manager_subject == manager_subject,
                existing.idempotency_key == idempotency_key,
                existing.case_revision.value == command.case_revision,
                existing.po_id == command.po_id,
                existing.po_write_date == command.po_revision,
                existing.vendor_id == command.vendor_id,
                existing.quantity == command.quantity,
                existing.normalized_cost == command.amount,
                existing.currency == command.currency,
                existing.budget_status == command.budget_status,
                existing.overage == command.overage,
                existing.evidence_digest == command.evidence_digest,
                existing.budget_exception == command.budget_exception,
                (
                    existing.justification.value
                    if existing.justification is not None
                    else None
                )
                == command.justification,
            )
        )

    @staticmethod
    def _rejection_replay_matches(
        existing: DecisionRecord,
        *,
        command: RejectionCommand,
        manager_subject: str,
        idempotency_key: str,
    ) -> bool:
        return isinstance(existing, RejectionRecord) and all(
            (
                existing.manager_subject == manager_subject,
                existing.idempotency_key == idempotency_key,
                existing.case_revision.value == command.case_revision,
                existing.po_id == command.po_id,
                existing.po_write_date == command.po_revision,
                existing.evidence_digest == command.evidence_digest,
                existing.reason.value == command.reason,
            )
        )

    @staticmethod
    def _revision_conflict() -> DomainError:
        return DomainError(
            error_code=ErrorCode.REVISION_CONFLICT,
            safe_message="The case revision is no longer current.",
        )

    @staticmethod
    def _audit_event_id(current: CaseRecord) -> str:
        return f"{current.revision.value:020d}:{uuid4().hex}"

    def _observe_decision(
        self, accepted: AcceptedDecision, *, started_at: float
    ) -> None:
        if self._metrics is not None:
            self._metrics.observe_manager_decision(
                decision=accepted.decision_type.value,
                result="accepted" if accepted.created else "replay",
                duration_seconds=perf_counter() - started_at,
            )

    @staticmethod
    def _invalid_binding() -> DomainError:
        return DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The manager decision does not match the current case.",
        )
