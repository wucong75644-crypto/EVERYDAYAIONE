"""统一上下文迁移与历史回填契约测试。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import backfill_conversation_context_items as backfill
from scripts.backfill_conversation_context_items import (
    Projection,
    decode_content,
    insert_projection,
    iter_rows,
    project_message,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "138_unified_conversation_context.sql"
ROLLBACK = ROOT / "migrations" / "rollback" / (
    "138_unified_conversation_context_rollback.sql"
)


def _row(content: list[dict], *, role: str = "assistant") -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "conversation_id": "22222222-2222-2222-2222-222222222222",
        "org_id": "33333333-3333-3333-3333-333333333333",
        "task_id": "44444444-4444-4444-4444-444444444444",
        "turn_id": "55555555-5555-5555-5555-555555555555",
        "role": role,
        "content": content,
        "context_revision": 7,
    }


def test_schema_has_bounded_tenant_scoped_fact_tables() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for table in (
        "conversation_artifacts",
        "conversation_context_items",
        "conversation_compactions",
        "conversation_context_receipts",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
    assert "pg_column_size(payload) <= 262144" in sql
    assert "idx_conversation_artifacts_content_hash" in sql
    assert "conversation_artifacts_identity_unique" in sql
    assert "conversation_context_items_sequence_unique" in sql


def test_vnext_commit_reuses_existing_fenced_commit() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "p_context_items JSONB" in sql
    assert "p_artifacts JSONB" in sql
    assert "p_context_receipts JSONB" in sql
    assert "SELECT commit_generation_turn(" in sql
    assert "p_data_evidence" in sql
    assert "v_revision * 1000" in sql
    assert "ON CONFLICT (task_id, local_sequence) DO NOTHING" in sql
    assert "rolname = 'service_role'" in sql
    assert "TO service_role" in sql


def test_tool_step_projects_atomic_pair_and_message_slice() -> None:
    projection = project_message(_row([{
        "type": "tool_step",
        "tool_call_id": "call-1",
        "tool_name": "file_analyze",
        "input": {"file_id": "file-1"},
        "status": "completed",
        "output": "细节" * 50000,
    }]))

    assert [item["item_type"] for item in projection.items] == [
        "tool_call",
        "tool_result",
    ]
    assert projection.items[0]["group_id"] == projection.items[1]["group_id"]
    assert projection.artifacts[0]["storage_kind"] == "message_slice"
    assert projection.artifacts[0]["byte_size"] > 64 * 1024
    assert projection.artifacts[0]["model_view"]["truncated"] is True


def test_projection_is_idempotent_and_role_sequences_do_not_collide() -> None:
    assistant = project_message(_row([{"type": "text", "text": "回答"}]))
    repeated = project_message(_row([{"type": "text", "text": "回答"}]))
    user = project_message(_row(
        [{"type": "text", "text": "问题"}], role="user"
    ))

    assert assistant == repeated
    assert assistant.items[0]["local_sequence"] == 500
    assert user.items[0]["local_sequence"] == 0
    assert assistant.items[0]["sequence"] != user.items[0]["sequence"]


def test_identical_results_from_distinct_calls_keep_distinct_artifacts() -> None:
    first = project_message({
        **_row([{
            "type": "tool_step",
            "tool_call_id": "call-1",
            "tool_name": "analy",
            "status": "completed",
            "output": {"total": 42},
        }]),
        "id": "11111111-1111-1111-1111-111111111101",
    })
    second = project_message({
        **_row([{
            "type": "tool_step",
            "tool_call_id": "call-2",
            "tool_name": "analy",
            "status": "completed",
            "output": {"total": 42},
        }]),
        "id": "11111111-1111-1111-1111-111111111102",
    })

    assert first.artifacts[0]["content_hash"] == second.artifacts[0]["content_hash"]
    assert first.artifacts[0]["id"] != second.artifacts[0]["id"]
    assert first.artifacts[0]["tool_call_id"] == "call-1"
    assert second.artifacts[0]["tool_call_id"] == "call-2"


def test_oversized_plain_block_becomes_artifact_ref() -> None:
    projection = project_message(_row(
        [{"type": "text", "text": "x" * (300 * 1024)}],
        role="user",
    ))

    assert projection.items[0]["item_type"] == "artifact_ref"
    assert projection.artifacts[0]["storage_kind"] == "message_slice"


def test_rollback_keeps_fact_tables_for_safe_application_rollback() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "DROP FUNCTION IF EXISTS commit_generation_turn" in sql
    assert "DROP TABLE" not in sql
    assert "REVOKE INSERT, UPDATE, DELETE" in sql
    assert "rolname = 'service_role'" in sql


def test_string_and_empty_content_have_stable_projection() -> None:
    string_projection = project_message({
        **_row([], role="user"),
        "content": "直接文本",
    })
    empty_projection = project_message({
        **_row([], role="user"),
        "content": None,
    })

    assert string_projection.items[0]["payload"]["content"]["text"] == "直接文本"
    assert empty_projection.items[0]["payload"]["content"]["text"] == ""
    assert decode_content('{"not": "blocks"}')[0]["text"] == '{"not": "blocks"}'
    assert decode_content(123) == []


def test_failed_tool_and_large_arguments_are_artifactized() -> None:
    projection = project_message(_row([{
        "type": "tool_step",
        "name": "dangerous_tool",
        "arguments": {"value": "x" * (300 * 1024)},
        "status": "error",
        "result": {"error": "拒绝执行"},
    }]))

    call_item, result_item = projection.items
    assert "artifact_id" in call_item["payload"]["arguments"]
    assert result_item["payload"]["is_error"] is True
    assert {item["artifact_type"] for item in projection.artifacts} == {
        "json",
        "error",
    }


def test_running_tool_without_output_only_projects_call() -> None:
    projection = project_message(_row([{
        "type": "tool_step",
        "tool_name": "file_read",
        "status": "running",
    }]))

    assert [item["item_type"] for item in projection.items] == ["tool_call"]
    assert projection.artifacts == ()


def test_standalone_tool_result_becomes_retrievable_artifact() -> None:
    projection = project_message(_row([{
        "type": "tool_result",
        "tool_name": "erp_agent",
        "text": "查询结果",
    }]))

    assert projection.items[0]["item_type"] == "artifact_ref"
    assert projection.artifacts[0]["tool_name"] == "erp_agent"
    assert projection.artifacts[0]["model_view"]["truncated"] is False


def test_insert_projection_adapts_json_and_is_conflict_safe() -> None:
    projection = project_message(_row([{
        "type": "tool_result",
        "tool_name": "erp_agent",
        "text": "查询结果",
    }]))
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context

    insert_projection(connection, projection)

    assert cursor.execute.call_count == 2
    artifact_sql = cursor.execute.call_args_list[0].args[0]
    item_sql = cursor.execute.call_args_list[1].args[0]
    assert "ON CONFLICT (id) DO NOTHING" in artifact_sql
    assert "ON CONFLICT (conversation_id, sequence)" in item_sql


def test_iter_rows_uses_holdable_server_cursor() -> None:
    cursor = MagicMock()
    cursor.fetchmany.side_effect = [
        [{"id": "message-1"}],
        [],
    ]
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context

    batches = list(iter_rows(connection, 25))

    assert batches == [[{"id": "message-1"}]]
    connection.cursor.assert_called_once_with(
        name="context_backfill",
        row_factory=backfill.psycopg.rows.dict_row,
        withhold=True,
    )
    cursor.fetchmany.assert_any_call(25)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (["--batch-size", "0"], "batch-size and limit must be positive"),
        (["--limit", "0"], "batch-size and limit must be positive"),
    ],
)
def test_main_rejects_non_positive_limits(
    arguments: list[str], error: str
) -> None:
    with patch("sys.argv", ["backfill", *arguments]):
        with pytest.raises(SystemExit) as exc:
            backfill.main()

    assert exc.value.code == 2


def test_main_dry_run_scans_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    row = _row([{"type": "text", "text": "回答"}])
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    connect = MagicMock(return_value=connection_context)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    with (
        patch("sys.argv", ["backfill", "--limit", "1"]),
        patch.object(backfill, "load_env"),
        patch.object(backfill.psycopg, "connect", connect),
        patch.object(backfill, "iter_rows", return_value=iter([[row]])),
        patch.object(backfill, "insert_projection") as insert,
    ):
        outcome = backfill.main()

    assert outcome == 0
    insert.assert_not_called()
    connection.rollback.assert_called_once()
    assert "dry-run: messages=1 items=1 artifacts=0" in capsys.readouterr().out


def test_main_apply_commits_each_batch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    row = _row([{"type": "text", "text": "回答"}])
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    with (
        patch("sys.argv", ["backfill", "--apply"]),
        patch.object(backfill, "load_env"),
        patch.object(
            backfill.psycopg,
            "connect",
            return_value=connection_context,
        ),
        patch.object(backfill, "iter_rows", return_value=iter([[row]])),
        patch.object(backfill, "insert_projection") as insert,
    ):
        outcome = backfill.main()

    assert outcome == 0
    insert.assert_called_once()
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    assert "apply: messages=1 items=1 artifacts=0" in capsys.readouterr().out
