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


def test_make_lint_runs_actionlint_after_workflows_exist() -> None:
    assert "$(ACTIONLINT) .github/workflows/*.yml" in _recipe("lint")


def test_promote_dev_is_a_local_preparation_command() -> None:
    recipe = _recipe("promote-dev")
    source = (PROJECT_ROOT / "scripts" / "release" / "promote_dev.py").read_text(
        encoding="utf-8"
    )

    assert "scripts.release.promote_dev" in recipe
    for forbidden in ("commit", "push", "merge", "aws ", "terraform", "apply"):
        assert forbidden not in recipe.lower()
    for forbidden in (
        "boto3",
        "terraform",
        "kubectl apply",
        "kubectl create",
        "kubectl patch",
        "kubectl set",
    ):
        assert forbidden not in source.lower()


def test_smoke_dev_runs_only_the_bounded_t23_script() -> None:
    assert "bash scripts/smoke/dev.sh" in _recipe("smoke-dev")


def test_smoke_prod_runs_only_the_bounded_t24_script() -> None:
    assert "bash scripts/smoke/prod.sh" in _recipe("smoke-prod")
    source = (PROJECT_ROOT / "scripts/smoke/prod.sh").read_text(encoding="utf-8")

    assert "tests/smoke/test_prod_skeleton.py" in source
    assert "STOCKAI_PROD_SESSION_TOKEN" in source
    assert "scripts.release.record_validation" not in source


def test_verify_release_requires_exact_prod_promotion_when_prod_exists() -> None:
    recipe = _recipe("verify-release")

    assert "--promoted-from deploy/releases/dev.json" in recipe
