"""Finite idempotent bootstrap for fictional Cognito users and groups."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from procurement.api.auth.cognito import MANAGER_GROUP, OFFICER_GROUP


class CognitoAdminClient(Protocol):
    """Administrative operations used by the finite bootstrap command."""

    def describe_user_pool(self, **request: Any) -> Mapping[str, Any]: ...

    def get_group(self, **request: Any) -> Mapping[str, Any]: ...

    def create_group(self, **request: Any) -> Mapping[str, Any]: ...

    def admin_get_user(self, **request: Any) -> Mapping[str, Any]: ...

    def admin_create_user(self, **request: Any) -> Mapping[str, Any]: ...

    def admin_add_user_to_group(self, **request: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CognitoBootstrapSettings:
    """Fictional identities supplied without logging credential material."""

    user_pool_id: str
    officer_username: str
    officer_email: str
    manager_username: str
    manager_email: str
    officer_temporary_password: str | None = field(default=None, repr=False)
    manager_temporary_password: str | None = field(default=None, repr=False)
    region: str = "us-east-1"

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("user pool ID", self.user_pool_id, 256),
            ("officer username", self.officer_username, 128),
            ("manager username", self.manager_username, 128),
            ("officer email", self.officer_email, 320),
            ("manager email", self.manager_email, 320),
            ("region", self.region, 64),
        ):
            if not value or len(value) > limit or not value.isascii():
                raise ValueError(f"Cognito bootstrap {name} is invalid")
        for password in (
            self.officer_temporary_password,
            self.manager_temporary_password,
        ):
            if password is not None and not 12 <= len(password) <= 256:
                raise ValueError("Cognito temporary password is invalid")

    @classmethod
    def from_environment(cls) -> CognitoBootstrapSettings:
        values = os.environ
        required = {
            "STOCKAI_COGNITO_USER_POOL_ID": values.get("STOCKAI_COGNITO_USER_POOL_ID"),
            "STOCKAI_COGNITO_OFFICER_USERNAME": values.get(
                "STOCKAI_COGNITO_OFFICER_USERNAME"
            ),
            "STOCKAI_COGNITO_OFFICER_EMAIL": values.get(
                "STOCKAI_COGNITO_OFFICER_EMAIL"
            ),
            "STOCKAI_COGNITO_MANAGER_USERNAME": values.get(
                "STOCKAI_COGNITO_MANAGER_USERNAME"
            ),
            "STOCKAI_COGNITO_MANAGER_EMAIL": values.get(
                "STOCKAI_COGNITO_MANAGER_EMAIL"
            ),
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"Missing Cognito bootstrap setting: {missing[0]}")
        return cls(
            user_pool_id=cast(str, required["STOCKAI_COGNITO_USER_POOL_ID"]),
            officer_username=cast(str, required["STOCKAI_COGNITO_OFFICER_USERNAME"]),
            officer_email=cast(str, required["STOCKAI_COGNITO_OFFICER_EMAIL"]),
            officer_temporary_password=values.get(
                "STOCKAI_COGNITO_OFFICER_TEMPORARY_PASSWORD"
            )
            or None,
            manager_username=cast(str, required["STOCKAI_COGNITO_MANAGER_USERNAME"]),
            manager_email=cast(str, required["STOCKAI_COGNITO_MANAGER_EMAIL"]),
            manager_temporary_password=values.get(
                "STOCKAI_COGNITO_MANAGER_TEMPORARY_PASSWORD"
            )
            or None,
            region=values.get("PROCUREMENT_AWS_REGION", "us-east-1"),
        )


def create_cognito_admin_client(*, region: str) -> CognitoAdminClient:
    client = boto3.client(
        "cognito-idp",
        region_name=region,
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"mode": "standard", "max_attempts": 3},
        ),
    )
    return cast(CognitoAdminClient, client)


def bootstrap_users(
    settings: CognitoBootstrapSettings,
    *,
    client: CognitoAdminClient,
) -> None:
    """Create exact fictional identities without returning or printing secrets."""

    pool = client.describe_user_pool(UserPoolId=settings.user_pool_id).get(
        "UserPool",
        {},
    )
    admin_config = (
        pool.get("AdminCreateUserConfig", {}) if isinstance(pool, Mapping) else {}
    )
    if not isinstance(admin_config, Mapping) or not admin_config.get(
        "AllowAdminCreateUserOnly"
    ):
        raise RuntimeError("Cognito self-service signup must be disabled")

    for group in (OFFICER_GROUP, MANAGER_GROUP):
        _ensure_group(client, user_pool_id=settings.user_pool_id, group=group)
    _ensure_user(
        client,
        user_pool_id=settings.user_pool_id,
        username=settings.officer_username,
        email=settings.officer_email,
        temporary_password=settings.officer_temporary_password,
    )
    _ensure_user(
        client,
        user_pool_id=settings.user_pool_id,
        username=settings.manager_username,
        email=settings.manager_email,
        temporary_password=settings.manager_temporary_password,
    )
    client.admin_add_user_to_group(
        UserPoolId=settings.user_pool_id,
        Username=settings.officer_username,
        GroupName=OFFICER_GROUP,
    )
    client.admin_add_user_to_group(
        UserPoolId=settings.user_pool_id,
        Username=settings.manager_username,
        GroupName=MANAGER_GROUP,
    )


def _ensure_group(
    client: CognitoAdminClient,
    *,
    user_pool_id: str,
    group: str,
) -> None:
    try:
        client.get_group(UserPoolId=user_pool_id, GroupName=group)
    except ClientError as error:
        if _error_code(error) != "ResourceNotFoundException":
            raise
        client.create_group(
            UserPoolId=user_pool_id,
            GroupName=group,
            Description=f"StockAI {group.removeprefix('stockai-procurement-')} role",
        )


def _ensure_user(
    client: CognitoAdminClient,
    *,
    user_pool_id: str,
    username: str,
    email: str,
    temporary_password: str | None,
) -> None:
    try:
        client.admin_get_user(UserPoolId=user_pool_id, Username=username)
        return
    except ClientError as error:
        if _error_code(error) != "UserNotFoundException":
            raise
    if temporary_password is None:
        raise ValueError("A temporary password is required to create a Cognito user")
    client.admin_create_user(
        UserPoolId=user_pool_id,
        Username=username,
        TemporaryPassword=temporary_password,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def run() -> None:
    settings = CognitoBootstrapSettings.from_environment()
    bootstrap_users(
        settings,
        client=create_cognito_admin_client(region=settings.region),
    )
