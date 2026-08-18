"""Tests for terminal result types in the minimal procurement graph state."""

from __future__ import annotations

import pytest

from procurement.agent.state import NoValidOfferResult


def test_no_valid_offer_result_is_frozen_and_read_only() -> None:
    result = NoValidOfferResult(
        product_id="product-1",
        product_name="Fictional Widget",
        rationale="No approved vendor offer is eligible for this product.",
    )
    assert result.product_id == "product-1"
    assert result.evidence_limitations == ()
    assert result.read_only is True
    with pytest.raises(AttributeError):
        result.product_id = "product-2"  # type: ignore[misc]
