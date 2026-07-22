"""
ImageHandler 多图批次生成单元测试

测试统一批次路径的核心逻辑：
- 缺少原子准备批次时在供应商调用前失败关闭
- _build_task_data 包含多图字段
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.image_handler import ImageHandler
from services.handlers.base import TaskMetadata
from services.handlers.image_request_settings import resolve_prepared_batch


# ============ Mock DB ============

class MockImageDB:
    """简化 DB mock"""

    def __init__(self):
        self._inserted_tasks = []
        self._users = []

    def table(self, name: str):
        return MockImageTableChain(self, name)

    def set_users(self, users: list):
        self._users = users

    def rpc(self, fn_name: str, params: dict = None):
        mock = MagicMock()
        if fn_name == "deduct_credits_atomic":
            mock.execute.return_value = MagicMock(
                data={"success": True, "new_balance": 90}
            )
        else:
            mock.execute.return_value = MagicMock(data={"success": True})
        return mock


class MockImageTableChain:
    """Mock 链式调用"""

    def __init__(self, db: MockImageDB, table_name: str):
        self._db = db
        self._table = table_name
        self._filters = {}

    def select(self, fields="*"):
        return self

    def insert(self, data):
        if self._table == "tasks":
            self._db._inserted_tasks.append(data)
        return self

    def update(self, data):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single = True
        return self

    def execute(self):
        result = MagicMock()
        if self._table == "users":
            filtered = self._db._users
            for f, v in self._filters.items():
                filtered = [u for u in filtered if u.get(f) == v]
            result.data = filtered[0] if filtered else None
        elif self._table == "credit_transactions":
            if hasattr(self, '_single') and self._single:
                # maybe_single / single 返回单个 dict
                result.data = {
                    "id": self._filters.get("id", "tx_mock"),
                    "user_id": "user_1",
                    "amount": 5,
                    "status": "pending",
                }
            else:
                result.data = [{}]
        else:
            result.data = []
        return result


# ============ 测试 ============


def test_resolve_prepared_batch_rejects_missing_tasks():
    with pytest.raises(RuntimeError, match="IMAGE_PREPARED_TASKS_MISSING"):
        resolve_prepared_batch(TaskMetadata(client_task_id="client_1"), 1)


def test_resolve_prepared_batch_rejects_missing_batch_id():
    metadata = MagicMock(
        prepared_task_ids=("task-1",), prepared_batch_id=None,
    )
    with pytest.raises(RuntimeError, match="IMAGE_PREPARED_BATCH_MISSING"):
        resolve_prepared_batch(metadata, 1)


def test_resolve_prepared_batch_rejects_count_mismatch():
    metadata = MagicMock(
        prepared_task_ids=("task-1", "task-2"), prepared_batch_id="batch-1",
    )
    with pytest.raises(RuntimeError, match="IMAGE_PREPARED_TASK_COUNT_MISMATCH"):
        resolve_prepared_batch(metadata, 1)


@pytest.mark.asyncio
async def test_start_without_prepared_batch_never_creates_adapter():
    db = MockImageDB()
    db.set_users([{"id": "user_1", "credits": 1000, "status": "active"}])
    handler = ImageHandler(db)
    with (
        patch("config.kie_models.calculate_image_cost", return_value={"user_credits": 5}),
        patch("services.adapters.factory.create_image_adapter") as create_adapter,
        pytest.raises(RuntimeError, match="IMAGE_PREPARED_TASKS_MISSING"),
    ):
        await handler.start(
            message_id="msg_1", conversation_id="conv_1", user_id="user_1",
            content=[], params={"model": "nano-banana"},
            metadata=TaskMetadata(client_task_id="client_1"),
        )
    create_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_start_submits_only_prepared_local_task():
    db = MockImageDB()
    db.set_users([{"id": "user_1", "credits": 1000, "status": "active"}])
    handler = ImageHandler(db)
    adapter = MagicMock(provider=MagicMock(value="kie"), supports_resolution=False)
    adapter.close = AsyncMock()
    metadata = MagicMock(
        client_task_id="client_1", prepared_task_ids=("local-1",),
        prepared_batch_id="batch-1",
    )
    with (
        patch("config.kie_models.calculate_image_cost", return_value={"user_credits": 5}),
        patch("services.adapters.factory.create_image_adapter", return_value=adapter),
        patch.object(handler, "_build_callback_url", return_value="https://callback"),
        patch(
            "services.handlers.image_prepared_submission.submit_prepared_image_task",
            new_callable=AsyncMock, return_value="external-1",
        ) as submit,
    ):
        result = await handler.start(
            message_id="msg_1", conversation_id="conv_1", user_id="user_1",
            content=[], params={"model": "nano-banana"}, metadata=metadata,
        )

    assert result == "client_1"
    assert submit.await_args.kwargs["local_task_id"] == "local-1"
    adapter.close.assert_awaited_once()

class TestBuildTaskDataMultiImage:
    """测试 _build_task_data 的 image_index/batch_id 参数"""

    @pytest.fixture
    def handler(self):
        return ImageHandler(MockImageDB())

    def test_build_task_data_with_batch_fields(self, handler):
        """测试：_build_task_data 包含 image_index 和 batch_id"""
        metadata = TaskMetadata(client_task_id="client_1")

        task_data = handler._build_task_data(
            task_id="ext_1",
            message_id="msg_1",
            conversation_id="conv_1",
            user_id="user_1",
            task_type="image",
            status="pending",
            model_id="nano-banana",
            request_params={"prompt": "cat"},
            metadata=metadata,
            image_index=2,
            batch_id="batch_abc",
        )

        assert task_data["image_index"] == 2
        assert task_data["batch_id"] == "batch_abc"

    def test_build_task_data_without_batch_fields(self, handler):
        """测试：_build_task_data 不传 batch 字段时为 None"""
        metadata = TaskMetadata(client_task_id="client_1")

        task_data = handler._build_task_data(
            task_id="ext_1",
            message_id="msg_1",
            conversation_id="conv_1",
            user_id="user_1",
            task_type="chat",
            status="running",
            model_id="gpt-4",
            request_params={"prompt": "hello"},
            metadata=metadata,
        )

        # 非图片任务不应有 batch 字段（或为 None）
        assert task_data.get("image_index") is None
        assert task_data.get("batch_id") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
