"""Opaque login transaction and application-session behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider

from procurement.api.app import create_app
from procurement.api.auth.cognito import AuthenticatedIdentity
from procurement.api.auth.session import AuthenticationService, UserRole
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.identifiers import Environment
from procurement.domain.models import UtcTimestamp
from procurement.ports.repositories import InMemoryApplicationRepository


class RecordingIdentityProvider:
    """Deterministic identity provider at the approved Cognito seam."""

    def __init__(self) -> None:
        self.exchanges: list[dict[str, str]] = []

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        return (
            "https://stockai-dev.auth.us-east-1.amazoncognito.com/oauth2/authorize"
            f"?state={state}&nonce={nonce}&code_challenge={code_challenge}"
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> AuthenticatedIdentity:
        self.exchanges.append(
            {
                "code": code,
                "code_verifier": code_verifier,
                "expected_nonce": expected_nonce,
            }
        )
        return AuthenticatedIdentity(
            user_id="cognito-user-001",
            email="officer@example.invalid",
            role=UserRole.OFFICER.value,
        )


@pytest.mark.anyio
async def test_authorization_code_login_creates_only_an_opaque_browser_session() -> (
    None
):
    now = datetime(2026, 8, 9, 12, tzinfo=UTC)
    provider = RecordingIdentityProvider()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    service = AuthenticationService(
        repository=repository,
        identity_provider=provider,
        now=lambda: now,
    )

    started = await service.begin_login()
    parameters = parse_qs(urlparse(started.authorization_url).query)
    completed = await service.complete_login(
        login_token=started.login_token,
        state=parameters["state"][0],
        code="fictional-authorization-code",
    )
    principal = await service.authenticate(completed.session_token)

    assert completed.session_token not in started.authorization_url
    assert "fictional-authorization-code" not in completed.session_token
    assert principal.user_id == "cognito-user-001"
    assert principal.email == "officer@example.invalid"
    assert principal.role is UserRole.OFFICER
    assert principal.expires_at == UtcTimestamp(now + timedelta(hours=8))
    assert provider.exchanges == [
        {
            "code": "fictional-authorization-code",
            "code_verifier": provider.exchanges[0]["code_verifier"],
            "expected_nonce": parameters["nonce"][0],
        }
    ]
    assert len(provider.exchanges[0]["code_verifier"]) >= 43


@pytest.mark.anyio
async def test_login_state_is_one_use_even_when_the_first_callback_is_invalid() -> None:
    provider = RecordingIdentityProvider()
    service = AuthenticationService(
        repository=InMemoryApplicationRepository(environment=Environment.DEV),
        identity_provider=provider,
    )
    started = await service.begin_login()
    state = parse_qs(urlparse(started.authorization_url).query)["state"][0]

    with pytest.raises(DomainError) as invalid:
        await service.complete_login(
            login_token=started.login_token,
            state="wrong-state",
            code="fictional-authorization-code",
        )
    with pytest.raises(DomainError) as replayed:
        await service.complete_login(
            login_token=started.login_token,
            state=state,
            code="fictional-authorization-code",
        )

    assert invalid.value.error_code is ErrorCode.AUTH_REQUIRED
    assert replayed.value.error_code is ErrorCode.AUTH_REQUIRED
    assert provider.exchanges == []


@pytest.mark.anyio
async def test_secure_cookie_session_endpoint_and_logout_are_token_free() -> None:
    application = create_app(identity_provider=LocalIdentityProvider())
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        started = await client.get("/auth/login", follow_redirects=False)
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        completed = await client.get(
            "/auth/callback",
            params={"code": "fictional-code", "state": state},
            follow_redirects=False,
        )
        session = await client.get("/api/v1/session")
        csrf_headers = {
            "X-CSRF-Token": client.cookies["stockai_csrf"],
        }
        logged_out = await client.post("/auth/logout", headers=csrf_headers)
        revoked = await client.get("/api/v1/session")

    login_cookie = started.headers.get_list("set-cookie")[0]
    callback_cookies = completed.headers.get_list("set-cookie")
    session_cookie = next(
        cookie for cookie in callback_cookies if cookie.startswith("stockai_session=")
    )
    csrf_cookie = next(
        cookie for cookie in callback_cookies if cookie.startswith("stockai_csrf=")
    )
    assert "Secure" in login_cookie and "HttpOnly" in login_cookie
    assert "SameSite=lax" in login_cookie
    assert "Secure" in session_cookie and "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Secure" in csrf_cookie and "HttpOnly" not in csrf_cookie
    assert "SameSite=strict" in csrf_cookie
    assert session.status_code == 200
    assert session.json() == {
        "user_id": "local-officer-001",
        "email": "officer@example.invalid",
        "role": "officer",
    }
    assert "token" not in session.text.lower()
    assert logged_out.status_code == 204
    assert revoked.status_code == 401


@pytest.mark.anyio
async def test_session_rotation_revokes_the_old_cookie_and_expiry_revokes_the_new() -> (
    None
):
    current_time = [datetime(2026, 8, 9, 12, tzinfo=UTC)]
    service = AuthenticationService(
        repository=InMemoryApplicationRepository(environment=Environment.DEV),
        identity_provider=RecordingIdentityProvider(),
        now=lambda: current_time[0],
    )

    first_start = await service.begin_login()
    first_state = parse_qs(urlparse(first_start.authorization_url).query)["state"][0]
    first = await service.complete_login(
        login_token=first_start.login_token,
        state=first_state,
        code="fictional-authorization-code",
    )
    second_start = await service.begin_login()
    second_state = parse_qs(urlparse(second_start.authorization_url).query)["state"][0]
    second = await service.complete_login(
        login_token=second_start.login_token,
        state=second_state,
        code="fictional-authorization-code",
        current_session_token=first.session_token,
    )

    with pytest.raises(DomainError) as rotated:
        await service.authenticate(first.session_token)
    assert rotated.value.error_code is ErrorCode.AUTH_REQUIRED
    assert (await service.authenticate(second.session_token)).role is UserRole.OFFICER

    current_time[0] += timedelta(hours=8)
    with pytest.raises(DomainError) as expired:
        await service.authenticate(second.session_token)
    assert expired.value.error_code is ErrorCode.AUTH_REQUIRED


@pytest.mark.anyio
async def test_provider_callback_error_returns_only_the_safe_auth_envelope() -> None:
    application = create_app(identity_provider=LocalIdentityProvider())
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        response = await client.get(
            "/auth/callback",
            params={"error": "private-provider-error"},
        )

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_REQUIRED"
    assert "private-provider-error" not in response.text
