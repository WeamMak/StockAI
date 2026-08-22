"""Application lifecycle state used by readiness checks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 40.0


class BackgroundService(Protocol):
    """Service whose accepted background work must finish before shutdown."""

    async def drain(self) -> None: ...


@dataclass(slots=True)
class LifecycleState:
    """Track whether the API should receive new traffic."""

    is_ready: bool = False


def lifespan_for(
    lifecycle: LifecycleState,
    *,
    background_services: Sequence[BackgroundService] = (),
    shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a lifespan that stops traffic before draining accepted work."""

    if shutdown_timeout_seconds <= 0:
        raise ValueError("shutdown_timeout_seconds must be positive")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        lifecycle.is_ready = True
        try:
            yield
        finally:
            lifecycle.is_ready = False
            try:
                async with asyncio.timeout(shutdown_timeout_seconds):
                    await asyncio.gather(
                        *(service.drain() for service in background_services)
                    )
            except TimeoutError:
                logging.getLogger(__name__).warning(
                    "background work exceeded the graceful shutdown timeout"
                )

    return lifespan
