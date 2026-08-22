"""MCP composition settings for shared decision persistence."""

import pytest

from procurement.bootstrap.mcp import LocalMcpSettings
from procurement.domain.identifiers import Environment


def test_settings_load_shared_application_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PROCUREMENT_MCP_TOKEN", "fictional-dev-mcp-token-at-least-32-characters"
    )
    monkeypatch.setenv("PROCUREMENT_ENVIRONMENT", "prod")
    monkeypatch.setenv("PROCUREMENT_AWS_REGION", "eu-west-1")
    monkeypatch.setenv("PROCUREMENT_DYNAMODB_APPLICATION_TABLE", "stockai-prod-app")
    monkeypatch.setenv("PROCUREMENT_DYNAMODB_ENDPOINT_URL", "http://dynamodb:8000")

    settings = LocalMcpSettings.from_environment()

    assert settings.environment is Environment.PROD
    assert settings.aws_region == "eu-west-1"
    assert settings.dynamodb_application_table == "stockai-prod-app"
    assert settings.dynamodb_endpoint_url == "http://dynamodb:8000"


def test_settings_reject_invalid_dynamodb_endpoint() -> None:
    with pytest.raises(ValueError, match="DYNAMODB_ENDPOINT_URL"):
        LocalMcpSettings(
            environment=Environment.DEV,
            bearer_token="fictional-dev-mcp-token-at-least-32-characters",
            dynamodb_endpoint_url="not-a-url",
        )
