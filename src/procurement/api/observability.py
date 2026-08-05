"""FastAPI-owned request logging and HTTP metrics."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from fastapi import FastAPI, Request, Response
from prometheus_client import CollectorRegistry, Counter, Histogram

from procurement.observability.logging import log_event

_KNOWN_HTTP_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)


@dataclass(frozen=True, slots=True)
class HttpMetrics:
    """Isolated collectors owned by one API application instance."""

    registry: CollectorRegistry
    requests: Counter
    errors: Counter
    duration: Histogram

    def observe(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """Record one completed request using bounded labels."""

        safe_method = method if method in _KNOWN_HTTP_METHODS else "OTHER"
        status_class = f"{status_code // 100}xx"
        labels = {
            "method": safe_method,
            "route": route,
            "status_class": status_class,
        }
        self.requests.labels(**labels).inc()
        if status_code >= 400:
            self.errors.labels(**labels).inc()
        self.duration.labels(method=safe_method, route=route).observe(duration_seconds)


def create_http_metrics() -> HttpMetrics:
    """Create collectors in a registry isolated from other app instances."""

    registry = CollectorRegistry(auto_describe=True)
    return HttpMetrics(
        registry=registry,
        requests=Counter(
            "procurement_http_requests",
            "Completed procurement API requests.",
            ("method", "route", "status_class"),
            registry=registry,
        ),
        errors=Counter(
            "procurement_http_request_errors",
            "Procurement API requests completed with an error status.",
            ("method", "route", "status_class"),
            registry=registry,
        ),
        duration=Histogram(
            "procurement_http_request_duration_seconds",
            "Procurement API request duration in seconds.",
            ("method", "route"),
            registry=registry,
        ),
    )


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def install_http_metrics(application: FastAPI, metrics: HttpMetrics) -> None:
    """Measure every API response without recording raw request paths."""

    async def measure_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            metrics.observe(
                method=request.method,
                route=_route_template(request),
                status_code=500,
                duration_seconds=perf_counter() - started_at,
            )
            raise
        metrics.observe(
            method=request.method,
            route=_route_template(request),
            status_code=response.status_code,
            duration_seconds=perf_counter() - started_at,
        )
        return response

    application.middleware("http")(measure_request)


def install_request_logging(application: FastAPI, logger: logging.Logger) -> None:
    """Emit one sanitized completion event for every API request."""

    async def log_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log_event(
                logger,
                "http_request_completed",
                level=logging.ERROR,
                request_id=request.state.correlation_id,
                method=request.method,
                route=_route_template(request),
                http_status=500,
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
                status="error",
            )
            raise
        log_event(
            logger,
            "http_request_completed",
            request_id=request.state.correlation_id,
            method=request.method,
            route=_route_template(request),
            http_status=response.status_code,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            status="success" if response.status_code < 400 else "error",
        )
        return response

    application.middleware("http")(log_request)
