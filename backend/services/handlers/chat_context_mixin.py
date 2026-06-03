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
    ) -> List[Dict[str, Any]]:
        """组装发送给 LLM 的完整消息列表。

        分层 append 模式（行业标准：OpenAI/Anthropic/LangChain）：
        Layer 1: 世界状态（时间 + 位置）
        Layer 2: 思考语言
        Layer 3: 领域知识（经验案例 + 通用知识 + schema + 工作区文件）
        Layer 4: 用户记忆
        Layer 5: 对话摘要
        Layer 6: 对话历史 + 话题聚焦
        Layer 7: 用户消息
        （后续 chat_handler._stream_generate 中 append TOOL_SYSTEM_PROMPT + 权限模式）
        """
        image_urls = self._extract_image_urls(content)
        file_urls = self._extract_file_urls(content)
        workspace_files = self._extract_workspace_files(content)

        # 注册用户提供的文件到会话级路径缓存（上传/插入/@引用三个入口统一注册）
        # 后续 LLM 用文件名引用，get_file 归一化匹配翻译成正确绝对路径
        if workspace_files:
            try:
                from services.agent.file_path_cache import get_file_cache
                from core.workspace import resolve_workspace_dir, resolve_staging_dir
                from core.config import get_settings
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

        # workspace 文件不走多模态 image_url（大部分格式不支持），由 AI 调工具读取
        if workspace_files:
            ws_urls = {f["url"] for f in workspace_files if f.get("url")}
            file_urls = [u for u in file_urls if u not in ws_urls]

        # ─── 并行获取：记忆 / 摘要 / 历史 / 知识库（全部独立，无交叉依赖）───
        if prefetched_memory is not None:
            summary_result, context_result, knowledge_result = await asyncio.gather(
                self._get_context_summary(conversation_id, prefetched=prefetched_summary),
                self._build_context_messages(conversation_id, text_content),
                self._fetch_knowledge(text_content),
                return_exceptions=True,
            )
            memory_prompt = prefetched_memory
        else:
            memory_result, summary_result, context_result, knowledge_result = await asyncio.gather(
                self._build_memory_prompt(user_id, text_content),
                self._get_context_summary(conversation_id, prefetched=prefetched_summary),
                self._build_context_messages(conversation_id, text_content),
                self._fetch_knowledge(text_content),
                return_exceptions=True,
            )
            memory_prompt = (
                memory_result if not isinstance(memory_result, BaseException) else None
            )
            if isinstance(memory_result, BaseException):
                logger.warning(f"Memory gather failed | error={memory_result}")

        # 安全解包（异常降级）
        summary_prompt = (
            summary_result if not isinstance(summary_result, BaseException) else None
        )
        context_messages = (
            context_result if not isinstance(context_result, BaseException) else []
        )
        knowledge_items = (
            knowledge_result if not isinstance(knowledge_result, BaseException) else None
        )
        if isinstance(summary_result, BaseException):
            logger.warning(f"Summary gather failed | error={summary_result}")
        if isinstance(context_result, BaseException):
            logger.warning(f"Context gather failed | error={context_result}")
        if isinstance(knowledge_result, BaseException):
            logger.debug(f"Knowledge fetch failed | error={knowledge_result}")

        # ─── 按层 append 构建 messages（禁止 insert(0)）───
        messages: List[Dict[str, Any]] = []

        # Layer 1: 世界状态（时间 + 位置，合并为一条 system message）
        # RequestContext 从入口传入（HTTP/WS/企微），全链路不可变 SSOT
        _request_ctx = getattr(self, "request_ctx", None) or RequestContext.build(
            user_id=user_id,
            org_id=getattr(self, "org_id", None),
            request_id=conversation_id or "",
        )
        world_state = _request_ctx.for_prompt_injection()
        if user_location:
            world_state += f"\n用户位置：{user_location}"
        messages.append({"role": "system", "content": world_state})

        # Layer 2: 思考语言指令（让推理模型的 thinking 过程使用中文）
        messages.append({"role": "system", "content": "请使用中文进行思考和推理。"})

        # Layer 3: 领域知识（经验案例 + 通用知识 + schema + 工作区文件）
        if knowledge_items:
            filtered_knowledge = self._filter_knowledge_by_similarity(knowledge_items)
            exp = [k for k in filtered_knowledge if k.get("_source") == "experience"]
            general = [k for k in filtered_knowledge if k.get("_source") != "experience"]

            if exp:
                exp_text = "\n".join(f"- {e['content']}" for e in exp)
                messages.append({"role": "system", "content":
                    f"以下是类似查询的历史成功案例，参考其查询方式：\n{exp_text}"})

            if general:
                knowledge_text = "\n".join(
                    f"- {k['title']}: {k['content']}" for k in general
                )
                messages.append({"role": "system", "content": f"你已掌握的经验知识：\n{knowledge_text}"})

        # 工作区文件提示注入：告诉 AI 文件名，由 AI 调工具处理
        if workspace_files:
            ws_prompt = self._build_workspace_prompt(workspace_files)
            if ws_prompt:
                messages.append({"role": "system", "content": ws_prompt})
                logger.debug(
                    f"Workspace files injected | count={len(workspace_files)} | "
                    f"paths={[f['workspace_path'] for f in workspace_files]}"
                )

        # Layer 4: 用户记忆（V2 双部分注入）
        # 4a: L3 Persona（稳定部分，放 system prompt，prompt cache 友好）
        _persona_ctx = getattr(self, "_memory_persona_context", "")
        if _persona_ctx:
            messages.append({"role": "system", "content": _persona_ctx})
        # 4b: L1 相关记忆（动态部分，暂存，稍后注入 user prompt 前面）
        _l1_memory_prepend = memory_prompt  # 来自 _build_memory_prompt 的 prepend_context

        # Layer 5: 对话摘要 — Phase 6 门控：短对话不注入
        _msg_count = len(context_messages) if context_messages else 0
        if summary_prompt and _msg_count > 5:
            messages.append({"role": "system", "content": summary_prompt})

        # Layer 6: 对话历史 + 话题聚焦
        if context_messages:
            messages.extend(context_messages)
            # 话题聚焦指令（紧贴用户消息前，防止旧话题污染新问题）
            messages.append({"role": "system", "content": "以用户最新一条消息为准。"})

        # Layer 6.5: L1 记忆前缀（动态部分，紧贴用户消息前）
        if _l1_memory_prepend:
            messages.append({"role": "system", "content": f"用户相关记忆：\n{_l1_memory_prepend}"})

        # Layer 7: 用户消息（始终最后）
        # 结构化附件元数据（XML <attachments>）追加到用户文本里；
        # 图片可视性、数据文件 analyzed 状态等信息全部由 <attachments> 块的
        # <status> 字段表达，无需额外 [图片] 文案。
        _user_text = text_content
        if workspace_files:
            _user_text += self._format_attachments(workspace_files, conversation_id)

        user_msg: Dict[str, Any] = {"role": "user", "content": _user_text}
        if image_urls or file_urls:
            # 仅 image/* 的 FilePart 会进 file_urls（_extract_file_urls 已按 mime 过滤）
            media_parts = [
                *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
                *[{"type": "image_url", "image_url": {"url": url}} for url in file_urls],
            ]
            user_msg["content"] = [
                {"type": "text", "text": _user_text},
                *media_parts,
            ]
        messages.append(user_msg)

        # ─── 分桶预算控制（按来源分流）───
        # 企微：保持小预算激进压缩
        # Web：用大预算容量触发，避免在 _build_llm_messages 阶段就丢 schema
        from core.config import get_settings
        from services.handlers.context_compressor import (
            enforce_tool_budget, enforce_history_budget, enforce_budget,
        )
        _s = get_settings()

        # ChatContextMixin 被 ChatHandler 继承，运行时 self 拥有 _get_conv_source
        # 独立使用 ChatContextMixin 时无此方法，hasattr 兜底为 Web 路径
        is_wecom = (
            hasattr(self, "_get_conv_source")
            and self._get_conv_source(conversation_id) == "wecom"
        )
        if is_wecom:
            tool_budget = _s.context_tool_token_budget
            history_budget = _s.context_history_token_budget
            total_budget = _s.context_max_tokens
        else:
            tool_budget = _s.context_web_tool_token_budget
            history_budget = _s.context_web_history_token_budget
            total_budget = _s.context_web_max_tokens

        enforce_tool_budget(messages, tool_budget)
        await enforce_history_budget(
            messages, history_budget, current_query=text_content,
        )
        enforce_budget(messages, total_budget)

        return messages

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
        """对话历史加载（token 预算驱动）—— 委托 history_loader."""
        return await build_context_messages(self.db, conversation_id, current_text)

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
