"""
DashScope Chat 适配器

通过阿里云百炼 OpenAI 兼容接口调用第三方模型。
支持：DeepSeek V3.2/R1、Qwen3.8-Max/Flash、Kimi-K2.5、GLM-5。
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from loguru import logger

from .responses import BUILTIN_TOOLS, CACHE_MODELS, ResponseStream, chat_messages, response_input, response_tools, stable_tools, usage_fields

from ..base import (
    BaseChatAdapter,
    ChatResponse,
    CostEstimate as BaseCostEstimate,
    ModelProvider,
    StreamChunk,
    ToolCallDelta,
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
    "qwen3.8-max": DashScopeModelPricing(credits_per_1m_input=170, credits_per_1m_output=510),
    "qwen3.8-flash": DashScopeModelPricing(credits_per_1m_input=12, credits_per_1m_output=39),
    "kimi-k2.5": DashScopeModelPricing(credits_per_1m_input=57, credits_per_1m_output=295),
    "glm-5": DashScopeModelPricing(credits_per_1m_input=57, credits_per_1m_output=253),
}

# 默认超时（秒）— 当工厂未传入 stream_timeout 时的兜底值
_DEFAULT_STREAM_TIMEOUT = 120.0
CONNECT_TIMEOUT = 15.0
_QWEN_TOOL_XML_TAG_RE = re.compile(r"</?(?:parameter|function|invoke)\b", re.IGNORECASE)


def _is_qwen_tool_xml_content(content: Optional[str]) -> bool:
    """DashScope/Qwen may leak internal tool-call XML via delta.content."""
    return bool(content and _QWEN_TOOL_XML_TAG_RE.search(content))


class DashScopeChatAdapter(BaseChatAdapter):
    """
    DashScope Chat 适配器

    通过 OpenAI 兼容 API 调用百炼平台上的模型。
    复用同一客户端，支持 Chat Completions 与 Responses 内置工具协议。
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        stream_timeout: Optional[float] = None,
        builtin_tools_enabled: bool = True,
        session_cache_enabled: bool = True,
    ):
        from config.model_aliases import canonical_model_id
        super().__init__(canonical_model_id(model))
        self._builtin_tools_enabled = builtin_tools_enabled
        self._session_cache_enabled = session_cache_enabled
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

    @property
    def supports_builtin_search(self) -> bool:
        return self._builtin_tools_enabled and bool(BUILTIN_TOOLS.get(self._model_id))

    @property
    def effective_context_window(self) -> int | None:
        # Responses reserves approximately 20% for tool execution and generation.
        return 800_000 if self.supports_builtin_search and self._model_id in CACHE_MODELS else None

    async def _stream_responses(self, messages, thinking_mode, reasoning_effort, **kwargs):
        builtins = BUILTIN_TOOLS[self._model_id]
        # web_extractor is rejected by Bailian in non-thinking mode.
        effort = reasoning_effort or ("medium" if thinking_mode in ("enabled", "deep_think") else "none")
        thinking = effort != "none"
        if not thinking:
            builtins = tuple(name for name in builtins if name != "web_extractor")
        request = {
            "model": self._model_id, "input": response_input(messages, self._model_id),
            "stream": True, "store": False,
            "tools": response_tools(kwargs.get("tools") or [], builtins),
        }
        if self._model_id == "kimi-k3":
            request["enable_thinking"] = True
        else:
            request["reasoning"] = {"effort": effort}
        if kwargs.get("temperature") is not None:
            request["temperature"] = kwargs["temperature"]
        if kwargs.get("max_tokens") is not None:
            request["max_output_tokens"] = kwargs["max_tokens"]
        headers = {"x-dashscope-session-cache": "enable" if self._session_cache_enabled else "disable"}
        parser = ResponseStream(self._model_id)
        client = await self._get_client()
        try:
            async with client.stream("POST", "/responses", json=request, headers=headers) as response:
                if response.status_code != 200:
                    raise DashScopeAPIError.from_http(await response.aread(), response.status_code,
                        getattr(response, "headers", {}).get("x-request-id"))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    for chunk in parser.consume(json.loads(data)):
                        if chunk.prompt_tokens or chunk.cache_creation_input_tokens:
                            logger.info("LLM cache | model={} | protocol=responses | prompt={} cached={} created={}",
                                        self._model_id, chunk.prompt_tokens, chunk.cached_tokens, chunk.cache_creation_input_tokens)
                        yield chunk
                if not parser.finished:
                    raise DashScopeAPIError("Responses stream ended without a terminal response")
        except DashScopeAPIError:
            raise
        except httpx.TimeoutException as error:
            raise DashScopeAPIError(f"Request timeout: {error}") from error
        except Exception as error:
            raise DashScopeAPIError(f"Responses stream failed: {error}") from error

    # ============================================================
    # 统一接口实现
    # ============================================================

    def _request_body(self, messages, *, stream, reasoning_effort, thinking_mode, **kwargs):
        body = {"model": self._model_id, "messages": chat_messages(messages, cache=self._session_cache_enabled if self._model_id in CACHE_MODELS else None), "stream": stream,
                "enable_thinking": self._model_id == "kimi-k3" or thinking_mode in ("enabled", "deep_think")}
        if stream:
            body["stream_options"] = {"include_usage": True}
        if kwargs.get("tools"):
            body["tools"] = stable_tools(kwargs["tools"]) if self._model_id in CACHE_MODELS else kwargs["tools"]
        if kwargs.get("temperature") is not None:
            body["temperature"] = kwargs["temperature"]
        if self._model_id == "kimi-k3":
            if reasoning_effort is not None:
                if reasoning_effort not in {"low", "high", "max"}:
                    raise ValueError("KIMI_REASONING_EFFORT_INVALID")
                body["reasoning_effort"] = reasoning_effort
            if kwargs.get("enable_search"):
                raise ValueError("KIMI_NATIVE_SEARCH_UNSUPPORTED")
            response_format = kwargs.get("response_format")
            if response_format is not None:
                if response_format != {"type": "json_object"}:
                    raise ValueError("KIMI_RESPONSE_FORMAT_UNSUPPORTED")
                body["response_format"] = response_format
        return body

    def _transport_options(self, body):
        if self._model_id == "kimi-k3" and any(
                isinstance(message.get("content"), list) and any(
                    isinstance(part, dict) and part.get("type") == "image_url"
                    and str((part.get("image_url") or {}).get("url", "")).startswith("data:image/")
                    for part in message["content"]) for message in body["messages"]):
            # The Gateway still owns the absolute deadline; allow its remaining
            # time to upload a bounded inline body instead of an unrelated 30s cap.
            return {"timeout": httpx.Timeout(connect=CONNECT_TIMEOUT, read=self._stream_timeout,
                write=max(30, min(self._stream_timeout, 120)), pool=30)}
        return {}

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """流式聊天（统一接口）"""

        # Agent requests alone opt into provider-managed network tools.
        if kwargs.get("enable_builtin_tools", False) and self.supports_builtin_search:
            async for chunk in self._stream_responses(messages, thinking_mode, reasoning_effort, **kwargs):
                yield chunk
            return
        tools = kwargs.get("tools")
        request_body = self._request_body(messages, stream=True, reasoning_effort=reasoning_effort,
            thinking_mode=thinking_mode, **kwargs)

        client = await self._get_client()

        try:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=request_body,
                **self._transport_options(request_body),
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise DashScopeAPIError.from_http(
                        error_body, response.status_code,
                        getattr(response, "headers", {}).get("x-request-id"),
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
                            status_code=chunk["error"].get("code", 0) if isinstance(chunk["error"].get("code"), int) else 0,
                            error_code=chunk["error"].get("code"), request_id=chunk.get("request_id"),
                        )

                    # 提取内容
                    content = None
                    thinking_content = None
                    finish_reason = None
                    tc_deltas = None
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        thinking_content = delta.get("reasoning_content")
                        finish_reason = choices[0].get("finish_reason")

                        # 提取 tool_calls 增量
                        raw_tcs = delta.get("tool_calls")
                        if raw_tcs:
                            tc_deltas = [
                                ToolCallDelta(
                                    index=tc.get("index", 0),
                                    id=tc.get("id"),
                                    name=tc.get("function", {}).get("name"),
                                    arguments_delta=tc.get("function", {}).get("arguments"),
                                )
                                for tc in raw_tcs
                            ]

                    # Provider boundary: Qwen sometimes emits internal tool-call XML
                    # in delta.content while tools are enabled. That is protocol data,
                    # not assistant-visible text, so never expose it as StreamChunk.content.
                    if tools and _is_qwen_tool_xml_content(content):
                        logger.debug("Suppressed Qwen tool XML from stream content")
                        content = None

                    # 提取 usage（通常在最后一个 chunk，中间 chunk 为 null）
                    usage = chunk.get("usage") or {}
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

                    # V2: 读 prompt cache 命中指标 (千问 OpenAI 兼容字段)
                    # 用于监控 cache 命中率, 验证 cache_control 配置是否生效
                    if prompt_tokens > 0:
                        details = usage.get("prompt_tokens_details") or {}
                        cached_tokens = details.get("cached_tokens", 0)
                        cache_creation = usage_fields(usage, responses=False)["cache_creation_input_tokens"]
                        if cached_tokens > 0 or cache_creation > 0:
                            hit_rate = cached_tokens / prompt_tokens if prompt_tokens else 0
                            logger.info(
                                f"LLM cache | model={self._model_id} | "
                                f"prompt={prompt_tokens} cached={cached_tokens} "
                                f"created={cache_creation} hit_rate={hit_rate:.1%}"
                            )

                    cache_usage = usage_fields(usage, responses=False)
                    yield StreamChunk(
                        cached_tokens=cache_usage["cached_tokens"],
                        cache_creation_input_tokens=cache_usage["cache_creation_input_tokens"],
                        content=content,
                        thinking_content=thinking_content,
                        finish_reason=finish_reason,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        tool_calls=tc_deltas,
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
        request_body = self._request_body(messages, stream=False, reasoning_effort=reasoning_effort,
            thinking_mode=thinking_mode, **kwargs)

        client = await self._get_client()

        try:
            response = await client.post("/chat/completions", json=request_body, **self._transport_options(request_body))

            if response.status_code != 200:
                raise DashScopeAPIError.from_http(response.content, response.status_code,
                    response.headers.get("x-request-id"))

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
                **{k: v for k, v in usage_fields(usage, responses=False).items() if k != "builtin_tool_usage"},
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
            if not isinstance(data, dict):
                return body.decode("utf-8", errors="replace")[:500]
            detail = data.get("error", data)
            if isinstance(detail, dict):
                message = detail.get("message")
                return str(message if message is not None else detail)
            return str(detail)
        except (json.JSONDecodeError, AttributeError):
            return body.decode("utf-8", errors="replace")[:500]


class DashScopeAPIError(Exception):
    """Keep provider evidence; a stream error never proves pre-inference rejection."""

    _DOWNLOAD_CODES = {"InvalidURL.Timeout", "BadRequest.InputDownloadFailed", "GatewayTimeout.InputDownload"}
    _UNAVAILABLE_CODES = {"ServiceUnavailable", "ModelUnavailable"}
    _QUOTA_CODES = {"Throttling.AllocationQuota", "insufficient_quota", "CommodityNotPurchased"}
    _DOWNLOAD_MESSAGES = {
        "Failed to download multimodal content.",
        "Download the media resource timed out during the data inspection process.",
        "Unable to download the media resource during the data inspection process.",
        "download image failed", "Failed to download input files.", "oss download error.",
    }

    def __init__(self, message: str, status_code: int = 0, *, error_code=None,
                 request_id=None, request_rejected=False, provider_message=""):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = self._identifier(error_code)
        self.request_id = self._identifier(request_id)
        self.request_rejected = request_rejected
        self.provider_message = provider_message
        self.quota_rejection = self.error_code in self._QUOTA_CODES

    @staticmethod
    def _identifier(value):
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) else None

    @classmethod
    def from_http(cls, body, status_code, request_id=None):
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        detail = payload.get("error", payload)
        if not isinstance(detail, dict):
            detail = {}
        code = cls._identifier(detail.get("code"))
        message = DashScopeChatAdapter._parse_error(body)
        rejected = (400 <= status_code < 500 and status_code != 408
                    or code in cls._DOWNLOAD_CODES
                    or status_code == 503 and code in cls._UNAVAILABLE_CODES)
        return cls(f"DashScope API error: {message}", status_code, error_code=code,
            request_id=request_id or payload.get("request_id") or detail.get("request_id"),
            request_rejected=rejected, provider_message=message)

    @property
    def retryable_rejection(self):
        # Official error-code table: URL/media download failures and admission
        # throttling can be replayed. Bad parameters, safety rejection and opaque
        # inference timeouts cannot be turned into free, definite rejections.
        return self.request_rejected and (
            self.status_code == 429 and self.error_code not in self._QUOTA_CODES
            or self.error_code in self._DOWNLOAD_CODES
            or self.status_code == 503 and self.error_code in self._UNAVAILABLE_CODES
            or self.error_code in {"InvalidParameter", "InvalidParameter.DataInspection"}
                and self.provider_message.strip() in self._DOWNLOAD_MESSAGES
            or self.error_code == "invalid_parameter_error"
                and self.provider_message.strip() == "<400> InternalError.Algo.InvalidParameter: Download multimodal file timed out")
