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
        from ..chat_protocol import chat_messages
        for msg in chat_messages(history):
            role = MessageRole(msg["role"])
            # 注意: dict.get("k", default) 在 key 存在但值为 None 时仍返回 None,需 or 兜底
            content = msg.get("content") or ""
            attachments = msg.get("attachments") or []

            if isinstance(content, list):
                # 结构化 content block（AgentResult.to_message_content()）
                # 所有 block 已统一为 type="text"，直接转 ChatContentPart
                parts = [ChatContentPart(**block) for block in content
                    if isinstance(block, dict) and block.get("type") in {"text", "image_url"}]
                messages.append(ChatMessage(
                    role=role,
                    content=parts if parts else "",
                ))
            elif attachments:
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
        # GPT planners use KIE's Responses endpoint through this
        # existing KIE adapter/client/auth/Gateway path.
        if self.config.get("api_protocol") == "responses":
            async for chunk in self._stream_responses(messages, reasoning_effort, **kwargs):
                yield chunk
            return

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
        if self.config.get("api_protocol") == "responses":
            return await self._responses_sync(messages, reasoning_effort, **kwargs)
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

    @staticmethod
    def _responses_content(messages):
        inputs = []
        for message in messages:
            role = message.get("role")
            if role not in {"system", "developer", "user", "assistant"}:
                raise ValueError("KIE_RESPONSES_ROLE_UNSUPPORTED")
            content = message.get("content")
            parts = []
            for part in ([{"type": "text", "text": content}] if isinstance(content, str) else (content or [])):
                if part.get("type") in {"text", "input_text", "output_text"}:
                    parts.append({"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]})
                elif part.get("type") in {"image_url", "input_image"}:
                    image = part["image_url"]
                    parts.append({"type": "input_image", "image_url": image["url"] if isinstance(image, dict) else image})
                else:
                    raise ValueError("KIE_RESPONSES_CONTENT_UNSUPPORTED")
            inputs.append({"role": role, "content": parts})
        return inputs

    def _responses_body(self, messages, reasoning_effort, stream):
        from core.config import get_settings
        effort = reasoning_effort or get_settings().ecom_image_planning_reasoning
        if effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError("KIE_RESPONSES_REASONING_INVALID")
        return {"model": self.model, "input": self._responses_content(messages),
                "stream": stream, "reasoning": {"effort": effort}}

    @staticmethod
    def _responses_usage(response):
        if response.get("status") != "completed":
            raise KieAPIError("KIE_RESPONSES_INCOMPLETE")
        usage = response.get("usage") or {}
        return usage, response.get("credits_consumed")

    @staticmethod
    def _responses_failure(event):
        import re
        from services.kie_image_fallback_request import safe_error
        response = event.get("response")
        detail = event.get("error") or (response.get("error") if isinstance(response, dict) else None) or event
        code = None
        if isinstance(detail, dict):
            code = detail.get("code")
            message = detail.get("message") or detail.get("msg") or code or event.get("type")
        else:
            message = detail
        # Keep provider diagnostics without treating a stream failure as a
        # definite HTTP rejection or automatically resending a charged request.
        code = str(code) if isinstance(code, (str, int)) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(code)) else None
        return KieAPIError("KIE_RESPONSES_FAILED: " + safe_error(RuntimeError(str(message))), error_code=code)

    async def _stream_responses(self, messages, reasoning_effort, **kwargs):
        import json
        import httpx
        if kwargs.get("tools") or kwargs.get("response_format"):
            raise ValueError("KIE_RESPONSES_PLANNER_OPTIONS_UNSUPPORTED")
        client = await self.client._get_client()
        body = self._responses_body(messages, reasoning_effort, True)
        completed = False
        async with client.stream("POST", "/codex/v1/responses", json=body,
                timeout=httpx.Timeout(connect=5, read=self.client._stream_timeout, write=30, pool=5)) as response:
            if response.status_code != 200:
                raw = await response.aread()
                try:
                    error_body = json.loads(raw)
                except ValueError:
                    error_body = {"msg": "Invalid provider error body"}
                self.client._handle_error_response(response.status_code, error_body, self.model)
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raw = await response.aread()
                try:
                    error_body = json.loads(raw)
                    code = int(error_body.get("code", 0))
                except (ValueError, TypeError, AttributeError):
                    raise KieAPIError("KIE_RESPONSES_STREAM_BODY_INVALID") from None
                # KIE can return HTTP 200 with a business-level 401/402/429.
                # Preserve that classification instead of reporting missing SSE.
                if 400 <= code <= 599:
                    self.client._handle_error_response(code, error_body, self.model)
                raise KieAPIError("KIE_RESPONSES_STREAM_BODY_INVALID")
            event_data = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    event_data.append(line[5:].lstrip())
                    continue
                if line or not event_data:
                    continue
                raw = "\n".join(event_data)
                event_data = []
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except ValueError as error:
                    raise KieAPIError("KIE_RESPONSES_INVALID_EVENT") from error
                if not isinstance(event, dict):
                    raise KieAPIError("KIE_RESPONSES_INVALID_EVENT")
                kind = event.get("type")
                if kind in {"error", "response.failed", "response.incomplete"} or event.get("error"):
                    raise self._responses_failure(event)
                if kind == "response.output_text.delta":
                    yield StreamChunk(content=event.get("delta", ""))
                elif kind == "response.completed":
                    usage, credits = self._responses_usage(event["response"])
                    yield StreamChunk(finish_reason="stop", prompt_tokens=usage.get("input_tokens", 0),
                        completion_tokens=usage.get("output_tokens", 0), credits_consumed=credits)
                    completed = True
            # SSE permits the final event to end at EOF without a blank line.
            if event_data:
                raw = "\n".join(event_data)
                if raw != "[DONE]":
                    try:
                        event = json.loads(raw)
                    except ValueError as error:
                        raise KieAPIError("KIE_RESPONSES_INVALID_EVENT") from error
                    if not isinstance(event, dict):
                        raise KieAPIError("KIE_RESPONSES_INVALID_EVENT")
                    kind = event.get("type")
                    if kind in {"error", "response.failed", "response.incomplete"} or event.get("error"):
                        raise self._responses_failure(event)
                    if kind == "response.output_text.delta":
                        yield StreamChunk(content=event.get("delta", ""))
                    elif kind == "response.completed":
                        usage, credits = self._responses_usage(event["response"])
                        yield StreamChunk(finish_reason="stop", prompt_tokens=usage.get("input_tokens", 0),
                            completion_tokens=usage.get("output_tokens", 0), credits_consumed=credits)
                        completed = True
            if not completed:
                raise KieAPIError("KIE_RESPONSES_TERMINAL_EVENT_MISSING")

    async def _responses_sync(self, messages, reasoning_effort, **kwargs):
        if kwargs.get("tools") or kwargs.get("response_format"):
            raise ValueError("KIE_RESPONSES_PLANNER_OPTIONS_UNSUPPORTED")
        client = await self.client._get_client()
        response = await client.post("/codex/v1/responses", json=self._responses_body(messages, reasoning_effort, False))
        data = response.json()
        if response.status_code != 200:
            self.client._handle_error_response(response.status_code, data, self.model)
        usage, _credits = self._responses_usage(data)
        text = "".join(part["text"] for output in data.get("output", []) if output.get("type") == "message"
            for part in output.get("content", []) if part.get("type") == "output_text")
        return ChatResponse(text, "stop", usage.get("input_tokens", 0), usage.get("output_tokens", 0))
