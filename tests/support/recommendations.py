"""Shared fictional T27 recommendation fixtures."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from procurement.agent.recommendation_schema import validate_recommendation_payload
from procurement.agent.state import ApprovalReadyResult
from procurement.bootstrap.mcp import _fictional_evidence
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import EvidenceStatus, ProcurementEvidence
from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
    apply_preferences,
)
from procurement.ports.erp import ProcurementEvidenceQuery
from procurement.ports.llm import RecommendationRequest, StructuredRecommendation
from procurement.ports.mcp import ReplenishmentCandidate


def t27_evidence(
    *, product_name: str = "Fictional Safety Gloves"
) -> ProcurementEvidence:
    evidence = replace(
        _fictional_evidence(
            ProcurementEvidenceQuery(Environment.DEV, "product-101", 14)
        ),
        product_name=product_name,
        captured_at=datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
    )
    profile = ProcurementPreference(
        profile_id="preference-1",
        company_id="1",
        category_id="category-safety",
        product_id="product-101",
        scope=PreferenceScope.COMPANY,
        scope_id="1",
        revision=1,
        ordered_criteria=(
            PreferenceCriterion.RELIABILITY,
            PreferenceCriterion.DELIVERY,
            PreferenceCriterion.PRICE,
        ),
        max_price_premium_percent=Decimal("25.000000"),
        enforcement_mode=PremiumEnforcement.ADVISORY,
        precedence_source=PreferenceScope.COMPANY,
    )
    return apply_preferences(evidence, profile)


def t27_request(
    *, product_name: str = "Fictional Safety Gloves"
) -> RecommendationRequest:
    evidence = t27_evidence(product_name=product_name)
    return RecommendationRequest(
        environment=Environment.DEV,
        candidates=(
            ReplenishmentCandidate(
                product_id=evidence.product_id,
                product_name=product_name,
                category_id=evidence.category_id,
                reorder_minimum=Decimal("10.000000"),
                reorder_maximum=Decimal("40.000000"),
                projected_quantity=Decimal("8.000000"),
                projected_trigger_date=date(2026, 8, 9),
                skip_reason_code=None,
            ),
        ),
        preferences=(evidence.preferences,) if evidence.preferences is not None else (),
        evidence=(evidence,),
    )


def t27_payload(request: RecommendationRequest | None = None) -> dict[str, object]:
    resolved = request or t27_request()
    evidence = resolved.evidence[0]
    offer = next(
        item for item in evidence.offers if item.status is EvidenceStatus.ELIGIBLE
    )
    assert evidence.preferences is not None
    premium = next(
        item
        for item in evidence.preferences.offer_results
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
    risk_flags = []
    if evidence.budget is None:
        risk_flags.append("BUDGET_UNAVAILABLE")
    elif evidence.budget.exception_required:
        risk_flags.append("BUDGET_EXCEPTION_REQUIRED")
    if offer.performance.history_status == "limited":
        risk_flags.append("LIMITED_VENDOR_HISTORY")
    if premium.outcome == "advisory_exceeded":
        risk_flags.append("ADVISORY_PREMIUM_EXCEEDED")
    return {
        "decision": "recommend",
        "offer_id": offer.offer_id,
        "rationale": "The eligible offer balances the configured priorities.",
        "trade_offs": ["Reliable delivery is favored over a marginal saving."],
        "risk_flags": risk_flags,
        "uncertainty": "Vendor history is limited to the supplied completed orders.",
        "evidence_limitations": ["No quality or return evidence is available."],
        "evidence_id": evidence.evidence_id,
        "evidence_digest": "sha256:"
        + hashlib.sha256(evidence.canonical_json()).hexdigest(),
        "quantity": format(offer.quantity, "f"),
        "unit_price": format(offer.unit_price, "f"),
        "normalized_cost": format(offer.normalized_cost, "f"),
        "budget_status": budget_status,
        "preference_profile_id": evidence.preferences.profile.profile_id,
        "preference_scope": evidence.preferences.profile.scope.value,
        "preference_revision": evidence.preferences.profile.revision,
        "priority_order": [
            item.value for item in evidence.preferences.profile.ordered_criteria
        ],
        "premium_outcome": premium.outcome,
    }


def t27_manual_review_payload() -> dict[str, object]:
    return {
        "decision": "manual_review",
        "offer_id": None,
        "rationale": "The available evidence requires human judgment.",
        "trade_offs": ["Compare the eligible offers manually."],
        "risk_flags": ["MANUAL_REVIEW_REQUIRED"],
        "uncertainty": "No model selection is available.",
        "evidence_limitations": ["Contextual judgment was declined."],
        "evidence_id": None,
        "evidence_digest": None,
        "quantity": None,
        "unit_price": None,
        "normalized_cost": None,
        "budget_status": "not_evaluated",
        "preference_profile_id": None,
        "preference_scope": None,
        "preference_revision": None,
        "priority_order": [],
        "premium_outcome": None,
    }


def t27_recommendation(
    request: RecommendationRequest | None = None,
) -> StructuredRecommendation:
    resolved = request or t27_request()
    return validate_recommendation_payload(t27_payload(resolved), resolved, 48, 19)


def t27_approval_result() -> ApprovalReadyResult:
    request = t27_request()
    recommendation = t27_recommendation(request)
    evidence = request.evidence[0]
    assert recommendation.offer_id is not None
    assert recommendation.evidence_digest is not None
    assert recommendation.quantity is not None
    assert recommendation.unit_price is not None
    assert recommendation.normalized_cost is not None
    assert recommendation.preference_profile_id is not None
    assert recommendation.preference_scope is not None
    assert recommendation.preference_revision is not None
    assert recommendation.premium_outcome is not None
    return ApprovalReadyResult(
        product_id=evidence.product_id,
        product_name=evidence.product_name,
        offer_id=recommendation.offer_id,
        rationale=recommendation.rationale,
        trade_offs=recommendation.trade_offs,
        risk_flags=recommendation.risk_flags,
        uncertainty=recommendation.uncertainty,
        evidence_limitations=recommendation.evidence_limitations,
        evidence_digest=recommendation.evidence_digest,
        quantity=recommendation.quantity,
        unit_price=recommendation.unit_price,
        normalized_cost=recommendation.normalized_cost,
        budget_status=recommendation.budget_status,
        preference_profile_id=recommendation.preference_profile_id,
        preference_scope=recommendation.preference_scope,
        preference_revision=recommendation.preference_revision,
        priority_order=recommendation.priority_order,
        premium_outcome=recommendation.premium_outcome,
        evidence=evidence,
    )
