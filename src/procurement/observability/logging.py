"""Structured JSON logging with defensive sensitive-data redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import TextIO

from fastapi import FastAPI, Request, Response

REDACTED = "[REDACTED]"
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "api_key",
        "access_token",
        "refresh_token",
        "prompt",
        "system_prompt",
        "user_prompt",
        "model_output",
        "model_response",
        "manager_note",
        "manager_justification",
        "justification",
        "upstream_error",
        "raw_error",
        "traceback",
        "request_body",
        "response_body",
    }
)


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _SENSITIVE_EXACT_KEYS
        or "secret" in normalized
        or "password" in normalized
        or "prompt" in normalized
        or "price" in normalized
        or "budget" in normalized
        or "contract_term" in normalized
        or normalized.endswith("_token")
    )


def _sanitize_log_value(value: object) -> object:
    if isinstance(value, Mapping):
        return sanitize_log_fields(
            {
                str(nested_key): nested_value
                for nested_key, nested_value in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return [_sanitize_log_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return REDACTED


def sanitize_log_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Recursively remove prohibited data from structured log fields."""

    return {
        key: REDACTED if _is_sensitive_key(key) else _sanitize_log_value(value)
        for key, value in fields.items()
    }


class JsonLogFormatter(logging.Formatter):
    """Format one bounded operational event as a JSON object."""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        raw_fields = getattr(record, "event_fields", {})
        if isinstance(raw_fields, Mapping):
            event_fields = sanitize_log_fields(
                {str(key): value for key, value in raw_fields.items()}
            )
        else:
            event_fields = {}
        payload: dict[str, object] = {
            **event_fields,
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "event": record.getMessage(),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_json_logging(
    *,
    service: str,
    environment: str,
    stream: TextIO | None = None,
    logger_name: str = "procurement",
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure an isolated project logger that emits JSON lines."""

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonLogFormatter(service=service, environment=environment))
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured event through the configured project logger."""

    logger.log(level, event, extra={"event_fields": fields})


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


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
