"""Real-process API composition with only the test-owned identity adapter."""

import os

from procurement.api.auth.session import UserRole
from procurement.bootstrap.api import create_local_api_app
from tests.support.local_identity import LocalIdentityProvider

app = create_local_api_app(
    identity_provider_override=LocalIdentityProvider(
        role=UserRole(os.environ.get("PROCUREMENT_TEST_USER_ROLE", "officer"))
    )
)
