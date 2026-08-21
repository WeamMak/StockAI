"""Strict structured-output boundary for contextual procurement recommendations."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from decimal import Decimal
from importlib.resources import files
from typing import Any

from procurement.domain.policy.evidence import (
    EvidenceStatus,
    OfferEvidence,
    ProcurementEvidence,
)
from procurement.ports.llm import (
    LlmOutputInvalidError,
    RecommendationDecision,
    RecommendationRequest,
    StructuredRecommendation,
    required_risk_flags,
)

_REQUIRED_FIELDS = {
    "decision",
    "offer_id",
    "rationale",
    "trade_offs",
    "risk_flags",
    "uncertainty",
    "evidence_limitations",
    "evidence_id",
    "evidence_digest",
    "quantity",
    "unit_price",
    "normalized_cost",
    "budget_status",
    "preference_profile_id",
    "preference_scope",
    "preference_revision",
    "priority_order",
    "premium_outcome",
}
_SCHEMA_TOKEN = re.compile(r"[a-z][a-z0-9_]*")
_EXPLANATION_PLACEHOLDERS = frozenset({"none", "n/a", "not applicable"})
_IDENTIFIER = {
    "type": ["string", "null"],
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}
_DECIMAL = {
    "type": ["string", "null"],
    "pattern": r"^(0|[1-9][0-9]{0,11})\.[0-9]{6}$",
}
_TEXT_LIST = {
    "type": "array",
    "maxItems": 6,
    "items": {"type": "string", "minLength": 1, "maxLength": 240},
}

RECOMMENDATION_JSON_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["recommend", "manual_review"]},
        "offer_id": _IDENTIFIER,
        "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
        "trade_offs": {**_TEXT_LIST, "minItems": 1},
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
        "uncertainty": {"type": "string", "minLength": 1, "maxLength": 240},
        "evidence_limitations": _TEXT_LIST,
        "evidence_id": _IDENTIFIER,
        "evidence_digest": {
            "type": ["string", "null"],
            "pattern": r"^sha256:[0-9a-f]{64}$",
        },
        "quantity": _DECIMAL,
        "unit_price": _DECIMAL,
        "normalized_cost": _DECIMAL,
        "budget_status": {
            "type": "string",
            "enum": [
                "within_budget",
                "exception_required",
                "unavailable",
                "not_evaluated",
            ],
        },
        "preference_profile_id": _IDENTIFIER,
        "preference_scope": {
            "type": ["string", "null"],
            "enum": ["company", "category", "product", None],
        },
        "preference_revision": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "priority_order": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "enum": ["price", "delivery", "reliability"]},
        },
        "premium_outcome": {
            "type": ["string", "null"],
            "enum": ["within_cap", "advisory_exceeded", "hard_excluded", None],
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


def _selected_evidence(
    request: RecommendationRequest,
    offer_id: str,
) -> tuple[ProcurementEvidence, OfferEvidence]:
    matches = [
        (evidence, offer)
        for evidence in request.evidence
        if evidence.skip_reason_code is None
        for offer in evidence.offers
        if offer.status is EvidenceStatus.ELIGIBLE and offer.offer_id == offer_id
    ]
    if len(matches) != 1:
        raise ValueError("the selected offer is not uniquely eligible")
    return matches[0]


def _normal_text_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} is invalid")
    return tuple(value)


def _looks_like_schema_field_dump(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _REQUIRED_FIELDS:
        return True
    schema_tokens = [
        token
        for token in _SCHEMA_TOKEN.findall(normalized)
        if token in _REQUIRED_FIELDS
    ]
    return len(schema_tokens) >= 2


def _is_explanation_placeholder(value: str) -> bool:
    return value.strip().lower().removesuffix(".") in _EXPLANATION_PLACEHOLDERS


def validate_recommendation_payload(
    payload: Mapping[str, object],
    request: RecommendationRequest,
    input_tokens: int,
    output_tokens: int,
    *,
    retry_count: int = 0,
    repair_attempted: bool = False,
) -> StructuredRecommendation:
    """Validate every model-selected and copied value against authoritative evidence."""

    try:
        if set(payload) != _REQUIRED_FIELDS:
            raise ValueError("recommendation fields are invalid")
        decision_value = payload["decision"]
        if not isinstance(decision_value, str):
            raise ValueError("recommendation decision is invalid")
        decision = RecommendationDecision(decision_value)
        rationale = payload["rationale"]
        uncertainty = payload["uncertainty"]
        if not isinstance(rationale, str) or not isinstance(uncertainty, str):
            raise ValueError("recommendation text is invalid")
        trade_offs = _normal_text_list(payload["trade_offs"], field="trade_offs")
        risk_flags = _normal_text_list(payload["risk_flags"], field="risk_flags")
        limitations = _normal_text_list(
            payload["evidence_limitations"], field="evidence_limitations"
        )
        if any(
            _looks_like_schema_field_dump(value) or _is_explanation_placeholder(value)
            for value in (rationale, uncertainty, *trade_offs, *limitations)
        ):
            raise ValueError("recommendation explanation is not user-facing prose")

        if decision is RecommendationDecision.MANUAL_REVIEW:
            selected_fields = (
                "offer_id",
                "evidence_id",
                "evidence_digest",
                "quantity",
                "unit_price",
                "normalized_cost",
                "preference_profile_id",
                "preference_scope",
                "preference_revision",
                "premium_outcome",
            )
            if (
                any(payload[field] is not None for field in selected_fields)
                or payload["priority_order"] != []
                or payload["budget_status"] != "not_evaluated"
            ):
                raise ValueError("manual review cannot select or copy offer evidence")
            return StructuredRecommendation(
                decision=decision,
                product_id=None,
                rationale=rationale,
                risk_flags=risk_flags,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                trade_offs=trade_offs,
                uncertainty=uncertainty,
                evidence_limitations=limitations,
                retry_count=retry_count,
                repair_attempted=repair_attempted,
            )

        offer_id = payload["offer_id"]
        if not isinstance(offer_id, str):
            raise ValueError("recommendation offer identifier is invalid")
        evidence, offer = _selected_evidence(request, offer_id)
        preferences = evidence.preferences
        if preferences is None:
            raise ValueError("validated preferences are required")
        premium = next(
            item
            for item in preferences.offer_results
            if item.offer_id == offer.offer_id
        )
        budget_status = (
            "unavailable"
            if evidence.budget is None
            else (
                "exception_required"
                if evidence.budget.exception_required
                else "within_budget"
            )
        )
        evidence_digest = (
            "sha256:" + hashlib.sha256(evidence.canonical_json()).hexdigest()
        )
        exact_values: dict[str, Any] = {
            "evidence_id": evidence.evidence_id,
            "evidence_digest": evidence_digest,
            "quantity": format(offer.quantity, "f"),
            "unit_price": format(offer.unit_price, "f"),
            "normalized_cost": format(offer.normalized_cost, "f"),
            "budget_status": budget_status,
            "preference_profile_id": preferences.profile.profile_id,
            "preference_scope": preferences.profile.scope.value,
            "preference_revision": preferences.profile.revision,
            "priority_order": [
                item.value for item in preferences.profile.ordered_criteria
            ],
            "premium_outcome": premium.outcome,
        }
        if any(payload[field] != value for field, value in exact_values.items()):
            raise ValueError("recommendation copied evidence does not match")
        if not set(required_risk_flags(evidence, offer)).issubset(set(risk_flags)):
            raise ValueError("recommendation omitted a required warning")

        return StructuredRecommendation(
            decision=decision,
            product_id=evidence.product_id,
            rationale=rationale,
            risk_flags=risk_flags,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            offer_id=offer.offer_id,
            trade_offs=trade_offs,
            uncertainty=uncertainty,
            evidence_limitations=limitations,
            evidence_id=evidence.evidence_id,
            evidence_digest=evidence_digest,
            quantity=Decimal(str(payload["quantity"])),
            unit_price=Decimal(str(payload["unit_price"])),
            normalized_cost=Decimal(str(payload["normalized_cost"])),
            budget_status=budget_status,
            preference_profile_id=preferences.profile.profile_id,
            preference_scope=preferences.profile.scope.value,
            preference_revision=preferences.profile.revision,
            priority_order=tuple(
                item.value for item in preferences.profile.ordered_criteria
            ),
            premium_outcome=premium.outcome,
            retry_count=retry_count,
            repair_attempted=repair_attempted,
        )
    except (StopIteration, TypeError, ValueError) as error:
        raise LlmOutputInvalidError(error) from None
