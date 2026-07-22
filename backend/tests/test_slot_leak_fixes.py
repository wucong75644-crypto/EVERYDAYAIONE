"""任务槽位泄漏修复 — 回归测试

覆盖任务槽位生命周期：
1. message.py 路由 try/finally + slot_handed_off（含 CancelledError 路径）
2. task.py cancel-by-message / mark_task_failed 调 release_task_slot
3. task_completion_service.py 终态分支兜底 release
4. video_handler 旧 _save_task 失败退积分（待视频旧链删除）
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas.message import (  # noqa: E402
    GenerateResponse, GenerationType, Message, MessageOperation, TextPart,
)
from api.deps import OrgContext  # noqa: E402


def _make_message(msg_id="msg_1"):
    return Message(
        id=msg_id, conversation_id="c1", role="assistant",
        content=[], created_at=datetime.now(timezone.utc),
    )


def _make_request():
    scope = {
        "type": "http", "method": "POST", "path": "/test",
        "headers": [], "query_string": b"",
    }
    return StarletteRequest(scope)


def _make_body(params=None):
    body = MagicMock()
    body.model = "gemini-3-pro"
    body.generation_type = GenerationType.CHAT
    body.operation = MessageOperation.SEND
    body.content = [TextPart(text="hi")]
    body.params = params
    body.created_at = None
    body.client_request_id = None
    body.original_message_id = None
    body.assistant_message_id = None
    body.placeholder_created_at = None
    body.client_task_id = "ct1"
    return body


def _idempotency_service():
    service = MagicMock()
    service.claim.return_value = SimpleNamespace(
        request_id="request-row", replay_response=None,
    )
    return service


def _chat_response():
    return GenerateResponse(
        task_id="ct1", assistant_message=_make_message("msg_a1"),
        operation=MessageOperation.SEND, generation_type="chat",
    )


# ============================================================
# 修复 1: message.py try/finally + slot_handed_off
# ============================================================

class TestMessageRouteSlotLifecycle:
    """generate_message: slot acquire 后正确交付 / 异常释放"""

    def _make_task_limit_svc(self):
        svc = MagicMock()
        svc.check_and_acquire = AsyncMock(return_value="slot-abc-123")
        svc.release = AsyncMock()
        return svc

    def _patch_success_path(self, mock_create_msg, mock_send, mock_start):
        """正常完成路径的通用 mock 配置"""
        mock_create_msg.return_value = _make_message("msg_u1")
        mock_send.return_value = ("msg_a1", _make_message("msg_a1"))
        mock_start.return_value = "ext_task_1"

    @pytest.mark.asyncio
    async def test_success_path_does_not_release_slot(self):
        """正常返回 → slot 交付给 task，路由层不释放（避免重复 release）"""
        body = _make_body(params={})
        svc = self._make_task_limit_svc()
        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value={"id": "c1"})

        with patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.MessageIdempotencyService", return_value=_idempotency_service()), \
             patch("api.routes.message.prepare_and_start_chat_generation", new_callable=AsyncMock, return_value=_chat_response()), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler"), \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start:
            self._patch_success_path(mock_create_msg, mock_send, mock_start)

            from api.routes.message import generate_message
            await generate_message(
                request=_make_request(),
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=svc,
            )

        # acquire 调过
        svc.check_and_acquire.assert_awaited_once()
        # release 没被路由层调用（task 已落库，交给 task 终态化路径释放）
        svc.release.assert_not_awaited()
        # slot_id 写入了 params
        assert body.params["_task_slot_id"] == "slot-abc-123"

    @pytest.mark.asyncio
    async def test_exception_path_releases_slot(self):
        """_do_generate_message 抛普通异常 → finally release"""
        body = _make_body(params={})
        svc = self._make_task_limit_svc()
        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.MessageIdempotencyService", return_value=_idempotency_service()):
            from api.routes.message import generate_message
            with pytest.raises(RuntimeError):
                await generate_message(
                    request=_make_request(),
                    conversation_id="c1",
                    body=body,
                    ctx=OrgContext(user_id="u1"),
                    db=MagicMock(),
                    task_limit_service=svc,
                )

        svc.release.assert_awaited_once_with(
            "u1", "c1", org_id=None, slot_id="slot-abc-123",
        )

    @pytest.mark.asyncio
    async def test_cancelled_error_releases_slot(self):
        """asyncio.CancelledError 也必须 release（核心：try/finally 优于 except Exception）"""
        body = _make_body(params={})
        svc = self._make_task_limit_svc()
        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(
            side_effect=asyncio.CancelledError(),
        )

        with patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.MessageIdempotencyService", return_value=_idempotency_service()):
            from api.routes.message import generate_message
            with pytest.raises((asyncio.CancelledError, BaseException)):
                await generate_message(
                    request=_make_request(),
                    conversation_id="c1",
                    body=body,
                    ctx=OrgContext(user_id="u1"),
                    db=MagicMock(),
                    task_limit_service=svc,
                )

        svc.release.assert_awaited_once_with(
            "u1", "c1", org_id=None, slot_id="slot-abc-123",
        )

    @pytest.mark.asyncio
    async def test_no_task_limit_service_no_acquire(self):
        """task_limit_service=None → 不 acquire 不 release（降级跳过限制）"""
        body = _make_body(params={})
        mock_conv_service = MagicMock()
        mock_conv_service.get_conversation = AsyncMock(return_value={"id": "c1"})

        with patch("api.routes.message.get_conversation_service", return_value=mock_conv_service), \
             patch("api.routes.message.MessageIdempotencyService", return_value=_idempotency_service()), \
             patch("api.routes.message.prepare_and_start_chat_generation", new_callable=AsyncMock, return_value=_chat_response()), \
             patch("api.routes.message.create_user_message", new_callable=AsyncMock) as mock_create_msg, \
             patch("api.routes.message.handle_regenerate_or_send_operation", new_callable=AsyncMock) as mock_send, \
             patch("api.routes.message.get_handler"), \
             patch("api.routes.message.start_generation_task", new_callable=AsyncMock) as mock_start:
            self._patch_success_path(mock_create_msg, mock_send, mock_start)

            from api.routes.message import generate_message
            await generate_message(
                request=_make_request(),
                conversation_id="c1",
                body=body,
                ctx=OrgContext(user_id="u1"),
                db=MagicMock(),
                task_limit_service=None,
            )

        # 没注入 slot_id
        assert "_task_slot_id" not in (body.params or {})


# ============================================================
# 修复 2: task.py cancel/fail 路由调 release_task_slot
# ============================================================

class TestTaskRouteSlotRelease:
    """cancel-by-message / mark_task_failed 必须释放槽位"""

    def _make_scoped_db(self, tasks_data):
        """构造支持 .table('tasks').select(...).eq(...).execute() 链式调用的 mock"""
        execute_result = MagicMock()
        execute_result.data = tasks_data

        single_chain = MagicMock()
        single_chain.execute.return_value = execute_result

        chain = MagicMock()
        chain.execute.return_value = execute_result
        chain.single.return_value = single_chain
        chain.eq.return_value = chain
        chain.in_.return_value = chain
        chain.is_.return_value = chain
        chain.select.return_value = chain
        chain.update.return_value = chain

        db = MagicMock()
        db.table.return_value = chain
        return db, chain

    @pytest.mark.asyncio
    async def test_cancel_by_message_releases_slot(self):
        """cancel-by-message 找到运行中 task → release_task_slot 被调"""
        from starlette.requests import Request as Req

        task_row = {
            "id": "t1", "external_task_id": "ext1",
            "user_id": "u1", "conversation_id": "c1", "org_id": None,
            "request_params": {"_task_slot_id": "slot-cancel-1"},
        }
        db, _ = self._make_scoped_db([task_row])

        with patch("api.routes.task.release_task_slot", new_callable=AsyncMock) as mock_release, \
             patch("services.websocket_manager.ws_manager") as mock_ws:
            mock_ws.cancel_task = MagicMock()

            from api.routes.task import cancel_task_by_message_id
            # 路由内部不用 request 对象本身，传 mock 即可
            req = Req(scope={"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
            result = await cancel_task_by_message_id(
                request=req,
                ctx=OrgContext(user_id="u1"),
                db=db,
                message_id="msg-1",
            )

        assert result["success"] is True
        mock_release.assert_awaited_once_with(task_row)

    @pytest.mark.asyncio
    async def test_cancel_actor_uses_atomic_cancel_rpc_path(self):
        from starlette.requests import Request as Req

        task_row = {
            "id": "internal",
            "client_task_id": "client",
            "user_id": "u1",
            "conversation_id": "c1",
            "org_id": None,
            "request_params": {"_task_slot_id": "slot-actor"},
            "delivery_context": {"actor": True},
        }
        db, chain = self._make_scoped_db([task_row])

        with patch(
            "services.conversation_task.cancel_actor_task",
            return_value=True,
        ) as mock_cancel, patch(
            "api.routes.task.release_task_slot",
            new_callable=AsyncMock,
        ), patch("services.websocket_manager.ws_manager"):
            from api.routes.task import cancel_task_by_message_id

            result = await cancel_task_by_message_id(
                request=Req(scope={
                    "type": "http",
                    "method": "POST",
                    "path": "/",
                    "headers": [],
                    "query_string": b"",
                }),
                ctx=OrgContext(user_id="u1"),
                db=db,
                message_id="msg-1",
            )

        assert result["cancelled_count"] == 1
        mock_cancel.assert_called_once_with(db, task_row, "u1", None)
        chain.update.assert_called()

    @pytest.mark.asyncio
    async def test_mark_task_failed_releases_slot(self):
        """mark_task_failed 找到 task → release_task_slot 被调"""
        from starlette.requests import Request as Req
        from api.routes.task import MarkTaskFailedRequest, mark_task_failed

        task_row = {
            "id": "t1",
            "user_id": "u1", "conversation_id": "c1", "org_id": None,
            "request_params": {"_task_slot_id": "slot-fail-1"},
        }
        db, _ = self._make_scoped_db(task_row)  # single() 返回单行

        with patch("api.routes.task.release_task_slot", new_callable=AsyncMock) as mock_release:
            req = Req(scope={"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
            result = await mark_task_failed.__wrapped__(
                request=req,
                ctx=OrgContext(user_id="u1"),
                db=db,
                external_task_id="ext-1",
                body=MarkTaskFailedRequest(reason="timeout"),
            )

        assert result["success"] is True
        mock_release.assert_awaited_once_with(task_row)


# ============================================================
# 修复 3: task_completion_service 终态分支兜底 release
# ============================================================

class TestTaskCompletionTerminalRelease:
    """webhook 看到 task 已终态时也调用 release_task_slot（兜底）"""

    @pytest.mark.asyncio
    async def test_terminal_status_triggers_release(self):
        """task.status in ['completed','failed','cancelled'] → release 被调"""
        from services.task_completion_service import TaskCompletionService
        from services.adapters.base import TaskStatus, ImageGenerateResult

        task_row = {
            "external_task_id": "ext-1",
            "status": "failed",  # 已终态
            "user_id": "u1", "conversation_id": "c1", "org_id": None,
            "request_params": {"_task_slot_id": "slot-terminal-1"},
        }

        db = MagicMock()
        svc = TaskCompletionService(db)
        svc.get_task = MagicMock(return_value=task_row)

        result = ImageGenerateResult(
            task_id="ext-1", status=TaskStatus.SUCCESS,
            image_urls=["http://example.com/img.png"],
        )

        with patch("services.task_limit_service.release_task_slot", new_callable=AsyncMock) as mock_release:
            ok = await svc.process_result("ext-1", result)

        assert ok is True
        mock_release.assert_awaited_once_with(task_row)


# ============================================================
# 修复 5: _SET_TTL = 1800
# ============================================================

class TestTTLConfig:
    def test_ttl_is_30min(self):
        from services.task_limit_service import _SET_TTL
        assert _SET_TTL == 1800
