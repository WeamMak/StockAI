"""Disposable Odoo stack fixtures for Task 10 contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "compose.odoo.yaml"
CONTRACT_DATABASE = "stockai_t10_contract"
CONTRACT_LOGIN = "stockai-contract@example.invalid"
FICTIONAL_DATABASE_PASSWORD = "fictional-t10-postgres-password"
KEY_FILE = "/run/stockai-contract/api-key"


@dataclass(frozen=True)
class OdooContractStack:
    """Observable outputs from one disposable clean-database contract run."""

    base_url: str
    database: str
    api_key: str
    key_mode: str
    first_bootstrap: dict[str, object]
    second_bootstrap: dict[str, object]


def _run(
    command: list[str],
    *,
    environment: Mapping[str, str],
    check: bool = True,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_summary(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError(f"bootstrap produced no JSON summary: {output[-1000:]}")


@pytest.fixture(scope="session")
def running_odoo_contract() -> Generator[OdooContractStack]:
    """Start a fresh pinned Odoo stack and remove all state after the suite."""

    project_name = f"stockai-t10-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    environment = {
        **os.environ,
        "ODOO_CONTRACT_DATABASE": CONTRACT_DATABASE,
        "ODOO_CONTRACT_LOGIN": CONTRACT_LOGIN,
        "ODOO_CONTRACT_PORT": "0",
        "ODOO_CONTRACT_POSTGRES_PASSWORD": FICTIONAL_DATABASE_PASSWORD,
    }
    compose = [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--file",
        str(COMPOSE_FILE),
    ]
    bootstrap = [
        *compose,
        "exec",
        "-T",
        "odoo",
        "bash",
        "-lc",
        (
            'odoo shell --no-http --database="$ODOO_CONTRACT_DATABASE" '
            '--db_host="$HOST" --db_port="$PORT" --db_user="$USER" '
            '--db_password="$PASSWORD" --log-level=error '
            "< /opt/stockai/probe_bootstrap.py"
        ),
    ]
    api_key = ""
    setup_succeeded = False

    try:
        started = _run(
            [*compose, "up", "--detach", "--wait", "--wait-timeout", "300"],
            environment=environment,
            check=False,
        )
        if started.returncode != 0:
            logs = _run(
                [*compose, "logs", "--no-color"],
                environment=environment,
                check=False,
            )
            pytest.fail(
                "disposable Odoo stack did not become healthy:\n"
                f"{started.stdout}\n{started.stderr}\n{logs.stdout[-4000:]}"
            )

        port_output = _run(
            [*compose, "port", "odoo", "8069"], environment=environment
        ).stdout.strip()
        published_port = int(port_output.rsplit(":", 1)[1])

        first = _run(bootstrap, environment=environment, check=False)
        if first.returncode != 0:
            pytest.fail(
                "first ORM bootstrap failed:\n"
                f"{first.stdout[-2000:]}\n{first.stderr[-4000:]}"
            )
        second = _run(bootstrap, environment=environment, check=False)
        if second.returncode != 0:
            pytest.fail(
                "second ORM bootstrap failed:\n"
                f"{second.stdout[-2000:]}\n{second.stderr[-4000:]}"
            )
        api_key = _run(
            [
                *compose,
                "exec",
                "-T",
                "odoo",
                "python3",
                "-c",
                f"from pathlib import Path; print(Path({KEY_FILE!r}).read_text())",
            ],
            environment=environment,
        ).stdout.strip()
        key_mode = _run(
            [*compose, "exec", "-T", "odoo", "stat", "-c", "%a", KEY_FILE],
            environment=environment,
        ).stdout.strip()
        setup_succeeded = True

        yield OdooContractStack(
            base_url=f"http://127.0.0.1:{published_port}",
            database=CONTRACT_DATABASE,
            api_key=api_key,
            key_mode=key_mode,
            first_bootstrap=_parse_summary(first.stdout),
            second_bootstrap=_parse_summary(second.stdout),
        )
    finally:
        logs = _run(
            [*compose, "logs", "--no-color"],
            environment=environment,
            check=False,
        )
        down = _run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            environment=environment,
            check=False,
        )
        if setup_succeeded:
            combined_logs = logs.stdout + logs.stderr
            assert api_key
            assert api_key not in combined_logs
            assert FICTIONAL_DATABASE_PASSWORD not in combined_logs
            assert down.returncode == 0, down.stderr
