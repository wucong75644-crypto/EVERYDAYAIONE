"""
统一消息处理器模块

提供不同类型消息的处理器：
- ChatHandler: 聊天消息（流式）
- ImageHandler: 图片生成（异步任务）
- VideoHandler: 视频生成（异步任务）
"""

from .base import BaseHandler
from .chat_handler import ChatHandler
from .image_handler import ImageHandler
from .video_handler import VideoHandler
from .factory import get_handler, HandlerFactory

__all__ = [
    "BaseHandler",
    "ChatHandler",
    "ImageHandler",
    "VideoHandler",
    "get_handler",
    "HandlerFactory",
]
