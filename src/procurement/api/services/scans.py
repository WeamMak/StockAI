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
    ScanResult,
    ScanState,
    UnresolvedResult,
)
from procurement.domain.audit import AuditEvent
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.observability.logging import log_event
from procurement.observability.metrics import AgentMetrics, create_agent_metrics
from procurement.ports.repositories import (
    ApplicationRepository,
    CaseRecord,
    FailureRecord,
    InMemoryApplicationRepository,
    RecommendationRecord,
)

DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 120.0
MAX_SCAN_HISTORY = 100
_RETENTION_DAYS = {Environment.DEV: 30, Environment.PROD: 365}


class ScanStatus(StrEnum):
    """Stable API-visible status of one asynchronous scan."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ScanTrigger(StrEnum):
    """Authorized source that requested a scan."""

    MANUAL = "manual"
    CRON = "cron"


@dataclass(frozen=True, slots=True)
class ScanFailure:
    """Safe failure exposed by scan polling."""

    error_code: ErrorCode
    message: str
    retryable: bool
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class ScanSnapshot:
    """Immutable API view of one durable scan record."""

    scan_id: str
    status: ScanStatus
    trigger: ScanTrigger
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    evidence: tuple[ProcurementEvidence, ...]
    result: ApprovalReadyResult | None
    error: ScanFailure | None


class ScanWorkflow(Protocol):
    """Compiled graph operation needed by the scan service."""

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        """Run one non-human workflow to a terminal result."""


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

    async def start_scan(self, *, trigger: ScanTrigger) -> ScanSnapshot:
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
            record = CaseRecord(
                case_id=CaseId(self._environment, scan_id),
                revision=Revision(1),
                status=ScanStatus.QUEUED.value,
                trigger=trigger.value,
                created_at=timestamp,
                updated_at=timestamp,
            )
            created = await self._repository.create_case(
                record,
                idempotency_key=scan_id,
                expires_at=self._expires_at(record),
            )
            if not created.created:
                return self._snapshot(created.record)
            await self._append_audit(record)
            self._active_scan_id = scan_id
            task = asyncio.create_task(self._run_scan(record))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return self._snapshot(record)

    async def list_scans(self) -> tuple[ScanSnapshot, ...]:
        """Return bounded newest-first durable scan history."""

        page = await self._repository.list_cases(
            limit=MAX_SCAN_HISTORY,
        )
        return tuple(self._snapshot(record) for record in page.records)

    async def get_scan(self, scan_id: str) -> ScanSnapshot:
        """Return one scan or a stable safe not-found error."""

        try:
            case_id = CaseId(self._environment, scan_id)
        except DomainError:
            case_id = None
        record = (
            await self._repository.get_case(case_id) if case_id is not None else None
        )
        if record is None:
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="The requested scan was not found.",
            )
        return self._snapshot(record)

    async def _run_scan(self, record: CaseRecord) -> None:
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
            running = await self._repository.update_case(
                running,
                expected_revision=record.revision,
                expires_at=self._expires_at(record),
            )
            await self._append_audit(running)
            try:
                async with asyncio.timeout(self._workflow_timeout_seconds):
                    state = await self._workflow.ainvoke(
                        {
                            "scan_id": record.case_id.value,
                            "environment": self._environment,
                        },
                        config={
                            "configurable": {"thread_id": record.case_id.value},
                        },
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
                completed_at=completed_at,
                updated_at=completed_at,
            )
            terminal = await self._repository.update_case(
                terminal,
                expected_revision=running.revision,
                expires_at=self._expires_at(record),
            )
            await self._append_audit(terminal)
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
                outcome=(
                    "approval_ready"
                    if terminal.status == ScanStatus.SUCCEEDED.value
                    else "unresolved"
                ),
                error_code=error_code,
            )
            fields: dict[str, object] = {
                "scan_id": terminal.case_id.value,
                "status": terminal.status,
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
                outcome="unresolved",
                error_code=ErrorCode.RECONCILIATION_REQUIRED,
            )
            log_event(
                self._logger,
                "scan_persistence_failed",
                level=logging.ERROR,
                scan_id=record.case_id.value,
                status=record.status,
                duration_ms=round(duration_seconds * 1000, 3),
                retry_count=0,
                error_code=ErrorCode.RECONCILIATION_REQUIRED.value,
            )
        finally:
            async with self._guard:
                if self._active_scan_id == record.case_id.value:
                    self._active_scan_id = None

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
                    rationale=result.rationale,
                    risk_flags=result.risk_flags,
                    evidence=result.evidence,
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
        )
        await self._repository.append_audit(
            event,
            expires_at=self._expires_at(record),
        )

    def _expires_at(self, record: CaseRecord) -> UtcTimestamp:
        return UtcTimestamp(
            record.created_at.value + timedelta(days=_RETENTION_DAYS[self._environment])
        )

    @staticmethod
    def _snapshot(record: CaseRecord) -> ScanSnapshot:
        result = (
            ApprovalReadyResult(
                product_id=record.result.product_id,
                product_name=record.result.product_name,
                rationale=record.result.rationale,
                risk_flags=record.result.risk_flags,
                evidence=record.result.evidence,
            )
            if record.result is not None
            else None
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
        return ScanSnapshot(
            scan_id=record.case_id.value,
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
        )
