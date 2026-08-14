"""Safety contract for exact-release production promotion and rollback."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = PROJECT_ROOT / "docs/runbooks/prod-promotion.md"


def test_prod_promotion_runbook_preserves_same_digest_gitops_boundaries() -> None:
    source = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "make promote-dev",
        "make verify-release",
        "make kubernetes-validate",
        "make smoke-prod",
        "byte-for-byte identical",
        "Git revert",
        "previously verified prod release",
        "Argo CD",
    ):
        assert required in source
    for forbidden in (
        "docker build",
        "docker tag",
        "kubectl apply",
        "kubectl set image",
        "argocd app sync",
    ):
        assert forbidden not in source.lower()
