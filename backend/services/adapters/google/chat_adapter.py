"""
Google Gemini 聊天适配器

使用新的 google-genai SDK（GA 状态）实现与 Google 官方 API 的对接。

支持模型:
- gemini-2.5-flash-preview-05-20
- gemini-2.5-pro-preview-05-06

版本: 2.0（使用 google-genai SDK）
"""

import base64
import json
from typing import List, Optional, Dict, Any, AsyncIterator
from decimal import Decimal

import httpx
from loguru import logger

from ..base import (
    BaseChatAdapter,
    ModelProvider,
    StreamChunk,
    ToolCallDelta,
    ChatResponse,
    CostEstimate,
)
from .client import GoogleClient
from .configs import get_model_config
from .models import GoogleAPIError, GoogleContentFilterError


class GoogleChatAdapter(BaseChatAdapter):
    """
    Google Gemini 聊天适配器

    使用新的 google-genai SDK（GA 状态）。

    特性:
    - 支持流式/非流式输出
    - 支持多模态输入 (文本、图像)
    - 免费层（无需积分）
    - 自动图片下载和 base64 编码

    注意:
    - 需要安装 google-genai: pip install google-genai
    - 需要设置 GOOGLE_API_KEY 环境变量
    """

    def __init__(self, model_id: str, api_key: str):
        """
        初始化适配器

        Args:
            model_id: 模型名称 (如 gemini-2.5-flash-preview-05-20)
            api_key: Google API 密钥
        """
        super().__init__(model_id)
        self.api_key = api_key
        self.client = GoogleClient(api_key)
        self.config = get_model_config(model_id)

        logger.info(
            f"GoogleChatAdapter initialized | "
            f"model={model_id} | "
            f"display_name={self.config['display_name']}"
        )

    @property
    def provider(self) -> ModelProvider:
        """返回提供商标识"""
        return ModelProvider.GOOGLE

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式输出"""
        return True

    async def _download_media(self, url: str, max_size_mb: int = 20) -> Optional[str]:
        """
        下载媒体文件并转换为 base64

        Args:
            url: 媒体文件 URL（图片或 PDF）
            max_size_mb: 最大文件大小（MB）

        Returns:
            base64 编码的数据，失败返回 None
        """
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0),
            ) as http_client:
                response = await http_client.get(url)
                response.raise_for_status()

                content_length = len(response.content)
                max_bytes = max_size_mb * 1024 * 1024
                if content_length > max_bytes:
                    logger.warning(
                        f"Media too large, skipping | "
                        f"url={url} | size={content_length / 1024 / 1024:.2f}MB"
                    )
                    return None

                data = base64.b64encode(response.content).decode('utf-8')
                logger.debug(f"Media downloaded | url={url} | size={content_length / 1024:.2f}KB")
                return data

        except Exception as e:
            logger.warning(f"Failed to download media | url={url} | error={str(e)}")
            return None

    def _detect_mime_type(self, url: str, default: str = "image/png") -> str:
        """
        根据 URL 检测 MIME 类型

        Args:
            url: 图片 URL
            default: 默认类型

        Returns:
            MIME 类型字符串
        """
        path = url.lower().split("?")[0]
        suffix_types = (
            ((".jpg", ".jpeg"), "image/jpeg"),
            ((".png",), "image/png"),
            ((".webp",), "image/webp"),
            ((".gif",), "image/gif"),
            ((".pdf",), "application/pdf"),
        )
        for suffixes, mime_type in suffix_types:
            if path.endswith(suffixes):
                return mime_type
        return default

    async def _convert_to_google_format(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        转换消息格式：OpenAI/KIE 格式 → Google 格式

        输入（OpenAI 格式）:
        {
            "role": "user" | "assistant" | "system",
            "content": "text" | [
                {"type": "text", "text": "..."},
                {"type": "image_url", "image_url": {"url": "..."}}
            ]
        }

        输出（Google 格式）:
        {
            "role": "user" | "model",
            "parts": [
                {"text": "..."},
                {"inline_data": {"mime_type": "image/png", "data": "base64..."}}
            ]
        }

        Args:
            messages: OpenAI 格式的消息列表

        Returns:
            Google 格式的消息列表
        """
        google_messages: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            google_role = "model" if role == "assistant" else "user"
            if role == "system":
                google_role = "user"
            parts = await self._convert_google_parts(
                msg.get("content", "")
            )
            if parts:
                google_messages.append(
                    {"role": google_role, "parts": parts}
                )

        logger.debug(
            f"Message format converted | "
            f"input={len(messages)} | output={len(google_messages)}"
        )
        return google_messages

    async def _convert_google_parts(
        self,
        content: Any,
    ) -> List[Dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content}] if content.strip() else []
        if not isinstance(content, list):
            return []
        parts: List[Dict[str, Any]] = []
        for item in content:
            if item.get("type", "text") == "text":
                text = item.get("text", "")
                if text.strip():
                    parts.append({"text": text})
                continue
            media = await self._convert_google_media(item)
            if media is not None:
                parts.append(media)
        return parts

    async def _convert_google_media(
        self,
        item: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if item.get("type") != "image_url":
            return None
        media_url = item.get("image_url", {}).get("url", "")
        if not media_url:
            return None
        mime_type = self._detect_mime_type(media_url)
        max_mb = 50 if mime_type == "application/pdf" else 20
        media_data = await self._download_media(
            media_url,
            max_size_mb=max_mb,
        )
        if media_data is None:
            logger.warning(
                f"Skipped media (download failed) | url={media_url}"
            )
            return None
        return {
            "inline_data": {
                "mime_type": mime_type,
                "data": media_data,
            }
        }

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """
        流式聊天

        Args:
            messages: 消息列表（OpenAI 格式）
            reasoning_effort: 推理力度（Google 暂不支持，忽略）
            thinking_mode: 思考模式（Google 暂不支持，忽略）
            **kwargs: 其他参数（temperature, max_output_tokens 等）

        Yields:
            StreamChunk: 响应块

        Raises:
            GoogleAPIError: API 调用失败
        """
        # 转换消息格式
        google_messages = await self._convert_to_google_format(messages)

        if not google_messages:
            logger.warning("No valid messages to send")
            return

        # 构建配置
        config = {
            "temperature": kwargs.get("temperature", 1.0),
            "top_p": kwargs.get("top_p", 0.95),
            "top_k": kwargs.get("top_k", 40),
            "max_output_tokens": kwargs.get("max_output_tokens", 8192),
        }
        if kwargs.get("response_format") is not None:
            config["response_mime_type"] = "application/json"
        tools = kwargs.get("tools")
        if tools:
            config["tools"] = [{
                "function_declarations": [
                    tool["function"] for tool in tools
                ]
            }]

        logger.info(
            f"Stream chat started | "
            f"model={self._model_id} | "
            f"messages={len(google_messages)} | "
            f"config={config}"
        )

        try:
            # 调用流式 API
            response_stream = self.client.generate_content_stream(
                model=self._model_id,
                contents=google_messages,
                config=config,
            )

            # 逐块处理并返回
            chunk_count = 0
            async for chunk in response_stream:
                chunk_count += 1
                yield _parse_google_stream_chunk(chunk)

            logger.info(f"Stream chat completed | chunks={chunk_count}")

        except GoogleAPIError:
            # 直接抛出自定义异常
            raise
        except Exception as e:
            logger.error(f"Stream chat failed | error={str(e)}", exc_info=True)
            raise GoogleAPIError(f"流式聊天失败: {str(e)}") from e

    async def chat_sync(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        """
        非流式聊天

        Args:
            messages: 消息列表（OpenAI 格式）
            reasoning_effort: 推理力度
            thinking_mode: 思考模式
            **kwargs: 其他参数

        Returns:
            ChatResponse: 完整响应

        Raises:
            GoogleAPIError: API 调用失败
        """
        # 复用 stream_chat 逻辑，累积所有 chunk
        accumulated_text = ""
        final_usage = {"prompt_tokens": 0, "completion_tokens": 0}

        async for chunk in self.stream_chat(messages, reasoning_effort, thinking_mode, **kwargs):
            if chunk.content:
                accumulated_text += chunk.content

            # 捕获最终的 token 使用量
            if chunk.prompt_tokens or chunk.completion_tokens:
                final_usage["prompt_tokens"] = chunk.prompt_tokens
                final_usage["completion_tokens"] = chunk.completion_tokens

        return ChatResponse(
            content=accumulated_text,
            finish_reason="stop",
            prompt_tokens=final_usage["prompt_tokens"],
            completion_tokens=final_usage["completion_tokens"],
        )

    def estimate_cost_unified(
        self,
        input_tokens: int,
        output_tokens: int
    ) -> CostEstimate:
        """
        估算成本（Google 免费层成本为 0）

        Args:
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数

        Returns:
            CostEstimate: 成本估算结果
        """
        # Google 免费层无需付费，但追踪 token 使用
        return CostEstimate(
            model=self._model_id,
            estimated_cost_usd=Decimal("0"),  # 免费
            estimated_credits=0,  # 不消耗积分
            breakdown={
                "provider": "Google (免费层)",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "note": "Google 免费层，无费用",
            }
        )

    async def close(self) -> None:
        """关闭连接，释放资源"""
        await self.client.aclose()
        logger.debug(f"GoogleChatAdapter closed | model={self._model_id}")


def _parse_google_stream_chunk(chunk: Any) -> StreamChunk:
    usage = getattr(chunk, "usage_metadata", None)
    prompt_tokens = (
        getattr(usage, "prompt_token_count", 0) if usage else 0
    )
    cached_tokens = (
        getattr(usage, "cached_content_token_count", 0) or 0
        if usage else 0
    )
    if cached_tokens > 0 and prompt_tokens > 0:
        logger.info(
            f"LLM cache | model=gemini | prompt={prompt_tokens} "
            f"cached={cached_tokens} "
            f"hit_rate={cached_tokens / prompt_tokens:.1%}"
        )
    finish_reason = _google_finish_reason(chunk)
    return StreamChunk(
        content=getattr(chunk, "text", None) or None,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=(
            getattr(usage, "candidates_token_count", 0) if usage else 0
        ),
        reasoning_tokens=(
            getattr(usage, "thoughts_token_count", 0) or 0
            if usage else 0
        ),
        cached_tokens=cached_tokens,
        tool_calls=_google_tool_call_deltas(chunk),
    )


def _google_finish_reason(chunk: Any) -> str | None:
    for candidate in getattr(chunk, "candidates", None) or ():
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        value = str(reason)
        if "SAFETY" in value or "BLOCK" in value:
            logger.warning(f"Content filtered | reason={value}")
            raise GoogleContentFilterError()
        return value
    return None


def _google_tool_call_deltas(
    chunk: Any,
) -> List[ToolCallDelta] | None:
    calls: List[ToolCallDelta] = []
    for candidate in getattr(chunk, "candidates", None) or ():
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or ():
            function = getattr(part, "function_call", None)
            if function is None:
                continue
            calls.append(ToolCallDelta(
                index=len(calls),
                id=getattr(function, "id", None),
                name=getattr(function, "name", None),
                arguments_delta=json.dumps(
                    getattr(function, "args", {}) or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ))
    return calls or None
