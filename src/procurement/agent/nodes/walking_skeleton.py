"""Minimal read-only LangGraph nodes for candidate recommendation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter

from procurement.agent.state import (
    ApprovalReadyResult,
    ScanState,
    UnresolvedResult,
)
from procurement.domain.errors import ErrorCode
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import apply_preferences
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
    company_id: str

    async def discover_candidates(self, state: ScanState) -> dict[str, object]:
        """Call the MCP port and retain only validated candidate data."""

        started_at = perf_counter()
        try:
            page = await self.mcp.list_replenishment_candidates(
                environment=state["environment"],
                horizon_days=14,
                limit=50,
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

    async def gather_evidence(self, state: ScanState) -> dict[str, object]:
        """Gather authoritative evidence for every candidate, three at a time."""

        if "result" in state:
            return {}
        semaphore = asyncio.Semaphore(3)

        async def gather(product_id: str) -> ProcurementEvidence:
            async with semaphore:
                return await self.mcp.get_procurement_evidence(
                    environment=state["environment"],
                    product_id=product_id,
                    horizon_days=14,
                )

        started_at = perf_counter()
        try:
            gathered = tuple(
                await asyncio.gather(
                    *(gather(candidate.product_id) for candidate in state["candidates"])
                )
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
                tool_name="get_procurement_evidence",
            )
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
                tool_name="get_procurement_evidence",
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.ODOO_UNAVAILABLE,
                    message="The procurement source returned invalid evidence.",
                    retryable=True,
                )
            }
        self._record_mcp_completion(
            state=state,
            started_at=started_at,
            status="success",
            tool_name="get_procurement_evidence",
        )
        eligible_ids = {
            item.product_id for item in gathered if item.skip_reason_code is None
        }
        eligible_candidates = tuple(
            candidate
            for candidate in state["candidates"]
            if candidate.product_id in eligible_ids
        )
        if not eligible_candidates:
            return {
                "evidence": gathered,
                "result": UnresolvedResult(
                    error_code=ErrorCode.NO_VALID_OFFER,
                    message=(
                        "Every replenishment candidate was deterministically skipped."
                    ),
                    retryable=False,
                ),
            }
        return {"evidence": gathered, "candidates": eligible_candidates}

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
                    preferences=tuple(
                        item.preferences
                        for item in state["evidence"]
                        if item.skip_reason_code is None
                        and item.preferences is not None
                    ),
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
                evidence=next(
                    item
                    for item in state["evidence"]
                    if item.product_id == selected.product_id
                ),
            ),
        }

    async def resolve_preferences(self, state: ScanState) -> dict[str, object]:
        """Resolve and enforce one immutable typed profile per eligible record."""

        if "result" in state:
            return {}
        started_at = perf_counter()
        try:
            resolved: list[ProcurementEvidence] = []
            for evidence in state["evidence"]:
                if evidence.skip_reason_code is not None:
                    resolved.append(evidence)
                    continue
                profile = await self.mcp.get_procurement_preferences(
                    environment=state["environment"],
                    company_id=self.company_id,
                    category_id=evidence.category_id,
                    product_id=evidence.product_id,
                )
                applied = apply_preferences(evidence, profile)
                if applied.preferences is None:  # pragma: no cover - domain invariant
                    raise ValueError("preference application produced no snapshot")
                for result in applied.preferences.offer_results:
                    self.metrics.record_preference_outcome(
                        scope=profile.scope.value,
                        mode=profile.enforcement_mode.value,
                        outcome=result.outcome,
                    )
                resolved.append(applied)
        except McpReadError as error:
            is_timeout = isinstance(error, McpTimeoutError)
            error_code = (
                ErrorCode.MCP_TIMEOUT if is_timeout else ErrorCode.PREFERENCE_INVALID
            )
            self._record_mcp_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=error_code,
                retry_count=error.retry_count,
                tool_name="get_procurement_preferences",
            )
            if is_timeout:
                self.metrics.record_mcp_timeout(tool="get_procurement_preferences")
            return {
                "result": UnresolvedResult(
                    error_code=error_code,
                    message=(
                        error.safe_message
                        if is_timeout
                        else "The procurement preferences require configuration review."
                    ),
                    retryable=is_timeout,
                    retry_count=error.retry_count,
                )
            }
        except (AttributeError, TypeError, ValueError):
            self._record_mcp_completion(
                state=state,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.PREFERENCE_INVALID,
                tool_name="get_procurement_preferences",
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.PREFERENCE_INVALID,
                    message="The procurement preferences require configuration review.",
                    retryable=False,
                )
            }
        self._record_mcp_completion(
            state=state,
            started_at=started_at,
            status="success",
            tool_name="get_procurement_preferences",
        )
        return {"evidence": tuple(resolved)}

    def _record_mcp_completion(
        self,
        *,
        state: ScanState,
        started_at: float,
        status: str,
        error_code: ErrorCode | None = None,
        retry_count: int = 0,
        tool_name: str = "list_replenishment_candidates",
    ) -> None:
        duration_seconds = perf_counter() - started_at
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
