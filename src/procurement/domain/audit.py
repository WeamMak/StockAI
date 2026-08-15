"""Immutable, sanitized audit events for procurement state transitions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from procurement.domain.errors import DomainValidationError, FieldError
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp

MAX_AUDIT_TEXT_LENGTH = 128
_AUDIT_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)


def _validated_audit_text(value: object, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_AUDIT_TEXT_LENGTH
        or _AUDIT_TEXT_PATTERN.fullmatch(value) is None
    ):
        raise DomainValidationError(
            "The audit event is invalid.",
            field_errors=(
                FieldError(
                    field=field,
                    message=f"{field} must be bounded safe identifier text.",
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One append-only, sanitized record of a case transition."""

    event_id: str
    case_id: CaseId
    event_type: str
    actor_id: str
    occurred_at: UtcTimestamp
    correlation_id: str
    source_revision: Revision
    outcome: str
    evidence_digest: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("event_id", self.event_id),
            ("event_type", self.event_type),
            ("actor_id", self.actor_id),
            ("correlation_id", self.correlation_id),
            ("outcome", self.outcome),
        ):
            _validated_audit_text(value, field=field)
        if not isinstance(self.case_id, CaseId):
            raise DomainValidationError(
                "The audit case identifier is invalid.",
                field_errors=(FieldError(field="case_id", message="Use a CaseId."),),
            )
        if not isinstance(self.occurred_at, UtcTimestamp):
            raise DomainValidationError(
                "The audit timestamp is invalid.",
                field_errors=(
                    FieldError(field="occurred_at", message="Use a UTC timestamp."),
                ),
            )
        if not isinstance(self.source_revision, Revision):
            raise DomainValidationError(
                "The audit source revision is invalid.",
                field_errors=(
                    FieldError(field="source_revision", message="Use a Revision."),
                ),
            )
        if (
            self.evidence_digest is not None
            and _DIGEST_PATTERN.fullmatch(self.evidence_digest) is None
        ):
            raise DomainValidationError(
                "The audit evidence digest is invalid.",
                field_errors=(
                    FieldError(
                        field="evidence_digest",
                        message="Use a lowercase SHA-256 digest.",
                    ),
                ),
            )

    @property
    def environment(self) -> Environment:
        """Return the environment that owns this event."""

        return self.case_id.environment
