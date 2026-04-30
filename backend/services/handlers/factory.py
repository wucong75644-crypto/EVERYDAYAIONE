"""
Handler 工厂

根据生成类型返回对应的 Handler 实例。
工厂负责注入公共上下文（org_id / request_ctx），
调用方不再需要手动设置属性——消灭散落注入。
"""

from typing import Dict, Optional, Type

from schemas.message import GenerationType
from services.handlers.base import BaseHandler
from services.handlers.chat_handler import ChatHandler
from services.handlers.image_handler import ImageHandler
from services.handlers.video_handler import VideoHandler


class HandlerFactory:
    """Handler 工厂类"""

    _handlers: Dict[GenerationType, Type[BaseHandler]] = {
        GenerationType.CHAT: ChatHandler,
        GenerationType.IMAGE: ImageHandler,
        GenerationType.VIDEO: VideoHandler,
    }

    @classmethod
    def get(
        cls,
        gen_type: GenerationType,
        db,
        *,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: str = "",
    ) -> BaseHandler:
        """获取 Handler 实例并注入公共上下文。

        Args:
            gen_type: 生成类型
            db: 数据库客户端
            org_id: 企业 ID
            user_id: 用户 ID（用于构造 RequestContext）
            request_id: 请求追踪 ID

        Returns:
            已注入 org_id + request_ctx 的 Handler 实例
        """
        handler_cls = cls._handlers.get(gen_type)
        if not handler_cls:
            raise ValueError(f"Unsupported generation type: {gen_type}")

        handler = handler_cls(db)
        handler.org_id = org_id

        # 时间事实层：工厂统一构造 RequestContext，全链路不可变 SSOT
        if user_id:
            from utils.time_context import RequestContext
            handler.request_ctx = RequestContext.build(
                user_id=user_id,
                org_id=org_id,
                request_id=request_id,
            )

        return handler

    @classmethod
    def register(cls, gen_type: GenerationType, handler_cls: Type[BaseHandler]) -> None:
        """注册自定义 Handler"""
        cls._handlers[gen_type] = handler_cls


def get_handler(
    gen_type: GenerationType,
    db,
    *,
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: str = "",
) -> BaseHandler:
    """便捷函数：获取已注入上下文的 Handler 实例。"""
    return HandlerFactory.get(
        gen_type, db,
        org_id=org_id, user_id=user_id, request_id=request_id,
    )
