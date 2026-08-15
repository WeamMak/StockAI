"""Strict structured-output boundary for procurement recommendations."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files

from procurement.ports.llm import (
    LlmOutputInvalidError,
    RecommendationDecision,
    RecommendationRequest,
    StructuredRecommendation,
)

_REQUIRED_FIELDS = {
    "decision",
    "product_id",
    "rationale",
    "risk_flags",
    "budget_acknowledgement",
}

RECOMMENDATION_JSON_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["recommend", "manual_review"]},
        "product_id": {
            "type": ["string", "null"],
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        },
        "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
        "risk_flags": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "pattern": r"^[A-Z][A-Z0-9_]*$",
            },
        },
        "budget_acknowledgement": {
            "type": "string",
            "enum": ["not_evaluated"],
        },
    },
    "required": sorted(_REQUIRED_FIELDS),
    "additionalProperties": False,
}


def load_procurement_system_prompt() -> str:
    """Load the version-controlled application-owned system prompt."""

    prompt = (
        files("procurement.agent")
        .joinpath("prompts/procurement_system.md")
        .read_text(encoding="utf-8")
    )
    if not prompt.strip():
        raise RuntimeError("procurement system prompt is empty")
    return prompt


def validate_recommendation_payload(
    payload: Mapping[str, object],
    request: RecommendationRequest,
    input_tokens: int,
    output_tokens: int,
) -> StructuredRecommendation:
    """Validate model JSON against deterministic walking-skeleton evidence."""

    try:
        if set(payload) != _REQUIRED_FIELDS:
            raise ValueError("recommendation fields are invalid")
        raw_decision = payload["decision"]
        if not isinstance(raw_decision, str):
            raise ValueError("recommendation decision is invalid")
        decision = RecommendationDecision(raw_decision)
        product_id = payload["product_id"]
        rationale = payload["rationale"]
        risk_flags = payload["risk_flags"]
        if payload["budget_acknowledgement"] != "not_evaluated":
            raise ValueError("budget acknowledgement is invalid")
        if product_id is not None and not isinstance(product_id, str):
            raise ValueError("product identifier is invalid")
        if decision is RecommendationDecision.RECOMMEND and product_id not in {
            candidate.product_id for candidate in request.candidates
        }:
            raise ValueError("the selected product is not eligible")
        if not isinstance(rationale, str) or not isinstance(risk_flags, list):
            raise ValueError("recommendation text is invalid")
        return StructuredRecommendation(
            decision=decision,
            product_id=product_id,
            rationale=rationale,
            risk_flags=tuple(risk_flags),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except (TypeError, ValueError) as error:
        raise LlmOutputInvalidError(error) from None
