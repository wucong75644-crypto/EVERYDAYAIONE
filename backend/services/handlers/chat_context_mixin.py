"""
Chat 上下文构建 Mixin

负责 LLM 消息组装：记忆注入、搜索上下文、对话历史、路由人设。
供 ChatHandler 混入使用。
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart
from services.websocket_manager import ws_manager


class ChatContextMixin:
    """Chat 上下文构建能力：记忆、搜索、历史、消息组装"""

    async def _build_llm_messages(
        self,
        content: List[ContentPart],
        user_id: str,
        conversation_id: str,
        text_content: str,
        router_system_prompt: Optional[str] = None,
        router_search_context: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """组装发送给 LLM 的完整消息列表"""
        image_urls = self._extract_image_urls(content)
        file_urls = self._extract_file_urls(content)

        # 当前用户消息
        messages = [{"role": "user", "content": text_content}]
        if image_urls or file_urls:
            # 图片和文件统一用 image_url 格式（Gemini 通过 MIME 自动识别 PDF）
            media_parts = [
                *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
                *[{"type": "image_url", "image_url": {"url": url}} for url in file_urls],
            ]
            messages[0]["content"] = [
                {"type": "text", "text": text_content},
                *media_parts,
            ]

        # 搜索上下文注入（作为 system prompt，让工作模型基于搜索结果回答）
        if router_search_context:
            search_prompt = f"以下是联网搜索结果，请基于这些信息回答用户问题：\n\n{router_search_context}"
            messages.insert(0, {"role": "system", "content": search_prompt})
            logger.debug(f"Search context injected | len={len(router_search_context)}")

        # 记忆注入（失败不影响主流程）
        memory_prompt = await self._build_memory_prompt(user_id, text_content)
        if memory_prompt:
            messages.insert(0, {"role": "system", "content": memory_prompt})

        # 路由人设注入（插在最前面，优先级最低）
        if router_system_prompt:
            messages.insert(0, {"role": "system", "content": router_system_prompt})
            logger.debug(f"Router system_prompt injected | len={len(router_system_prompt)}")

        # 当前日期时间注入（让模型知道"今天"是哪天）
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
        messages.insert(0, {"role": "system", "content": f"当前时间：{now_str}"})

        # 思考语言指令（让推理模型的 thinking 过程使用中文）
        messages.insert(
            0,
            {"role": "system", "content": "请使用中文进行思考和推理。"},
        )

        # 对话历史摘要注入（覆盖 20 条之前的消息，失败不影响主流程）
        summary_prompt = await self._get_context_summary(conversation_id)
        if summary_prompt:
            messages.insert(0, {"role": "system", "content": summary_prompt})

        # 对话上下文注入（失败不影响主流程）
        context_messages = await self._build_context_messages(
            conversation_id, text_content
        )
        if context_messages:
            pos = len(messages) - 1
            for i, ctx_msg in enumerate(context_messages):
                messages.insert(pos + i, ctx_msg)

            # 话题聚焦指令（紧贴用户消息前，防止旧话题污染新问题）
            focus_prompt = (
                "回答时只关注用户的最新问题。"
                "如果对话中途切换了话题，以最新话题为准，不要受之前话题的影响。"
            )
            messages.insert(
                len(messages) - 1,
                {"role": "system", "content": focus_prompt},
            )

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
                user_id, query
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
                user_id, messages, conversation_id
            )

            if extracted:
                await ws_manager.send_to_user(user_id, {
                    "type": "memory_extracted",
                    "data": {
                        "memories": extracted,
                        "count": len(extracted),
                    },
                })
        except Exception as e:
            logger.warning(
                f"Memory extraction failed | user_id={user_id} | "
                f"conversation_id={conversation_id} | error={e}"
            )

    async def _build_context_messages(
        self, conversation_id: str, current_text: str
    ) -> List[Dict[str, Any]]:
        """获取对话历史并构建纯文本上下文（失败时降级为空）"""
        try:
            from core.config import settings

            limit = settings.chat_context_limit
            if limit <= 0:
                return []

            result = (
                self.db.table("messages")
                .select("role, content, status, created_at")
                .eq("conversation_id", conversation_id)
                .eq("status", "completed")
                .in_("role", ["user", "assistant"])
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

            if not result.data:
                return []

            # 从新→旧遍历，优先保留最近消息（防止旧长回复吃光字符预算）
            context = []
            total_chars = 0
            max_chars = settings.chat_context_max_chars
            for row in result.data:  # 已按 created_at DESC 排序
                text = self._extract_text_from_content(row.get("content"))
                if not text:
                    continue
                total_chars += len(text)
                if total_chars > max_chars:
                    break
                context.append({"role": row["role"], "content": text})

            # 反转为正序（旧→新），LLM 需要按时间顺序读取
            context.reverse()

            # 去除末尾与当前消息重复的 user 消息
            if context and context[-1]["role"] == "user":
                tail = self._extract_text_from_content(
                    context[-1]["content"]
                )
                if tail.strip() == current_text.strip():
                    context.pop()

            if context:
                logger.debug(
                    f"Context injected | conversation_id={conversation_id} "
                    f"| count={len(context)} | chars={total_chars}"
                )

            return context

        except Exception as e:
            logger.warning(
                f"Context injection failed, skipping | "
                f"conversation_id={conversation_id} | error={e}"
            )
            return []

    async def _get_context_summary(
        self, conversation_id: str
    ) -> Optional[str]:
        """获取已缓存的对话摘要（失败返回 None）"""
        try:
            from core.config import settings

            if not settings.context_summary_enabled:
                return None

            result = (
                self.db.table("conversations")
                .select("context_summary")
                .eq("id", conversation_id)
                .single()
                .execute()
            )

            if not result.data:
                return None

            summary = result.data.get("context_summary")
            if not summary:
                return None

            logger.debug(
                f"Context summary injected | "
                f"conversation_id={conversation_id} | len={len(summary)}"
            )
            return f"以下是之前对话的摘要（供参考）：\n{summary}"

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

            # 查询对话信息
            conv_result = (
                self.db.table("conversations")
                .select("message_count, summary_message_count")
                .eq("id", conversation_id)
                .single()
                .execute()
            )

            if not conv_result.data:
                return

            message_count = conv_result.data.get("message_count", 0)
            summary_count = conv_result.data.get("summary_message_count", 0)
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

            # 调用压缩服务
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
