"""Session-bound synchronizer-token validation."""

from __future__ import annotations

import secrets

from fastapi import Request

from procurement.api.auth.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SessionPrincipal,
    digest_opaque_token,
)
from procurement.domain.errors import DomainError, ErrorCode

_MAX_CSRF_TOKEN_LENGTH = 256


def validate_csrf(request: Request, principal: SessionPrincipal) -> None:
    """Require matching cookie/header tokens bound to the current session."""

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if (
        cookie_token is None
        or header_token is None
        or not 1 <= len(cookie_token) <= _MAX_CSRF_TOKEN_LENGTH
        or not 1 <= len(header_token) <= _MAX_CSRF_TOKEN_LENGTH
        or not cookie_token.isascii()
        or not header_token.isascii()
        or not secrets.compare_digest(cookie_token, header_token)
        or not secrets.compare_digest(
            digest_opaque_token(header_token),
            principal.csrf_token_hash,
        )
    ):
        raise DomainError(
            error_code=ErrorCode.CSRF_INVALID,
            safe_message="The CSRF token is missing or invalid.",
        )
