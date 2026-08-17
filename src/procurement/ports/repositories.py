"""Persistence records and application-repository boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from procurement.domain.audit import AuditEvent
from procurement.domain.identifiers import CaseId, Environment, Revision, ScanId
from procurement.domain.models import UtcTimestamp
from procurement.domain.policy.evidence import ProcurementEvidence


class RepositoryConflictError(Exception):
    """Base class for stable conditional-write conflicts."""


class IdempotencyConflictError(RepositoryConflictError):
    """An idempotency key was already bound to another request."""


class RevisionConflictError(RepositoryConflictError):
    """A write used a stale expected case revision."""


class ImmutableRecordError(RepositoryConflictError):
    """An append-only record already exists."""


@dataclass(frozen=True, slots=True)
class RecommendationRecord:
    """Sanitized recommendation result retained with a case."""

    product_id: str | None
    product_name: str | None
    rationale: str
    risk_flags: tuple[str, ...]
    evidence: ProcurementEvidence | None = None
    outcome: str = "approval_ready"
    offer_id: str | None = None
    trade_offs: tuple[str, ...] = ()
    uncertainty: str = "No additional uncertainty identified."
    evidence_limitations: tuple[str, ...] = ()
    evidence_digest: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    normalized_cost: Decimal | None = None
    budget_status: str = "not_evaluated"
    preference_profile_id: str | None = None
    preference_scope: str | None = None
    preference_revision: int | None = None
    priority_order: tuple[str, ...] = ()
    premium_outcome: str | None = None


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """Safe terminal failure retained with a case."""

    error_code: str
    message: str
    retryable: bool
    retry_count: int


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """Durable application view of one procurement scan/case."""

    case_id: CaseId
    revision: Revision
    status: str
    trigger: str
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    started_at: UtcTimestamp | None = None
    completed_at: UtcTimestamp | None = None
    evidence: tuple[ProcurementEvidence, ...] = ()
    result: RecommendationRecord | None = None
    error: FailureRecord | None = None


@dataclass(frozen=True, slots=True)
class CaseCreateResult:
    """Outcome of a conditional, idempotent case creation."""

    record: CaseRecord
    created: bool


@dataclass(frozen=True, slots=True)
class CasePage:
    """One bounded case page and its opaque continuation cursor."""

    records: tuple[CaseRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CaseSummary:
    """Enough of one case's result to render a scan's results table."""

    case_id: str
    product_id: str
    product_name: str
    outcome: str
    amount: Decimal | None
    need_by_date: date | None


@dataclass(frozen=True, slots=True)
class ScanRecord:
    """Durable application view of one scan run, aggregating its cases."""

    scan_id: ScanId
    revision: Revision
    status: str
    trigger: str
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    started_at: UtcTimestamp | None = None
    completed_at: UtcTimestamp | None = None
    case_summaries: tuple[CaseSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class ScanCreateResult:
    """Outcome of a conditional, idempotent scan creation."""

    record: ScanRecord
    created: bool


@dataclass(frozen=True, slots=True)
class ScanPage:
    """One bounded scan page and its opaque continuation cursor."""

    records: tuple[ScanRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """Minimal immutable approval facts required for a strong read."""

    case_id: CaseId
    revision: Revision
    approved_by: str
    approved_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class LoginTransactionRecord:
    """One-use server-side OAuth transaction with bounded retention."""

    transaction_id_hash: str
    state_hash: str
    nonce: str
    code_verifier: str
    expires_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Revocable application session addressed by an opaque-token digest."""

    session_id_hash: str
    user_id: str
    email: str
    role: str
    csrf_token_hash: str
    created_at: UtcTimestamp
    expires_at: UtcTimestamp


class ApplicationRepository(Protocol):
    """Application persistence operations independent of DynamoDB."""

    async def create_case(
        self,
        record: CaseRecord,
        *,
        idempotency_key: str,
        expires_at: UtcTimestamp,
    ) -> CaseCreateResult:
        """Create exactly one case for an idempotent request."""

    async def update_case(
        self,
        record: CaseRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> CaseRecord:
        """Update a case only at the expected revision."""

    async def get_case(self, case_id: CaseId) -> CaseRecord | None:
        """Return one case using its immutable identifier."""

    async def list_cases(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        scan_id: str | None = None,
    ) -> CasePage:
        """Return a bounded newest-first page, optionally scoped to one scan."""

    async def create_scan(
        self,
        record: ScanRecord,
        *,
        idempotency_key: str,
        expires_at: UtcTimestamp,
    ) -> ScanCreateResult:
        """Create exactly one scan for an idempotent request."""

    async def update_scan(
        self,
        record: ScanRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> ScanRecord:
        """Update a scan only at the expected revision."""

    async def get_scan(self, scan_id: ScanId) -> ScanRecord | None:
        """Return one scan using its immutable identifier."""

    async def list_scans(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> ScanPage:
        """Return a bounded newest-first page of scans."""

    async def get_approval(self, case_id: CaseId) -> ApprovalRecord | None:
        """Return the current approval using a strongly consistent read."""

    async def append_audit(
        self,
        event: AuditEvent,
        *,
        expires_at: UtcTimestamp,
    ) -> None:
        """Append one immutable audit event."""

    async def put_login_transaction(self, record: LoginTransactionRecord) -> None:
        """Persist a one-use OAuth login transaction."""

    async def consume_login_transaction(
        self,
        transaction_id_hash: str,
    ) -> LoginTransactionRecord | None:
        """Atomically remove and return one OAuth transaction."""

    async def put_session(self, record: SessionRecord) -> None:
        """Persist a new opaque application session."""

    async def get_session(self, session_id_hash: str) -> SessionRecord | None:
        """Load a session by opaque-token digest."""

    async def delete_session(self, session_id_hash: str) -> None:
        """Revoke a session if it exists."""


class InMemoryApplicationRepository(ApplicationRepository):
    """Deterministic process-local substitute for local and unit-test modes."""

    def __init__(self, *, environment: Environment) -> None:
        if not isinstance(environment, Environment):
            raise ValueError("environment must be dev or prod")
        self._environment = environment
        self._cases: dict[str, CaseRecord] = {}
        self._idempotency: dict[str, tuple[str, CaseRecord]] = {}
        self._scans: dict[str, ScanRecord] = {}
        self._scan_idempotency: dict[str, tuple[str, ScanRecord]] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._audit: dict[str, AuditEvent] = {}
        self._login_transactions: dict[str, LoginTransactionRecord] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._guard = asyncio.Lock()

    async def create_case(
        self,
        record: CaseRecord,
        *,
        idempotency_key: str,
        expires_at: UtcTimestamp,
    ) -> CaseCreateResult:
        self._validate_case(record)
        self._validate_expiry(expires_at)
        async with self._guard:
            binding = self._idempotency.get(idempotency_key)
            if binding is not None:
                case_id, original = binding
                if case_id != record.case_id.value or original != record:
                    raise IdempotencyConflictError(
                        "The idempotency key belongs to another request."
                    )
                return CaseCreateResult(
                    record=self._cases[record.case_id.value],
                    created=False,
                )
            if record.case_id.value in self._cases:
                raise IdempotencyConflictError("The case already exists.")
            self._cases[record.case_id.value] = record
            self._idempotency[idempotency_key] = (record.case_id.value, record)
            return CaseCreateResult(record=record, created=True)

    async def update_case(
        self,
        record: CaseRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> CaseRecord:
        self._validate_case(record)
        self._validate_expiry(expires_at)
        async with self._guard:
            current = self._cases.get(record.case_id.value)
            if current is None or current.revision != expected_revision:
                raise RevisionConflictError("The case revision has changed.")
            self._cases[record.case_id.value] = record
            return record

    async def get_case(self, case_id: CaseId) -> CaseRecord | None:
        self._validate_case_id(case_id)
        return self._cases.get(case_id.value)

    async def list_cases(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        scan_id: str | None = None,
    ) -> CasePage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("case page limit must be between 1 and 100")
        candidates: Iterable[CaseRecord] = self._cases.values()
        if scan_id is not None:
            prefix = f"{scan_id}:"
            candidates = (
                record
                for record in candidates
                if record.case_id.value.startswith(prefix)
            )
        records = sorted(
            candidates,
            key=lambda record: record.case_id.value,
            reverse=True,
        )
        start = 0
        if cursor is not None:
            try:
                start = next(
                    index + 1
                    for index, record in enumerate(records)
                    if record.case_id.value == cursor
                )
            except StopIteration as error:
                raise ValueError("case cursor is invalid") from error
        page = records[start : start + limit]
        next_cursor = (
            page[-1].case_id.value if start + limit < len(records) and page else None
        )
        return CasePage(records=tuple(page), next_cursor=next_cursor)

    async def create_scan(
        self,
        record: ScanRecord,
        *,
        idempotency_key: str,
        expires_at: UtcTimestamp,
    ) -> ScanCreateResult:
        self._validate_scan(record)
        self._validate_expiry(expires_at)
        async with self._guard:
            binding = self._scan_idempotency.get(idempotency_key)
            if binding is not None:
                scan_id, original = binding
                if scan_id != record.scan_id.value or original != record:
                    raise IdempotencyConflictError(
                        "The idempotency key belongs to another request."
                    )
                return ScanCreateResult(
                    record=self._scans[record.scan_id.value],
                    created=False,
                )
            if record.scan_id.value in self._scans:
                raise IdempotencyConflictError("The scan already exists.")
            self._scans[record.scan_id.value] = record
            self._scan_idempotency[idempotency_key] = (record.scan_id.value, record)
            return ScanCreateResult(record=record, created=True)

    async def update_scan(
        self,
        record: ScanRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> ScanRecord:
        self._validate_scan(record)
        self._validate_expiry(expires_at)
        async with self._guard:
            current = self._scans.get(record.scan_id.value)
            if current is None or current.revision != expected_revision:
                raise RevisionConflictError("The scan revision has changed.")
            self._scans[record.scan_id.value] = record
            return record

    async def get_scan(self, scan_id: ScanId) -> ScanRecord | None:
        self._validate_scan_id(scan_id)
        return self._scans.get(scan_id.value)

    async def list_scans(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> ScanPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("scan page limit must be between 1 and 100")
        records = sorted(
            self._scans.values(),
            key=lambda record: record.scan_id.value,
            reverse=True,
        )
        start = 0
        if cursor is not None:
            try:
                start = next(
                    index + 1
                    for index, record in enumerate(records)
                    if record.scan_id.value == cursor
                )
            except StopIteration as error:
                raise ValueError("scan cursor is invalid") from error
        page = records[start : start + limit]
        next_cursor = (
            page[-1].scan_id.value if start + limit < len(records) and page else None
        )
        return ScanPage(records=tuple(page), next_cursor=next_cursor)

    async def get_approval(self, case_id: CaseId) -> ApprovalRecord | None:
        self._validate_case_id(case_id)
        return self._approvals.get(case_id.value)

    async def append_audit(
        self,
        event: AuditEvent,
        *,
        expires_at: UtcTimestamp,
    ) -> None:
        if event.environment is not self._environment:
            raise ValueError("audit event belongs to another environment")
        self._validate_expiry(expires_at)
        async with self._guard:
            if event.event_id in self._audit:
                raise ImmutableRecordError("The audit event already exists.")
            self._audit[event.event_id] = event

    async def put_login_transaction(self, record: LoginTransactionRecord) -> None:
        async with self._guard:
            if record.transaction_id_hash in self._login_transactions:
                raise ImmutableRecordError("The login transaction already exists.")
            self._login_transactions[record.transaction_id_hash] = record

    async def consume_login_transaction(
        self,
        transaction_id_hash: str,
    ) -> LoginTransactionRecord | None:
        async with self._guard:
            return self._login_transactions.pop(transaction_id_hash, None)

    async def put_session(self, record: SessionRecord) -> None:
        async with self._guard:
            if record.session_id_hash in self._sessions:
                raise ImmutableRecordError("The session already exists.")
            self._sessions[record.session_id_hash] = record

    async def get_session(self, session_id_hash: str) -> SessionRecord | None:
        return self._sessions.get(session_id_hash)

    async def delete_session(self, session_id_hash: str) -> None:
        async with self._guard:
            self._sessions.pop(session_id_hash, None)

    def _validate_case(self, record: CaseRecord) -> None:
        if not isinstance(record, CaseRecord):
            raise ValueError("record must be a CaseRecord")
        self._validate_case_id(record.case_id)

    def _validate_case_id(self, case_id: CaseId) -> None:
        if (
            not isinstance(case_id, CaseId)
            or case_id.environment is not self._environment
        ):
            raise ValueError("case belongs to another environment")

    def _validate_scan(self, record: ScanRecord) -> None:
        if not isinstance(record, ScanRecord):
            raise ValueError("record must be a ScanRecord")
        self._validate_scan_id(record.scan_id)

    def _validate_scan_id(self, scan_id: ScanId) -> None:
        if (
            not isinstance(scan_id, ScanId)
            or scan_id.environment is not self._environment
        ):
            raise ValueError("scan belongs to another environment")

    @staticmethod
    def _validate_expiry(expires_at: UtcTimestamp) -> None:
        if not isinstance(expires_at, UtcTimestamp):
            raise ValueError("expiry must be a UTC timestamp")
