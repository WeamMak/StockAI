"""Secret-safe output sinks for the finite Odoo bootstrap job."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast

import boto3

SECRET_ARN_PATTERN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):secretsmanager:[^:]+:"
    r"\d{12}:secret:[A-Za-z0-9/_+=.@-]+$"
)


class _SecretsManagerExceptions(Protocol):
    ResourceNotFoundException: type[Exception]


class _SecretsManagerClient(Protocol):
    exceptions: _SecretsManagerExceptions

    def get_secret_value(self, *, SecretId: str) -> Mapping[str, object]: ...

    def put_secret_value(self, *, SecretId: str, SecretString: str) -> object: ...


class SecretSink(Protocol):
    """Storage boundary used by the finite bootstrap job."""

    kind: str

    def read(self) -> str | None: ...

    def write(self, value: str) -> None: ...


class FileSink:
    """Store the fictional local bootstrap key in a protected runtime file."""

    kind = "file"

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.is_absolute() or not self.path.is_relative_to("/run"):
            raise RuntimeError("the local bootstrap sink must be an absolute /run path")

    def read(self) -> str | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink():
            raise RuntimeError("the local bootstrap sink cannot be a symbolic link")
        if self.path.stat().st_mode & 0o777 != 0o600:
            raise RuntimeError("the local bootstrap sink must use mode 0600")
        value = self.path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError("the local bootstrap sink is empty")
        return value

    def write(self, value: str) -> None:
        if not self.path.parent.is_dir():
            raise RuntimeError("the local bootstrap sink parent does not exist")
        if self.path.is_symlink():
            raise RuntimeError("the local bootstrap sink cannot be a symbolic link")
        temporary_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


class SecretsManagerSink:
    """Read and replace one exact Secrets Manager secret."""

    kind = "secretsmanager"

    def __init__(
        self,
        arn: str,
        *,
        client_factory: Callable[..., object] = boto3.client,
    ) -> None:
        if not SECRET_ARN_PATTERN.fullmatch(arn):
            raise RuntimeError(
                "the bootstrap secret must be an exact Secrets Manager ARN"
            )
        self.arn = arn
        self.client = cast(
            _SecretsManagerClient,
            client_factory("secretsmanager", region_name=arn.split(":")[3]),
        )

    def read(self) -> str | None:
        try:
            response = self.client.get_secret_value(SecretId=self.arn)
        except self.client.exceptions.ResourceNotFoundException:
            return None
        value = response.get("SecretString")
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("the bootstrap secret must contain a non-empty string")
        return value.strip()

    def write(self, value: str) -> None:
        self.client.put_secret_value(SecretId=self.arn, SecretString=value)


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required bootstrap setting is missing: {name}")
    return value


def sink_from_environment(
    environment: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[..., object] = boto3.client,
) -> SecretSink:
    """Build only the configured local or exact-ARN secret sink."""

    settings = os.environ if environment is None else environment
    kind = settings.get("STOCKAI_ODOO_BOOTSTRAP_SINK", "secretsmanager").strip()
    if kind == "file":
        return FileSink(
            _required_environment(settings, "STOCKAI_ODOO_BOOTSTRAP_KEY_FILE")
        )
    if kind == "secretsmanager":
        return SecretsManagerSink(
            _required_environment(settings, "STOCKAI_ODOO_BOOTSTRAP_SECRET_ARN"),
            client_factory=client_factory,
        )
    raise RuntimeError("bootstrap sink must be file or secretsmanager")
