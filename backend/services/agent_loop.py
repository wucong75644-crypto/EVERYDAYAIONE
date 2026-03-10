"""
Agent Loop — 多步工具编排引擎 (ReAct 模式)

对齐行业标准：OpenAI Agents SDK / Claude Agent SDK / LangGraph
核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复

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

from schemas.message import ContentPart, GenerationType, TextPart, ImagePart
from config.agent_tools import (
    AGENT_TOOLS,
    AGENT_SYSTEM_PROMPT,
    SYNC_TOOLS,
    ASYNC_TOOLS,
    TERMINAL_TOOLS,
    validate_tool_call,
)
from config.smart_model_config import TOOL_TO_TYPE
from services.agent_types import AgentResult, PendingAsyncTool, AgentGuardrails
from services.tool_executor import ToolExecutor

# 向后兼容：外部通过 from services.agent_loop import AgentResult 导入
__all__ = ["AgentLoop", "AgentResult", "PendingAsyncTool", "AgentGuardrails"]


class AgentLoop:
    """
    多步工具编排引擎 (ReAct 模式)

    核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复
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

    async def run(self, content: List[ContentPart]) -> AgentResult:
        """执行 Agent Loop，返回路由结果"""
        text = self._extract_text(content)
        has_image = any(isinstance(p, ImagePart) for p in content)

        result = await self._execute_loop(content)

        # 记录 Agent Loop 路由信号
        self._record_loop_signal(result, len(text), has_image)

        return result

    async def _execute_loop(self, content: List[ContentPart]) -> AgentResult:
        """Agent Loop 核心循环"""
        from core.config import get_settings

        self._settings = get_settings()
        guardrails = AgentGuardrails(
            max_turns=self._settings.agent_loop_max_turns,
            max_total_tokens=self._settings.agent_loop_max_tokens,
        )

        # 构建初始消息
        system_prompt = await self._build_system_prompt(content)
        user_text = self._extract_text(content)
        image_count = sum(1 for p in content if isinstance(p, ImagePart))
        if image_count > 0:
            user_text = f"[上下文：用户附带了{image_count}张图片]\n{user_text}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

        accumulated_context: List[str] = []
        pending_async: List[PendingAsyncTool] = []

        for turn in range(guardrails.max_turns):
            # 1. 检查 token 预算
            if guardrails.should_abort():
                logger.warning(
                    f"Agent loop token budget exceeded | "
                    f"tokens={guardrails.tokens_used} | turn={turn}"
                )
                return self._build_graceful_timeout(
                    pending_async, accumulated_context, turn, guardrails.tokens_used,
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
                return self._build_chat_result(
                    "", accumulated_context, turn + 1, guardrails.tokens_used,
                )

            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls")

            # 无 tool_calls → 大脑直接文字回复
            if not tool_calls:
                text_content = message.get("content", "")
                return self._build_chat_result(
                    text_content, accumulated_context, turn + 1, guardrails.tokens_used,
                )

            # 4. 处理每个 tool_call
            tool_results: List[Dict[str, Any]] = []
            for tc in tool_calls:
                terminal = await self._process_tool_call(
                    tc, turn, guardrails, tool_results,
                    accumulated_context, pending_async,
                )
                if terminal is not None:
                    return terminal

            # 5. 纯异步（无同步结果需要回传）→ 结束
            if not tool_results and pending_async:
                return self._build_async_result(
                    pending_async, accumulated_context,
                    turn + 1, guardrails.tokens_used,
                )

            # 没有任何结果也没有异步 → 退出
            if not tool_results and not pending_async:
                return self._build_chat_result(
                    "", accumulated_context, turn + 1, guardrails.tokens_used,
                )

            # 6. 回传同步结果给大脑（标准 tool_result 格式）
            messages.append({"role": "assistant", "tool_calls": tool_calls})
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })

        # 超出轮次 → 优雅终止
        return self._build_graceful_timeout(
            pending_async, accumulated_context,
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
        pending_async: List[PendingAsyncTool],
    ) -> Optional[AgentResult]:
        """处理单个 tool_call，返回 AgentResult 表示终止循环，None 表示继续"""
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        tc_id = tc.get("id", "")

        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            arguments = {}

        # Schema 验证
        if not validate_tool_call(tool_name, arguments):
            logger.warning(f"Invalid tool call | tool={tool_name} | args={arguments}")
            tool_results.append({
                "tool_call_id": tc_id,
                "content": f"无效的工具调用：{tool_name}",
                "is_error": True,
            })
            return None

        # 循环检测
        if tool_name in SYNC_TOOLS and guardrails.detect_loop(tool_name, arguments):
            logger.warning(f"Loop detected | tool={tool_name}")
            return self._build_graceful_timeout(
                pending_async, accumulated_context, turn + 1, guardrails.tokens_used,
            )

        logger.info(
            f"agent_step | turn={turn} | tool={tool_name} | "
            f"conv={self.conversation_id} | tokens_cum={guardrails.tokens_used}"
        )

        # 分发：终端工具
        if tool_name == "ask_user":
            return self._build_ask_user_result(
                arguments, accumulated_context, pending_async,
                turn + 1, guardrails.tokens_used,
            )
        if tool_name in TERMINAL_TOOLS:
            return self._build_terminal_result(
                tool_name, arguments, accumulated_context,
                pending_async, turn + 1, guardrails.tokens_used,
            )

        # web_search → 终端工具：大脑只判断意图，搜索由能力匹配的模型执行
        if tool_name == "web_search":
            return self._build_search_result(
                arguments, accumulated_context,
                turn + 1, guardrails.tokens_used,
            )

        # 分发：同步工具
        if tool_name in SYNC_TOOLS:
            await self._notify_progress(turn, tool_name, "executing")
            try:
                result = await self.executor.execute(tool_name, arguments)
                tool_results.append({"tool_call_id": tc_id, "content": result})
                accumulated_context.append(result)
            except Exception as e:
                logger.warning(f"Sync tool error | tool={tool_name} | error={e}")
                tool_results.append({
                    "tool_call_id": tc_id,
                    "content": f"工具执行失败: {str(e)}",
                    "is_error": True,
                })

        # 分发：异步工具
        elif tool_name in ASYNC_TOOLS:
            pending_async.append(PendingAsyncTool(
                tool_name=tool_name,
                arguments=arguments,
            ))

        return None

    # ========================================
    # 大脑调用
    # ========================================

    async def _call_brain(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """调用千问 FC（DashScope OpenAI 兼容 API）"""
        assert self._settings is not None
        client = await self._get_client()

        response = await client.post(
            "/chat/completions",
            json={
                "model": self._settings.agent_loop_model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        return response.json()

    # ========================================
    # 结果构建
    # ========================================

    def _build_chat_result(
        self, text: str, context: List[str], turns: int, tokens: int,
    ) -> AgentResult:
        """大脑直接文字回复 → 走 ChatHandler"""
        from config.smart_model_config import DEFAULT_CHAT_MODEL

        search_ctx = "\n".join(context) if context else None
        return AgentResult(
            generation_type=GenerationType.CHAT,
            model=DEFAULT_CHAT_MODEL,
            search_context=search_ctx,
            direct_reply=text if text else None,
            turns_used=turns,
            total_tokens=tokens,
        )

    def _build_terminal_result(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: List[str],
        pending_async: List[PendingAsyncTool],
        turns: int,
        tokens: int,
    ) -> AgentResult:
        """终端工具 → 构建最终结果"""
        model = arguments.get("model", "")
        search_ctx = "\n".join(context) if context else None

        if tool_name == "text_chat":
            return AgentResult(
                generation_type=GenerationType.CHAT,
                model=model,
                system_prompt=arguments.get("system_prompt"),
                search_context=search_ctx,
                tool_params=arguments,
                turns_used=turns,
                total_tokens=tokens,
            )

        if tool_name == "finish" and pending_async:
            return self._build_async_result(pending_async, context, turns, tokens)

        return AgentResult(
            generation_type=GenerationType.CHAT,
            model="",
            search_context=search_ctx,
            turns_used=turns,
            total_tokens=tokens,
        )

    def _build_ask_user_result(
        self,
        arguments: Dict[str, Any],
        context: List[str],
        pending_async: List[PendingAsyncTool],
        turns: int,
        tokens: int,
    ) -> AgentResult:
        """ask_user → 大脑主动回复用户（追问/说明）"""
        message = arguments.get("message", "")
        reason = arguments.get("reason", "need_info")
        search_ctx = "\n".join(context) if context else None

        logger.info(
            f"Agent ask_user | reason={reason} | "
            f"conv={self.conversation_id} | turns={turns}"
        )

        return AgentResult(
            generation_type=GenerationType.CHAT,
            model="",
            search_context=search_ctx,
            direct_reply=message,
            tool_params={"_ask_reason": reason},
            turns_used=turns,
            total_tokens=tokens,
        )

    def _build_search_result(
        self,
        arguments: Dict[str, Any],
        context: List[str],
        turns: int,
        tokens: int,
    ) -> AgentResult:
        """web_search → 按能力匹配搜索模型（从模型库按优先级取）

        大脑只负责判断"需要搜索"，实际搜索由模型库中有搜索能力的模型执行。
        模型选择：smart_models.json → web_search.models（按 priority 排序）→ 取第一个。
        """
        from config.smart_model_config import SMART_CONFIG, DEFAULT_CHAT_MODEL

        ws_models = SMART_CONFIG.get("web_search", {}).get("models", [])
        model = ws_models[0]["id"] if ws_models else DEFAULT_CHAT_MODEL

        search_ctx = "\n".join(context) if context else None

        logger.info(
            f"Agent web_search → routed to search model | model={model} | "
            f"query={arguments.get('search_query', '')} | conv={self.conversation_id}"
        )

        return AgentResult(
            generation_type=GenerationType.CHAT,
            model=model,
            system_prompt=arguments.get("system_prompt"),
            search_context=search_ctx,
            tool_params={
                "_needs_google_search": True,
                "_search_query": arguments.get("search_query", ""),
            },
            turns_used=turns,
            total_tokens=tokens,
        )

    # 工具名 → 前端渲染提示（大脑控制前端显示）
    _TOOL_RENDER_HINTS: Dict[str, Dict[str, str]] = {
        "generate_image": {"placeholder_text": "图片生成中", "component": "image_grid"},
        "generate_video": {"placeholder_text": "视频生成中", "component": "video_player"},
        "batch_generate_image": {"placeholder_text": "图片生成中", "component": "image_grid"},
    }

    def _build_async_result(
        self,
        pending_async: List[PendingAsyncTool],
        context: List[str],
        turns: int,
        tokens: int,
    ) -> AgentResult:
        """纯异步工具 → 从第一个异步工具推断 generation_type"""
        if not pending_async:
            return self._build_chat_result("", context, turns, tokens)

        first = pending_async[0]
        gen_type = TOOL_TO_TYPE.get(first.tool_name, GenerationType.CHAT)
        model = first.arguments.get("model", "")
        search_ctx = "\n".join(context) if context else None
        render_hints = self._TOOL_RENDER_HINTS.get(first.tool_name)

        if first.tool_name == "batch_generate_image":
            prompts = first.arguments.get("prompts", [])
            return AgentResult(
                generation_type=GenerationType.IMAGE,
                model=model,
                search_context=search_ctx,
                batch_prompts=prompts,
                tool_params=first.arguments,
                render_hints=render_hints,
                turns_used=turns,
                total_tokens=tokens,
            )

        return AgentResult(
            generation_type=gen_type,
            model=model,
            search_context=search_ctx,
            tool_params=first.arguments,
            render_hints=render_hints,
            turns_used=turns,
            total_tokens=tokens,
        )

    def _build_graceful_timeout(
        self,
        pending_async: List[PendingAsyncTool],
        context: List[str],
        turns: int,
        tokens: int,
    ) -> AgentResult:
        """超出轮次/token → 优雅终止（保存已有进度）"""
        logger.warning(
            f"Agent loop graceful timeout | turns={turns} | "
            f"tokens={tokens} | pending_async={len(pending_async)}"
        )

        if pending_async:
            return self._build_async_result(pending_async, context, turns, tokens)
        if context:
            return self._build_chat_result("", context, turns, tokens)

        from config.smart_model_config import DEFAULT_CHAT_MODEL
        return AgentResult(
            generation_type=GenerationType.CHAT,
            model=DEFAULT_CHAT_MODEL,
            turns_used=turns,
            total_tokens=tokens,
        )

    # ========================================
    # 系统提示词
    # ========================================

    async def _build_system_prompt(self, content: List[ContentPart]) -> str:
        """Agent 系统提示词 + 知识库经验注入"""
        base_prompt = AGENT_SYSTEM_PROMPT

        text = self._extract_text(content)
        if not text:
            return base_prompt

        try:
            from services.knowledge_service import search_relevant

            items = await search_relevant(query=text, limit=3)
            if items:
                knowledge_text = "\n".join(
                    f"- {k['title']}: {k['content']}" for k in items
                )
                return base_prompt + f"\n\n你已掌握的经验知识：\n{knowledge_text}"
        except Exception as e:
            logger.debug(f"Agent knowledge injection skipped | error={e}")

        return base_prompt

    # ========================================
    # WebSocket 通知
    # ========================================

    async def _notify_progress(
        self, turn: int, tool_name: str, status: str,
    ) -> None:
        """推送 agent_step 事件给前端"""
        try:
            from schemas.websocket import build_agent_step
            from services.websocket_manager import ws_manager

            msg = build_agent_step(
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                status=status,
                turn=turn,
            )
            await ws_manager.send_to_user(self.user_id, msg)
        except Exception as e:
            logger.debug(f"Agent step notification failed | error={e}")

    # ========================================
    # 辅助方法
    # ========================================

    def _extract_text(self, content: List[ContentPart]) -> str:
        """从 ContentPart 列表提取文本"""
        return " ".join(
            part.text for part in content if isinstance(part, TextPart)
        ).strip()

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            assert self._settings is not None
            self._client = httpx.AsyncClient(
                base_url=self._settings.dashscope_base_url,
                headers={
                    "Authorization": f"Bearer {self._settings.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._settings.agent_loop_timeout),
            )
        return self._client

    def _record_loop_signal(
        self, result: AgentResult, input_length: int, has_image: bool,
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
                        "loop_turns": result.turns_used,
                        "loop_tokens": result.total_tokens,
                    },
                )
            except Exception as e:
                logger.debug(f"Agent loop signal record skipped | error={e}")

        asyncio.create_task(_do_record())

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
