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
        source.count("astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9") == 3
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

    assert len(fingerprints) == 7
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
