"""media_tool_executor 单元测试 — 图片/视频生成 + 积分 lock/confirm/refund"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Mock adapter 返回值
@dataclass
class MockImageResult:
    task_id: str = "img_001"
    image_urls: List[str] = None
    fail_msg: Optional[str] = None

    def __post_init__(self):
        if self.image_urls is None:
            self.image_urls = []


@dataclass
class MockVideoResult:
    task_id: str = "vid_001"
    video_url: Optional[str] = None
    fail_msg: Optional[str] = None


def _make_executor():
    """创建带 mock db 的 ToolExecutor"""
    from services.tool_executor import ToolExecutor

    mock_db = MagicMock()
    # mock _lock_credits（CreditMixin 方法）
    mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data={"credits": 1000}
    )
    mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "u1"}]
    )
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    mock_db.rpc.return_value.execute.return_value = MagicMock(data={"refunded": True})

    exe = ToolExecutor(db=mock_db, user_id="u1", conversation_id="c1", org_id="org1")
    return exe


class TestGenerateImage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [{"model":"google/nano-banana"},{"model_name":"google/nano-banana","size":"1K","format":"PNG"}])
    async def test_model_override_is_rejected_before_database_or_provider(self,monkeypatch,extra):
        from core.config import Settings
        from services.tools import ToolCall, ToolPolicy, build_legacy_catalog
        from services.tools.result import ToolResult
        from tests.test_tool_execution import context
        settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",chat_image_async_enabled=True)
        monkeypatch.setattr("core.config.get_settings",lambda:settings)
        exe = _make_executor()
        args={"mode":"text_to_image","prompt":"exact",**extra}
        with patch("services.adapters.factory.create_image_adapter") as provider:
            result = await exe._generate_image(args)
        assert result.is_failure and result.metadata["submission_state"] == "not_accepted"
        assert result.metadata["accepted"] is False
        assert "未创建图片任务" in result.summary and "未预扣图片积分" in result.summary
        exe.db.table.assert_not_called()
        exe.db.rpc.assert_not_called()
        provider.assert_not_called()
        ctx=context(permission_mode="auto",feature_flags={"chat_image_async_enabled":True})
        decision=ToolPolicy(build_legacy_catalog()).decide("generate_image",ctx,args)
        envelope=ToolResult.wrap(result,call=ToolCall("call","generate_image",args),context=ctx,decision=decision)
        assert envelope.execution.status != "uncertain" and not envelope.uncertainty_notice

    @pytest.mark.asyncio
    async def test_acceptance_response_loss_is_still_uncertain(self):
        from services.tools import ToolCall, ToolPolicy, build_legacy_catalog
        from services.tools.result import ToolResult
        from tests.test_tool_execution import context
        exe = _make_executor()
        args={"mode":"text_to_image","prompt":"exact"}
        with patch("services.handlers.image_handler.ImageHandler.accept_chat_image",new=AsyncMock(side_effect=TimeoutError("response lost"))):
            with pytest.raises(TimeoutError) as failure:
                await exe._generate_image(args)
        ctx=context(permission_mode="auto",feature_flags={"chat_image_async_enabled":True})
        decision=ToolPolicy(build_legacy_catalog()).decide("generate_image",ctx,args)
        envelope=ToolResult.from_exception(failure.value,call=ToolCall("call","generate_image",args),context=ctx,decision=decision,handler_started=True)
        assert envelope.execution.status == "uncertain" and envelope.uncertainty_notice

    @pytest.mark.asyncio
    async def test_acceptance_is_not_completion_and_has_no_provider_or_credit_io(self):
        exe = _make_executor()
        accepted = {"status": "submitted", "task_id": "child", "message_id": "child-message", "submission_state": "queued"}
        with patch("services.handlers.image_handler.ImageHandler.accept_chat_image", new=AsyncMock(return_value=accepted)) as accept, patch("services.adapters.factory.create_image_adapter") as provider:
            result = await exe._generate_image({"mode": "text_to_image", "prompt": "  exact  "})
        accept.assert_awaited_once_with(exe, {"mode": "text_to_image", "prompt": "  exact  "})
        provider.assert_not_called()
        assert result.metadata["accepted"] is True and result.metadata["completed"] is False
        assert result.metadata["task_id"] == "child" and not result.emit_payloads
        exe.db.rpc.assert_not_called()

    @pytest.mark.asyncio
    async def test_closed_acceptance_never_falls_back_to_sync_generation(self):
        exe = _make_executor()
        with patch("services.handlers.image_handler.ImageHandler.accept_chat_image", new=AsyncMock(side_effect=PermissionError("disabled"))), patch("services.adapters.factory.create_image_adapter") as provider:
            with pytest.raises(PermissionError):
                await exe._generate_image({"prompt": "original"})
        provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["text_to_image","image_to_image"])
@pytest.mark.parametrize("outcome", ["accepted","invalid_input","rpc_response_lost"])
async def test_real_acceptance_preparation_uses_default_and_preserves_uncertain_boundary(monkeypatch,mode,outcome):
    from copy import deepcopy
    from core.config import Settings
    from services.handlers.chat_image_request import ChatImageNotAcceptedError,default_chat_image_model
    from services.handlers.image_handler import ImageHandler
    from services.tools.dispatcher import _dispatch_call_id
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    exe=_make_executor()
    exe.user_id="11111111-1111-4111-8111-111111111111"
    exe.workspace_user_id=exe.user_id
    exe.org_id="22222222-2222-4222-8222-222222222222"
    exe.task_id="parent"
    exe.image_execution_token="owner-token"
    exe.db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data={
        "id":"parent","user_id":exe.user_id,"org_id":exe.org_id,"conversation_id":exe.conversation_id,
        "turn_id":"turn","input_message_id":"input","base_context_revision":1,
    }
    scoped=MagicMock()
    scoped.rpc.return_value.execute.return_value.data={"outcome":"accepted","task_id":"child","message_id":"child-message","submission_state":"queued"}
    scoped.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data={"content":[]}
    monkeypatch.setattr("core.db_scope.ScopedDatabaseClient",lambda *_:scoped)
    resolver=MagicMock()
    resolver.size_context.return_value=(None, {})
    resolver.normalize_legacy.side_effect=deepcopy
    resolver.resolve.return_value=([{"workspace_path":"original.png","content_sha256":"a"*64,"role":"subject","width":1024,"height":1024,"aspect_ratio":"1:1"}] if mode=="image_to_image" else [])
    monkeypatch.setattr("services.handlers.chat_image_request.ChatImageInputResolver",lambda *_,**__:resolver)
    if outcome=="invalid_input":
        resolver.verify.side_effect=ValueError("IMAGE_RESOURCE_CHANGED")
    elif outcome=="rpc_response_lost":
        scoped.rpc.return_value.execute.side_effect=ValueError("acceptance response invalid")
    send=AsyncMock()
    monkeypatch.setattr("services.websocket_manager.ws_manager.send_to_task_or_user",send)
    args={"mode":mode,"prompt":"  exact original  "}
    token=_dispatch_call_id.set("model-call")
    try:
        with patch("services.adapters.factory.create_image_adapter") as provider:
            if outcome=="invalid_input":
                with pytest.raises(ChatImageNotAcceptedError,match="IMAGE_RESOURCE_CHANGED"):
                    await ImageHandler(exe.db).accept_chat_image(exe,args)
                scoped.rpc.assert_not_called()
                send.assert_not_awaited()
            elif outcome=="rpc_response_lost":
                with pytest.raises(ValueError,match="acceptance response invalid") as failure:
                    await ImageHandler(exe.db).accept_chat_image(exe,args)
                assert not isinstance(failure.value,ChatImageNotAcceptedError)
                scoped.rpc.assert_called_once()
            else:
                result=await ImageHandler(exe.db).accept_chat_image(exe,args)
                frozen=scoped.rpc.call_args.args[1]["p_snapshot"]
                assert frozen["model"] == default_chat_image_model(mode)
                assert frozen["mode"] == mode and frozen["prompt"] == "  exact original  "
                assert len(frozen["references"]) == int(mode=="image_to_image")
                assert result["status"] == "submitted" and result["model"] == frozen["model"]
            provider.assert_not_called()
    finally:
        _dispatch_call_id.reset(token)


class TestGenerateVideo:
    """_generate_video 测试"""

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_error(self):
        exe = _make_executor()
        result = await exe._generate_video({"prompt": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_success_returns_url(self):
        exe = _make_executor()
        mock_result = MockVideoResult(video_url="https://cdn.example.com/demo.mp4")
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_video_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_video_cost", return_value={"user_credits": 30}):
            result = await exe._generate_video({"prompt": "a sunset scene"})

        assert "https://cdn.example.com/demo.mp4" in result
        assert "视频已生成" in result

    @pytest.mark.asyncio
    async def test_adapter_failure_refunds(self):
        exe = _make_executor()
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(side_effect=Exception("GPU error"))
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_video_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_video_cost", return_value={"user_credits": 30}):
            result = await exe._generate_video({"prompt": "test"})

        assert "失败" in result
        exe.db.rpc.assert_called()


class TestToolExecutorInheritance:
    """ToolExecutor 继承链验证"""

    def test_inherits_credit_mixin(self):
        from services.handlers.mixins.credit_mixin import CreditMixin
        from services.tool_executor import ToolExecutor
        assert issubclass(ToolExecutor, CreditMixin)

    def test_has_lock_credits_method(self):
        exe = _make_executor()
        assert hasattr(exe, "_lock_credits")
        assert hasattr(exe, "_confirm_deduct")
        assert hasattr(exe, "_refund_credits")

    def test_has_media_methods(self):
        exe = _make_executor()
        assert hasattr(exe, "_generate_image")
        assert hasattr(exe, "_generate_video")

    def test_has_erp_methods(self):
        exe = _make_executor()
        assert hasattr(exe, "_erp_dispatch")
        assert hasattr(exe, "_local_dispatch")
        assert hasattr(exe, "_get_erp_dispatcher")
