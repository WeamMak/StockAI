"""Asynchronous scan orchestration backed by an application repository."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from procurement.agent.state import (
    ApprovalReadyResult,
    LegacyApprovalReadyResult,
    ManualReviewResult,
    NoValidOfferResult,
    ScanResult,
    ScanState,
    UnresolvedResult,
)
from procurement.domain.audit import AuditEvent
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId, Environment, Revision, ScanId
from procurement.domain.models import UtcTimestamp
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.observability.logging import log_event
from procurement.observability.metrics import AgentMetrics, create_agent_metrics
from procurement.ports.mcp import ReplenishmentCandidate
from procurement.ports.repositories import (
    ApplicationRepository,
    CandidateSnapshot,
    CaseRecord,
    CaseSummary,
    FailureRecord,
    InMemoryApplicationRepository,
    RecommendationRecord,
    RevisionConflictError,
    ScanRecord,
)

DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 120.0
MAX_SCAN_HISTORY = 100
MAX_REFINEMENTS = 3
_RETENTION_DAYS = {Environment.DEV: 30, Environment.PROD: 365}


class ScanStatus(StrEnum):
    """Stable API-visible status of one asynchronous scan or case."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ScanTrigger(StrEnum):
    """Authorized source that requested a scan."""

    MANUAL = "manual"
    CRON = "cron"


@dataclass(frozen=True, slots=True)
class ScanFailure:
    """Safe failure exposed by scan or case polling."""

    error_code: ErrorCode
    message: str
    retryable: bool
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class ScanSnapshot:
    """Immutable API view of one durable case record."""

    scan_id: str
    case_id: str
    status: ScanStatus
    trigger: ScanTrigger
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    evidence: tuple[ProcurementEvidence, ...]
    result: (
        ApprovalReadyResult
        | LegacyApprovalReadyResult
        | ManualReviewResult
        | NoValidOfferResult
        | None
    )
    error: ScanFailure | None
    refinement_count: int


@dataclass(frozen=True, slots=True)
class ScanAggregateSnapshot:
    """Immutable API view of one durable scan record."""

    scan_id: str
    status: ScanStatus
    trigger: ScanTrigger
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    results: tuple[CaseSummary, ...]
    error: ScanFailure | None


class ScanWorkflow(Protocol):
    """Compiled-graph and discovery operations needed by the scan service."""

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        """Run one non-human workflow to a terminal result."""

    async def discover_candidates(
        self, *, environment: Environment, scan_id: str
    ) -> tuple[ReplenishmentCandidate, ...] | UnresolvedResult:
        """Discover this scan's candidates once, outside any one case."""


class UnconfiguredScanWorkflow:
    """Safe default until the API composition root wires real adapters."""

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        del config
        return {
            **state,
            "result": UnresolvedResult(
                error_code=ErrorCode.LLM_UNAVAILABLE,
                message="The scan workflow is not configured.",
                retryable=True,
            ),
        }

    async def discover_candidates(
        self, *, environment: Environment, scan_id: str
    ) -> tuple[ReplenishmentCandidate, ...] | UnresolvedResult:
        del environment, scan_id
        return UnresolvedResult(
            error_code=ErrorCode.LLM_UNAVAILABLE,
            message="The scan workflow is not configured.",
            retryable=True,
        )


class ScanService:
    """Start short API requests while one local scan continues in a task."""

    def __init__(
        self,
        *,
        workflow: ScanWorkflow,
        environment: Environment,
        repository: ApplicationRepository | None = None,
        workflow_timeout_seconds: float = DEFAULT_WORKFLOW_TIMEOUT_SECONDS,
        metrics: AgentMetrics | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not isinstance(environment, Environment):
            raise ValueError("environment must be dev or prod")
        if workflow_timeout_seconds <= 0:
            raise ValueError("workflow_timeout_seconds must be positive")
        self._workflow = workflow
        self._environment = environment
        self._repository = repository or InMemoryApplicationRepository(
            environment=environment
        )
        self._workflow_timeout_seconds = workflow_timeout_seconds
        self._metrics = metrics or create_agent_metrics()
        self._logger = logger or logging.getLogger(__name__)
        self._guard = asyncio.Lock()
        self._active_scan_id: str | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start_scan(self, *, trigger: ScanTrigger) -> ScanAggregateSnapshot:
        """Reserve the local scan slot and schedule bounded background work."""

        if not isinstance(trigger, ScanTrigger):
            raise ValueError("trigger must be manual or cron")
        async with self._guard:
            if self._active_scan_id is not None:
                raise DomainError(
                    error_code=ErrorCode.SCAN_ALREADY_RUNNING,
                    safe_message="A procurement scan is already running.",
                )
            created_at = datetime.now(tz=UTC)
            scan_id = f"scan-{created_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}"
            timestamp = UtcTimestamp(created_at)
            record = ScanRecord(
                scan_id=ScanId(self._environment, scan_id),
                revision=Revision(1),
                status=ScanStatus.QUEUED.value,
                trigger=trigger.value,
                created_at=timestamp,
                updated_at=timestamp,
            )
            created = await self._repository.create_scan(
                record,
                idempotency_key=scan_id,
                expires_at=self._expires_at(timestamp),
            )
            if not created.created:
                return self._scan_snapshot(created.record)
            self._active_scan_id = scan_id
            task = asyncio.create_task(self._run_scan(record))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return self._scan_snapshot(record)

    async def list_scans(self) -> tuple[ScanAggregateSnapshot, ...]:
        """Return bounded newest-first durable scan history."""

        page = await self._repository.list_scans(limit=MAX_SCAN_HISTORY)
        return tuple(self._scan_snapshot(record) for record in page.records)

    async def list_recent_cases(self, *, limit: int) -> tuple[CaseSummary, ...]:
        """Return a bounded newest-first list of cases spanning every scan."""

        page = await self._repository.list_cases(limit=limit)
        summaries = (self._summarize_record(record) for record in page.records)
        return tuple(summary for summary in summaries if summary is not None)

    @staticmethod
    def _summarize_record(record: CaseRecord) -> CaseSummary | None:
        if record.status == ScanStatus.SKIPPED.value:
            return None
        product_id = record.result.product_id if record.result is not None else None
        product_name = record.result.product_name if record.result is not None else None
        if product_id is None or product_name is None:
            if not record.evidence:
                return None
            product_id = record.evidence[0].product_id
            product_name = record.evidence[0].product_name
        if record.result is not None:
            outcome = record.result.outcome
            amount = record.result.normalized_cost
            budget_status = record.result.budget_status
        else:
            outcome = "error"
            amount = None
            budget_status = "not_evaluated"
        need_by_date = (
            record.evidence[0].shortage.need_by_date if record.evidence else None
        )
        return CaseSummary(
            case_id=record.case_id.value,
            product_id=product_id,
            product_name=product_name,
            outcome=outcome,
            amount=amount,
            need_by_date=need_by_date,
            scan_id=record.case_id.value.split(":", 1)[0],
            budget_status=budget_status,
            completed_at=record.completed_at,
        )

    async def get_scan(self, scan_id: str) -> ScanAggregateSnapshot:
        """Return one scan or a stable safe not-found error."""

        try:
            id_ = ScanId(self._environment, scan_id)
        except DomainError:
            id_ = None
        record = await self._repository.get_scan(id_) if id_ is not None else None
        if record is None:
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="The requested scan was not found.",
            )
        return self._scan_snapshot(record)

    async def get_case(self, case_id: str) -> ScanSnapshot:
        """Return one case or a stable safe not-found error."""

        try:
            id_ = CaseId(self._environment, case_id)
        except DomainError:
            id_ = None
        record = await self._repository.get_case(id_) if id_ is not None else None
        if record is None:
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="The requested case was not found.",
            )
        return self._snapshot(record)

    async def refine_case(self, *, case_id: str, note: str) -> ScanSnapshot:
        """Reserve one bounded refinement attempt and schedule its background work."""

        try:
            id_ = CaseId(self._environment, case_id)
        except DomainError:
            id_ = None
        record = await self._repository.get_case(id_) if id_ is not None else None
        if record is None:
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="The requested case was not found.",
            )
        if (
            record.status != ScanStatus.SUCCEEDED.value
            or record.result is None
            or record.result.outcome != "approval_ready"
            or record.candidate_snapshot is None
        ):
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="Only an approval-ready case can be refined.",
            )
        if record.refinement_count >= MAX_REFINEMENTS:
            raise DomainError(
                error_code=ErrorCode.REFINEMENT_LIMIT_REACHED,
                safe_message="This case has reached its refinement limit.",
            )
        running_at = UtcTimestamp(datetime.now(tz=UTC))
        running = replace(
            record,
            revision=record.revision.next(),
            status=ScanStatus.RUNNING.value,
            started_at=running_at,
            updated_at=running_at,
        )
        try:
            running = await self._repository.update_case(
                running,
                expected_revision=record.revision,
                expires_at=self._expires_at(record.created_at),
            )
        except RevisionConflictError as error:
            raise DomainError(
                error_code=ErrorCode.REVISION_CONFLICT,
                safe_message="This case was already updated by another request.",
            ) from error
        await self._append_audit(running)
        task = asyncio.create_task(self._run_refinement(running, note=note))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self._snapshot(running)

    async def _run_refinement(self, running: CaseRecord, *, note: str) -> None:
        assert running.candidate_snapshot is not None
        assert running.result is not None
        assert running.result.product_id is not None
        assert running.result.product_name is not None
        scan_id, _, product_id = running.case_id.value.partition(":")
        snapshot = running.candidate_snapshot
        candidate = ReplenishmentCandidate(
            product_id=product_id,
            product_name=running.result.product_name,
            category_id=snapshot.category_id,
            reorder_minimum=snapshot.reorder_minimum,
            reorder_maximum=snapshot.reorder_maximum,
            projected_quantity=snapshot.projected_quantity,
            projected_trigger_date=snapshot.projected_trigger_date,
            skip_reason_code=None,
        )
        next_attempt = running.refinement_count + 1
        thread_id = f"{running.case_id.value}:refine-{next_attempt}"
        try:
            async with asyncio.timeout(self._workflow_timeout_seconds):
                state = await self._workflow.ainvoke(
                    {
                        "scan_id": scan_id,
                        "environment": self._environment,
                        "candidates": (candidate,),
                        "officer_note": note,
                    },
                    config={"configurable": {"thread_id": thread_id}},
                )
            terminal = self._apply_result(running, state)
        except TimeoutError:
            terminal = self._fail(
                running,
                error_code=ErrorCode.MCP_TIMEOUT,
                message="The procurement scan exceeded its workflow deadline.",
                retryable=True,
            )
        except Exception:
            terminal = self._fail(
                running,
                error_code=ErrorCode.LLM_UNAVAILABLE,
                message="The procurement scan could not be completed.",
                retryable=True,
            )
        completed_at = UtcTimestamp(datetime.now(tz=UTC))
        terminal = replace(
            terminal,
            revision=running.revision.next(),
            refinement_count=next_attempt,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        terminal = await self._repository.update_case(
            terminal,
            expected_revision=running.revision,
            expires_at=self._expires_at(running.created_at),
        )
        await self._append_audit(terminal)

    async def _run_scan(self, record: ScanRecord) -> None:
        started_at = perf_counter()
        running_at = UtcTimestamp(datetime.now(tz=UTC))
        running = replace(
            record,
            revision=record.revision.next(),
            status=ScanStatus.RUNNING.value,
            started_at=running_at,
            updated_at=running_at,
        )
        try:
            running = await self._repository.update_scan(
                running,
                expected_revision=record.revision,
                expires_at=self._expires_at(record.created_at),
            )
            discovery = await self._workflow.discover_candidates(
                environment=self._environment, scan_id=record.scan_id.value
            )
            if isinstance(discovery, UnresolvedResult):
                terminal = replace(
                    running,
                    status=ScanStatus.FAILED.value,
                    error=FailureRecord(
                        error_code=discovery.error_code.value,
                        message=discovery.message,
                        retryable=discovery.retryable,
                        retry_count=discovery.retry_count,
                    ),
                )
            else:
                summaries: list[CaseSummary] = []
                for candidate in discovery:
                    summary = await self._run_case(
                        scan_id=record.scan_id.value,
                        trigger=record.trigger,
                        candidate=candidate,
                    )
                    if summary is not None:
                        summaries.append(summary)
                terminal = replace(
                    running,
                    status=ScanStatus.SUCCEEDED.value,
                    case_summaries=tuple(summaries),
                )
            completed_at = UtcTimestamp(datetime.now(tz=UTC))
            terminal = replace(
                terminal,
                revision=running.revision.next(),
                completed_at=completed_at,
                updated_at=completed_at,
            )
            terminal = await self._repository.update_scan(
                terminal,
                expected_revision=running.revision,
                expires_at=self._expires_at(record.created_at),
            )
            duration_seconds = perf_counter() - started_at
            error_code = (
                ErrorCode(terminal.error.error_code)
                if terminal.error is not None
                else None
            )
            self._metrics.observe_scan(
                trigger=terminal.trigger,
                status=(
                    "success"
                    if terminal.status == ScanStatus.SUCCEEDED.value
                    else "error"
                ),
                duration_seconds=duration_seconds,
            )
            fields: dict[str, object] = {
                "scan_id": terminal.scan_id.value,
                "status": terminal.status,
                "case_count": len(terminal.case_summaries),
                "duration_ms": round(duration_seconds * 1000, 3),
                "retry_count": (
                    terminal.error.retry_count if terminal.error is not None else 0
                ),
            }
            if error_code is not None:
                fields["error_code"] = error_code.value
            log_event(
                self._logger,
                "scan_completed",
                level=(
                    logging.INFO
                    if terminal.status == ScanStatus.SUCCEEDED.value
                    else logging.ERROR
                ),
                **fields,
            )
        except Exception:
            duration_seconds = perf_counter() - started_at
            self._metrics.observe_scan(
                trigger=record.trigger,
                status="error",
                duration_seconds=duration_seconds,
            )
            log_event(
                self._logger,
                "scan_persistence_failed",
                level=logging.ERROR,
                scan_id=record.scan_id.value,
                status=record.status,
                duration_ms=round(duration_seconds * 1000, 3),
                retry_count=0,
                error_code=ErrorCode.RECONCILIATION_REQUIRED.value,
            )
        finally:
            async with self._guard:
                if self._active_scan_id == record.scan_id.value:
                    self._active_scan_id = None

    async def _run_case(
        self,
        *,
        scan_id: str,
        trigger: str,
        candidate: ReplenishmentCandidate,
    ) -> CaseSummary | None:
        case_id_value = f"{scan_id}:{candidate.product_id}"
        created_at = UtcTimestamp(datetime.now(tz=UTC))
        record = CaseRecord(
            case_id=CaseId(self._environment, case_id_value),
            revision=Revision(1),
            status=ScanStatus.QUEUED.value,
            trigger=trigger,
            created_at=created_at,
            updated_at=created_at,
            candidate_snapshot=CandidateSnapshot(
                category_id=candidate.category_id,
                reorder_minimum=candidate.reorder_minimum,
                reorder_maximum=candidate.reorder_maximum,
                projected_quantity=candidate.projected_quantity,
                projected_trigger_date=candidate.projected_trigger_date,
            ),
        )
        created = await self._repository.create_case(
            record,
            idempotency_key=case_id_value,
            expires_at=self._expires_at(created_at),
        )
        if not created.created:
            if created.record.status == ScanStatus.SKIPPED.value:
                return None
            return self._summarize(candidate, created.record)
        record = created.record
        await self._append_audit(record)
        running_at = UtcTimestamp(datetime.now(tz=UTC))
        running = replace(
            record,
            revision=record.revision.next(),
            status=ScanStatus.RUNNING.value,
            started_at=running_at,
            updated_at=running_at,
        )
        running = await self._repository.update_case(
            running,
            expected_revision=record.revision,
            expires_at=self._expires_at(created_at),
        )
        await self._append_audit(running)
        try:
            async with asyncio.timeout(self._workflow_timeout_seconds):
                state = await self._workflow.ainvoke(
                    {
                        "scan_id": scan_id,
                        "environment": self._environment,
                        "candidates": (candidate,),
                    },
                    config={"configurable": {"thread_id": case_id_value}},
                )
            if state.get("skip_reason") is not None:
                terminal = replace(running, status=ScanStatus.SKIPPED.value)
            else:
                terminal = self._apply_result(running, state)
        except TimeoutError:
            terminal = self._fail(
                running,
                error_code=ErrorCode.MCP_TIMEOUT,
                message="The procurement scan exceeded its workflow deadline.",
                retryable=True,
            )
        except Exception:
            terminal = self._fail(
                running,
                error_code=ErrorCode.LLM_UNAVAILABLE,
                message="The procurement scan could not be completed.",
                retryable=True,
            )
        completed_at = UtcTimestamp(datetime.now(tz=UTC))
        terminal = replace(
            terminal,
            revision=running.revision.next(),
            completed_at=completed_at,
            updated_at=completed_at,
        )
        terminal = await self._repository.update_case(
            terminal,
            expected_revision=running.revision,
            expires_at=self._expires_at(created_at),
        )
        await self._append_audit(terminal)
        if terminal.status == ScanStatus.SKIPPED.value:
            return None
        self._metrics.observe_case_result(
            outcome=(
                terminal.result.outcome if terminal.result is not None else "unresolved"
            ),
            error_code=(
                ErrorCode(terminal.error.error_code)
                if terminal.error is not None
                else None
            ),
        )
        return self._summarize(candidate, terminal)

    def _apply_result(
        self,
        record: CaseRecord,
        state: ScanState,
    ) -> CaseRecord:
        result: ScanResult | None = state.get("result")
        evidence = state.get("evidence", ())
        if isinstance(result, ApprovalReadyResult):
            return replace(
                record,
                status=ScanStatus.SUCCEEDED.value,
                evidence=evidence,
                result=RecommendationRecord(
                    product_id=result.product_id,
                    product_name=result.product_name,
                    offer_id=result.offer_id,
                    rationale=result.rationale,
                    trade_offs=result.trade_offs,
                    risk_flags=result.risk_flags,
                    uncertainty=result.uncertainty,
                    evidence_limitations=result.evidence_limitations,
                    evidence_digest=result.evidence_digest,
                    quantity=result.quantity,
                    unit_price=result.unit_price,
                    normalized_cost=result.normalized_cost,
                    budget_status=result.budget_status,
                    preference_profile_id=result.preference_profile_id,
                    preference_scope=result.preference_scope,
                    preference_revision=result.preference_revision,
                    priority_order=result.priority_order,
                    premium_outcome=result.premium_outcome,
                    evidence=result.evidence,
                ),
            )
        if isinstance(result, ManualReviewResult):
            return replace(
                record,
                status=ScanStatus.SUCCEEDED.value,
                evidence=evidence,
                result=RecommendationRecord(
                    outcome="manual_review",
                    product_id=None,
                    product_name=None,
                    rationale=result.rationale,
                    trade_offs=result.trade_offs,
                    risk_flags=result.risk_flags,
                    uncertainty=result.uncertainty,
                    evidence_limitations=result.evidence_limitations,
                ),
            )
        if isinstance(result, NoValidOfferResult):
            return replace(
                record,
                status=ScanStatus.SUCCEEDED.value,
                evidence=evidence,
                result=RecommendationRecord(
                    outcome="no_valid_offer",
                    product_id=result.product_id,
                    product_name=result.product_name,
                    rationale=result.rationale,
                    risk_flags=(),
                    evidence_limitations=result.evidence_limitations,
                ),
            )
        if isinstance(result, UnresolvedResult):
            return self._fail(
                replace(record, evidence=evidence),
                error_code=result.error_code,
                message=result.message,
                retryable=result.retryable,
                retry_count=result.retry_count,
            )
        return self._fail(
            record,
            error_code=ErrorCode.LLM_OUTPUT_INVALID,
            message="The procurement scan returned an invalid result.",
            retryable=False,
        )

    @staticmethod
    def _fail(
        record: CaseRecord,
        *,
        error_code: ErrorCode,
        message: str,
        retryable: bool,
        retry_count: int = 0,
    ) -> CaseRecord:
        return replace(
            record,
            status=ScanStatus.FAILED.value,
            error=FailureRecord(
                error_code=error_code.value,
                message=message,
                retryable=retryable,
                retry_count=retry_count,
            ),
        )

    @staticmethod
    def _summarize(
        candidate: ReplenishmentCandidate, terminal: CaseRecord
    ) -> CaseSummary:
        need_by_date = next(
            (
                item.shortage.need_by_date
                for item in terminal.evidence
                if item.product_id == candidate.product_id
            ),
            None,
        )
        if terminal.result is not None:
            outcome = terminal.result.outcome
            amount = terminal.result.normalized_cost
            budget_status = terminal.result.budget_status
        else:
            outcome = "error"
            amount = None
            budget_status = "not_evaluated"
        return CaseSummary(
            case_id=terminal.case_id.value,
            product_id=candidate.product_id,
            product_name=candidate.product_name,
            outcome=outcome,
            amount=amount,
            need_by_date=need_by_date,
            scan_id=terminal.case_id.value.split(":", 1)[0],
            budget_status=budget_status,
            completed_at=terminal.completed_at,
        )

    async def _append_audit(self, record: CaseRecord) -> None:
        event = AuditEvent(
            event_id=f"{record.case_id.value}-r{record.revision.value}",
            case_id=record.case_id,
            event_type="scan_state_changed",
            actor_id=f"system:{record.trigger}-scan",
            occurred_at=record.updated_at,
            correlation_id=record.case_id.value,
            source_revision=record.revision,
            outcome=record.status,
            evidence_digest=(
                "sha256:"
                + hashlib.sha256(
                    b"["
                    + b",".join(item.canonical_json() for item in record.evidence)
                    + b"]"
                ).hexdigest()
                if record.evidence
                else None
            ),
            preferences=next(
                (
                    item.preferences
                    for item in record.evidence
                    if item.preferences is not None
                ),
                None,
            ),
        )
        await self._repository.append_audit(
            event,
            expires_at=self._expires_at(record.created_at),
        )

    def _expires_at(self, created_at: UtcTimestamp) -> UtcTimestamp:
        return UtcTimestamp(
            created_at.value + timedelta(days=_RETENTION_DAYS[self._environment])
        )

    @staticmethod
    def _snapshot(record: CaseRecord) -> ScanSnapshot:
        stored = record.result
        result: (
            ApprovalReadyResult
            | LegacyApprovalReadyResult
            | ManualReviewResult
            | NoValidOfferResult
            | None
        ) = None
        if stored is not None and stored.outcome == "manual_review":
            result = ManualReviewResult(
                rationale=stored.rationale,
                trade_offs=stored.trade_offs,
                risk_flags=stored.risk_flags,
                uncertainty=stored.uncertainty,
                evidence_limitations=stored.evidence_limitations,
            )
        elif stored is not None and stored.outcome == "no_valid_offer":
            assert stored.product_id is not None
            assert stored.product_name is not None
            result = NoValidOfferResult(
                product_id=stored.product_id,
                product_name=stored.product_name,
                rationale=stored.rationale,
                evidence_limitations=stored.evidence_limitations,
            )
        elif stored is not None:
            required = (
                stored.product_id,
                stored.product_name,
                stored.offer_id,
                stored.evidence_digest,
                stored.quantity,
                stored.unit_price,
                stored.normalized_cost,
                stored.preference_profile_id,
                stored.preference_scope,
                stored.preference_revision,
                stored.premium_outcome,
            )
            if any(value is None for value in required):
                if stored.product_id is not None and stored.product_name is not None:
                    result = LegacyApprovalReadyResult(
                        product_id=stored.product_id,
                        product_name=stored.product_name,
                        rationale=stored.rationale,
                        trade_offs=("Authoritative evidence remains available.",),
                        risk_flags=tuple(
                            dict.fromkeys((*stored.risk_flags, "LEGACY_RECOMMENDATION"))
                        ),
                        uncertainty="This recommendation predates T27 validation.",
                        evidence_limitations=(
                            "Offer-level validation metadata is unavailable.",
                        ),
                        evidence=stored.evidence,
                    )
                else:
                    result = ManualReviewResult(
                        rationale=stored.rationale,
                        trade_offs=("Authoritative evidence remains available.",),
                        risk_flags=("LEGACY_RECOMMENDATION",),
                        uncertainty="This recommendation predates T27 validation.",
                        evidence_limitations=(
                            "Offer-level validation metadata is unavailable.",
                        ),
                    )
            else:
                assert stored.product_id is not None
                assert stored.product_name is not None
                assert stored.offer_id is not None
                assert stored.evidence_digest is not None
                assert stored.quantity is not None
                assert stored.unit_price is not None
                assert stored.normalized_cost is not None
                assert stored.preference_profile_id is not None
                assert stored.preference_scope is not None
                assert stored.preference_revision is not None
                assert stored.premium_outcome is not None
                result = ApprovalReadyResult(
                    product_id=stored.product_id,
                    product_name=stored.product_name,
                    offer_id=stored.offer_id,
                    rationale=stored.rationale,
                    trade_offs=stored.trade_offs,
                    risk_flags=stored.risk_flags,
                    uncertainty=stored.uncertainty,
                    evidence_limitations=stored.evidence_limitations,
                    evidence_digest=stored.evidence_digest,
                    quantity=stored.quantity,
                    unit_price=stored.unit_price,
                    normalized_cost=stored.normalized_cost,
                    budget_status=stored.budget_status,
                    preference_profile_id=stored.preference_profile_id,
                    preference_scope=stored.preference_scope,
                    preference_revision=stored.preference_revision,
                    priority_order=stored.priority_order,
                    premium_outcome=stored.premium_outcome,
                    evidence=stored.evidence,
                )
        error = (
            ScanFailure(
                error_code=ErrorCode(record.error.error_code),
                message=record.error.message,
                retryable=record.error.retryable,
                retry_count=record.error.retry_count,
            )
            if record.error is not None
            else None
        )
        scan_id = record.case_id.value.split(":", 1)[0]
        return ScanSnapshot(
            scan_id=scan_id,
            case_id=record.case_id.value,
            status=ScanStatus(record.status),
            trigger=ScanTrigger(record.trigger),
            created_at=record.created_at.value,
            started_at=(
                record.started_at.value if record.started_at is not None else None
            ),
            completed_at=(
                record.completed_at.value if record.completed_at is not None else None
            ),
            evidence=record.evidence,
            result=result,
            error=error,
            refinement_count=record.refinement_count,
        )

    @staticmethod
    def _scan_snapshot(record: ScanRecord) -> ScanAggregateSnapshot:
        error = (
            ScanFailure(
                error_code=ErrorCode(record.error.error_code),
                message=record.error.message,
                retryable=record.error.retryable,
                retry_count=record.error.retry_count,
            )
            if record.error is not None
            else None
        )
        return ScanAggregateSnapshot(
            scan_id=record.scan_id.value,
            status=ScanStatus(record.status),
            trigger=ScanTrigger(record.trigger),
            created_at=record.created_at.value,
            started_at=(
                record.started_at.value if record.started_at is not None else None
            ),
            completed_at=(
                record.completed_at.value if record.completed_at is not None else None
            ),
            results=record.case_summaries,
            error=error,
        )
