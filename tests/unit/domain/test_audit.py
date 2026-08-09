"""Immutable, sanitized procurement audit-event contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from procurement.domain.audit import AuditEvent
from procurement.domain.errors import DomainValidationError
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp


def _event(**overrides: object) -> AuditEvent:
    values: dict[str, object] = {
        "event_id": "audit-scan-001-r1",
        "case_id": CaseId(Environment.DEV, "scan-20260809T120000000000Z-001"),
        "event_type": "scan_state_changed",
        "actor_id": "system:manual-scan",
        "occurred_at": UtcTimestamp(datetime(2026, 8, 9, 12, tzinfo=UTC)),
        "correlation_id": "scan-20260809T120000000000Z-001",
        "source_revision": Revision(1),
        "outcome": "queued",
    }
    values.update(overrides)
    return AuditEvent(**values)  # type: ignore[arg-type]


def test_audit_event_is_immutable_and_keeps_only_sanitized_transition_data() -> None:
    event = _event()

    assert event.environment is Environment.DEV
    assert event.outcome == "queued"
    with pytest.raises(FrozenInstanceError):
        event.outcome = "rewritten"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "unsafe event id"),
        ("event_type", "scan\nstate"),
        ("actor_id", "actor secret\x00value"),
        ("correlation_id", "correlation id"),
        ("outcome", "x" * 129),
    ],
)
def test_audit_event_rejects_unsafe_or_unbounded_text(
    field: str,
    value: str,
) -> None:
    with pytest.raises(DomainValidationError):
        _event(**{field: value})


def test_audit_event_requires_typed_case_revision_and_utc_time() -> None:
    with pytest.raises(DomainValidationError):
        _event(case_id="case-001")
    with pytest.raises(DomainValidationError):
        _event(source_revision=1)
    with pytest.raises(DomainValidationError):
        _event(occurred_at=datetime(2026, 8, 9, 12, tzinfo=UTC))
