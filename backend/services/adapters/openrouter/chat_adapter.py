"""
OpenRouter Chat 适配器

通过 OpenRouter 统一网关调用多家 AI 模型。
API 与 OpenAI 完全兼容，额外返回 usage.cost（USD）用于精确计费。

积分公式：ceil(cost_usd × 200) + 1
"""

import json
import math
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from loguru import logger

from ..base import (
    BaseChatAdapter,
    ChatResponse,
    CostEstimate as BaseCostEstimate,
    ModelProvider,
    StreamChunk,
)

# 默认超时（秒）— 当工厂未传入 stream_timeout 时的兜底值
_DEFAULT_STREAM_TIMEOUT = 120.0
CONNECT_TIMEOUT = 15.0

# 积分换算：$50 = 10000 积分 → 1 USD = 200 积分
CREDITS_PER_USD = 200
CREDITS_MARKUP = 1  # 每次调用额外加 1 积分


class OpenRouterChatAdapter(BaseChatAdapter):
    """
    OpenRouter Chat 适配器

    通过 OpenAI 兼容 API 调用 OpenRouter 网关。
    支持 GPT、Claude、Gemini、Grok 等多家模型。
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        app_title: str = "EverydayAI",
        stream_timeout: Optional[float] = None,
    ):
        super().__init__(model)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._app_title = app_title
        self._stream_timeout = stream_timeout or _DEFAULT_STREAM_TIMEOUT
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-Title": self._app_title,
                },
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT,
                    read=self._stream_timeout,
                    write=30.0,
                    pool=30.0,
                ),
            )
        return self._client

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.OPENROUTER

    @property
    def supports_streaming(self) -> bool:
        return True

    # ============================================================
    # 统一接口实现
    # ============================================================

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """流式聊天（统一接口）"""

        request_body: Dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        client = await self._get_client()

        try:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=request_body,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_msg = self._parse_error(error_body)
                    raise OpenRouterAPIError(
                        f"OpenRouter API error: {error_msg}",
                        status_code=response.status_code,
                    )

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    data = line[6:]  # 去掉 "data: "
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # 检查错误
                    if chunk.get("error"):
                        raise OpenRouterAPIError(
                            f"Stream error: {chunk['error'].get('message', str(chunk['error']))}",
                            status_code=chunk["error"].get("code", 500),
                        )

                    # 提取内容
                    content = None
                    thinking_content = None
                    finish_reason = None
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        thinking_content = delta.get("reasoning_content")
                        finish_reason = choices[0].get("finish_reason")

                    # 提取 usage（通常在最后一个 chunk）
                    usage = chunk.get("usage") or {}
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

                    # OpenRouter 独有：直接返回 USD 成本
                    cost_usd = usage.get("cost")
                    credits_consumed = None
                    if cost_usd is not None:
                        credits_consumed = math.ceil(float(cost_usd) * CREDITS_PER_USD) + CREDITS_MARKUP

                    yield StreamChunk(
                        content=content,
                        thinking_content=thinking_content,
                        finish_reason=finish_reason,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        credits_consumed=credits_consumed,
                    )

        except OpenRouterAPIError:
            raise
        except httpx.TimeoutException as e:
            raise OpenRouterAPIError(f"Request timeout: {e}") from e
        except Exception as e:
            logger.error(f"OpenRouter stream error | model={self._model_id} | error={e}")
            raise OpenRouterAPIError(f"Stream failed: {e}") from e

    async def chat_sync(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        """非流式聊天（统一接口）"""
        request_body: Dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": False,
        }

        client = await self._get_client()

        try:
            response = await client.post("/chat/completions", json=request_body)

            if response.status_code != 200:
                error_msg = self._parse_error(response.content)
                raise OpenRouterAPIError(
                    f"OpenRouter API error: {error_msg}",
                    status_code=response.status_code,
                )

            data = response.json()
            choices = data.get("choices", [])
            usage = data.get("usage") or {}

            content = ""
            finish_reason = None
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                finish_reason = choices[0].get("finish_reason")

            return ChatResponse(
                content=content,
                finish_reason=finish_reason,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )

        except OpenRouterAPIError:
            raise
        except Exception as e:
            logger.error(f"OpenRouter sync error | model={self._model_id} | error={e}")
            raise OpenRouterAPIError(f"Sync chat failed: {e}") from e

    def estimate_cost_unified(
        self, input_tokens: int, output_tokens: int,
    ) -> BaseCostEstimate:
        """
        积分消耗估算（兜底方案）

        优先使用 stream_chat 返回的 credits_consumed（来自 usage.cost）。
        此方法仅在 usage.cost 缺失时作为兜底。
        """
        from ..factory import get_model_config

        config = get_model_config(self._model_id)
        if not config:
            return BaseCostEstimate(
                model=self._model_id,
                estimated_cost_usd=Decimal("0"),
                estimated_credits=CREDITS_MARKUP,
            )

        # 用 ModelConfig 的价格估算
        input_cost = Decimal(str(input_tokens)) * Decimal(str(config.input_price)) / 1_000_000
        output_cost = Decimal(str(output_tokens)) * Decimal(str(config.output_price)) / 1_000_000
        total_usd = input_cost + output_cost
        total_credits = math.ceil(float(total_usd) * CREDITS_PER_USD) + CREDITS_MARKUP

        return BaseCostEstimate(
            model=self._model_id,
            estimated_cost_usd=total_usd,
            estimated_credits=max(CREDITS_MARKUP + 1, total_credits),
            breakdown={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_cost_usd": float(input_cost),
                "output_cost_usd": float(output_cost),
                "rate": f"1 USD = {CREDITS_PER_USD} credits + {CREDITS_MARKUP} markup",
            },
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ============================================================
    # 内部工具
    # ============================================================

    @staticmethod
    def _parse_error(body: bytes) -> str:
        """解析错误响应"""
        try:
            data = json.loads(body)
            if "error" in data:
                return data["error"].get("message", str(data["error"]))
            return data.get("message", str(data))
        except (json.JSONDecodeError, AttributeError):
            return body.decode("utf-8", errors="replace")[:500]


class OpenRouterAPIError(Exception):
    """OpenRouter API 错误"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
