"""
Google 官方 Gemini API 聊天适配器

使用 google-generativeai SDK 实现与 Google 官方 API 的对接。

支持模型:
- gemini-2.5-flash-preview-05-20
- gemini-2.5-pro-preview-05-06
"""

from typing import List, Optional, Dict, Any, AsyncIterator
from decimal import Decimal

from loguru import logger

from ..base import (
    BaseChatAdapter,
    ModelProvider,
    StreamChunk,
    ChatResponse,
    CostEstimate,
)

# 模型配置
MODEL_CONFIGS = {
    "gemini-2.5-flash-preview-05-20": {
        "display_name": "Gemini 2.5 Flash",
        "cost_per_1k_input": Decimal("0.00015"),   # $0.15 / 1M
        "cost_per_1k_output": Decimal("0.0006"),   # $0.60 / 1M
        "credits_per_1k_input": Decimal("0.3"),
        "credits_per_1k_output": Decimal("1.2"),
        "max_tokens": 65536,
        "context_window": 1_000_000,
    },
    "gemini-2.5-pro-preview-05-06": {
        "display_name": "Gemini 2.5 Pro",
        "cost_per_1k_input": Decimal("0.00125"),   # $1.25 / 1M
        "cost_per_1k_output": Decimal("0.01"),     # $10.0 / 1M
        "credits_per_1k_input": Decimal("2.5"),
        "credits_per_1k_output": Decimal("20"),
        "max_tokens": 65536,
        "context_window": 1_000_000,
    },
}


class GoogleChatAdapter(BaseChatAdapter):
    """
    Google 官方 Gemini API 聊天适配器

    特性:
    - 支持流式/非流式输出
    - 支持多模态输入 (文本、图像、视频)
    - 支持函数调用

    注意:
    - 需要安装 google-generativeai: pip install google-generativeai
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

        if model_id not in MODEL_CONFIGS:
            logger.warning(f"Unknown model: {model_id}, using default config")
            self.config = MODEL_CONFIGS.get(
                "gemini-2.5-flash-preview-05-20",
                list(MODEL_CONFIGS.values())[0]
            )
        else:
            self.config = MODEL_CONFIGS[model_id]

        self.api_key = api_key
        self._client = None
        self._model = None

    def _ensure_client(self):
        """确保客户端已初始化（延迟加载）"""
        if self._client is None:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._client = genai
                self._model = genai.GenerativeModel(self._model_id)
            except ImportError:
                raise ImportError(
                    "google-generativeai is required for Google adapter. "
                    "Install with: pip install google-generativeai"
                )

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.GOOGLE

    @property
    def supports_streaming(self) -> bool:
        return True

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        将统一消息格式转换为 Google API 格式

        统一格式: [{"role": "user", "content": "..."}]
        Google 格式: [{"role": "user", "parts": [{"text": "..."}]}]
        """
        google_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Google API 使用 "model" 而不是 "assistant"
            if role == "assistant":
                role = "model"
            elif role == "system":
                # Google API 将 system prompt 作为第一条 user 消息
                role = "user"

            # 处理内容
            if isinstance(content, str):
                parts = [{"text": content}]
            elif isinstance(content, list):
                # 多模态内容
                parts = []
                for item in content:
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        # 需要转换图片 URL 为 inline_data
                        image_url = item.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:"):
                            # data:image/png;base64,xxx
                            header, data = image_url.split(",", 1)
                            mime_type = header.split(":")[1].split(";")[0]
                            parts.append({
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": data
                                }
                            })
                        else:
                            # HTTP URL - 需要下载或使用 file_data
                            parts.append({
                                "file_data": {
                                    "file_uri": image_url,
                                    "mime_type": "image/jpeg"  # 默认
                                }
                            })
            else:
                parts = [{"text": str(content)}]

            google_messages.append({
                "role": role,
                "parts": parts
            })

        return google_messages

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
            messages: 消息列表
            reasoning_effort: 推理强度 (Google API 暂不支持)
            thinking_mode: 思考模式 (Google API 暂不支持)

        Yields:
            StreamChunk: 统一格式的流式响应块
        """
        self._ensure_client()

        # 转换消息格式
        google_messages = self._convert_messages(messages)

        # 配置生成参数
        generation_config = {
            "max_output_tokens": self.config.get("max_tokens", 8192),
            "temperature": kwargs.get("temperature", 1.0),
        }

        try:
            # 使用 chat 模式
            chat = self._model.start_chat(history=google_messages[:-1] if len(google_messages) > 1 else [])

            # 获取最后一条用户消息
            last_message = google_messages[-1] if google_messages else {"parts": [{"text": ""}]}
            last_content = last_message.get("parts", [{"text": ""}])

            # 发送消息并获取流式响应
            response = await chat.send_message_async(
                last_content,
                generation_config=generation_config,
                stream=True
            )

            prompt_tokens = 0
            completion_tokens = 0

            async for chunk in response:
                # 提取文本内容
                text = ""
                if hasattr(chunk, "text"):
                    text = chunk.text
                elif hasattr(chunk, "parts"):
                    for part in chunk.parts:
                        if hasattr(part, "text"):
                            text += part.text

                # 获取 usage（如果有）
                if hasattr(chunk, "usage_metadata"):
                    usage = chunk.usage_metadata
                    prompt_tokens = getattr(usage, "prompt_token_count", 0)
                    completion_tokens = getattr(usage, "candidates_token_count", 0)

                # 获取 finish_reason
                finish_reason = None
                if hasattr(chunk, "candidates") and chunk.candidates:
                    candidate = chunk.candidates[0]
                    if hasattr(candidate, "finish_reason"):
                        finish_reason = str(candidate.finish_reason)

                yield StreamChunk(
                    content=text if text else None,
                    finish_reason=finish_reason,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

        except Exception as e:
            logger.error(f"Google API stream_chat error: {e}")
            raise

    async def chat_sync(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        """非流式聊天"""
        self._ensure_client()

        # 转换消息格式
        google_messages = self._convert_messages(messages)

        # 配置生成参数
        generation_config = {
            "max_output_tokens": self.config.get("max_tokens", 8192),
            "temperature": kwargs.get("temperature", 1.0),
        }

        try:
            chat = self._model.start_chat(history=google_messages[:-1] if len(google_messages) > 1 else [])
            last_message = google_messages[-1] if google_messages else {"parts": [{"text": ""}]}
            last_content = last_message.get("parts", [{"text": ""}])

            response = await chat.send_message_async(
                last_content,
                generation_config=generation_config,
                stream=False
            )

            # 提取内容
            content = ""
            if hasattr(response, "text"):
                content = response.text
            elif hasattr(response, "parts"):
                for part in response.parts:
                    if hasattr(part, "text"):
                        content += part.text

            # 提取 usage
            prompt_tokens = 0
            completion_tokens = 0
            if hasattr(response, "usage_metadata"):
                usage = response.usage_metadata
                prompt_tokens = getattr(usage, "prompt_token_count", 0)
                completion_tokens = getattr(usage, "candidates_token_count", 0)

            # 提取 finish_reason
            finish_reason = None
            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, "finish_reason"):
                    finish_reason = str(candidate.finish_reason)

            return ChatResponse(
                content=content,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        except Exception as e:
            logger.error(f"Google API chat_sync error: {e}")
            raise

    def estimate_cost_unified(self, input_tokens: int, output_tokens: int) -> CostEstimate:
        """估算成本"""
        input_cost = Decimal(input_tokens) / 1000 * self.config["cost_per_1k_input"]
        output_cost = Decimal(output_tokens) / 1000 * self.config["cost_per_1k_output"]
        total_cost = input_cost + output_cost

        input_credits = Decimal(input_tokens) / 1000 * self.config["credits_per_1k_input"]
        output_credits = Decimal(output_tokens) / 1000 * self.config["credits_per_1k_output"]
        total_credits = int((input_credits + output_credits).to_integral_value())

        return CostEstimate(
            model=self._model_id,
            estimated_cost_usd=total_cost,
            estimated_credits=total_credits,
            breakdown={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_cost_usd": float(input_cost),
                "output_cost_usd": float(output_cost),
                "input_credits": float(input_credits),
                "output_credits": float(output_credits),
            },
        )

    async def close(self) -> None:
        """关闭连接（Google SDK 无需显式关闭）"""
        self._client = None
        self._model = None
