"""Bounded, sanitized probe for the pinned Odoo 19 JSON-2 contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_.]+$")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ProbeError(RuntimeError):
    """A deliberately sanitized probe failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class Json2Client:
    """Small JSON-2 client used only to verify the external wire contract."""

    def __init__(
        self,
        *,
        base_url: str,
        database: str,
        api_key: str,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"bearer {api_key}",
                "X-Odoo-Database": database,
            },
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def call(self, model: str, method: str, payload: dict[str, object]) -> Any:
        """Call one model method with JSON-2 named arguments."""

        self._validate_path_segment(model)
        self._validate_path_segment(method)
        return self._request("POST", f"/json/2/{model}/{method}", json_body=payload)

    def doc(self, model: str | None = None) -> Any:
        """Read the bearer-protected, database-specific API documentation."""

        if model is None:
            path = "/doc-bearer/index.json"
        else:
            self._validate_path_segment(model)
            path = f"/doc-bearer/{model}.json"
        return self._request("GET", path)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> Any:
        try:
            with self._client.stream(method, path, json=json_body) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ProbeError(
                            "Odoo response exceeded the probe limit",
                            status_code=response.status_code,
                        )
                    content.extend(chunk)
                status_code = response.status_code
        except httpx.TimeoutException as exc:
            raise ProbeError("Odoo request timed out") from exc
        except httpx.RequestError as exc:
            raise ProbeError("Odoo request was unavailable") from exc

        if not 200 <= status_code < 300:
            raise ProbeError(
                f"Odoo JSON-2 request failed (HTTP {status_code})",
                status_code=status_code,
            )
        try:
            return json.loads(content)
        except ValueError as exc:
            raise ProbeError(
                "Odoo returned malformed JSON", status_code=status_code
            ) from exc

    @staticmethod
    def _validate_path_segment(value: str) -> None:
        if not _PATH_SEGMENT.fullmatch(value):
            raise ValueError("Odoo model and method names must be plain path segments")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    arguments = parser.parse_args()

    api_key = arguments.api_key_file.read_text(encoding="utf-8").strip()
    with Json2Client(
        base_url=arguments.base_url,
        database=arguments.database,
        api_key=api_key,
    ) as client:
        context = client.call("res.users", "context_get", {})
        purchase_order_doc = client.doc("purchase.order")
    print(
        json.dumps(
            {
                "context_available": isinstance(context, dict),
                "purchase_order_doc_available": isinstance(purchase_order_doc, dict),
                "status": "ok",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
