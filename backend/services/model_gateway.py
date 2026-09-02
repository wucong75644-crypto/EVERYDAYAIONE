"""进程内 Chat ModelGateway。

Gateway 是主 Chat、Actor Chat 和企微兼容入口共享的模型边界。它复用现有
模型注册表与 adapter factory，只负责打开一次模型会话、转发 StreamChunk
以及关闭 Provider adapter；工具编排、消息持久化和通道协议仍由上层负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

if TYPE_CHECKING:
    from services.adapters.base import StreamChunk


@dataclass(frozen=True)
class ModelCallRequest:
    """一次模型会话的稳定请求边界。

    request_id、timeout、cancel_token 和 retry_policy 目前只作为扩展点
    保留，不在 T1 引入新的取消、超时或重试语义。
    """

    model_id: str
    org_id: str | None = None
    db: Any = None
    request_id: str | None = None
    timeout: float | None = None
    cancel_token: Any = None
    retry_policy: Any = None


class ModelGatewaySession:
    """由 ModelGateway 管理生命周期的单次 Chat adapter 会话。"""

    def __init__(self, adapter: Any, request: ModelCallRequest) -> None:
        self._adapter = adapter
        self.request = request
        self._closed = False

    @property
    def model_id(self) -> str:
        return self.request.model_id

    @property
    def adapter_name(self) -> str:
        return type(self._adapter).__name__

    @property
    def supports_google_search(self) -> bool:
        return bool(getattr(self._adapter, "supports_google_search", False))

    def create_google_search_tool(self) -> dict[str, Any]:
        return self._adapter.create_google_search_tool()

    def estimate_cost_unified(self, input_tokens: int, output_tokens: int) -> Any:
        return self._adapter.estimate_cost_unified(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        reasoning_effort: str | None = None,
        thinking_mode: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """转发现有 StreamChunk，不改变 Provider 异常或输出语义。"""
        if self._closed:
            raise RuntimeError("MODEL_GATEWAY_SESSION_CLOSED")
        async for chunk in self._adapter.stream_chat(
            messages=messages,
            reasoning_effort=reasoning_effort,
            thinking_mode=thinking_mode,
            **kwargs,
        ):
            yield chunk

    async def close(self) -> None:
        """幂等关闭 Provider adapter，兼容异常与取消收尾。"""
        if self._closed:
            return
        self._closed = True
        await self._adapter.close()


class ModelGateway:
    """永久进程内的模型调用门面。"""

    def __init__(
        self,
        adapter_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory

    def open_chat(self, request: ModelCallRequest) -> ModelGatewaySession:
        """按现有工厂选择模型并创建一个可复用的 Chat 会话。"""
        if self._adapter_factory is None:
            # 运行时读取模块属性，保留现有测试和配置注入对 factory
            # 的替换能力。
            from services.adapters import factory

            adapter_factory = factory.create_chat_adapter
        else:
            adapter_factory = self._adapter_factory

        factory_kwargs: dict[str, Any] = {
            "org_id": request.org_id,
            "db": request.db,
        }
        if request.timeout is not None:
            factory_kwargs["stream_timeout"] = request.timeout
        adapter = adapter_factory(request.model_id, **factory_kwargs)
        return ModelGatewaySession(adapter, request)


_MODEL_GATEWAY = ModelGateway()


def get_model_gateway() -> ModelGateway:
    """返回应用进程内唯一的 Chat ModelGateway。"""
    return _MODEL_GATEWAY


__all__ = [
    "ModelCallRequest",
    "ModelGateway",
    "ModelGatewaySession",
    "get_model_gateway",
]
