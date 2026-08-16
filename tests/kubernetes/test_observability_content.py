"""Render and content contracts for the T20B dashboards and alert rules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "base" / "observability"
OVERLAYS_ROOT = PROJECT_ROOT / "deploy" / "kubernetes" / "overlays"
ENVIRONMENTS = ("dev", "prod")
EXPECTED_DASHBOARDS = {
    "agent-health.json": "StockAI Agent Health",
    "dependencies-edge.json": "StockAI Dependencies and Edge",
    "kubernetes-capacity.json": "StockAI Kubernetes and Capacity",
    "llm-mcp.json": "StockAI LLM and MCP",
}
EXPECTED_ALERTS = {
    "StockAIDependencyUnavailable",
    "StockAIHttpErrorRateHigh",
    "StockAIHttpsCertificateExpiring",
    "StockAILlmFailures",
    "StockAIMcpFailures",
    "StockAIOdooKeyExpiring",
    "StockAIPersistentVolumePressure",
    "StockAIPodCrashLooping",
    "StockAIPodUnavailable",
    "StockAIPublicHttpsUnavailable",
    "StockAIWorkerDiskPressure",
    "StockAIWorkerReadyMismatch",
}


def _render(environment: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(OVERLAYS_ROOT / environment)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"{environment} overlay did not render:\n{result.stderr.strip()}")
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def _named(resources: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(
        resource
        for resource in resources
        if resource["kind"] == kind and resource["metadata"]["name"] == name
    )


def test_four_dashboards_are_valid_git_managed_json() -> None:
    dashboard_root = OBSERVABILITY_ROOT / "dashboards"
    paths = sorted(dashboard_root.glob("*.json"))
    assert {path.name for path in paths} == set(EXPECTED_DASHBOARDS)

    for path in paths:
        dashboard = json.loads(path.read_text())
        assert dashboard["title"] == EXPECTED_DASHBOARDS[path.name]
        assert dashboard["uid"].startswith("stockai-")
        assert dashboard["editable"] is False
        assert dashboard["schemaVersion"] == 41
        assert dashboard["refresh"] == "30s"
        assert dashboard["panels"]
        assert all(
            panel.get("title") and panel.get("targets") for panel in dashboard["panels"]
        )


def test_required_dashboard_panels_and_queries_are_present() -> None:
    dashboards = {
        path.name: json.loads(path.read_text())
        for path in (OBSERVABILITY_ROOT / "dashboards").glob("*.json")
    }
    titles = {
        filename: {panel["title"] for panel in dashboard["panels"]}
        for filename, dashboard in dashboards.items()
    }
    assert {
        "Requests per minute by result",
        "Request error rate",
        "Request latency p50 / p95 / p99",
    } <= titles["agent-health.json"]
    assert {
        "LLM input tokens",
        "LLM output tokens",
        "Retries, repairs, and safe fallbacks",
        "Preference premium outcomes",
    } <= titles["llm-mcp.json"]
    assert {
        "HPA current and desired replicas",
        "Pending pods",
        "Ready workers vs ASG capacity",
        "Worker replacement duration",
        "Volume attach errors",
        "Cleanup outcomes",
    } <= titles["kubernetes-capacity.json"]
    assert {
        "Public HTTPS availability",
        "TLS certificate lifetime",
        "ALB healthy hosts",
        "ALB 5xx responses",
    } <= titles["dependencies-edge.json"]
    mixed_panel = next(
        panel
        for panel in dashboards["kubernetes-capacity.json"]["panels"]
        if panel["title"] == "Ready workers vs ASG capacity"
    )
    assert mixed_panel["datasource"]["uid"] == "-- Mixed --"

    serialized = json.dumps(dashboards).replace('\\"', '"')
    for metric in (
        "procurement_http_requests_total",
        "procurement_http_request_duration_seconds_bucket",
        'procurement_llm_tokens_total{direction="input"}',
        'procurement_llm_tokens_total{direction="output"}',
        "procurement_preference_offer_outcomes_total",
        "procurement_llm_repairs_total",
        "procurement_llm_fallbacks_total",
        "kube_horizontalpodautoscaler_status_current_replicas",
        "kube_pod_status_phase",
        "WorkerCleanupOutcome",
        "GroupDesiredCapacity",
        "GroupInServiceInstances",
        "HealthyHostCount",
        "HTTPCode_Target_5XX_Count",
    ):
        assert metric in serialized
    for unsafe_label in ("request_id", "scan_id", "case_id", "product_id", "vendor_id"):
        assert unsafe_label not in serialized


def test_alert_rules_are_actionable_and_low_cardinality() -> None:
    rules_path = OBSERVABILITY_ROOT / "rules" / "stockai-alerts.yaml"
    rules = yaml.safe_load(rules_path.read_text())
    alerts = [rule for group in rules["groups"] for rule in group["rules"]]
    assert {rule["alert"] for rule in alerts} == EXPECTED_ALERTS

    for rule in alerts:
        assert rule["for"]
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["labels"]["owner"] == "stockai-operator"
        assert rule["annotations"]["description"]
        assert rule["annotations"]["evidence"]
        assert rule["annotations"]["runbook_url"].startswith("docs/runbooks/alerts.md#")

    serialized = rules_path.read_text()
    for unsafe_label in ("request_id", "scan_id", "case_id", "product_id", "vendor_id"):
        assert unsafe_label not in serialized


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_render_mounts_dashboards_and_rules_from_git(environment: str) -> None:
    resources = _render(environment)
    prometheus = _named(resources, "Deployment", "stockai-prometheus")
    grafana = _named(resources, "Deployment", "stockai-grafana")
    prometheus_spec = prometheus["spec"]["template"]["spec"]
    grafana_spec = grafana["spec"]["template"]["spec"]

    dashboard_config = _named(
        resources, "ConfigMap", "stockai-observability-dashboards"
    )
    rules_config = _named(resources, "ConfigMap", "stockai-observability-rules")
    assert set(dashboard_config["data"]) == set(EXPECTED_DASHBOARDS)
    assert set(rules_config["data"]) == {"stockai-alerts.yaml"}
    assert any(
        volume.get("configMap", {}).get("name") == "stockai-observability-dashboards"
        for volume in grafana_spec["volumes"]
    )
    assert any(
        mount["mountPath"] == "/etc/grafana/dashboards"
        for mount in grafana_spec["containers"][0]["volumeMounts"]
    )
    assert any(
        volume.get("configMap", {}).get("name") == "stockai-observability-rules"
        for volume in prometheus_spec["volumes"]
    )
    assert any(
        mount["mountPath"] == "/etc/prometheus/rules"
        for mount in prometheus_spec["containers"][0]["volumeMounts"]
    )
    prometheus_config = _named(resources, "ConfigMap", "stockai-observability-config")[
        "data"
    ]["prometheus.yml"]
    assert "rule_files:" in prometheus_config
    assert "/etc/prometheus/rules/*.yaml" in prometheus_config


def test_alert_runbook_covers_every_provisioned_alert() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbooks" / "alerts.md").read_text()
    for alert_name in EXPECTED_ALERTS:
        assert f"## {alert_name}" in runbook
    assert "internal-only" in runbook
    assert "Silence" in runbook
