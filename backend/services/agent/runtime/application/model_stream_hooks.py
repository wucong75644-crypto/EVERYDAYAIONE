"""Best-effort Runtime stream lifecycle notifications."""

from __future__ import annotations

import asyncio

from services.agent.runtime.ports.model import (
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
