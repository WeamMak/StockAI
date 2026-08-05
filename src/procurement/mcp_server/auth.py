"""Opaque bearer-token verification for the private MCP boundary."""

from __future__ import annotations

import hmac
import re

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

READ_SCOPE = "procurement:read"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{32,512}$", re.ASCII)


def validate_bearer_token(token: object) -> str:
    """Validate configured credential shape without exposing its value."""

    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError(
            "The MCP bearer token must be 32 to 512 safe ASCII characters."
        )
    return token


class StaticBearerTokenVerifier:
    """Verify one environment-specific opaque token in constant time."""

    def __init__(self, bearer_token: str) -> None:
        self._bearer_token = validate_bearer_token(bearer_token)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return narrow access metadata only for the configured token."""

        if _TOKEN_PATTERN.fullmatch(token) is None or not hmac.compare_digest(
            token, self._bearer_token
        ):
            return None
        return AccessToken(
            token=token,
            client_id="stockai-agent",
            subject="stockai-agent",
            scopes=[READ_SCOPE],
        )


def create_auth_settings() -> AuthSettings:
    """Build resource-server settings for the private bearer boundary."""

    return AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.stockai.invalid"),
        resource_server_url=None,
        required_scopes=[READ_SCOPE],
    )
