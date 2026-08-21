"""Procurement MCP-owned tool metrics."""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram

from procurement.domain.errors import ErrorCode

_KNOWN_MCP_TOOLS = frozenset(
    {
        "list_replenishment_candidates",
        "get_procurement_evidence",
        "get_procurement_preferences",
        "create_purchase_order_draft",
        "confirm_purchase_order",
        "cancel_draft_purchase_order",
    }
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
    purchase_order_actions: Counter
    purchase_order_reconciliation: Histogram

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

    def observe_purchase_order_action(
        self,
        *,
        action: str,
        result: str,
        duration_seconds: float,
        reconciled: bool = False,
    ) -> None:
        safe_action = action if action in {"confirm", "cancel"} else "unknown"
        safe_result = (
            result
            if result in {"success", "stale", "reconciliation_required", "error"}
            else "error"
        )
        self.purchase_order_actions.labels(action=safe_action, result=safe_result).inc()
        if safe_result == "reconciliation_required" or reconciled:
            self.purchase_order_reconciliation.labels(action=safe_action).observe(
                duration_seconds
            )


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
        purchase_order_actions=Counter(
            "procurement_purchase_order_actions",
            "Purchase-order actions by bounded result.",
            ("action", "result"),
            registry=registry,
        ),
        purchase_order_reconciliation=Histogram(
            "procurement_purchase_order_reconciliation_seconds",
            "Purchase-order reconciliation duration in seconds.",
            ("action",),
            registry=registry,
        ),
    )
