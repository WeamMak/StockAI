"""Framework-neutral structured LLM boundary for procurement reasoning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import AppliedPreferences
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
    preferences: tuple[AppliedPreferences, ...] = ()
    evidence: tuple[ProcurementEvidence, ...] = ()

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
        if (
            not isinstance(self.preferences, tuple)
            or len(self.preferences) not in {0, len(self.candidates)}
            or not all(
                isinstance(preference, AppliedPreferences)
                for preference in self.preferences
            )
        ):
            raise ValueError("preferences must match the candidate set")
        if (
            not isinstance(self.evidence, tuple)
            or len(self.evidence) not in {0, len(self.candidates)}
            or not all(isinstance(item, ProcurementEvidence) for item in self.evidence)
        ):
            raise ValueError("evidence must match the candidate set")
        if self.evidence and {
            item.product_id for item in self.evidence if item.skip_reason_code is None
        } != {candidate.product_id for candidate in self.candidates}:
            raise ValueError("evidence must describe the eligible candidate set")


@dataclass(frozen=True, slots=True)
class StructuredRecommendation:
    """Validated model output before deterministic agent checks."""

    decision: RecommendationDecision
    product_id: str | None
    rationale: str
    risk_flags: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    offer_id: str | None = None
    trade_offs: tuple[str, ...] = ()
    uncertainty: str = "No additional uncertainty identified."
    evidence_limitations: tuple[str, ...] = ()
    evidence_id: str | None = None
    evidence_digest: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    normalized_cost: Decimal | None = None
    budget_status: str = "not_evaluated"
    preference_profile_id: str | None = None
    preference_scope: str | None = None
    preference_revision: int | None = None
    priority_order: tuple[str, ...] = ()
    premium_outcome: str | None = None
    retry_count: int = 0
    repair_attempted: bool = False

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
        if self.decision is RecommendationDecision.RECOMMEND:
            if (
                not isinstance(self.offer_id, str)
                or not 1 <= len(self.offer_id) <= 128
                or _IDENTIFIER_PATTERN.fullmatch(self.offer_id) is None
            ):
                raise ValueError("a recommendation requires a bounded offer_id")
            for field_name, value in (
                ("quantity", self.quantity),
                ("unit_price", self.unit_price),
                ("normalized_cost", self.normalized_cost),
            ):
                if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                    raise ValueError(
                        f"{field_name} must be an exact non-negative Decimal"
                    )
        elif any(
            value is not None
            for value in (
                self.offer_id,
                self.evidence_id,
                self.evidence_digest,
                self.quantity,
                self.unit_price,
                self.normalized_cost,
            )
        ):
            raise ValueError("manual review cannot select or copy offer evidence")
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
        for field_name, values, maximum_items, maximum_length in (
            ("trade_offs", self.trade_offs, 6, 240),
            ("evidence_limitations", self.evidence_limitations, 6, 240),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > maximum_items
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > maximum_length
                    or _CONTROL_CHARACTER_PATTERN.search(value) is not None
                    for value in values
                )
            ):
                raise ValueError(f"{field_name} must contain bounded normal text")
        if (
            not isinstance(self.uncertainty, str)
            or not self.uncertainty.strip()
            or len(self.uncertainty) > 240
            or _CONTROL_CHARACTER_PATTERN.search(self.uncertainty) is not None
        ):
            raise ValueError("uncertainty must be bounded normal text")
        if self.budget_status not in {
            "within_budget",
            "exception_required",
            "unavailable",
            "not_evaluated",
        }:
            raise ValueError("budget_status is invalid")
        if self.preference_scope not in {None, "company", "category", "product"}:
            raise ValueError("preference_scope is invalid")
        if self.premium_outcome not in {
            None,
            "within_cap",
            "advisory_exceeded",
            "hard_excluded",
        }:
            raise ValueError("premium_outcome is invalid")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 2:
            raise ValueError("retry_count must be between zero and two")
        if type(self.repair_attempted) is not bool:
            raise ValueError("repair_attempted must be boolean")
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

    def __init__(self, private_detail: object = None, *, retry_count: int = 0) -> None:
        del private_detail
        self.retry_count = retry_count
        super().__init__("The recommendation model is unavailable.")


class LlmOutputInvalidError(Exception):
    """Safe signal that structured model output could not be validated."""

    def __init__(
        self,
        private_detail: object = None,
        *,
        retry_count: int = 0,
        repair_attempted: bool = False,
    ) -> None:
        del private_detail
        self.retry_count = retry_count
        self.repair_attempted = repair_attempted
        super().__init__("The recommendation model returned an invalid result.")
