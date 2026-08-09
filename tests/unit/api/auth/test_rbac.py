"""Role protection at the public FastAPI boundary."""

from __future__ import annotations

import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in

from procurement.api.app import create_app
from procurement.api.auth.session import UserRole


@pytest.mark.anyio
async def test_scan_routes_require_an_officer_or_manager_session() -> None:
    application = create_app(identity_provider=LocalIdentityProvider())
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        unauthenticated = await client.get("/api/v1/scans")
        await sign_in(client)
        authorized = await client.get("/api/v1/scans")

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error_code"] == "AUTH_REQUIRED"
    assert authorized.status_code == 200


@pytest.mark.anyio
async def test_dependency_health_allows_both_authenticated_operator_roles() -> None:
    officer_app = create_app(identity_provider=LocalIdentityProvider())
    manager_app = create_app(
        identity_provider=LocalIdentityProvider(role=UserRole.MANAGER)
    )
    async with AsyncClient(
        transport=ASGITransport(app=officer_app),
        base_url="https://officer.test",
    ) as officer:
        unauthenticated = await officer.get("/health/dependencies")
        await sign_in(officer)
        officer_authorized = await officer.get("/health/dependencies")
    async with AsyncClient(
        transport=ASGITransport(app=manager_app),
        base_url="https://manager.test",
    ) as manager:
        await sign_in(manager)
        authorized = await manager.get("/health/dependencies")

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error_code"] == "AUTH_REQUIRED"
    assert officer_authorized.status_code == 200
    assert authorized.status_code == 200
