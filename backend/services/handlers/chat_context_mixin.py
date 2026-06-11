"""
Chat 上下文构建 Mixin

负责 LLM 消息组装：记忆注入、搜索上下文、对话历史、路由人设。
供 ChatHandler 混入使用。

Phase 1-6 上下文工程重构。设计文档：docs/document/TECH_上下文工程重构.md
Phase 7: 知识库 similarity 分数门控（替代正则排除）
Phase 8: 结构化附件元数据（XML <attachments> + status 行动指引）

本文件保持 Mixin 类骨架 + 主流程（_build_llm_messages）+ 记忆/知识库召回。
具体渲染/提取/历史/摘要等纯函数逻辑在 services/handlers/chat_context/ 子包。
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart
from services.handlers.chat_context.attachments import (
    build_workspace_prompt,
    format_attachments,
)
from services.handlers.chat_context.content_extractors import (
    extract_image_urls_from_content,
    extract_oai_messages_from_content,
    extract_text_from_content,
)
from services.handlers.chat_context.history_loader import build_context_messages
from services.handlers.chat_context.knowledge import (
    filter_knowledge_by_similarity,
)
from services.handlers.chat_context.summary_manager import (
    get_context_summary,
    update_summary_if_needed,
)
from utils.time_context import RequestContext


class ChatContextMixin:
    """Chat 上下文构建能力：记忆、搜索、历史、消息组装

    所有具体渲染/提取逻辑委托到 chat_context/ 子包，
    本类负责编排（_build_llm_messages）+ 记忆/知识库召回的 self.* 调用。
    """

    # ── 知识库 similarity 过滤（纯静态，外部直接调）──
    _filter_knowledge_by_similarity = staticmethod(filter_knowledge_by_similarity)

    # ── 附件 XML 渲染（纯静态）──
    _format_attachments = staticmethod(format_attachments)
    _build_workspace_prompt = staticmethod(build_workspace_prompt)

    # ── DB content 提取（纯静态）──
    _extract_image_urls_from_content = staticmethod(extract_image_urls_from_content)
    _extract_text_from_content = staticmethod(extract_text_from_content)
    _extract_oai_messages_from_content = staticmethod(extract_oai_messages_from_content)

    async def _build_llm_messages(
        self,
        content: List[ContentPart],
        user_id: str,
        conversation_id: str,
        text_content: str,
        prefetched_summary: Optional[str] = None,
        prefetched_memory: Optional[str] = None,
        user_location: Optional[str] = None,
        permission_mode: str = "auto",
    ) -> List[Dict[str, Any]]:
        """组装发送给 LLM 的完整消息列表。

        V3.4: 统一走 PromptBuilder, 替代旧的 11 处碎片化 system append。
        设计文档: docs/document/TECH_PromptBuilder架构重构.md

        旧 11 处注入全部合并到 PromptBuilder 的 3 层结构:
          Layer 1 (静态): 角色 + 规则 + 工作流 + 工具策略 + 模式约束
          Layer 2 (动态): 时间 + 位置 + 偏好 + persona + 相关记忆
          Layer 3 (user): 附件 XML + user text (不加时间戳前缀)

        本函数保持原签名兼容旧调用方 (chat_handler / chat_generate_mixin)。
        """
        from services.prompt_builder import PromptBuilder, BuildInput
        from core.config import get_settings

        image_urls = self._extract_image_urls(content)
        file_urls = self._extract_file_urls(content)
        workspace_files = self._extract_workspace_files(content)

        # 注册 workspace 文件到会话级路径缓存 (保留旧逻辑, PromptBuilder 不负责文件管理)
        if workspace_files:
            try:
                from services.agent.file_path_cache import get_file_cache
                from core.workspace import resolve_workspace_dir, resolve_staging_dir
                _org_id = getattr(self, "org_id", None)
                _settings = get_settings()
                _ws_dir = resolve_workspace_dir(
                    _settings.file_workspace_root, user_id, _org_id,
                )
                _cache = get_file_cache(conversation_id)
                _staging = resolve_staging_dir(
                    _settings.file_workspace_root, user_id, _org_id, conversation_id,
                )
                _cache.set_staging_dir(_staging)
                for f in workspace_files:
                    wp = f.get("workspace_path", "")
                    if wp:
                        import os
                        _abs = os.path.join(_ws_dir, wp)
                        _cache.register(wp, workspace=_abs)
            except Exception as e:
                logger.debug(f"Workspace file cache registration failed | error={e}")

        # workspace 文件不走多模态 image_url (大部分格式不支持)
        if workspace_files:
            ws_urls = {f["url"] for f in workspace_files if f.get("url")}
            file_urls = [u for u in file_urls if u not in ws_urls]

        # 调 PromptBuilder 统一构造
        inp = BuildInput(
            user_id=user_id,
            conversation_id=conversation_id,
            org_id=getattr(self, "org_id", None),
            text_content=text_content,
            workspace_files=workspace_files,
            image_urls=image_urls,
            file_urls=file_urls,
            permission_mode=permission_mode,
            user_location=user_location,
            user_preferences=None,  # TODO Phase 2 后: 从 user 表读取 custom_instructions
            db=self.db,
            prefetched_summary=prefetched_summary,
            prefetched_memory=prefetched_memory,
            request_ctx=getattr(self, "request_ctx", None),
            attachments_as_system=get_settings().messages_attachments_as_system,
        )

        builder = PromptBuilder(inp)
        result = await builder.build()

        logger.info(
            f"PromptBuilder done | conv={conversation_id} | "
            f"static={result.static_block_chars} | "
            f"dynamic={result.dynamic_block_chars} | "
            f"persona={result.persona_injected} | "
            f"memory={result.memory_injected} | "
            f"messages={len(result.messages)}"
        )

        return result.messages

    async def _build_memory_prompt(
        self, user_id: str, query: str
    ) -> Optional[str]:
        """构建记忆上下文（V2 双部分注入）

        返回格式仍为 Optional[str]（兼容旧调用方），但内部用 V2 管道。
        双部分注入（prepend L1 + append persona）在 _build_llm_messages 中拆分处理。
        """
        try:
            from services.memory.memory_service_v2 import MemoryServiceV2

            svc = MemoryServiceV2(db_pool=self.db)
            prepend, append_system = await svc.build_memory_context(
                user_id=user_id,
                org_id=self.org_id,
                query=query,
            )

            # 缓存 persona 到实例属性，供 _build_llm_messages 取用
            self._memory_persona_context = append_system

            if prepend:
                logger.debug(
                    f"Memory V2 injected | user_id={user_id} | "
                    f"l1_len={len(prepend)} | persona={'yes' if append_system else 'no'}"
                )
            return prepend or None
        except Exception as e:
            logger.warning(
                f"Memory V2 injection failed, skipping | "
                f"user_id={user_id} | error={e}"
            )
            self._memory_persona_context = ""
            return None

    async def _fetch_knowledge(self, query: str) -> Optional[list]:
        """获取知识库经验 + 历史成功案例（两路并行召回）。

        通用知识和经验案例混合返回，经验结果加 _source="experience" tag，
        注入时按 tag 分离为独立 system message。
        设计文档: docs/document/TECH_Agent能力通信架构.md §3.4.2 / Phase 3
        """
        if not query:
            return None
        try:
            from services.knowledge_service import search_relevant
            general, experience = await asyncio.gather(
                search_relevant(query=query, limit=3, org_id=self.org_id),
                search_relevant(
                    query=query,
                    limit=2,
                    category="experience",
                    node_type="routing_pattern",
                    min_confidence=0.6,
                    org_id=self.org_id,
                ),
                return_exceptions=True,
            )
            g = general if not isinstance(general, BaseException) else []
            e = experience if not isinstance(experience, BaseException) else []
            for item in (e or []):
                item["_source"] = "experience"
            result = (g or []) + (e or [])
            return result if result else None
        except Exception as ex:
            logger.debug(f"Knowledge fetch skipped | error={ex}")
            return None

    async def _extract_memories_async(
        self,
        user_id: str,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """异步从对话中提取记忆（V2 管道调度器）

        V2 改造：不再直接调 Mem0，而是通知 PipelineScheduler。
        调度器根据 Warm-up 阈值 / 稳态计数决定何时触发 L1 提取。
        L1→L2→L3 全部由调度器自动编排。
        """
        try:
            if len(user_text) < 10:
                return

            from services.memory.memory_service_v2 import MemoryServiceV2, get_scheduler

            scheduler = await get_scheduler(db_pool=self.db)

            messages = [
                {"role": "user", "content": user_text, "id": str(conversation_id), "timestamp": __import__("time").time() * 1000},
                {"role": "assistant", "content": assistant_text, "id": "", "timestamp": __import__("time").time() * 1000},
            ]

            await scheduler.on_turn_committed(
                user_id=user_id,
                org_id=self.org_id,
                session_id=conversation_id,
                messages=messages,
            )

        except Exception as e:
            logger.warning(
                f"Memory V2 extraction failed | user_id={user_id} | "
                f"conversation_id={conversation_id} | "
                f"error_type={type(e).__name__} | error={e!r}"
            )

    async def _build_context_messages(
        self, conversation_id: str, current_text: str
    ) -> List[Dict[str, Any]]:
        """对话历史加载 — V3.3 Redis cache 优先 + DB 重建降级。

        路径(对齐 OpenAI Assistants thread 模式):
          1. Redis.get(conv_id) → hit 直接返回(99% 流程)
          2. miss → DB 重建 → 调统一压缩入口 → 回填 Redis(冷启动 / 过期)

        Redis 故障时自动降级走 DB 路径,不阻塞主流程。
        """
        from services.handlers import conversation_cache
        from services.handlers.context_compressor import compress_messages_if_needed

        org_id = getattr(self, "org_id", None)

        # 1. Redis hit 直接用
        cached = await conversation_cache.get_messages(conversation_id, org_id)
        if cached is not None:
            return cached

        # 2. Redis miss → DB 重建(history_loader 已删 budget break,返回完整)
        messages = await build_context_messages(
            self.db, conversation_id, current_text,
        )
        if not messages:
            return messages

        # 3. 重建后调统一压缩入口(防大 file_analyze 历史撑爆下游)
        # 注:DB 重建是冷启动路径,默认 web 压缩策略(大预算容量触发)
        # wecom 路径在主流程内已被层 4/5/6 压缩,cache miss 后 messages 已是压缩态
        try:
            messages, state = await compress_messages_if_needed(
                messages, conv_source="web",
            )
            if state != "NORMAL":
                logger.debug(
                    f"DB rebuild compressed | conv={conversation_id} | state={state}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"compress on rebuild failed | conv={conversation_id} | {e}")

        # 4. 回填 cache(下次直接 hit)
        await conversation_cache.set_messages(conversation_id, messages, org_id)

        return messages

    async def _get_context_summary(
        self, conversation_id: str, prefetched: Optional[str] = None
    ) -> Optional[str]:
        """读取已存的对话摘要 —— 委托 summary_manager."""
        return await get_context_summary(self.db, conversation_id, prefetched)

    async def _update_summary_if_needed(
        self, conversation_id: str
    ) -> None:
        """检查并更新对话摘要（fire-and-forget）—— 委托 summary_manager."""
        await update_summary_if_needed(self.db, conversation_id)
