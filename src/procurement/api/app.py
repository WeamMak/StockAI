"""FastAPI application construction for the procurement service."""

import logging

from fastapi import FastAPI

from procurement.api.config import ApiSettings
from procurement.api.errors import install_error_handling
from procurement.api.lifecycle import LifecycleState, lifespan_for
from procurement.api.routes.health import router as health_router
from procurement.observability.logging import (
    configure_json_logging,
    install_request_logging,
)
from procurement.observability.metrics import (
    HttpMetrics,
    create_http_metrics,
    install_http_metrics,
)


def create_app(
    *,
    settings: ApiSettings | None = None,
    logger: logging.Logger | None = None,
    lifecycle: LifecycleState | None = None,
    http_metrics: HttpMetrics | None = None,
) -> FastAPI:
    """Create an isolated procurement API application."""

    resolved_settings = settings or ApiSettings.from_environment()
    resolved_logger = logger or configure_json_logging(
        service=resolved_settings.service_name,
        environment=resolved_settings.environment.value,
        level=resolved_settings.log_level,
    )
    lifecycle = lifecycle or LifecycleState()
    http_metrics = http_metrics or create_http_metrics()
    application = FastAPI(
        title="StockAI Procurement API",
        lifespan=lifespan_for(lifecycle),
    )
    application.state.settings = resolved_settings
    application.state.logger = resolved_logger
    application.state.lifecycle = lifecycle
    application.state.http_metrics = http_metrics
    install_error_handling(application)
    install_http_metrics(application, http_metrics)
    install_request_logging(application, resolved_logger)
    application.include_router(health_router)
    return application


app = create_app()
