"""Cognito redirect, opaque session, and logout routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from procurement.api.auth.rbac import (
    CsrfDep,
    CurrentPrincipalDep,
    authentication_service_from,
)
from procurement.api.auth.session import (
    CSRF_COOKIE_NAME,
    LOGIN_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_TTL,
)
from procurement.domain.errors import DomainError, ErrorCode

router = APIRouter(tags=["authentication"])
_COOKIE_PATH = "/"


class SessionResponse(BaseModel):
    """Public current-user view without provider or session tokens."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    role: str


@router.get("/auth/login")
async def login(request: Request) -> RedirectResponse:
    started = await authentication_service_from(request).begin_login()
    response = RedirectResponse(
        started.authorization_url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    response.set_cookie(
        LOGIN_COOKIE_NAME,
        started.login_token,
        max_age=600,
        secure=True,
        httponly=True,
        samesite="lax",
        path=_COOKIE_PATH,
    )
    return response


@router.get("/auth/callback")
async def callback(
    request: Request,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
) -> RedirectResponse:
    login_token = request.cookies.get(LOGIN_COOKIE_NAME)
    if error is not None or code is None or state is None or login_token is None:
        raise DomainError(
            error_code=ErrorCode.AUTH_REQUIRED,
            safe_message="The Cognito callback could not be validated.",
        )
    completed = await authentication_service_from(request).complete_login(
        login_token=login_token,
        state=state,
        code=code,
        current_session_token=request.cookies.get(SESSION_COOKIE_NAME),
    )
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(LOGIN_COOKIE_NAME, path=_COOKIE_PATH)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        completed.session_token,
        max_age=int(SESSION_TTL.total_seconds()),
        secure=True,
        httponly=True,
        samesite="lax",
        path=_COOKIE_PATH,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        completed.csrf_token,
        max_age=int(SESSION_TTL.total_seconds()),
        secure=True,
        httponly=False,
        samesite="strict",
        path=_COOKIE_PATH,
    )
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    _principal: CurrentPrincipalDep,
    _csrf: CsrfDep,
) -> None:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token is not None:
        await authentication_service_from(request).logout(session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path=_COOKIE_PATH)
    response.delete_cookie(CSRF_COOKIE_NAME, path=_COOKIE_PATH)


@router.get("/api/v1/session")
async def current_session(principal: CurrentPrincipalDep) -> SessionResponse:
    return SessionResponse(
        user_id=principal.user_id,
        email=principal.email,
        role=principal.role.value,
    )
