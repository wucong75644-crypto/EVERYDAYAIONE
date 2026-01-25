"""
KIE Chat 模型适配器

适配 Gemini 3 Pro / Gemini 3 Flash (OpenAI 兼容格式)
"""

from typing import List, Optional, Dict, Any, AsyncIterator, Union
from decimal import Decimal

from loguru import logger

from .client import KieClient, KieAPIError
from .models import (
    ChatCompletionRequest,
    ChatCompletionChunk,
    ChatMessage,
    ChatContentPart,
    MessageRole,
    ReasoningEffort,
    ThinkingMode,
    ToolDefinition,
    FunctionDefinition,
    ResponseFormat,
    JsonSchema,
    TokenUsage,
    CostEstimate,
    UsageRecord,
    KieModelType,
)


class KieChatAdapter:
    """
    KIE Chat 模型适配器

    支持模型:
    - gemini-3-pro: 高级推理模型，支持 Google Search、函数调用、结构化输出
    - gemini-3-flash: 快速推理模型，支持函数调用

    特性:
    - 支持流式/非流式输出
    - 支持多模态输入 (文本、图像、视频、音频、PDF)
    - 支持思考过程显示 (include_thoughts)
    - 支持推理力度控制 (reasoning_effort)
    """

    # 模型配置
    MODEL_CONFIGS = {
        "gemini-3-pro": {
            "context_window": 1_000_000,
            "max_output_tokens": 65536,
            "supports_vision": True,
            "supports_google_search": True,
            "supports_function_calling": True,
            "supports_response_format": True,
            "cost_per_1k_input": Decimal("0.0005"),   # $0.50 / 1M
            "cost_per_1k_output": Decimal("0.0035"),  # $3.50 / 1M
            "credits_per_1k_input": 1,   # 1 积分 / 1K input
            "credits_per_1k_output": 7,  # 7 积分 / 1K output
        },
        "gemini-3-flash": {
            "context_window": 1_000_000,
            "max_output_tokens": 65536,
            "supports_vision": True,
            "supports_google_search": False,
            "supports_function_calling": True,
            "supports_response_format": False,
            "cost_per_1k_input": Decimal("0.00015"),   # $0.15 / 1M
            "cost_per_1k_output": Decimal("0.0009"),   # $0.90 / 1M
            "credits_per_1k_input": Decimal("0.3"),    # 0.3 积分 / 1K input
            "credits_per_1k_output": Decimal("1.8"),   # 1.8 积分 / 1K output
        },
    }

    def __init__(self, client: KieClient, model: str):
        """
        初始化适配器

        Args:
            client: KIE HTTP 客户端
            model: 模型名称
        """
        if model not in self.MODEL_CONFIGS:
            raise ValueError(f"Unsupported model: {model}")

        self.client = client
        self.model = model
        self.config = self.MODEL_CONFIGS[model]

    @property
    def model_type(self) -> KieModelType:
        return KieModelType.CHAT

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return self.config["supports_vision"]

    @property
    def supports_google_search(self) -> bool:
        return self.config["supports_google_search"]

    @property
    def supports_function_calling(self) -> bool:
        return self.config["supports_function_calling"]

    @property
    def supports_response_format(self) -> bool:
        return self.config["supports_response_format"]

    # ============================================================
    # 消息格式化
    # ============================================================

    def format_text_message(self, role: MessageRole, text: str) -> ChatMessage:
        """创建纯文本消息"""
        return ChatMessage(role=role, content=text)

    def format_multimodal_message(
        self,
        role: MessageRole,
        text: str,
        media_urls: List[str],
    ) -> ChatMessage:
        """
        创建多模态消息

        Args:
            role: 消息角色
            text: 文本内容
            media_urls: 媒体文件 URL 列表 (图片/视频/音频/PDF)

        Returns:
            格式化的消息
        """
        content_parts: List[ChatContentPart] = []

        # 添加文本部分
        if text:
            content_parts.append(ChatContentPart(type="text", text=text))

        # 添加媒体文件 (统一使用 image_url 格式)
        for url in media_urls:
            content_parts.append(
                ChatContentPart(
                    type="image_url",
                    image_url={"url": url},
                )
            )

        return ChatMessage(role=role, content=content_parts)

    def format_messages_from_history(
        self,
        history: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
    ) -> List[ChatMessage]:
        """
        从对话历史格式化消息列表

        Args:
            history: 对话历史 [{"role": "user/assistant", "content": "...", "attachments": [...]}]
            system_prompt: 系统提示 (可选)

        Returns:
            格式化的消息列表
        """
        messages: List[ChatMessage] = []

        # 添加系统提示 (使用 developer 角色)
        if system_prompt:
            messages.append(
                ChatMessage(role=MessageRole.DEVELOPER, content=system_prompt)
            )

        # 转换历史消息
        for msg in history:
            role = MessageRole(msg["role"])
            content = msg.get("content", "")
            attachments = msg.get("attachments", [])

            if attachments:
                # 提取媒体 URL
                media_urls = [
                    att.get("url") or att.get("data")
                    for att in attachments
                    if att.get("type") in ("image", "video", "audio", "file")
                ]
                messages.append(
                    self.format_multimodal_message(role, content, media_urls)
                )
            else:
                messages.append(self.format_text_message(role, content))

        return messages

    # ============================================================
    # 工具和响应格式
    # ============================================================

    def create_google_search_tool(self) -> ToolDefinition:
        """创建 Google Search 工具 (仅 gemini-3-pro)"""
        if not self.supports_google_search:
            raise ValueError(f"Model {self.model} does not support Google Search")

        return ToolDefinition(
            type="function",
            function=FunctionDefinition(name="googleSearch"),
        )

    def create_function_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
    ) -> ToolDefinition:
        """
        创建自定义函数工具

        Args:
            name: 函数名
            description: 函数描述
            parameters: 参数 JSON Schema

        Returns:
            工具定义
        """
        return ToolDefinition(
            type="function",
            function=FunctionDefinition(
                name=name,
                description=description,
                parameters=parameters,
            ),
        )

    def create_response_format(
        self,
        schema: Dict[str, Any],
        name: str = "structured_output",
    ) -> ResponseFormat:
        """
        创建结构化输出格式 (仅 gemini-3-pro)

        Args:
            schema: JSON Schema
            name: Schema 名称

        Returns:
            响应格式定义
        """
        if not self.supports_response_format:
            raise ValueError(f"Model {self.model} does not support response_format")

        return ResponseFormat(
            type="json_schema",
            json_schema=JsonSchema(name=name, strict=True, schema=schema),
        )

    # ============================================================
    # API 调用
    # ============================================================

    async def chat(
        self,
        messages: List[ChatMessage],
        stream: bool = True,
        include_thoughts: bool = True,
        reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH,
        thinking_mode: Optional[ThinkingMode] = None,
        tools: Optional[List[ToolDefinition]] = None,
        response_format: Optional[ResponseFormat] = None,
    ) -> Union[ChatCompletionChunk, AsyncIterator[ChatCompletionChunk]]:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            stream: 是否流式输出
            include_thoughts: 是否包含思考过程
            reasoning_effort: 推理力度
            thinking_mode: 推理模式（Deep Think 等）
            tools: 工具列表
            response_format: 响应格式 (与 tools 互斥)

        Returns:
            流式响应迭代器 或 完整响应
        """
        # 验证互斥参数
        if tools and response_format:
            raise ValueError("tools and response_format are mutually exclusive")

        request = ChatCompletionRequest(
            messages=messages,
            stream=stream,
            include_thoughts=include_thoughts,
            reasoning_effort=reasoning_effort,
            thinking_mode=thinking_mode,
            tools=tools,
            response_format=response_format,
        )

        try:
            if stream:
                return self.client.chat_completions_stream(self.model, request)
            else:
                return await self.client.chat_completions(self.model, request)
        except (KieAPIError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Chat request failed: model={self.model}, stream={stream}, error={e}")
            raise KieAPIError(f"Chat request failed: {e}") from e

    async def chat_simple(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        stream: bool = True,
        include_thoughts: bool = False,
        reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH,
        thinking_mode: Optional[ThinkingMode] = None,
    ) -> Union[ChatCompletionChunk, AsyncIterator[ChatCompletionChunk]]:
        """
        简化的聊天接口

        Args:
            user_message: 用户消息
            system_prompt: 系统提示
            history: 对话历史
            stream: 是否流式
            include_thoughts: 是否包含思考过程
            reasoning_effort: 推理力度
            thinking_mode: 推理模式（Deep Think 等）

        Returns:
            响应
        """
        try:
            messages = self.format_messages_from_history(
                history or [],
                system_prompt=system_prompt,
            )

            # 添加当前用户消息
            messages.append(self.format_text_message(MessageRole.USER, user_message))

            return await self.chat(
                messages=messages,
                stream=stream,
                include_thoughts=include_thoughts,
                reasoning_effort=reasoning_effort,
                thinking_mode=thinking_mode,
            )
        except (KieAPIError, ValueError):
            raise
        except Exception as e:
            logger.error(
                f"Chat simple failed: model={self.model}, "
                f"message_preview={user_message[:50]}..., error={e}"
            )
            raise KieAPIError(f"Chat simple failed: {e}") from e

    # ============================================================
    # 成本计算
    # ============================================================

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> CostEstimate:
        """
        估算成本

        Args:
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数

        Returns:
            成本估算
        """
        input_cost = (
            Decimal(input_tokens) / 1000 * self.config["cost_per_1k_input"]
        )
        output_cost = (
            Decimal(output_tokens) / 1000 * self.config["cost_per_1k_output"]
        )
        total_cost = input_cost + output_cost

        input_credits = (
            Decimal(input_tokens) / 1000 * self.config["credits_per_1k_input"]
        )
        output_credits = (
            Decimal(output_tokens) / 1000 * self.config["credits_per_1k_output"]
        )
        total_credits = int((input_credits + output_credits).to_integral_value())

        return CostEstimate(
            model=self.model,
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

    def calculate_usage(self, usage: TokenUsage) -> UsageRecord:
        """
        计算实际使用量

        Args:
            usage: Token 使用统计

        Returns:
            使用记录
        """
        estimate = self.estimate_cost(usage.prompt_tokens, usage.completion_tokens)

        return UsageRecord(
            model=self.model,
            model_type=KieModelType.CHAT,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_usd=estimate.estimated_cost_usd,
            credits_consumed=estimate.estimated_credits,
        )


# ============================================================
# 便捷函数
# ============================================================

async def create_chat_adapter(
    api_key: str,
    model: str = "gemini-3-flash",
) -> KieChatAdapter:
    """
    创建 Chat 适配器

    Args:
        api_key: KIE API 密钥
        model: 模型名称

    Returns:
        Chat 适配器实例
    """
    try:
        client = KieClient(api_key)
        return KieChatAdapter(client, model)
    except Exception as e:
        logger.error(f"Create chat adapter failed: model={model}, error={e}")
        raise
