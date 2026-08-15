"""Strict Odoo preference precedence and validation."""

from copy import deepcopy

import pytest

from procurement.adapters.odoo.preference_mapper import map_effective_preference
from procurement.domain.policy.preferences import PreferenceScope


def _records() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    profiles = [
        {
            "id": 1,
            "company_id": [7, "Fictional Company"],
            "scope": "company",
            "product_category_id": False,
            "product_id": False,
            "revision": 2,
            "max_price_premium_percent": 25.0,
            "enforcement_mode": "advisory",
            "active": True,
            "priority_ids": [11, 12, 13],
        },
        {
            "id": 2,
            "company_id": [7, "Fictional Company"],
            "scope": "category",
            "product_category_id": [8, "Critical"],
            "product_id": False,
            "revision": 4,
            "max_price_premium_percent": 15.0,
            "enforcement_mode": "hard",
            "active": True,
            "priority_ids": [21, 22, 23],
        },
        {
            "id": 3,
            "company_id": [7, "Fictional Company"],
            "scope": "product",
            "product_category_id": False,
            "product_id": [9, "Component"],
            "revision": 6,
            "max_price_premium_percent": 10.0,
            "enforcement_mode": "advisory",
            "active": True,
            "priority_ids": [31, 32, 33],
        },
    ]
    criteria = {
        1: ["reliability", "delivery", "price"],
        2: ["delivery", "reliability", "price"],
        3: ["price", "reliability", "delivery"],
    }
    priorities = [
        {
            "id": profile_id * 10 + sequence,
            "preference_id": [profile_id, "Preference"],
            "sequence": sequence * 10,
            "criterion": criterion,
        }
        for profile_id, ordered in criteria.items()
        for sequence, criterion in enumerate(ordered, start=1)
    ]
    return profiles, priorities


@pytest.mark.parametrize(
    ("category_id", "product_id", "scope", "profile_id"),
    [
        (8, 9, PreferenceScope.PRODUCT, "preference-3"),
        (8, 10, PreferenceScope.CATEGORY, "preference-2"),
        (80, 10, PreferenceScope.COMPANY, "preference-1"),
    ],
)
def test_mapper_resolves_product_category_company_precedence(
    category_id: int,
    product_id: int,
    scope: PreferenceScope,
    profile_id: str,
) -> None:
    profiles, priorities = _records()

    result = map_effective_preference(
        profiles=profiles,
        priorities=priorities,
        company_id=7,
        category_id=category_id,
        product_id=product_id,
    )

    assert result.scope is scope
    assert result.profile_id == profile_id
    assert result.precedence_source is scope


def test_mapper_rejects_malformed_revision_scope_and_priorities() -> None:
    profiles, priorities = _records()
    invalid_revision = deepcopy(profiles)
    invalid_revision[2]["revision"] = 0
    with pytest.raises(ValueError, match="invalid"):
        map_effective_preference(
            profiles=invalid_revision,
            priorities=priorities,
            company_id=7,
            category_id=8,
            product_id=9,
        )

    missing_priority = priorities[:-1]
    with pytest.raises(ValueError, match="invalid"):
        map_effective_preference(
            profiles=profiles,
            priorities=missing_priority,
            company_id=7,
            category_id=8,
            product_id=9,
        )
