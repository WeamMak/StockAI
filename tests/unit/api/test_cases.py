"""Cross-scan recent-recommendations listing API behavior."""

from __future__ import annotations

from typing import cast

import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in
from tests.unit.api.test_scans import MultiCandidateWorkflow, _poll_until_finished

from procurement.api.app import create_app


@pytest.mark.anyio
async def test_recent_cases_spans_multiple_scans_newest_first() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=2)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        first = await client.post("/api/v1/scans", headers=csrf_headers)
        await _poll_until_finished(client, first.json()["scan_id"])
        second = await client.post("/api/v1/scans", headers=csrf_headers)
        second_finished = await _poll_until_finished(client, second.json()["scan_id"])
        recent = await client.get("/api/v1/cases")

    assert recent.status_code == 200
    cases = cast(list[dict[str, object]], recent.json()["cases"])
    assert len(cases) == 4  # 2 candidates per scan, 2 scans
    second_scan_id = second_finished["scan_id"]
    assert cases[0]["scan_id"] == second_scan_id
    assert cases[1]["scan_id"] == second_scan_id
    assert {row["outcome"] for row in cases} == {"approval_ready"}
    assert {row["case_id"] for row in cases} == {
        f"{first.json()['scan_id']}:product-0",
        f"{first.json()['scan_id']}:product-1",
        f"{second_scan_id}:product-0",
        f"{second_scan_id}:product-1",
    }


@pytest.mark.anyio
async def test_recent_cases_bounds_limit_to_one_through_twenty() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=1)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        await sign_in(client)
        too_large = await client.get("/api/v1/cases?limit=21")
        too_small = await client.get("/api/v1/cases?limit=0")

    assert too_large.status_code == 422
    assert too_small.status_code == 422


@pytest.mark.anyio
async def test_recent_cases_defaults_to_no_history() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=1)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        await sign_in(client)
        recent = await client.get("/api/v1/cases")

    assert recent.status_code == 200
    assert recent.json()["cases"] == []
