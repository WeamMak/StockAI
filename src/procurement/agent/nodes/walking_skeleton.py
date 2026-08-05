"""Minimal read-only LangGraph nodes for candidate recommendation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter

from procurement.agent.state import (
    ApprovalReadyResult,
    ScanState,
    UnresolvedResult,
)
from procurement.domain.errors import ErrorCode
from procurement.observability.logging import log_event
from procurement.observability.metrics import AgentMetrics
from procurement.ports.llm import (
    LlmOutputInvalidError,
    LlmUnavailableError,
    RecommendationDecision,
    RecommendationRequest,
    StructuredLlmPort,
)
from procurement.ports.mcp import (
    McpReadError,
    McpTimeoutError,
    ProcurementMcpPort,
)


@dataclass(frozen=True, slots=True)
class WalkingSkeletonNodes:
    """Dependency-injected nodes used by the compiled graph."""

    mcp: ProcurementMcpPort
    llm: StructuredLlmPort
    metrics: AgentMetrics
    logger: logging.Logger

    async def discover_candidates(self, state: ScanState) -> dict[str, object]:
        """Call the MCP port and retain only validated candidate data."""

        started_at = perf_counter()
        try:
            page = await self.mcp.list_replenishment_candidates(
                environment=state["environment"],
                horizon_days=14,
                limit=25,
            )
        except McpReadError as error:
            error_code = (
                ErrorCode.MCP_TIMEOUT
                if isinstance(error, McpTimeoutError)
                else ErrorCode.ODOO_UNAVAILABLE
            )
            self._record_mcp_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=error_code,
                retry_count=error.retry_count,
            )
            if isinstance(error, McpTimeoutError):
                self.metrics.record_mcp_timeout(tool="list_replenishment_candidates")
            return {
                "result": UnresolvedResult(
                    error_code=error_code,
                    message=error.safe_message,
                    retryable=True,
                    retry_count=error.retry_count,
                )
            }
        except Exception:
            self._record_mcp_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.ODOO_UNAVAILABLE,
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.ODOO_UNAVAILABLE,
                    message="The procurement source is unavailable.",
                    retryable=True,
                )
            }

        if page.environment is not state["environment"]:
            self._record_mcp_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.ODOO_UNAVAILABLE,
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.ODOO_UNAVAILABLE,
                    message="The procurement source returned an invalid response.",
                    retryable=True,
                )
            }
        self._record_mcp_completion(
            state=state,
            started_at=started_at,
            status="success",
        )
        candidates = tuple(
            candidate
            for candidate in page.candidates
            if candidate.skip_reason_code is None
        )
        if not candidates:
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.NO_VALID_OFFER,
                    message="No approval-ready replenishment candidate was found.",
                    retryable=False,
                )
            }
        return {"candidates": candidates}

    async def reason_about_candidate(self, state: ScanState) -> dict[str, object]:
        """Invoke structured reasoning and validate the selected identifier."""

        if "result" in state:
            return {}

        started_at = perf_counter()
        try:
            recommendation = await self.llm.recommend(
                RecommendationRequest(
                    environment=state["environment"],
                    candidates=state["candidates"],
                )
            )
        except LlmUnavailableError:
            self._record_llm_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.LLM_UNAVAILABLE,
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.LLM_UNAVAILABLE,
                    message="The recommendation model is unavailable.",
                    retryable=True,
                )
            }
        except (LlmOutputInvalidError, ValueError):
            self._record_llm_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.LLM_OUTPUT_INVALID,
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.LLM_OUTPUT_INVALID,
                    message="The recommendation model returned an invalid result.",
                    retryable=False,
                )
            }
        except Exception:
            self._record_llm_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.LLM_UNAVAILABLE,
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.LLM_UNAVAILABLE,
                    message="The recommendation model is unavailable.",
                    retryable=True,
                )
            }

        self._record_llm_completion(
            state=state,
            started_at=started_at,
            status="success",
            input_tokens=recommendation.input_tokens,
            output_tokens=recommendation.output_tokens,
        )
        selected = next(
            (
                candidate
                for candidate in state["candidates"]
                if candidate.product_id == recommendation.product_id
            ),
            None,
        )
        if (
            recommendation.decision is not RecommendationDecision.RECOMMEND
            or selected is None
        ):
            return {
                "recommendation": recommendation,
                "result": UnresolvedResult(
                    error_code=ErrorCode.LLM_OUTPUT_INVALID,
                    message="The model could not produce an approval-ready result.",
                    retryable=False,
                ),
            }
        return {
            "recommendation": recommendation,
            "result": ApprovalReadyResult(
                product_id=selected.product_id,
                product_name=selected.product_name,
                rationale=recommendation.rationale,
                risk_flags=recommendation.risk_flags,
            ),
        }

    def _record_mcp_completion(
        self,
        *,
        state: ScanState,
        started_at: float,
        status: str,
        error_code: ErrorCode | None = None,
        retry_count: int = 0,
    ) -> None:
        duration_seconds = perf_counter() - started_at
        tool_name = "list_replenishment_candidates"
        self.metrics.observe_mcp_call(
            tool=tool_name,
            status=status,
            duration_seconds=duration_seconds,
            error_code=error_code,
        )
        self.metrics.record_retries(
            operation=tool_name,
            count=retry_count,
        )
        fields: dict[str, object] = {
            "scan_id": state["scan_id"],
            "tool_name": tool_name,
            "duration_ms": round(duration_seconds * 1000, 3),
            "status": status,
            "retry_count": retry_count,
        }
        if error_code is not None:
            fields["error_code"] = error_code.value
        log_event(
            self.logger,
            "agent_mcp_call_completed",
            level=logging.ERROR if error_code is not None else logging.INFO,
            **fields,
        )

    def _record_llm_completion(
        self,
        *,
        state: ScanState,
        started_at: float,
        status: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_code: ErrorCode | None = None,
    ) -> None:
        duration_seconds = perf_counter() - started_at
        self.metrics.observe_llm_call(
            status=status,
            duration_seconds=duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_code=error_code,
        )
        fields: dict[str, object] = {
            "scan_id": state["scan_id"],
            "model_id": "structured-llm-port",
            "duration_ms": round(duration_seconds * 1000, 3),
            "status": status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        if error_code is not None:
            fields["error_code"] = error_code.value
        log_event(
            self.logger,
            "llm_call_completed",
            level=logging.ERROR if error_code is not None else logging.INFO,
            **fields,
        )
