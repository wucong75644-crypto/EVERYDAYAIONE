from types import SimpleNamespace
from unittest.mock import MagicMock

from services.wecom.attachment_service import stage_wecom_attachment


def test_stage_attachment_uses_stable_message_id_and_filepart() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(data={
        "attachment_id": "asset-1",
        "message_id": "message-1",
        "already_staged": False,
    })
    payload = {
        "url": "https://cdn/report.csv",
        "workspace_path": "上传/企微/report.csv",
        "name": "report.csv",
        "mime_type": "text/csv",
        "size": 10,
    }

    result = stage_wecom_attachment(
        db,
        msgid="provider-1",
        conversation_id="conversation-1",
        sender_user_id="user-1",
        sender_identity="wx-user",
        file_payload=payload,
        storage_scope="user",
    )

    params = db.rpc.call_args.args[1]
    assert params["p_content"].obj == [{"type": "file", **payload}]
    assert params["p_source_provider_id"] == "provider-1"
    assert params["p_url"] == "https://cdn/report.csv"
    assert result.attachment_id == "asset-1"
