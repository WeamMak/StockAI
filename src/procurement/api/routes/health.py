"""Health endpoints for the procurement API process."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Report process liveness without checking external dependencies."""

    return {"status": "live"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """Report whether the application is accepting new traffic."""

    if request.app.state.lifecycle.is_ready:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready"},
        )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "not_ready"},
    )


@router.get("/health/dependencies")
async def dependencies() -> dict[str, object]:
    """Report bounded dependency states without exposing upstream details."""

    return {
        "status": "not_configured",
        "dependencies": {
            "bedrock": "not_configured",
            "dynamodb": "not_configured",
            "mcp": "not_configured",
            "odoo": "not_configured",
            "recent_scan": "not_configured",
        },
    }


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Expose this process's Prometheus registry."""

    return Response(
        content=generate_latest(request.app.state.http_metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
