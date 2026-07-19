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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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

if TYPE_CHECKING:
    from services.handlers.context_snapshot import ContextAnchor


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
        user_location: Optional[str] = None,
        permission_mode: str = "auto",
        context_anchor: Optional["ContextAnchor"] = None,
        model_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """组装发送给 LLM 的完整消息列表。

        V3.4: 统一走 PromptBuilder, 替代旧的 11 处碎片化 system append。
        设计文档: docs/document/TECH_PromptBuilder架构重构.md

        旧 11 处注入全部合并到 PromptBuilder 的 3 层结构:
          Layer 1 (静态): 角色 + 规则 + 工作流 + 工具策略 + 模式约束
          Layer 2 (动态): 时间 + 位置 + 偏好 + persona + 相关记忆
          Layer 3 (user): 附件 XML + user text (不加时间戳前缀)

        Curated Memory 查询统一由 PromptBuilder 执行。
        (session cache 在 builder._memory() 内, 单一入口避免双查)
        """
        from services.prompt_builder import PromptBuilder, BuildInput
        from core.config import get_settings

        image_urls = self._extract_image_urls(content)
        file_urls = self._extract_file_urls(content)
        workspace_files = self._extract_workspace_files(content)
        workspace_user_id = getattr(self, "_workspace_user_id", user_id)
        effective_org_id = (
            org_id if org_id is not None else getattr(self, "org_id", None)
        )
        personal_context_allowed = getattr(
            self, "_personal_context_allowed", True,
        )

        # 注册 workspace 文件到会话级路径缓存 (保留旧逻辑, PromptBuilder 不负责文件管理)
        if workspace_files:
            try:
                from services.agent.file_path_cache import get_file_cache
                from core.workspace import resolve_workspace_dir, resolve_staging_dir
                _settings = get_settings()
                _ws_dir = resolve_workspace_dir(
                    _settings.file_workspace_root,
                    workspace_user_id,
                    effective_org_id,
                )
                _cache = get_file_cache(conversation_id)
                _staging = resolve_staging_dir(
                    _settings.file_workspace_root,
                    workspace_user_id,
                    effective_org_id,
                    conversation_id,
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
        context_snapshot = None
        if context_anchor is not None:
            from services.handlers.context_snapshot import build_context_snapshot

            context_snapshot = await build_context_snapshot(
                self.db, context_anchor, text_content,
            )
            self._resource_manifest = context_snapshot.resource_manifest
            self._data_context_snapshot = context_snapshot.data_context

        inp = BuildInput(
            user_id=user_id,
            conversation_id=conversation_id,
            org_id=effective_org_id,
            text_content=text_content,
            workspace_files=workspace_files,
            image_urls=image_urls,
            file_urls=file_urls,
            permission_mode=permission_mode,
            model_id=model_id,
            user_location=user_location,
            user_preferences=None,  # TODO 阶段 4.4: 从 user_preferences 表读取
            db=self.db,
            prefetched_summary=prefetched_summary,
            context_snapshot=context_snapshot,
            request_ctx=getattr(self, "request_ctx", None),
            attachments_as_system=get_settings().messages_attachments_as_system,
            personal_context_allowed=personal_context_allowed,
        )

        builder = PromptBuilder(inp)
        result = await builder.build()
        self._pending_context_compaction = result.compaction

        logger.info(
            f"PromptBuilder done | conv={conversation_id} | "
            f"static={result.static_block_chars} | "
            f"dynamic={result.dynamic_block_chars} | "
            f"persona={result.persona_injected} | "
            f"memory={result.memory_injected} | "
            f"messages={len(result.messages)}"
        )

        return result.messages

    # V2 阶段 4.1: _build_memory_prompt 已删除
    # Curated Memory 查询统一到 PromptBuilder._parallel_fetch._memory()
    # 配合 session_memory_cache 实现"新会话首查 + 整会话固定"
    #
    # V2 阶段 6.6 (2026-06-14): _fetch_knowledge 已删除
    # 知识库召回走 LLM 主动调工具路径 (search_knowledge / erp_analyze),
    # 不再在 prompt 预注入. 按需召回优于无脑塞.

    async def _extract_memories_async(
        self,
        user_id: str,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
        input_message_id: Optional[str] = None,
        output_message_id: Optional[str] = None,
        through_revision: Optional[int] = None,
    ) -> None:
        """通知调度器按闭合 revision 执行 Session Flush。"""
        try:
            from services.memory.memory_service_v2 import get_scheduler

            scheduler = await get_scheduler(db_pool=self.db)

            await scheduler.on_turn_committed(
                user_id=user_id,
                org_id=self.org_id,
                session_id=conversation_id,
                through_revision=through_revision,
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
        """Legacy 对话历史加载；正式任务统一使用 ContextSnapshot。"""
        from services.handlers.context_compressor import compress_messages_if_needed

        messages = await build_context_messages(
            self.db, conversation_id, current_text,
        )
        if not messages:
            return messages

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

        return messages

    async def _get_context_summary(
        self, conversation_id: str, prefetched: Optional[str] = None
    ) -> Optional[str]:
        """读取已存的对话摘要 —— 委托 summary_manager."""
        return await get_context_summary(self.db, conversation_id, prefetched)

    async def _update_summary_if_needed(
        self,
        conversation_id: str,
    ) -> None:
        """检查并更新对话摘要（fire-and-forget）—— 委托 summary_manager."""
        await update_summary_if_needed(self.db, conversation_id)
