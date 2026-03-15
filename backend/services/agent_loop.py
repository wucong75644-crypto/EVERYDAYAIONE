"""
Agent Loop — 多步工具编排引擎 (ReAct 模式)

对齐行业标准：OpenAI Agents SDK / Claude Agent SDK / LangGraph
核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复

工具分类（2 类）：
- INFO_TOOLS: 信息采集工具，结果回传大脑
- ROUTING_TOOLS: 路由决策工具，记录决策 + 确认回传

安全护栏（代码级，不靠提示词）：
- 最大轮次限制（max_turns）
- Token 预算追踪（max_total_tokens）
- 循环检测（连续 3 次相同调用 → 中止）
- Schema 验证（拒绝幻觉工具调用）
"""

import asyncio
import json
import time as _time
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from schemas.message import ContentPart, FilePart, ImagePart
from config.agent_tools import (
    AGENT_TOOLS,
    INFO_TOOLS,
    ROUTING_TOOLS,
    validate_tool_call,
)
from services.agent_context import AgentContextMixin
from services.agent_types import AgentResult, PendingAsyncTool, AgentGuardrails
from services.agent_result_builder import (
    build_chat_result,
    build_final_result,
    build_graceful_timeout,
)
from services.tool_executor import ToolExecutor

# 向后兼容：外部通过 from services.agent_loop import AgentResult 导入
__all__ = ["AgentLoop", "AgentResult", "PendingAsyncTool", "AgentGuardrails"]

# 慢速工具超时配置（秒），未列出的工具默认 30s
_SLOW_TOOL_TIMEOUT = {
    "social_crawler": 180.0,
}


class AgentLoop(AgentContextMixin):
    """
    多步工具编排引擎 (ReAct 模式)

    核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复
    上下文构建方法继承自 AgentContextMixin。
    """

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.executor = ToolExecutor(db, user_id, conversation_id)
        self._client: Optional[httpx.AsyncClient] = None
        self._settings: Optional[Any] = None

    async def run(
        self,
        content: List[ContentPart],
        thinking_mode: Optional[str] = None,
        task_id: Optional[str] = None,
        user_location: Optional[str] = None,
    ) -> AgentResult:
        """执行 Agent Loop，返回路由结果"""
        text = self._extract_text(content)
        has_image = any(isinstance(p, ImagePart) for p in content)
        has_file = any(isinstance(p, FilePart) for p in content)
        self._user_text = text  # 意图学习需要原始文本
        self._has_image = has_image  # 模型校验需要
        self._thinking_mode = thinking_mode  # 深度思考开关状态
        self._task_id = task_id  # WS 进度通知用
        self._user_location = user_location  # IP 定位城市

        result = await self._execute_loop(content)

        # 意图学习：路由成功（非 ask_user）时检查是否有 pending 的意图确认
        self._check_intent_learning(result, text)

        # 记录 Agent Loop 路由信号
        self._record_loop_signal(result, len(text), has_image, has_file)

        return result

    async def _execute_loop(self, content: List[ContentPart]) -> AgentResult:
        """Agent Loop 核心循环 — 串联执行，所有结果回传大脑"""
        from core.config import get_settings

        self._settings = get_settings()
        guardrails = AgentGuardrails(
            max_turns=self._settings.agent_loop_max_turns,
            max_total_tokens=self._settings.agent_loop_max_tokens,
        )

        # 构建初始消息：系统提示词 + 对话历史（并行获取，无交叉依赖）
        prompt_result, history_result = await asyncio.gather(
            self._build_system_prompt(content),
            self._get_recent_history(),
            return_exceptions=True,
        )

        # 安全解包：异常降级（两个函数内部已有 try/except，这里兜底防御）
        if isinstance(prompt_result, BaseException):
            logger.warning(f"Agent system prompt failed | error={prompt_result}")
            from config.agent_tools import AGENT_SYSTEM_PROMPT
            system_prompt = AGENT_SYSTEM_PROMPT
        else:
            system_prompt = prompt_result

        history_msgs = (
            history_result
            if not isinstance(history_result, BaseException)
            else None
        )
        if isinstance(history_result, BaseException):
            logger.warning(f"Agent history failed | error={history_result}")

        now = _time.strftime("%Y-%m-%d %H:%M", _time.localtime())
        system_prompt += f"\n\n当前时间：{now}"

        # 用户位置注入（IP 定位，辅助天气/本地搜索查询）
        user_location = getattr(self, "_user_location", None)
        if user_location:
            system_prompt += f"\n用户所在位置：{user_location}"

        # 深度思考模式提示（用户开启时，优先选支持深度思考的模型）
        thinking_mode = getattr(self, "_thinking_mode", None)
        if thinking_mode == "deep_think":
            system_prompt += (
                "\n\n用户已开启深度思考模式，"
                "请优先选择 深度思考:✓ 的模型。"
            )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if history_msgs:
            messages.append({
                "role": "system",
                "content": "以下是最近的对话记录：",
            })
            messages.extend(history_msgs)
            messages.append({
                "role": "system",
                "content": "以上是历史记录。以下是用户当前的新消息：",
            })

        # 当前用户消息（多模态：文本 + 图片 content blocks）
        user_content = self._build_user_content(content)
        messages.append({"role": "user", "content": user_content})

        routing_holder: Dict[str, Any] = {}
        accumulated_context: List[str] = []

        for turn in range(guardrails.max_turns):
            # 1. 检查 token 预算
            if guardrails.should_abort():
                logger.warning(
                    f"Agent loop token budget exceeded | "
                    f"tokens={guardrails.tokens_used} | turn={turn}"
                )
                return build_graceful_timeout(
                    accumulated_context, turn, guardrails.tokens_used,
                )

            # 2. 调用大脑
            start_ts = _time.monotonic()
            response = await self._call_brain(messages)
            elapsed_ms = int((_time.monotonic() - start_ts) * 1000)

            # 累加 token
            usage = response.get("usage", {})
            guardrails.add_tokens(usage.get("total_tokens", 0))

            # 3. 解析 tool_calls
            choices = response.get("choices", [])
            if not choices:
                return build_chat_result(
                    "", accumulated_context, turn + 1, guardrails.tokens_used,
                )

            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls")

            # 无 tool_calls → 大脑判断完毕 → 循环结束
            if not tool_calls:
                text_content = message.get("content", "")
                return build_chat_result(
                    text_content, accumulated_context,
                    turn + 1, guardrails.tokens_used,
                )

            # 4. 处理每个 tool_call
            tool_results: List[Dict[str, Any]] = []
            for tc in tool_calls:
                await self._process_tool_call(
                    tc, turn, guardrails, tool_results,
                    accumulated_context, routing_holder,
                )
                # 循环检测导致的中止
                if routing_holder.get("_loop_abort"):
                    return build_graceful_timeout(
                        accumulated_context, turn + 1,
                        guardrails.tokens_used,
                    )

            # 5. 所有结果回传大脑
            messages.append({"role": "assistant", "tool_calls": tool_calls})
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })

            # 6. 如果已有路由决策，不再继续循环
            if routing_holder.get("decision"):
                return build_final_result(
                    routing_holder, accumulated_context,
                    turn + 1, guardrails.tokens_used,
                )

        # 超出轮次 → 优雅终止
        if routing_holder.get("decision"):
            return build_final_result(
                routing_holder, accumulated_context,
                guardrails.max_turns, guardrails.tokens_used,
            )
        return build_graceful_timeout(
            accumulated_context,
            guardrails.max_turns, guardrails.tokens_used,
        )

    # ========================================
    # 工具处理
    # ========================================

    async def _process_tool_call(
        self,
        tc: Dict[str, Any],
        turn: int,
        guardrails: AgentGuardrails,
        tool_results: List[Dict[str, Any]],
        accumulated_context: List[str],
        routing_holder: Dict[str, Any],
    ) -> None:
        """处理单个 tool_call（INFO → 执行回传，ROUTING → 记录决策）"""
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        tc_id = tc.get("id", "")

        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            arguments = {}

        # Schema 验证
        if not validate_tool_call(tool_name, arguments):
            logger.warning(
                f"Invalid tool call | tool={tool_name} | args={arguments}"
            )
            self._fire_and_forget_knowledge(
                task_type="tool_validation", model_id=tool_name,
                status="failed",
                error_message=f"幻觉工具调用: {tool_name}, 参数: {arguments}",
            )
            tool_results.append({
                "tool_call_id": tc_id,
                "content": f"无效的工具调用：{tool_name}",
                "is_error": True,
            })
            return

        # 循环检测（仅 INFO 工具）
        if tool_name in INFO_TOOLS and guardrails.detect_loop(
            tool_name, arguments,
        ):
            logger.warning(f"Loop detected | tool={tool_name}")
            self._fire_and_forget_knowledge(
                task_type="loop_detection", model_id=tool_name,
                status="failed",
                error_message=f"连续3次相同调用被中止: {tool_name}({arguments})",
            )
            routing_holder["_loop_abort"] = True
            return

        logger.info(
            f"agent_step | turn={turn} | tool={tool_name} | "
            f"args={arguments} | "
            f"conv={self.conversation_id} | "
            f"tokens_cum={guardrails.tokens_used}"
        )

        # 信息工具：执行 → 结果回传大脑
        if tool_name in INFO_TOOLS:
            await self._notify_progress(turn, tool_name, "executing")
            try:
                timeout = _SLOW_TOOL_TIMEOUT.get(tool_name, 30.0)
                result = await asyncio.wait_for(
                    self.executor.execute(tool_name, arguments),
                    timeout=timeout,
                )
                tool_results.append({
                    "tool_call_id": tc_id, "content": result,
                })
                accumulated_context.append(result)
            except asyncio.TimeoutError:
                logger.warning(
                    f"Slow tool timeout | tool={tool_name} | "
                    f"timeout={timeout}s"
                )
                tool_results.append({
                    "tool_call_id": tc_id,
                    "content": f"工具执行超时（{int(timeout)}秒），请缩小范围后重试",
                    "is_error": True,
                })
            except Exception as e:
                logger.warning(
                    f"Sync tool error | tool={tool_name} | error={e}"
                )
                self._fire_and_forget_knowledge(
                    task_type="tool_execution", model_id=tool_name,
                    status="failed",
                    error_message=f"工具 {tool_name} 执行异常: {e}",
                )
                tool_results.append({
                    "tool_call_id": tc_id,
                    "content": f"工具执行失败: {str(e)}",
                    "is_error": True,
                })
            return

        # 路由工具：模型校验 + 记录决策 + 返回确认文本
        if tool_name in ROUTING_TOOLS:
            # route_to_chat 模型校验（图片/搜索能力匹配）
            model_warning = self._validate_routing_model(
                tool_name, arguments,
            )
            if model_warning:
                tool_results.append({
                    "tool_call_id": tc_id, "content": model_warning,
                })
                return

            routing_holder["decision"] = {
                "tool_name": tool_name,
                "arguments": arguments,
            }
            # 意图学习：ask_user 时记录上下文（fire-and-forget）
            if tool_name == "ask_user":
                self._record_ask_user_context(arguments.get("message", ""))

            confirmation = self._build_routing_confirmation(
                tool_name, arguments,
            )
            tool_results.append({
                "tool_call_id": tc_id, "content": confirmation,
            })
            return

    def _build_routing_confirmation(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> str:
        """构建路由确认文本（回传给大脑）"""
        if tool_name == "route_to_chat":
            return (
                f"已选择 {arguments.get('model', '')} 进行对话回复"
            )
        if tool_name == "route_to_image":
            count = len(arguments.get("prompts", []))
            return f"已安排生成 {count} 张图片"
        if tool_name == "route_to_video":
            return "已安排生成视频"
        if tool_name == "ask_user":
            return "将向用户发送询问"
        return "已确认"

    def _validate_routing_model(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> Optional[str]:
        """校验路由决策的模型选择，不匹配返回警告文本（大脑可重选）"""
        if tool_name != "route_to_chat":
            return None

        model_id = arguments.get("model", "")
        has_image = getattr(self, "_has_image", False)
        needs_search = arguments.get("needs_google_search", False)

        try:
            from config.smart_model_config import validate_model_choice
            warning = validate_model_choice(
                model_id, has_image=has_image, needs_search=needs_search,
            )
            if warning:
                logger.warning(
                    f"AgentLoop model mismatch | model={model_id} "
                    f"has_image={has_image} needs_search={needs_search}"
                )
                self._fire_and_forget_knowledge(
                    task_type="model_selection", model_id=model_id,
                    status="failed", error_message=warning,
                )
            return warning
        except Exception as e:
            logger.error(f"AgentLoop model validation error: {e}")
            return None

    # ========================================
    # 知识记录
    # ========================================

    def _fire_and_forget_knowledge(
        self, *, task_type: str, model_id: str,
        status: str, error_message: Optional[str] = None,
    ) -> None:
        """Fire-and-forget 知识记录（不阻塞主循环）"""
        try:
            from services.knowledge_extractor import extract_and_save
            asyncio.create_task(
                extract_and_save(
                    task_type=task_type, model_id=model_id,
                    status=status, error_message=error_message,
                )
            )
        except Exception as e:
            logger.debug(f"Knowledge recording skipped | error={e}")

    # ========================================
    # 大脑调用
    # ========================================

    async def _call_brain(
        self, messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """调用大脑（DashScope 或 OpenRouter，均为 OpenAI 兼容 API）"""
        assert self._settings is not None
        client = await self._get_client()

        provider = self._settings.agent_loop_provider
        model = (
            self._settings.agent_loop_openrouter_model
            if provider == "openrouter"
            else self._settings.agent_loop_model
        )

        logger.info(
            f"Brain calling | provider={provider} | model={model}"
        )

        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
        )
        response.raise_for_status()
        data = response.json()

        usage = data.get("usage", {})
        logger.info(
            f"Brain responded | tokens={usage.get('total_tokens', '?')}"
        )
        return data

    # ========================================
    # WebSocket 通知
    # ========================================

    async def _notify_progress(
        self, turn: int, tool_name: str, status: str,
    ) -> None:
        """推送 agent_step 事件给前端（优先通过 task_id 精准推送）"""
        try:
            from schemas.websocket import build_agent_step
            from services.websocket_manager import ws_manager

            msg = build_agent_step(
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                status=status,
                turn=turn,
                task_id=getattr(self, "_task_id", None),
            )
            task_id = getattr(self, "_task_id", None)
            if task_id:
                await ws_manager.send_to_task_subscribers(task_id, msg)
            else:
                await ws_manager.send_to_user(self.user_id, msg)
        except Exception as e:
            logger.debug(f"Agent step notification failed | error={e}")

    # ========================================
    # 基础设施
    # ========================================

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端（根据 provider 选择 DashScope 或 OpenRouter）"""
        if self._client is None or self._client.is_closed:
            assert self._settings is not None
            provider = self._settings.agent_loop_provider

            if provider == "openrouter":
                base_url = self._settings.openrouter_base_url
                api_key = self._settings.openrouter_api_key or ""
                extra_headers = {
                    "HTTP-Referer": "https://everydayai.one",
                    "X-Title": self._settings.openrouter_app_title,
                }
            else:
                base_url = self._settings.dashscope_base_url
                api_key = self._settings.dashscope_api_key or ""
                extra_headers = {}

            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **extra_headers,
                },
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self._settings.agent_loop_timeout,
                    write=10.0,
                    pool=5.0,
                ),
            )
        return self._client

    def _record_loop_signal(
        self, result: AgentResult, input_length: int,
        has_image: bool, has_file: bool = False,
    ) -> None:
        """记录 Agent Loop 路由信号到 knowledge_metrics"""
        async def _do_record() -> None:
            try:
                from services.knowledge_service import record_metric
                await record_metric(
                    task_type="routing",
                    model_id="agent_loop",
                    status="success",
                    user_id=self.user_id,
                    params={
                        "routing_tool": result.generation_type.value,
                        "routed_by": "agent_loop",
                        "recommended_model": result.model,
                        "input_length": input_length,
                        "has_image": has_image,
                        "has_file": has_file,
                        "loop_turns": result.turns_used,
                        "loop_tokens": result.total_tokens,
                    },
                )
            except Exception as e:
                logger.debug(
                    f"Agent loop signal record skipped | error={e}"
                )

        asyncio.create_task(_do_record())

    def _record_ask_user_context(self, ask_message: str) -> None:
        """ask_user 触发时，记录上下文供后续意图学习"""
        user_text = getattr(self, "_user_text", "")
        if not user_text:
            return

        async def _do_record() -> None:
            try:
                from services.intent_learning import record_ask_user_context
                await record_ask_user_context(
                    conversation_id=self.conversation_id,
                    user_id=self.user_id,
                    original_message=user_text,
                    ask_options=ask_message,
                )
            except Exception as e:
                logger.debug(f"Intent pending record skipped | error={e}")

        asyncio.create_task(_do_record())

    def _check_intent_learning(
        self, result: AgentResult, user_text: str,
    ) -> None:
        """路由成功时，检查是否有 pending 的意图学习"""
        # ask_user 本身不触发确认
        tool_params = result.tool_params or {}
        if tool_params.get("_ask_reason"):
            return
        # 纯兜底（无模型）不触发
        if not result.model:
            return

        confirmed_tool = f"route_to_{result.generation_type.value}"

        async def _do_check() -> None:
            try:
                from services.intent_learning import check_and_record_intent
                await check_and_record_intent(
                    conversation_id=self.conversation_id,
                    user_id=self.user_id,
                    user_response=user_text,
                    confirmed_tool=confirmed_tool,
                )
            except Exception as e:
                logger.debug(f"Intent learning check skipped | error={e}")

        asyncio.create_task(_do_check())

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
