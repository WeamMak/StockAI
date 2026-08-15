"""Typed recommendation preferences and deterministic premium enforcement."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from procurement.domain.policy.evidence import ProcurementEvidence

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_QUANTUM = Decimal("0.000001")


class PreferenceScope(StrEnum):
    COMPANY = "company"
    CATEGORY = "category"
    PRODUCT = "product"


class PreferenceCriterion(StrEnum):
    PRICE = "price"
    DELIVERY = "delivery"
    RELIABILITY = "reliability"


class PremiumEnforcement(StrEnum):
    ADVISORY = "advisory"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class ProcurementPreference:
    """One resolved, strictly validated current preference profile."""

    profile_id: str
    company_id: str
    category_id: str
    product_id: str
    scope: PreferenceScope
    scope_id: str
    revision: int
    ordered_criteria: tuple[PreferenceCriterion, ...]
    max_price_premium_percent: Decimal
    enforcement_mode: PremiumEnforcement
    precedence_source: PreferenceScope

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "company_id",
            "category_id",
            "product_id",
            "scope_id",
        ):
            if _IDENTIFIER.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"{name} must be a bounded identifier")
        if not isinstance(self.scope, PreferenceScope):
            raise ValueError("preference scope is invalid")
        expected_scope_id = {
            PreferenceScope.COMPANY: self.company_id,
            PreferenceScope.CATEGORY: self.category_id,
            PreferenceScope.PRODUCT: self.product_id,
        }[self.scope]
        if self.scope_id != expected_scope_id:
            raise ValueError("preference scope identity is invalid")
        if self.precedence_source is not self.scope:
            raise ValueError("preference precedence source is invalid")
        if type(self.revision) is not int or not 1 <= self.revision <= 2_147_483_647:
            raise ValueError("preference revision must be positive")
        if (
            not isinstance(self.ordered_criteria, tuple)
            or len(self.ordered_criteria) != 3
            or set(self.ordered_criteria) != set(PreferenceCriterion)
        ):
            raise ValueError("preference criteria must order each supported criterion")
        premium = self.max_price_premium_percent
        if (
            not isinstance(premium, Decimal)
            or not premium.is_finite()
            or not Decimal("0") <= premium <= Decimal("100")
            or premium.quantize(_QUANTUM) != premium
        ):
            raise ValueError("maximum price premium must be between 0 and 100 percent")
        if not isinstance(self.enforcement_mode, PremiumEnforcement):
            raise ValueError("premium enforcement mode is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "company_id": self.company_id,
            "category_id": self.category_id,
            "product_id": self.product_id,
            "scope": self.scope.value,
            "scope_id": self.scope_id,
            "revision": self.revision,
            "ordered_criteria": [item.value for item in self.ordered_criteria],
            "max_price_premium_percent": format(self.max_price_premium_percent, "f"),
            "enforcement_mode": self.enforcement_mode.value,
            "precedence_source": self.precedence_source.value,
        }


@dataclass(frozen=True, slots=True)
class OfferPremiumResult:
    """Premium outcome for one otherwise-eligible offer."""

    offer_id: str
    premium_percent: Decimal
    exceeds_cap: bool
    outcome: str

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.offer_id) is None:
            raise ValueError("premium offer ID is invalid")
        if (
            not isinstance(self.premium_percent, Decimal)
            or not self.premium_percent.is_finite()
            or self.premium_percent < 0
            or self.premium_percent.quantize(_QUANTUM) != self.premium_percent
        ):
            raise ValueError("premium percentage is invalid")
        if type(self.exceeds_cap) is not bool:
            raise ValueError("premium exceeds_cap is invalid")
        if self.outcome not in {"within_cap", "advisory_exceeded", "hard_excluded"}:
            raise ValueError("premium outcome is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "offer_id": self.offer_id,
            "premium_percent": format(self.premium_percent, "f"),
            "exceeds_cap": self.exceeds_cap,
            "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class AppliedPreferences:
    """Immutable resolved profile plus its deterministic offer results."""

    profile: ProcurementPreference
    cheapest_eligible_cost: Decimal
    offer_results: tuple[OfferPremiumResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ProcurementPreference):
            raise ValueError("applied preference profile is invalid")
        if (
            not isinstance(self.cheapest_eligible_cost, Decimal)
            or not self.cheapest_eligible_cost.is_finite()
            or self.cheapest_eligible_cost <= 0
            or self.cheapest_eligible_cost.quantize(_QUANTUM)
            != self.cheapest_eligible_cost
        ):
            raise ValueError("cheapest eligible cost must be positive")
        if (
            not isinstance(self.offer_results, tuple)
            or not self.offer_results
            or not all(
                isinstance(item, OfferPremiumResult) for item in self.offer_results
            )
            or len({item.offer_id for item in self.offer_results})
            != len(self.offer_results)
        ):
            raise ValueError("offer premium results are invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            **self.profile.to_dict(),
            "cheapest_eligible_cost": format(self.cheapest_eligible_cost, "f"),
            "offer_results": [item.to_dict() for item in self.offer_results],
        }


def apply_preferences(
    evidence: ProcurementEvidence,
    profile: ProcurementPreference,
) -> ProcurementEvidence:
    """Apply the bounded premium policy to otherwise-eligible offers."""

    from procurement.domain.policy.evidence import EvidenceStatus, OfferEvidence

    if (
        evidence.product_id != profile.product_id
        or evidence.category_id != profile.category_id
    ):
        raise ValueError("preference does not match procurement evidence")
    eligible = tuple(
        offer for offer in evidence.offers if offer.status is EvidenceStatus.ELIGIBLE
    )
    if not eligible:
        raise ValueError("premium policy requires an otherwise-eligible offer")
    if any(offer.normalized_cost <= 0 for offer in eligible):
        raise ValueError("premium policy rejects non-positive eligible costs")
    baseline = min(offer.normalized_cost for offer in eligible)
    results: list[OfferPremiumResult] = []
    replacements: dict[str, OfferEvidence] = {}
    for offer in eligible:
        premium = (
            ((offer.normalized_cost - baseline) / baseline) * Decimal("100")
        ).quantize(_QUANTUM)
        exceeds = premium > profile.max_price_premium_percent
        if exceeds and profile.enforcement_mode is PremiumEnforcement.HARD:
            outcome = "hard_excluded"
            replacements[offer.offer_id] = replace(
                offer,
                status=EvidenceStatus.REJECTED,
                reason_codes=("PRICE_PREMIUM_EXCEEDED",),
            )
        elif exceeds:
            outcome = "advisory_exceeded"
        else:
            outcome = "within_cap"
        results.append(
            OfferPremiumResult(
                offer_id=offer.offer_id,
                premium_percent=premium,
                exceeds_cap=exceeds,
                outcome=outcome,
            )
        )
    return replace(
        evidence,
        offers=tuple(
            replacements.get(offer.offer_id, offer) for offer in evidence.offers
        ),
        preferences=AppliedPreferences(
            profile=profile,
            cheapest_eligible_cost=baseline,
            offer_results=tuple(results),
        ),
    )


def preference_from_dict(raw: object) -> ProcurementPreference:
    """Reconstruct one strict profile from untrusted MCP content."""

    expected = {
        "profile_id",
        "company_id",
        "category_id",
        "product_id",
        "scope",
        "scope_id",
        "revision",
        "ordered_criteria",
        "max_price_premium_percent",
        "enforcement_mode",
        "precedence_source",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("preference payload is invalid")
    criteria = raw["ordered_criteria"]
    string_fields = (
        "profile_id",
        "company_id",
        "category_id",
        "product_id",
        "scope",
        "scope_id",
        "max_price_premium_percent",
        "enforcement_mode",
        "precedence_source",
    )
    if not all(isinstance(raw[field], str) for field in string_fields):
        raise ValueError("preference fields are invalid")
    if not isinstance(criteria, list) or not all(
        isinstance(item, str) for item in criteria
    ):
        raise ValueError("preference criteria are invalid")
    if type(raw["revision"]) is not int:
        raise ValueError("preference revision is invalid")
    return ProcurementPreference(
        profile_id=raw["profile_id"],
        company_id=raw["company_id"],
        category_id=raw["category_id"],
        product_id=raw["product_id"],
        scope=PreferenceScope(raw["scope"]),
        scope_id=raw["scope_id"],
        revision=raw["revision"],
        ordered_criteria=tuple(PreferenceCriterion(item) for item in criteria),
        max_price_premium_percent=Decimal(raw["max_price_premium_percent"]),
        enforcement_mode=PremiumEnforcement(raw["enforcement_mode"]),
        precedence_source=PreferenceScope(raw["precedence_source"]),
    )
