"""PromptBuilder 主入口 —— 替代 chat_context_mixin._build_llm_messages。

设计原则 (来自 docs/document/TECH_PromptBuilder架构重构.md):
  1. Single Source of Truth: 同一信息只注入 1 次
  2. 稳定内容前置: Layer 1 静态段 + cache 友好
  3. 工具 schema 走 tools 字段, system 只写策略
  4. XML 结构化包裹, 模型解析最稳

替代的旧代码:
  - chat_context_mixin._build_llm_messages 全部 10 处 system append (SB1-10)
  - chat_handler L339-344 注入 TOOL_SYSTEM_PROMPT (SB11)
  - chat_handler L360-362 注入 _AUTO_FULL_PROMPT (SB12)
  - history_loader L93-97 user 消息时间戳前缀

保留的依赖 (不删, 复用):
  - utils.time_context.RequestContext (时间+位置注入)
  - services.handlers.chat_context.attachments.format_attachments
  - services.handlers.chat_context.attachments.build_workspace_prompt
  - services.memory.memory_service_v2.MemoryServiceV2 (memory 提取)
  - services.handlers.chat_context.summary_manager (对话摘要)
  - services.handlers.chat_context.history_loader.build_context_messages (历史)
  - services.handlers.conversation_cache (V3.3 Redis 缓存)
  - services.handlers.context_compressor (V3.3 六层压缩, 末尾保留 budget 控制)
  - services.knowledge_service.search_relevant (知识库召回)
  - services.handlers.chat_context.knowledge.filter_knowledge_by_similarity
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

from services.prompt_builder.layers.static_layer import StaticLayer
from services.prompt_builder.layers.dynamic_layer import DynamicContext, DynamicLayer
from services.prompt_builder.layers.session_stable_layer import (
    SessionStableContext, SessionStableLayer,
)
from services.prompt_builder.layers.turn_dynamic_layer import (
    TurnDynamicContext, TurnDynamicLayer,
)
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from services.prompt_builder.persona_gate import PersonaGate, default_gate

if TYPE_CHECKING:
    from services.handlers.context_snapshot import ContextSnapshot


@dataclass
class BuildInput:
    """PromptBuilder 输入参数。"""

    # 基础身份
    user_id: str
    conversation_id: str
    org_id: Optional[str] = None

    # user 消息相关
    text_content: str = ""                                      # user 原话
    workspace_files: List[Dict[str, Any]] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)
    file_urls: List[str] = field(default_factory=list)

    # 运行时配置
    permission_mode: str = "auto"                                # 'auto' | 'plan' | 'ask'
    user_location: Optional[str] = None
    user_preferences: Optional[str] = None                       # Custom Instructions

    # DB 句柄 (供并行获取 memory / summary / history / knowledge)
    db: Any = None

    # 可选注入 (chat_context_mixin 兼容)
    prefetched_summary: Optional[str] = None
    context_snapshot: Optional["ContextSnapshot"] = None
    # V2 阶段 4.1: prefetched_memory 已删除
    # mem0 调用统一到 PromptBuilder._memory() 内部 + session cache
    persona_gate_instance: Optional[PersonaGate] = None

    # request context (含时间 + 位置, 来自上游)
    request_ctx: Any = None                                      # utils.time_context.RequestContext

    # 配置开关
    attachments_as_system: bool = True                           # 配合 messages_attachments_as_system


@dataclass
class BuildResult:
    """PromptBuilder 输出。"""

    messages: List[Dict[str, Any]]                               # OpenAI/Anthropic 兼容 messages 列表
    static_block_chars: int                                      # 调试: layer 1 字符数
    dynamic_block_chars: int                                     # 调试: layer 2 字符数
    persona_injected: bool                                       # persona 是否进入 prompt
    memory_injected: bool                                        # L1 memory 是否注入
    state: str                                                   # NORMAL / ARCHIVED / SUMMARIZED / ENFORCED


class PromptBuilder:
    """统一 prompt 构造入口。"""

    def __init__(self, inp: BuildInput):
        self.inp = inp
        self._gate = inp.persona_gate_instance or default_gate()

    async def build(self) -> BuildResult:
        """构造 messages 列表, 替代 _build_llm_messages。

        流程:
          1. 并行 fetch: memory / summary / history / knowledge
          2. Layer 1 (system 静态): StaticLayer.render() — 进程级缓存
          3. Layer 2 (system 动态): DynamicLayer.render(ctx) — 时间/偏好/persona/memory
          4. Layer 3 (user): UserLayer.render() — 附件 XML + user text
          5. 拼接顺序: [static_sys, dynamic_sys, *workspace_files_sys,
                       *history, attach_sys (可选), user]
          6. 末尾 budget 控制: enforce_tool_budget / enforce_history_budget / enforce_budget
        """
        from utils.time_context import RequestContext
        from services.handlers.chat_context.attachments import (
            build_workspace_prompt,
            format_attachments,
        )

        inp = self.inp

        # ── Step 1: 并行 fetch ──
        memory_prompt, summary_prompt, history_messages = await self._parallel_fetch()

        # 提取 persona (memory_service_v2.build_memory_context 同时返回 prepend + persona)
        # 注: persona 已在 _parallel_fetch 内通过 self._persona_text 暂存
        persona_text = getattr(self, "_persona_text", "") or None

        # ── Step 2: Layer 1 静态段 ──
        static_content = StaticLayer.render()

        # ── Step 3: Layer 2 动态段 ──
        if inp.request_ctx is None:
            request_ctx = RequestContext.build(
                user_id=inp.user_id,
                org_id=inp.org_id,
                request_id=inp.conversation_id or "",
            )
        else:
            request_ctx = inp.request_ctx

        time_text = request_ctx.for_prompt_injection()

        # persona gate 过滤
        gated_persona = self._gate.filter(persona_text)

        # V2: 把原 Layer 2 拆成 L2a 会话稳定 + L2b 本轮动态
        # L2a 整会话不变 (permission_mode + preferences + persona + memory) → cache 友好
        # L2b 每条新 user 才变 (current_time) → 不 cache, 但小
        session_stable_ctx = SessionStableContext(
            permission_mode=inp.permission_mode,
            user_preferences=inp.user_preferences,
            user_facts=gated_persona,       # mem0 短事实 (原 persona)
            user_memory=memory_prompt,      # mem0 召回
        )
        session_stable_content = SessionStableLayer.render(session_stable_ctx)

        turn_dynamic_ctx = TurnDynamicContext(
            current_time_text=time_text,
            user_location=inp.user_location,
        )
        turn_dynamic_content = TurnDynamicLayer.render(turn_dynamic_ctx)

        # ── Step 4: Layer 3 用户层 ──
        attachments_xml = (
            format_attachments(inp.workspace_files, inp.conversation_id, inp.org_id)
            if inp.workspace_files else ""
        )
        workspace_prompt = (
            build_workspace_prompt(inp.workspace_files, inp.conversation_id, inp.org_id)
            if inp.workspace_files else ""
        )

        user_inp = UserMessageInput(
            text=inp.text_content,
            workspace_files=inp.workspace_files,
            attachments_xml=attachments_xml,
            workspace_prompt=workspace_prompt,
            image_urls=inp.image_urls,
            file_urls=inp.file_urls,
            attachments_as_system=inp.attachments_as_system,
        )
        user_result = UserLayer.render(user_inp)

        messages = self._compose_messages(
            static_content=static_content,
            session_stable_content=session_stable_content,
            turn_dynamic_content=turn_dynamic_content,
            history_messages=history_messages,
            summary_prompt=summary_prompt,
            user_result=user_result,
        )

        # ── Step 6: budget 控制 (保留 V3.3 三层兜底) ──
        state = await self._apply_budgets(messages, inp.text_content)

        return BuildResult(
            messages=messages,
            static_block_chars=len(static_content),
            # V2: dynamic_block_chars 现在等于 L2a + L2b 总和 (兼容旧字段名)
            dynamic_block_chars=len(session_stable_content) + len(turn_dynamic_content),
            persona_injected=gated_persona is not None,
            memory_injected=memory_prompt is not None,
            state=state,
        )

    @staticmethod
    def _compose_messages(
        static_content: str,
        session_stable_content: str,
        turn_dynamic_content: str,
        history_messages: List[Dict[str, Any]],
        summary_prompt: Optional[str],
        user_result: Any,
    ) -> List[Dict[str, Any]]:
        """按稳定缓存边界拼接 system、历史、附件和当前 user。"""
        from core.config import get_settings

        messages: List[Dict[str, Any]] = []
        if get_settings().prompt_cache_control_enabled:
            messages.append({
                "role": "system",
                "content": [
                    {"type": "text", "text": static_content},
                    {
                        "type": "text",
                        "text": session_stable_content,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            })
        else:
            messages.append({"role": "system", "content": static_content})
            messages.append({
                "role": "system",
                "content": session_stable_content,
            })

        messages.append({"role": "system", "content": turn_dynamic_content})
        if user_result.workspace_system_block:
            messages.append({
                "role": "system",
                "content": user_result.workspace_system_block,
            })
        messages.extend(history_messages)
        if summary_prompt and len(history_messages) > 5:
            messages.append({"role": "system", "content": summary_prompt})
        if user_result.attachments_system_block:
            messages.append({
                "role": "system",
                "content": user_result.attachments_system_block,
            })
        messages.append(user_result.user_message)
        return messages

    async def _parallel_fetch(self) -> tuple[Optional[str], Optional[str], List[Dict[str, Any]]]:
        """并行获取 memory / summary / history。

        返回 (memory_prompt, summary_prompt, history_messages)。
        persona 文本通过 self._persona_text 暂存。
        """
        from services.memory.memory_service_v2 import MemoryServiceV2
        from services.handlers.chat_context.summary_manager import get_context_summary
        from services.handlers.context_compressor import compress_messages_if_needed
        from services.handlers.chat_context.history_loader import build_context_messages

        inp = self.inp

        async def _memory() -> tuple[Optional[str], str]:
            """返回 (l1_prepend, persona_text)。

            V2 阶段 4.1: 单一入口 + 会话级缓存
              - 新会话开头查一次 mem0, 整会话固定
              - 学到的新事实异步抽取存 DB, 等下次新会话生效
              - prefetched_memory 路径已删除 (chat_handler / chat_generate_mixin 不再预取)
            """
            # 学到的新事实异步抽取存 DB, 等下次新会话生效
            from services.prompt_builder import session_memory_cache
            cached = await session_memory_cache.get_session_memory(
                inp.conversation_id, inp.org_id,
            )
            if cached is not None:
                prepend, persona = cached
                logger.info(
                    f"mem0 session cache HIT | conv={inp.conversation_id} | "
                    f"l1_len={len(prepend) if prepend else 0} | persona={'yes' if persona else 'no'}"
                )
                return prepend, persona
            try:
                svc = MemoryServiceV2(db_pool=inp.db)
                prepend, persona = await svc.build_memory_context(
                    user_id=inp.user_id,
                    org_id=inp.org_id,
                    query=inp.text_content,
                )
                prepend = prepend or None
                persona = persona or ""
                # 写回 session cache, 整会话内后续轮次命中
                await session_memory_cache.set_session_memory(
                    inp.conversation_id, prepend, persona, inp.org_id,
                )
                return prepend, persona
            except Exception as e:
                logger.warning(f"PromptBuilder memory fetch failed | {e}")
                return None, ""

        async def _summary() -> Optional[str]:
            if inp.context_snapshot is not None:
                return inp.context_snapshot.summary_prompt
            try:
                return await get_context_summary(
                    inp.db, inp.conversation_id, prefetched=inp.prefetched_summary,
                )
            except Exception as e:
                logger.warning(f"PromptBuilder summary fetch failed | {e}")
                return None

        async def _history() -> List[Dict[str, Any]]:
            if inp.context_snapshot is not None:
                # 每个 PromptBuilder 只消费副本，后续预算压缩和工具循环
                # 不得修改冻结在 ContextSnapshot 中的历史。
                return copy.deepcopy(inp.context_snapshot.history_messages)
            try:
                msgs = await build_context_messages(
                    inp.db, inp.conversation_id, inp.text_content,
                )
                if not msgs:
                    return msgs
                try:
                    msgs, _ = await compress_messages_if_needed(msgs, conv_source="web")
                except Exception as e:
                    logger.warning(f"PromptBuilder compress on rebuild failed | {e}")
                return msgs
            except Exception as e:
                logger.warning(f"PromptBuilder history fetch failed | {e}")
                return []

        memory_result, summary_result, history_result = await asyncio.gather(
            _memory(), _summary(), _history(),
            return_exceptions=True,
        )

        if isinstance(memory_result, BaseException):
            l1_prepend, persona_text = None, ""
        else:
            l1_prepend, persona_text = memory_result

        summary_prompt = (
            None if isinstance(summary_result, BaseException) else summary_result
        )
        history = (
            [] if isinstance(history_result, BaseException) else (history_result or [])
        )

        # persona 暂存到 self, build() 主流程后续取用
        self._persona_text = persona_text

        return l1_prepend, summary_prompt, history

    async def _apply_budgets(
        self, messages: List[Dict[str, Any]], current_text: str,
    ) -> str:
        """复用 V3.3 三层 budget 控制, 防止超长。

        返回 budget state (NORMAL / ARCHIVED / SUMMARIZED / ENFORCED)。
        """
        try:
            from core.config import get_settings
            from services.handlers.context_compressor import (
                enforce_tool_budget, enforce_history_budget, enforce_budget,
                compress_messages_if_needed,
            )

            _s = get_settings()
            inp = self.inp

            source = (
                inp.context_snapshot.conversation_source
                if inp.context_snapshot is not None else ""
            )
            if source == "wecom":
                tool_budget = _s.context_tool_token_budget
                history_budget = _s.context_history_token_budget
                total_budget = _s.context_max_tokens
            else:
                tool_budget = _s.context_web_tool_token_budget
                history_budget = _s.context_web_history_token_budget
                total_budget = _s.context_web_max_tokens

            enforce_tool_budget(messages, tool_budget)
            await enforce_history_budget(
                messages, history_budget, current_query=current_text,
            )
            enforce_budget(messages, total_budget)

            # 状态: 简化为 NORMAL (具体压缩状态由 compress_messages_if_needed 自管理)
            return "NORMAL"
        except Exception as e:
            logger.warning(f"PromptBuilder budget enforcement failed | {e}")
            return "ERROR"
