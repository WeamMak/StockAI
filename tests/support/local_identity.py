"""Test-only identity provider; application settings cannot select it."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse

if TYPE_CHECKING:
    import httpx
    from httpx2 import AsyncClient

from procurement.api.auth.cognito import AuthenticatedIdentity
from procurement.api.auth.session import CSRF_COOKIE_NAME, UserRole


class LocalIdentityProvider:
    """Deterministic test identity behind the production provider protocol."""

    def __init__(self, *, role: UserRole = UserRole.OFFICER) -> None:
        self.role = role

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        del nonce, code_challenge
        return "/auth/callback?" + urlencode(
            {
                "code": "fictional-code",
                "state": state,
            }
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> AuthenticatedIdentity:
        del code_verifier
        del expected_nonce
        if code != "fictional-code":
            raise AssertionError("test identity received an unexpected code")
        return AuthenticatedIdentity(
            user_id=f"local-{self.role.value}-001",
            email=f"{self.role.value}@example.invalid",
            role=self.role.value,
        )


async def sign_in(client: AsyncClient) -> dict[str, str]:
    """Complete the public login flow and return the required CSRF header."""

    started = await client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    completed = await client.get(
        "/auth/callback",
        params={"code": "fictional-code", "state": state},
        follow_redirects=False,
    )
    assert completed.status_code == 303
    csrf_token = client.cookies[CSRF_COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token}


def sign_in_sync(client: httpx.Client) -> dict[str, str]:
    """Authenticate an HTTP test client while retaining Secure-cookie semantics."""

    started = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    login_token = started.cookies["stockai_login"]
    completed = client.get(
        "/auth/callback",
        params={"code": "fictional-code", "state": state},
        headers={"Cookie": f"stockai_login={login_token}"},
        follow_redirects=False,
    )
    if completed.status_code != 303:
        raise AssertionError("test identity callback was rejected")
    session_token = completed.cookies["stockai_session"]
    csrf_token = completed.cookies[CSRF_COOKIE_NAME]
    return {
        "Cookie": (f"stockai_session={session_token}; {CSRF_COOKIE_NAME}={csrf_token}"),
        "X-CSRF-Token": csrf_token,
    }
