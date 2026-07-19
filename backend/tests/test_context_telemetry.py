"""上下文治理结构化观测事件测试。"""

from unittest.mock import MagicMock, patch

from services.agent.runtime.context.telemetry import record_context_event
from services.handlers.chat.stream_setup import _record_context_receipt


def test_record_context_event_uses_stable_metric_name() -> None:
    bound = MagicMock()
    with patch(
        "services.agent.runtime.context.telemetry.logger.bind",
        return_value=bound,
    ) as bind:
        record_context_event(
            "context_compaction",
            outcome="trimmed",
            trimmed_tokens=120,
        )

    bind.assert_called_once_with(
        metric="gen_ai.context_compaction",
        outcome="trimmed",
        trimmed_tokens=120,
    )
    bound.info.assert_called_once_with("context_compaction")


def test_unknown_context_event_is_ignored() -> None:
    with patch(
        "services.agent.runtime.context.telemetry.logger.bind",
    ) as bind:
        record_context_event("unregistered", content="sensitive")

    bind.assert_not_called()


def test_unapproved_fields_cannot_enter_context_metrics() -> None:
    bound = MagicMock()
    with patch(
        "services.agent.runtime.context.telemetry.logger.bind",
        return_value=bound,
    ) as bind:
        record_context_event(
            "context_receipt",
            task_id="task-1",
            content="sensitive body",
            tool_output={"token": "secret"},
        )

    bind.assert_called_once_with(
        metric="gen_ai.context_receipt",
        task_id="task-1",
    )


def test_receipt_emits_token_metrics_without_content() -> None:
    messages = [{"role": "user", "content": "敏感问题正文"}]
    with patch(
        "services.agent.runtime.context.record_context_event",
    ) as record:
        _record_context_receipt(
            messages=messages,
            tools=[],
            conversation_id="conv-1",
            task_id="task-1",
            model_id="model-1",
        )

    fields = record.call_args.kwargs
    assert fields["context_estimated_tokens"] > 0
    assert fields["context_tokens_by_kind"]["text"] > 0
    assert "敏感问题正文" not in str(fields)
