"""受控 Evidence Search/Get 工具测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from config.evidence_tools import build_evidence_tools
from services.agent.runtime.runtime_state import RuntimeState
from services.agent.tool_executor import ToolExecutor
from services.handlers.chat.stream_setup import _prepare_permission_and_tools


def _query(data):
    query = MagicMock()
    for method in (
        "select", "eq", "lte", "lt", "order", "range", "maybe_single",
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=data)
    return query


def _executor(
    data,
    *,
    conversation_id: str = "conv-1",
    base_revision: int = 7,
):
    query = _query(data)
    db = MagicMock()
    db.table.return_value = query
    state = RuntimeState(
        conversation_id=conversation_id,
        base_revision=base_revision,
    )
    return (
        ToolExecutor(
            db,
            user_id="user-1",
            conversation_id=conversation_id,
            org_id="org-1",
            runtime_state=state,
        ),
        query,
    )


def test_evidence_tool_schemas_are_read_only_and_bounded() -> None:
    tools = build_evidence_tools()
    functions = {tool["function"]["name"]: tool["function"] for tool in tools}

    assert set(functions) == {"evidence_search", "evidence_get"}
    assert functions["evidence_search"]["parameters"]["properties"][
        "limit"
    ]["maximum"] == 10
    assert "before_revision" in functions[
        "evidence_search"
    ]["parameters"]["properties"]
    assert functions["evidence_get"]["parameters"]["properties"][
        "max_tokens"
    ]["maximum"] == 4000


def test_evidence_tools_open_only_when_snapshot_has_evidence() -> None:
    with patch("config.chat_tools.get_tools_for_mode", return_value=[]):
        _, without_evidence = _prepare_permission_and_tools(
            "auto", "org-1", True, evidence_available=False,
        )
        _, with_evidence = _prepare_permission_and_tools(
            "auto", "org-1", True, evidence_available=True,
        )

    assert {
        tool["function"]["name"] for tool in without_evidence
    } == {
        "artifact_search", "artifact_get", "artifact_read",
        "memory_search", "memory_get",
    }
    assert {
        tool["function"]["name"] for tool in with_evidence
    } == {
        "artifact_search",
        "artifact_get",
        "artifact_read",
        "memory_search",
        "memory_get",
        "evidence_search",
        "evidence_get",
    }


@pytest.mark.asyncio
async def test_search_is_bound_to_conversation_and_base_revision() -> None:
    executor, query = _executor([
        {
            "artifact_id": "artifact-orders",
            "source": "erp_agent",
            "columns": [{"name": "platform"}],
            "query_scope": {"date": "2026-07-17"},
            "model_view": {"row_count": 2, "tier": "full"},
            "byte_size": 100,
            "context_revision": 6,
        },
        {
            "artifact_id": "artifact-stock",
            "source": "erp_agent",
            "columns": [{"name": "sku"}],
            "query_scope": {},
            "model_view": {"row_count": 3, "tier": "full"},
            "byte_size": 120,
            "context_revision": 5,
        },
    ])

    result = await executor.execute(
        "evidence_search", {"query": "platform", "limit": 10},
    )

    query.eq.assert_any_call("conversation_id", "conv-1")
    query.lte.assert_called_once_with("context_revision", 7)
    query.eq.assert_any_call("validation_status", "ready")
    assert "artifact-orders" in result.summary
    assert "artifact-stock" not in result.summary


@pytest.mark.asyncio
async def test_get_returns_one_scoped_artifact_with_token_bound() -> None:
    executor, query = _executor({
        "artifact_id": "artifact-1",
        "source": "erp_agent",
        "columns": [{"name": "value"}],
        "rows": [{"value": "数" * 1000} for _ in range(20)],
        "file_ref": None,
        "query_scope": {},
        "metric_definitions": {},
        "model_view": {
            "artifact_id": "artifact-1",
            "tier": "sampled",
            "row_count": 20,
            "sample_rows": [{"value": "数" * 1000} for _ in range(6)],
        },
        "byte_size": 60000,
        "context_revision": 6,
    })

    result = await executor.execute(
        "evidence_get",
        {
            "artifact_id": "artifact-1",
            "selector": "rows",
            "max_tokens": 256,
        },
    )

    query.eq.assert_any_call("conversation_id", "conv-1")
    query.eq.assert_any_call("artifact_id", "artifact-1")
    query.lte.assert_called_once_with("context_revision", 7)
    assert len(result.summary) <= int(256 * 2.5)
    assert "artifact-1" in result.summary


@pytest.mark.asyncio
async def test_search_cursor_can_reach_older_revision_pages() -> None:
    rows = [
        {
            "artifact_id": f"artifact-{index}",
            "source": "erp_agent",
            "columns": [],
            "query_scope": {},
            "model_view": {"row_count": 1, "tier": "full"},
            "byte_size": 10,
            "context_revision": 500 - index,
        }
        for index in range(200)
    ]
    executor, query = _executor(rows, base_revision=500)

    result = await executor.execute(
        "evidence_search",
        {"query": "missing", "before_revision": 501, "limit": 5},
    )

    query.lt.assert_called_once_with("context_revision", 501)
    assert '"next_before_revision":301' in result.summary


@pytest.mark.asyncio
async def test_get_without_fixed_scope_never_queries_database() -> None:
    db = MagicMock()
    executor = ToolExecutor(
        db,
        user_id="user-1",
        conversation_id="conv-1",
        runtime_state=RuntimeState(),
    )

    result = await executor.execute(
        "evidence_get", {"artifact_id": "artifact-1"},
    )

    assert result.status == "error"
    db.table.assert_not_called()
