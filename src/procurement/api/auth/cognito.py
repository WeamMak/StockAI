"""Cognito authorization-code adapter and verified identity boundary."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import anyio
import httpx
import jwt

from procurement.domain.errors import DomainError, ErrorCode

OFFICER_GROUP = "stockai-procurement-officer"
MANAGER_GROUP = "stockai-procurement-manager"
_MAX_AUTH_VALUE_LENGTH = 2048
_MAX_TOKEN_LENGTH = 16_384


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    """Verified identity facts accepted from the configured provider."""

    user_id: str
    email: str
    role: str


class IdentityProvider(Protocol):
    """Small authorization-code boundary used by the session service."""

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Build the provider authorization URL."""

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> AuthenticatedIdentity:
        """Exchange a code and return only verified identity facts."""


@dataclass(frozen=True, slots=True)
class CognitoSettings:
    """Validated Cognito relying-party configuration."""

    domain_url: str
    region: str
    user_pool_id: str
    client_id: str
    redirect_uri: str
    client_secret: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        domain = httpx.URL(self.domain_url)
        redirect = httpx.URL(self.redirect_uri)
        if (
            domain.scheme != "https"
            or domain.host is None
            or domain.path not in {"", "/"}
        ):
            raise ValueError("Cognito domain must be an HTTPS origin")
        if redirect.scheme != "https" or redirect.host is None or redirect.fragment:
            raise ValueError("Cognito redirect URI must be an absolute HTTPS URL")
        for name, value in (
            ("region", self.region),
            ("user pool ID", self.user_pool_id),
            ("client ID", self.client_id),
        ):
            if not value or len(value) > 256 or not value.isascii():
                raise ValueError(f"Cognito {name} is invalid")
        if self.client_secret is not None and not 16 <= len(self.client_secret) <= 256:
            raise ValueError("Cognito client secret is invalid")
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError("Cognito timeout must be between 0 and 30 seconds")

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


class TokenEndpoint(Protocol):
    """Bounded code exchange used by the Cognito adapter."""

    async def exchange(
        self,
        *,
        settings: CognitoSettings,
        code: str,
        code_verifier: str,
    ) -> str:
        """Return only the ID token from a successful code exchange."""


class IdTokenVerifier(Protocol):
    """Signature and registered-claim verifier."""

    async def verify(self, token: str) -> Mapping[str, Any]:
        """Return trusted claims or raise a safe authentication error."""


class HttpxTokenEndpoint:
    """HTTPS implementation of Cognito's OAuth token endpoint."""

    async def exchange(
        self,
        *,
        settings: CognitoSettings,
        code: str,
        code_verifier: str,
    ) -> str:
        data = {
            "grant_type": "authorization_code",
            "client_id": settings.client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": settings.redirect_uri,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
                token_url = f"{settings.domain_url.rstrip('/')}/oauth2/token"
                if settings.client_secret is None:
                    response = await client.post(
                        token_url,
                        data=data,
                        headers={"Accept": "application/json"},
                    )
                else:
                    response = await client.post(
                        token_url,
                        data=data,
                        auth=httpx.BasicAuth(
                            settings.client_id,
                            settings.client_secret,
                        ),
                        headers={"Accept": "application/json"},
                    )
            response.raise_for_status()
            payload = response.json()
            id_token = payload.get("id_token") if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError) as error:
            raise _invalid_sign_in() from error
        if not isinstance(id_token, str) or not 1 <= len(id_token) <= _MAX_TOKEN_LENGTH:
            raise _invalid_sign_in()
        return id_token


class PyJwtIdTokenVerifier:
    """Validate Cognito ID-token signatures and registered claims."""

    def __init__(self, settings: CognitoSettings) -> None:
        self._settings = settings
        self._jwks = jwt.PyJWKClient(
            settings.jwks_url,
            cache_keys=True,
            lifespan=300,
            timeout=settings.timeout_seconds,
        )

    async def verify(self, token: str) -> Mapping[str, Any]:
        try:
            signing_key = await anyio.to_thread.run_sync(
                self._jwks.get_signing_key_from_jwt,
                token,
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.client_id,
                issuer=self._settings.issuer,
                options={"require": ["aud", "exp", "iat", "iss", "sub", "token_use"]},
            )
        except (jwt.PyJWTError, OSError, ValueError) as error:
            raise _invalid_sign_in() from error
        if claims.get("token_use") != "id":
            raise _invalid_sign_in()
        return cast(Mapping[str, Any], claims)


class CognitoIdentityProvider:
    """Cognito adapter that exposes only verified, role-bounded identity facts."""

    def __init__(
        self,
        *,
        settings: CognitoSettings,
        token_endpoint: TokenEndpoint | None = None,
        verifier: IdTokenVerifier | None = None,
    ) -> None:
        self._settings = settings
        self._token_endpoint = token_endpoint or HttpxTokenEndpoint()
        self._verifier = verifier or PyJwtIdTokenVerifier(settings)

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        parameters = {
            "response_type": "code",
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
        }
        return (
            f"{self._settings.domain_url.rstrip('/')}/oauth2/authorize?"
            f"{urlencode(parameters)}"
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> AuthenticatedIdentity:
        for value in (code, code_verifier, expected_nonce):
            if not value or len(value) > _MAX_AUTH_VALUE_LENGTH or not value.isascii():
                raise _invalid_sign_in()
        id_token = await self._token_endpoint.exchange(
            settings=self._settings,
            code=code,
            code_verifier=code_verifier,
        )
        claims = await self._verifier.verify(id_token)
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(
            nonce,
            expected_nonce,
        ):
            raise _invalid_sign_in()
        groups = claims.get("cognito:groups")
        group_values = (
            {value for value in groups if isinstance(value, str)}
            if isinstance(groups, list)
            else set()
        )
        if MANAGER_GROUP in group_values:
            role = "manager"
        elif OFFICER_GROUP in group_values:
            role = "officer"
        else:
            raise DomainError(
                error_code=ErrorCode.FORBIDDEN,
                safe_message="The signed-in user has no authorized procurement role.",
            )
        subject = claims.get("sub")
        email = claims.get("email")
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 256
            or not isinstance(email, str)
            or not 3 <= len(email) <= 320
        ):
            raise _invalid_sign_in()
        return AuthenticatedIdentity(user_id=subject, email=email, role=role)


class UnavailableIdentityProvider:
    """Safe default that never grants a local bypass identity."""

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        del state, nonce, code_challenge
        raise DomainError(
            error_code=ErrorCode.AUTH_REQUIRED,
            safe_message="Cognito sign-in is not configured.",
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> AuthenticatedIdentity:
        del code, code_verifier, expected_nonce
        raise DomainError(
            error_code=ErrorCode.AUTH_REQUIRED,
            safe_message="Cognito sign-in is not configured.",
        )


def _invalid_sign_in() -> DomainError:
    return DomainError(
        error_code=ErrorCode.AUTH_REQUIRED,
        safe_message="The Cognito sign-in could not be validated.",
    )
