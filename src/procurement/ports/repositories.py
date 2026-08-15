"""Persistence records and application-repository boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from procurement.domain.audit import AuditEvent
from procurement.domain.identifiers import CaseId, Environment, Revision
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
    ) -> CasePage:
        """Return a bounded newest-first page."""

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
    ) -> CasePage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("case page limit must be between 1 and 100")
        records = sorted(
            self._cases.values(),
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

    @staticmethod
    def _validate_expiry(expires_at: UtcTimestamp) -> None:
        if not isinstance(expires_at, UtcTimestamp):
            raise ValueError("expiry must be a UTC timestamp")
