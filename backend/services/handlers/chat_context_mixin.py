"""
Chat 上下文构建 Mixin

负责 LLM 消息组装：记忆注入、搜索上下文、对话历史、路由人设。
供 ChatHandler 混入使用。

Phase 1-6 上下文工程重构。设计文档：docs/document/TECH_上下文工程重构.md
Phase 7: 知识库 similarity 分数门控（替代正则排除）
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart
from services.websocket_manager import ws_manager
from utils.time_context import RequestContext


# ============================================================
# Phase 7: 知识库注入 — similarity 分数门控
# 替代旧的 Phase 6 正则排除（_should_skip_knowledge）
# 原理：向量相似度本身就是最好的相关性判断，闲聊/创作自然匹配不到高分知识
# ============================================================

# 高相关：全量注入（该类别所有命中结果）
_KB_SIMILARITY_HIGH = 0.7
# 中等相关：最多注入 1 条（防止边缘噪声堆积）
_KB_SIMILARITY_MID = 0.5
# 低于 _KB_SIMILARITY_MID 的结果直接丢弃（SQL 层 threshold=0.5 已做粗筛，
# 这里是注入层的二次过滤，阈值一致意味着 SQL 返回的最低分刚好卡在边界）


class ChatContextMixin:
    """Chat 上下文构建能力：记忆、搜索、历史、消息组装"""

    @staticmethod
    def _filter_knowledge_by_similarity(
        items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """按 similarity 分数过滤知识条目（替代旧的正则排除）

        策略：
        - ≥ _KB_SIMILARITY_HIGH (0.7)：全量保留
        - _KB_SIMILARITY_MID ~ HIGH (0.5~0.7)：最多保留 1 条
        - < _KB_SIMILARITY_MID (0.5)：丢弃

        向量相似度本身就是最好的相关性判断——闲聊/创作自然匹配不到
        高分知识，不需要额外的正则排除集。
        """
        high = [k for k in items if k.get("similarity", 1.0) >= _KB_SIMILARITY_HIGH]
        mid = [k for k in items if _KB_SIMILARITY_MID <= k.get("similarity", 1.0) < _KB_SIMILARITY_HIGH]
        filtered = high + mid[:1]
        if filtered:
            logger.debug(
                f"Knowledge similarity filter | "
                f"input={len(items)} | high={len(high)} | mid={len(mid)} | "
                f"output={len(filtered)} | "
                f"scores={[round(k.get('similarity', 1.0), 3) for k in items]}"
            )
        return filtered

    @staticmethod
    def _format_attachments(workspace_files: List[Dict[str, Any]]) -> str:
        """把附件信息结构化追加到用户消息文本里。

        LLM 在用户消息中直接看到：有什么文件、多大、什么类型。
        """
        if not workspace_files:
            return ""

        def _fmt_size(size) -> str:
            if not size:
                return ""
            size = int(size)
            if size < 1024:
                return f"{size}B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f}KB"
            return f"{size / (1024 * 1024):.1f}MB"

        _DATA_EXTS = {".xlsx", ".xls", ".csv", ".tsv"}
        _IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

        lines = ["\n\n[附件]"]
        for f in workspace_files:
            name = f.get("name", f.get("workspace_path", ""))
            size_str = _fmt_size(f.get("size"))
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext in _DATA_EXTS:
                lines.append(f"  {name} ({size_str}) — 数据文件")
            elif ext in _IMG_EXTS:
                lines.append(f"  {name} ({size_str}) — 图片")
            elif ext == ".pdf":
                lines.append(f"  {name} ({size_str}) — PDF文档")
            elif ext in {".docx", ".doc"}:
                lines.append(f"  {name} ({size_str}) — Word文档")
            else:
                lines.append(f"  {name} ({size_str})")
        return "\n".join(lines)

    @staticmethod
    def _build_workspace_prompt(workspace_files: List[Dict[str, Any]]) -> str:
        """生成工作区文件提示——告知文件名，引导数据文件用 file_analyze。"""
        if not workspace_files:
            return ""

        def _fmt_size(size) -> str:
            if not size:
                return ""
            size = int(size)
            if size < 1024:
                return f"{size} B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            return f"{size / (1024 * 1024):.1f} MB"

        lines: list[str] = ["用户当前消息附加的文件："]
        for f in workspace_files:
            wp = f.get("workspace_path", "")
            size_str = _fmt_size(f.get("size"))
            name = f.get("name", wp)
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext in {".xlsx", ".xls", ".csv", ".tsv"}:
                lines.append(f"  '{name}'  ({size_str}) — 数据文件，用 file_analyze 读取")
            else:
                lines.append(f"  '{name}'  ({size_str})")

        return "\n".join(lines)

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
        # 把附件信息结构化追加到用户文本里，LLM 直接在用户消息中看到文件
        _user_text = text_content
        if workspace_files:
            _user_text += self._format_attachments(workspace_files)
        if image_urls:
            _user_text += f"\n\n[图片] 已注入视觉理解（{len(image_urls)}张）"

        user_msg: Dict[str, Any] = {"role": "user", "content": _user_text}
        if image_urls or file_urls:
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
        """基于 token 预算加载对话历史（含图片，失败时降级为空）

        Phase 1 重写：替代旧的固定 10 条滑窗，改为 token 预算驱动。
        - token 没满 → 尽可能多加载历史
        - token 满了 → 才停止加载
        - 分批查 DB（每批 20 条），短对话只查一次
        设计文档：docs/document/TECH_上下文工程重构.md §四
        """
        try:
            import re
            from core.config import settings
            from utils.time_context import _parse_iso_to_cn

            budget = settings.context_history_token_budget  # 8000
            max_images = settings.chat_context_max_images   # 5
            BATCH_SIZE = 20
            MAX_BATCHES = 5  # 安全上限 5×20=100 条

            context: List[Dict[str, Any]] = []
            total_tokens = 0
            total_images = 0
            offset = 0
            has_more = True
            batch_count = 0

            while has_more and total_tokens < budget and batch_count < MAX_BATCHES:
                batch_count += 1
                result = (
                    self.db.table("messages")
                    # NOTE: 加载 generation_params 用于提取 tool_digest（跨轮上下文补全）
                    .select("role, content, status, created_at, generation_params")
                    .eq("conversation_id", conversation_id)
                    .eq("status", "completed")
                    .in_("role", ["user", "assistant"])
                    .order("created_at", desc=True)
                    .range(offset, offset + BATCH_SIZE - 1)
                    .execute()
                )
                if not result.data or len(result.data) == 0:
                    break
                has_more = len(result.data) == BATCH_SIZE
                offset += BATCH_SIZE

                budget_exhausted = False
                for row in result.data:  # DESC 排序，最新在前
                    raw_content = row.get("content")
                    role = row["role"]
                    images = (
                        self._extract_image_urls_from_content(raw_content)
                        if total_images < max_images
                        else []
                    )

                    # 时间戳前缀 — 让模型区分历史消息日期，防止旧"今天"污染当前请求
                    ts_prefix = ""
                    if row.get("created_at"):
                        msg_time = _parse_iso_to_cn(row["created_at"])
                        if msg_time:
                            ts_prefix = f"[{msg_time.strftime('%m-%d %H:%M')}] "

                    # Step 4 结构化：把 block list 拆成多条 OpenAI 标准消息
                    # （tool_step → assistant.tool_calls + role=tool 配对）
                    oai_msgs = self._extract_oai_messages_from_content(
                        raw_content, role=role, ts_prefix=ts_prefix,
                    )

                    # 图片处理：只有 user 消息可以发 image_url，assistant 转占位符
                    remaining = max_images - total_images
                    if images and remaining > 0:
                        images = images[:remaining]
                        if role == "user":
                            # 把第一条 user 文本消息升级为多模态 parts
                            text_msg_idx = next(
                                (i for i, m in enumerate(oai_msgs)
                                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                                None,
                            )
                            text_value = oai_msgs[text_msg_idx]["content"] if text_msg_idx is not None else ts_prefix
                            parts: List[Dict[str, Any]] = []
                            if text_value:
                                parts.append({"type": "text", "text": text_value})
                            for url in images:
                                parts.append({"type": "image_url", "image_url": {"url": url}})
                            if text_msg_idx is not None:
                                oai_msgs[text_msg_idx] = {"role": "user", "content": parts}
                            else:
                                oai_msgs.insert(0, {"role": "user", "content": parts})
                        else:
                            # assistant 图片 → 文本占位符（LLM API 不接受 assistant 的 image_url）
                            img_hint = "".join("\n📊 [已生成图表]" for _ in images)
                            target_idx = next(
                                (i for i in range(len(oai_msgs) - 1, -1, -1)
                                 if oai_msgs[i].get("role") == "assistant"
                                 and isinstance(oai_msgs[i].get("content"), str)),
                                None,
                            )
                            if target_idx is not None:
                                oai_msgs[target_idx]["content"] = (
                                    oai_msgs[target_idx]["content"] + img_hint
                                )
                            else:
                                oai_msgs.append({
                                    "role": "assistant",
                                    "content": f"{ts_prefix}{img_hint.lstrip()}",
                                })
                        total_images += len(images)

                    if not oai_msgs:
                        continue

                    # 估算这批 OAI 消息的总 token 数
                    msg_chars = 0
                    for m in oai_msgs:
                        c = m.get("content")
                        if isinstance(c, str):
                            msg_chars += len(c)
                        elif isinstance(c, list):
                            msg_chars += sum(
                                len(p.get("text", "")) for p in c
                                if isinstance(p, dict) and p.get("type") == "text"
                            )
                        for tc in (m.get("tool_calls") or []):
                            msg_chars += len(tc.get("function", {}).get("arguments", ""))
                    msg_tokens = int(msg_chars / 2.5)
                    if total_tokens + msg_tokens > budget:
                        budget_exhausted = True
                        break  # 预算用完

                    context.extend(oai_msgs)
                    total_tokens += msg_tokens

                    # 注入工具执行摘要（让 LLM 知道上轮做了什么、数据在哪）
                    # 追加到这一轮最后一条 assistant text 消息（不是 tool 消息）
                    if role == "assistant" and oai_msgs:
                        gen_params = row.get("generation_params") or {}
                        digest = gen_params.get("tool_digest") if isinstance(gen_params, dict) else None
                        if digest:
                            from services.handlers.tool_digest import format_tool_digest
                            annotation = format_tool_digest(digest)
                            if annotation:
                                target = None
                                for m in reversed(context):
                                    if m.get("role") != "assistant":
                                        continue
                                    if m.get("tool_calls"):
                                        continue
                                    if isinstance(m.get("content"), (str, list)):
                                        target = m
                                        break
                                if target is not None:
                                    if isinstance(target["content"], str):
                                        target["content"] += annotation
                                    elif isinstance(target["content"], list):
                                        target["content"].append({"type": "text", "text": annotation})

                if budget_exhausted:
                    break  # 跳出外层 while

            # 反转为正序（旧→新），LLM 需要按时间顺序读取
            context.reverse()

            # 去除末尾与当前消息重复的 user 消息
            if context and context[-1]["role"] == "user":
                tail_content = context[-1]["content"]
                tail = (
                    self._extract_text_from_content(tail_content)
                    if isinstance(tail_content, list)
                    else tail_content
                )
                # 剥掉时间戳前缀 [MM-DD HH:MM] 后再比较
                tail_stripped = re.sub(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] ", "", tail).strip()
                if tail_stripped == current_text.strip():
                    context.pop()

            if context:
                logger.debug(
                    f"Context injected | conversation_id={conversation_id} "
                    f"| count={len(context)} | tokens={total_tokens} "
                    f"| budget={budget} | images={total_images}"
                )

            return context

        except Exception as e:
            logger.warning(
                f"Context injection failed, skipping | "
                f"conversation_id={conversation_id} | error={e}"
            )
            return []

    async def _get_context_summary(
        self, conversation_id: str, prefetched: Optional[str] = None
    ) -> Optional[str]:
        """获取已缓存的对话摘要（失败返回 None）

        Args:
            conversation_id: 对话 ID
            prefetched: HTTP 阶段预取的 context_summary（有值时跳过 DB 查询）
        """
        try:
            from core.config import settings

            if not settings.context_summary_enabled:
                return None

            # 优先使用预取值（HTTP 阶段 get_conversation 已查过同一行）
            summary_updated = None
            if prefetched is not None:
                summary = prefetched
            else:
                result = (
                    self.db.table("conversations")
                    .select("context_summary, updated_at")
                    .eq("id", conversation_id)
                    .single()
                    .execute()
                )

                if not result.data:
                    return None

                summary = result.data.get("context_summary")
                summary_updated = result.data.get("updated_at")
            if not summary:
                return None

            # 标注摘要生成时间，防止模型误将旧摘要当最新数据
            from utils.time_context import _parse_iso_to_cn
            ts_label = ""
            if summary_updated:
                ts = _parse_iso_to_cn(summary_updated)
                if ts:
                    ts_label = f"（生成于 {ts.strftime('%m-%d %H:%M')}，可能不是最新数据）"

            logger.debug(
                f"Context summary injected | "
                f"conversation_id={conversation_id} | len={len(summary)}"
            )
            return f"以下是之前对话的摘要{ts_label}：\n{summary}"

        except Exception as e:
            logger.warning(
                f"Context summary fetch failed, skipping | "
                f"conversation_id={conversation_id} | error={e}"
            )
            return None

    async def _update_summary_if_needed(
        self, conversation_id: str
    ) -> None:
        """检查并更新对话摘要（fire-and-forget，失败不影响主流程）"""
        try:
            from core.config import settings

            if not settings.context_summary_enabled:
                return

            # 查询对话信息（含已有摘要，一次查完）
            conv_result = (
                self.db.table("conversations")
                .select("message_count, summary_message_count, context_summary")
                .eq("id", conversation_id)
                .single()
                .execute()
            )

            if not conv_result.data:
                return

            message_count = conv_result.data.get("message_count", 0)
            summary_count = conv_result.data.get("summary_message_count", 0)
            existing_summary: Optional[str] = conv_result.data.get("context_summary")
            context_limit = settings.chat_context_limit

            # 不需要摘要（≤20 条消息）
            if message_count <= context_limit:
                return

            # 已有摘要且不需要更新（新增消息 < update_interval）
            if summary_count > 0 and (message_count - summary_count) < settings.context_summary_update_interval:
                return

            # 获取所有已完成的 user/assistant 消息（按时间正序）
            all_result = (
                self.db.table("messages")
                .select("role, content")
                .eq("conversation_id", conversation_id)
                .eq("status", "completed")
                .in_("role", ["user", "assistant"])
                .order("created_at", desc=False)
                .execute()
            )

            if not all_result.data:
                return

            all_msgs = all_result.data

            # 取除最近 N 条之外的消息进行压缩
            if len(all_msgs) <= context_limit:
                return

            msgs_to_summarize = all_msgs[:-context_limit]

            # 提取纯文本
            text_messages = []
            for msg in msgs_to_summarize:
                text = self._extract_text_from_content(msg.get("content"))
                if text:
                    text_messages.append(
                        {"role": msg["role"], "content": text}
                    )

            if not text_messages:
                return

            # 增量路径：有旧摘要时只传新增消息（对标 Claude PARTIAL_COMPACT_PROMPT）
            summary = None
            if existing_summary and summary_count > 0:
                from services.context_summarizer import update_summary

                # new_total = 新增消息数（所有角色），用作 msgs_to_summarize 尾部切片上界
                # 偏大（含 tool/system）无害——LLM 增量 prompt 会自动去重
                new_total = message_count - summary_count
                if new_total > 0:
                    new_slice = msgs_to_summarize[-new_total:] if new_total < len(msgs_to_summarize) else msgs_to_summarize
                    new_text_messages = []
                    for msg in new_slice:
                        text = self._extract_text_from_content(msg.get("content"))
                        if text:
                            new_text_messages.append({"role": msg["role"], "content": text})
                    if new_text_messages:
                        summary = await update_summary(existing_summary, new_text_messages)
                        if summary:
                            logger.info(
                                f"Context summary incremental update | "
                                f"conversation_id={conversation_id} | "
                                f"new_msgs={len(new_text_messages)}"
                            )

            # 全量降级：增量失败或无旧摘要
            if not summary:
                from services.context_summarizer import summarize_messages
                summary = await summarize_messages(text_messages)

            if summary:
                self.db.table("conversations").update({
                    "context_summary": summary,
                    "summary_message_count": message_count,
                }).eq("id", conversation_id).execute()

                logger.info(
                    f"Context summary updated | "
                    f"conversation_id={conversation_id} | "
                    f"message_count={message_count} | "
                    f"compressed={len(msgs_to_summarize)} msgs | "
                    f"summary_len={len(summary)}"
                )

        except Exception as e:
            logger.warning(
                f"Context summary update failed | "
                f"conversation_id={conversation_id} | error={e}"
            )

    def _extract_image_urls_from_content(self, content: Any) -> List[str]:
        """从 DB content 字段提取图片 URL 列表"""
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return self._extract_image_urls_from_content(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            return []
        if isinstance(content, list):
            return [
                part["url"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "image"
                and part.get("url")
            ]
        return []

    def _extract_text_from_content(self, content: Any) -> str:
        """从 DB content 字段提取纯文本，跳过图片/视频 URL"""
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return self._extract_text_from_content(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            return content.strip()
        if isinstance(content, list):
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text", "").strip()
                    if text:
                        texts.append(text)
            return " ".join(texts)
        return ""

    # ============================================================
    # OpenAI 标准协议序列化（Step 4 结构化历史）
    # ============================================================
    @staticmethod
    def _extract_oai_messages_from_content(
        content: Any,
        role: str,
        ts_prefix: str = "",
    ) -> List[Dict[str, Any]]:
        """把一条 DB 消息的 content blocks 拆成多条 OpenAI 标准消息。

        DB 里 content 是结构化的 block 列表（text / thinking / tool_step / ...），
        旧的 _extract_text_from_content 会把它们压成一段 plain text 注入 history，
        导致 LLM 把代码当模板复用 + 工具调用细节丢失（跨轮失忆）。

        本方法按 block 顺序展开为标准 OAI 消息：
          - text         → {role: <user/assistant>, content: "<ts_prefix><text>"}
          - thinking     → 跳过（不发回 LLM）
          - tool_step (completed/error)
                         → {role: "assistant", tool_calls: [{id, function: {name, arguments}}]}
                         → {role: "tool", tool_call_id, content: "<output>"}
          - tool_step (running) → 跳过（未完成无意义）
          - tool_result  → 退化为 assistant content（无 tool_call_id 无法配对）

        所有 tool_step 的 input/code 优先级：input(JSON) > {code: ...}
        ts_prefix 仅注入到首个 text/assistant 消息上（避免重复噪声）。
        """
        msgs: List[Dict[str, Any]] = []

        # 解析 raw content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    blocks = parsed
                else:
                    text = content.strip()
                    if text:
                        msgs.append({"role": role, "content": f"{ts_prefix}{text}"})
                    return msgs
            except (json.JSONDecodeError, TypeError):
                text = content.strip()
                if text:
                    msgs.append({"role": role, "content": f"{ts_prefix}{text}"})
                return msgs
        elif isinstance(content, list):
            blocks = content
        else:
            return msgs

        ts_consumed = False  # ts_prefix 只用一次，避免每个 text block 重复
        for part in blocks:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")

            if ptype == "text":
                text = (part.get("text") or "").strip()
                if not text:
                    continue
                prefix = "" if ts_consumed else ts_prefix
                ts_consumed = True
                msgs.append({"role": role, "content": f"{prefix}{text}"})

            elif ptype == "thinking":
                # thinking 是推理过程，不应回灌给 LLM
                continue

            elif ptype == "tool_step":
                status = part.get("status")
                if status not in ("completed", "error"):
                    continue
                tool_name = part.get("tool_name") or "unknown"
                tool_call_id = part.get("tool_call_id") or ""
                if not tool_call_id:
                    # 历史数据兜底：生成稳定 id
                    import hashlib
                    seed = f"{tool_name}|{part.get('input') or part.get('code') or ''}|{len(msgs)}"
                    tool_call_id = "call_" + hashlib.md5(seed.encode()).hexdigest()[:24]
                arguments = part.get("input") or ""
                if not arguments and part.get("code"):
                    arguments = json.dumps({"code": part["code"]}, ensure_ascii=False)
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)

                # assistant 消息携带 tool_calls
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": arguments,
                        },
                    }],
                })
                # 紧跟的 tool 消息（OpenAI 协议要求配对）
                output = part.get("output") or ""
                if status == "error" and not output:
                    output = "[工具执行失败]"
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": output,
                })

            elif ptype == "tool_result":
                # 独立的 tool_result block 没有 tool_call_id，无法配对到 assistant.tool_calls
                # 退化为 assistant content（保留信息但不进入工具协议链）
                text = (part.get("text") or "").strip()
                if text:
                    tool_name = part.get("tool_name") or ""
                    prefix = "" if ts_consumed else ts_prefix
                    ts_consumed = True
                    msgs.append({
                        "role": "assistant",
                        "content": f"{prefix}[工具结论: {tool_name}] {text}",
                    })

            # 其他类型（image/video/audio/file/form/chart/...）由上层 _build_context_messages
            # 单独处理为多模态格式，这里不动

        return msgs
