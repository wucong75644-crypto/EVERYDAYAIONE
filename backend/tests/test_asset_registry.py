"""统一用户资产原子登记服务测试。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.exceptions import AppException
from services.assets.asset_identity import (
    AssetIdentityError,
    CanonicalAssetIdentity,
)
from services.assets.asset_registry import (
    AssetRefDraft,
    AssetRegistryService,
    ReadyAssetDraft,
    register_message_media_best_effort,
    register_task_media_best_effort,
    register_wecom_attachment_best_effort,
    register_web_upload_best_effort,
)

USER_ID = "00000000-0000-4000-8000-000000000001"
ORG_ID = "00000000-0000-4000-8000-000000000002"
CHANNEL_OWNER = "channels/wecom/0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def mock_identity_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.assets.asset_registry.resolve_asset_identity",
        lambda **_kwargs: CanonicalAssetIdentity(
            "workspace", "下载/generated.png",
        ),
    )


def _asset(**overrides) -> ReadyAssetDraft:
    values = {
        "org_id": ORG_ID,
        "storage_scope": "user",
        "storage_owner_key": USER_ID,
        "media_type": "image",
        "original_url": "https://cdn.example/generated.png",
        "download_url": "https://cdn.example/generated.png",
        "workspace_path": "下载/generated.png",
        "name": "generated.png",
    }
    values.update(overrides)
    return ReadyAssetDraft(**values)


def _ref(**overrides) -> AssetRefDraft:
    values = {
        "ref_key": "task:task-1:0",
        "actor_user_id": USER_ID,
        "source_type": "generated",
        "source_kind": "image_task",
        "ref_kind": "task",
        "source_task_id": "task-1",
        "content_index": 0,
    }
    values.update(overrides)
    return AssetRefDraft(**values)


def _rpc_db(
    data: object = None,
    *,
    error: Exception | None = None,
) -> MagicMock:
    if data is None:
        data = {
            "asset": {"id": "asset-1"},
            "ref": {"id": "ref-1"},
            "asset_created": True,
            "ref_created": True,
        }
    db = MagicMock()
    call = db.rpc.return_value
    if error:
        call.execute.side_effect = error
    else:
        call.execute.return_value = SimpleNamespace(data=data)
    return db


def test_register_calls_atomic_rpc_with_asset_and_ref_fields() -> None:
    db = _rpc_db()

    result = AssetRegistryService(db).register_ready_asset(
        _asset(metadata={"width": 1024}),
        _ref(prompt="a cat", metadata={"resolution": "2K"}),
    )

    assert result["asset"]["id"] == "asset-1"
    params = db.rpc.call_args.args[1]
    assert db.rpc.call_args.args[0] == "register_user_asset"
    assert params["p_storage_provider"] == "workspace"
    assert params["p_storage_key"] == "下载/generated.png"
    assert params["p_storage_owner_key"] == USER_ID
    assert params["p_ref_key"] == "task:task-1:0"
    assert params["p_source_task_id"] == "task-1"
    assert params["p_asset_metadata"] == {"width": 1024}
    assert params["p_ref_metadata"] == {"resolution": "2K"}


def test_register_accepts_single_item_rpc_list_response() -> None:
    payload = {
        "asset": {"id": "asset-1"},
        "ref": {"id": "ref-1"},
    }
    db = _rpc_db([payload])

    result = AssetRegistryService(db).register_ready_asset(_asset(), _ref())

    assert result == payload


@pytest.mark.parametrize(
    ("asset", "ref"),
    [
        (_asset(name=""), _ref()),
        (_asset(size=-1), _ref()),
        (_asset(content_sha256="not-a-sha"), _ref()),
        (_asset(), _ref(content_index=-1)),
        (_asset(), _ref(ref_key="")),
    ],
)
def test_register_rejects_invalid_draft(
    asset: ReadyAssetDraft,
    ref: AssetRefDraft,
) -> None:
    db = MagicMock()

    with pytest.raises(AppException) as exc_info:
        AssetRegistryService(db).register_ready_asset(asset, ref)

    assert exc_info.value.code == "ASSET_REGISTRY_INVALID"
    db.rpc.assert_not_called()


def test_register_maps_identity_error_to_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_identity(**_kwargs):
        raise AssetIdentityError("ASSET_URL_NOT_PERSISTED")

    monkeypatch.setattr(
        "services.assets.asset_registry.resolve_asset_identity",
        reject_identity,
    )

    with pytest.raises(AppException) as exc_info:
        AssetRegistryService(MagicMock()).register_ready_asset(
            _asset(), _ref(),
        )

    assert exc_info.value.code == "ASSET_REGISTRY_INVALID"


def test_register_maps_rpc_conflict_without_leaking_details() -> None:
    db = _rpc_db(error=RuntimeError("USER_ASSET_REF_CONFLICT"))

    with pytest.raises(AppException) as exc_info:
        AssetRegistryService(db).register_ready_asset(_asset(), _ref())

    assert exc_info.value.code == "ASSET_REGISTRY_CONFLICT"
    assert exc_info.value.status_code == 409


def test_register_rejects_invalid_rpc_payload() -> None:
    db = _rpc_db({"asset": {"id": "asset-1"}})

    with pytest.raises(AppException) as exc_info:
        AssetRegistryService(db).register_ready_asset(_asset(), _ref())

    assert exc_info.value.code == "ASSET_REGISTRY_WRITE_FAILED"


def test_web_upload_builds_unattached_upload_ref() -> None:
    db = _rpc_db()

    row = register_web_upload_best_effort(
        db,
        user_id=USER_ID,
        org_id=None,
        url="https://cdn.example/photo.png",
        name="photo.png",
        mime_type="image/png",
        size=12,
        workspace_path="上传/2026-07/photo.png",
        thumbnail_url="https://cdn.example/thumb.webp",
    )

    assert row is not None
    params = db.rpc.call_args.args[1]
    assert params["p_source_type"] == "upload"
    assert params["p_source_kind"] == "web_upload"
    assert params["p_ref_kind"] == "upload"
    assert params["p_conversation_id"] is None
    assert params["p_ref_key"].endswith("上传/2026-07/photo.png")


def test_web_upload_registry_failure_is_best_effort() -> None:
    db = _rpc_db(error=RuntimeError("database unavailable"))

    row = register_web_upload_best_effort(
        db,
        user_id=USER_ID,
        org_id=ORG_ID,
        url="https://cdn.example/report.pdf",
        name="report.pdf",
        mime_type="application/pdf",
    )

    assert row is None


def test_task_media_builds_generated_task_ref() -> None:
    db = _rpc_db()

    rows = register_task_media_best_effort(
        db,
        task={
            "id": "task-1",
            "user_id": USER_ID,
            "org_id": ORG_ID,
            "type": "image",
            "conversation_id": "conv-1",
            "placeholder_message_id": "message-1",
            "model_id": "image-model",
            "request_params": {
                "prompt": "a cat",
                "aspect_ratio": "1:1",
                "resolution": "2K",
            },
        },
        content_parts=[{
            "type": "image",
            "url": "https://cdn.example/generated.png",
            "workspace_path": "下载/AI图片/generated.png",
            "name": "generated.png",
            "mime_type": "image/png",
            "size": 123,
        }],
    )

    assert len(rows) == 1
    params = db.rpc.call_args.args[1]
    assert params["p_ref_key"] == "task:task-1:0"
    assert params["p_source_kind"] == "image_task"
    assert params["p_ref_kind"] == "task"
    assert params["p_source_task_id"] == "task-1"
    assert params["p_source_message_id"] == "message-1"
    assert params["p_prompt"] == "a cat"
    assert params["p_ref_metadata"] == {
        "aspect_ratio": "1:1",
        "resolution": "2K",
    }


def test_task_media_registry_failure_is_best_effort() -> None:
    db = _rpc_db(error=RuntimeError("database unavailable"))

    rows = register_task_media_best_effort(
        db,
        task={
            "id": "task-2",
            "user_id": USER_ID,
            "type": "video",
            "request_params": {},
        },
        content_parts=[{
            "type": "video",
            "url": "https://cdn.example/path/result.mp4",
        }],
    )

    assert rows == []


def test_wecom_attachment_builds_channel_asset_ref() -> None:
    db = _rpc_db()

    row = register_wecom_attachment_best_effort(
        db,
        attachment_id="attachment-1",
        message_id="message-1",
        conversation_id="conversation-1",
        actor_user_id=USER_ID,
        org_id=ORG_ID,
        storage_scope="channel",
        storage_owner_key=CHANNEL_OWNER,
        file_payload={
            "url": "https://cdn.example/report.csv",
            "workspace_path": "上传/企微/report.csv",
            "name": "report.csv",
            "mime_type": "text/csv",
            "size": 42,
            "asset_identity": {"content_sha256": "a" * 64},
        },
    )

    assert row is not None
    params = db.rpc.call_args.args[1]
    assert params["p_ref_key"] == "wecom:attachment-1"
    assert params["p_ref_kind"] == "attachment"
    assert params["p_storage_scope"] == "channel"
    assert params["p_storage_owner_key"] == CHANNEL_OWNER
    assert params["p_source_attachment_id"] == "attachment-1"
    assert params["p_content_sha256"] == "a" * 64


def test_message_media_uses_final_content_index_and_channel_owner() -> None:
    db = _rpc_db()

    rows = register_message_media_best_effort(
        db,
        actor_user_id=USER_ID,
        org_id=ORG_ID,
        storage_scope="channel",
        storage_owner_key=CHANNEL_OWNER,
        conversation_id="conversation-1",
        source_message_id="message-1",
        indexed_parts=[(3, {
            "type": "image",
            "url": "https://cdn.example/generated.png",
            "workspace_path": "下载/AI图片/generated.png",
            "name": "generated.png",
            "mime_type": "image/png",
            "_asset_source_kind": "ecom_image",
            "_asset_prompt": "白底主图",
            "_asset_model_id": "image-model",
        })],
    )

    assert len(rows) == 1
    params = db.rpc.call_args.args[1]
    assert params["p_ref_key"] == "message:message-1:3"
    assert params["p_storage_owner_key"] == CHANNEL_OWNER
    assert params["p_ref_kind"] == "message"
    assert params["p_content_index"] == 3
    assert params["p_source_kind"] == "ecom_image"
    assert params["p_prompt"] == "白底主图"


def test_message_media_skips_temporary_url_without_workspace_path() -> None:
    db = MagicMock()

    rows = register_message_media_best_effort(
        db,
        actor_user_id=USER_ID,
        org_id=None,
        storage_scope="user",
        storage_owner_key=USER_ID,
        conversation_id="conversation-1",
        source_message_id="message-1",
        indexed_parts=[(0, {
            "type": "video",
            "url": "https://provider.example/temporary.mp4",
            "_asset_source_kind": "media_tool",
        })],
    )

    assert rows == []
    db.rpc.assert_not_called()
