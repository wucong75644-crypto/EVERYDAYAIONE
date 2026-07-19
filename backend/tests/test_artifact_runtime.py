"""通用工具 Artifact Runtime 行为测试。"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from config.artifact_tools import build_artifact_tools
from services.agent.agent_result import AgentResult
from services.agent.artifact_tool_mixin import ArtifactToolMixin
from services.agent.runtime.artifacts import (
    ArtifactStore,
    normalize_tool_result,
    project_tool_result,
)
from services.agent.tool_output import ColumnMeta
from services.handlers.chat.tool_loop import apply_tool_results


def _draft(value: object, *, call_id: str = "call-1"):
    return normalize_tool_result(
        value,
        tool_call_id=call_id,
        tool_name="analyze",
    )


def test_normalizer_preserves_plain_string_complete_fact() -> None:
    first = _draft("完整分析结果")
    second = _draft("完整分析结果")

    assert first == second
    assert first.artifact_type == "text"
    assert first.content == "完整分析结果"
    assert first.byte_size > 0
    assert first.model_view == {
        "content": "完整分析结果",
        "truncated": False,
    }


def test_artifact_identity_is_stable_but_conversation_scoped() -> None:
    first = normalize_tool_result(
        "相同结果",
        tool_call_id="call-1",
        tool_name="query",
        conversation_id="conv-1",
    )
    retry = normalize_tool_result(
        "相同结果",
        tool_call_id="call-1",
        tool_name="query",
        conversation_id="conv-1",
    )
    other_tenant_conversation = normalize_tool_result(
        "相同结果",
        tool_call_id="call-1",
        tool_name="query",
        conversation_id="conv-2",
    )

    assert first.artifact_id == retry.artifact_id
    assert first.artifact_id != other_tenant_conversation.artifact_id


def test_normalizer_preserves_agent_result_structured_fields() -> None:
    result = AgentResult(
        summary="统计完成",
        data=[{"amount": Decimal("12.30")}],
        columns=[ColumnMeta(name="amount", dtype="decimal", label="金额")],
        source="erp_agent",
        metadata={"scope": {"date": "yesterday"}},
    )

    draft = _draft(result)

    assert draft.artifact_type == "table"
    assert draft.content["data"] == [{"amount": "12.30"}]
    assert draft.content["columns"][0]["label"] == "金额"
    assert draft.metadata["source"] == "erp_agent"


def test_failed_result_becomes_error_artifact() -> None:
    draft = normalize_tool_result(
        AgentResult(
            summary="执行失败",
            status="error",
            error_message="provider timeout",
        ),
        tool_call_id="failed-call",
        tool_name="any_tool",
        is_error=True,
    )

    assert draft.status == "failed"
    assert draft.artifact_type == "error"
    assert draft.content["error_message"] == "provider timeout"


def test_large_result_projects_reference_instead_of_silent_truncation() -> None:
    value = "开头\n" + "细节" * 30000 + "\n结尾"
    draft = _draft(value)

    projected = project_tool_result(value, draft)

    assert draft.model_view["truncated"] is True
    assert draft.artifact_id in projected
    assert "artifact_read" in projected
    assert "不能把预览当作完整结果" in projected


def test_small_result_keeps_existing_tool_protocol() -> None:
    result = AgentResult(summary="小结果")
    draft = _draft(result)

    assert project_tool_result(result, draft) == [
        {"type": "text", "text": "小结果"}
    ]


def test_store_deduplicates_searches_and_reads_utf8_pages() -> None:
    store = ArtifactStore()
    draft = _draft("中文内容" * 5000)

    assert store.add(draft) is True
    assert store.add(draft) is False
    assert store.search("analyze", limit=5)[0]["artifact_id"] == draft.artifact_id

    first = store.read(draft.artifact_id, max_tokens=256)
    assert first is not None
    assert first.complete is False
    assert first.next_cursor is not None
    second = store.read(
        draft.artifact_id,
        cursor=first.next_cursor,
        max_tokens=256,
    )
    assert second is not None
    assert second.cursor == first.next_cursor
    assert "\ufffd" not in first.content + second.content


def test_store_aligns_arbitrary_cursor_and_handles_missing_id() -> None:
    store = ArtifactStore()
    draft = _draft("中文")
    store.add(draft)

    page = store.read(draft.artifact_id, cursor=2, max_tokens=256)

    assert page is not None
    assert page.cursor >= 2
    assert store.read("missing") is None
    assert store.search("not-found") == ()


def test_artifact_tool_schema_is_read_only_and_bounded() -> None:
    tools = build_artifact_tools()
    functions = {tool["function"]["name"]: tool["function"] for tool in tools}

    assert set(functions) == {
        "artifact_search",
        "artifact_get",
        "artifact_read",
    }
    read = functions["artifact_read"]["parameters"]
    assert read["properties"]["max_tokens"]["maximum"] == 16000
    assert read["properties"]["cursor"]["minimum"] == 0


class _Executor(ArtifactToolMixin):
    def __init__(self, store: ArtifactStore | None) -> None:
        self.runtime_state = (
            SimpleNamespace(artifacts=store) if store is not None else None
        )


@pytest.mark.asyncio
async def test_artifact_tools_search_get_and_read() -> None:
    store = ArtifactStore()
    draft = _draft("订单细节" * 2000)
    store.add(draft)
    executor = _Executor(store)

    search = await executor._artifact_search({"query": "analyze"})
    get = await executor._artifact_get({"artifact_id": draft.artifact_id})
    read = await executor._artifact_read({
        "artifact_id": draft.artifact_id,
        "max_tokens": 256,
    })

    assert search.status == "success"
    assert draft.artifact_id in search.summary
    assert get.status == "success"
    assert read.status == "success"
    assert "next_cursor" in read.summary


@pytest.mark.asyncio
async def test_artifact_tools_enforce_current_runtime_scope() -> None:
    executor = _Executor(None)

    search = await executor._artifact_search({})
    get = await executor._artifact_get({"artifact_id": "outside"})
    read = await executor._artifact_read({"artifact_id": ""})

    assert search.status == "empty"
    assert get.status == "empty"
    assert read.status == "error"


def test_chat_consumer_registers_all_tool_results_without_name_whitelist() -> None:
    from services.agent.runtime.runtime_state import RuntimeState

    state = RuntimeState.observing()
    messages: list[dict] = []

    apply_tool_results(
        tool_results=[
            (
                {"id": "custom-call", "name": "future_unknown_tool"},
                "完整返回",
                False,
                "完整返回",
            )
        ],
        messages=messages,
        content_blocks=[],
        start_times={},
        tool_context=SimpleNamespace(update_from_result=lambda *_: None),
        runtime_state=state,
    )

    assert messages[0]["content"] == "完整返回"
    assert state.artifacts.snapshot()[0].tool_name == "future_unknown_tool"
