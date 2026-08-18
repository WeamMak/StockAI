"""Framework-neutral Prometheus metrics for procurement agent behavior."""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram

from procurement.domain.errors import ErrorCode

_KNOWN_MCP_TOOLS = frozenset(
    {
        "list_replenishment_candidates",
        "get_procurement_evidence",
        "get_procurement_preferences",
    }
)
_KNOWN_RETRY_OPERATIONS = _KNOWN_MCP_TOOLS | {"bedrock"}


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
    llm_repairs: Counter
    llm_fallbacks: Counter
    mcp_calls: Counter
    mcp_failures: Counter
    mcp_timeouts: Counter
    mcp_duration: Histogram
    retries: Counter
    preference_outcomes: Counter

    @staticmethod
    def _safe_tool(tool: str) -> str:
        return tool if tool in _KNOWN_MCP_TOOLS else "unknown"

    def observe_scan(
        self,
        *,
        trigger: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Record one terminal background scan's own orchestration health."""

        safe_trigger = trigger if trigger in {"manual", "cron"} else "unknown"
        safe_status = status if status in {"success", "error"} else "error"
        self.scans.labels(trigger=safe_trigger, status=safe_status).inc()
        self.scan_duration.labels(trigger=safe_trigger).observe(duration_seconds)

    def observe_case_result(
        self,
        *,
        outcome: str,
        error_code: ErrorCode | None,
    ) -> None:
        """Record one terminal case's bounded result, independent of its scan."""

        safe_outcome = (
            outcome
            if outcome
            in {"approval_ready", "manual_review", "no_valid_offer", "unresolved"}
            else "unresolved"
        )
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
        safe_operation = (
            operation if operation in _KNOWN_RETRY_OPERATIONS else "unknown"
        )
        self.retries.labels(operation=safe_operation).inc(count)

    def record_llm_repair(self) -> None:
        """Record one bounded structured-output repair attempt."""

        self.llm_repairs.inc()

    def record_llm_fallback(self, *, reason: str) -> None:
        """Record a deterministic manual-review fallback with bounded labels."""

        safe_reason = (
            reason
            if reason in {"unavailable", "invalid", "model_declined"}
            else "unknown"
        )
        self.llm_fallbacks.labels(reason=safe_reason).inc()

    def record_preference_outcome(self, *, scope: str, mode: str, outcome: str) -> None:
        safe_scope = scope if scope in {"company", "category", "product"} else "unknown"
        safe_mode = mode if mode in {"advisory", "hard"} else "unknown"
        safe_outcome = (
            outcome
            if outcome in {"within_cap", "advisory_exceeded", "hard_excluded"}
            else "unknown"
        )
        self.preference_outcomes.labels(
            scope=safe_scope, mode=safe_mode, outcome=safe_outcome
        ).inc()


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
        llm_repairs=Counter(
            "procurement_llm_repairs",
            "Structured recommendation schema repair attempts.",
            registry=resolved_registry,
        ),
        llm_fallbacks=Counter(
            "procurement_llm_fallbacks",
            "Deterministic manual-review fallbacks after model reasoning.",
            ("reason",),
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
        preference_outcomes=Counter(
            "procurement_preference_offer_outcomes",
            "Deterministic offer outcomes under the applied preference profile.",
            ("scope", "mode", "outcome"),
            registry=resolved_registry,
        ),
    )
