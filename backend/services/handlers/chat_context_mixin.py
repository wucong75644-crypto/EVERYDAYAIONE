"""
Chat 上下文构建 Mixin

负责 LLM 消息组装：记忆注入、搜索上下文、对话历史、路由人设。
供 ChatHandler 混入使用。
"""

import json
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

        # 当前用户消息
        messages = [{"role": "user", "content": text_content}]
        if image_urls:
            messages[0]["content"] = [
                {"type": "text", "text": text_content},
                *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
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

        # 对话上下文注入（失败不影响主流程）
        context_messages = self._build_context_messages(
            conversation_id, text_content
        )
        if context_messages:
            pos = len(messages) - 1
            for i, ctx_msg in enumerate(context_messages):
                messages.insert(pos + i, ctx_msg)

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

    def _build_context_messages(
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
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

            if not result.data:
                return []

            # 反转为正序（旧→新），过滤 role
            rows = [
                r for r in reversed(result.data)
                if r.get("role") in ("user", "assistant")
            ]

            context = []
            for row in rows:
                text = self._extract_text_from_content(row.get("content"))
                if text:
                    context.append({"role": row["role"], "content": text})

            # 去除末尾与当前消息重复的 user 消息
            if (
                context
                and context[-1]["role"] == "user"
                and context[-1]["content"].strip() == current_text.strip()
            ):
                context.pop()

            if context:
                logger.debug(
                    f"Context injected | conversation_id={conversation_id} | "
                    f"count={len(context)}"
                )

            return context

        except Exception as e:
            logger.warning(
                f"Context injection failed, skipping | "
                f"conversation_id={conversation_id} | error={e}"
            )
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
