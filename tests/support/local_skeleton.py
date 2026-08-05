"""Actual-process harness for the local walking-skeleton integration tests."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RunningLocalSkeleton:
    """Addresses and captured output for two running backend processes."""

    api_url: str
    mcp_url: str
    api_log_path: Path
    mcp_log_path: Path
    bearer_token: str


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_ready(
    *,
    url: str,
    process: subprocess.Popen[bytes],
    log_path: Path,
) -> None:
    deadline = monotonic() + 10
    while monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            output = log_path.read_text(encoding="utf-8", errors="replace")
            raise AssertionError(
                f"Process exited with {exit_code} before {url} was ready:\n{output}"
            )
        try:
            response = httpx.get(url, timeout=0.2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        sleep(0.02)
    raise AssertionError(f"Process did not become ready at {url}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def run_local_skeleton(
    temporary_directory: Path,
    *,
    erp_mode: str = "success",
) -> Iterator[RunningLocalSkeleton]:
    """Start the real local MCP and API composition roots on unused ports."""

    api_port = _unused_port()
    mcp_port = _unused_port()
    api_url = f"http://127.0.0.1:{api_port}"
    mcp_base_url = f"http://127.0.0.1:{mcp_port}"
    bearer_token = secrets.token_urlsafe(32)
    environment = {
        **os.environ,
        "PROCUREMENT_ENVIRONMENT": "dev",
        "PROCUREMENT_LOG_LEVEL": "INFO",
        "PROCUREMENT_MCP_URL": f"{mcp_base_url}/mcp",
        "PROCUREMENT_MCP_TOKEN": bearer_token,
        "PROCUREMENT_LOCAL_ERP_MODE": erp_mode,
        "PROCUREMENT_MCP_READ_TIMEOUT_SECONDS": "0.01",
        "PROCUREMENT_MCP_MAX_RETRIES": "2",
        "PROCUREMENT_MCP_RETRY_DELAY_SECONDS": "0",
    }
    api_log_path = temporary_directory / "api.log"
    mcp_log_path = temporary_directory / "mcp.log"
    command_prefix = [sys.executable, "-m", "uvicorn"]
    server_options = [
        "--host",
        "127.0.0.1",
        "--log-level",
        "warning",
        "--no-access-log",
    ]

    with (
        mcp_log_path.open("wb") as mcp_output,
        api_log_path.open("wb") as api_output,
    ):
        mcp_process = subprocess.Popen(
            [
                *command_prefix,
                "procurement.bootstrap.mcp:app",
                *server_options,
                "--port",
                str(mcp_port),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=mcp_output,
            stderr=subprocess.STDOUT,
        )
        api_process: subprocess.Popen[bytes] | None = None
        try:
            _wait_until_ready(
                url=f"{mcp_base_url}/metrics",
                process=mcp_process,
                log_path=mcp_log_path,
            )
            api_process = subprocess.Popen(
                [
                    *command_prefix,
                    "procurement.bootstrap.api:app",
                    *server_options,
                    "--port",
                    str(api_port),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=api_output,
                stderr=subprocess.STDOUT,
            )
            _wait_until_ready(
                url=f"{api_url}/health/live",
                process=api_process,
                log_path=api_log_path,
            )
            yield RunningLocalSkeleton(
                api_url=api_url,
                mcp_url=mcp_base_url,
                api_log_path=api_log_path,
                mcp_log_path=mcp_log_path,
                bearer_token=bearer_token,
            )
        finally:
            if api_process is not None:
                _stop(api_process)
            _stop(mcp_process)
