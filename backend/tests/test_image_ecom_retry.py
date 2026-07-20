"""电商图片失败占位原位重试测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.image_ecom import (
    RetryImageRequest,
    _update_message_image_part,
    retry_image,
)


def _query(data) -> MagicMock:
    query = MagicMock()
    for method in (
        "select", "eq", "maybe_single", "single", "update",
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=data)
    return query


def test_update_failed_image_returns_real_index_and_strips_sidecar() -> None:
    message_query = _query({
        "content": [
            {"type": "text", "text": "结果"},
            {"type": "image", "failed": True},
            {"type": "image", "url": "https://cdn/other.png"},
        ],
    })
    db = MagicMock()
    db.table.return_value = message_query

    content_index = _update_message_image_part(
        db,
        "message-1",
        "conversation-1",
        0,
        {
            "kind": "image",
            "url": "https://cdn/generated.png",
            "workspace_path": "下载/AI图片/generated.png",
            "_asset_source_kind": "ecom_image",
            "_asset_prompt": "白底主图",
        },
    )

    assert content_index == 1
    updated = message_query.update.call_args.args[0]["content"]
    assert "_asset_source_kind" not in updated
    assert "下载/AI图片/generated.png" in updated
    assert '"type": "image"' in updated
    message_query.eq.assert_any_call(
        "conversation_id", "conversation-1",
    )


def test_update_failed_image_rejects_missing_image_ordinal() -> None:
    db = MagicMock()
    db.table.return_value = _query({
        "content": [{"type": "text", "text": "没有图片"}],
    })

    with pytest.raises(
        RuntimeError, match="ECOM_RETRY_IMAGE_PART_NOT_FOUND",
    ):
        _update_message_image_part(
            db,
            "message-1",
            "conversation-1",
            0,
            {"kind": "image", "url": "https://cdn/generated.png"},
        )


@pytest.mark.asyncio
@patch("services.assets.register_message_media_best_effort")
@patch("services.agent.image.image_agent.ImageAgent")
async def test_retry_registers_first_success_at_message_content_index(
    image_agent_class: MagicMock,
    register_asset: MagicMock,
) -> None:
    db = MagicMock()
    db.table.return_value = _query({
        "id": "conversation-1",
        "org_id": "org-1",
    })
    payload = {
        "kind": "image",
        "url": "https://cdn/generated.png",
        "workspace_path": "下载/AI图片/generated.png",
        "_asset_source_kind": "ecom_image",
    }
    image_agent_class.return_value.execute = AsyncMock(
        return_value=SimpleNamespace(
            status="success",
            emit_payloads=[payload],
        ),
    )

    with patch(
        "api.routes.image_ecom._update_message_image_part",
        return_value=2,
    ):
        response = await retry_image(
            RetryImageRequest(
                conversation_id="conversation-1",
                message_id="message-1",
                task="白底主图",
                part_index=0,
            ),
            "user-1",
            db,
        )

    assert response == {
        "success": True,
        "image_url": "https://cdn/generated.png",
    }
    assert image_agent_class.call_args.kwargs["org_id"] == "org-1"
    register_asset.assert_called_once()
    kwargs = register_asset.call_args.kwargs
    assert kwargs["source_message_id"] == "message-1"
    assert kwargs["indexed_parts"] == [(2, payload)]
