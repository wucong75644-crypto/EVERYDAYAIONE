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

        # workspace 文件的 URL 不走 image_url 通道（AI 通过 file_read 工具读取）
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
        metadata_map: dict = {}  # 不再提取，传空 dict 保持接口兼容
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

        # schema 智能过滤注入（B2）— 从对话级 registry 中筛选相关 schema
        await self._inject_schema_context(messages, conversation_id, text_content)

        # 工作区文件提示注入（只展示静态信息：文件名/大小/类型/时间/路径）
        if workspace_files:
            from services.file_metadata_extractor import format_workspace_files_prompt
            ws_prompt = format_workspace_files_prompt(workspace_files, metadata_map)
            if ws_prompt:
                messages.append({"role": "system", "content": ws_prompt})
                logger.debug(
                    f"Workspace files injected | count={len(workspace_files)} | "
                    f"metadata_count={len(metadata_map)} | "
                    f"paths={[f['workspace_path'] for f in workspace_files]}"
                )

        # Layer 4: 用户记忆（失败不影响主流程）
        if memory_prompt:
            messages.append({"role": "system", "content": memory_prompt})

        # Layer 5: 对话摘要 — Phase 6 门控：短对话不注入
        _msg_count = len(context_messages) if context_messages else 0
        if summary_prompt and _msg_count > 5:
            messages.append({"role": "system", "content": summary_prompt})

        # Layer 6: 对话历史 + 话题聚焦
        if context_messages:
            messages.extend(context_messages)
            # 话题聚焦指令（紧贴用户消息前，防止旧话题污染新问题）
            messages.append({"role": "system", "content": "以用户最新一条消息为准。"})

        # Layer 7: 用户消息（始终最后）
        user_msg: Dict[str, Any] = {"role": "user", "content": text_content}
        if image_urls or file_urls:
            media_parts = [
                *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
                *[{"type": "image_url", "image_url": {"url": url}} for url in file_urls],
            ]
            user_msg["content"] = [
                {"type": "text", "text": text_content},
                *media_parts,
            ]
        messages.append(user_msg)

        # ─── 分桶预算控制（Phase 2）───
        from core.config import get_settings
        from services.handlers.context_compressor import (
            enforce_tool_budget, enforce_history_budget, enforce_budget,
        )
        _s = get_settings()
        enforce_tool_budget(messages, _s.context_tool_token_budget)
        await enforce_history_budget(
            messages, _s.context_history_token_budget, current_query=text_content,
        )
        enforce_budget(messages, _s.context_max_tokens)

        return messages

    async def _build_memory_prompt(
        self, user_id: str, query: str
    ) -> Optional[str]:
        """构建记忆 system prompt（失败时返回 None）"""
        try:
            from services.memory_service import MemoryService
            from services.memory_config import build_memory_system_prompt

            memory_service = MemoryService(self.db)

            if not await memory_service.is_memory_enabled(user_id):
                return None

            memories = await memory_service.get_relevant_memories(
                user_id, query, org_id=self.org_id
            )
            if not memories:
                return None

            prompt = build_memory_system_prompt(memories)
            if prompt:
                logger.debug(
                    f"Memory injected | user_id={user_id} | "
                    f"memory_count={len(memories)}"
                )
            return prompt
        except Exception as e:
            logger.warning(
                f"Memory injection failed, skipping | "
                f"user_id={user_id} | error={e}"
            )
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

    async def _inject_schema_context(
        self,
        messages: List[Dict[str, Any]],
        conversation_id: str,
        query: str,
    ) -> None:
        """从对话级 registry 中筛选相关 schema 并注入为 system 消息。

        B2: schema 智能过滤注入。
        位置：Layer 3 领域知识层，知识库之后、工作区文件之前。
        """
        try:
            from services.agent.session_file_registry import get_conversation_registry
            registry = get_conversation_registry(conversation_id)
            schema_entries = registry.get_schema_entries()
            if not schema_entries:
                return

            from services.agent.schema_filter import filter_schemas
            recent_entries = registry.get_recent_schema_entries(3)
            matched = await filter_schemas(query, schema_entries, recent_entries)
            if not matched:
                return

            # 构建注入文本
            lines = ["[可用数据文件 schema]", ""]
            for key, ref, schema_text in matched:
                lines.append(f"=== {ref.filename} ===")
                lines.append(schema_text)
                lines.append("")
                # 更新 last_used
                registry.touch(key)

            schema_prompt = "\n".join(lines).rstrip()
            messages.append({"role": "system", "content": schema_prompt})
            logger.debug(
                f"Schema context injected | conv={conversation_id} | "
                f"matched={len(matched)}/{len(schema_entries)} | "
                f"files={[m[1].filename for m in matched]}"
            )
        except Exception as e:
            logger.debug(f"Schema injection skipped | error={e}")

    async def _extract_memories_async(
        self,
        user_id: str,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """异步从对话中提取记忆（fire-and-forget，短消息跳过）"""
        try:
            # 短消息无信息量，跳过提取（中文信息密度高，阈值设低）
            if len(user_text) < 10:
                return

            from services.memory_service import MemoryService

            memory_service = MemoryService(self.db)

            if not await memory_service.is_memory_enabled(user_id):
                return

            messages = [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]

            extracted = await memory_service.extract_memories_from_conversation(
                user_id, messages, conversation_id, org_id=self.org_id
            )

            if extracted:
                await ws_manager.send_to_user(user_id, {
                    "type": "memory_extracted",
                    "data": {
                        "memories": extracted,
                        "count": len(extracted),
                    },
                }, org_id=self.org_id)
        except Exception as e:
            logger.warning(
                f"Memory extraction failed | user_id={user_id} | "
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
                    text = self._extract_text_from_content(raw_content)
                    images = (
                        self._extract_image_urls_from_content(raw_content)
                        if total_images < max_images
                        else []
                    )

                    if not text and not images:
                        continue

                    # 估算这条消息的 token 数
                    msg_tokens = int(len(text) / 2.5) if text else 0
                    if total_tokens + msg_tokens > budget:
                        budget_exhausted = True
                        break  # 预算用完

                    # 限制图片数量不超过配额
                    remaining = max_images - total_images
                    if images and remaining > 0:
                        images = images[:remaining]
                        total_images += len(images)
                    else:
                        images = []

                    # 时间戳前缀 — 让模型区分历史消息日期，防止旧"今天"污染当前请求
                    ts_prefix = ""
                    if row.get("created_at"):
                        msg_time = _parse_iso_to_cn(row["created_at"])
                        if msg_time:
                            ts_prefix = f"[{msg_time.strftime('%m-%d %H:%M')}] "

                    # 有图片时用多模态格式，无图片时保持纯文本（节省 token）
                    # 注意：只有 user 消息可以发 image_url（视觉理解），
                    # assistant 消息中的图片（沙盒图表等）转为文本占位符，LLM API 不接受 assistant 的 image_url
                    if images and row["role"] == "user":
                        parts: List[Dict[str, Any]] = []
                        if text:
                            parts.append({"type": "text", "text": f"{ts_prefix}{text}"})
                        for url in images:
                            parts.append({
                                "type": "image_url",
                                "image_url": {"url": url},
                            })
                        context.append({"role": row["role"], "content": parts})
                    elif images and row["role"] == "assistant":
                        # assistant 图片转为文本占位符（LLM API 不接受 assistant 的 image_url）
                        img_hint = "".join(f"\n📊 [已生成图表]" for _ in images)
                        context.append({"role": "assistant", "content": f"{ts_prefix}{text}{img_hint}"})
                    else:
                        context.append({"role": row["role"], "content": f"{ts_prefix}{text}"})
                    total_tokens += msg_tokens

                    # 注入工具执行摘要（让 LLM 知道上轮做了什么、数据在哪）
                    if row["role"] == "assistant" and context:
                        gen_params = row.get("generation_params") or {}
                        digest = gen_params.get("tool_digest") if isinstance(gen_params, dict) else None
                        if digest:
                            from services.handlers.tool_digest import format_tool_digest
                            annotation = format_tool_digest(digest)
                            if annotation:
                                last_msg = context[-1]
                                if isinstance(last_msg["content"], str):
                                    last_msg["content"] += annotation
                                elif isinstance(last_msg["content"], list):
                                    last_msg["content"].append({"type": "text", "text": annotation})

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
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "").strip()
                    if text:
                        texts.append(text)
            return " ".join(texts)
        return ""
