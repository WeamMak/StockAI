"""DynamoDB application-repository request and concurrency contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from tests.unit.domain.policy.test_evidence import _evidence

from procurement.adapters.aws.dynamodb import DynamoApplicationRepository
from procurement.domain.audit import AuditEvent
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
    apply_preferences,
)
from procurement.ports.repositories import (
    ApprovalRecord,
    CandidateSnapshot,
    CaseRecord,
    IdempotencyConflictError,
    ImmutableRecordError,
    LoginTransactionRecord,
    RevisionConflictError,
    SessionRecord,
)

TABLE_NAME = "stockai-dev-application"
CASE_ID = CaseId(Environment.DEV, "scan-20260809T120000000000Z-001")
CREATED_AT = UtcTimestamp(datetime(2026, 8, 9, 12, tzinfo=UTC))
UPDATED_AT = UtcTimestamp(datetime(2026, 8, 9, 12, 0, 1, tzinfo=UTC))
EXPIRES_AT = UtcTimestamp(datetime(2026, 9, 8, 12, tzinfo=UTC))


class RecordingDynamoClient:
    """Tiny low-level client fake with queued responses and captured requests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, list[object]] = {}

    def queue(self, operation: str, *responses: object) -> None:
        self.responses.setdefault(operation, []).extend(responses)

    def _call(self, operation: str, request: dict[str, Any]) -> Mapping[str, Any]:
        self.calls.append((operation, request))
        queued = self.responses.get(operation, [])
        response = queued.pop(0) if queued else {}
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    def transact_write_items(self, **request: Any) -> Mapping[str, Any]:
        return self._call("transact_write_items", request)

    def get_item(self, **request: Any) -> Mapping[str, Any]:
        return self._call("get_item", request)

    def update_item(self, **request: Any) -> Mapping[str, Any]:
        return self._call("update_item", request)

    def put_item(self, **request: Any) -> Mapping[str, Any]:
        return self._call("put_item", request)

    def query(self, **request: Any) -> Mapping[str, Any]:
        return self._call("query", request)

    def delete_item(self, **request: Any) -> Mapping[str, Any]:
        return self._call("delete_item", request)


def _record(*, revision: int = 1, status: str = "queued") -> CaseRecord:
    return CaseRecord(
        case_id=CASE_ID,
        revision=Revision(revision),
        status=status,
        trigger="manual",
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )


def _case_item(*, revision: int = 1, status: str = "queued") -> dict[str, Any]:
    return {
        "PK": {"S": "ENV#dev"},
        "SK": {"S": f"CASE#{CASE_ID.value}"},
        "entity_type": {"S": "case"},
        "case_id": {"S": CASE_ID.value},
        "revision": {"N": str(revision)},
        "status": {"S": status},
        "trigger": {"S": "manual"},
        "created_at": {"S": CREATED_AT.value.isoformat()},
        "updated_at": {"S": UPDATED_AT.value.isoformat()},
        "ttl": {"N": str(int(EXPIRES_AT.value.timestamp()))},
    }


@pytest.mark.anyio
async def test_case_round_trip_preserves_immutable_preference_snapshot() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client, table_name=TABLE_NAME, environment=Environment.DEV
    )
    profile = ProcurementPreference(
        profile_id="preference-3",
        company_id="7",
        category_id="category-1",
        product_id="product-1",
        scope=PreferenceScope.PRODUCT,
        scope_id="product-1",
        revision=6,
        ordered_criteria=(
            PreferenceCriterion.PRICE,
            PreferenceCriterion.RELIABILITY,
            PreferenceCriterion.DELIVERY,
        ),
        max_price_premium_percent=Decimal("10.000000"),
        enforcement_mode=PremiumEnforcement.ADVISORY,
        precedence_source=PreferenceScope.PRODUCT,
    )
    evidence = apply_preferences(_evidence(), profile)
    record = CaseRecord(
        case_id=CASE_ID,
        revision=Revision(2),
        status="succeeded",
        trigger="manual",
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
        evidence=(evidence,),
    )
    client.queue(
        "get_item",
        {"Item": repository._case_item(record, expires_at=EXPIRES_AT)},
    )

    restored = await repository.get_case(CASE_ID)

    assert restored == record
    assert restored is not None
    assert restored.evidence[0].preferences == evidence.preferences


@pytest.mark.anyio
async def test_case_round_trip_preserves_candidate_snapshot_and_refinement_count() -> (
    None
):
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client, table_name=TABLE_NAME, environment=Environment.DEV
    )
    record = CaseRecord(
        case_id=CASE_ID,
        revision=Revision(2),
        status="succeeded",
        trigger="manual",
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
        candidate_snapshot=CandidateSnapshot(
            category_id="category-safety",
            reorder_minimum=Decimal("10.000000"),
            reorder_maximum=Decimal("40.000000"),
            projected_quantity=Decimal("8.000000"),
            projected_trigger_date=date(2026, 8, 9),
        ),
        refinement_count=2,
    )
    client.queue(
        "get_item",
        {"Item": repository._case_item(record, expires_at=EXPIRES_AT)},
    )

    restored = await repository.get_case(CASE_ID)

    assert restored == record


@pytest.mark.anyio
async def test_case_without_candidate_snapshot_restores_zero_refinement_count() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client, table_name=TABLE_NAME, environment=Environment.DEV
    )
    client.queue("get_item", {"Item": _case_item()})

    restored = await repository.get_case(CASE_ID)

    assert restored is not None
    assert restored.candidate_snapshot is None
    assert restored.refinement_count == 0


def _transaction_cancelled() -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "private"},
            "CancellationReasons": [{"Code": "ConditionalCheckFailed"}],
        },
        "TransactWriteItems",
    )


@pytest.mark.anyio
async def test_create_case_uses_environment_prefixed_conditional_keys_and_ttl() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )

    result = await repository.create_case(
        _record(),
        idempotency_key="manual-request-001",
        expires_at=EXPIRES_AT,
    )

    assert result.created is True
    operation, request = client.calls[0]
    assert operation == "transact_write_items"
    writes = request["TransactItems"]
    case_put = writes[0]["Put"]
    idempotency_put = writes[1]["Put"]
    assert case_put["TableName"] == TABLE_NAME
    assert case_put["ConditionExpression"] == (
        "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    )
    assert case_put["Item"]["PK"] == {"S": "ENV#dev"}
    assert case_put["Item"]["SK"] == {"S": f"CASE#{CASE_ID.value}"}
    assert case_put["Item"]["ttl"] == {"N": str(int(EXPIRES_AT.value.timestamp()))}
    assert idempotency_put["Item"]["PK"] == {"S": "ENV#dev"}
    assert idempotency_put["Item"]["SK"] == {"S": "IDEMPOTENCY#manual-request-001"}
    assert "private" not in str(request)


@pytest.mark.anyio
async def test_repeating_the_same_idempotent_create_returns_the_existing_case() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    fingerprint = repository.case_fingerprint(_record())
    client.queue("transact_write_items", _transaction_cancelled())
    client.queue(
        "get_item",
        {
            "Item": {
                "PK": {"S": "ENV#dev"},
                "SK": {"S": "IDEMPOTENCY#manual-request-001"},
                "case_id": {"S": CASE_ID.value},
                "fingerprint": {"S": fingerprint},
            }
        },
        {"Item": _case_item()},
    )

    result = await repository.create_case(
        _record(),
        idempotency_key="manual-request-001",
        expires_at=EXPIRES_AT,
    )

    assert result.created is False
    assert result.record == _record()
    reads = [request for operation, request in client.calls if operation == "get_item"]
    assert all(request["ConsistentRead"] is True for request in reads)


@pytest.mark.anyio
async def test_reusing_an_idempotency_key_for_another_request_is_rejected() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    client.queue("transact_write_items", _transaction_cancelled())
    client.queue(
        "get_item",
        {
            "Item": {
                "case_id": {"S": "scan-another-case"},
                "fingerprint": {"S": "another-fingerprint"},
            }
        },
    )

    with pytest.raises(IdempotencyConflictError):
        await repository.create_case(
            _record(),
            idempotency_key="manual-request-001",
            expires_at=EXPIRES_AT,
        )


@pytest.mark.anyio
async def test_revisioned_update_uses_an_optimistic_condition() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    client.queue(
        "update_item",
        {"Attributes": _case_item(revision=2, status="running")},
    )

    updated = await repository.update_case(
        _record(revision=2, status="running"),
        expected_revision=Revision(1),
        expires_at=EXPIRES_AT,
    )

    request = client.calls[0][1]
    assert request["ConditionExpression"] == (
        "attribute_exists(PK) AND revision = :expected_revision"
    )
    assert request["ExpressionAttributeValues"][":expected_revision"] == {"N": "1"}
    assert request["ExpressionAttributeValues"][":revision"] == {"N": "2"}
    assert updated.revision == Revision(2)


@pytest.mark.anyio
async def test_stale_revision_is_a_stable_repository_conflict() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    client.queue(
        "update_item",
        ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "private current item",
                }
            },
            "UpdateItem",
        ),
    )

    with pytest.raises(RevisionConflictError) as raised:
        await repository.update_case(
            _record(revision=2, status="running"),
            expected_revision=Revision(1),
            expires_at=EXPIRES_AT,
        )

    assert "private" not in str(raised.value)


@pytest.mark.anyio
async def test_approval_read_is_strongly_consistent() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    client.queue(
        "get_item",
        {
            "Item": {
                "PK": {"S": "ENV#dev"},
                "SK": {"S": f"APPROVAL#{CASE_ID.value}"},
                "case_id": {"S": CASE_ID.value},
                "revision": {"N": "3"},
                "approved_by": {"S": "manager-001"},
                "approved_at": {"S": UPDATED_AT.value.isoformat()},
                "ttl": {"N": str(int(EXPIRES_AT.value.timestamp()))},
            }
        },
    )

    approval = await repository.get_approval(CASE_ID)

    assert approval == ApprovalRecord(
        case_id=CASE_ID,
        revision=Revision(3),
        approved_by="manager-001",
        approved_at=UPDATED_AT,
    )
    request = client.calls[0][1]
    assert request["ConsistentRead"] is True
    assert request["Key"]["PK"] == {"S": "ENV#dev"}


@pytest.mark.anyio
async def test_audit_append_is_immutable_and_retained_by_ttl() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    event = AuditEvent(
        event_id="audit-scan-001-r1",
        case_id=CASE_ID,
        event_type="scan_state_changed",
        actor_id="system:manual-scan",
        occurred_at=CREATED_AT,
        correlation_id=CASE_ID.value,
        source_revision=Revision(1),
        outcome="queued",
        evidence_digest="sha256:" + "a" * 64,
    )

    await repository.append_audit(event, expires_at=EXPIRES_AT)

    request = client.calls[0][1]
    assert request["ConditionExpression"] == (
        "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    )
    assert request["Item"]["PK"] == {"S": "ENV#dev"}
    assert request["Item"]["SK"]["S"].startswith(f"AUDIT#{CASE_ID.value}#")
    assert request["Item"]["ttl"] == {"N": str(int(EXPIRES_AT.value.timestamp()))}
    assert request["Item"]["evidence_digest"] == {"S": "sha256:" + "a" * 64}

    client.queue(
        "put_item",
        ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
            "PutItem",
        ),
    )
    with pytest.raises(ImmutableRecordError):
        await repository.append_audit(event, expires_at=EXPIRES_AT)


@pytest.mark.anyio
async def test_case_listing_is_bounded_newest_first_and_uses_an_opaque_cursor() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    last_key = {
        "PK": {"S": "ENV#dev"},
        "SK": {"S": f"CASE#{CASE_ID.value}"},
    }
    client.queue(
        "query",
        {"Items": [_case_item()], "LastEvaluatedKey": last_key},
        {"Items": [_case_item()]},
    )

    first = await repository.list_cases(limit=1)
    second = await repository.list_cases(limit=1, cursor=first.next_cursor)

    assert first.records == (_record(),)
    assert first.next_cursor is not None
    first_request = client.calls[0][1]
    assert first_request["KeyConditionExpression"] == (
        "PK = :environment AND begins_with(SK, :case_prefix)"
    )
    assert first_request["ScanIndexForward"] is False
    assert first_request["Limit"] == 1
    assert "ExclusiveStartKey" not in first_request
    assert client.calls[1][1]["ExclusiveStartKey"] == last_key
    assert second.records == (_record(),)


@pytest.mark.anyio
async def test_case_listing_accepts_preference_free_legacy_evidence() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    legacy_evidence = _evidence().to_dict()
    legacy_evidence.pop("preferences")
    item = _case_item(status="succeeded")
    item["evidence"] = {
        "L": [{"S": json.dumps(legacy_evidence, separators=(",", ":"))}]
    }
    client.queue("query", {"Items": [item]})

    page = await repository.list_cases(limit=1)

    assert page.records[0].evidence[0].preferences is None


@pytest.mark.anyio
async def test_session_is_environment_scoped_consistent_and_revocable() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    record = SessionRecord(
        session_id_hash="a" * 64,
        user_id="cognito-user-001",
        email="officer@example.invalid",
        role="officer",
        csrf_token_hash="b" * 64,
        created_at=CREATED_AT,
        expires_at=EXPIRES_AT,
    )
    client.queue(
        "get_item",
        {
            "Item": {
                "PK": {"S": "ENV#dev"},
                "SK": {"S": f"SESSION#{'a' * 64}"},
                "entity_type": {"S": "session"},
                "session_id_hash": {"S": "a" * 64},
                "user_id": {"S": "cognito-user-001"},
                "email": {"S": "officer@example.invalid"},
                "role": {"S": "officer"},
                "csrf_token_hash": {"S": "b" * 64},
                "created_at": {"S": CREATED_AT.value.isoformat()},
                "expires_at": {"S": EXPIRES_AT.value.isoformat()},
                "ttl": {"N": str(int(EXPIRES_AT.value.timestamp()))},
            }
        },
    )

    await repository.put_session(record)
    restored = await repository.get_session(record.session_id_hash)
    await repository.delete_session(record.session_id_hash)

    put_request = client.calls[0][1]
    assert put_request["ConditionExpression"] == (
        "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    )
    assert put_request["Item"]["PK"] == {"S": "ENV#dev"}
    assert put_request["Item"]["SK"] == {"S": f"SESSION#{'a' * 64}"}
    assert put_request["Item"]["ttl"] == {"N": str(int(EXPIRES_AT.value.timestamp()))}
    assert client.calls[1][1]["ConsistentRead"] is True
    assert client.calls[2] == (
        "delete_item",
        {
            "TableName": TABLE_NAME,
            "Key": {
                "PK": {"S": "ENV#dev"},
                "SK": {"S": f"SESSION#{'a' * 64}"},
            },
        },
    )
    assert restored == record


@pytest.mark.anyio
async def test_login_transaction_is_conditionally_written_and_atomically_consumed() -> (
    None
):
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client,
        table_name=TABLE_NAME,
        environment=Environment.DEV,
    )
    record = LoginTransactionRecord(
        transaction_id_hash="c" * 64,
        state_hash="d" * 64,
        nonce="opaque-nonce",
        code_verifier="opaque-code-verifier-at-least-forty-three-characters",
        expires_at=EXPIRES_AT,
    )
    client.queue(
        "delete_item",
        {
            "Attributes": {
                "transaction_id_hash": {"S": "c" * 64},
                "state_hash": {"S": "d" * 64},
                "nonce": {"S": "opaque-nonce"},
                "code_verifier": {
                    "S": "opaque-code-verifier-at-least-forty-three-characters"
                },
                "expires_at": {"S": EXPIRES_AT.value.isoformat()},
            }
        },
    )

    await repository.put_login_transaction(record)
    consumed = await repository.consume_login_transaction(record.transaction_id_hash)

    assert client.calls[0][1]["ConditionExpression"] == (
        "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    )
    assert client.calls[0][1]["Item"]["SK"] == {"S": f"LOGIN#{'c' * 64}"}
    assert client.calls[1][1]["ReturnValues"] == "ALL_OLD"
    assert consumed == record
