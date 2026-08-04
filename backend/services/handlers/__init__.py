"""统一消息处理器模块，公共导出按需加载。"""

from importlib import import_module


_EXPORTS = {
    "BaseHandler": ("services.handlers.base", "BaseHandler"),
    "ChatHandler": ("services.handlers.chat_handler", "ChatHandler"),
    "ImageHandler": ("services.handlers.image_handler", "ImageHandler"),
    "VideoHandler": ("services.handlers.video_handler", "VideoHandler"),
    "get_handler": ("services.handlers.factory", "get_handler"),
    "HandlerFactory": ("services.handlers.factory", "HandlerFactory"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = [
    "BaseHandler",
    "ChatHandler",
    "ImageHandler",
    "VideoHandler",
    "get_handler",
    "HandlerFactory",
]
