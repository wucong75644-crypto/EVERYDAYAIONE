"""MessageGateway 单元测试

覆盖：
- save_system_message：存消息 + org_id 写入 + 扇出逻辑
- fanout_to_wecom：Markdown 清理 + 推送
- skip 逻辑：skip_wecom / skip_web
- 边界：空文本 / 无企微映射 / 无对话自动创建
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.message_gateway import MessageGateway


# ── 测试用 DB Stub ────────────────────────────────────────


class _ChainStub:
    """链式 query stub，模拟 supabase 查询链"""

    def __init__(self, data=None):
        self._data = data if data is not None else []

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def maybe_single(self): return self

    def insert(self, data, **kw):
        self._inserted = data
        return self

    def update(self, data, **kw):
        self._updated = data
        return self

    def execute(self):
        result = MagicMock()
        result.data = self._data
        return result


class _GatewayDB:
    """可配置各表返回数据的 DB stub"""

    def __init__(self, table_data: dict | None = None):
        self._tables = table_data or {}
        self._inserts: dict[str, list] = {}
        self.org_id = None

    def table(self, name):
        data = self._tables.get(name, [])
        stub = _ChainStub(data)
        # 劫持 insert，记录写入的数据
        original_insert = stub.insert

        def tracked_insert(insert_data, **kw):
            self._inserts.setdefault(name, []).append(insert_data)
            return original_insert(insert_data, **kw)

        stub.insert = tracked_insert
        return stub

    def rpc(self, *a, **kw):
        return _ChainStub()


# ── save_system_message ───────────────────────────────────


class TestSaveSystemMessage:
    @pytest.mark.asyncio
    async def test_saves_message_with_org_id(self):
        """消息插入时必须携带 org_id（多租户隔离）"""
        db = _GatewayDB({
            "conversations": [{"id": "conv-1"}],
            "messages": [{"id": "msg-1"}],
        })
        gateway = MessageGateway(db)

        with patch.object(gateway, "_notify_web", new_callable=AsyncMock):
            with patch.object(gateway, "_push_to_wecom", new_callable=AsyncMock):
                result = await gateway.save_system_message(
                    user_id="u1", org_id="org-1",
                    text="hello", source="test",
                )

        assert result is not None
        # 验证 insert 数据包含 org_id
        msg_inserts = db._inserts.get("messages", [])
        assert len(msg_inserts) == 1
        assert msg_inserts[0]["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self):
        """空文本直接返回 None，不触发任何操作"""
        gateway = MessageGateway(MagicMock())
        result = await gateway.save_system_message(
            user_id="u1", org_id="org-1", text="",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_wecom_does_not_push(self):
        """skip_wecom=True 时不推送到企微"""
        db = _GatewayDB({
            "conversations": [{"id": "conv-1"}],
            "messages": [{"id": "msg-1"}],
        })
        gateway = MessageGateway(db)

        with patch.object(gateway, "_notify_web", new_callable=AsyncMock):
            with patch.object(
                gateway, "_push_to_wecom", new_callable=AsyncMock,
            ) as mock_push:
                await gateway.save_system_message(
                    user_id="u1", org_id="org-1",
                    text="result", skip_wecom=True,
                )
        mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_web_does_not_notify(self):
        """skip_web=True 时不通知 Web 前端"""
        db = _GatewayDB({
            "conversations": [{"id": "conv-1"}],
            "messages": [{"id": "msg-1"}],
        })
        gateway = MessageGateway(db)

        with patch.object(
            gateway, "_notify_web", new_callable=AsyncMock,
        ) as mock_notify:
            with patch.object(gateway, "_push_to_wecom", new_callable=AsyncMock):
                await gateway.save_system_message(
                    user_id="u1", org_id="org-1",
                    text="alert", skip_web=True,
                )
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_conversation_if_not_exists(self):
        """没有企微对话时自动创建"""
        db = _GatewayDB({
            "conversations": [],  # 无对话
            "messages": [{"id": "msg-1"}],
        })
        gateway = MessageGateway(db)

        mock_conv = {"id": "new-conv"}
        with patch(
            "services.conversation_service.ConversationService",
        ) as MockConvSvc:
            MockConvSvc.return_value.create_conversation = AsyncMock(
                return_value=mock_conv,
            )
            with patch.object(gateway, "_notify_web", new_callable=AsyncMock):
                with patch.object(gateway, "_push_to_wecom", new_callable=AsyncMock):
                    result = await gateway.save_system_message(
                        user_id="u1", org_id="org-1", text="hello",
                    )

        assert result is not None
        MockConvSvc.return_value.create_conversation.assert_called_once_with(
            user_id="u1", title="企微对话", model_id="auto",
            org_id="org-1", source="wecom",
        )

    @pytest.mark.asyncio
    async def test_no_conversation_returns_none(self):
        """查找/创建对话失败时返回 None"""
        gateway = MessageGateway(MagicMock())

        with patch.object(
            gateway, "_get_or_create_wecom_conversation",
            new_callable=AsyncMock, return_value=None,
        ):
            result = await gateway.save_system_message(
                user_id="u1", org_id="org-1", text="hello",
            )
        assert result is None


# ── fanout_to_wecom ───────────────────────────────────────


class TestFanoutToWecom:
    @pytest.mark.asyncio
    async def test_calls_push_to_wecom(self):
        """fanout_to_wecom 调用 _push_to_wecom"""
        gateway = MessageGateway(MagicMock())

        with patch.object(
            gateway, "_push_to_wecom",
            new_callable=AsyncMock, return_value=True,
        ) as mock_push:
            result = await gateway.fanout_to_wecom("u1", "org-1", "hello")

        assert result is True
        mock_push.assert_called_once_with("u1", "org-1", "hello")


# ── _push_to_wecom（Markdown 清理 + 推送）─────────────────


class TestPushToWecom:
    @pytest.mark.asyncio
    async def test_cleans_markdown_before_push(self):
        """推送前调用 clean_for_stream 清理 Markdown"""
        db = _GatewayDB({
            "wecom_user_mappings": [{"wecom_userid": "wu1"}],
        })
        gateway = MessageGateway(db)

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value="pushed")

        with patch(
            "services.scheduler.push_dispatcher.push_dispatcher",
            mock_dispatcher,
        ):
            with patch(
                "services.wecom.markdown_adapter.clean_for_stream",
                return_value="cleaned text",
            ) as mock_clean:
                result = await gateway._push_to_wecom("u1", "org-1", "# Hello")

        mock_clean.assert_called_once_with("# Hello")
        # dispatch 收到的是清理后的文本
        call_args = mock_dispatcher.dispatch.call_args
        assert call_args.kwargs["text"] == "cleaned text"
        assert result is True

    @pytest.mark.asyncio
    async def test_no_mapping_returns_false(self):
        """用户没有企微映射时返回 False"""
        db = _GatewayDB({
            "wecom_user_mappings": [],
        })
        gateway = MessageGateway(db)

        result = await gateway._push_to_wecom("u1", "org-1", "hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_push_failure_returns_false(self):
        """推送异常时返回 False 不抛"""
        db = _GatewayDB({
            "wecom_user_mappings": [{"wecom_userid": "wu1"}],
        })
        gateway = MessageGateway(db)

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(
            side_effect=RuntimeError("ws down"),
        )

        with patch(
            "services.scheduler.push_dispatcher.push_dispatcher",
            mock_dispatcher,
        ):
            with patch(
                "services.wecom.markdown_adapter.clean_for_stream",
                return_value="text",
            ):
                result = await gateway._push_to_wecom("u1", "org-1", "hello")

        assert result is False


# ── _maybe_fanout_to_wecom（message_mixin 集成）──────────


class TestMaybeFanoutToWecom:
    """message_mixin._maybe_fanout_to_wecom 行为验证"""

    def _make_mixin(self, conv_source: str, conv_org_id: str | None = "org-1"):
        """构造带 mock db 的 MessageMixin 实例"""
        from services.handlers.mixins.message_mixin import MessageMixin

        mixin = MessageMixin()
        # mock db: conversations.maybe_single().execute() 返回单条记录
        conv_data = {
            "source": conv_source,
            "org_id": conv_org_id,
        }
        result_mock = MagicMock()
        result_mock.data = conv_data

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.maybe_single.return_value = chain
        chain.execute.return_value = result_mock

        db = MagicMock()
        db.table.return_value = chain
        mixin.db = db
        return mixin

    @pytest.mark.asyncio
    async def test_source_wecom_triggers_fanout(self):
        """source=wecom 的对话触发企微推送"""
        mixin = self._make_mixin("wecom")
        content_dicts = [{"type": "text", "text": "hello"}]
        task = {"user_id": "u1"}

        with patch(
            "services.message_gateway.MessageGateway",
        ) as MockGW:
            MockGW.return_value.fanout_to_wecom = AsyncMock()
            await mixin._maybe_fanout_to_wecom("conv-1", content_dicts, task)

        MockGW.return_value.fanout_to_wecom.assert_called_once_with(
            "u1", "org-1", "hello",
        )

    @pytest.mark.asyncio
    async def test_source_web_skips_fanout(self):
        """source=web 的对话不触发企微推送"""
        mixin = self._make_mixin("web")
        content_dicts = [{"type": "text", "text": "hello"}]
        task = {"user_id": "u1"}

        with patch(
            "services.message_gateway.MessageGateway",
        ) as MockGW:
            MockGW.return_value.fanout_to_wecom = AsyncMock()
            await mixin._maybe_fanout_to_wecom("conv-1", content_dicts, task)

        MockGW.return_value.fanout_to_wecom.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_text_content_skips(self):
        """content_dicts 无文本时不推送"""
        mixin = self._make_mixin("wecom")
        content_dicts = [{"type": "image", "url": "http://example.com/img.png"}]
        task = {"user_id": "u1"}

        with patch(
            "services.message_gateway.MessageGateway",
        ) as MockGW:
            MockGW.return_value.fanout_to_wecom = AsyncMock()
            await mixin._maybe_fanout_to_wecom("conv-1", content_dicts, task)

        MockGW.return_value.fanout_to_wecom.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_org_id_skips(self):
        """对话无 org_id 时不推送"""
        mixin = self._make_mixin("wecom", conv_org_id=None)
        content_dicts = [{"type": "text", "text": "hello"}]
        task = {"user_id": "u1"}

        with patch(
            "services.message_gateway.MessageGateway",
        ) as MockGW:
            MockGW.return_value.fanout_to_wecom = AsyncMock()
            await mixin._maybe_fanout_to_wecom("conv-1", content_dicts, task)

        MockGW.return_value.fanout_to_wecom.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        """异常不传播（fire-and-forget）"""
        mixin = self._make_mixin("wecom")
        content_dicts = [{"type": "text", "text": "hello"}]
        task = {"user_id": "u1"}

        with patch(
            "services.message_gateway.MessageGateway",
        ) as MockGW:
            MockGW.return_value.fanout_to_wecom = AsyncMock(
                side_effect=RuntimeError("boom"),
            )
            # 不应抛异常
            await mixin._maybe_fanout_to_wecom("conv-1", content_dicts, task)
