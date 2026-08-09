"""Public behavior of the finite Odoo bootstrap secret sinks."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SINKS_MODULE = PROJECT_ROOT / "odoo" / "bootstrap" / "sinks.py"

SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:123456789012:"
    "secret:stockai/dev/odoo-api-key-AbCd12"
)
FICTIONAL_SECRET = "fictional-t12a-odoo-api-key"


class SecretSink(Protocol):
    kind: str

    def read(self) -> str | None: ...

    def write(self, value: str) -> None: ...


class SecretSinkConstructor(Protocol):
    def __call__(
        self,
        arn: str,
        *,
        client_factory: Callable[..., object],
    ) -> SecretSink: ...


class SinkFromEnvironment(Protocol):
    def __call__(
        self,
        environment: Mapping[str, str],
        *,
        client_factory: Callable[..., object],
    ) -> SecretSink: ...


def _load_sinks() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stockai_odoo_sinks", SINKS_MODULE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINKS = _load_sinks()
SecretsManagerSink = cast(
    SecretSinkConstructor,
    SINKS.SecretsManagerSink,
)
sink_from_environment = cast(
    SinkFromEnvironment,
    SINKS.sink_from_environment,
)


class FakeResourceNotFoundError(Exception):
    """Stand in for the generated boto3 client exception type."""


class FakeSecretsManagerClient:
    """Record exact secret operations without contacting AWS."""

    class exceptions:
        ResourceNotFoundException = FakeResourceNotFoundError

    def __init__(self, response: object) -> None:
        self.response = response
        self.get_requests: list[dict[str, str]] = []
        self.put_requests: list[dict[str, str]] = []

    def get_secret_value(self, **request: str) -> Any:
        self.get_requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def put_secret_value(self, **request: str) -> None:
        self.put_requests.append(request)


def test_secrets_manager_sink_uses_the_exact_arn_and_derived_region(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeSecretsManagerClient({"SecretString": f" {FICTIONAL_SECRET} "})
    factory_calls: list[tuple[str, str]] = []

    def client_factory(service_name: str, *, region_name: str) -> object:
        factory_calls.append((service_name, region_name))
        return client

    sink = SecretsManagerSink(SECRET_ARN, client_factory=client_factory)

    secret = sink.read()
    assert secret == FICTIONAL_SECRET
    sink.write(FICTIONAL_SECRET)

    assert factory_calls == [("secretsmanager", "us-east-1")]
    assert client.get_requests == [{"SecretId": SECRET_ARN}]
    assert client.put_requests == [
        {"SecretId": SECRET_ARN, "SecretString": FICTIONAL_SECRET}
    ]
    captured = capsys.readouterr()
    assert FICTIONAL_SECRET not in captured.out
    assert FICTIONAL_SECRET not in captured.err
    assert FICTIONAL_SECRET not in caplog.text


def test_secrets_manager_sink_treats_a_missing_secret_as_absent() -> None:
    client = FakeSecretsManagerClient(FakeResourceNotFoundError())
    sink = SecretsManagerSink(
        SECRET_ARN,
        client_factory=lambda *_args, **_kwargs: client,
    )

    assert sink.read() is None
    assert client.get_requests == [{"SecretId": SECRET_ARN}]


@pytest.mark.parametrize(
    "response",
    [{}, {"SecretString": ""}, {"SecretString": "   "}, {"SecretString": 7}],
)
def test_secrets_manager_sink_rejects_an_empty_or_non_string_secret(
    response: object,
) -> None:
    client = FakeSecretsManagerClient(response)
    sink = SecretsManagerSink(
        SECRET_ARN,
        client_factory=lambda *_args, **_kwargs: client,
    )

    with pytest.raises(RuntimeError, match="must contain a non-empty string"):
        sink.read()


@pytest.mark.parametrize(
    "arn",
    [
        "",
        "stockai/dev/odoo-api-key",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:*",
        "arn:aws:secretsmanager:us-east-1:not-an-account:secret:stockai/dev",
    ],
)
def test_secrets_manager_sink_rejects_a_non_exact_arn(arn: str) -> None:
    with pytest.raises(RuntimeError, match="exact Secrets Manager ARN"):
        SecretsManagerSink(arn, client_factory=lambda *_args, **_kwargs: object())


def test_sink_selection_uses_only_the_configured_exact_secret() -> None:
    client = FakeSecretsManagerClient({"SecretString": FICTIONAL_SECRET})

    sink = sink_from_environment(
        {
            "STOCKAI_ODOO_BOOTSTRAP_SINK": "secretsmanager",
            "STOCKAI_ODOO_BOOTSTRAP_SECRET_ARN": SECRET_ARN,
        },
        client_factory=lambda *_args, **_kwargs: client,
    )

    assert sink.kind == "secretsmanager"
    assert sink.read() == FICTIONAL_SECRET
