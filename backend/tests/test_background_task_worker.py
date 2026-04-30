"""
BackgroundTaskWorker 单元测试

覆盖:
- _refund_credits: 原子退款 RPC 成功/跳过/异常
- _handle_timeout: chat/image/video 超时处理
- cleanup_stale_tasks: 超时清理逻辑
- _resolve_poll_interval: 轮询间隔自适应
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.background_task_worker import BackgroundTaskWorker, _resolve_poll_interval


# ── Fixtures ────────────────────────────────────────────────


class FakeMockDB:
    """模拟 Supabase Client"""

    def __init__(self):
        self._rpc_results = {}
        self._table_mock = MagicMock()
        # 链式调用默认返回自身
        self._table_mock.select.return_value = self._table_mock
        self._table_mock.update.return_value = self._table_mock
        self._table_mock.eq.return_value = self._table_mock
        self._table_mock.in_.return_value = self._table_mock
        self._table_mock.execute.return_value = MagicMock(data=[])

    def table(self, name: str):
        return self._table_mock

    def set_rpc_result(self, name: str, data):
        self._rpc_results[name] = data

    def rpc(self, name: str, params: dict = None):
        mock = MagicMock()
        data = self._rpc_results.get(name, {})
        mock.execute.return_value = MagicMock(data=data)
        return mock


@pytest.fixture
def db():
    return FakeMockDB()


@pytest.fixture
def worker(db):
    with patch("services.background_task_worker.get_settings") as mock_settings:
        settings = MagicMock()
        settings.poll_interval_seconds = 0
        settings.callback_base_url = ""
        settings.kie_qps_limit = 50
        mock_settings.return_value = settings
        return BackgroundTaskWorker(db)


# ── _resolve_poll_interval 测试 ─────────────────────────────


class TestResolvePollInterval:

    def test_manual_override(self):
        """手动设置轮询间隔优先"""
        settings = MagicMock()
        settings.poll_interval_seconds = 30
        settings.callback_base_url = "http://example.com"
        assert _resolve_poll_interval(settings) == 30

    def test_with_webhook(self):
        """有回调时使用兜底模式（120s）"""
        settings = MagicMock()
        settings.poll_interval_seconds = 0
        settings.callback_base_url = "http://example.com"
        assert _resolve_poll_interval(settings) == 120

    def test_without_webhook(self):
        """无回调时使用主轮询模式（15s）"""
        settings = MagicMock()
        settings.poll_interval_seconds = 0
        settings.callback_base_url = ""
        assert _resolve_poll_interval(settings) == 15


# ── _refund_credits 测试 ────────────────────────────────────


class TestRefundCredits:

    @pytest.mark.asyncio
    async def test_refund_success(self, worker, db):
        """退款成功：RPC 返回 refunded=true"""
        db.set_rpc_result("atomic_refund_credits", {
            "refunded": True,
            "user_id": "user-1",
            "amount": 10
        })

        # 应该不抛异常
        await worker._refund_credits("tx-123")

    @pytest.mark.asyncio
    async def test_refund_skipped(self, worker, db):
        """退款跳过：事务非 pending 状态"""
        db.set_rpc_result("atomic_refund_credits", {
            "refunded": False,
            "reason": "status_confirmed"
        })

        # 静默跳过，不抛异常
        await worker._refund_credits("tx-123")

    @pytest.mark.asyncio
    async def test_refund_not_found(self, worker, db):
        """退款跳过：事务不存在"""
        db.set_rpc_result("atomic_refund_credits", {
            "refunded": False,
            "reason": "not_found"
        })

        await worker._refund_credits("tx-nonexistent")

    @pytest.mark.asyncio
    async def test_refund_rpc_exception(self, worker):
        """RPC 调用异常：捕获并记录，不向上抛"""
        worker.db = MagicMock()
        worker.db.rpc.side_effect = Exception("DB connection lost")

        # 不应向外抛异常
        await worker._refund_credits("tx-123")


# ── _handle_timeout 测试 ────────────────────────────────────


class TestHandleTimeout:

    def _make_task(self, task_type: str = "chat", **overrides) -> dict:
        base = {
            "id": "task-1",
            "type": task_type,
            "external_task_id": "ext-1",
            "credit_transaction_id": "tx-1",
            "started_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_chat_timeout_refunds_and_updates(self, worker, db):
        """chat 超时：直接退回积分 + 更新 task 状态"""
        db.set_rpc_result("atomic_refund_credits", {
            "refunded": True, "user_id": "u1", "amount": 5
        })

        task = self._make_task("chat")
        await worker._handle_timeout(task, 10)

        # 验证 task 表被更新为 failed
        db._table_mock.update.assert_called()
        update_args = db._table_mock.update.call_args[0][0]
        assert update_args["status"] == "failed"
        assert "超时" in update_args["error_message"]

    @pytest.mark.asyncio
    async def test_chat_timeout_no_transaction(self, worker, db):
        """chat 超时但无积分事务：只更新状态，不退款"""
        task = self._make_task("chat", credit_transaction_id=None)
        await worker._handle_timeout(task, 10)

        # task 表仍被更新
        db._table_mock.update.assert_called()

    @pytest.mark.asyncio
    @patch("services.task_utils.save_accumulated_to_message")
    async def test_chat_timeout_saves_accumulated_with_correct_type(self, mock_save, worker, db):
        """chat 超时回写时 task_type 应为实际任务类型"""
        mock_save.return_value = True
        task = self._make_task("chat",
            accumulated_content="部分内容",
            placeholder_message_id="msg-1",
            conversation_id="conv-1",
            model_id="gpt-4",
            client_task_id="client-1",
            credit_transaction_id=None,
        )
        await worker._handle_timeout(task, 10)

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        assert call_kwargs[1]["task_type"] == "chat"

    @pytest.mark.asyncio
    @patch("services.task_utils.save_accumulated_to_message")
    async def test_timeout_fallback_saves_with_actual_task_type(self, mock_save, worker, db):
        """fallback 路径超时回写时 task_type 应为实际任务类型（非硬编码 chat）"""
        mock_save.return_value = True
        # image 任务走 service 失败后 fallback
        with patch("services.background_task_worker.TaskCompletionService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.process_result.side_effect = Exception("service error")
            MockService.return_value = mock_instance

            task = self._make_task("image",
                accumulated_content="fallback内容",
                placeholder_message_id="msg-2",
                conversation_id="conv-2",
                model_id="flux-pro",
                client_task_id="client-2",
            )
            await worker._handle_timeout(task, 30)

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        assert call_kwargs[1]["task_type"] == "image"

    @pytest.mark.asyncio
    @patch("services.background_task_worker.TaskCompletionService")
    async def test_image_timeout_via_service(self, MockService, worker):
        """image 超时：通过 TaskCompletionService 处理"""
        mock_instance = AsyncMock()
        mock_instance.process_result.return_value = True
        MockService.return_value = mock_instance

        task = self._make_task("image")
        await worker._handle_timeout(task, 30)

        mock_instance.process_result.assert_called_once()
        call_args = mock_instance.process_result.call_args
        assert call_args[0][0] == "ext-1"
        assert call_args[0][1].status.value == "failed"

    @pytest.mark.asyncio
    @patch("services.background_task_worker.TaskCompletionService")
    async def test_video_timeout_via_service(self, MockService, worker):
        """video 超时：通过 TaskCompletionService 处理"""
        mock_instance = AsyncMock()
        mock_instance.process_result.return_value = True
        MockService.return_value = mock_instance

        task = self._make_task("video")
        await worker._handle_timeout(task, 60)

        mock_instance.process_result.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.background_task_worker.TaskCompletionService")
    async def test_image_timeout_service_fails_fallback(self, MockService, worker, db):
        """image 超时：服务处理失败时 fallback 到直接更新"""
        mock_instance = AsyncMock()
        mock_instance.process_result.side_effect = Exception("service error")
        MockService.return_value = mock_instance

        db.set_rpc_result("atomic_refund_credits", {
            "refunded": True, "user_id": "u1", "amount": 5
        })

        task = self._make_task("image")
        await worker._handle_timeout(task, 30)

        # fallback：task 表被直接更新
        db._table_mock.update.assert_called()

    @pytest.mark.asyncio
    @patch("services.background_task_worker.TaskCompletionService")
    async def test_image_timeout_service_returns_false(self, MockService, worker, db):
        """image 超时：服务返回 False 时 fallback"""
        mock_instance = AsyncMock()
        mock_instance.process_result.return_value = False
        MockService.return_value = mock_instance

        db.set_rpc_result("atomic_refund_credits", {
            "refunded": True, "user_id": "u1", "amount": 5
        })

        task = self._make_task("image")
        await worker._handle_timeout(task, 30)

        # fallback 触发
        db._table_mock.update.assert_called()


# ── cleanup_stale_tasks 测试 ────────────────────────────────


class TestCleanupStaleTasks:

    @pytest.mark.asyncio
    async def test_no_tasks(self, worker, db):
        """无任务时静默返回"""
        db._table_mock.execute.return_value = MagicMock(data=[])
        await worker.cleanup_stale_tasks()

    @pytest.mark.asyncio
    async def test_skip_task_without_started_at(self, worker, db):
        """跳过没有 started_at 的任务"""
        db._table_mock.execute.return_value = MagicMock(data=[
            {"id": "t1", "type": "chat", "started_at": None}
        ])

        with patch.object(worker, "_handle_timeout") as mock_timeout:
            await worker.cleanup_stale_tasks()
            mock_timeout.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_chat_task(self, worker, db):
        """chat 任务超过 10 分钟触发超时"""
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        db._table_mock.execute.return_value = MagicMock(data=[
            {"id": "t1", "type": "chat", "started_at": old_time}
        ])

        with patch.object(worker, "_handle_timeout", new_callable=AsyncMock) as mock_timeout:
            await worker.cleanup_stale_tasks()
            mock_timeout.assert_called_once()
            assert mock_timeout.call_args[0][1] == 10  # max_duration_minutes

    @pytest.mark.asyncio
    async def test_not_timeout_recent_task(self, worker, db):
        """未超时的任务不触发清理"""
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        db._table_mock.execute.return_value = MagicMock(data=[
            {"id": "t1", "type": "chat", "started_at": recent_time}
        ])

        with patch.object(worker, "_handle_timeout", new_callable=AsyncMock) as mock_timeout:
            await worker.cleanup_stale_tasks()
            mock_timeout.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_error_handled(self, worker, db):
        """DB 查询异常被捕获"""
        db._table_mock.execute.side_effect = Exception("connection lost")
        # 不应抛异常
        await worker.cleanup_stale_tasks()


# ── Slot 释放测试 ─────────────────────────────────────────────


class TestTimeoutSlotRelease:
    """_handle_timeout 后释放任务限制槽位"""

    def _make_task(self, task_type: str = "chat", **overrides) -> dict:
        base = {
            "id": "task-1",
            "type": task_type,
            "external_task_id": "ext-1",
            "user_id": "u1",
            "conversation_id": "conv1",
            "credit_transaction_id": "tx-1",
            "request_params": {"_task_slot_id": "slot-timeout-1"},
            "started_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    @patch("services.task_limit_service.release_task_slot", new_callable=AsyncMock)
    async def test_chat_timeout_releases_slot(self, mock_release, worker, db):
        """chat 超时 fallback 路径后释放 slot"""
        db.set_rpc_result("atomic_refund_credits", {
            "refunded": True, "user_id": "u1", "amount": 5
        })
        task = self._make_task("chat")
        await worker._handle_timeout(task, 10)

        mock_release.assert_awaited_once_with(task)

    @pytest.mark.asyncio
    @patch("services.task_limit_service.release_task_slot", new_callable=AsyncMock)
    @patch("services.background_task_worker.TaskCompletionService")
    async def test_image_timeout_fallback_releases_slot(self, MockService, mock_release, worker, db):
        """image 超时 service 失败后 fallback 释放 slot"""
        mock_instance = AsyncMock()
        mock_instance.process_result.side_effect = Exception("service error")
        MockService.return_value = mock_instance

        task = self._make_task("image")
        await worker._handle_timeout(task, 30)

        mock_release.assert_awaited_once_with(task)

    @pytest.mark.asyncio
    @patch("services.task_limit_service.release_task_slot", new_callable=AsyncMock)
    async def test_no_slot_id_no_crash(self, mock_release, worker, db):
        """task 没有 slot_id 时 release 不崩溃"""
        task = self._make_task("chat", request_params={})
        await worker._handle_timeout(task, 10)

        # release_task_slot 被调用但内部会 early return（无 slot_id）
        mock_release.assert_awaited_once_with(task)
