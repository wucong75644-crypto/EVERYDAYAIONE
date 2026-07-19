"""模型主动 Memory Search/Get 与个人上下文隔离测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.memory_tools import build_memory_tools
from services.agent.memory_tool_mixin import MemoryToolMixin
from services.handlers.chat.stream_setup import _prepare_permission_and_tools
from services.memory.retrieval_pipeline import ScoredMemory


class _Executor(MemoryToolMixin):
    def __init__(self) -> None:
        self.db = MagicMock()
        self.user_id = "user-1"
        self.org_id = "org-1"


def _tool_names(tools: list[dict]) -> set[str]:
    return {tool["function"]["name"] for tool in tools}


def test_memory_tool_schemas_are_bounded_and_use_stable_ref() -> None:
    tools = build_memory_tools()

    assert _tool_names(tools) == {"memory_search", "memory_get"}
    search = next(
        tool for tool in tools
        if tool["function"]["name"] == "memory_search"
    )
    assert search["function"]["parameters"]["properties"]["limit"]["maximum"] == 6
    get = next(
        tool for tool in tools
        if tool["function"]["name"] == "memory_get"
    )
    assert get["function"]["parameters"]["required"] == ["memory_ref"]


def test_tool_executor_registers_memory_handlers() -> None:
    from services.agent.tool_executor import ToolExecutor

    executor = ToolExecutor(
        db=MagicMock(),
        user_id="user-1",
        org_id="org-1",
        conversation_id="conversation-1",
    )

    assert executor.has_handler("memory_search")
    assert executor.has_handler("memory_get")


def test_tool_executor_blocks_memory_handlers_without_personal_context() -> None:
    from services.agent.tool_executor import ToolExecutor

    executor = ToolExecutor(
        db=MagicMock(),
        user_id="user-1",
        org_id="org-1",
        conversation_id="conversation-1",
        personal_context_allowed=False,
    )

    assert not executor.has_handler("memory_search")
    assert not executor.has_handler("memory_get")


def test_personal_context_policy_hides_memory_tools() -> None:
    _, allowed = _prepare_permission_and_tools(
        "auto",
        "org-1",
        personal_context_allowed=True,
    )
    _, blocked = _prepare_permission_and_tools(
        "auto",
        "org-1",
        personal_context_allowed=False,
    )

    assert {"memory_search", "memory_get"} <= _tool_names(allowed)
    assert {"memory_search", "memory_get"}.isdisjoint(_tool_names(blocked))


@pytest.mark.asyncio
async def test_memory_search_returns_stable_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _Executor()
    search = AsyncMock(return_value=[
        ScoredMemory(
            atom_id="memory-1",
            content="用户偏好简洁回答",
            kind="preference",
            priority=80,
            score=0.9,
        )
    ])
    monkeypatch.setattr(
        "services.agent.memory_tool_mixin.RetrievalPipeline.search",
        search,
    )

    result = await executor._memory_search({"query": "回答偏好", "limit": 99})
    payload = json.loads(result.summary)

    assert result.status == "success"
    assert payload["memories"][0]["memory_ref"] == "memory:memory-1"
    search.assert_awaited_once_with(
        query="回答偏好",
        user_id="user-1",
        org_id="org-1",
        max_results=6,
    )


@pytest.mark.asyncio
async def test_memory_get_requires_exact_ref_and_returns_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _Executor()
    get = AsyncMock(return_value=ScoredMemory(
        atom_id="memory-1",
        content="用户偏好简洁回答",
        kind="preference",
        priority=80,
        score=1.0,
        source_message_ids=("message-1",),
    ))
    monkeypatch.setattr(
        "services.agent.memory_tool_mixin.RetrievalPipeline.get",
        get,
    )

    invalid = await executor._memory_get({"memory_ref": "memory-1"})
    result = await executor._memory_get({"memory_ref": "memory:memory-1"})
    payload = json.loads(result.summary)

    assert invalid.status == "error"
    assert payload["source_message_ids"] == ["message-1"]
    get.assert_awaited_once_with(
        atom_id="memory-1",
        user_id="user-1",
        org_id="org-1",
    )


@pytest.mark.asyncio
async def test_memory_get_returns_empty_for_stale_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _Executor()
    monkeypatch.setattr(
        "services.agent.memory_tool_mixin.RetrievalPipeline.get",
        AsyncMock(return_value=None),
    )

    result = await executor._memory_get({"memory_ref": "memory:missing"})

    assert result.status == "empty"


@pytest.mark.asyncio
async def test_memory_tools_use_personal_scope_without_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _Executor()
    executor.org_id = None
    search_call = AsyncMock(return_value=[])
    get_call = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "services.agent.memory_tool_mixin.RetrievalPipeline.search",
        search_call,
    )
    monkeypatch.setattr(
        "services.agent.memory_tool_mixin.RetrievalPipeline.get",
        get_call,
    )

    search = await executor._memory_search({"query": "偏好"})
    get = await executor._memory_get({"memory_ref": "memory:memory-1"})

    assert search.status == "empty"
    assert get.status == "empty"
    search_call.assert_awaited_once_with(
        query="偏好",
        user_id="user-1",
        org_id=None,
        max_results=3,
    )
    get_call.assert_awaited_once_with(
        atom_id="memory-1",
        user_id="user-1",
        org_id=None,
    )
