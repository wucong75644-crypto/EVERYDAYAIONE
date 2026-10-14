"""Bounded scalar diagnostics for Actor delivery, without response content."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from loguru import logger


_MAX_PENDING_SUMMARIES = 64
_pending_summaries: set[asyncio.Task[None]] = set()
_STAGES = ("append", "send", "progress", "snapshot", "persist", "sink_arrival_gap")


@dataclass
class _Timing:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    slow_count: int = 0

    def add(self, milliseconds: float, threshold_ms: float) -> None:
        self.count += 1
        self.total_ms += milliseconds
        self.max_ms = max(self.max_ms, milliseconds)
        self.slow_count += int(milliseconds >= threshold_ms)


class ChatStreamDiagnostics:
    """One fixed-size accumulator per attempt; arrival gaps include backpressure.

    Gaps can also contain model/tool round boundaries. They are not provider-only
    network timings. Persist includes progress/snapshot and retry waits, so its
    timing overlaps those stages rather than representing an additional cost.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        publish: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._publish = publish or publish_chat_stream_summary
        self._started_at = self._clock()
        self._last_text_at: float | None = None
        self._timings = {stage: _Timing() for stage in _STAGES}
        self._text_chunks = 0
        self._text_chars = 0
        self._max_text_chunk_chars = 0
        self._first_text_since_sink_start_ms: float | None = None
        self._finished = False

    def on_text(self, chars: int) -> None:
        now = self._clock()
        if self._first_text_since_sink_start_ms is None:
            # Includes preparation/thinking since sink creation, not provider TTFT.
            self._first_text_since_sink_start_ms = max(0.0, (now - self._started_at) * 1000)
        if self._last_text_at is not None:
            self._timings["sink_arrival_gap"].add(
                max(0.0, (now - self._last_text_at) * 1000), 100.0,
            )
        self._last_text_at = now
        self._text_chunks += 1
        self._text_chars += chars
        self._max_text_chunk_chars = max(self._max_text_chunk_chars, chars)

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        start = self._clock()
        try:
            yield
        finally:
            self._timings[stage].add(max(0.0, (self._clock() - start) * 1000), 50.0)

    def finish(
        self, *, outcome: str, task_id: str, execution_attempt: int,
        stream_id: str | None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        fields: dict[str, Any] = {
            "scope": "actor_generation",
            "task_id": task_id,
            "execution_attempt": execution_attempt,
            "stream_id": stream_id,
            "outcome": outcome,
            "text_chunks": self._text_chunks,
            "text_chars": self._text_chars,
            "max_text_chunk_chars": self._max_text_chunk_chars,
            "first_text_since_sink_start_ms": (
                round(self._first_text_since_sink_start_ms, 3)
                if self._first_text_since_sink_start_ms is not None else None
            ),
            "elapsed_ms": round(max(0.0, (self._clock() - self._started_at) * 1000), 3),
            "stage_slow_threshold_ms": 50,
            "gap_slow_threshold_ms": 100,
        }
        for stage, timing in self._timings.items():
            fields.update({
                f"{stage}_count": timing.count,
                f"{stage}_total_ms": round(timing.total_ms, 3),
                f"{stage}_max_ms": round(timing.max_ms, 3),
                f"{stage}_slow_count": timing.slow_count,
            })
        try:
            self._publish(fields)
        except Exception:
            # Telemetry must never replace a provider/fencing/control exception.
            pass


def publish_chat_stream_summary(fields: dict[str, Any]) -> None:
    """Reuse the sampling publisher's background-thread logging pattern.

    Only one bounded summary is scheduled per Actor attempt. This observes
    generation/delivery, not the Actor's later database commit. Queue saturation
    drops diagnostics rather than delaying delivery or growing memory forever.
    """
    if len(_pending_summaries) >= _MAX_PENDING_SUMMARIES:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_publish_in_background(fields))
    _pending_summaries.add(task)
    task.add_done_callback(_pending_summaries.discard)


async def _publish_in_background(fields: dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(_log_summary, fields)
    except Exception:
        pass


def _log_summary(fields: dict[str, Any]) -> None:
    logger.info("Actor stream delivery summary {}", json.dumps(fields, separators=(",", ":")))
