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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from services.prompt_builder.layers.static_layer import StaticLayer
from services.prompt_builder.layers.dynamic_layer import DynamicContext, DynamicLayer
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from services.prompt_builder.persona_gate import PersonaGate, default_gate


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
    prefetched_memory: Optional[str] = None
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

        dynamic_ctx = DynamicContext(
            current_time_text=time_text,
            permission_mode=inp.permission_mode,
            user_location=inp.user_location,
            user_preferences=inp.user_preferences,
            persona=gated_persona,
            relevant_memory=memory_prompt,
        )
        dynamic_content = DynamicLayer.render(dynamic_ctx)

        # ── Step 4: Layer 3 用户层 ──
        attachments_xml = (
            format_attachments(inp.workspace_files, inp.conversation_id)
            if inp.workspace_files else ""
        )
        workspace_prompt = (
            build_workspace_prompt(inp.workspace_files, inp.conversation_id)
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

        # ── Step 5: 拼接 messages ──
        messages: List[Dict[str, Any]] = []

        # Layer 1: 静态 system (cache_control 友好, 永久不变)
        messages.append({"role": "system", "content": static_content})

        # Layer 2: 动态 system (时间/偏好/persona/memory)
        messages.append({"role": "system", "content": dynamic_content})

        # Workspace 文件清单 (workspace_files 存在时, 独立 system block 做注意力锚点)
        # 行业对齐 Cline environment_details 思路, 但作为前置 system 而非附加
        if user_result.workspace_system_block:
            messages.append(
                {"role": "system", "content": user_result.workspace_system_block}
            )

        # Layer 6: 对话历史 + 话题聚焦
        if history_messages:
            messages.extend(history_messages)

        # Layer 5: 对话摘要 (短对话不注入)
        if summary_prompt and history_messages and len(history_messages) > 5:
            # 摘要紧贴 user 前 (对齐旧逻辑位置)
            messages.append({"role": "system", "content": summary_prompt})

        # 附件 XML (attachments_as_system=True 时独立 system block, 紧贴 user 前)
        if user_result.attachments_system_block:
            messages.append(
                {"role": "system", "content": user_result.attachments_system_block}
            )

        # Layer 3: user message (最终)
        messages.append(user_result.user_message)

        # ── Step 6: budget 控制 (保留 V3.3 三层兜底) ──
        state = await self._apply_budgets(messages, inp.text_content)

        return BuildResult(
            messages=messages,
            static_block_chars=len(static_content),
            dynamic_block_chars=len(dynamic_content),
            persona_injected=gated_persona is not None,
            memory_injected=memory_prompt is not None,
            state=state,
        )

    async def _parallel_fetch(self) -> tuple[Optional[str], Optional[str], List[Dict[str, Any]]]:
        """并行获取 memory / summary / history。

        返回 (memory_prompt, summary_prompt, history_messages)。
        persona 文本通过 self._persona_text 暂存。
        """
        from services.memory.memory_service_v2 import MemoryServiceV2
        from services.handlers.chat_context.summary_manager import get_context_summary
        from services.handlers import conversation_cache
        from services.handlers.context_compressor import compress_messages_if_needed
        from services.handlers.chat_context.history_loader import build_context_messages

        inp = self.inp

        async def _memory() -> tuple[Optional[str], str]:
            """返回 (l1_prepend, persona_text)。"""
            if inp.prefetched_memory is not None:
                return inp.prefetched_memory, ""
            try:
                svc = MemoryServiceV2(db_pool=inp.db)
                prepend, persona = await svc.build_memory_context(
                    user_id=inp.user_id,
                    org_id=inp.org_id,
                    query=inp.text_content,
                )
                return (prepend or None), (persona or "")
            except Exception as e:
                logger.warning(f"PromptBuilder memory fetch failed | {e}")
                return None, ""

        async def _summary() -> Optional[str]:
            try:
                return await get_context_summary(
                    inp.db, inp.conversation_id, prefetched=inp.prefetched_summary,
                )
            except Exception as e:
                logger.warning(f"PromptBuilder summary fetch failed | {e}")
                return None

        async def _history() -> List[Dict[str, Any]]:
            try:
                # 先走 Redis cache (V3.3)
                cached = await conversation_cache.get_messages(
                    inp.conversation_id, inp.org_id,
                )
                if cached is not None:
                    return cached
                # cache miss → DB rebuild + 统一压缩 + 回填
                msgs = await build_context_messages(
                    inp.db, inp.conversation_id, inp.text_content,
                )
                if not msgs:
                    return msgs
                try:
                    msgs, _ = await compress_messages_if_needed(msgs, conv_source="web")
                except Exception as e:
                    logger.warning(f"PromptBuilder compress on rebuild failed | {e}")
                await conversation_cache.set_messages(
                    inp.conversation_id, msgs, inp.org_id,
                )
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

            # 是否企微 conv (小预算)
            # PromptBuilder 不依赖 ChatHandler.self, 简化为统一 web 预算
            # wecom 走独立路径 (wecom_handler), 不复用 PromptBuilder
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
