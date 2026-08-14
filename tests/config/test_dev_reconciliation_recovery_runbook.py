"""Contracts for the bounded one-time dev reconciliation recovery."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "dev-reconciliation-recovery.md"


def test_runbook_is_bounded_to_the_three_dev_storage_sets() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for name in (
        "stockai-dev-odoo-filestore",
        "stockai-dev-postgresql-data",
        "stockai-dev-prometheus-data",
        "odoo-filestore",
        "postgresql-data",
        "prometheus-data",
    ):
        assert name in text
    for volume_id in (
        "vol-051d6c42ca98f0b15",
        "vol-0491b34550d11b018",
        "vol-01ab986773724a6b1",
    ):
        assert volume_id in text
    assert "pause automated reconciliation" in text
    assert "restore automated reconciliation" in text
    assert "Retain" in text


def test_runbook_forbids_destructive_or_production_operations() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for forbidden in (
        "kubectl delete namespace",
        "aws ec2 delete-volume",
        "stockai-prod-",
        "--force",
    ):
        assert forbidden not in text


def test_runbook_contains_required_preflight_and_acceptance_checks() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "aws ec2 describe-volumes",
        "kubectl get volumeattachments",
        "kubectl -n dev get secretstore,externalsecret",
        "kubectl -n argocd get application stockai-dev",
        "Synced",
        "Healthy",
    ):
        assert required in text
