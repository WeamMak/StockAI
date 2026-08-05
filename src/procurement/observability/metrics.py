"""Low-cardinality Prometheus metrics for procurement service boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from fastapi import FastAPI, Request, Response
from prometheus_client import CollectorRegistry, Counter, Histogram

from procurement.domain.errors import ErrorCode

_KNOWN_HTTP_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)
_KNOWN_MCP_TOOLS = frozenset({"list_replenishment_candidates"})


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


@dataclass(frozen=True, slots=True)
class McpMetrics:
    """Isolated, low-cardinality collectors for MCP tool execution."""

    registry: CollectorRegistry
    calls: Counter
    failures: Counter
    timeouts: Counter
    retries: Counter
    duration: Histogram

    @staticmethod
    def _safe_tool(tool: str) -> str:
        return tool if tool in _KNOWN_MCP_TOOLS else "unknown"

    def observe_call(
        self,
        *,
        tool: str,
        status: str,
        duration_seconds: float,
        error_code: ErrorCode | None = None,
    ) -> None:
        """Record one completed tool call and its final outcome."""

        safe_tool = self._safe_tool(tool)
        safe_status = status if status in {"success", "error"} else "error"
        self.calls.labels(tool=safe_tool, status=safe_status).inc()
        self.duration.labels(tool=safe_tool).observe(duration_seconds)
        if error_code is not None:
            self.failures.labels(
                tool=safe_tool,
                error_code=error_code.value,
            ).inc()

    def record_timeout(self, *, tool: str) -> None:
        """Record one timed-out ERP read attempt."""

        self.timeouts.labels(tool=self._safe_tool(tool)).inc()

    def record_retry(self, *, tool: str) -> None:
        """Record one safe retry of an MCP read."""

        self.retries.labels(tool=self._safe_tool(tool)).inc()


def create_mcp_metrics() -> McpMetrics:
    """Create MCP collectors in an isolated registry."""

    registry = CollectorRegistry(auto_describe=True)
    return McpMetrics(
        registry=registry,
        calls=Counter(
            "procurement_mcp_tool_calls",
            "Completed Procurement MCP tool calls.",
            ("tool", "status"),
            registry=registry,
        ),
        failures=Counter(
            "procurement_mcp_tool_failures",
            "Failed Procurement MCP tool calls.",
            ("tool", "error_code"),
            registry=registry,
        ),
        timeouts=Counter(
            "procurement_mcp_tool_timeouts",
            "Timed-out Procurement MCP dependency attempts.",
            ("tool",),
            registry=registry,
        ),
        retries=Counter(
            "procurement_mcp_tool_retries",
            "Retried Procurement MCP dependency attempts.",
            ("tool",),
            registry=registry,
        ),
        duration=Histogram(
            "procurement_mcp_tool_duration_seconds",
            "Procurement MCP tool duration in seconds.",
            ("tool",),
            registry=registry,
        ),
    )


@dataclass(frozen=True, slots=True)
class AgentMetrics:
    """Low-cardinality scan, LLM, and agent-side MCP collectors."""

    registry: CollectorRegistry
    scans: Counter
    scan_duration: Histogram
    scan_results: Counter
    llm_calls: Counter
    llm_failures: Counter
    llm_duration: Histogram
    llm_tokens: Counter
    mcp_calls: Counter
    mcp_failures: Counter
    mcp_timeouts: Counter
    mcp_duration: Histogram
    retries: Counter

    @staticmethod
    def _safe_tool(tool: str) -> str:
        return tool if tool in _KNOWN_MCP_TOOLS else "unknown"

    def observe_scan(
        self,
        *,
        trigger: str,
        status: str,
        duration_seconds: float,
        outcome: str,
        error_code: ErrorCode | None,
    ) -> None:
        """Record one terminal background scan and its bounded result."""

        safe_trigger = trigger if trigger in {"manual", "cron"} else "unknown"
        safe_status = status if status in {"success", "error"} else "error"
        safe_outcome = (
            outcome if outcome in {"approval_ready", "unresolved"} else "unresolved"
        )
        self.scans.labels(trigger=safe_trigger, status=safe_status).inc()
        self.scan_duration.labels(trigger=safe_trigger).observe(duration_seconds)
        self.scan_results.labels(
            outcome=safe_outcome,
            error_code=error_code.value if error_code is not None else "none",
        ).inc()

    def observe_llm_call(
        self,
        *,
        status: str,
        duration_seconds: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_code: ErrorCode | None = None,
    ) -> None:
        """Record one structured model call without model output labels."""

        safe_status = status if status in {"success", "error"} else "error"
        self.llm_calls.labels(status=safe_status).inc()
        self.llm_duration.observe(duration_seconds)
        if error_code is not None:
            self.llm_failures.labels(error_code=error_code.value).inc()
        if input_tokens:
            self.llm_tokens.labels(direction="input").inc(input_tokens)
        if output_tokens:
            self.llm_tokens.labels(direction="output").inc(output_tokens)

    def observe_mcp_call(
        self,
        *,
        tool: str,
        status: str,
        duration_seconds: float,
        error_code: ErrorCode | None = None,
    ) -> None:
        """Record one agent-side MCP transport call."""

        safe_tool = self._safe_tool(tool)
        safe_status = status if status in {"success", "error"} else "error"
        self.mcp_calls.labels(tool=safe_tool, status=safe_status).inc()
        self.mcp_duration.labels(tool=safe_tool).observe(duration_seconds)
        if error_code is not None:
            self.mcp_failures.labels(
                tool=safe_tool,
                error_code=error_code.value,
            ).inc()

    def record_mcp_timeout(self, *, tool: str) -> None:
        self.mcp_timeouts.labels(tool=self._safe_tool(tool)).inc()

    def record_retries(self, *, operation: str, count: int) -> None:
        if count <= 0:
            return
        safe_operation = operation if operation in _KNOWN_MCP_TOOLS else "unknown"
        self.retries.labels(operation=safe_operation).inc(count)


def create_agent_metrics(
    registry: CollectorRegistry | None = None,
) -> AgentMetrics:
    """Create agent collectors, optionally sharing the API registry."""

    resolved_registry = registry or CollectorRegistry(auto_describe=True)
    return AgentMetrics(
        registry=resolved_registry,
        scans=Counter(
            "procurement_scans",
            "Completed procurement scans.",
            ("trigger", "status"),
            registry=resolved_registry,
        ),
        scan_duration=Histogram(
            "procurement_scan_duration_seconds",
            "Non-human procurement scan duration in seconds.",
            ("trigger",),
            registry=resolved_registry,
        ),
        scan_results=Counter(
            "procurement_scan_results",
            "Terminal procurement scan results.",
            ("outcome", "error_code"),
            registry=resolved_registry,
        ),
        llm_calls=Counter(
            "procurement_llm_calls",
            "Completed structured recommendation model calls.",
            ("status",),
            registry=resolved_registry,
        ),
        llm_failures=Counter(
            "procurement_llm_failures",
            "Failed structured recommendation model calls.",
            ("error_code",),
            registry=resolved_registry,
        ),
        llm_duration=Histogram(
            "procurement_llm_duration_seconds",
            "Structured recommendation model duration in seconds.",
            registry=resolved_registry,
        ),
        llm_tokens=Counter(
            "procurement_llm_tokens",
            "Structured recommendation model tokens.",
            ("direction",),
            registry=resolved_registry,
        ),
        mcp_calls=Counter(
            "procurement_agent_mcp_calls",
            "Completed agent-side Procurement MCP calls.",
            ("tool", "status"),
            registry=resolved_registry,
        ),
        mcp_failures=Counter(
            "procurement_agent_mcp_failures",
            "Failed agent-side Procurement MCP calls.",
            ("tool", "error_code"),
            registry=resolved_registry,
        ),
        mcp_timeouts=Counter(
            "procurement_agent_mcp_timeouts",
            "Timed-out agent-side Procurement MCP calls.",
            ("tool",),
            registry=resolved_registry,
        ),
        mcp_duration=Histogram(
            "procurement_agent_mcp_duration_seconds",
            "Agent-side Procurement MCP call duration in seconds.",
            ("tool",),
            registry=resolved_registry,
        ),
        retries=Counter(
            "procurement_agent_retries",
            "Safe retries observed by the procurement agent.",
            ("operation",),
            registry=resolved_registry,
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
