"""Real Odoo integration contracts for the finite T11A bootstrap and seed jobs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts.odoo.probe_contract import Json2Client, ProbeError

from tests.contract.conftest import OdooContractStack, _run, running_odoo_contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_LOGIN = "stockai-bootstrap-contract@example.invalid"
BOOTSTRAP_KEY_FILE = "/run/stockai-contract/bootstrap-api-key"


def _summary(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{"):
            parsed = json.loads(candidate)
            assert isinstance(parsed, dict)
            return parsed
    raise AssertionError("Odoo job produced no JSON summary")


def _shell_job(
    stack: OdooContractStack,
    script: str,
    *,
    extra_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {**stack.environment, **(extra_environment or {})}
    exports = [
        item
        for key, value in (extra_environment or {}).items()
        for item in ("-e", f"{key}={value}")
    ]
    return _run(
        [
            *stack.compose_command,
            "exec",
            "-T",
            *exports,
            "odoo",
            "bash",
            "-lc",
            (
                'odoo shell --no-http --database="$ODOO_CONTRACT_DATABASE" '
                '--db_host="$HOST" --db_port="$PORT" --db_user="$USER" '
                '--db_password="$PASSWORD" --log-level=error '
                f"< {script}"
            ),
        ],
        environment=environment,
        check=False,
    )


def _read_key(stack: OdooContractStack, path: str) -> str:
    completed = _run(
        [
            *stack.compose_command,
            "exec",
            "-T",
            "odoo",
            "python3",
            "-c",
            f"from pathlib import Path; print(Path({path!r}).read_text())",
        ],
        environment=stack.environment,
    )
    return completed.stdout.strip()


def _bootstrap(
    stack: OdooContractStack, *, rotate: bool = False
) -> subprocess.CompletedProcess[str]:
    return _shell_job(
        stack,
        "/opt/stockai/bootstrap.py",
        extra_environment={
            "STOCKAI_ODOO_BOOTSTRAP_LOGIN": BOOTSTRAP_LOGIN,
            "STOCKAI_ODOO_BOOTSTRAP_KEY_NAME": "stockai-t11a-bootstrap-contract",
            "STOCKAI_ODOO_BOOTSTRAP_KEY_EXPIRY_DAYS": "30",
            "STOCKAI_ODOO_BOOTSTRAP_ROTATE": "true" if rotate else "false",
            "STOCKAI_ODOO_BOOTSTRAP_SINK": "file",
            "STOCKAI_ODOO_BOOTSTRAP_KEY_FILE": BOOTSTRAP_KEY_FILE,
        },
    )


def test_bootstrap_is_idempotent_authenticates_and_rotates_without_disclosure(
    running_odoo_contract: OdooContractStack,
) -> None:
    first = _bootstrap(running_odoo_contract)
    assert first.returncode == 0, first.stderr
    first_summary = _summary(first.stdout)
    first_key = _read_key(running_odoo_contract, BOOTSTRAP_KEY_FILE)

    second = _bootstrap(running_odoo_contract)
    assert second.returncode == 0, second.stderr
    second_summary = _summary(second.stdout)
    second_key = _read_key(running_odoo_contract, BOOTSTRAP_KEY_FILE)

    assert first_summary["status"] == "created"
    assert second_summary["status"] == "existing"
    assert first_summary["user_id"] == second_summary["user_id"]
    assert first_summary["active_named_key_count"] == 1
    assert second_summary["active_named_key_count"] == 1
    assert first_summary["direct_group_count"] == 1
    assert second_key == first_key
    assert first_key not in first.stdout + first.stderr + second.stdout + second.stderr

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=first_key,
    ) as client:
        assert (
            client.call("res.users", "context_get", {})["uid"]
            == first_summary["user_id"]
        )

    rotated = _bootstrap(running_odoo_contract, rotate=True)
    assert rotated.returncode == 0, rotated.stderr
    rotated_summary = _summary(rotated.stdout)
    rotated_key = _read_key(running_odoo_contract, BOOTSTRAP_KEY_FILE)
    assert rotated_summary["status"] == "rotated"
    assert rotated_summary["active_named_key_count"] == 1
    assert rotated_key != first_key
    assert rotated_key not in rotated.stdout + rotated.stderr

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=rotated_key,
    ) as client:
        assert (
            client.call("res.users", "context_get", {})["uid"]
            == first_summary["user_id"]
        )
    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=first_key,
    ) as revoked_client:
        try:
            revoked_client.call("res.users", "context_get", {})
        except ProbeError as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("the rotated API key was not revoked")

    logs = _run(
        [*running_odoo_contract.compose_command, "logs", "--no-color"],
        environment=running_odoo_contract.environment,
        check=False,
    )
    combined_logs = logs.stdout + logs.stderr
    assert first_key not in combined_logs
    assert rotated_key not in combined_logs


@pytest.mark.parametrize("seed_environment", ["dev", "prod"])
def test_seed_and_verification_are_stable_across_reruns(
    running_odoo_contract: OdooContractStack,
    seed_environment: str,
) -> None:
    first_seed = _shell_job(
        running_odoo_contract,
        "/opt/stockai/seed.py",
        extra_environment={"STOCKAI_ODOO_SEED_ENVIRONMENT": seed_environment},
    )
    assert first_seed.returncode == 0, first_seed.stderr
    first_verification = _shell_job(
        running_odoo_contract,
        "/opt/stockai/verify_seed.py",
        extra_environment={"STOCKAI_ODOO_SEED_ENVIRONMENT": seed_environment},
    )
    assert first_verification.returncode == 0, first_verification.stderr

    second_seed = _shell_job(
        running_odoo_contract,
        "/opt/stockai/seed.py",
        extra_environment={"STOCKAI_ODOO_SEED_ENVIRONMENT": seed_environment},
    )
    assert second_seed.returncode == 0, second_seed.stderr
    second_verification = _shell_job(
        running_odoo_contract,
        "/opt/stockai/verify_seed.py",
        extra_environment={"STOCKAI_ODOO_SEED_ENVIRONMENT": seed_environment},
    )
    assert second_verification.returncode == 0, second_verification.stderr

    first = _summary(first_verification.stdout)
    second = _summary(second_verification.stdout)
    assert first == second
    assert first["status"] == "ok"
    assert first["environment"] == seed_environment
    references = first["references"]
    scenarios = first["scenarios"]
    counts = first["counts"]
    assert isinstance(references, dict)
    assert isinstance(scenarios, list)
    assert isinstance(counts, dict)
    assert all(
        isinstance(reference, str)
        and reference.startswith(f"STOCKAI-{seed_environment.upper()}-")
        for reference in references.values()
    )
    assert set(scenarios) == {
        "happy",
        "no-valid-offer",
        "over-budget",
        "receipt-return",
    }
    for key, minimum in {
        "budgets": 2,
        "open_purchase_orders": 2,
        "completed_receipts": 1,
        "returns": 1,
    }.items():
        value = counts[key]
        assert isinstance(value, int)
        assert value >= minimum


__all__ = ["running_odoo_contract"]
