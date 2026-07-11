"""图片请求预检与提交阶段失败收尾测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.message_generation_helpers import finalize_image_request_failure
from api.routes.message_request_preparation import prepare_generation_request
from core.exceptions import InsufficientCreditsError
from schemas.message import GenerationType, MessageOperation
from services.handlers.image_handler import ImageHandler


class MessageTableMock:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.update_data = None

    def select(self, _fields="*"):
        return self

    def update(self, data):
        self.update_data = data
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        result = MagicMock()
        if self.update_data is not None:
            self.db.updates.append(self.update_data)
            result.data = self.update_data
        else:
            result.data = self.db.message
        return result


class MessageDBMock:
    def __init__(self, message=None):
        self.message = message
        self.updates = []

    def table(self, name):
        return MessageTableMock(self, name)


@patch("config.kie_models.calculate_image_cost")
def test_preflight_checks_total_batch_cost(mock_cost):
    mock_cost.return_value = {"user_credits": 20}
    handler = ImageHandler(MagicMock())
    handler._check_balance = MagicMock()

    handler.preflight(
        user_id="user_1",
        content=[],
        params={"model": "nano-banana", "num_images": 4},
    )

    handler._check_balance.assert_called_once_with("user_1", 20)


def test_batch_failure_creates_all_failed_slots():
    db = MessageDBMock()

    finalize_image_request_failure(
        db, "msg_1", MessageOperation.RETRY, {"num_images": 3},
        "IMAGE_GENERATION_FAILED", "图片生成服务暂时不可用，请稍后重试",
    )

    update = db.updates[-1]
    assert update["status"] == "failed"
    assert update["is_error"] is False
    assert len(update["content"]) == 3
    assert all(part["failed"] is True for part in update["content"])


def test_single_failure_preserves_successful_siblings():
    db = MessageDBMock({"content": [
        {"type": "image", "url": "https://example.com/ok.png"},
        {"type": "image", "url": None},
    ]})

    finalize_image_request_failure(
        db, "msg_1", MessageOperation.REGENERATE_SINGLE, {"image_index": 1},
        "IMAGE_GENERATION_FAILED", "图片生成服务暂时不可用，请稍后重试",
    )

    update = db.updates[-1]
    assert update["status"] == "completed"
    assert update["content"][0]["url"] == "https://example.com/ok.png"
    assert update["content"][1]["failed"] is True


@pytest.mark.asyncio
async def test_insufficient_credits_does_not_create_or_reset_messages():
    handler = MagicMock()
    handler.preflight.side_effect = InsufficientCreditsError(required=20, current=5)
    conversation_service = MagicMock()
    conversation_service.get_conversation = AsyncMock(return_value={})
    create_user_message = AsyncMock()
    body = MagicMock(
        operation=MessageOperation.RETRY,
        content=[],
        params={"num_images": 2},
        model="nano-banana",
    )

    with pytest.raises(InsufficientCreditsError):
        await prepare_generation_request(
            db=MagicMock(), conversation_id="conv_1", body=body,
            gen_type=GenerationType.IMAGE, user_id="user_1", org_id=None,
            handler=handler, conversation_service=conversation_service,
            create_user_message_fn=create_user_message,
        )

    create_user_message.assert_not_awaited()
