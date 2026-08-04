"""Public health-endpoint behavior for the procurement API."""

import pytest
from httpx2 import ASGITransport, AsyncClient

from procurement.api.app import create_app


@pytest.mark.anyio
async def test_liveness_reports_that_the_process_is_alive() -> None:
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


@pytest.mark.anyio
async def test_readiness_tracks_the_application_lifecycle() -> None:
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        before_startup = await client.get("/health/ready")

        async with application.router.lifespan_context(application):
            while_running = await client.get("/health/ready")

        after_shutdown = await client.get("/health/ready")

    assert before_startup.status_code == 503
    assert before_startup.json() == {"status": "not_ready"}
    assert while_running.status_code == 200
    assert while_running.json() == {"status": "ready"}
    assert after_shutdown.status_code == 503
    assert after_shutdown.json() == {"status": "not_ready"}


@pytest.mark.anyio
async def test_dependency_health_is_explicit_before_dependencies_are_added() -> None:
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "status": "not_configured",
        "dependencies": {
            "bedrock": "not_configured",
            "dynamodb": "not_configured",
            "mcp": "not_configured",
            "odoo": "not_configured",
            "recent_scan": "not_configured",
        },
    }


@pytest.mark.anyio
async def test_metrics_expose_bounded_request_error_and_latency_series() -> None:
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        await client.get("/health/live")
        await client.get("/does-not-exist/case-123")
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert (
        'procurement_http_requests_total{method="GET",route="/health/live",'
        'status_class="2xx"} 1.0'
    ) in response.text
    assert (
        'procurement_http_request_errors_total{method="GET",route="unmatched",'
        'status_class="4xx"} 1.0'
    ) in response.text
    assert (
        'procurement_http_request_duration_seconds_count{method="GET",'
        'route="/health/live"} 1.0'
    ) in response.text
    assert "case-123" not in response.text
