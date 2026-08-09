"""Opaque browser-login and application-session lifecycle."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from procurement.api.auth.cognito import IdentityProvider
from procurement.domain.errors import DomainError, ErrorCode
from procurement.domain.models import UtcTimestamp
from procurement.ports.repositories import (
    ApplicationRepository,
    LoginTransactionRecord,
    SessionRecord,
)

LOGIN_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(hours=8)
LOGIN_COOKIE_NAME = "stockai_login"
SESSION_COOKIE_NAME = "stockai_session"
CSRF_COOKIE_NAME = "stockai_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"


class UserRole(StrEnum):
    """Human authorities represented by Cognito groups."""

    OFFICER = "officer"
    MANAGER = "manager"


@dataclass(frozen=True, slots=True)
class LoginStart:
    """Opaque login cookie and provider redirect returned to the route."""

    authorization_url: str
    login_token: str
    expires_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class LoginComplete:
    """New application-session material returned only to the cookie route."""

    session_token: str
    csrf_token: str
    principal: SessionPrincipal


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    """Authenticated request identity loaded from server-side state."""

    session_id_hash: str
    user_id: str
    email: str
    role: UserRole
    csrf_token_hash: str
    expires_at: UtcTimestamp


def digest_opaque_token(value: str) -> str:
    """Hash a high-entropy opaque token before repository lookup."""

    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return encoded.decode().rstrip("=")


class AuthenticationService:
    """Coordinate one-time login state and revocable opaque sessions."""

    def __init__(
        self,
        *,
        repository: ApplicationRepository,
        identity_provider: IdentityProvider,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._identity_provider = identity_provider
        self._now = now or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    async def begin_login(self) -> LoginStart:
        """Persist a one-use OAuth transaction and build a PKCE redirect."""

        login_token = self._token_factory()
        state = self._token_factory()
        nonce = self._token_factory()
        code_verifier = self._token_factory() + self._token_factory()
        expires_at = UtcTimestamp(self._now() + LOGIN_TTL)
        authorization_url = self._identity_provider.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=_challenge(code_verifier),
        )
        await self._repository.put_login_transaction(
            LoginTransactionRecord(
                transaction_id_hash=digest_opaque_token(login_token),
                state_hash=digest_opaque_token(state),
                nonce=nonce,
                code_verifier=code_verifier,
                expires_at=expires_at,
            )
        )
        return LoginStart(
            authorization_url=authorization_url,
            login_token=login_token,
            expires_at=expires_at,
        )

    async def complete_login(
        self,
        *,
        login_token: str,
        state: str,
        code: str,
        current_session_token: str | None = None,
    ) -> LoginComplete:
        """Consume OAuth state, verify identity, and rotate the application session."""

        transaction = await self._repository.consume_login_transaction(
            digest_opaque_token(login_token)
        )
        if (
            transaction is None
            or transaction.expires_at.value <= self._now()
            or not secrets.compare_digest(
                transaction.state_hash,
                digest_opaque_token(state),
            )
        ):
            raise DomainError(
                error_code=ErrorCode.AUTH_REQUIRED,
                safe_message="The sign-in request is invalid or expired.",
            )
        identity = await self._identity_provider.exchange_code(
            code=code,
            code_verifier=transaction.code_verifier,
            expected_nonce=transaction.nonce,
        )
        try:
            role = UserRole(identity.role)
        except ValueError as error:
            raise DomainError(
                error_code=ErrorCode.FORBIDDEN,
                safe_message="The signed-in user has no authorized procurement role.",
            ) from error

        if current_session_token:
            await self._repository.delete_session(
                digest_opaque_token(current_session_token)
            )
        session_token = self._token_factory()
        csrf_token = self._token_factory()
        expires_at = UtcTimestamp(self._now() + SESSION_TTL)
        record = SessionRecord(
            session_id_hash=digest_opaque_token(session_token),
            user_id=identity.user_id,
            email=identity.email,
            role=role.value,
            csrf_token_hash=digest_opaque_token(csrf_token),
            created_at=UtcTimestamp(self._now()),
            expires_at=expires_at,
        )
        await self._repository.put_session(record)
        return LoginComplete(
            session_token=session_token,
            csrf_token=csrf_token,
            principal=self._principal(record),
        )

    async def authenticate(self, session_token: str) -> SessionPrincipal:
        """Load one unexpired server-side session from an opaque cookie."""

        record = await self._repository.get_session(digest_opaque_token(session_token))
        if record is None:
            raise self._authentication_required()
        if record.expires_at.value <= self._now():
            await self._repository.delete_session(record.session_id_hash)
            raise self._authentication_required()
        return self._principal(record)

    async def logout(self, session_token: str) -> None:
        """Revoke the local session without exposing provider tokens."""

        await self._repository.delete_session(digest_opaque_token(session_token))

    @staticmethod
    def _principal(record: SessionRecord) -> SessionPrincipal:
        return SessionPrincipal(
            session_id_hash=record.session_id_hash,
            user_id=record.user_id,
            email=record.email,
            role=UserRole(record.role),
            csrf_token_hash=record.csrf_token_hash,
            expires_at=record.expires_at,
        )

    @staticmethod
    def _authentication_required() -> DomainError:
        return DomainError(
            error_code=ErrorCode.AUTH_REQUIRED,
            safe_message="Authentication is required.",
        )
