"""
WecomAIMixin 单元测试

覆盖：_stream_and_reply 流式推送+DB更新、_build_chat_messages 上下文构建、
      _handle_chat_response 直接回复/流式路径、_handle_chat_fallback 兜底、
      _get_user_balance / _deduct_credits 积分操作
"""

import sys
from pathlib import Path
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from schemas.wecom import WecomReplyContext
from services.wecom.wecom_message_service import WecomMessageService


def _make_db_mock():
    """按表名隔离的 DB mock"""
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)
    db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock()))
    db._table_mocks = table_mocks
    return db


def _make_reply_ctx(channel: str = "smart_robot") -> WecomReplyContext:
    if channel == "smart_robot":
        return WecomReplyContext(
            channel="smart_robot",
            ws_client=AsyncMock(),
            req_id="req001",
        )
    return WecomReplyContext(
        channel="app",
        wecom_userid="user_abc",
        agent_id=1000006,
    )


# ============================================================
# TestStreamAndReply
# ============================================================


class TestStreamAndReply:
    """_stream_and_reply 流式生成 + 推送"""

    @pytest.mark.asyncio
    async def test_accumulates_and_pushes_chunks(self):
        """每 5 个 chunk 推送一次，最终 finish=True"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._push_stream_chunk = AsyncMock()
        svc._update_assistant_message = AsyncMock()

        # 模拟 adapter 产出 10 个 chunk
        chunks = []
        for i in range(10):
            c = MagicMock()
            c.content = f"c{i}"
            chunks.append(c)

        adapter = MagicMock()
        adapter.stream_chat = MagicMock(return_value=_async_iter(chunks))

        ctx = _make_reply_ctx("smart_robot")

        await svc._stream_and_reply(adapter, [], ctx, "msg1")

        # 初始 "正在思考..." 1次 + 每 5 chunk 推送 2次 (5, 10) + 最终 finish 1次 = 4次
        push_calls = svc._push_stream_chunk.call_args_list
        assert len(push_calls) == 4
        # 最后一次 finish=True
        assert push_calls[-1].kwargs.get("finish") or push_calls[-1][0][-1] is True

    @pytest.mark.asyncio
    async def test_db_stores_original_content(self):
        """DB 存储原始内容，不做 clean_for_stream"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._push_stream_chunk = AsyncMock()
        svc._update_assistant_message = AsyncMock()

        # 含 mermaid 的内容
        chunk = MagicMock()
        chunk.content = "前文\n\n```mermaid\ngraph TD\nA-->B\n```\n\n后文"

        adapter = MagicMock()
        adapter.stream_chat = MagicMock(return_value=_async_iter([chunk]))

        ctx = _make_reply_ctx("smart_robot")
        await svc._stream_and_reply(adapter, [], ctx, "msg1")

        # DB 存原始（含 mermaid）
        update_call = svc._update_assistant_message.call_args
        stored_text = update_call[0][1] if len(update_call[0]) > 1 else update_call[1]["text"]
        assert "mermaid" in stored_text

    @pytest.mark.asyncio
    async def test_empty_output_sends_fallback(self):
        """adapter 无输出 → 发送兜底回复"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._push_stream_chunk = AsyncMock()
        svc._update_assistant_message = AsyncMock()
        svc._reply_text = AsyncMock()

        adapter = MagicMock()
        adapter.stream_chat = MagicMock(return_value=_async_iter([]))

        ctx = _make_reply_ctx("smart_robot")
        await svc._stream_and_reply(adapter, [], ctx, "msg1")

        svc._reply_text.assert_called_once()
        svc._update_assistant_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_display_cleaned(self):
        """推送到企微的内容经过 clean_for_stream 清理"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._push_stream_chunk = AsyncMock()
        svc._update_assistant_message = AsyncMock()

        # 产出 5 个 chunk，最后一个含 mermaid
        chunks = []
        for i in range(4):
            c = MagicMock()
            c.content = f"text{i} "
            chunks.append(c)
        last = MagicMock()
        last.content = "\n\n```mermaid\ngraph TD\n```"
        chunks.append(last)

        adapter = MagicMock()
        adapter.stream_chat = MagicMock(return_value=_async_iter(chunks))

        ctx = _make_reply_ctx("smart_robot")
        await svc._stream_and_reply(adapter, [], ctx, "msg1")

        # 检查第 5 个 chunk 推送时（chunk_count=5 → 推送），内容不含 mermaid
        push_calls = svc._push_stream_chunk.call_args_list
        for call in push_calls:
            content_arg = call[0][2] if len(call[0]) > 2 else call[1].get("content", "")
            assert "mermaid" not in content_arg


# ============================================================
# TestBuildChatMessages
# ============================================================


class TestBuildChatMessages:
    """_build_chat_messages 上下文构建"""

    @pytest.mark.asyncio
    async def test_basic_messages(self):
        """基本消息构建：时间 + 历史 + 当前消息"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_conversation_history = AsyncMock(return_value=[
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ])

        messages = await svc._build_chat_messages(
            user_id="u1",
            conversation_id="c1",
            text_content="你好",
        )

        # 应有：时间 system + 2 条历史 + 当前 user = 4 条
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert "当前时间" in messages[0]["content"]
        assert messages[-1] == {"role": "user", "content": "你好"}

    @pytest.mark.asyncio
    async def test_with_memory_and_system_prompt(self):
        """memory_prompt 和 system_prompt 注入"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_conversation_history = AsyncMock(return_value=[])

        messages = await svc._build_chat_messages(
            user_id="u1",
            conversation_id="c1",
            text_content="test",
            system_prompt="你是助手",
            memory_prompt="用户偏好：简洁回复",
        )

        roles = [m["role"] for m in messages]
        contents = [m["content"] for m in messages]
        # memory在前，system在后
        assert "用户偏好：简洁回复" in contents
        assert "你是助手" in contents
        mem_idx = contents.index("用户偏好：简洁回复")
        sys_idx = contents.index("你是助手")
        assert mem_idx < sys_idx

    @pytest.mark.asyncio
    async def test_with_search_context(self):
        """search_context 注入"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_conversation_history = AsyncMock(return_value=[])

        messages = await svc._build_chat_messages(
            user_id="u1",
            conversation_id="c1",
            text_content="搜索问题",
            search_context="搜索结果内容",
        )

        search_msgs = [m for m in messages if "搜索到的相关信息" in m["content"]]
        assert len(search_msgs) == 1

    @pytest.mark.asyncio
    async def test_with_image_urls(self):
        """有图片 → 用户消息使用多模态格式"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_conversation_history = AsyncMock(return_value=[])

        messages = await svc._build_chat_messages(
            user_id="u1",
            conversation_id="c1",
            text_content="看这张图",
            image_urls=["https://oss.example.com/img.jpg"],
        )

        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        # content 应是多模态列表
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][0] == {"type": "text", "text": "看这张图"}
        assert user_msg["content"][1]["type"] == "image_url"
        assert user_msg["content"][1]["image_url"]["url"] == "https://oss.example.com/img.jpg"

    @pytest.mark.asyncio
    async def test_without_image_urls_plain_text(self):
        """无图片 → 用户消息是普通字符串"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_conversation_history = AsyncMock(return_value=[])

        messages = await svc._build_chat_messages(
            user_id="u1",
            conversation_id="c1",
            text_content="纯文本",
        )

        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "纯文本"

    @pytest.mark.asyncio
    async def test_multiple_images(self):
        """多张图片 → 多个 image_url 块"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_conversation_history = AsyncMock(return_value=[])

        messages = await svc._build_chat_messages(
            user_id="u1",
            conversation_id="c1",
            text_content="两张图",
            image_urls=["https://oss.example.com/a.jpg", "https://oss.example.com/b.jpg"],
        )

        user_msg = messages[-1]
        assert len(user_msg["content"]) == 3  # 1 text + 2 images
        image_parts = [p for p in user_msg["content"] if p["type"] == "image_url"]
        assert len(image_parts) == 2


# ============================================================
# TestHandleChatResponse
# ============================================================


class TestHandleChatResponse:
    """_handle_chat_response 聊天响应处理"""

    @pytest.mark.asyncio
    async def test_direct_reply(self):
        """agent_result.direct_reply 存在 → 直接回复，不走流式"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._update_assistant_message = AsyncMock()
        svc._stream_and_reply = AsyncMock()

        agent_result = MagicMock()
        agent_result.direct_reply = "直接回答"

        ctx = _make_reply_ctx("smart_robot")
        await svc._handle_chat_response(
            "u1", "c1", "m1", "问题", ctx, agent_result, None,
        )

        svc._reply_text.assert_called_once_with(ctx, "直接回答")
        svc._update_assistant_message.assert_called_once_with("m1", "直接回答")
        svc._stream_and_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_streaming_path(self):
        """无 direct_reply → 走流式生成"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._build_chat_messages = AsyncMock(return_value=[])
        svc._stream_and_reply = AsyncMock()

        from schemas.message import GenerationType

        agent_result = MagicMock()
        agent_result.direct_reply = None
        agent_result.generation_type = GenerationType.CHAT
        agent_result.model = "test-model"
        agent_result.system_prompt = None
        agent_result.search_context = None

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "services.wecom.wecom_ai_mixin.create_chat_adapter",
        ) as mock_create:
            mock_adapter = AsyncMock()
            mock_create.return_value = mock_adapter

            with patch(
                "services.intent_router.resolve_auto_model",
                return_value="resolved-model",
            ):
                await svc._handle_chat_response(
                    "u1", "c1", "m1", "问题", ctx, agent_result, None,
                )

            svc._stream_and_reply.assert_called_once()
            mock_adapter.close.assert_called_once()


# ============================================================
# TestHandleChatFallback
# ============================================================


class TestHandleChatFallback:
    """_handle_chat_fallback 兜底聊天"""

    @pytest.mark.asyncio
    async def test_fallback_calls_stream(self):
        """兜底 → 使用默认模型流式生成"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._build_chat_messages = AsyncMock(return_value=[])
        svc._stream_and_reply = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "services.wecom.wecom_ai_mixin.create_chat_adapter",
        ) as mock_create:
            mock_adapter = AsyncMock()
            mock_create.return_value = mock_adapter

            await svc._handle_chat_fallback(
                "u1", "c1", "m1", "text", ctx,
            )

            svc._stream_and_reply.assert_called_once()
            mock_adapter.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_exception_replies_error(self):
        """兜底流式异常 → 发送错误回复"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._build_chat_messages = AsyncMock(
            side_effect=RuntimeError("DB down"),
        )
        svc._reply_text = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "services.wecom.wecom_ai_mixin.create_chat_adapter",
        ) as mock_create:
            mock_adapter = AsyncMock()
            mock_create.return_value = mock_adapter

            await svc._handle_chat_fallback(
                "u1", "c1", "m1", "text", ctx,
            )

            svc._reply_text.assert_called_once()
            reply_text = svc._reply_text.call_args[0][1]
            assert "问题" in reply_text or "稍后" in reply_text


# ============================================================
# TestGetUserBalance
# ============================================================


class TestGetUserBalance:
    """_get_user_balance 积分余额"""

    def test_returns_credits(self):
        """正常返回积分"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        mock_result = MagicMock()
        mock_result.data = {"credits": 500}
        db._table_mocks.clear()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.single.return_value = chain
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        balance = svc._get_user_balance("u1")
        assert balance == 500

    def test_no_data_returns_zero(self):
        """无数据 → 0"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        mock_result = MagicMock()
        mock_result.data = None
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.single.return_value = chain
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        balance = svc._get_user_balance("u1")
        assert balance == 0

    def test_exception_returns_zero(self):
        """异常 → 0"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        db.table = MagicMock(side_effect=RuntimeError("DB crash"))

        balance = svc._get_user_balance("u1")
        assert balance == 0


# ============================================================
# TestDeductCredits
# ============================================================


class TestDeductCredits:
    """_deduct_credits 积分扣除"""

    def test_calls_rpc(self):
        """正常调用 rpc"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._deduct_credits("u1", 100, "test reason")

        db.rpc.assert_called_once_with("deduct_credits_atomic", {
            "p_user_id": "u1",
            "p_amount": 100,
            "p_reason": "test reason",
            "p_change_type": "conversation_cost",
        })

    def test_exception_no_raise(self):
        """异常不抛出"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        db.rpc = MagicMock(side_effect=RuntimeError("RPC fail"))

        # 不应抛出
        svc._deduct_credits("u1", 100, "test")


# ============================================================
# TestRunAgentLoop
# ============================================================


class TestRunAgentLoop:
    """_run_agent_loop Agent Loop 路由 + 降级"""

    @pytest.mark.asyncio
    async def test_agent_loop_success(self):
        """Agent Loop 启用且成功 → 返回 AgentResult"""
        from schemas.message import GenerationType
        from services.agent_types import AgentResult

        db = _make_db_mock()
        svc = WecomMessageService(db)

        expected = AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=2, total_tokens=500,
        )
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=expected)
        mock_agent.close = AsyncMock()

        with patch.object(svc, "settings") as mock_settings:
            mock_settings.agent_loop_enabled = True

            with patch(
                "services.agent_loop.AgentLoop",
                return_value=mock_agent,
            ):
                result = await svc._run_agent_loop("u1", "c1", [])

        assert result.generation_type == GenerationType.CHAT
        assert result.turns_used == 2
        mock_agent.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_loop_fails_fallback_intent_router(self):
        """Agent Loop 失败 → 降级到 IntentRouter"""
        from schemas.message import GenerationType

        db = _make_db_mock()
        svc = WecomMessageService(db)

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("agent fail"))
        mock_agent.close = AsyncMock()

        mock_decision = MagicMock()
        mock_decision.generation_type = GenerationType.IMAGE
        mock_decision.recommended_model = "test-img"
        mock_decision.system_prompt = None
        mock_decision.tool_params = {"prompt": "cat"}

        mock_router = AsyncMock()
        mock_router.route = AsyncMock(return_value=mock_decision)
        mock_router.close = AsyncMock()

        with patch.object(svc, "settings") as mock_settings:
            mock_settings.agent_loop_enabled = True

            with patch(
                "services.agent_loop.AgentLoop",
                return_value=mock_agent,
            ), patch(
                "services.intent_router.IntentRouter",
                return_value=mock_router,
            ):
                result = await svc._run_agent_loop("u1", "c1", [])

        assert result.generation_type == GenerationType.IMAGE
        mock_router.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_fail_returns_chat(self):
        """Agent Loop + IntentRouter 都失败 → 兜底 CHAT"""
        from schemas.message import GenerationType

        db = _make_db_mock()
        svc = WecomMessageService(db)

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("fail"))
        mock_agent.close = AsyncMock()

        mock_router = AsyncMock()
        mock_router.route = AsyncMock(side_effect=RuntimeError("router fail"))
        mock_router.close = AsyncMock()

        with patch.object(svc, "settings") as mock_settings:
            mock_settings.agent_loop_enabled = True

            with patch(
                "services.agent_loop.AgentLoop",
                return_value=mock_agent,
            ), patch(
                "services.intent_router.IntentRouter",
                return_value=mock_router,
            ):
                result = await svc._run_agent_loop("u1", "c1", [])

        assert result.generation_type == GenerationType.CHAT
        assert result.turns_used == 0


# ============================================================
# TestBuildMemoryPrompt
# ============================================================


class TestBuildMemoryPrompt:
    """_build_memory_prompt 记忆构建"""

    @pytest.mark.asyncio
    async def test_memory_enabled_with_results(self):
        """记忆启用 + 有结果 → 返回 prompt"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        with patch(
            "services.memory_service.MemoryService",
        ) as MockMemSvc, patch(
            "services.memory_config.build_memory_system_prompt",
            return_value="记忆提示",
        ):
            mock_inst = AsyncMock()
            mock_inst.is_memory_enabled = AsyncMock(return_value=True)
            mock_inst.get_relevant_memories = AsyncMock(
                return_value=[{"text": "m1"}],
            )
            MockMemSvc.return_value = mock_inst

            result = await svc._build_memory_prompt("u1", "query")

        assert result == "记忆提示"

    @pytest.mark.asyncio
    async def test_memory_disabled(self):
        """记忆未启用 → None"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        with patch(
            "services.memory_service.MemoryService",
        ) as MockMemSvc:
            mock_inst = AsyncMock()
            mock_inst.is_memory_enabled = AsyncMock(return_value=False)
            MockMemSvc.return_value = mock_inst

            result = await svc._build_memory_prompt("u1", "query")

        assert result is None

    @pytest.mark.asyncio
    async def test_memory_exception_returns_none(self):
        """记忆异常 → None（不影响主流程）"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        with patch(
            "services.memory_service.MemoryService",
            side_effect=RuntimeError("mem crash"),
        ):
            result = await svc._build_memory_prompt("u1", "query")

        assert result is None


# ============================================================
# TestHandleImageResponse
# ============================================================


class TestHandleImageResponse:
    """_handle_image_response 图片生成"""

    @pytest.mark.asyncio
    async def test_insufficient_credits(self):
        """积分不足 → 回复积分不足卡片"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_credits_insufficient = AsyncMock()
        svc._get_user_balance = MagicMock(return_value=0)

        agent_result = MagicMock()
        agent_result.model = "test-img"
        agent_result.tool_params = {"prompt": "cat"}

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "config.kie_models.calculate_image_cost",
            return_value={"user_credits": 100},
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="test-img",
        ):
            await svc._handle_image_response(
                "u1", "c1", "m1", "画猫", ctx, agent_result,
            )

        # 应调用积分不足卡片
        svc._reply_credits_insufficient.assert_called_once()
        call_args = svc._reply_credits_insufficient.call_args
        assert call_args[0][1] == 100  # needed
        assert call_args[0][2] == 0    # balance
        assert call_args[0][3] == "图片"  # action

    @pytest.mark.asyncio
    async def test_generation_success(self):
        """积分充足 + 生成成功 → 扣积分 + 发送媒体"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._get_user_balance = MagicMock(return_value=1000)
        svc._deduct_credits = MagicMock()
        svc._send_media_to_wecom = AsyncMock()

        agent_result = MagicMock()
        agent_result.model = "test-img"
        agent_result.tool_params = {"prompt": "cat", "aspect_ratio": "16:9"}

        mock_gen_result = MagicMock()
        mock_gen_result.image_urls = ["https://img.example.com/1.png"]

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_gen_result)
        mock_adapter.close = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "config.kie_models.calculate_image_cost",
            return_value={"user_credits": 50},
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="test-img",
        ), patch(
            "services.adapters.factory.create_image_adapter",
            return_value=mock_adapter,
        ):
            await svc._handle_image_response(
                "u1", "c1", "m1", "画猫", ctx, agent_result,
            )

        svc._deduct_credits.assert_called_once_with("u1", 50, "Wecom Image: test-img", org_id=None)
        svc._send_media_to_wecom.assert_called_once()

    @pytest.mark.asyncio
    async def test_generation_failure(self):
        """生成失败 → 回复错误，不扣积分"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._get_user_balance = MagicMock(return_value=1000)
        svc._deduct_credits = MagicMock()

        agent_result = MagicMock()
        agent_result.model = "test-img"
        agent_result.tool_params = {"prompt": "cat"}

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(side_effect=RuntimeError("gen fail"))
        mock_adapter.close = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "config.kie_models.calculate_image_cost",
            return_value={"user_credits": 50},
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="test-img",
        ), patch(
            "services.adapters.factory.create_image_adapter",
            return_value=mock_adapter,
        ):
            await svc._handle_image_response(
                "u1", "c1", "m1", "画猫", ctx, agent_result,
            )

        svc._deduct_credits.assert_not_called()
        # 回复包含 "生成" 通知 + "失败" 错误
        replies = [c[0][1] for c in svc._reply_text.call_args_list]
        assert any("失败" in r for r in replies)


# ============================================================
# TestHandleVideoResponse
# ============================================================


class TestHandleVideoResponse:
    """_handle_video_response 视频生成"""

    @pytest.mark.asyncio
    async def test_insufficient_credits(self):
        """积分不足 → 回复积分不足卡片"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_credits_insufficient = AsyncMock()
        svc._get_user_balance = MagicMock(return_value=0)

        agent_result = MagicMock()
        agent_result.model = "test-vid"
        agent_result.tool_params = {"prompt": "sunrise"}

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "config.kie_models.calculate_video_cost",
            return_value={"user_credits": 200},
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="test-vid",
        ):
            await svc._handle_video_response(
                "u1", "c1", "m1", "日出视频", ctx, agent_result,
            )

        svc._reply_credits_insufficient.assert_called_once()
        call_args = svc._reply_credits_insufficient.call_args
        assert call_args[0][1] == 200  # needed
        assert call_args[0][2] == 0    # balance
        assert call_args[0][3] == "视频"  # action

    @pytest.mark.asyncio
    async def test_generation_success(self):
        """生成成功 → 扣积分 + 发送视频"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._get_user_balance = MagicMock(return_value=1000)
        svc._deduct_credits = MagicMock()
        svc._send_media_to_wecom = AsyncMock()

        agent_result = MagicMock()
        agent_result.model = "test-vid"
        agent_result.tool_params = {"prompt": "sunrise"}

        mock_gen_result = MagicMock()
        mock_gen_result.video_url = "https://vid.example.com/1.mp4"

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_gen_result)
        mock_adapter.close = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "config.kie_models.calculate_video_cost",
            return_value={"user_credits": 200},
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="test-vid",
        ), patch(
            "services.adapters.factory.create_video_adapter",
            return_value=mock_adapter,
        ):
            await svc._handle_video_response(
                "u1", "c1", "m1", "日出视频", ctx, agent_result,
            )

        svc._deduct_credits.assert_called_once()
        svc._send_media_to_wecom.assert_called_once()
        # 验证传入 video_url
        call_args = svc._send_media_to_wecom.call_args
        assert call_args[0][1] == ["https://vid.example.com/1.mp4"]
        assert call_args[0][2] == "video"

    @pytest.mark.asyncio
    async def test_no_video_url(self):
        """生成成功但无 video_url → 回复失败"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._get_user_balance = MagicMock(return_value=1000)
        svc._deduct_credits = MagicMock()

        agent_result = MagicMock()
        agent_result.model = "test-vid"
        agent_result.tool_params = {"prompt": "sunrise"}

        mock_gen_result = MagicMock(spec=[])  # no video_url attr

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_gen_result)
        mock_adapter.close = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")

        with patch(
            "config.kie_models.calculate_video_cost",
            return_value={"user_credits": 200},
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="test-vid",
        ), patch(
            "services.adapters.factory.create_video_adapter",
            return_value=mock_adapter,
        ):
            await svc._handle_video_response(
                "u1", "c1", "m1", "日出视频", ctx, agent_result,
            )

        svc._deduct_credits.assert_not_called()
        replies = [c[0][1] for c in svc._reply_text.call_args_list]
        assert any("失败" in r for r in replies)


# ============================================================
# TestSendMediaToWecom
# ============================================================


class TestSendMediaToWecom:
    """_send_media_to_wecom 双渠道媒体发送"""

    @pytest.mark.asyncio
    async def test_smart_robot_image(self):
        """smart_robot 渠道图片 → markdown 回复"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        # 设置 DB chain mock
        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        ctx = _make_reply_ctx("smart_robot")

        await svc._send_media_to_wecom(
            ctx, ["https://img.example.com/1.png"], "image", "m1",
        )

        ctx.ws_client.send_reply.assert_called_once()
        call_kwargs = ctx.ws_client.send_reply.call_args.kwargs
        assert call_kwargs["msgtype"] == "markdown"
        assert "![图片]" in call_kwargs["content"]["content"]

    @pytest.mark.asyncio
    async def test_smart_robot_video(self):
        """smart_robot 渠道视频 → 文本链接"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        ctx = _make_reply_ctx("smart_robot")

        await svc._send_media_to_wecom(
            ctx, ["https://vid.example.com/1.mp4"], "video", "m1",
        )

        ctx.ws_client.send_reply.assert_called_once()
        call_kwargs = ctx.ws_client.send_reply.call_args.kwargs
        assert call_kwargs["msgtype"] == "text"
        assert "视频已生成" in call_kwargs["content"]["content"]

    @pytest.mark.asyncio
    async def test_app_channel_upload_success(self):
        """app 渠道上传成功 → send_image"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.upload_temp_media",
            new=AsyncMock(return_value="mid1"),
        ), patch(
            "services.wecom.app_message_sender.send_image",
            new=AsyncMock(return_value=True),
        ) as mock_send_img, patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ):
            await svc._send_media_to_wecom(
                ctx, ["https://img.example.com/1.png"], "image", "m1",
            )

        mock_send_img.assert_called_once_with("user_abc", "mid1", 1000006)

    @pytest.mark.asyncio
    async def test_app_channel_upload_failure_fallback(self):
        """app 渠道上传失败 → 降级发文本链接"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.upload_temp_media",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.wecom.app_message_sender.send_image",
            new=AsyncMock(),
        ), patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(return_value=True),
        ) as mock_send_text:
            await svc._send_media_to_wecom(
                ctx, ["https://img.example.com/1.png"], "image", "m1",
            )

        mock_send_text.assert_called_once()
        text_arg = mock_send_text.call_args[0][1]
        assert "图片已生成" in text_arg

    @pytest.mark.asyncio
    async def test_updates_db_message(self):
        """更新 DB 消息内容"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        ctx = _make_reply_ctx("smart_robot")

        await svc._send_media_to_wecom(
            ctx, ["https://img.example.com/1.png"], "image", "m1",
        )

        # 验证 DB 更新
        db.table.assert_called_with("messages")
        chain.update.assert_called_once()
        update_data = chain.update.call_args[0][0]
        assert update_data["status"] == "completed"
        assert update_data["content"][0]["type"] == "image"


# ── helper ──────────────────────────────────────────────


async def _async_iter(items):
    """将列表转为 async iterator"""
    for item in items:
        yield item


# ============================================================
# TestStreamAndReplyReuseStream — 复用已有 stream
# ============================================================


class TestStreamAndReplyReuseStream:
    """_stream_and_reply 复用已有 active_stream_id"""

    @pytest.mark.asyncio
    async def test_reuses_existing_stream_id(self):
        """有 active_stream_id → 不新建 stream，不发"正在思考..." """
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._push_stream_chunk = AsyncMock()
        svc._update_assistant_message = AsyncMock()

        chunks = [MagicMock(content="hello")]
        adapter = MagicMock()
        adapter.stream_chat = MagicMock(return_value=_async_iter(chunks))

        ctx = _make_reply_ctx("smart_robot")
        ctx.active_stream_id = "existing_stream_999"

        await svc._stream_and_reply(adapter, [], ctx, "msg1")

        # 只有 finish push，无 "正在思考..." 初始 push
        calls = svc._push_stream_chunk.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs.get("finish") is True
        # 使用已有 stream_id
        assert calls[0].args[1] == "existing_stream_999"

    @pytest.mark.asyncio
    async def test_creates_new_stream_when_none(self):
        """无 active_stream_id → 创建新 stream + 发"正在思考..." """
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._push_stream_chunk = AsyncMock()
        svc._update_assistant_message = AsyncMock()

        chunks = [MagicMock(content="world")]
        adapter = MagicMock()
        adapter.stream_chat = MagicMock(return_value=_async_iter(chunks))

        ctx = _make_reply_ctx("smart_robot")
        assert ctx.active_stream_id is None

        await svc._stream_and_reply(adapter, [], ctx, "msg2")

        # 初始 "正在思考..." + finish = 2 次
        calls = svc._push_stream_chunk.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs.get("finish") is False
        assert calls[-1].kwargs.get("finish") is True


# ============================================================
# TestHandleChatResponseWithImages — 多模态聊天路径
# ============================================================


class TestHandleChatResponseWithImages:
    """_handle_chat_response 传入 image_urls → 多模态处理"""

    @pytest.mark.asyncio
    async def test_image_urls_passed_to_build(self):
        """image_urls 传递到 _build_chat_messages"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        mock_agent = MagicMock()
        mock_agent.direct_reply = None
        mock_agent.model = "auto"
        mock_agent.system_prompt = None
        mock_agent.search_context = None
        from schemas.message import GenerationType
        mock_agent.generation_type = GenerationType.CHAT

        ctx = _make_reply_ctx("smart_robot")
        ctx.active_stream_id = "s1"

        with (
            patch.object(svc, "_build_chat_messages", new=AsyncMock(return_value=[])) as mock_build,
            patch.object(svc, "_stream_and_reply", new=AsyncMock()) as mock_stream,
            patch(
                "services.wecom.wecom_ai_mixin.create_chat_adapter",
                return_value=MagicMock(close=AsyncMock()),
            ),
            patch(
                "services.intent_router.resolve_auto_model",
                return_value="test-model",
            ),
        ):
            await svc._handle_chat_response(
                "u1", "c1", "m1", "看这张图",
                ctx, mock_agent, None,
                image_urls=["https://oss.example.com/img.jpg"],
            )

            mock_build.assert_called_once()
            build_kwargs = mock_build.call_args[1]
            assert build_kwargs["image_urls"] == ["https://oss.example.com/img.jpg"]


# ============================================================
# TestHandleImageResponseEmpty — 图片生成无结果
# ============================================================


class TestHandleImageResponseEmpty:
    """_handle_image_response 生成结果无 URL"""

    @pytest.mark.asyncio
    async def test_empty_urls_replies_failure(self):
        """generate 返回空 image_urls → 提示失败"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._reply_credits_insufficient = AsyncMock()

        mock_agent = MagicMock()
        mock_agent.model = "auto"
        mock_agent.tool_params = {"prompt": "a cat"}

        ctx = _make_reply_ctx("smart_robot")

        mock_result = MagicMock()
        mock_result.image_urls = []  # 空列表

        mock_adapter = MagicMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with (
            patch(
                "services.wecom.wecom_ai_mixin.create_chat_adapter",
            ),
            patch(
                "config.kie_models.calculate_image_cost",
                return_value={"user_credits": 10},
            ),
            patch.object(svc, "_get_user_balance", return_value=100),
            patch(
                "services.intent_router.resolve_auto_model",
                return_value="test-img-model",
            ),
            patch(
                "services.adapters.factory.create_image_adapter",
                return_value=mock_adapter,
            ),
        ):
            await svc._handle_image_response(
                "u1", "c1", "m1", "画只猫", ctx, mock_agent,
            )

        # 应有"失败"提示
        reply_calls = svc._reply_text.call_args_list
        assert any("失败" in str(c) for c in reply_calls)


# ============================================================
# TestHandleVideoResponseException — 视频生成异常
# ============================================================


class TestHandleVideoResponseException:
    """_handle_video_response 生成异常 → 错误提示"""

    @pytest.mark.asyncio
    async def test_gen_exception_replies_error(self):
        """adapter.generate 异常 → 提示失败"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._reply_text = AsyncMock()
        svc._reply_credits_insufficient = AsyncMock()

        mock_agent = MagicMock()
        mock_agent.model = "auto"
        mock_agent.tool_params = {"prompt": "a sunset"}

        ctx = _make_reply_ctx("smart_robot")

        mock_adapter = MagicMock()
        mock_adapter.generate = AsyncMock(side_effect=RuntimeError("GPU OOM"))
        mock_adapter.close = AsyncMock()

        with (
            patch(
                "config.kie_models.calculate_video_cost",
                return_value={"user_credits": 50},
            ),
            patch.object(svc, "_get_user_balance", return_value=200),
            patch(
                "services.intent_router.resolve_auto_model",
                return_value="test-vid-model",
            ),
            patch(
                "services.adapters.factory.create_video_adapter",
                return_value=mock_adapter,
            ),
        ):
            await svc._handle_video_response(
                "u1", "c1", "m1", "拍日落", ctx, mock_agent,
            )

        # 应有"失败"提示
        reply_calls = svc._reply_text.call_args_list
        assert any("失败" in str(c) for c in reply_calls)


# ============================================================
# TestRunAgentLoopDisabled — agent_loop 禁用走 IntentRouter
# ============================================================


class TestRunAgentLoopDisabled:
    """_run_agent_loop 在 agent_loop_enabled=False 时直接走 IntentRouter"""

    @pytest.mark.asyncio
    async def test_disabled_uses_intent_router(self):
        """agent_loop_enabled=False → IntentRouter 路由"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc.settings = MagicMock()
        svc.settings.agent_loop_enabled = False

        from schemas.message import GenerationType, TextPart

        mock_decision = MagicMock()
        mock_decision.generation_type = GenerationType.CHAT
        mock_decision.recommended_model = "test-model"
        mock_decision.system_prompt = "你是助手"
        mock_decision.tool_params = {}

        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=mock_decision)
        mock_router.close = AsyncMock()

        with patch(
            "services.intent_router.IntentRouter",
            return_value=mock_router,
        ):
            result = await svc._run_agent_loop(
                "u1", "c1", [TextPart(text="hi")],
            )

        assert result.generation_type == GenerationType.CHAT
        assert result.model == "test-model"
        mock_router.route.assert_called_once()
