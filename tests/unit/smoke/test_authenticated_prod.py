"""Protected production smoke authentication wrapper."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.smoke.authenticated_prod import (
    AuthenticatedProdSettings,
    _extract_auth_cookies,
    run_authenticated_prod_smoke,
)


def _settings() -> AuthenticatedProdSettings:
    return AuthenticatedProdSettings(
        base_url="https://app.prod.stockai.fursa.click",
        user_pool_id="us-east-1_fictional",
        username="prod-smoke-officer",
        email="prod-smoke@example.invalid",
        password="Smoke-Fictional-Password-42!",
    )


def test_extract_auth_cookies_requires_both_nonempty_values() -> None:
    assert _extract_auth_cookies(
        [
            {"name": "stockai_session", "value": "session-value"},
            {"name": "stockai_csrf", "value": "csrf-value"},
            {"name": "unrelated", "value": "ignored"},
        ]
    ) == ("session-value", "csrf-value")

    with pytest.raises(RuntimeError, match="required StockAI cookies"):
        _extract_auth_cookies([{"name": "stockai_session", "value": "only-one"}])


def test_wrapper_passes_cookies_only_in_process_and_clears_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings()
    events: list[str] = []
    monkeypatch.setenv("STOCKAI_SMOKE_RUN_ID", "prod-smoke-unit")
    monkeypatch.setenv("STOCKAI_SMOKE_EVIDENCE", str(tmp_path / "evidence.json"))

    def smoke_runner(environment: str) -> None:
        assert environment == "prod"
        assert os.environ["STOCKAI_PROD_SESSION_TOKEN"] == "session-value"
        assert os.environ["STOCKAI_PROD_CSRF_TOKEN"] == "csrf-value"
        events.append("smoke")

    run_authenticated_prod_smoke(
        settings,
        bootstrap_user=lambda _settings: events.append("bootstrap"),
        login=lambda _settings: ("session-value", "csrf-value"),
        smoke_runner=smoke_runner,
    )

    assert events == ["bootstrap", "smoke"]
    assert "STOCKAI_PROD_SESSION_TOKEN" not in os.environ
    assert "STOCKAI_PROD_CSRF_TOKEN" not in os.environ
    assert settings.password not in repr(settings)


def test_authentication_failure_prevents_smoke_and_leaves_no_cookie_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke_called = False

    def fail_login(_settings: AuthenticatedProdSettings) -> tuple[str, str]:
        raise RuntimeError("managed login failed")

    def smoke_runner(_environment: str) -> None:
        nonlocal smoke_called
        smoke_called = True

    with pytest.raises(RuntimeError, match="managed login failed"):
        run_authenticated_prod_smoke(
            _settings(),
            bootstrap_user=lambda _settings: None,
            login=fail_login,
            smoke_runner=smoke_runner,
        )

    assert smoke_called is False
    assert "STOCKAI_PROD_SESSION_TOKEN" not in os.environ
    assert "STOCKAI_PROD_CSRF_TOKEN" not in os.environ
