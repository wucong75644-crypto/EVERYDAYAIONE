"""
DashScope Chat 适配器

通过阿里云百炼 OpenAI 兼容接口调用第三方模型。
支持：DeepSeek V3.2/R1、Qwen3.5-Plus、Kimi-K2.5、GLM-5。
"""

import json
from dataclasses import dataclass
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


# ============================================================
# 模型定价配置（积分/百万 token）
# ============================================================

@dataclass
class DashScopeModelPricing:
    """单个模型的积分定价"""
    credits_per_1m_input: int
    credits_per_1m_output: int


DASHSCOPE_PRICING: Dict[str, DashScopeModelPricing] = {
    "deepseek-v3.2": DashScopeModelPricing(credits_per_1m_input=29, credits_per_1m_output=113),
    "deepseek-r1": DashScopeModelPricing(credits_per_1m_input=57, credits_per_1m_output=225),
    "qwen3.5-plus": DashScopeModelPricing(credits_per_1m_input=12, credits_per_1m_output=68),
    "kimi-k2.5": DashScopeModelPricing(credits_per_1m_input=57, credits_per_1m_output=295),
    "glm-5": DashScopeModelPricing(credits_per_1m_input=57, credits_per_1m_output=253),
}

# 默认超时（秒）— 当工厂未传入 stream_timeout 时的兜底值
_DEFAULT_STREAM_TIMEOUT = 120.0
CONNECT_TIMEOUT = 15.0


class DashScopeChatAdapter(BaseChatAdapter):
    """
    DashScope Chat 适配器

    通过 OpenAI 兼容 API 调用百炼平台上的模型。
    API 格式与 OpenAI 完全一致，仅 base_url 和 api_key 不同。
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        stream_timeout: Optional[float] = None,
    ):
        super().__init__(model)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
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
        return ModelProvider.DASHSCOPE

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

        # 构建请求体（OpenAI 兼容格式）
        request_body: Dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # 思考模式支持（DeepSeek/Qwen/GLM 通用参数）
        if thinking_mode == "enabled":
            request_body["enable_thinking"] = True
        elif thinking_mode == "disabled":
            request_body["enable_thinking"] = False

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
                    raise DashScopeAPIError(
                        f"DashScope API error: {error_msg}",
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
                        raise DashScopeAPIError(
                            f"Stream error: {chunk['error'].get('message', str(chunk['error']))}",
                            status_code=chunk["error"].get("code", 500),
                        )

                    # 提取内容
                    content = None
                    finish_reason = None
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        finish_reason = choices[0].get("finish_reason")

                    # 提取 usage（通常在最后一个 chunk，中间 chunk 为 null）
                    usage = chunk.get("usage") or {}
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

                    yield StreamChunk(
                        content=content,
                        finish_reason=finish_reason,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )

        except DashScopeAPIError:
            raise
        except httpx.TimeoutException as e:
            raise DashScopeAPIError(f"Request timeout: {e}") from e
        except Exception as e:
            logger.error(f"DashScope stream error | model={self._model_id} | error={e}")
            raise DashScopeAPIError(f"Stream failed: {e}") from e

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

        if thinking_mode == "enabled":
            request_body["enable_thinking"] = True

        client = await self._get_client()

        try:
            response = await client.post("/chat/completions", json=request_body)

            if response.status_code != 200:
                error_msg = self._parse_error(response.content)
                raise DashScopeAPIError(
                    f"DashScope API error: {error_msg}",
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

        except DashScopeAPIError:
            raise
        except Exception as e:
            logger.error(f"DashScope sync error | model={self._model_id} | error={e}")
            raise DashScopeAPIError(f"Sync chat failed: {e}") from e

    def estimate_cost_unified(
        self, input_tokens: int, output_tokens: int,
    ) -> BaseCostEstimate:
        """积分消耗估算"""
        pricing = DASHSCOPE_PRICING.get(self._model_id)
        if not pricing:
            return BaseCostEstimate(
                model=self._model_id,
                estimated_cost_usd=Decimal("0"),
                estimated_credits=1,
            )

        input_credits = int(
            Decimal(input_tokens) * pricing.credits_per_1m_input / 1_000_000
        )
        output_credits = int(
            Decimal(output_tokens) * pricing.credits_per_1m_output / 1_000_000
        )
        total = input_credits + output_credits

        return BaseCostEstimate(
            model=self._model_id,
            estimated_cost_usd=Decimal(str(total)) / 100,
            estimated_credits=max(1, total) if total > 0 else 0,
            breakdown={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_credits": input_credits,
                "output_credits": output_credits,
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


class DashScopeAPIError(Exception):
    """DashScope API 错误"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
