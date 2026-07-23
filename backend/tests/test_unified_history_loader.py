"""统一 ConversationItem 历史投影测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.handlers.chat_context.unified_history_loader import (
    _project_item,
    load_unified_context_messages,
)


def _db_with_rows(rows, compactions=None):
    compaction_query = MagicMock()
    for method in ("select", "eq", "lte", "order", "range"):
        getattr(compaction_query, method).return_value = compaction_query
    compaction_query.execute.return_value = SimpleNamespace(
        data=compactions or [],
    )
    query = MagicMock()
    for method in ("select", "eq", "lte", "gt", "order", "range"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=rows)
    db = MagicMock()
    db.table.side_effect = [compaction_query, query]
    return db, query


def test_projects_ordered_turn_and_preserves_atomic_tool_protocol():
    rows = [
        {
            "item_type": "assistant",
            "sequence": 1003,
            "payload": {"content": {"type": "text", "text": "分析完成"}},
        },
        {
            "item_type": "tool_result",
            "sequence": 1002,
            "group_id": "group-1",
            "payload": {
                "tool_call_id": "call-1",
                "artifact_id": "artifact-1",
                "model_view": {"preview": "平台统计"},
            },
        },
        {
            "item_type": "tool_call",
            "sequence": 1001,
            "group_id": "group-1",
            "payload": {
                "tool_call_id": "call-1",
                "tool_name": "analy",
                "arguments": {"path": "/tmp/orders.csv"},
            },
        },
        {
            "item_type": "user",
            "sequence": 1000,
            "payload": {
                "content": [{"type": "text", "text": "分析订单"}],
            },
        },
    ]
    db, query = _db_with_rows(rows)

    messages = load_unified_context_messages(
        db,
        conversation_id="conv-1",
        base_revision=8,
        org_id="org-1",
    )

    assert [message["role"] for message in messages] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "analy"
    assert messages[2]["tool_call_id"] == "call-1"
    assert "artifact-1" in messages[2]["content"]
    assert messages[3]["content"] == "分析完成"
    query.lte.assert_called_once_with("context_revision", 8)
    query.gt.assert_not_called()
    query.eq.assert_any_call("org_id", "org-1")
    query.range.assert_called_once_with(0, 199)


def test_empty_new_conversation_is_valid():
    db, _ = _db_with_rows([])

    messages = load_unified_context_messages(
        db,
        conversation_id="conv-1",
        base_revision=0,
        org_id=None,
    )

    assert messages == []


def test_missing_projected_history_fails_closed():
    db, _ = _db_with_rows([])

    import pytest

    with pytest.raises(RuntimeError, match="UNIFIED_CONTEXT_HISTORY_MISSING"):
        load_unified_context_messages(
            db,
            conversation_id="conv-1",
            base_revision=1,
            org_id=None,
        )


def test_latest_compaction_replaces_covered_prefix():
    db, query = _db_with_rows(
        [{
            "item_type": "user",
            "sequence": 2000,
            "context_revision": 2,
            "payload": {"content": [{"type": "text", "text": "近期问题"}]},
        }],
        compactions=[{
            "id": "compaction-1",
            "through_sequence": 1500,
            "context_revision": 1,
            "summary_payload": {"facts": ["旧事实"]},
        }],
    )

    messages = load_unified_context_messages(
        db,
        conversation_id="conv-1",
        base_revision=2,
        org_id="org-1",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "旧事实" in messages[0]["content"]
    assert messages[1]["content"] == "近期问题"
    query.gt.assert_called_once_with("sequence", 1500)


def test_scan_limit_fails_instead_of_silently_dropping_old_items():
    full_page = [
        {
            "item_type": "assistant",
            "sequence": index,
            "payload": {"content": {"type": "text", "text": str(index)}},
        }
        for index in range(200)
    ]
    db, _ = _db_with_rows(full_page)

    import pytest

    with pytest.raises(
        RuntimeError,
        match="CONTEXT_ITEM_SCAN_LIMIT_EXCEEDED",
    ):
        load_unified_context_messages(
            db,
            conversation_id="conv-1",
            base_revision=2,
            org_id=None,
        )


@pytest.mark.parametrize(
    ("item_type", "payload", "role", "fragment"),
    [
        ("assistant", {"content": {"type": "text", "text": "回答"}}, "assistant", "回答"),
        ("artifact_ref", {"artifact_id": "artifact-1"}, "assistant", "artifact-1"),
        ("interrupt", {"reason": "user_cancelled"}, "system", "user_cancelled"),
        ("compaction", {"facts": ["事实"]}, "system", "事实"),
        ("tool_result", {"tool_call_id": "call-1", "value": 7}, "tool", "call-1"),
    ],
)
def test_projects_supported_unified_item_types(
    item_type, payload, role, fragment,
):
    messages = _project_item({"item_type": item_type, "payload": payload})

    assert messages[0]["role"] == role
    assert fragment in str(messages[0])
