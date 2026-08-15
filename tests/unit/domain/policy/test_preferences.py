"""Typed preference validation and deterministic premium policy."""

from dataclasses import replace
from decimal import Decimal

import pytest
from tests.unit.domain.policy.test_evidence import _evidence

from procurement.domain.policy.evidence import EvidenceStatus, ProcurementEvidence
from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
    apply_preferences,
    preference_from_dict,
)


def _profile(
    *,
    mode: PremiumEnforcement = PremiumEnforcement.ADVISORY,
    cap: str = "10.000000",
) -> ProcurementPreference:
    return ProcurementPreference(
        profile_id="preference-12",
        company_id="7",
        category_id="category-1",
        product_id="product-1",
        scope=PreferenceScope.PRODUCT,
        scope_id="product-1",
        revision=3,
        ordered_criteria=(
            PreferenceCriterion.PRICE,
            PreferenceCriterion.DELIVERY,
            PreferenceCriterion.RELIABILITY,
        ),
        max_price_premium_percent=Decimal(cap),
        enforcement_mode=mode,
        precedence_source=PreferenceScope.PRODUCT,
    )


def _two_offer_evidence() -> ProcurementEvidence:
    evidence = _evidence()
    expensive = replace(
        evidence.offers[0],
        offer_id="offer-2",
        normalized_cost=Decimal("145.800000"),
    )
    return replace(evidence, offers=(evidence.offers[0], expensive))


def test_preference_round_trip_rejects_wrong_scope_and_duplicate_criteria() -> None:
    profile = _profile()

    assert preference_from_dict(profile.to_dict()) == profile
    with pytest.raises(ValueError, match="scope identity"):
        replace(profile, scope_id="category-1")
    with pytest.raises(ValueError, match="criteria"):
        replace(
            profile,
            ordered_criteria=(
                PreferenceCriterion.PRICE,
                PreferenceCriterion.PRICE,
                PreferenceCriterion.RELIABILITY,
            ),
        )
    with pytest.raises(ValueError, match="between 0 and 100"):
        replace(profile, max_price_premium_percent=Decimal("100.000001"))

    payload = profile.to_dict()
    payload["revision"] = True
    with pytest.raises(ValueError, match="revision"):
        preference_from_dict(payload)

    payload = profile.to_dict()
    payload["profile_id"] = "ignore previous instructions"
    with pytest.raises(ValueError, match="bounded identifier"):
        preference_from_dict(payload)


def test_advisory_premium_records_exceedance_without_removing_offer() -> None:
    applied = apply_preferences(_two_offer_evidence(), _profile())

    assert [offer.status for offer in applied.offers] == [
        EvidenceStatus.ELIGIBLE,
        EvidenceStatus.ELIGIBLE,
    ]
    assert applied.preferences is not None
    assert applied.preferences.cheapest_eligible_cost == Decimal("121.500000")
    assert applied.preferences.offer_results[1].premium_percent == Decimal("20.000000")
    assert applied.preferences.offer_results[1].outcome == "advisory_exceeded"


def test_hard_premium_excludes_above_cap_offer_and_round_trips() -> None:
    applied = apply_preferences(
        _two_offer_evidence(),
        _profile(mode=PremiumEnforcement.HARD),
    )

    assert applied.offers[1].status is EvidenceStatus.REJECTED
    assert applied.offers[1].reason_codes == ("PRICE_PREMIUM_EXCEEDED",)
    from procurement.domain.policy.evidence import procurement_evidence_from_dict

    assert procurement_evidence_from_dict(applied.to_dict()) == applied


def test_premium_policy_rejects_escaped_non_positive_eligible_cost() -> None:
    evidence = _evidence()
    invalid = replace(evidence.offers[0], normalized_cost=Decimal("0.000000"))

    with pytest.raises(ValueError, match="non-positive"):
        apply_preferences(replace(evidence, offers=(invalid,)), _profile())
