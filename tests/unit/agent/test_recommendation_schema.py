"""Semantic validation of advisory model recommendations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from procurement.agent.recommendation_schema import (
    RECOMMENDATION_JSON_SCHEMA,
    validate_recommendation_payload,
)
from procurement.domain.identifiers import Environment
from procurement.ports.llm import (
    LlmOutputInvalidError,
    RecommendationDecision,
    RecommendationRequest,
)
from procurement.ports.mcp import ReplenishmentCandidate


def _request() -> RecommendationRequest:
    return RecommendationRequest(
        environment=Environment.DEV,
        candidates=(
            ReplenishmentCandidate(
                product_id="product-101",
                product_name="Fictional Safety Gloves",
                category_id="category-safety",
                reorder_minimum=Decimal("10.000000"),
                reorder_maximum=Decimal("40.000000"),
                projected_quantity=Decimal("8.000000"),
                projected_trigger_date=date(2026, 8, 9),
                skip_reason_code=None,
            ),
        ),
    )


def _payload() -> dict[str, object]:
    return {
        "decision": "recommend",
        "product_id": "product-101",
        "rationale": "Stock is below the configured reorder minimum.",
        "risk_flags": ["LIMITED_WALKING_SKELETON_EVIDENCE"],
        "budget_acknowledgement": "not_evaluated",
    }


def test_valid_output_uses_provider_token_metadata() -> None:
    recommendation = validate_recommendation_payload(
        _payload(),
        _request(),
        83,
        27,
    )

    assert recommendation.decision is RecommendationDecision.RECOMMEND
    assert recommendation.product_id == "product-101"
    assert recommendation.input_tokens == 83
    assert recommendation.output_tokens == 27


def test_provider_schema_enforces_the_application_text_bounds() -> None:
    properties = cast(dict[str, object], RECOMMENDATION_JSON_SCHEMA["properties"])

    assert properties["product_id"] == {
        "type": ["string", "null"],
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    }
    assert properties["rationale"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 500,
    }
    assert properties["risk_flags"] == {
        "type": "array",
        "maxItems": 10,
        "items": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": r"^[A-Z][A-Z0-9_]*$",
        },
    }


def test_ineligible_identifier_is_rejected() -> None:
    payload = _payload()
    payload["product_id"] = "product-not-eligible"

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, _request(), 1, 1)


def test_model_supplied_or_changed_arithmetic_is_rejected() -> None:
    payload = _payload()
    payload["projected_quantity"] = "999999.000000"

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, _request(), 1, 1)


def test_missing_budget_acknowledgement_is_rejected() -> None:
    payload = _payload()
    del payload["budget_acknowledgement"]

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, _request(), 1, 1)


def test_oversized_recommendation_text_is_rejected() -> None:
    payload = _payload()
    payload["rationale"] = "x" * 501

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, _request(), 1, 1)
