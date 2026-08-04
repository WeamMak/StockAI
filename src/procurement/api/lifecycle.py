"""Application lifecycle state used by readiness checks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI


@dataclass(slots=True)
class LifecycleState:
    """Track whether the API should receive new traffic."""

    is_ready: bool = False


def lifespan_for(
    lifecycle: LifecycleState,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a FastAPI lifespan that controls readiness."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        lifecycle.is_ready = True
        try:
            yield
        finally:
            lifecycle.is_ready = False

    return lifespan
