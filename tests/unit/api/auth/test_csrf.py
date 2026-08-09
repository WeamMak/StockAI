"""Session-bound CSRF protection for browser writes."""

from __future__ import annotations

import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in

from procurement.api.app import create_app


@pytest.mark.anyio
async def test_manual_scan_rejects_missing_or_mismatched_csrf() -> None:
    application = create_app(identity_provider=LocalIdentityProvider())
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        valid_headers = await sign_in(client)
        missing = await client.post("/api/v1/scans")
        mismatched = await client.post(
            "/api/v1/scans",
            headers={"X-CSRF-Token": "wrong-opaque-token"},
        )

    assert valid_headers["X-CSRF-Token"] != "wrong-opaque-token"
    assert missing.status_code == 403
    assert missing.json()["error_code"] == "CSRF_INVALID"
    assert mismatched.status_code == 403
    assert mismatched.json()["error_code"] == "CSRF_INVALID"
