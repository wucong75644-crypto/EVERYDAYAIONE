"""
Handler 工厂单元测试

覆盖：
- get_handler() 返回正确的 Handler 类型
- org_id 注入到 handler 实例
- user_id 存在时构造 request_ctx 并注入
- user_id 缺失时 request_ctx 保持 None
- request_id 透传到 RequestContext
- 不支持的 GenerationType 抛 ValueError
- HandlerFactory.register() 注册自定义 Handler
"""

from unittest.mock import MagicMock

import pytest

from schemas.message import GenerationType
from services.handlers.factory import get_handler, HandlerFactory


@pytest.fixture
def mock_db():
    return MagicMock()


class TestGetHandler:
    """get_handler() 便捷函数"""

    def test_chat_returns_chat_handler(self, mock_db):
        handler = get_handler(GenerationType.CHAT, mock_db)
        from services.handlers.chat_handler import ChatHandler
        assert isinstance(handler, ChatHandler)

    def test_image_returns_image_handler(self, mock_db):
        handler = get_handler(GenerationType.IMAGE, mock_db)
        from services.handlers.image_handler import ImageHandler
        assert isinstance(handler, ImageHandler)

    def test_video_returns_video_handler(self, mock_db):
        handler = get_handler(GenerationType.VIDEO, mock_db)
        from services.handlers.video_handler import VideoHandler
        assert isinstance(handler, VideoHandler)

    def test_unsupported_type_raises(self, mock_db):
        with pytest.raises(ValueError, match="Unsupported generation type"):
            get_handler("nonexistent", mock_db)


class TestOrgIdInjection:
    """org_id 注入"""

    def test_org_id_set_on_handler(self, mock_db):
        handler = get_handler(GenerationType.CHAT, mock_db, org_id="org123")
        assert handler.org_id == "org123"

    def test_org_id_none_by_default(self, mock_db):
        handler = get_handler(GenerationType.CHAT, mock_db)
        assert handler.org_id is None


class TestRequestCtxInjection:
    """request_ctx 注入（时间事实层 SSOT）"""

    def test_request_ctx_created_when_user_id_provided(self, mock_db):
        """user_id 存在时，factory 构造 RequestContext 并注入"""
        handler = get_handler(
            GenerationType.CHAT, mock_db,
            org_id="org1", user_id="u1", request_id="req-abc",
        )
        ctx = handler.request_ctx
        assert ctx is not None
        assert ctx.user_id == "u1"
        assert ctx.org_id == "org1"
        assert ctx.request_id == "req-abc"

    def test_request_ctx_none_when_no_user_id(self, mock_db):
        """user_id 缺失时，request_ctx 保持 None（ImageHandler/VideoHandler 场景）"""
        handler = get_handler(GenerationType.IMAGE, mock_db, org_id="org1")
        assert handler.request_ctx is None

    def test_request_ctx_immutable(self, mock_db):
        """RequestContext 是 frozen dataclass，不可修改"""
        handler = get_handler(
            GenerationType.CHAT, mock_db,
            user_id="u1", request_id="req1",
        )
        with pytest.raises(AttributeError):
            handler.request_ctx.user_id = "hacked"

    def test_request_ctx_has_valid_time(self, mock_db):
        """request_ctx 包含有效的北京时间"""
        handler = get_handler(
            GenerationType.CHAT, mock_db, user_id="u1",
        )
        ctx = handler.request_ctx
        assert ctx.now is not None
        assert ctx.today is not None
        assert ctx.today.weekday_cn in (
            "周一", "周二", "周三", "周四", "周五", "周六", "周日",
        )


class TestHandlerFactoryRegister:
    """HandlerFactory.register() 自定义 Handler"""

    def test_register_and_get_custom_handler(self, mock_db):
        """注册自定义类型后可通过 factory 获取"""
        from services.handlers.base import BaseHandler

        class CustomHandler(BaseHandler):
            @property
            def handler_type(self):
                return "custom"
            async def start(self, *a, **kw): pass
            async def on_complete(self, *a, **kw): pass
            async def on_error(self, *a, **kw): pass
            def _convert_content_parts_to_dicts(self, *a, **kw): pass
            def _handle_credits_on_complete(self, *a, **kw): pass
            def _handle_credits_on_error(self, *a, **kw): pass

        # 使用一个不会和现有类型冲突的 key
        custom_type = "custom_test_type"
        HandlerFactory.register(custom_type, CustomHandler)

        try:
            handler = HandlerFactory.get(custom_type, mock_db, user_id="u1")
            assert isinstance(handler, CustomHandler)
            assert handler.request_ctx is not None
        finally:
            # 清理：避免污染其他测试
            HandlerFactory._handlers.pop(custom_type, None)
