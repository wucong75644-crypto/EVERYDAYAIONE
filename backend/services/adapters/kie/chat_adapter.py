"""
KIE Chat 模型适配器

适配 Gemini 3 Pro / Gemini 3 Flash (OpenAI 兼容格式)

继承统一基类 BaseChatAdapter，保持现有接口不变。
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
from ..base import (
    BaseChatAdapter,
    ModelProvider,
    ToolCallDelta,
    StreamChunk,
    ChatResponse,
    CostEstimate as BaseCostEstimate,
)
from .configs import CHAT_MODEL_CONFIGS


class KieChatAdapter(BaseChatAdapter):
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

    # 模型配置（从 configs.py 导入）
    MODEL_CONFIGS = CHAT_MODEL_CONFIGS

    def __init__(self, client: KieClient, model: str):
        """
        初始化适配器

        Args:
            client: KIE HTTP 客户端
            model: 模型名称
        """
        super().__init__(model)  # 调用基类初始化

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
        """发送聊天请求（tools 和 response_format 互斥）"""
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
        """简化的聊天接口：自动格式化历史消息并发送"""
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
    # 基类抽象方法实现（统一接口）
    # ============================================================

    @property
    def provider(self) -> ModelProvider:
        """返回提供商标识"""
        return ModelProvider.KIE

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """
        统一格式的流式聊天

        将现有 chat() 方法的输出转换为统一的 StreamChunk 格式
        """
        # 转换消息格式
        formatted_messages = self.format_messages_from_history(messages)

        # 解析参数
        effort = ReasoningEffort(reasoning_effort) if reasoning_effort else ReasoningEffort.HIGH
        mode = ThinkingMode(thinking_mode) if thinking_mode else None

        # 调用现有方法
        stream = await self.chat(
            messages=formatted_messages,
            stream=True,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
            **kwargs,
        )

        # 转换输出格式
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None

            # 提取 tool_calls 增量
            tc_deltas = None
            if delta and delta.tool_calls:
                tc_deltas = [
                    ToolCallDelta(
                        index=tc.index,
                        id=tc.id,
                        name=tc.function.name if tc.function else None,
                        arguments_delta=tc.function.arguments if tc.function else None,
                    )
                    for tc in delta.tool_calls
                ]

            yield StreamChunk(
                content=delta.content if delta else None,
                thinking_content=delta.reasoning_content if delta else None,
                finish_reason=chunk.choices[0].finish_reason if chunk.choices else None,
                prompt_tokens=chunk.usage.prompt_tokens if chunk.usage else 0,
                completion_tokens=chunk.usage.completion_tokens if chunk.usage else 0,
                credits_consumed=chunk.credits_consumed,
                tool_calls=tc_deltas,
            )

    async def chat_sync(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        """非流式聊天（统一接口，避免与现有 chat 方法冲突）"""
        formatted_messages = self.format_messages_from_history(messages)
        effort = ReasoningEffort(reasoning_effort) if reasoning_effort else ReasoningEffort.HIGH
        mode = ThinkingMode(thinking_mode) if thinking_mode else None

        response = await self.chat(
            messages=formatted_messages,
            stream=False,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
            **kwargs,
        )

        content = ""
        if response.choices:
            content = response.choices[0].delta.content or ""

        return ChatResponse(
            content=content,
            finish_reason=response.choices[0].finish_reason if response.choices else None,
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    def estimate_cost_unified(self, input_tokens: int, output_tokens: int) -> BaseCostEstimate:
        """转换现有 estimate_cost 的输出为统一格式"""
        result = self.estimate_cost(input_tokens, output_tokens)
        return BaseCostEstimate(
            model=result.model,
            estimated_cost_usd=result.estimated_cost_usd,
            estimated_credits=result.estimated_credits,
            breakdown=result.breakdown,
        )

    async def close(self) -> None:
        """关闭客户端连接"""
        await self.client.close()
