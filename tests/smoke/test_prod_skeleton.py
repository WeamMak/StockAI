"""Live T24 proof for the exact Argo-reconciled prod walking skeleton."""

from __future__ import annotations

import os

import pytest
from tests.smoke.test_dev_skeleton import run_exact_walking_skeleton


@pytest.mark.skipif(
    os.environ.get("STOCKAI_RUN_PROD_SMOKE") != "1",
    reason="requires explicit live prod authorization and session input",
)
def test_exact_prod_walking_skeleton() -> None:
    run_exact_walking_skeleton("prod")
