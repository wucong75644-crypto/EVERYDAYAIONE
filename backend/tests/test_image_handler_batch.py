"""
ImageHandler 多图批次生成单元测试

测试统一批次路径的核心逻辑：
- num_images 参数解析（默认 1，上限 4）
- 循环创建 N 个 task
- 部分 API 失败时继续创建其余 task
- 全部失败时抛异常
- _save_task 传递 image_index 和 batch_id
- _build_task_data 包含多图字段
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.image_handler import ImageHandler
from services.handlers.base import TaskMetadata


# ============ Mock Adapter ============

class MockAdapterResult:
    """Mock KIE adapter 返回值"""
    def __init__(self, task_id: str):
        self.task_id = task_id


class MockImageAdapter:
    """Mock 图片生成适配器"""

    def __init__(self, fail_indices: set = None):
        self.fail_indices = fail_indices or set()
        self.call_count = 0
        self.generate_calls = []
        self.provider = MagicMock(value="kie")
        self.supports_resolution = False

    async def generate(self, **kwargs):
        index = self.call_count
        self.call_count += 1
        self.generate_calls.append(kwargs)
        if index in self.fail_indices:
            raise Exception(f"API error at index {index}")
        return MockAdapterResult(task_id=f"ext_task_{index}")

    async def close(self):
        pass


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

class TestImageHandlerNumImages:
    """测试 num_images 参数解析"""

    @pytest.fixture
    def db(self):
        db = MockImageDB()
        db.set_users([{
            "id": "user_1", "credits": 1000, "status": "active",
        }])
        return db

    @pytest.fixture
    def handler(self, db):
        return ImageHandler(db)

    @pytest.fixture
    def metadata(self):
        return TaskMetadata(
            client_task_id="client_1",
            placeholder_created_at=datetime.now(timezone.utc),
        )

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_default_num_images_is_1(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：默认 num_images=1"""
        mock_cost.return_value = {"user_credits": 5}
        adapter = MockImageAdapter()
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            await handler.start(
                message_id="msg_1",
                conversation_id="conv_1",
                user_id="user_1",
                content=[],
                params={"model": "nano-banana"},
                metadata=metadata,
            )

        assert adapter.call_count == 1
        assert len(db._inserted_tasks) == 1
        assert db._inserted_tasks[0]["image_index"] == 0

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_num_images_4(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：num_images=4 创建 4 个 task"""
        mock_cost.return_value = {"user_credits": 20}
        adapter = MockImageAdapter()
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            with patch("services.handlers.image_handler.asyncio.sleep", new_callable=AsyncMock):
                await handler.start(
                    message_id="msg_1",
                    conversation_id="conv_1",
                    user_id="user_1",
                    content=[],
                    params={"model": "nano-banana", "num_images": 4},
                    metadata=metadata,
                )

        assert adapter.call_count == 4
        assert len(db._inserted_tasks) == 4
        # 验证 image_index 递增
        for i in range(4):
            assert db._inserted_tasks[i]["image_index"] == i
        # 验证 batch_id 相同
        batch_id = db._inserted_tasks[0]["batch_id"]
        assert batch_id is not None
        for t in db._inserted_tasks:
            assert t["batch_id"] == batch_id

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_num_images_clamped_to_4(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：num_images 超过 4 被截断"""
        mock_cost.return_value = {"user_credits": 50}
        adapter = MockImageAdapter()
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            with patch("services.handlers.image_handler.asyncio.sleep", new_callable=AsyncMock):
                await handler.start(
                    message_id="msg_1",
                    conversation_id="conv_1",
                    user_id="user_1",
                    content=[],
                    params={"model": "nano-banana", "num_images": 10},
                    metadata=metadata,
                )

        assert adapter.call_count == 4  # 最多 4

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_num_images_min_1(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：num_images=0 被截断为 1"""
        mock_cost.return_value = {"user_credits": 5}
        adapter = MockImageAdapter()
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            await handler.start(
                message_id="msg_1",
                conversation_id="conv_1",
                user_id="user_1",
                content=[],
                params={"model": "nano-banana", "num_images": 0},
                metadata=metadata,
            )

        assert adapter.call_count == 1


class TestImageHandlerPartialFailure:
    """测试部分 API 调用失败"""

    @pytest.fixture
    def db(self):
        db = MockImageDB()
        db.set_users([{
            "id": "user_1", "credits": 1000, "status": "active",
        }])
        return db

    @pytest.fixture
    def handler(self, db):
        return ImageHandler(db)

    @pytest.fixture
    def metadata(self):
        return TaskMetadata(client_task_id="client_1")

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_partial_failure_continues(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：部分失败时继续创建其余 task"""
        mock_cost.return_value = {"user_credits": 20}
        # index=1 和 index=3 失败
        adapter = MockImageAdapter(fail_indices={1, 3})
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            with patch("services.handlers.image_handler.asyncio.sleep", new_callable=AsyncMock):
                result = await handler.start(
                    message_id="msg_1",
                    conversation_id="conv_1",
                    user_id="user_1",
                    content=[],
                    params={"model": "nano-banana", "num_images": 4},
                    metadata=metadata,
                )

        assert adapter.call_count == 4  # 全部尝试
        assert len(db._inserted_tasks) == 2  # 只有 index=0 和 index=2 成功保存

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_all_failure_raises(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：全部失败时抛异常"""
        mock_cost.return_value = {"user_credits": 20}
        adapter = MockImageAdapter(fail_indices={0, 1, 2, 3})
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            with patch("services.handlers.image_handler.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(Exception, match="所有图片生成请求均失败"):
                    await handler.start(
                        message_id="msg_1",
                        conversation_id="conv_1",
                        user_id="user_1",
                        content=[],
                        params={"model": "nano-banana", "num_images": 4},
                        metadata=metadata,
                    )


class TestImageHandlerBatchFields:
    """测试 batch_id 和 image_index 在 task_data 中"""

    @pytest.fixture
    def db(self):
        db = MockImageDB()
        db.set_users([{
            "id": "user_1", "credits": 1000, "status": "active",
        }])
        return db

    @pytest.fixture
    def handler(self, db):
        return ImageHandler(db)

    @pytest.fixture
    def metadata(self):
        return TaskMetadata(client_task_id="client_1")

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_task_data_has_batch_fields(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：保存的 task 包含 image_index 和 batch_id"""
        mock_cost.return_value = {"user_credits": 10}
        adapter = MockImageAdapter()
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            with patch("services.handlers.image_handler.asyncio.sleep", new_callable=AsyncMock):
                await handler.start(
                    message_id="msg_1",
                    conversation_id="conv_1",
                    user_id="user_1",
                    content=[],
                    params={"model": "nano-banana", "num_images": 2},
                    metadata=metadata,
                )

        assert len(db._inserted_tasks) == 2

        task_0 = db._inserted_tasks[0]
        task_1 = db._inserted_tasks[1]

        # batch_id 相同且非空
        assert task_0["batch_id"] is not None
        assert task_0["batch_id"] == task_1["batch_id"]

        # image_index 不同
        assert task_0["image_index"] == 0
        assert task_1["image_index"] == 1

        # 类型正确
        assert task_0["type"] == "image"
        assert task_1["type"] == "image"

    @pytest.mark.asyncio
    @patch("services.adapters.factory.create_image_adapter")
    @patch("config.kie_models.calculate_image_cost")
    async def test_single_image_also_has_batch_id(self, mock_cost, mock_factory, handler, db, metadata):
        """测试：单图也有 batch_id（统一路径）"""
        mock_cost.return_value = {"user_credits": 5}
        adapter = MockImageAdapter()
        mock_factory.return_value = adapter

        with patch.object(handler, '_build_callback_url', return_value="http://cb"):
            await handler.start(
                message_id="msg_1",
                conversation_id="conv_1",
                user_id="user_1",
                content=[],
                params={"model": "nano-banana", "num_images": 1},
                metadata=metadata,
            )

        assert len(db._inserted_tasks) == 1
        assert db._inserted_tasks[0]["batch_id"] is not None
        assert db._inserted_tasks[0]["image_index"] == 0


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


# ============ 错误处理改造测试 ============


class TestImageHandlerErrorHandling:
    """_save_task 和 _refund_credits 失败场景"""

    def _make_handler(self):
        db = MagicMock()
        handler = ImageHandler(db=db)
        return handler, db

    @pytest.mark.asyncio
    async def test_save_task_failure_does_not_crash(self):
        """主路径 _save_task 失败 → 不崩溃，返回 external_task_id"""
        handler, db = self._make_handler()

        # mock adapter.generate 成功
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.task_id = "ext_123"
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")

        # _lock_credits 成功
        handler._lock_credits = MagicMock(return_value="tx_1")

        # _save_task 失败
        handler._save_task = MagicMock(side_effect=Exception("DB down"))

        # _attempt_image_sync_retry 不应被调用
        handler._attempt_image_sync_retry = AsyncMock()

        metadata = TaskMetadata(client_task_id="client_1")

        result = await handler._create_single_task(
            adapter=mock_adapter,
            index=0,
            batch_id="batch_1",
            generate_kwargs={"prompt": "test"},
            message_id="msg_1",
            conversation_id="conv_1",
            user_id="user_1",
            model_id="test-model",
            per_image_credits=5,
            params={},
            prompt="test",
            metadata=metadata,
        )

        # 返回 task_id（API 已成功）
        assert result == "ext_123"
        # _save_task 被调用（虽然失败了）
        handler._save_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_refund_failure_in_api_error_path_no_crash(self):
        """API 失败 + refund 也失败 → 不崩溃"""
        handler, db = self._make_handler()

        # mock adapter.generate 失败
        mock_adapter = MagicMock()
        mock_adapter.generate = AsyncMock(side_effect=Exception("API timeout"))
        mock_adapter.close = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")

        # _lock_credits 成功
        handler._lock_credits = MagicMock(return_value="tx_1")

        # _refund_credits 也失败
        handler._refund_credits = MagicMock(side_effect=Exception("refund DB down"))

        # 不触发 smart mode 重试
        handler._attempt_image_sync_retry = AsyncMock(return_value=None)

        metadata = TaskMetadata(client_task_id="client_1")

        # 不崩溃，返回 None
        result = await handler._create_single_task(
            adapter=mock_adapter,
            index=0,
            batch_id="batch_1",
            generate_kwargs={"prompt": "test"},
            message_id="msg_1",
            conversation_id="conv_1",
            user_id="user_1",
            model_id="test-model",
            per_image_credits=5,
            params={},
            prompt="test",
            metadata=metadata,
        )

        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
