"""
Agent Loop — 多步工具编排引擎 (ReAct 模式)

对齐行业标准：OpenAI Agents SDK / Claude Agent SDK / LangGraph
核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复

两阶段架构：
- Phase 1：轻量意图分类（6 工具，~256 tokens，1 次 LLM 调用）
- Phase 2：按 domain 动态加载工具（仅 ERP/crawler/computer 需要多步循环）

安全护栏（代码级，不靠提示词）：
- 最大轮次限制（max_turns）
- Token 预算追踪（max_total_tokens）
- 循环检测（连续 3 次相同调用 → 中止）
- Schema 验证（拒绝幻觉工具调用）
"""

import asyncio
import json
import time as _time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

import httpx
from schemas.message import ContentPart, FilePart, GenerationType, ImagePart
from services.agent_context import AgentContextMixin
from services.agent_loop_infra import AgentInfraMixin
from services.agent_loop_tools import (
    AgentToolsMixin, ToolExpansionNeeded, _SLOW_TOOL_TIMEOUT,
    try_expand_tools, inject_phase1_model,
)
from services.agent_result_builder import (
    TOOL_RENDER_HINTS,
    build_chat_result,
    build_final_result,
    build_graceful_timeout,
    build_image_result,
)
from services.agent_types import AgentResult, PendingAsyncTool, AgentGuardrails
from services.tool_executor import ToolExecutor

# 向后兼容：外部通过 from services.agent_loop import ... 导入
__all__ = [
    "AgentLoop", "AgentResult", "PendingAsyncTool",
    "AgentGuardrails", "ToolExpansionNeeded", "_SLOW_TOOL_TIMEOUT",
]


class AgentLoop(
    AgentContextMixin, AgentToolsMixin, AgentInfraMixin,
):
    """
    多步工具编排引擎 (ReAct 模式)

    核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复
    上下文构建方法继承自 AgentContextMixin。
    工具处理方法继承自 AgentToolsMixin。
    基础设施方法继承自 AgentInfraMixin。
    """

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.executor = ToolExecutor(db, user_id, conversation_id, org_id=org_id)
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

    # ========================================
    # Phase 1 响应解析
    # ========================================

    def _parse_phase1_response(
        self, response: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """解析 Phase 1 响应 → (domain, signals)"""
        from config.phase_tools import PHASE1_TOOL_TO_DOMAIN

        choices = response.get("choices", [])
        if not choices:
            return "chat", {}

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls", [])
        if not tool_calls:
            return "chat", {}

        tc = tool_calls[0]
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Phase1 JSON parse error | raw={func.get('arguments')}",
            )
            arguments = {}

        domain = PHASE1_TOOL_TO_DOMAIN.get(tool_name, "chat")
        return domain, arguments

    # ========================================
    # 主循环入口
    # ========================================

    async def _execute_loop(
        self, content: List[ContentPart],
    ) -> AgentResult:
        """Agent Loop 主入口 — Phase 1 意图分类 + Phase 2 按需加载"""
        from config.phase_tools import PHASE1_SYSTEM_PROMPT, PHASE1_TOOLS
        from core.config import get_settings
        from services.model_selector import select_model

        self._settings = get_settings()
        text = self._extract_text(content)

        # 并行获取：完整历史 + 知识上下文
        history_result, knowledge_result = await asyncio.gather(
            self._get_recent_history(),
            self._fetch_knowledge(text),
            return_exceptions=True,
        )

        history_full = (
            history_result
            if not isinstance(history_result, BaseException)
            else None
        )
        knowledge_items = (
            knowledge_result
            if not isinstance(knowledge_result, BaseException)
            else None
        )

        # Phase 1 历史切片（最后 3 条纯文本，零额外 DB 查询）
        history_lite = self._slice_text_only(history_full, limit=3)

        # Phase 1 系统提示词（轻量，~350 字符）
        now = _time.strftime("%Y-%m-%d %H:%M", _time.localtime())
        phase1_prompt = PHASE1_SYSTEM_PROMPT + f"\n\n当前时间：{now}"
        user_location = getattr(self, "_user_location", None)
        if user_location:
            phase1_prompt += f"\n用户所在位置：{user_location}"

        # Phase 1 消息构建
        phase1_msgs: List[Dict[str, Any]] = [
            {"role": "system", "content": phase1_prompt},
        ]
        if history_lite:
            phase1_msgs.extend(history_lite)
        user_content = self._build_user_content(content)
        phase1_msgs.append({"role": "user", "content": user_content})

        # Phase 1 大脑调用（~256 tokens，tool_choice=required）
        domain: str = "chat"
        signals: Dict[str, Any] = {}
        phase1_ok = False
        for attempt in range(2):
            try:
                response = await self._call_brain(
                    phase1_msgs,
                    tools=PHASE1_TOOLS,
                    tool_choice="required",
                    max_tokens=256,
                )
                domain, signals = self._parse_phase1_response(response)
                phase1_ok = True
                break
            except Exception as e:
                logger.warning(
                    f"Phase1 attempt {attempt + 1} failed | error={e}",
                )
                if attempt == 0:
                    await asyncio.sleep(0.3)

        if not phase1_ok:
            logger.warning("Phase1 all attempts failed, default to chat")
            response = {"usage": {"total_tokens": 0}}

        # 模型选择（规则引擎，无 LLM 调用）
        has_image = getattr(self, "_has_image", False)
        thinking_mode = getattr(self, "_thinking_mode", None)
        model_id = select_model(domain, signals, has_image, thinking_mode)
        self._phase1_model = model_id

        usage = response.get("usage", {})
        phase1_tokens = usage.get("total_tokens", 0)

        logger.info(
            f"Phase1 done | domain={domain} | model={model_id} "
            f"| tokens={phase1_tokens}",
        )

        # ── Domain Dispatch ──
        if domain not in ("erp", "crawler", "computer"):
            return self._dispatch_direct_domain(
                domain, signals, model_id, phase1_tokens,
            )

        return await self._execute_phase2_loop(
            domain, content, user_content,
            history_full, knowledge_items, phase1_tokens,
        )

    # ========================================
    # Phase 1 直接返回
    # ========================================

    def _dispatch_direct_domain(
        self,
        domain: str,
        signals: Dict[str, Any],
        model_id: str,
        phase1_tokens: int,
    ) -> AgentResult:
        """chat/image/video/ask_user → 直接构建结果（无 Phase 2）"""
        if domain == "ask_user":
            self._record_ask_user_context(signals.get("message", ""))
            return AgentResult(
                generation_type=GenerationType.CHAT,
                model="",
                direct_reply=signals.get("message", ""),
                tool_params={
                    "_ask_reason": signals.get("reason", "need_info"),
                },
                turns_used=1,
                total_tokens=phase1_tokens,
            )

        if domain == "chat":
            return AgentResult(
                generation_type=GenerationType.CHAT,
                model=model_id,
                system_prompt=signals.get("system_prompt"),
                tool_params={
                    "_needs_google_search": signals.get(
                        "needs_search", False,
                    ),
                },
                turns_used=1,
                total_tokens=phase1_tokens,
            )

        if domain == "image":
            return build_image_result(signals, model_id, phase1_tokens)

        # video
        return AgentResult(
            generation_type=GenerationType.VIDEO,
            model=model_id,
            tool_params={"prompt": signals.get("prompt", "")},
            render_hints=TOOL_RENDER_HINTS.get("route_to_video"),
            turns_used=1,
            total_tokens=phase1_tokens,
        )

    # ========================================
    # Phase 2 辅助
    # ========================================

    async def _fetch_knowledge(
        self, text: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """获取知识库经验（Phase 2 注入用）"""
        if not text:
            return None
        try:
            from services.knowledge_service import search_relevant
            return await search_relevant(query=text, limit=3, org_id=self.org_id)
        except Exception as e:
            logger.debug(f"Knowledge fetch skipped | error={e}")
            return None

    def _build_phase2_messages(
        self,
        domain: str,
        user_content: List[Dict[str, Any]],
        history_full: Optional[List[Dict[str, Any]]],
        knowledge_items: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """构建 Phase 2 消息列表（domain prompt + 知识 + 历史 + 用户）"""
        from config.phase_tools import build_domain_prompt

        domain_prompt = build_domain_prompt(domain)

        if knowledge_items:
            knowledge_text = "\n".join(
                f"- {k['title']}: {k['content']}"
                for k in knowledge_items
            )
            domain_prompt += f"\n\n你已掌握的经验知识：\n{knowledge_text}"

        now = _time.strftime("%Y-%m-%d %H:%M", _time.localtime())
        domain_prompt += f"\n\n当前时间：{now}"
        user_location = getattr(self, "_user_location", None)
        if user_location:
            domain_prompt += f"\n用户所在位置：{user_location}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": domain_prompt},
        ]
        if history_full:
            messages.append({
                "role": "system",
                "content": "以下是最近的对话记录：",
            })
            messages.extend(history_full)
            messages.append({
                "role": "system",
                "content": "以上是历史记录。以下是用户当前的新消息：",
            })
        messages.append({"role": "user", "content": user_content})
        return messages

    # ========================================
    # Phase 2 多步工具循环
    # ========================================

    async def _execute_phase2_loop(
        self,
        domain: str,
        content: List[ContentPart],
        user_content: List[Dict[str, Any]],
        history_full: Optional[List[Dict[str, Any]]],
        knowledge_items: Optional[List[Dict[str, Any]]],
        phase1_tokens: int,
    ) -> AgentResult:
        """Phase 2: ERP/crawler/computer 多步工具编排循环"""
        from config.phase_tools import build_domain_tools
        from services.tool_selector import select_and_filter_tools

        guardrails = AgentGuardrails(
            max_turns=self._settings.agent_loop_max_turns,
            max_total_tokens=self._settings.agent_loop_max_tokens,
        )
        guardrails.add_tokens(phase1_tokens)

        # 全量工具 schema → 智能筛选
        all_tools = build_domain_tools(domain)
        user_text = self._extract_text(content)
        domain_tools = await select_and_filter_tools(
            domain, user_text, all_tools,
        )
        messages = self._build_phase2_messages(
            domain, user_content, history_full, knowledge_items,
        )

        routing_holder: Dict[str, Any] = {}
        accumulated_context: List[str] = []
        model = self._phase1_model

        # 兜底扩充状态
        expand_state = {"tool_expanded": False, "action_expanded": False}

        for turn in range(guardrails.max_turns):
            if guardrails.should_abort():
                return build_graceful_timeout(
                    accumulated_context, turn,
                    guardrails.tokens_used, model=model,
                )

            response = await self._call_brain(
                messages, tools=domain_tools,
            )
            usage = response.get("usage", {})
            guardrails.add_tokens(usage.get("total_tokens", 0))

            choices = response.get("choices", [])
            if not choices:
                return build_chat_result(
                    "", accumulated_context,
                    turn + 1, guardrails.tokens_used, model=model,
                )

            msg = choices[0].get("message", {})
            tool_calls = msg.get("tool_calls")

            if not tool_calls:
                return build_chat_result(
                    msg.get("content", ""), accumulated_context,
                    turn + 1, guardrails.tokens_used, model=model,
                )

            # 兜底扩充检测
            expanded = try_expand_tools(
                tool_calls, domain_tools, all_tools, expand_state,
            )
            if expanded:
                domain_tools = expanded
                logger.info(
                    f"Tool expansion | turn={turn} | "
                    f"tools={len(domain_tools)} | state={expand_state}"
                )
                continue

            tool_results: List[Dict[str, Any]] = []
            for tc in tool_calls:
                await self._process_tool_call(
                    tc, turn, guardrails, tool_results,
                    accumulated_context, routing_holder,
                )
                if routing_holder.get("_loop_abort"):
                    return build_graceful_timeout(
                        accumulated_context, turn + 1,
                        guardrails.tokens_used, model=model,
                    )

            messages.append({
                "role": "assistant", "tool_calls": tool_calls,
            })
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })

            if routing_holder.get("decision"):
                inject_phase1_model(routing_holder, model)
                return build_final_result(
                    routing_holder, accumulated_context,
                    turn + 1, guardrails.tokens_used,
                )

        # 超出轮次
        if routing_holder.get("decision"):
            inject_phase1_model(routing_holder, model)
            return build_final_result(
                routing_holder, accumulated_context,
                guardrails.max_turns, guardrails.tokens_used,
            )
        return build_graceful_timeout(
            accumulated_context,
            guardrails.max_turns, guardrails.tokens_used, model=model,
        )
