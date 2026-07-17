"""工具执行器的会话历史读取能力。"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger


class ConversationToolMixin:
    async def _get_conversation_context(
        self,
        args: Dict[str, Any],
    ) -> str:
        """读取当前作用域内的近期对话记录。"""
        from services.message_service import MessageService

        limit = min(args.get("limit", 10), 20)
        service = MessageService(self.db)
        result = await service.get_messages(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            limit=limit,
            org_id=self.org_id,
        )
        messages = result.get("messages", [])
        if not messages:
            return "当前对话暂无历史消息"
        lines = []
        for message in reversed(messages):
            text_parts = []
            image_urls = []
            for part in message.get("content", []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image" and part.get("url"):
                    image_urls.append(part["url"])
            line = f"[{message.get('role', 'unknown')}] {' '.join(text_parts)}"
            if image_urls:
                line += f" [图片: {', '.join(image_urls)}]"
            lines.append(line)
        context_text = "\n".join(lines)
        logger.debug(
            f"get_conversation_context result | conv={self.conversation_id} "
            f"| msg_count={len(messages)} | len={len(context_text)} "
            f"| preview={context_text[:500]}"
        )
        return context_text
