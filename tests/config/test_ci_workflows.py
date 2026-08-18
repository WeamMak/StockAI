"""Static contracts for the T21 GitHub Actions workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict[str, Any]:
    document = yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(document, dict)
    return document


def test_pull_request_checks_cover_required_offline_stages() -> None:
    workflow = _workflow("pr-checks.yml")

    assert "pull_request" in workflow["on"]
    assert set(workflow["jobs"]) == {
        "containers",
        "docker-scout",
        "infrastructure",
        "release",
        "secrets",
        "tests",
    }
    source = (WORKFLOWS / "pr-checks.yml").read_text(encoding="utf-8")
    for command in (
        "make lock-check format-check lint",
        "make test-unit",
        "make test-integration",
        "make test-e2e",
        "make compose-validate",
        "make build",
        "make odoo-contract",
        "make terraform-validate",
        "make kubernetes-validate",
    ):
        assert command in source
    assert "zricethezav/gitleaks:v8.28.0" in source


def test_pull_request_checks_pin_uv_and_prepare_fictional_compose_values() -> None:
    source = (WORKFLOWS / "pr-checks.yml").read_text(encoding="utf-8")

    assert "astral-sh/setup-uv@v9" not in source
    assert (
        source.count("astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9") == 4
    )
    assert source.count("run: cp .env.example .env") == 2


def test_secret_scan_baselines_only_reviewed_fingerprints() -> None:
    fingerprints = {
        line
        for line in (PROJECT_ROOT / ".gitleaksignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    }

    assert len(fingerprints) == 11
    assert (
        "c123a64e8f27f948855f315c5806637e7cdcae04:"
        "tests/unit/infra/test_cluster_platform.py:generic-api-key:56"
    ) in fingerprints
    assert {
        (
            "a35ec340f5eb02da1e91ea7e3f6a42ee601e7fed:"
            ".claude/skills/kubernetes-specialist/references/"
            "configuration.md:generic-api-key:75"
        ),
        (
            "a35ec340f5eb02da1e91ea7e3f6a42ee601e7fed:"
            ".claude/skills/kubernetes-specialist/references/"
            "configuration.md:generic-api-key:120"
        ),
        (
            "a35ec340f5eb02da1e91ea7e3f6a42ee601e7fed:"
            ".claude/skills/kubernetes-specialist/references/"
            "configuration.md:private-key:98"
        ),
    } <= fingerprints
    assert all(fingerprint.count(":") >= 3 for fingerprint in fingerprints)


def test_scout_runs_only_for_main_pull_requests_and_all_four_images() -> None:
    job = _workflow("pr-checks.yml")["jobs"]["docker-scout"]

    assert "base.ref == 'main'" in job["if"]
    matrix = job["strategy"]["matrix"]["include"]
    assert {entry["name"] for entry in matrix} == {
        "api",
        "frontend",
        "mcp",
        "odoo",
    }
    assert all(entry["image"] for entry in matrix)
    scout_step = next(step for step in job["steps"] if step.get("id") == "scout")
    assert scout_step["with"]["exit-code"] == "false"


def test_terraform_plans_are_path_filtered_and_use_read_only_oidc() -> None:
    workflow = _workflow("terraform-plan.yml")
    source = (WORKFLOWS / "terraform-plan.yml").read_text(encoding="utf-8")

    assert "infra/terraform/**" in workflow["on"]["pull_request"]["paths"]
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    assert "AWS_TERRAFORM_PLAN_ROLE_ARN" in source
    assert "terraform plan -refresh=false -no-color -out=tfplan" in source
    assert "terraform apply" not in source
    assert "scripts.infra.provision" in source
    assert "ci.auto.tfvars.json" in source
    assert "TERRAFORM_PLATFORM_TFVARS_JSON" not in source
    assert "TERRAFORM_EDGE_TFVARS_JSON" not in source
    assert "TERRAFORM_DEV_TFVARS_JSON" not in source
    assert "TERRAFORM_PROD_TFVARS_JSON" not in source


def test_terraform_apply_is_manual_protected_and_consumes_a_saved_plan() -> None:
    workflow = _workflow("terraform-apply.yml")
    source = (WORKFLOWS / "terraform-apply.yml").read_text(encoding="utf-8")

    assert set(workflow["on"]) == {"workflow_dispatch"}
    job = workflow["jobs"]["apply"]
    assert job["environment"] == "${{ inputs.environment }}"
    assert "AWS_TERRAFORM_APPLY_ROLE_ARN" in source
    assert "plan_run_id" in source
    assert "merged_at != null" in source
    assert "terraform apply -no-color" in source
    assert "terraform plan" not in source


def test_t21b_lifecycle_workflows_are_manual_protected_and_keyless() -> None:
    expected = {
        "terraform-provision.yml": "infrastructure-provision",
        "terraform-destroy.yml": "infrastructure-destroy",
    }

    for name, environment in expected.items():
        workflow = _workflow(name)
        source = (WORKFLOWS / name).read_text(encoding="utf-8")

        assert set(workflow["on"]) == {"workflow_dispatch"}
        assert workflow["permissions"] == {
            "contents": "read",
            "id-token": "write",
        }
        assert environment in source
        assert "AWS_TERRAFORM_APPLY_ROLE_ARN" in source
        assert "aws-actions/configure-aws-credentials" in source
        assert "AWS_ACCESS_KEY_ID" not in source
        assert "AWS_SECRET_ACCESS_KEY" not in source
        assert "ssh" not in source.lower()
        assert "kubectl" not in source
        assert "schedule:" not in source
        assert "push:" not in source
        assert "pull_request:" not in source
        assert "infra/terraform/bootstrap" not in source
        assert "terraform apply -auto-approve" not in source


def test_t21b_provision_uses_saved_plans_in_dependency_order() -> None:
    workflow = _workflow("terraform-provision.yml")
    source = (WORKFLOWS / "terraform-provision.yml").read_text(encoding="utf-8")

    positions = [
        source.index(f"apply-{root}:") for root in ("platform", "edge", "dev", "prod")
    ]
    assert positions == sorted(positions)
    assert "needs: apply-platform" in source
    assert "needs: apply-edge" in source
    assert "needs: apply-dev" in source
    assert source.index("cluster-platform:") > source.index("apply-prod:")
    assert "terraform plan -out=tfplan" in source
    assert "-refresh=false" not in source
    assert "sha256sum tfplan" in source
    assert "terraform apply -no-color tfplan" in source
    assert "scripts.infra.provision" in source
    assert "verify-runner" in source
    assert "capture-output" in source
    assert "sync-outputs" in source
    assert "scripts.infra.cluster_platform" in source
    assert "install" in source
    assert 'expected="provision ${DEPLOYMENT} in ${ACCOUNT}"' in source
    platform_step = next(
        step
        for step in workflow["jobs"]["cluster-platform"]["steps"]
        if step.get("name")
        == "Install and verify the shared Kubernetes platform through SSM"
    )
    assert platform_step["run"].startswith("set -euo pipefail\n")


def test_t21b_destroy_is_reverse_order_and_preserves_bootstrap() -> None:
    source = (WORKFLOWS / "terraform-destroy.yml").read_text(encoding="utf-8")

    positions = [
        source.index(f"apply-{root}:") for root in ("prod", "dev", "edge", "platform")
    ]
    assert positions == sorted(positions)
    assert "needs: quiesce" in source
    assert "plan-prod-deletion-protection:" in source
    assert "apply-prod-deletion-protection:" in source
    assert "needs: apply-prod-deletion-protection" in source
    assert "-var=enable_cognito_deletion_protection=false" in source
    assert "terraform apply -no-color deactivate.tfplan" in source
    assert "needs: apply-prod" in source
    assert "needs: apply-dev" in source
    assert "needs: apply-edge" in source
    assert "terraform plan -destroy -out=tfplan" in source
    assert "-refresh=false" not in source
    assert "terraform apply -no-color tfplan" in source
    assert "scripts.infra.cluster_platform" in source
    assert "quiesce" in source
    assert 'expected="destroy ${DEPLOYMENT} in ${ACCOUNT}"' in source
    for protected in (
        "terraform-state",
        "terraform-lock",
        "github_actions",
        "github_plan",
        "github_apply",
    ):
        assert protected not in source


def test_every_workflow_retains_reports_or_publishes_a_summary() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        source = path.read_text(encoding="utf-8")
        assert "GITHUB_STEP_SUMMARY" in source
    assert "actions/upload-artifact@v6" in (WORKFLOWS / "pr-checks.yml").read_text(
        encoding="utf-8"
    )
    assert "actions/upload-artifact@v6" in (WORKFLOWS / "terraform-plan.yml").read_text(
        encoding="utf-8"
    )


def test_dev_images_builds_changed_images_and_updates_only_git_desired_state() -> None:
    workflow = _workflow("dev-images.yml")
    source = (WORKFLOWS / "dev-images.yml").read_text(encoding="utf-8")

    assert workflow["on"]["push"]["branches"] == ["dev"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["desired-state"]["permissions"] == {"contents": "write"}
    assert "github-actions[bot]" in source
    assert "[skip dev-images]" in source
    assert "[record dev-validation]" in source
    assert "scripts.release.build_inputs" in source
    assert "jq --null-input --compact-output" in source
    assert "scripts.release.verify_manifest" in source
    assert "scripts.release.update_dev_overlay" in source
    assert "docker/build-push-action@v7" in source
    assert "push: true" in source
    assert "@sha256" in source
    assert "kubectl" not in source
    assert "git commit" in source
    assert "git push origin HEAD:dev" in source


def test_dev_and_pr_scout_are_report_only() -> None:
    dev_source = (WORKFLOWS / "dev-images.yml").read_text(encoding="utf-8")
    pr_job = _workflow("pr-checks.yml")["jobs"]["docker-scout"]

    assert "continue-on-error: true" in dev_source
    assert "exit-code: false" in dev_source
    assert "if-no-files-found: warn" in dev_source
    scout_step = next(step for step in pr_job["steps"] if step.get("id") == "scout")
    assert scout_step["continue-on-error"] == "true"
    upload_step = next(
        step for step in pr_job["steps"] if step.get("name") == "Retain Scout report"
    )
    assert upload_step["continue-on-error"] == "true"


def test_dev_workflow_leaves_reconciliation_to_bootstrapped_argocd() -> None:
    source = (WORKFLOWS / "dev-images.yml").read_text(encoding="utf-8")

    assert "ARGOCD_SERVER" not in source
    assert "ARGOCD_AUTH_TOKEN" not in source
    assert "api/v1/applications" not in source
    assert "curl" not in source
    for forbidden in ("kubectl", "argocd app sync", "helm upgrade"):
        assert forbidden not in source.lower()


def test_main_pull_request_rechecks_exact_prepared_dev_promotion() -> None:
    job = _workflow("pr-checks.yml")["jobs"]["release"]
    source = yaml.safe_dump(job)
    checkout = next(
        step
        for step in job["steps"]
        if step.get("name") == "Check out the promotion branch with history"
    )

    assert "base.ref == 'main'" in job["if"]
    assert checkout["with"]["ref"] == "${{ github.head_ref }}"
    assert "make promote-dev" in source
    assert "git show origin/dev:deploy/releases/dev.json" in source
    assert "--promoted-from /tmp/dev-release.json" in source
    assert "git diff --exit-code" in source
    for forbidden in ("docker build", "docker push", "git commit", "kubectl"):
        assert forbidden not in source.lower()


def test_main_promotion_verifies_observes_and_smokes_without_rebuild() -> None:
    workflow = _workflow("main-promote.yml")
    source = (WORKFLOWS / "main-promote.yml").read_text(encoding="utf-8")

    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["jobs"]["promote"]["environment"] == "prod"
    assert "--promoted-from" in source
    assert "+refs/heads/dev:refs/remotes/origin/dev" in source
    assert "scripts.release.observe_argocd" in source
    assert "playwright install --with-deps chromium" in source
    assert "python -m scripts.smoke.authenticated_prod" in source
    assert "STOCKAI_PROD_COGNITO_USER_POOL_ID" in source
    assert "STOCKAI_PROD_SMOKE_USERNAME" in source
    assert "STOCKAI_PROD_SMOKE_EMAIL" in source
    assert "STOCKAI_PROD_SMOKE_PASSWORD" in source
    assert "STOCKAI_PROD_SESSION_TOKEN" not in source
    assert "STOCKAI_PROD_CSRF_TOKEN" not in source
    assert "screenshot" not in source.lower()
    assert "trace" not in source.lower()
    assert "AWS_TERRAFORM_APPLY_ROLE_ARN" in source
    for forbidden in (
        "docker build",
        "docker push",
        "docker tag",
        "git commit",
        "git push",
        "kubectl",
    ):
        assert forbidden not in source.lower()
