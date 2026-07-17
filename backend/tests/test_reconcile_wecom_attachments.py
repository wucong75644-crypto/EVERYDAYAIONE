"""历史企微附件调和脚本测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts.reconcile_wecom_attachments import (
    ReconcilePlan,
    apply_plan,
    build_plan,
    fetch_attachments,
    print_report,
    resolve_asset_path,
    run,
    update_message_content,
)


def _row(**overrides):
    row = {
        "id": "asset-1",
        "source_message_id": "message-1",
        "source_provider_id": "provider-message-123",
        "org_id": "org-1",
        "conversation_id": "conv-1",
        "sender_user_id": "user-1",
        "storage_scope": "user",
        "workspace_path": "上传/企微/file.bin",
        "provider_name": "file.bin",
        "canonical_name": "file.bin",
        "detected_mime_type": "application/octet-stream",
        "detection_source": "legacy",
        "content_sha256": None,
        "size": 78,
        "corp_id": None,
        "external_chat_id": None,
    }
    row.update(overrides)
    return row


def test_build_plan_recovers_csv_from_legacy_bin(tmp_path: Path) -> None:
    target = (
        tmp_path / "org" / "org-1" / "user-1"
        / "上传" / "企微" / "file.bin"
    )
    target.parent.mkdir(parents=True)
    target.write_text("月份,销售额\n1月,120\n2月,180\n")

    plan = build_plan(_row(), tmp_path)

    assert plan.status == "update"
    assert plan.identity is not None
    assert plan.identity.canonical_name == "企微文件_provider-mes.csv"
    assert plan.identity.detected_mime_type == "text/csv"


def test_resolve_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside_scope"):
        resolve_asset_path(
            _row(workspace_path="../../other/file.csv"),
            tmp_path,
        )


def test_missing_channel_binding_is_reported(tmp_path: Path) -> None:
    plan = build_plan(
        _row(storage_scope="channel", corp_id=None),
        tmp_path,
    )

    assert plan.status == "unsafe_path"
    assert plan.reason == "channel_binding_missing"


def test_update_message_content_changes_only_file_identity() -> None:
    from services.assets.file_identity import identify_file

    identity = identify_file(
        b"a,b\n1,2\n",
        stable_id="provider",
        provider_name="report.bin",
    )
    content = update_message_content(
        '[{"type":"text","text":"分析"},'
        '{"type":"file","asset_id":"asset-1","url":"secret-url",'
        '"workspace_path":"上传/file.bin","name":"file.bin",'
        '"mime_type":"application/octet-stream"}]',
        "asset-1",
        identity,
    )

    assert content[0] == {"type": "text", "text": "分析"}
    assert content[1]["name"] == "report.csv"
    assert content[1]["url"] == "secret-url"
    assert content[1]["workspace_path"] == "上传/file.bin"


def test_apply_plan_updates_attachment_and_source_message() -> None:
    from services.assets.file_identity import identify_file
    from scripts.reconcile_wecom_attachments import ReconcilePlan

    cursor = _Cursor()
    identity = identify_file(
        b"a,b\n1,2\n", stable_id="provider", provider_name=None,
    )
    plan = ReconcilePlan(
        attachment_id="asset-1",
        source_message_id="message-1",
        workspace_path="上传/file.bin",
        identity=identity,
        status="update",
    )

    apply_plan(cursor, plan)

    assert len(cursor.executions) == 3
    saved = cursor.executions[-1][1][0]
    assert '"asset_id": "asset-1"' in saved
    assert "企微文件_provider.csv" in saved


def test_fetch_attachments_limits_before_row_lock() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"id": "asset-1"}]

    rows = fetch_attachments(
        cursor, org_id="org-1", limit=10, lock=True,
    )

    sql = cursor.execute.call_args.args[0]
    assert sql.index("LIMIT %s") < sql.index("FOR UPDATE OF a")
    assert cursor.execute.call_args.args[1] == ["org-1", 10]
    assert rows == [{"id": "asset-1"}]


def test_unchanged_plan_and_missing_file(tmp_path: Path) -> None:
    target = (
        tmp_path / "org" / "org-1" / "user-1"
        / "上传" / "企微" / "file.bin"
    )
    target.parent.mkdir(parents=True)
    data = b"a,b\n1,2\n"
    target.write_bytes(data)
    from services.assets.file_identity import identify_file

    identity = identify_file(
        data, stable_id="provider-message-123", provider_name=None,
    )
    unchanged = build_plan(_row(
        provider_name=None,
        canonical_name=identity.canonical_name,
        detected_mime_type=identity.detected_mime_type,
        detection_source=identity.detection_source,
        content_sha256=identity.content_sha256,
        size=identity.size,
    ), tmp_path)
    missing = build_plan(
        _row(workspace_path="上传/企微/missing.bin"),
        tmp_path,
    )

    assert unchanged.status == "unchanged"
    assert missing.status == "missing"


def test_report_does_not_print_urls(capsys) -> None:
    plan = ReconcilePlan(
        attachment_id="asset-1",
        source_message_id="message-1",
        workspace_path="上传/file.bin",
        identity=None,
        status="missing",
        reason="workspace_file_missing",
    )

    print_report([plan], apply=False)

    output = capsys.readouterr().out
    assert "DRY-RUN" in output
    assert "http" not in output


def test_run_defaults_to_rollback_dry_run(tmp_path: Path) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    args = SimpleNamespace(
        apply=False, dry_run=False, org_id=None, limit=None,
    )
    plan = ReconcilePlan(
        attachment_id="asset-1",
        source_message_id="message-1",
        workspace_path="上传/file.bin",
        identity=None,
        status="missing",
        reason="workspace_file_missing",
    )
    with (
        patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}),
        patch(
            "scripts.reconcile_wecom_attachments.psycopg.connect",
            return_value=connection,
        ),
        patch(
            "scripts.reconcile_wecom_attachments.get_settings",
            return_value=SimpleNamespace(file_workspace_root=str(tmp_path)),
        ),
        patch(
            "scripts.reconcile_wecom_attachments.fetch_attachments",
            return_value=[_row()],
        ),
        patch(
            "scripts.reconcile_wecom_attachments.build_plan",
            return_value=plan,
        ),
        patch("scripts.reconcile_wecom_attachments.print_report"),
    ):
        result = run(args)

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    assert result == 1


class _Cursor:
    def __init__(self):
        self.executions = []

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        return {
            "content": [{
                "type": "file",
                "url": "cdn",
                "workspace_path": "上传/file.bin",
                "name": "file.bin",
                "mime_type": "application/octet-stream",
            }],
        }
