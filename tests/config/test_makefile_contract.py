"""Contracts for stable repository validation targets."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = PROJECT_ROOT / "Makefile"


def _recipe(target: str) -> str:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n(?P<recipe>(?:\t[^\n]*\n)+)",
        makefile,
        flags=re.MULTILINE,
    )
    assert match is not None, f"Makefile target is missing: {target}"
    return match.group("recipe")


def test_make_lint_runs_the_pinned_frontend_eslint_command() -> None:
    assert "npm --prefix frontend run lint" in _recipe("lint")
