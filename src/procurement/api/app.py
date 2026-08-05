"""FastAPI application construction for the procurement service."""

import logging

from fastapi import FastAPI

from procurement.api.config import ApiSettings
from procurement.api.errors import install_error_handling
from procurement.api.lifecycle import LifecycleState, lifespan_for
from procurement.api.routes.health import router as health_router
from procurement.api.routes.internal import router as internal_router
from procurement.api.routes.scans import router as scans_router
from procurement.api.services.scans import (
    ScanService,
    ScanWorkflow,
    UnconfiguredScanWorkflow,
)
from procurement.observability.logging import (
    configure_json_logging,
    install_request_logging,
)
from procurement.observability.metrics import (
    AgentMetrics,
    HttpMetrics,
    create_agent_metrics,
    create_http_metrics,
    install_http_metrics,
)


def create_app(
    *,
    settings: ApiSettings | None = None,
    logger: logging.Logger | None = None,
    lifecycle: LifecycleState | None = None,
    http_metrics: HttpMetrics | None = None,
    scan_workflow: ScanWorkflow | None = None,
    scan_service: ScanService | None = None,
    agent_metrics: AgentMetrics | None = None,
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
    agent_metrics = agent_metrics or create_agent_metrics(http_metrics.registry)
    scan_service = scan_service or ScanService(
        workflow=scan_workflow or UnconfiguredScanWorkflow(),
        environment=resolved_settings.environment,
        metrics=agent_metrics,
        logger=resolved_logger,
    )
    application = FastAPI(
        title="StockAI Procurement API",
        lifespan=lifespan_for(lifecycle),
    )
    application.state.settings = resolved_settings
    application.state.logger = resolved_logger
    application.state.lifecycle = lifecycle
    application.state.http_metrics = http_metrics
    application.state.agent_metrics = agent_metrics
    application.state.scan_service = scan_service
    install_error_handling(application)
    install_http_metrics(application, http_metrics)
    install_request_logging(application, resolved_logger)
    application.include_router(health_router)
    application.include_router(scans_router)
    application.include_router(internal_router)
    return application


app = create_app()
