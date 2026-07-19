"""Artifact、ConversationItem 与 ContextReceipt 的 Actor 提交测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from schemas.message import TextPart
from services.agent.runtime.artifacts import (
    materialize_artifacts,
    normalize_tool_result,
)
from services.agent.runtime.context import build_turn_context_items


def test_turn_items_preserve_user_and_atomic_tool_pair() -> None:
    draft = normalize_tool_result(
        "完整订单数据",
        tool_call_id="call-1",
        tool_name="erp_agent",
    )

    items = build_turn_context_items(
        input_content=[TextPart(text="查询订单")],
        output_blocks=[
            {
                "type": "tool_step",
                "tool_call_id": "call-1",
                "tool_name": "erp_agent",
                "input": {"date": "yesterday"},
                "status": "completed",
                "output": "完整订单数据",
            },
            {"type": "text", "text": "查询完成"},
        ],
        artifacts=[draft],
        input_message_id="input-1",
        output_message_id="output-1",
    )

    assert [item["item_type"] for item in items] == [
        "user",
        "tool_call",
        "tool_result",
        "assistant",
    ]
    assert items[0]["source_message_id"] == "input-1"
    assert items[1]["source_message_id"] == "output-1"
    assert items[1]["group_id"] == items[2]["group_id"]
    assert items[2]["payload"]["artifact_id"] == draft.artifact_id
    assert [item["local_sequence"] for item in items] == [0, 500, 501, 502]


def test_oversized_message_item_uses_durable_message_reference() -> None:
    items = build_turn_context_items(
        input_content=[TextPart(text="x" * (300 * 1024))],
        output_blocks=[{"type": "text", "text": "完成"}],
        artifacts=[],
        input_message_id="input-large",
        output_message_id="output-large",
    )

    assert items[0]["payload"]["message_ref"]["message_id"] == "input-large"
    assert items[0]["payload"]["byte_size"] > 256 * 1024


def test_missing_tool_draft_keeps_bounded_message_fact() -> None:
    items = build_turn_context_items(
        input_content=[],
        output_blocks=[{
            "type": "tool_step",
            "tool_call_id": "call-missing",
            "tool_name": "tool",
            "status": "error",
            "output": "错误" * 150000,
        }],
        artifacts=[],
        input_message_id="input-1",
        output_message_id="output-1",
    )

    result = items[2]["payload"]
    assert result["message_ref"]["message_id"] == "output-1"


@pytest.mark.asyncio
async def test_small_artifact_materializes_inline_without_oss() -> None:
    draft = normalize_tool_result(
        {"rows": [{"id": 1}]},
        tool_call_id="call-small",
        tool_name="query",
    )

    with patch("services.oss_service.get_oss_service") as get_oss:
        materialized = await materialize_artifacts(
            [draft],
            task_id="task-1",
            user_id="user-1",
            org_id="org-1",
        )

    assert materialized[0]["storage_kind"] == "inline"
    assert materialized[0]["inline_content"] == {"rows": [{"id": 1}]}
    get_oss.assert_not_called()


@pytest.mark.asyncio
async def test_large_artifact_uploads_before_actor_commit() -> None:
    draft = normalize_tool_result(
        "细节" * 40000,
        tool_call_id="call-large",
        tool_name="file_analyze",
    )
    oss = MagicMock()
    oss.upload_bytes.return_value = {
        "object_key": "org/org-1/images/artifacts/task-1/result.json",
        "url": "https://cdn.example/result.json",
    }

    with patch(
        "services.oss_service.get_oss_service",
        return_value=oss,
    ):
        materialized = await materialize_artifacts(
            [draft],
            task_id="task-1",
            user_id="user-1",
            org_id="org-1",
        )

    item = materialized[0]
    assert item["storage_kind"] == "oss"
    assert item["inline_content"] is None
    assert item["storage_ref"]["object_key"].endswith("result.json")
    upload = oss.upload_bytes.call_args
    assert upload.args[2:5] == (
        "json",
        "artifacts/task-1",
        "application/json",
    )


@pytest.mark.asyncio
async def test_partial_materialization_failure_deletes_uploaded_orphan() -> None:
    drafts = [
        normalize_tool_result(
            "甲" * 70000,
            tool_call_id="call-1",
            tool_name="analy",
        ),
        normalize_tool_result(
            "乙" * 70000,
            tool_call_id="call-2",
            tool_name="analy",
        ),
    ]
    oss = MagicMock()
    oss.upload_bytes.side_effect = [
        {
            "object_key": "org/org-1/artifacts/first.json",
            "url": "https://cdn.example/first.json",
        },
        RuntimeError("upload failed"),
    ]

    with patch(
        "services.oss_service.get_oss_service",
        return_value=oss,
    ):
        with pytest.raises(RuntimeError, match="upload failed"):
            await materialize_artifacts(
                drafts,
                task_id="task-1",
                user_id="user-1",
                org_id="org-1",
            )

    oss.delete.assert_called_once_with(
        "org/org-1/artifacts/first.json",
    )


def test_migration_scopes_context_item_to_turn_messages() -> None:
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "138_unified_conversation_context.sql"
    ).read_text(encoding="utf-8")

    assert "ACTOR_CONTEXT_MESSAGE_SCOPE_MISMATCH" in migration
    assert "v_task.input_message_id, p_output_message_id" in migration
    assert "(v_item->>'source_message_id')::UUID" in migration
    assert "ON CONFLICT (id) DO NOTHING" in migration
