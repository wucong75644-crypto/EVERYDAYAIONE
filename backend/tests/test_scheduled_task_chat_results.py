"""Task management receipts survive the actual chat sinks and replay checkpoint."""
import pytest

from services.agent.agent_result import AgentResult
from services.handlers.chat_tool_result_mixin import ChatToolResultMixin
from services.handlers.chat.execution_engine import _consume_emit_payloads
from services.handlers.chat.execution_sink import CollectingExecutionSink
from tests.test_tool_production_integration import setup, tc
import tests.test_tool_result_consumption as consumer


@pytest.mark.parametrize("transport", ["legacy", "actor"])
@pytest.mark.parametrize("status", ["draft", "awaiting_approval", "applied", "failed", "conflicted"])
async def test_change_set_is_delivered_and_checkpointed(setup, monkeypatch, transport, status):
    _, root = setup
    change = {"id": "change-test", "resource_type": "scheduled_task", "status": status}
    raw = AgentResult("查看任务操作结果", metadata={"change_set": change})
    monkeypatch.setattr(consumer, "sample", lambda case, root: (
        tc("manage_scheduled_task", {"action": "pause", "task_name": "日报"}), raw,
    ))
    actual, results, host, executor, _ = await consumer.chat_run("pause", transport, root, monkeypatch)
    reference = {"type": "changeset", "change_set_id": "change-test", "resource_type": "scheduled_task"}
    assert actual["checkpoint"]["content_blocks"].count(reference) == 1
    from pydantic import TypeAdapter
    from schemas.message import ContentPart, serialize_content_parts
    from services.handlers.chat.outcome_builder import build_content_parts
    # Final completion overwrites streamed blocks: test the persistence/API boundary.
    final = serialize_content_parts(build_content_parts(
        [{**block, **({"elapsed_ms": 0} if block.get("type") == "tool_step" else {})}
         for block in actual["checkpoint"]["content_blocks"]], fallback_text="",
    ))
    assert final.count(reference) == 1
    assert TypeAdapter(list[ContentPart]).validate_python(final)[-1].change_set_id == "change-test"

    assert results[0][1].metadata["change_set"] == change
    assert host._terminal_change_set_pending is True
    assert host._pending_change_set_blocks == []
    executor.handler.assert_awaited_once()
    messages = [entry[-1] for entry in actual["ws"]]
    assert any(message.get("type") == "content_block_add" and
               message.get("payload", {}).get("block") == reference for message in messages)


@pytest.mark.parametrize("transport", ["legacy", "actor"])
async def test_skill_proposal_is_delivered_and_checkpointed(setup, monkeypatch, transport):
    _, root = setup
    raw = AgentResult("Skill 草稿已创建", metadata={"skill_chat_proposal": {
        "id": "proposal-test",
        "name": "商品图风格拆解",
    }})
    monkeypatch.setattr(consumer, "sample", lambda _case, _root: (
        tc("manage_scheduled_task", {"action": "pause", "task_name": "日报"}), raw,
    ))

    actual, results, host, executor, _ = await consumer.chat_run(
        "skill_proposal", transport, root, monkeypatch,
    )
    reference = {
        "type": "skill_proposal",
        "proposal_id": "proposal-test",
        "title": "商品图风格拆解",
    }
    assert actual["checkpoint"]["content_blocks"].count(reference) == 1

    from pydantic import TypeAdapter
    from schemas.message import ContentPart, serialize_content_parts
    from services.handlers.chat.outcome_builder import build_content_parts
    final = serialize_content_parts(build_content_parts(
        [{**block, **({"elapsed_ms": 0} if block.get("type") == "tool_step" else {})}
         for block in actual["checkpoint"]["content_blocks"]], fallback_text="",
    ))
    assert final.count(reference) == 1
    validated = TypeAdapter(list[ContentPart]).validate_python(final)
    assert validated[-1].proposal_id == "proposal-test"

    assert results[0][1].metadata["skill_chat_proposal"] == raw.metadata["skill_chat_proposal"]
    assert host._terminal_change_set_pending is True
    assert host._pending_change_set_blocks == []
    executor.handler.assert_awaited_once()
    messages = [entry[-1] for entry in actual["ws"]]
    assert any(message.get("type") == "content_block_add" and
               message.get("payload", {}).get("block") == reference for message in messages)


async def test_legacy_result_staging_and_replayed_reference_do_not_duplicate():
    from types import SimpleNamespace
    host = SimpleNamespace(_pending_emit_payloads=[])
    metadata = {"change_set": {"id": "c1", "resource_type": "scheduled_task"}}
    ChatToolResultMixin._stage_change_set(host, metadata)
    ChatToolResultMixin._stage_change_set(host, metadata)
    blocks = [{"type": "changeset", "change_set_id": "c1", "resource_type": "scheduled_task"}]
    sink = CollectingExecutionSink()
    await _consume_emit_payloads(host, blocks, sink)
    assert len(blocks) == 1
    assert sink.blocks == []
    assert host._pending_change_set_blocks == []


@pytest.mark.parametrize("metadata", [{}, {"change_set": None}, {"change_set": {"id": None}}])
def test_other_results_do_not_stop_the_model(metadata):
    from types import SimpleNamespace
    host = SimpleNamespace()
    ChatToolResultMixin._stage_change_set(host, metadata)
    assert not getattr(host, "_terminal_change_set_pending", False)
