"""Reusable FastAPI authentication and role dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from procurement.api.auth.csrf import validate_csrf
from procurement.api.auth.session import (
    SESSION_COOKIE_NAME,
    AuthenticationService,
    SessionPrincipal,
    UserRole,
)
from procurement.domain.errors import DomainError, ErrorCode


def authentication_service_from(request: Request) -> AuthenticationService:
    service = request.app.state.authentication_service
    if not isinstance(service, AuthenticationService):  # pragma: no cover
        raise RuntimeError("authentication service is not configured")
    return service


async def current_principal(request: Request) -> SessionPrincipal:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token is None or not 1 <= len(session_token) <= 256:
        raise DomainError(
            error_code=ErrorCode.AUTH_REQUIRED,
            safe_message="Authentication is required.",
        )
    return await authentication_service_from(request).authenticate(session_token)


CurrentPrincipalDep = Annotated[SessionPrincipal, Depends(current_principal)]


async def require_officer(principal: CurrentPrincipalDep) -> SessionPrincipal:
    if principal.role not in {UserRole.OFFICER, UserRole.MANAGER}:
        raise _forbidden()
    return principal


OfficerPrincipalDep = Annotated[SessionPrincipal, Depends(require_officer)]


async def require_manager(principal: CurrentPrincipalDep) -> SessionPrincipal:
    if principal.role is not UserRole.MANAGER:
        raise _forbidden()
    return principal


ManagerPrincipalDep = Annotated[SessionPrincipal, Depends(require_manager)]


async def require_csrf(
    request: Request,
    principal: CurrentPrincipalDep,
) -> None:
    validate_csrf(request, principal)


CsrfDep = Annotated[None, Depends(require_csrf)]


def _forbidden() -> DomainError:
    return DomainError(
        error_code=ErrorCode.FORBIDDEN,
        safe_message="The current role is not authorized for this action.",
    )
