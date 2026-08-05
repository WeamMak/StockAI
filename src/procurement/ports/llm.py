"""Framework-neutral structured LLM boundary for procurement reasoning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from procurement.domain.identifiers import Environment
from procurement.ports.mcp import ReplenishmentCandidate

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_RISK_FLAG_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$", re.ASCII)
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class RecommendationDecision(StrEnum):
    """Bounded decisions emitted by the structured LLM port."""

    RECOMMEND = "recommend"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class RecommendationRequest:
    """Typed evidence supplied to the walking-skeleton model call."""

    environment: Environment
    candidates: tuple[ReplenishmentCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("environment must be dev or prod")
        if (
            not isinstance(self.candidates, tuple)
            or not 1 <= len(self.candidates) <= 25
            or not all(
                isinstance(candidate, ReplenishmentCandidate)
                for candidate in self.candidates
            )
        ):
            raise ValueError("candidates must contain 1 to 25 candidates")


@dataclass(frozen=True, slots=True)
class StructuredRecommendation:
    """Validated model output before deterministic agent checks."""

    decision: RecommendationDecision
    product_id: str | None
    rationale: str
    risk_flags: tuple[str, ...]
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.decision, RecommendationDecision):
            raise ValueError("decision must be a supported value")
        if self.decision is RecommendationDecision.RECOMMEND:
            if (
                not isinstance(self.product_id, str)
                or not 1 <= len(self.product_id) <= 128
                or _IDENTIFIER_PATTERN.fullmatch(self.product_id) is None
            ):
                raise ValueError("a recommendation requires a bounded product_id")
        elif self.product_id is not None:
            raise ValueError("manual review cannot select a product")
        if (
            not isinstance(self.rationale, str)
            or not self.rationale.strip()
            or len(self.rationale) > 500
            or _CONTROL_CHARACTER_PATTERN.search(self.rationale) is not None
        ):
            raise ValueError("rationale must be bounded normal text")
        if (
            not isinstance(self.risk_flags, tuple)
            or len(self.risk_flags) > 10
            or any(
                not isinstance(flag, str)
                or not 1 <= len(flag) <= 64
                or _RISK_FLAG_PATTERN.fullmatch(flag) is None
                for flag in self.risk_flags
            )
        ):
            raise ValueError("risk_flags must contain bounded stable codes")
        for field_name, token_count in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if type(token_count) is not int or not 0 <= token_count <= 10_000_000:
                raise ValueError(f"{field_name} must be a bounded integer")


class StructuredLlmPort(Protocol):
    """Structured reasoning operation owned by the procurement agent."""

    async def recommend(
        self,
        request: RecommendationRequest,
    ) -> StructuredRecommendation:
        """Return one validated recommendation or manual-review decision."""


class LlmUnavailableError(Exception):
    """Safe signal that the configured model could not be called."""

    def __init__(self, private_detail: object = None) -> None:
        del private_detail
        super().__init__("The recommendation model is unavailable.")


class LlmOutputInvalidError(Exception):
    """Safe signal that structured model output could not be validated."""

    def __init__(self, private_detail: object = None) -> None:
        del private_detail
        super().__init__("The recommendation model returned an invalid result.")
