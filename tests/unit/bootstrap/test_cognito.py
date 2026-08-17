"""Idempotent fictional Cognito user and group bootstrap."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from procurement.bootstrap.cognito import (
    CognitoBootstrapSettings,
    CognitoSmokeUserSettings,
    bootstrap_smoke_user,
    bootstrap_users,
)


def _not_found(code: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "private provider detail"}},
        operation,
    )


class StatefulCognitoAdmin:
    """Small stateful fake at the boto3 administrative boundary."""

    def __init__(self) -> None:
        self.groups: set[str] = set()
        self.users: set[str] = set()
        self.create_user_requests: list[dict[str, Any]] = []
        self.password_requests: list[dict[str, Any]] = []
        self.memberships: list[tuple[str, str]] = []
        self.removals: list[tuple[str, str]] = []

    def describe_user_pool(self, **request: Any) -> dict[str, Any]:
        return {
            "UserPool": {
                "Id": request["UserPoolId"],
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
            }
        }

    def get_group(self, **request: Any) -> dict[str, Any]:
        name = str(request["GroupName"])
        if name not in self.groups:
            raise _not_found("ResourceNotFoundException", "GetGroup")
        return {"Group": {"GroupName": name}}

    def create_group(self, **request: Any) -> dict[str, Any]:
        self.groups.add(str(request["GroupName"]))
        return {}

    def admin_get_user(self, **request: Any) -> dict[str, Any]:
        username = str(request["Username"])
        if username not in self.users:
            raise _not_found("UserNotFoundException", "AdminGetUser")
        return {"Username": username}

    def admin_create_user(self, **request: Any) -> dict[str, Any]:
        self.create_user_requests.append(request)
        self.users.add(str(request["Username"]))
        return {}

    def admin_add_user_to_group(self, **request: Any) -> dict[str, Any]:
        self.memberships.append((str(request["Username"]), str(request["GroupName"])))
        return {}

    def admin_set_user_password(self, **request: Any) -> dict[str, Any]:
        self.password_requests.append(request)
        return {}

    def admin_remove_user_from_group(self, **request: Any) -> dict[str, Any]:
        self.removals.append((str(request["Username"]), str(request["GroupName"])))
        return {}


def _settings() -> CognitoBootstrapSettings:
    return CognitoBootstrapSettings(
        user_pool_id="us-east-1_fictional",
        officer_username="fictional-officer",
        officer_email="officer@example.invalid",
        officer_temporary_password="Officer-Fictional-Password-42!",
        manager_username="fictional-manager",
        manager_email="manager@example.invalid",
        manager_temporary_password="Manager-Fictional-Password-42!",
    )


def test_bootstrap_is_idempotent_and_never_emits_temporary_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = StatefulCognitoAdmin()
    settings = _settings()

    bootstrap_users(settings, client=client)
    bootstrap_users(settings, client=client)

    assert client.groups == {
        "stockai-procurement-officer",
        "stockai-procurement-manager",
    }
    assert client.users == {"fictional-officer", "fictional-manager"}
    assert len(client.create_user_requests) == 2
    assert all(
        request["MessageAction"] == "SUPPRESS"
        for request in client.create_user_requests
    )
    assert set(client.memberships) == {
        ("fictional-officer", "stockai-procurement-officer"),
        ("fictional-manager", "stockai-procurement-manager"),
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert settings.officer_temporary_password is not None
    assert settings.manager_temporary_password is not None
    assert settings.officer_temporary_password not in repr(settings)
    assert settings.manager_temporary_password not in repr(settings)


def test_bootstrap_refuses_a_pool_with_self_service_signup() -> None:
    client = StatefulCognitoAdmin()
    client.describe_user_pool = lambda **_request: {  # type: ignore[method-assign]
        "UserPool": {"AdminCreateUserConfig": {"AllowAdminCreateUserOnly": False}}
    }

    with pytest.raises(
        RuntimeError,
        match="Cognito self-service signup must be disabled",
    ):
        bootstrap_users(_settings(), client=client)

    assert client.groups == set()
    assert client.users == set()


def test_smoke_user_bootstrap_is_idempotent_permanent_and_officer_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = StatefulCognitoAdmin()
    settings = CognitoSmokeUserSettings(
        user_pool_id="us-east-1_fictional",
        username="prod-smoke-officer",
        email="prod-smoke@example.invalid",
        password="Smoke-Fictional-Password-42!",
    )

    bootstrap_smoke_user(settings, client=client)
    bootstrap_smoke_user(settings, client=client)

    assert client.users == {"prod-smoke-officer"}
    assert len(client.create_user_requests) == 1
    assert set(client.memberships) == {
        ("prod-smoke-officer", "stockai-procurement-officer")
    }
    assert set(client.removals) == {
        ("prod-smoke-officer", "stockai-procurement-manager")
    }
    assert len(client.password_requests) == 2
    assert all(request["Permanent"] is True for request in client.password_requests)
    assert all(
        request["Password"] == "Smoke-Fictional-Password-42!"
        for request in client.password_requests
    )
    assert settings.password not in repr(settings)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
