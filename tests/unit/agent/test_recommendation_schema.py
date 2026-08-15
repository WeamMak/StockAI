"""Semantic validation of contextual advisory model recommendations."""

from __future__ import annotations

from typing import cast

import pytest
from tests.support.recommendations import (
    t27_manual_review_payload,
    t27_payload,
    t27_request,
)

from procurement.agent.recommendation_schema import (
    RECOMMENDATION_JSON_SCHEMA,
    validate_recommendation_payload,
)
from procurement.ports.llm import LlmOutputInvalidError, RecommendationDecision


def test_valid_recommendation_uses_exact_offer_evidence_and_tokens() -> None:
    request = t27_request()

    recommendation = validate_recommendation_payload(
        t27_payload(request), request, 83, 27
    )

    assert recommendation.decision is RecommendationDecision.RECOMMEND
    assert recommendation.product_id == "product-101"
    assert recommendation.offer_id == "offer-101"
    assert recommendation.quantity is not None
    assert recommendation.input_tokens == 83
    assert recommendation.output_tokens == 27


def test_valid_manual_review_selects_no_offer() -> None:
    recommendation = validate_recommendation_payload(
        t27_manual_review_payload(), t27_request(), 10, 5
    )

    assert recommendation.decision is RecommendationDecision.MANUAL_REVIEW
    assert recommendation.product_id is None
    assert recommendation.offer_id is None


def test_provider_schema_enforces_offer_and_explanation_bounds() -> None:
    properties = cast(dict[str, object], RECOMMENDATION_JSON_SCHEMA["properties"])

    assert properties["offer_id"] == {
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
    assert cast(dict[str, object], properties["trade_offs"])["maxItems"] == 6
    assert cast(dict[str, object], properties["risk_flags"])["maxItems"] == 10
    assert RECOMMENDATION_JSON_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("offer_id", "offer-not-eligible"),
        ("quantity", "999999.000000"),
        ("unit_price", "0.000000"),
        ("normalized_cost", "1.000000"),
        ("evidence_digest", "sha256:" + "0" * 64),
        ("budget_status", "exception_required"),
        ("preference_revision", 999),
        ("premium_outcome", "hard_excluded"),
    ),
)
def test_unknown_offer_or_copied_evidence_mismatch_is_rejected(
    field: str, value: object
) -> None:
    request = t27_request()
    payload = t27_payload(request)
    payload[field] = value

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, request, 1, 1)


def test_omitted_required_warning_is_rejected() -> None:
    request = t27_request()
    payload = t27_payload(request)
    payload["risk_flags"] = []

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, request, 1, 1)


def test_malformed_or_extra_schema_is_rejected() -> None:
    request = t27_request()
    payload = t27_payload(request)
    payload["projected_quantity"] = "999999.000000"

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, request, 1, 1)


def test_oversized_recommendation_text_is_rejected() -> None:
    request = t27_request()
    payload = t27_payload(request)
    payload["rationale"] = "x" * 501

    with pytest.raises(LlmOutputInvalidError):
        validate_recommendation_payload(payload, request, 1, 1)
