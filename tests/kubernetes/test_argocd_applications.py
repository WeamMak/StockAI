"""Declarative Argo CD application contracts."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dev_application_tracks_dev_overlay_and_uses_automated_gitops() -> None:
    argocd_root = PROJECT_ROOT / "deploy/kubernetes/cluster/argocd"
    path = argocd_root / "dev-application.yaml"
    application = yaml.safe_load(path.read_text(encoding="utf-8"))
    kustomization = yaml.safe_load(
        (argocd_root / "kustomization.yaml").read_text(encoding="utf-8")
    )

    assert "dev-application.yaml" in kustomization["resources"]

    assert application["apiVersion"] == "argoproj.io/v1alpha1"
    assert application["kind"] == "Application"
    assert application["metadata"] == {
        "name": "stockai-dev",
        "namespace": "argocd",
    }
    source = application["spec"]["source"]
    assert source["repoURL"] == "https://github.com/WeamMak/StockAI.git"
    assert source["targetRevision"] == "dev"
    assert source["path"] == "deploy/kubernetes/overlays/dev"
    assert application["spec"]["destination"] == {
        "server": "https://kubernetes.default.svc",
        "namespace": "stockai-dev",
    }
    automated = application["spec"]["syncPolicy"]["automated"]
    assert automated == {"prune": True, "selfHeal": True}
