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
        "asset_identity": {
            "provider_name": "report.csv",
            "canonical_name": "report.csv",
            "detected_mime_type": "text/csv",
            "detection_source": "content",
            "content_sha256": "a" * 64,
        },
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
    expected_file = {
        key: value for key, value in payload.items()
        if key != "asset_identity"
    }
    assert params["p_content"].obj == [{"type": "file", **expected_file}]
    assert params["p_source_provider_id"] == "provider-1"
    assert params["p_url"] == "https://cdn/report.csv"
    assert params["p_asset_identity"].obj == payload["asset_identity"]
    assert db.rpc.call_args.args[0] == "stage_wecom_attachment_v2"
    assert result.attachment_id == "asset-1"


def test_stage_attachment_requires_normalized_identity() -> None:
    payload = {
        "url": "https://cdn/report.csv",
        "workspace_path": "上传/企微/report.csv",
        "name": "report.csv",
        "mime_type": "text/csv",
        "size": 10,
    }

    try:
        stage_wecom_attachment(
            MagicMock(),
            msgid="provider-1",
            conversation_id="conversation-1",
            sender_user_id="user-1",
            sender_identity="wx-user",
            file_payload=payload,
            storage_scope="user",
        )
    except RuntimeError as error:
        assert str(error) == "WECOM_ATTACHMENT_IDENTITY_MISSING"
    else:
        raise AssertionError("missing identity must be rejected")
