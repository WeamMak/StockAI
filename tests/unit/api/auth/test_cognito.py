"""Cognito authorization-code and verified-role behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from procurement.api.auth.cognito import (
    CognitoIdentityProvider,
    CognitoSettings,
)
from procurement.api.auth.session import UserRole
from procurement.domain.errors import DomainError, ErrorCode


class RecordingTokenEndpoint:
    def __init__(self) -> None:
        self.requests: list[dict[str, str | None]] = []

    async def exchange(
        self,
        *,
        settings: CognitoSettings,
        code: str,
        code_verifier: str,
    ) -> str:
        self.requests.append(
            {
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": settings.redirect_uri,
            }
        )
        return "signed-id-token"


class StaticVerifier:
    def __init__(self, claims: Mapping[str, Any]) -> None:
        self.claims = claims
        self.tokens: list[str] = []

    async def verify(self, token: str) -> Mapping[str, Any]:
        self.tokens.append(token)
        return self.claims


def _settings() -> CognitoSettings:
    return CognitoSettings(
        domain_url="https://stockai-dev.auth.us-east-1.amazoncognito.com",
        region="us-east-1",
        user_pool_id="us-east-1_fictional",
        client_id="fictional-client-id",
        client_secret="fictional-client-secret-at-least-32-characters",
        redirect_uri="https://dev.stockai.example.invalid/auth/callback",
    )


@pytest.mark.anyio
async def test_cognito_uses_code_pkce_nonce_and_verified_manager_group() -> None:
    endpoint = RecordingTokenEndpoint()
    verifier = StaticVerifier(
        {
            "sub": "manager-user-001",
            "email": "manager@example.invalid",
            "nonce": "expected-nonce",
            "cognito:groups": ["stockai-procurement-manager"],
        }
    )
    provider = CognitoIdentityProvider(
        settings=_settings(),
        token_endpoint=endpoint,
        verifier=verifier,
    )

    authorization_url = provider.authorization_url(
        state="opaque-state",
        nonce="expected-nonce",
        code_challenge="pkce-challenge",
    )
    identity = await provider.exchange_code(
        code="authorization-code",
        code_verifier="pkce-verifier",
        expected_nonce="expected-nonce",
    )

    parsed = urlparse(authorization_url)
    assert parsed.path == "/oauth2/authorize"
    assert parse_qs(parsed.query) == {
        "response_type": ["code"],
        "client_id": ["fictional-client-id"],
        "redirect_uri": ["https://dev.stockai.example.invalid/auth/callback"],
        "scope": ["openid email profile"],
        "state": ["opaque-state"],
        "nonce": ["expected-nonce"],
        "code_challenge_method": ["S256"],
        "code_challenge": ["pkce-challenge"],
    }
    assert identity.user_id == "manager-user-001"
    assert identity.role == UserRole.MANAGER.value
    assert endpoint.requests == [
        {
            "client_id": "fictional-client-id",
            "client_secret": "fictional-client-secret-at-least-32-characters",
            "code": "authorization-code",
            "code_verifier": "pkce-verifier",
            "redirect_uri": "https://dev.stockai.example.invalid/auth/callback",
        }
    ]
    assert verifier.tokens == ["signed-id-token"]


@pytest.mark.anyio
async def test_cognito_rejects_a_signed_identity_with_the_wrong_nonce() -> None:
    provider = CognitoIdentityProvider(
        settings=_settings(),
        token_endpoint=RecordingTokenEndpoint(),
        verifier=StaticVerifier(
            {
                "sub": "officer-user-001",
                "email": "officer@example.invalid",
                "nonce": "replayed-nonce",
                "cognito:groups": ["stockai-procurement-officer"],
            }
        ),
    )

    with pytest.raises(DomainError) as rejected:
        await provider.exchange_code(
            code="authorization-code",
            code_verifier="pkce-verifier",
            expected_nonce="expected-nonce",
        )

    assert rejected.value.error_code is ErrorCode.AUTH_REQUIRED
    assert "replayed-nonce" not in rejected.value.safe_message
