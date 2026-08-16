"""Best-effort Runtime stream lifecycle notifications."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from services.agent.runtime.ports.model import (
    ModelCallUnknownError,
    ModelResponseStreamObserver,
    ModelStepResult,
)


async def notify_stream_started(
    observer: ModelResponseStreamObserver, model_id: str,
) -> None:
    try:
        await observer.stream_started(model_id=model_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def notify_stream_completed(
    observer: ModelResponseStreamObserver, result: ModelStepResult,
) -> None:
    try:
        await observer.stream_completed(result=result)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def notify_stream_failed(
    observer: ModelResponseStreamObserver | None, error_code: str,
) -> None:
    if observer is None:
        return
    try:
        await observer.stream_failed(error_code=error_code)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def await_model_work(
    work: Awaitable[ModelStepResult],
    observer: ModelResponseStreamObserver | None,
) -> ModelStepResult:
    try:
        return await work
    except asyncio.CancelledError:
        raise
    except ModelCallUnknownError:
        raise
    except Exception:
        await notify_stream_failed(observer, "RUNTIME_MODEL_STREAM_FAILED")
        raise
