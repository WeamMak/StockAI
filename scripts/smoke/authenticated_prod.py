"""Obtain fresh Cognito cookies in memory and run the exact prod smoke."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from tests.smoke.test_dev_skeleton import run_exact_walking_skeleton

from procurement.bootstrap.cognito import (
    CognitoSmokeUserSettings,
    bootstrap_smoke_user,
    create_cognito_admin_client,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COOKIE_ENV_NAMES = (
    "STOCKAI_PROD_SESSION_TOKEN",
    "STOCKAI_PROD_CSRF_TOKEN",
)
CREDENTIAL_ENV_NAMES = (
    "STOCKAI_PROD_COGNITO_USER_POOL_ID",
    "STOCKAI_PROD_SMOKE_USERNAME",
    "STOCKAI_PROD_SMOKE_EMAIL",
    "STOCKAI_PROD_SMOKE_PASSWORD",
)


class _LoginControl(Protocol):
    def fill(self, value: str) -> None: ...

    def click(self) -> None: ...


class _LoginPage(Protocol):
    def locator(self, selector: str) -> _LoginControl: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedProdSettings:
    """Validated protected inputs for one production smoke login."""

    base_url: str
    user_pool_id: str
    username: str
    email: str
    password: str = field(repr=False)
    region: str = "us-east-1"

    def __post_init__(self) -> None:
        url = httpx.URL(self.base_url)
        if (
            url.scheme != "https"
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Production smoke base URL must be an HTTPS origin")
        CognitoSmokeUserSettings(
            user_pool_id=self.user_pool_id,
            username=self.username,
            email=self.email,
            password=self.password,
        )

    @classmethod
    def from_environment(cls) -> AuthenticatedProdSettings:
        values = {name: os.environ.get(name, "") for name in CREDENTIAL_ENV_NAMES}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"Missing protected production smoke input: {missing[0]}")
        return cls(
            base_url=os.environ.get(
                "STOCKAI_PROD_BASE_URL",
                "https://app.prod.stockai.fursa.click",
            ).rstrip("/"),
            user_pool_id=values["STOCKAI_PROD_COGNITO_USER_POOL_ID"],
            username=values["STOCKAI_PROD_SMOKE_USERNAME"],
            email=values["STOCKAI_PROD_SMOKE_EMAIL"],
            password=values["STOCKAI_PROD_SMOKE_PASSWORD"],
            region=os.environ.get("PROCUREMENT_AWS_REGION", "us-east-1"),
        )


def _extract_auth_cookies(cookies: Sequence[Mapping[str, object]]) -> tuple[str, str]:
    values = {
        str(cookie.get("name")): str(cookie.get("value", ""))
        for cookie in cookies
        if cookie.get("name") in {"stockai_session", "stockai_csrf"}
    }
    session = values.get("stockai_session", "")
    csrf = values.get("stockai_csrf", "")
    if not session or not csrf:
        raise RuntimeError("Managed login did not return the required StockAI cookies")
    return session, csrf


def _bootstrap_user(settings: AuthenticatedProdSettings) -> None:
    bootstrap_smoke_user(
        CognitoSmokeUserSettings(
            user_pool_id=settings.user_pool_id,
            username=settings.username,
            email=settings.email,
            password=settings.password,
        ),
        client=create_cognito_admin_client(region=settings.region),
    )


def _submit_cognito_login(
    page: _LoginPage,
    settings: AuthenticatedProdSettings,
) -> None:
    page.locator('input[name="username"]:visible').fill(settings.username)
    page.locator('input[name="password"]:visible').fill(settings.password)
    page.locator('input[name="signInSubmitButton"]:visible').click()


def _browser_login(settings: AuthenticatedProdSettings) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(
                f"{settings.base_url}/auth/login",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            _submit_cognito_login(page, settings)
            page.wait_for_url(
                f"{settings.base_url}/",
                wait_until="networkidle",
                timeout=30_000,
            )
            return _extract_auth_cookies(context.cookies([settings.base_url]))
        finally:
            browser.close()


def run_authenticated_prod_smoke(
    settings: AuthenticatedProdSettings | None = None,
    *,
    bootstrap_user: Callable[[AuthenticatedProdSettings], None] | None = None,
    login: Callable[[AuthenticatedProdSettings], tuple[str, str]] | None = None,
    smoke_runner: Callable[[str], None] | None = None,
) -> None:
    """Run prod smoke with fresh cookies and erase credential environment."""

    resolved = settings or AuthenticatedProdSettings.from_environment()
    bootstrap_action = bootstrap_user or _bootstrap_user
    login_action = login or _browser_login
    runner = smoke_runner or run_exact_walking_skeleton
    try:
        bootstrap_action(resolved)
        session, csrf = login_action(resolved)
        os.environ["STOCKAI_PROD_SESSION_TOKEN"] = session
        os.environ["STOCKAI_PROD_CSRF_TOKEN"] = csrf
        os.environ["STOCKAI_RUN_PROD_SMOKE"] = "1"
        run_id = os.environ.setdefault(
            "STOCKAI_SMOKE_RUN_ID",
            f"prod-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        )
        os.environ.setdefault(
            "STOCKAI_SMOKE_EVIDENCE",
            str(PROJECT_ROOT / "reports" / "smoke" / f"{run_id}.json"),
        )
        runner("prod")
    finally:
        for name in (*COOKIE_ENV_NAMES, *CREDENTIAL_ENV_NAMES):
            os.environ.pop(name, None)


if __name__ == "__main__":
    run_authenticated_prod_smoke()
