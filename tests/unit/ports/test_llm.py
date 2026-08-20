"""RecommendationRequest.officer_note bounded-text validation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.support.recommendations import t27_request


def test_officer_note_defaults_to_none() -> None:
    request = t27_request()

    assert request.officer_note is None


def test_officer_note_accepts_bounded_text() -> None:
    request = replace(t27_request(), officer_note="Prioritize delivery speed.")

    assert request.officer_note == "Prioritize delivery speed."


def test_officer_note_accepts_exactly_280_characters() -> None:
    request = replace(t27_request(), officer_note="x" * 280)

    assert request.officer_note == "x" * 280


def test_officer_note_rejects_text_over_280_characters() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        replace(t27_request(), officer_note="x" * 281)


def test_officer_note_rejects_blank_text() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        replace(t27_request(), officer_note="   ")


def test_officer_note_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        replace(t27_request(), officer_note="Avoid vendor\x07 please.")
