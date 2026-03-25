"""
Handler 工厂

根据生成类型返回对应的 Handler 实例。
"""

from typing import Dict, Type



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
    def get(cls, gen_type: GenerationType, db) -> BaseHandler:
        """
        获取 Handler 实例

        Args:
            gen_type: 生成类型
            db: 数据库客户端

        Returns:
            对应类型的 Handler 实例

        Raises:
            ValueError: 不支持的生成类型
        """
        handler_cls = cls._handlers.get(gen_type)
        if not handler_cls:
            raise ValueError(f"Unsupported generation type: {gen_type}")
        return handler_cls(db)

    @classmethod
    def register(cls, gen_type: GenerationType, handler_cls: Type[BaseHandler]) -> None:
        """
        注册自定义 Handler

        Args:
            gen_type: 生成类型
            handler_cls: Handler 类
        """
        cls._handlers[gen_type] = handler_cls


def get_handler(gen_type: GenerationType, db) -> BaseHandler:
    """
    便捷函数：获取 Handler 实例

    Args:
        gen_type: 生成类型
        db: 数据库客户端

    Returns:
        对应类型的 Handler 实例
    """
    return HandlerFactory.get(gen_type, db)
