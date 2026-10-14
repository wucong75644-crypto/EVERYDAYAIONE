"""Delivery instrumentation must be scalar, private and off the streaming path."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from services.agent.observability import chat_stream
from services.agent.observability.chat_stream import ChatStreamDiagnostics
from services.conversation_delivery_store import DeliverySession
from services.handlers.chat.actor_sink import ActorDelivery, ActorWebSink


class _Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_metrics_keep_fixed_scalar_state_and_publish_once():
    clock, summaries = _Clock(), []
    metrics = ChatStreamDiagnostics(clock=clock, publish=summaries.append)
    clock.advance(0.025)
    metrics.on_text(3)
    with metrics.measure("append"):
        clock.advance(0.060)
    clock.advance(0.060)
    metrics.on_text(2)
    metrics.finish(outcome="paused", task_id="task-1", execution_attempt=2, stream_id="stream-1")
    metrics.finish(outcome="completed", task_id="task-1", execution_attempt=2, stream_id="stream-1")
    assert len(summaries) == 1
    row = summaries[0]
    assert row["scope"] == "actor_generation"
    assert row["text_chunks"] == 2 and row["text_chars"] == 5
    assert row["max_text_chunk_chars"] == 3
    assert row["first_text_since_sink_start_ms"] == 25
    assert row["append_count"] == 1 and row["append_total_ms"] == 60
    assert row["append_max_ms"] == 60 and row["append_slow_count"] == 1
    assert row["sink_arrival_gap_count"] == 1
    assert row["sink_arrival_gap_max_ms"] == 120
    assert row["sink_arrival_gap_slow_count"] == 1
    assert all(value is None or isinstance(value, (str, int, float)) for value in row.values())


def test_failed_stage_is_measured_without_replacing_exception():
    clock, summaries = _Clock(), []
    metrics = ChatStreamDiagnostics(clock=clock, publish=summaries.append)
    failure = RuntimeError("private provider detail")
    with pytest.raises(RuntimeError) as caught:
        with metrics.measure("append"):
            clock.advance(0.075)
            raise failure
    assert caught.value is failure
    metrics.finish(outcome="failed", task_id="task-1", execution_attempt=1, stream_id=None)
    assert summaries[0]["append_max_ms"] == 75
    assert summaries[0]["first_text_since_sink_start_ms"] is None
    assert "private provider detail" not in json.dumps(summaries)


@pytest.mark.asyncio
async def test_sink_stage_metrics_preserve_delivery_and_progress_order():
    clock, summaries, calls, sent = _Clock(), [], [], []

    class Store:
        async def begin(self, **_kwargs):
            return DeliverySession("session-1", "stream-1", 2, 0, 0, "", [])

        async def append(self, **kwargs):
            calls.append("append")
            clock.advance(0.060)
            return {"outcome": "appended", "delivery_seq": len(sent) + 1}

        async def save_snapshot(self, **_kwargs):
            calls.append("snapshot")
            clock.advance(0.090)
            return {"outcome": "saved"}

    class DB:
        def rpc(self, _name, _params):
            async def execute():
                calls.append("progress")
                clock.advance(0.080)
                return SimpleNamespace(data={"outcome": "updated"})
            return SimpleNamespace(execute=execute)

    class WS:
        async def send_to_task_or_user(self, _task, _user, message, **_kwargs):
            calls.append("send")
            clock.advance(0.030)
            sent.append(message)

    sink = ActorWebSink(
        DB(), ActorDelivery("task-1", "client-1", "private-token", "conv-1", "message-1", "private-user", "org-1", "model-1", 2),
        asyncio.Event(), WS(), Store(),
    )
    sink._diagnostics = ChatStreamDiagnostics(clock=clock, publish=summaries.append)
    for _ in range(21):
        await sink.on_text("秘密正文")
    sink.finish_diagnostics("generated")
    row = summaries[0]
    assert row["text_chunks"] == 21 and row["text_chars"] == 84
    assert row["append_count"] == row["send_count"] == 21
    assert row["append_total_ms"] == 1260
    assert row["send_total_ms"] == 630
    assert row["progress_max_ms"] == 80 and row["snapshot_max_ms"] == 90
    assert row["persist_max_ms"] == 170
    assert row["sink_arrival_gap_max_ms"] == 260
    assert calls[38:44] == ["append", "send", "progress", "snapshot", "append", "send"]
    assert [message["payload"]["chunk"] for message in sent] == ["秘密正文"] * 21
    serialized = json.dumps(row)
    assert not any(private in serialized for private in ("秘密正文", "private-user", "private-token", "org-1"))
    assert row["stream_id"] == "stream-1" and row["execution_attempt"] == 2


@pytest.mark.asyncio
async def test_summary_publication_is_nonblocking_and_bounded(monkeypatch):
    release, started = asyncio.Event(), asyncio.Event()
    published = []

    async def held_thread(function, *args):
        started.set()
        await release.wait()
        function(*args)

    monkeypatch.setattr(chat_stream, "_MAX_PENDING_SUMMARIES", 2)
    monkeypatch.setattr(chat_stream, "_pending_summaries", set())
    monkeypatch.setattr(chat_stream.asyncio, "to_thread", held_thread)
    monkeypatch.setattr(chat_stream, "_log_summary", published.append)
    for i in range(100):
        chat_stream.publish_chat_stream_summary({"text_chunks": i})
    assert len(chat_stream._pending_summaries) == 2
    assert not published
    await started.wait()
    release.set()
    await asyncio.gather(*chat_stream._pending_summaries)
    await asyncio.sleep(0)
    assert published == [{"text_chunks": 0}, {"text_chunks": 1}]
    assert not chat_stream._pending_summaries


def test_publisher_failure_never_escapes_finish():
    def broken(_row):
        raise RuntimeError("logging failed")
    ChatStreamDiagnostics(publish=broken).finish(
        outcome="failed", task_id="task-1", execution_attempt=1, stream_id=None,
    )


@pytest.mark.asyncio
async def test_append_failure_keeps_retries_cause_and_does_not_send(monkeypatch):
    clock, summaries, sent = _Clock(), [], []
    failure = ConnectionError("private rpc detail")

    class Store:
        async def begin(self, **_kwargs):
            return DeliverySession("session-1", "stream-1", 2, 0, 0, "", [])

        async def append(self, **_kwargs):
            clock.advance(0.010)
            raise failure

    async def sleep(seconds):
        clock.advance(seconds)

    async def send(*_args, **_kwargs):
        sent.append(True)

    monkeypatch.setattr("services.handlers.chat.actor_sink.asyncio.sleep", sleep)
    sink = ActorWebSink(
        None, ActorDelivery("task-1", "client-1", "token", "conv", "msg", "user", None, "model"),
        asyncio.Event(), SimpleNamespace(send_to_task_or_user=send), Store(),
    )
    sink._diagnostics = ChatStreamDiagnostics(clock=clock, publish=summaries.append)
    with pytest.raises(RuntimeError, match="DELIVERY_EVENT_PERSISTENCE_FAILED") as caught:
        await sink.on_text("private answer")
    assert caught.value.__cause__ is failure
    assert not sent
    sink.finish_diagnostics("failed")
    assert summaries[0]["append_max_ms"] == 180
    assert summaries[0]["send_count"] == 0
    assert summaries[0]["execution_attempt"] == 2
    assert "private" not in json.dumps(summaries)
