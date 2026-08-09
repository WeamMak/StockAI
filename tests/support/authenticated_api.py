"""Real-process API composition with only the test-owned identity adapter."""

from procurement.bootstrap.api import create_local_api_app
from tests.support.local_identity import LocalIdentityProvider

app = create_local_api_app(identity_provider_override=LocalIdentityProvider())
