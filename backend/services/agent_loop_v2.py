"""
Agent Loop v2 Mixin — 意图优先 + 动态工具加载

Phase 1：轻量意图分类（6 工具，~2000 tokens，1 次 LLM 调用）
Phase 2：按 domain 动态加载工具（仅 ERP/crawler 需要多步循环）

与 AgentLoop 通过 Mixin 继承组合，共享 self._settings / self._client 等属性。
"""

import asyncio
import time as _time
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart, GenerationType
from services.agent_result_builder import (
    TOOL_RENDER_HINTS,
    build_chat_result,
    build_final_result,
    build_graceful_timeout,
)
from services.agent_types import AgentGuardrails, AgentResult


class AgentLoopV2Mixin:
    """Agent Loop v2 意图优先架构方法集（Mixin，由 AgentLoop 继承）"""

    async def _execute_loop_v2(
        self, content: List[ContentPart],
    ) -> AgentResult:
        """Agent Loop v2 — Phase 1 意图分类 + Phase 2 按需加载"""
        from config.phase_tools import PHASE1_SYSTEM_PROMPT, PHASE1_TOOLS
        from services.model_selector import select_model

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
        # 失败时重试 1 次，仍失败则降级为 chat 域（不回退 v1）
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
        # chat/image/video/ask_user → 直接返回（零 Phase 2 调用）
        if domain not in ("erp", "crawler"):
            return self._dispatch_direct_domain(
                domain, signals, model_id, phase1_tokens,
            )

        # erp/crawler → Phase 2 多步工具循环
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
            return self._build_image_result(
                signals, model_id, phase1_tokens,
            )

        # video
        return AgentResult(
            generation_type=GenerationType.VIDEO,
            model=model_id,
            tool_params={"prompt": signals.get("prompt", "")},
            render_hints=TOOL_RENDER_HINTS.get("route_to_video"),
            turns_used=1,
            total_tokens=phase1_tokens,
        )

    def _build_image_result(
        self,
        signals: Dict[str, Any],
        model_id: str,
        phase1_tokens: int,
    ) -> AgentResult:
        """Phase 1 image 域 → 转换 prompts 格式并构建结果"""
        raw_prompts = signals.get("prompts", [])
        aspect = signals.get("aspect_ratio", "1:1")
        render_hints = TOOL_RENDER_HINTS.get("route_to_image")

        if not raw_prompts:
            return build_chat_result(
                "", [], 1, phase1_tokens, model=model_id,
            )

        if len(raw_prompts) == 1:
            prompt_text = (
                raw_prompts[0] if isinstance(raw_prompts[0], str)
                else str(raw_prompts[0])
            )
            return AgentResult(
                generation_type=GenerationType.IMAGE,
                model=model_id,
                tool_params={
                    "prompt": prompt_text,
                    "aspect_ratio": aspect,
                },
                render_hints=render_hints,
                turns_used=1,
                total_tokens=phase1_tokens,
            )

        batch = [
            {
                "prompt": (p if isinstance(p, str) else str(p)),
                "aspect_ratio": aspect,
            }
            for p in raw_prompts
        ]
        return AgentResult(
            generation_type=GenerationType.IMAGE,
            model=model_id,
            batch_prompts=batch,
            tool_params=signals,
            render_hints=render_hints,
            turns_used=1,
            total_tokens=phase1_tokens,
        )

    # ========================================
    # Phase 2 多步工具循环
    # ========================================

    async def _fetch_knowledge(
        self, text: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """获取知识库经验（Phase 2 注入用）"""
        if not text:
            return None
        try:
            from services.knowledge_service import search_relevant
            return await search_relevant(query=text, limit=3)
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

    async def _execute_phase2_loop(
        self,
        domain: str,
        content: List[ContentPart],
        user_content: List[Dict[str, Any]],
        history_full: Optional[List[Dict[str, Any]]],
        knowledge_items: Optional[List[Dict[str, Any]]],
        phase1_tokens: int,
    ) -> AgentResult:
        """Phase 2: ERP/crawler 多步工具编排循环"""
        from config.phase_tools import build_domain_tools

        guardrails = AgentGuardrails(
            max_turns=self._settings.agent_loop_max_turns,
            max_total_tokens=self._settings.agent_loop_max_tokens,
        )
        guardrails.add_tokens(phase1_tokens)

        domain_tools = build_domain_tools(domain)
        messages = self._build_phase2_messages(
            domain, user_content, history_full, knowledge_items,
        )

        routing_holder: Dict[str, Any] = {}
        accumulated_context: List[str] = []
        model = self._phase1_model

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
                self._inject_phase1_model(routing_holder, model)
                return build_final_result(
                    routing_holder, accumulated_context,
                    turn + 1, guardrails.tokens_used,
                )

        # 超出轮次
        if routing_holder.get("decision"):
            self._inject_phase1_model(routing_holder, model)
            return build_final_result(
                routing_holder, accumulated_context,
                guardrails.max_turns, guardrails.tokens_used,
            )
        return build_graceful_timeout(
            accumulated_context,
            guardrails.max_turns, guardrails.tokens_used, model=model,
        )

    @staticmethod
    def _inject_phase1_model(
        routing_holder: Dict[str, Any], model: str,
    ) -> None:
        """Phase 2 出口注入 Phase 1 选定的模型"""
        decision = routing_holder.get("decision")
        if decision and not decision["arguments"].get("model"):
            decision["arguments"]["model"] = model
