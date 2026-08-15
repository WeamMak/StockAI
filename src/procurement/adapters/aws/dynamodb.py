"""Low-level DynamoDB application repository with conditional writes."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from procurement.domain.audit import AuditEvent
from procurement.domain.identifiers import CaseId, Environment, Revision
from procurement.domain.models import UtcTimestamp
from procurement.domain.policy.evidence import procurement_evidence_from_dict
from procurement.ports.repositories import (
    ApplicationRepository,
    ApprovalRecord,
    CaseCreateResult,
    CasePage,
    CaseRecord,
    FailureRecord,
    IdempotencyConflictError,
    ImmutableRecordError,
    LoginTransactionRecord,
    RecommendationRecord,
    RevisionConflictError,
    SessionRecord,
)

MAX_PAGE_SIZE = 100
_SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_CONDITIONAL_CREATE = "attribute_not_exists(PK) AND attribute_not_exists(SK)"


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
        names = {f"#{name}": name for name in values}
        expression_values = {f":{name}": value for name, value in values.items()}
        expression_values[":expected_revision"] = {"N": str(expected_revision.value)}
        request = {
            "TableName": self._table_name,
            "Key": self._case_key(record.case_id),
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
    ) -> CasePage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("case page limit must be between 1 and 100")
        request: dict[str, Any] = {
            "TableName": self._table_name,
            "KeyConditionExpression": (
                "PK = :environment AND begins_with(SK, :case_prefix)"
            ),
            "ExpressionAttributeValues": {
                ":environment": {"S": self._partition_key},
                ":case_prefix": {"S": "CASE#"},
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
                "product_id": {"S": record.result.product_id},
                "product_name": {"S": record.result.product_name},
                "rationale": {"S": record.result.rationale},
                "risk_flags": {"L": [{"S": flag} for flag in record.result.risk_flags]},
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
        return values

    def _case_from_item(self, item: Mapping[str, Any]) -> CaseRecord:
        if not item:
            raise ValueError("DynamoDB returned an empty case")
        result_item = cast(Mapping[str, Any] | None, item.get("result", {}).get("M"))
        error_item = cast(Mapping[str, Any] | None, item.get("error", {}).get("M"))
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
                    product_id=self._string(result_item, "product_id"),
                    product_name=self._string(result_item, "product_name"),
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
        )

    def _case_key(self, case_id: CaseId) -> dict[str, Any]:
        return {
            "PK": {"S": self._partition_key},
            "SK": {"S": f"CASE#{case_id.value}"},
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
