"""Low-level DynamoDB application repository with conditional writes."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from procurement.domain.audit import AuditEvent
from procurement.domain.decisions import ApprovalRecord as ManagerApprovalRecord
from procurement.domain.decisions import (
    DecisionId,
    DecisionRecord,
    DecisionText,
    DecisionType,
    RejectionRecord,
)
from procurement.domain.identifiers import CaseId, Environment, Revision, ScanId
from procurement.domain.models import UtcTimestamp
from procurement.domain.policy.evidence import procurement_evidence_from_dict
from procurement.domain.policy.preferences import (
    AppliedPreferences,
    OfferPremiumResult,
    preference_from_dict,
)
from procurement.ports.decisions import DecisionConflictError, DecisionCreateResult
from procurement.ports.repositories import (
    ApplicationRepository,
    ApprovalRecord,
    CandidateSnapshot,
    CaseCreateResult,
    CasePage,
    CaseRecord,
    CaseSummary,
    DraftRecord,
    FailureRecord,
    IdempotencyConflictError,
    ImmutableRecordError,
    LoginTransactionRecord,
    RecommendationRecord,
    RevisionConflictError,
    ScanCreateResult,
    ScanPage,
    ScanRecord,
    SessionRecord,
)

MAX_PAGE_SIZE = 100
_SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_CONDITIONAL_CREATE = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
_OPTIONAL_CASE_ATTRIBUTES = frozenset(
    {
        "started_at",
        "completed_at",
        "result",
        "evidence",
        "error",
        "candidate_snapshot",
        "draft",
        "workflow_thread_id",
    }
)


class DynamoClient(Protocol):
    """Low-level operations used by the application table adapter."""

    def transact_write_items(self, **request: Any) -> Mapping[str, Any]: ...

    def get_item(self, **request: Any) -> Mapping[str, Any]: ...

    def update_item(self, **request: Any) -> Mapping[str, Any]: ...

    def put_item(self, **request: Any) -> Mapping[str, Any]: ...

    def query(self, **request: Any) -> Mapping[str, Any]: ...

    def delete_item(self, **request: Any) -> Mapping[str, Any]: ...


def create_dynamodb_client(
    *,
    region_name: str,
    endpoint_url: str | None = None,
) -> DynamoClient:
    """Create the low-level application-table client at the composition edge."""

    client = boto3.client(
        "dynamodb",
        region_name=region_name,
        endpoint_url=endpoint_url,
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"mode": "standard", "max_attempts": 3},
        ),
    )
    return cast(DynamoClient, client)


class DynamoApplicationRepository(ApplicationRepository):
    """Environment-scoped application state in one DynamoDB table."""

    def __init__(
        self,
        *,
        client: DynamoClient,
        table_name: str,
        environment: Environment,
    ) -> None:
        if not table_name.strip():
            raise ValueError("application table name is required")
        if not isinstance(environment, Environment):
            raise ValueError("environment must be dev or prod")
        self._client = client
        self._table_name = table_name
        self._environment = environment

    @property
    def _partition_key(self) -> str:
        return f"ENV#{self._environment.value}"

    @staticmethod
    def case_fingerprint(record: CaseRecord) -> str:
        """Return a stable request fingerprint without retaining request content."""

        payload = {
            "case_id": record.case_id.value,
            "created_at": record.created_at.value.isoformat(),
            "status": record.status,
            "trigger": record.trigger,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def scan_fingerprint(record: ScanRecord) -> str:
        """Return a stable request fingerprint without retaining request content."""

        payload = {
            "scan_id": record.scan_id.value,
            "created_at": record.created_at.value.isoformat(),
            "status": record.status,
            "trigger": record.trigger,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def create_case(
        self,
        record: CaseRecord,
        *,
        idempotency_key: str,
        expires_at: UtcTimestamp,
    ) -> CaseCreateResult:
        self._validate_case(record)
        self._validate_expiry(expires_at)
        self._validate_key(idempotency_key, name="idempotency key")
        fingerprint = self.case_fingerprint(record)
        idempotency_item = {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"IDEMPOTENCY#{idempotency_key}"},
            "entity_type": {"S": "idempotency"},
            "case_id": {"S": record.case_id.value},
            "fingerprint": {"S": fingerprint},
            "ttl": self._ttl(expires_at),
        }
        request = {
            "TransactItems": [
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": self._case_item(record, expires_at=expires_at),
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": idempotency_item,
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
            ]
        }
        try:
            self._client.transact_write_items(**request)
        except ClientError as error:
            if self._error_code(error) != "TransactionCanceledException":
                raise
            return await self._resolve_idempotent_create(
                record,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
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
        if not isinstance(expected_revision, Revision):
            raise ValueError("expected revision must be a Revision")
        values = self._case_attributes(record, expires_at=expires_at)
        values.pop("case_id")
        remove_names = _OPTIONAL_CASE_ATTRIBUTES - values.keys()
        names = {f"#{name}": name for name in (*values, *remove_names)}
        expression_values = {f":{name}": value for name, value in values.items()}
        expression_values[":expected_revision"] = {"N": str(expected_revision.value)}
        update_expression = "SET " + ", ".join(f"#{name} = :{name}" for name in values)
        if remove_names:
            update_expression += " REMOVE " + ", ".join(
                f"#{name}" for name in remove_names
            )
        request = {
            "TableName": self._table_name,
            "Key": self._case_key(record.case_id),
            "UpdateExpression": update_expression,
            "ConditionExpression": (
                "attribute_exists(PK) AND revision = :expected_revision"
            ),
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": expression_values,
            "ReturnValues": "ALL_NEW",
        }
        try:
            response = self._client.update_item(**request)
        except ClientError as error:
            if self._error_code(error) == "ConditionalCheckFailedException":
                raise RevisionConflictError("The case revision has changed.") from None
            raise
        attributes = cast(Mapping[str, Any], response.get("Attributes", {}))
        return self._case_from_item(attributes)

    async def get_case(self, case_id: CaseId) -> CaseRecord | None:
        self._validate_case_id(case_id)
        response = self._client.get_item(
            TableName=self._table_name,
            Key=self._case_key(case_id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return self._case_from_item(cast(Mapping[str, Any], item)) if item else None

    async def list_cases(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        scan_id: str | None = None,
    ) -> CasePage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("case page limit must be between 1 and 100")
        case_prefix = f"CASE#{scan_id}:" if scan_id is not None else "CASE#"
        request: dict[str, Any] = {
            "TableName": self._table_name,
            "KeyConditionExpression": (
                "PK = :environment AND begins_with(SK, :case_prefix)"
            ),
            "ExpressionAttributeValues": {
                ":environment": {"S": self._partition_key},
                ":case_prefix": {"S": case_prefix},
            },
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor is not None:
            request["ExclusiveStartKey"] = self._decode_cursor(cursor)
        response = self._client.query(**request)
        items = cast(list[Mapping[str, Any]], response.get("Items", []))
        last_key = cast(Mapping[str, Any] | None, response.get("LastEvaluatedKey"))
        return CasePage(
            records=tuple(self._case_from_item(item) for item in items),
            next_cursor=self._encode_cursor(last_key) if last_key else None,
        )

    async def create_scan(
        self,
        record: ScanRecord,
        *,
        idempotency_key: str,
        expires_at: UtcTimestamp,
    ) -> ScanCreateResult:
        self._validate_scan(record)
        self._validate_expiry(expires_at)
        self._validate_key(idempotency_key, name="idempotency key")
        fingerprint = self.scan_fingerprint(record)
        idempotency_item = {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"IDEMPOTENCY#{idempotency_key}"},
            "entity_type": {"S": "idempotency"},
            "scan_id": {"S": record.scan_id.value},
            "fingerprint": {"S": fingerprint},
            "ttl": self._ttl(expires_at),
        }
        request = {
            "TransactItems": [
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": self._scan_item(record, expires_at=expires_at),
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": idempotency_item,
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
            ]
        }
        try:
            self._client.transact_write_items(**request)
        except ClientError as error:
            if self._error_code(error) != "TransactionCanceledException":
                raise
            return await self._resolve_idempotent_scan_create(
                record,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
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
        if not isinstance(expected_revision, Revision):
            raise ValueError("expected revision must be a Revision")
        values = self._scan_attributes(record, expires_at=expires_at)
        values.pop("scan_id")
        names = {f"#{name}": name for name in values}
        expression_values = {f":{name}": value for name, value in values.items()}
        expression_values[":expected_revision"] = {"N": str(expected_revision.value)}
        request = {
            "TableName": self._table_name,
            "Key": self._scan_key(record.scan_id),
            "UpdateExpression": "SET "
            + ", ".join(f"#{name} = :{name}" for name in values),
            "ConditionExpression": (
                "attribute_exists(PK) AND revision = :expected_revision"
            ),
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": expression_values,
            "ReturnValues": "ALL_NEW",
        }
        try:
            response = self._client.update_item(**request)
        except ClientError as error:
            if self._error_code(error) == "ConditionalCheckFailedException":
                raise RevisionConflictError("The scan revision has changed.") from None
            raise
        attributes = cast(Mapping[str, Any], response.get("Attributes", {}))
        return self._scan_from_item(attributes)

    async def get_scan(self, scan_id: ScanId) -> ScanRecord | None:
        self._validate_scan_id(scan_id)
        response = self._client.get_item(
            TableName=self._table_name,
            Key=self._scan_key(scan_id),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return self._scan_from_item(cast(Mapping[str, Any], item)) if item else None

    async def list_scans(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> ScanPage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("scan page limit must be between 1 and 100")
        request: dict[str, Any] = {
            "TableName": self._table_name,
            "KeyConditionExpression": (
                "PK = :environment AND begins_with(SK, :scan_prefix)"
            ),
            "ExpressionAttributeValues": {
                ":environment": {"S": self._partition_key},
                ":scan_prefix": {"S": "SCAN#"},
            },
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor is not None:
            request["ExclusiveStartKey"] = self._decode_scan_cursor(cursor)
        response = self._client.query(**request)
        items = cast(list[Mapping[str, Any]], response.get("Items", []))
        last_key = cast(Mapping[str, Any] | None, response.get("LastEvaluatedKey"))
        return ScanPage(
            records=tuple(self._scan_from_item(item) for item in items),
            next_cursor=self._encode_cursor(last_key) if last_key else None,
        )

    async def get_approval(self, case_id: CaseId) -> ApprovalRecord | None:
        self._validate_case_id(case_id)
        response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": self._partition_key},
                "SK": {"S": f"APPROVAL#{case_id.value}"},
            },
            ConsistentRead=True,
        )
        item = cast(Mapping[str, Any] | None, response.get("Item"))
        if item is None:
            return None
        return ApprovalRecord(
            case_id=CaseId(self._environment, self._string(item, "case_id")),
            revision=Revision(self._number(item, "revision")),
            approved_by=self._string(item, "approved_by"),
            approved_at=UtcTimestamp.from_value(self._string(item, "approved_at")),
        )

    async def create_decision(
        self,
        record: DecisionRecord,
        *,
        retention_expires_at: UtcTimestamp,
    ) -> DecisionCreateResult:
        self._validate_decision(record)
        self._validate_expiry(retention_expires_at)
        fingerprint = self._decision_fingerprint(record)
        guard_source = (
            f"{record.case_id.value}\x1f{record.po_id}\x1f{record.po_write_date}"
        )
        guard_digest = hashlib.sha256(guard_source.encode()).hexdigest()
        idempotency_digest = hashlib.sha256(record.idempotency_key.encode()).hexdigest()
        decision_item = self._decision_item(
            record,
            retention_expires_at=retention_expires_at,
            fingerprint=fingerprint,
        )
        request = {
            "TransactItems": [
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": decision_item,
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": {
                            "PK": {"S": self._partition_key},
                            "SK": {"S": f"DECISION_GUARD#{guard_digest}"},
                            "entity_type": {"S": "decision_guard"},
                            "decision_id": {"S": record.decision_id.value},
                            "ttl": self._ttl(retention_expires_at),
                        },
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
                {
                    "Put": {
                        "TableName": self._table_name,
                        "Item": {
                            "PK": {"S": self._partition_key},
                            "SK": {
                                "S": f"DECISION_IDEMPOTENCY#{idempotency_digest}"
                            },
                            "entity_type": {"S": "decision_idempotency"},
                            "decision_id": {"S": record.decision_id.value},
                            "fingerprint": {"S": fingerprint},
                            "ttl": self._ttl(retention_expires_at),
                        },
                        "ConditionExpression": _CONDITIONAL_CREATE,
                    }
                },
            ]
        }
        try:
            self._client.transact_write_items(**request)
        except ClientError as error:
            if self._error_code(error) != "TransactionCanceledException":
                raise
            existing = await self.get_decision(record.decision_id)
            if existing == record:
                return DecisionCreateResult(record=existing, created=False)
            raise DecisionConflictError(
                "Another manager decision already won."
            ) from None
        return DecisionCreateResult(record=record, created=True)

    async def get_decision(self, decision_id: DecisionId) -> DecisionRecord | None:
        if (
            not isinstance(decision_id, DecisionId)
            or decision_id.environment is not self._environment
        ):
            raise ValueError("decision belongs to another environment")
        response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": self._partition_key},
                "SK": {"S": f"DECISION#{decision_id.value}"},
            },
            ConsistentRead=True,
        )
        item = cast(Mapping[str, Any] | None, response.get("Item"))
        return self._decision_from_item(item) if item is not None else None

    async def append_audit(
        self,
        event: AuditEvent,
        *,
        expires_at: UtcTimestamp,
    ) -> None:
        if event.environment is not self._environment:
            raise ValueError("audit event belongs to another environment")
        self._validate_expiry(expires_at)
        item = {
            "PK": {"S": self._partition_key},
            "SK": {
                "S": (
                    f"AUDIT#{event.case_id.value}#"
                    f"{event.occurred_at.value.isoformat()}#{event.event_id}"
                )
            },
            "entity_type": {"S": "audit"},
            "event_id": {"S": event.event_id},
            "case_id": {"S": event.case_id.value},
            "event_type": {"S": event.event_type},
            "actor_id": {"S": event.actor_id},
            "occurred_at": {"S": event.occurred_at.value.isoformat()},
            "correlation_id": {"S": event.correlation_id},
            "source_revision": {"N": str(event.source_revision.value)},
            "outcome": {"S": event.outcome},
            "ttl": self._ttl(expires_at),
        }
        if event.evidence_digest is not None:
            item["evidence_digest"] = {"S": event.evidence_digest}
        if event.preferences is not None:
            item["preferences"] = {
                "S": json.dumps(
                    event.preferences.to_dict(),
                    separators=(",", ":"),
                    sort_keys=True,
                )
            }
        if event.decision_id is not None:
            item["decision_id"] = {"S": event.decision_id}
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression=_CONDITIONAL_CREATE,
            )
        except ClientError as error:
            if self._error_code(error) == "ConditionalCheckFailedException":
                raise ImmutableRecordError("The audit event already exists.") from None
            raise

    async def list_audit(
        self,
        case_id: CaseId,
        *,
        limit: int,
    ) -> tuple[AuditEvent, ...]:
        self._validate_case_id(case_id)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("audit limit must be between 1 and 100")
        response = self._client.query(
            TableName=self._table_name,
            KeyConditionExpression="PK = :environment AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":environment": {"S": self._partition_key},
                ":prefix": {"S": f"AUDIT#{case_id.value}#"},
            },
            ScanIndexForward=True,
            Limit=limit,
        )
        items = cast(list[Mapping[str, Any]], response.get("Items", []))
        return tuple(self._audit_from_item(item) for item in items)

    async def put_login_transaction(self, record: LoginTransactionRecord) -> None:
        self._validate_digest(record.transaction_id_hash, name="login transaction")
        item = {
            **self._auth_key("LOGIN", record.transaction_id_hash),
            "entity_type": {"S": "login_transaction"},
            "transaction_id_hash": {"S": record.transaction_id_hash},
            "state_hash": {"S": record.state_hash},
            "nonce": {"S": record.nonce},
            "code_verifier": {"S": record.code_verifier},
            "expires_at": {"S": record.expires_at.value.isoformat()},
            "ttl": self._ttl(record.expires_at),
        }
        await self._conditional_auth_put(item, duplicate="login transaction")

    async def consume_login_transaction(
        self,
        transaction_id_hash: str,
    ) -> LoginTransactionRecord | None:
        self._validate_digest(transaction_id_hash, name="login transaction")
        response = self._client.delete_item(
            TableName=self._table_name,
            Key=self._auth_key("LOGIN", transaction_id_hash),
            ReturnValues="ALL_OLD",
        )
        item = cast(Mapping[str, Any] | None, response.get("Attributes"))
        if item is None:
            return None
        return LoginTransactionRecord(
            transaction_id_hash=self._string(item, "transaction_id_hash"),
            state_hash=self._string(item, "state_hash"),
            nonce=self._string(item, "nonce"),
            code_verifier=self._string(item, "code_verifier"),
            expires_at=UtcTimestamp.from_value(self._string(item, "expires_at")),
        )

    async def put_session(self, record: SessionRecord) -> None:
        self._validate_digest(record.session_id_hash, name="session")
        self._validate_digest(record.csrf_token_hash, name="CSRF token")
        item = {
            **self._auth_key("SESSION", record.session_id_hash),
            "entity_type": {"S": "session"},
            "session_id_hash": {"S": record.session_id_hash},
            "user_id": {"S": record.user_id},
            "email": {"S": record.email},
            "role": {"S": record.role},
            "csrf_token_hash": {"S": record.csrf_token_hash},
            "created_at": {"S": record.created_at.value.isoformat()},
            "expires_at": {"S": record.expires_at.value.isoformat()},
            "ttl": self._ttl(record.expires_at),
        }
        await self._conditional_auth_put(item, duplicate="session")

    async def get_session(self, session_id_hash: str) -> SessionRecord | None:
        self._validate_digest(session_id_hash, name="session")
        response = self._client.get_item(
            TableName=self._table_name,
            Key=self._auth_key("SESSION", session_id_hash),
            ConsistentRead=True,
        )
        item = cast(Mapping[str, Any] | None, response.get("Item"))
        if item is None:
            return None
        return SessionRecord(
            session_id_hash=self._string(item, "session_id_hash"),
            user_id=self._string(item, "user_id"),
            email=self._string(item, "email"),
            role=self._string(item, "role"),
            csrf_token_hash=self._string(item, "csrf_token_hash"),
            created_at=UtcTimestamp.from_value(self._string(item, "created_at")),
            expires_at=UtcTimestamp.from_value(self._string(item, "expires_at")),
        )

    async def delete_session(self, session_id_hash: str) -> None:
        self._validate_digest(session_id_hash, name="session")
        self._client.delete_item(
            TableName=self._table_name,
            Key=self._auth_key("SESSION", session_id_hash),
        )

    async def _conditional_auth_put(
        self,
        item: Mapping[str, Any],
        *,
        duplicate: str,
    ) -> None:
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression=_CONDITIONAL_CREATE,
            )
        except ClientError as error:
            if self._error_code(error) == "ConditionalCheckFailedException":
                raise ImmutableRecordError(f"The {duplicate} already exists.") from None
            raise

    async def _resolve_idempotent_create(
        self,
        record: CaseRecord,
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> CaseCreateResult:
        marker_response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": self._partition_key},
                "SK": {"S": f"IDEMPOTENCY#{idempotency_key}"},
            },
            ConsistentRead=True,
        )
        marker = cast(Mapping[str, Any] | None, marker_response.get("Item"))
        if (
            marker is None
            or self._string(marker, "case_id") != record.case_id.value
            or self._string(marker, "fingerprint") != fingerprint
        ):
            raise IdempotencyConflictError(
                "The idempotency key belongs to another request."
            )
        existing = await self.get_case(record.case_id)
        if existing is None:
            raise IdempotencyConflictError("The idempotent case is unavailable.")
        return CaseCreateResult(record=existing, created=False)

    async def _resolve_idempotent_scan_create(
        self,
        record: ScanRecord,
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> ScanCreateResult:
        marker_response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "PK": {"S": self._partition_key},
                "SK": {"S": f"IDEMPOTENCY#{idempotency_key}"},
            },
            ConsistentRead=True,
        )
        marker = cast(Mapping[str, Any] | None, marker_response.get("Item"))
        if (
            marker is None
            or self._string(marker, "scan_id") != record.scan_id.value
            or self._string(marker, "fingerprint") != fingerprint
        ):
            raise IdempotencyConflictError(
                "The idempotency key belongs to another request."
            )
        existing = await self.get_scan(record.scan_id)
        if existing is None:
            raise IdempotencyConflictError("The idempotent scan is unavailable.")
        return ScanCreateResult(record=existing, created=False)

    @staticmethod
    def _decision_fingerprint(record: DecisionRecord) -> str:
        payload: dict[str, object] = {
            "decision_id": record.decision_id.value,
            "decision_type": record.decision_type.value,
            "case_id": record.case_id.value,
            "manager_subject": record.manager_subject,
            "manager_role": record.manager_role,
            "case_revision": record.case_revision.value,
            "po_id": record.po_id,
            "po_write_date": record.po_write_date,
            "po_state": record.po_state,
            "partner_id": record.partner_id,
            "currency_id": record.currency_id,
            "amount_total": format(record.amount_total, "f"),
            "evidence_digest": record.evidence_digest,
            "idempotency_key": record.idempotency_key,
            "decided_at": record.decided_at.value.isoformat(),
        }
        if isinstance(record, ManagerApprovalRecord):
            payload.update(
                {
                    "offer_id": record.offer_id,
                    "vendor_id": record.vendor_id,
                    "quantity": format(record.quantity, "f"),
                    "unit_price": format(record.unit_price, "f"),
                    "currency": record.currency,
                    "normalized_cost": format(record.normalized_cost, "f"),
                    "budget_status": record.budget_status,
                    "budget_amount": format(record.budget_amount, "f"),
                    "confirmed_commitment": format(
                        record.confirmed_commitment, "f"
                    ),
                    "remaining_before": format(record.remaining_before, "f"),
                    "remaining_after": format(record.remaining_after, "f"),
                    "overage": format(record.overage, "f"),
                    "exception_required": record.exception_required,
                    "budget_exception": record.budget_exception,
                    "justification": (
                        record.justification.value
                        if record.justification is not None
                        else None
                    ),
                    "authorization_expires_at": record.expires_at.value.isoformat(),
                }
            )
        else:
            payload["reason"] = record.reason.value
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _decision_item(
        self,
        record: DecisionRecord,
        *,
        retention_expires_at: UtcTimestamp,
        fingerprint: str,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"DECISION#{record.decision_id.value}"},
            "entity_type": {"S": "decision"},
            "decision_id": {"S": record.decision_id.value},
            "decision_type": {"S": record.decision_type.value},
            "case_id": {"S": record.case_id.value},
            "manager_subject": {"S": record.manager_subject},
            "manager_role": {"S": record.manager_role},
            "case_revision": {"N": str(record.case_revision.value)},
            "po_id": {"N": str(record.po_id)},
            "po_write_date": {"S": record.po_write_date},
            "po_state": {"S": record.po_state},
            "partner_id": {"N": str(record.partner_id)},
            "currency_id": {"N": str(record.currency_id)},
            "amount_total": {"S": format(record.amount_total, "f")},
            "evidence_digest": {"S": record.evidence_digest},
            "idempotency_key": {"S": record.idempotency_key},
            "decided_at": {"S": record.decided_at.value.isoformat()},
            "fingerprint": {"S": fingerprint},
            "ttl": self._ttl(retention_expires_at),
        }
        if isinstance(record, ManagerApprovalRecord):
            item.update(
                {
                    "offer_id": {"S": record.offer_id},
                    "vendor_id": {"S": record.vendor_id},
                    "quantity": {"S": format(record.quantity, "f")},
                    "unit_price": {"S": format(record.unit_price, "f")},
                    "currency": {"S": record.currency},
                    "normalized_cost": {"S": format(record.normalized_cost, "f")},
                    "budget_status": {"S": record.budget_status},
                    "budget_amount": {"S": format(record.budget_amount, "f")},
                    "confirmed_commitment": {
                        "S": format(record.confirmed_commitment, "f")
                    },
                    "remaining_before": {
                        "S": format(record.remaining_before, "f")
                    },
                    "remaining_after": {"S": format(record.remaining_after, "f")},
                    "overage": {"S": format(record.overage, "f")},
                    "exception_required": {"BOOL": record.exception_required},
                    "budget_exception": {"BOOL": record.budget_exception},
                    "authorization_expires_at": {
                        "S": record.expires_at.value.isoformat()
                    },
                }
            )
            if record.justification is not None:
                item["justification"] = {"S": record.justification.value}
        else:
            item["reason"] = {"S": record.reason.value}
        return item

    def _decision_from_item(self, item: Mapping[str, Any]) -> DecisionRecord:
        decision_id = DecisionId(
            self._environment, self._string(item, "decision_id")
        )
        case_id = CaseId(self._environment, self._string(item, "case_id"))
        manager_subject = self._string(item, "manager_subject")
        manager_role = self._string(item, "manager_role")
        case_revision = Revision(self._number(item, "case_revision"))
        po_id = self._number(item, "po_id")
        po_write_date = self._string(item, "po_write_date")
        po_state = self._string(item, "po_state")
        partner_id = self._number(item, "partner_id")
        currency_id = self._number(item, "currency_id")
        amount_total = Decimal(self._string(item, "amount_total"))
        evidence_digest = self._string(item, "evidence_digest")
        idempotency_key = self._string(item, "idempotency_key")
        decided_at = UtcTimestamp.from_value(self._string(item, "decided_at"))
        decision_type = DecisionType(self._string(item, "decision_type"))
        if decision_type is DecisionType.REJECT:
            return RejectionRecord(
                decision_id=decision_id,
                case_id=case_id,
                manager_subject=manager_subject,
                manager_role=manager_role,
                case_revision=case_revision,
                po_id=po_id,
                po_write_date=po_write_date,
                po_state=po_state,
                partner_id=partner_id,
                currency_id=currency_id,
                amount_total=amount_total,
                reason=DecisionText(self._string(item, "reason")),
                evidence_digest=evidence_digest,
                idempotency_key=idempotency_key,
                decided_at=decided_at,
            )
        justification = (
            DecisionText(self._string(item, "justification"))
            if "justification" in item
            else None
        )
        return ManagerApprovalRecord(
            decision_id=decision_id,
            case_id=case_id,
            manager_subject=manager_subject,
            manager_role=manager_role,
            case_revision=case_revision,
            po_id=po_id,
            po_write_date=po_write_date,
            po_state=po_state,
            partner_id=partner_id,
            currency_id=currency_id,
            amount_total=amount_total,
            offer_id=self._string(item, "offer_id"),
            vendor_id=self._string(item, "vendor_id"),
            quantity=Decimal(self._string(item, "quantity")),
            unit_price=Decimal(self._string(item, "unit_price")),
            currency=self._string(item, "currency"),
            normalized_cost=Decimal(self._string(item, "normalized_cost")),
            budget_status=self._string(item, "budget_status"),
            budget_amount=Decimal(self._string(item, "budget_amount")),
            confirmed_commitment=Decimal(
                self._string(item, "confirmed_commitment")
            ),
            remaining_before=Decimal(self._string(item, "remaining_before")),
            remaining_after=Decimal(self._string(item, "remaining_after")),
            overage=Decimal(self._string(item, "overage")),
            exception_required=bool(item["exception_required"]["BOOL"]),
            budget_exception=bool(item["budget_exception"]["BOOL"]),
            justification=justification,
            evidence_digest=evidence_digest,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            expires_at=UtcTimestamp.from_value(
                self._string(item, "authorization_expires_at")
            ),
        )

    def _audit_from_item(self, item: Mapping[str, Any]) -> AuditEvent:
        preferences: AppliedPreferences | None = None
        if "preferences" in item:
            raw = json.loads(self._string(item, "preferences"))
            profile_keys = {
                "profile_id",
                "company_id",
                "category_id",
                "product_id",
                "scope",
                "scope_id",
                "revision",
                "ordered_criteria",
                "max_price_premium_percent",
                "enforcement_mode",
                "precedence_source",
            }
            profile = preference_from_dict(
                {key: raw[key] for key in profile_keys}
            )
            preferences = AppliedPreferences(
                profile=profile,
                cheapest_eligible_cost=Decimal(raw["cheapest_eligible_cost"]),
                offer_results=tuple(
                    OfferPremiumResult(
                        offer_id=value["offer_id"],
                        premium_percent=Decimal(value["premium_percent"]),
                        exceeds_cap=value["exceeds_cap"],
                        outcome=value["outcome"],
                    )
                    for value in raw["offer_results"]
                ),
            )
        return AuditEvent(
            event_id=self._string(item, "event_id"),
            case_id=CaseId(self._environment, self._string(item, "case_id")),
            event_type=self._string(item, "event_type"),
            actor_id=self._string(item, "actor_id"),
            occurred_at=UtcTimestamp.from_value(self._string(item, "occurred_at")),
            correlation_id=self._string(item, "correlation_id"),
            source_revision=Revision(self._number(item, "source_revision")),
            outcome=self._string(item, "outcome"),
            evidence_digest=(
                self._string(item, "evidence_digest")
                if "evidence_digest" in item
                else None
            ),
            preferences=preferences,
            decision_id=(
                self._string(item, "decision_id")
                if "decision_id" in item
                else None
            ),
        )

    def _scan_item(
        self,
        record: ScanRecord,
        *,
        expires_at: UtcTimestamp,
    ) -> dict[str, Any]:
        return {
            **self._scan_key(record.scan_id),
            **self._scan_attributes(record, expires_at=expires_at),
        }

    def _scan_attributes(
        self,
        record: ScanRecord,
        *,
        expires_at: UtcTimestamp,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "entity_type": {"S": "scan"},
            "scan_id": {"S": record.scan_id.value},
            "revision": {"N": str(record.revision.value)},
            "status": {"S": record.status},
            "trigger": {"S": record.trigger},
            "created_at": {"S": record.created_at.value.isoformat()},
            "updated_at": {"S": record.updated_at.value.isoformat()},
            "ttl": self._ttl(expires_at),
        }
        for name, timestamp in (
            ("started_at", record.started_at),
            ("completed_at", record.completed_at),
        ):
            if timestamp is not None:
                values[name] = {"S": timestamp.value.isoformat()}
        values["case_summaries"] = {
            "L": [
                {
                    "M": {
                        "case_id": {"S": summary.case_id},
                        "product_id": {"S": summary.product_id},
                        "product_name": {"S": summary.product_name},
                        "outcome": {"S": summary.outcome},
                        "scan_id": {"S": summary.scan_id},
                        "budget_status": {"S": summary.budget_status},
                        "status": {"S": summary.status},
                        **(
                            {"amount": {"S": format(summary.amount, "f")}}
                            if summary.amount is not None
                            else {}
                        ),
                        **(
                            {"need_by_date": {"S": summary.need_by_date.isoformat()}}
                            if summary.need_by_date is not None
                            else {}
                        ),
                        **(
                            {
                                "completed_at": {
                                    "S": summary.completed_at.value.isoformat()
                                }
                            }
                            if summary.completed_at is not None
                            else {}
                        ),
                    }
                }
                for summary in record.case_summaries
            ]
        }
        if record.error is not None:
            values["error"] = {
                "M": {
                    "error_code": {"S": record.error.error_code},
                    "message": {"S": record.error.message},
                    "retryable": {"BOOL": record.error.retryable},
                    "retry_count": {"N": str(record.error.retry_count)},
                }
            }
        return values

    def _scan_from_item(self, item: Mapping[str, Any]) -> ScanRecord:
        if not item:
            raise ValueError("DynamoDB returned an empty scan")
        summaries = []
        for entry in item.get("case_summaries", {}).get("L", []):
            summary_item = cast(Mapping[str, Any], entry["M"])
            summaries.append(
                CaseSummary(
                    case_id=self._string(summary_item, "case_id"),
                    product_id=self._string(summary_item, "product_id"),
                    product_name=self._string(summary_item, "product_name"),
                    outcome=self._string(summary_item, "outcome"),
                    amount=(
                        Decimal(self._string(summary_item, "amount"))
                        if "amount" in summary_item
                        else None
                    ),
                    need_by_date=(
                        date.fromisoformat(self._string(summary_item, "need_by_date"))
                        if "need_by_date" in summary_item
                        else None
                    ),
                    scan_id=self._string(summary_item, "scan_id"),
                    budget_status=self._string(summary_item, "budget_status"),
                    completed_at=self._optional_timestamp(summary_item, "completed_at"),
                    status=(
                        self._string(summary_item, "status")
                        if "status" in summary_item
                        else "succeeded"
                    ),
                )
            )
        error_item = cast(Mapping[str, Any] | None, item.get("error", {}).get("M"))
        return ScanRecord(
            scan_id=ScanId(self._environment, self._string(item, "scan_id")),
            revision=Revision(self._number(item, "revision")),
            status=self._string(item, "status"),
            trigger=self._string(item, "trigger"),
            created_at=UtcTimestamp.from_value(self._string(item, "created_at")),
            updated_at=UtcTimestamp.from_value(self._string(item, "updated_at")),
            started_at=self._optional_timestamp(item, "started_at"),
            completed_at=self._optional_timestamp(item, "completed_at"),
            case_summaries=tuple(summaries),
            error=(
                FailureRecord(
                    error_code=self._string(error_item, "error_code"),
                    message=self._string(error_item, "message"),
                    retryable=bool(error_item["retryable"]["BOOL"]),
                    retry_count=self._number(error_item, "retry_count"),
                )
                if error_item is not None
                else None
            ),
        )

    def _case_item(
        self,
        record: CaseRecord,
        *,
        expires_at: UtcTimestamp,
    ) -> dict[str, Any]:
        return {
            **self._case_key(record.case_id),
            **self._case_attributes(record, expires_at=expires_at),
        }

    def _case_attributes(
        self,
        record: CaseRecord,
        *,
        expires_at: UtcTimestamp,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "entity_type": {"S": "case"},
            "case_id": {"S": record.case_id.value},
            "revision": {"N": str(record.revision.value)},
            "status": {"S": record.status},
            "trigger": {"S": record.trigger},
            "created_at": {"S": record.created_at.value.isoformat()},
            "updated_at": {"S": record.updated_at.value.isoformat()},
            "ttl": self._ttl(expires_at),
        }
        for name, timestamp in (
            ("started_at", record.started_at),
            ("completed_at", record.completed_at),
        ):
            if timestamp is not None:
                values[name] = {"S": timestamp.value.isoformat()}
        if record.result is not None:
            result_values: dict[str, Any] = {
                "outcome": {"S": record.result.outcome},
                "rationale": {"S": record.result.rationale},
                "risk_flags": {"L": [{"S": flag} for flag in record.result.risk_flags]},
                "trade_offs": {"L": [{"S": item} for item in record.result.trade_offs]},
                "uncertainty": {"S": record.result.uncertainty},
                "evidence_limitations": {
                    "L": [{"S": item} for item in record.result.evidence_limitations]
                },
                "budget_status": {"S": record.result.budget_status},
                "priority_order": {
                    "L": [{"S": item} for item in record.result.priority_order]
                },
            }
            for name in (
                "product_id",
                "product_name",
                "offer_id",
                "evidence_digest",
                "preference_profile_id",
                "preference_scope",
                "premium_outcome",
            ):
                value = getattr(record.result, name)
                if value is not None:
                    result_values[name] = {"S": value}
            for name in ("quantity", "unit_price", "normalized_cost"):
                value = getattr(record.result, name)
                if value is not None:
                    result_values[name] = {"S": format(value, "f")}
            if record.result.preference_revision is not None:
                result_values["preference_revision"] = {
                    "N": str(record.result.preference_revision)
                }
            if record.result.evidence is not None:
                result_values["evidence"] = {
                    "S": record.result.evidence.canonical_json().decode("utf-8")
                }
            values["result"] = {"M": result_values}
        if record.evidence:
            values["evidence"] = {
                "L": [
                    {"S": item.canonical_json().decode("utf-8")}
                    for item in record.evidence
                ]
            }
        if record.error is not None:
            values["error"] = {
                "M": {
                    "error_code": {"S": record.error.error_code},
                    "message": {"S": record.error.message},
                    "retryable": {"BOOL": record.error.retryable},
                    "retry_count": {"N": str(record.error.retry_count)},
                }
            }
        if record.candidate_snapshot is not None:
            snapshot = record.candidate_snapshot
            values["candidate_snapshot"] = {
                "M": {
                    "category_id": {"S": snapshot.category_id},
                    "reorder_minimum": {"S": format(snapshot.reorder_minimum, "f")},
                    "reorder_maximum": {"S": format(snapshot.reorder_maximum, "f")},
                    "projected_quantity": {
                        "S": format(snapshot.projected_quantity, "f")
                    },
                    "projected_trigger_date": {
                        "S": snapshot.projected_trigger_date.isoformat()
                    },
                }
            }
        values["refinement_count"] = {"N": str(record.refinement_count)}
        if record.workflow_thread_id is not None:
            self._validate_key(record.workflow_thread_id, name="workflow thread ID")
            values["workflow_thread_id"] = {"S": record.workflow_thread_id}
        if record.draft is not None:
            draft = record.draft
            values["draft"] = {
                "M": {
                    "po_id": {"N": str(draft.po_id)},
                    "write_date": {"S": draft.write_date},
                    "state": {"S": draft.state},
                    "partner_id": {"N": str(draft.partner_id)},
                    "currency_id": {"N": str(draft.currency_id)},
                    "amount_total": {"S": format(draft.amount_total, "f")},
                }
            }
        return values

    def _case_from_item(self, item: Mapping[str, Any]) -> CaseRecord:
        if not item:
            raise ValueError("DynamoDB returned an empty case")
        result_item = cast(Mapping[str, Any] | None, item.get("result", {}).get("M"))
        error_item = cast(Mapping[str, Any] | None, item.get("error", {}).get("M"))
        snapshot_item = cast(
            Mapping[str, Any] | None, item.get("candidate_snapshot", {}).get("M")
        )
        draft_item = cast(Mapping[str, Any] | None, item.get("draft", {}).get("M"))
        return CaseRecord(
            case_id=CaseId(self._environment, self._string(item, "case_id")),
            revision=Revision(self._number(item, "revision")),
            status=self._string(item, "status"),
            trigger=self._string(item, "trigger"),
            created_at=UtcTimestamp.from_value(self._string(item, "created_at")),
            updated_at=UtcTimestamp.from_value(self._string(item, "updated_at")),
            started_at=self._optional_timestamp(item, "started_at"),
            completed_at=self._optional_timestamp(item, "completed_at"),
            evidence=tuple(
                procurement_evidence_from_dict(json.loads(entry["S"]))
                for entry in item.get("evidence", {}).get("L", [])
            ),
            result=(
                RecommendationRecord(
                    product_id=(
                        self._string(result_item, "product_id")
                        if "product_id" in result_item
                        else None
                    ),
                    product_name=(
                        self._string(result_item, "product_name")
                        if "product_name" in result_item
                        else None
                    ),
                    rationale=self._string(result_item, "rationale"),
                    risk_flags=tuple(
                        cast(Mapping[str, str], entry)["S"]
                        for entry in result_item["risk_flags"]["L"]
                    ),
                    evidence=(
                        procurement_evidence_from_dict(
                            json.loads(self._string(result_item, "evidence"))
                        )
                        if "evidence" in result_item
                        else None
                    ),
                    outcome=(
                        self._string(result_item, "outcome")
                        if "outcome" in result_item
                        else "approval_ready"
                    ),
                    offer_id=(
                        self._string(result_item, "offer_id")
                        if "offer_id" in result_item
                        else None
                    ),
                    trade_offs=tuple(
                        cast(Mapping[str, str], entry)["S"]
                        for entry in result_item.get("trade_offs", {}).get("L", [])
                    ),
                    uncertainty=(
                        self._string(result_item, "uncertainty")
                        if "uncertainty" in result_item
                        else "No additional uncertainty identified."
                    ),
                    evidence_limitations=tuple(
                        cast(Mapping[str, str], entry)["S"]
                        for entry in result_item.get("evidence_limitations", {}).get(
                            "L", []
                        )
                    ),
                    evidence_digest=(
                        self._string(result_item, "evidence_digest")
                        if "evidence_digest" in result_item
                        else None
                    ),
                    quantity=(
                        Decimal(self._string(result_item, "quantity"))
                        if "quantity" in result_item
                        else None
                    ),
                    unit_price=(
                        Decimal(self._string(result_item, "unit_price"))
                        if "unit_price" in result_item
                        else None
                    ),
                    normalized_cost=(
                        Decimal(self._string(result_item, "normalized_cost"))
                        if "normalized_cost" in result_item
                        else None
                    ),
                    budget_status=(
                        self._string(result_item, "budget_status")
                        if "budget_status" in result_item
                        else "not_evaluated"
                    ),
                    preference_profile_id=(
                        self._string(result_item, "preference_profile_id")
                        if "preference_profile_id" in result_item
                        else None
                    ),
                    preference_scope=(
                        self._string(result_item, "preference_scope")
                        if "preference_scope" in result_item
                        else None
                    ),
                    preference_revision=(
                        self._number(result_item, "preference_revision")
                        if "preference_revision" in result_item
                        else None
                    ),
                    priority_order=tuple(
                        cast(Mapping[str, str], entry)["S"]
                        for entry in result_item.get("priority_order", {}).get("L", [])
                    ),
                    premium_outcome=(
                        self._string(result_item, "premium_outcome")
                        if "premium_outcome" in result_item
                        else None
                    ),
                )
                if result_item is not None
                else None
            ),
            error=(
                FailureRecord(
                    error_code=self._string(error_item, "error_code"),
                    message=self._string(error_item, "message"),
                    retryable=bool(error_item["retryable"]["BOOL"]),
                    retry_count=self._number(error_item, "retry_count"),
                )
                if error_item is not None
                else None
            ),
            candidate_snapshot=(
                CandidateSnapshot(
                    category_id=self._string(snapshot_item, "category_id"),
                    reorder_minimum=Decimal(
                        self._string(snapshot_item, "reorder_minimum")
                    ),
                    reorder_maximum=Decimal(
                        self._string(snapshot_item, "reorder_maximum")
                    ),
                    projected_quantity=Decimal(
                        self._string(snapshot_item, "projected_quantity")
                    ),
                    projected_trigger_date=date.fromisoformat(
                        self._string(snapshot_item, "projected_trigger_date")
                    ),
                )
                if snapshot_item is not None
                else None
            ),
            refinement_count=(
                self._number(item, "refinement_count")
                if "refinement_count" in item
                else 0
            ),
            workflow_thread_id=(
                self._string(item, "workflow_thread_id")
                if "workflow_thread_id" in item
                else None
            ),
            draft=(
                DraftRecord(
                    po_id=self._number(draft_item, "po_id"),
                    write_date=self._string(draft_item, "write_date"),
                    state=self._string(draft_item, "state"),
                    partner_id=self._number(draft_item, "partner_id"),
                    currency_id=self._number(draft_item, "currency_id"),
                    amount_total=Decimal(self._string(draft_item, "amount_total")),
                )
                if draft_item is not None
                else None
            ),
        )

    def _case_key(self, case_id: CaseId) -> dict[str, Any]:
        return {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"CASE#{case_id.value}"},
        }

    def _scan_key(self, scan_id: ScanId) -> dict[str, Any]:
        return {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"SCAN#{scan_id.value}"},
        }

    def _auth_key(self, prefix: str, digest: str) -> dict[str, Any]:
        return {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"{prefix}#{digest}"},
        }

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

    def _validate_decision(self, record: DecisionRecord) -> None:
        if record.case_id.environment is not self._environment:
            raise ValueError("decision belongs to another environment")

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

    @staticmethod
    def _validate_key(value: str, *, name: str) -> None:
        if _SAFE_KEY_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{name} must be bounded safe identifier text")

    @staticmethod
    def _validate_digest(value: str, *, name: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", value, re.ASCII) is None:
            raise ValueError(f"{name} digest is invalid")

    @staticmethod
    def _ttl(expires_at: UtcTimestamp) -> dict[str, str]:
        return {"N": str(int(expires_at.value.timestamp()))}

    @staticmethod
    def _error_code(error: ClientError) -> str:
        return str(error.response.get("Error", {}).get("Code", ""))

    @staticmethod
    def _string(item: Mapping[str, Any], name: str) -> str:
        return str(item[name]["S"])

    @staticmethod
    def _number(item: Mapping[str, Any], name: str) -> int:
        return int(item[name]["N"])

    @staticmethod
    def _optional_timestamp(
        item: Mapping[str, Any],
        name: str,
    ) -> UtcTimestamp | None:
        value = item.get(name)
        return UtcTimestamp.from_value(str(value["S"])) if value is not None else None

    def _encode_cursor(self, key: Mapping[str, Any]) -> str:
        payload = {
            "PK": self._string(key, "PK"),
            "SK": self._string(key, "SK"),
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(encoded).decode().rstrip("=")

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
            if (
                not isinstance(payload, dict)
                or payload.get("PK") != self._partition_key
                or not isinstance(payload.get("SK"), str)
                or not payload["SK"].startswith("CASE#")
            ):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("case cursor is invalid") from error
        return {"PK": {"S": payload["PK"]}, "SK": {"S": payload["SK"]}}

    def _decode_scan_cursor(self, cursor: str) -> dict[str, Any]:
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
            if (
                not isinstance(payload, dict)
                or payload.get("PK") != self._partition_key
                or not isinstance(payload.get("SK"), str)
                or not payload["SK"].startswith("SCAN#")
            ):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("scan cursor is invalid") from error
        return {"PK": {"S": payload["PK"]}, "SK": {"S": payload["SK"]}}
