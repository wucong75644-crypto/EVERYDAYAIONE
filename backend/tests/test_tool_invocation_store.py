"""Actor 工具幂等存储与结果回放测试。"""

from __future__ import annotations

from types import SimpleNamespace

from services.agent.agent_result import AgentResult
from services.tool_invocation_store import (
    DatabaseToolInvocationStore,
    deserialize_tool_result,
    hash_tool_arguments,
    serialize_tool_result,
)


class _RpcCall:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return SimpleNamespace(data=self._data)


class _DB:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall({"outcome": "execute"})


def test_tool_argument_hash_is_canonical():
    assert hash_tool_arguments({"b": 2, "a": 1}) == hash_tool_arguments(
        {"a": 1, "b": 2}
    )
    assert len(hash_tool_arguments({})) == 64


def test_database_store_uses_begin_and_complete_rpc():
    db = _DB()
    store = DatabaseToolInvocationStore(db)

    assert store.mark_stale(
        task_id="task-1",
        turn_id="turn-1",
        tool_call_id="tool-1",
        execution_token="token-1",
    )["outcome"] == "execute"

    assert store.begin(
        task_id="task-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        execution_token="token-1",
        tool_call_id="tool-1",
        tool_name="erp_execute",
        args_hash="a" * 64,
    )["outcome"] == "execute"
    assert store.complete(
        task_id="task-1",
        turn_id="turn-1",
        tool_call_id="tool-1",
        execution_token="token-1",
        status="succeeded",
        result={"kind": "scalar", "value": "ok"},
    )["outcome"] == "execute"
    assert db.calls[0][0] == "mark_stale_tool_invocation_uncertain"
    assert db.calls[1][0] == "begin_tool_invocation"
    assert db.calls[2][0] == "complete_tool_invocation"


def test_agent_result_replay_preserves_tool_content_and_status():
    original = AgentResult(
        summary="写入完成",
        status="success",
        source="erp",
        emit_payloads=[{"kind": "file", "url": "https://example.test/a"}],
    )
    replayed = deserialize_tool_result(serialize_tool_result(original))

    assert isinstance(replayed, AgentResult)
    assert replayed.summary == "写入完成"
    assert replayed.status == "success"
    assert replayed.emit_payloads[0]["kind"] == "file"
