"""
Agent 上下文构建 Mixin

负责为 Agent Loop 主脑构建多模态消息：
- 当前用户消息（文本 + 图片 content blocks）
- 历史对话注入（结构化多模态格式）
- 系统提示词 + 知识库经验注入
- 文本提取工具方法

与 AgentLoop 通过 Mixin 继承组合，共享 self.db / self._settings 等属性。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from config.agent_tools import AGENT_SYSTEM_PROMPT
from schemas.message import ContentPart, FilePart, ImagePart, TextPart


class AgentContextMixin:
    """Agent 上下文构建方法集（Mixin，由 AgentLoop 继承）"""

    # ========================================
    # 当前用户消息构建
    # ========================================

    def _build_user_content(
        self, content: List[ContentPart],
    ) -> List[Dict[str, Any]]:
        """构建多模态用户消息（文本 + 图片 content blocks）"""
        blocks: List[Dict[str, Any]] = []

        # 文本部分
        text = self._extract_text(content)
        file_count = sum(1 for p in content if isinstance(p, FilePart))
        if file_count > 0:
            text = (
                f"[上下文：用户附带了{file_count}份PDF文档，"
                f"请选择支持PDF的模型]\n{text}"
            )
        if text:
            blocks.append({"type": "text", "text": text})

        # 图片部分（DB格式 → OpenAI image_url 格式）
        for part in content:
            if isinstance(part, ImagePart) and part.url:
                blocks.append({
                    "type": "image_url",
                    "image_url": {"url": part.url},
                })

        return blocks if blocks else [{"type": "text", "text": ""}]

    def _extract_text(self, content: List[ContentPart]) -> str:
        """从 ContentPart 列表提取文本"""
        return " ".join(
            part.text for part in content if isinstance(part, TextPart)
        ).strip()

    # ========================================
    # 历史对话注入
    # ========================================

    async def _get_recent_history(
        self,
    ) -> Optional[List[Dict[str, Any]]]:
        """获取最近对话历史（结构化多模态格式，含图片 content blocks）

        返回 OpenAI 兼容的消息列表，图片以 image_url block 传递，
        让主脑（qwen3.5-plus）能真正"看到"图片内容。
        """
        assert self._settings is not None
        try:
            from services.message_service import MessageService

            limit = self._settings.agent_loop_brain_context_limit
            max_chars = self._settings.agent_loop_brain_context_max_chars
            max_images = self._settings.agent_loop_brain_max_images
            service = MessageService(self.db)
            result = await service.get_messages(
                conversation_id=self.conversation_id,
                user_id=self.user_id,
                limit=limit,
            )

            messages = result.get("messages", [])
            if not messages:
                return None

            history_msgs: List[Dict[str, Any]] = []
            total_chars = 0
            total_images = 0

            for msg in reversed(messages):  # 从旧到新
                role = msg.get("role", "unknown")
                content_parts = msg.get("content", [])
                blocks: List[Dict[str, Any]] = []

                for part in content_parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text = part.get("text", "")
                        if text:
                            blocks.append({"type": "text", "text": text})
                            total_chars += len(text)
                    elif (
                        part.get("type") == "image"
                        and total_images < max_images
                    ):
                        url = part.get("url", "")
                        if url:
                            blocks.append({
                                "type": "image_url",
                                "image_url": {"url": url},
                            })
                            total_images += 1

                if total_chars > max_chars:
                    break

                if not blocks:
                    continue

                msg_role = "user" if role == "user" else "assistant"
                # assistant 角色不支持 image_url blocks
                if msg_role == "assistant":
                    blocks = [
                        b for b in blocks if b.get("type") == "text"
                    ]
                if blocks:
                    history_msgs.append({
                        "role": msg_role, "content": blocks,
                    })

            if history_msgs:
                logger.debug(
                    f"Agent history | conv={self.conversation_id} "
                    f"| msgs={len(history_msgs)} "
                    f"| chars={total_chars} | images={total_images}"
                )
            return history_msgs if history_msgs else None
        except Exception as e:
            logger.debug(
                f"Agent history injection skipped | error={e}"
            )
            return None

    # ========================================
    # 系统提示词
    # ========================================

    async def _build_system_prompt(
        self, content: List[ContentPart],
    ) -> str:
        """Agent 系统提示词 + 知识库经验注入"""
        base_prompt = AGENT_SYSTEM_PROMPT

        text = self._extract_text(content)
        if not text:
            return base_prompt

        try:
            from services.knowledge_service import search_relevant

            items = await search_relevant(query=text, limit=3)
            if items:
                knowledge_text = "\n".join(
                    f"- {k['title']}: {k['content']}" for k in items
                )
                return (
                    base_prompt
                    + f"\n\n你已掌握的经验知识：\n{knowledge_text}"
                )
        except Exception as e:
            logger.debug(
                f"Agent knowledge injection skipped | error={e}"
            )

        return base_prompt
