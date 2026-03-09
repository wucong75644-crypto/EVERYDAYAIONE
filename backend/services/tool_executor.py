"""
同步工具执行器

执行 Agent Loop 中的同步工具（结果回传大脑）。
异常不在此处 catch — 调用方（AgentLoop）统一处理并回传大脑。

工具列表：
- web_search: 搜索互联网（复用 IntentRouter.execute_search）
- get_conversation_context: 获取近期对话（复用 MessageService）
- search_knowledge: 查询知识库（复用 knowledge_service）
"""

from typing import Any, Callable, Coroutine, Dict

from loguru import logger
from supabase import Client


class ToolExecutor:
    """同步工具执行器"""

    def __init__(self, db: Client, user_id: str, conversation_id: str) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self._handlers: Dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
            "web_search": self._web_search,
            "get_conversation_context": self._get_conversation_context,
            "search_knowledge": self._search_knowledge,
        }

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """执行同步工具，返回结果文本

        Raises:
            ValueError: 未知工具名
            Exception: 工具执行异常（由调用方 catch 后回传大脑）
        """
        handler = self._handlers.get(tool_name)
        if not handler:
            raise ValueError(f"Unknown sync tool: {tool_name}")
        return await handler(arguments)

    # ========================================
    # 工具实现
    # ========================================

    async def _web_search(self, args: Dict[str, Any]) -> str:
        """搜索互联网（复用 IntentRouter.execute_search）"""
        from services.intent_router import IntentRouter

        query = args.get("search_query", "")
        if not query:
            return "搜索查询不能为空"

        router = IntentRouter()
        try:
            result = await router.execute_search(
                query=query,
                user_text=query,
                system_prompt=None,
            )
            if result:
                logger.info(f"ToolExecutor web_search | query={query} | len={len(result)}")
                return result
            return f"搜索「{query}」未找到相关结果"
        finally:
            await router.close()

    async def _get_conversation_context(self, args: Dict[str, Any]) -> str:
        """获取近期对话记录（含图片 URL）"""
        from services.message_service import MessageService

        limit = min(args.get("limit", 10), 20)

        service = MessageService(self.db)
        result = await service.get_messages(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            limit=limit,
        )

        messages = result.get("messages", [])
        if not messages:
            return "当前对话暂无历史消息"

        # 格式化为大脑可读的文本
        lines = []
        for msg in reversed(messages):  # 从旧到新
            role = msg.get("role", "unknown")
            content_parts = msg.get("content", [])
            text_parts = []
            image_urls = []

            for part in content_parts:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image":
                        url = part.get("url", "")
                        if url:
                            image_urls.append(url)

            line = f"[{role}] {' '.join(text_parts)}"
            if image_urls:
                line += f" [图片: {', '.join(image_urls)}]"
            lines.append(line)

        return "\n".join(lines)

    async def _search_knowledge(self, args: Dict[str, Any]) -> str:
        """查询 AI 知识库"""
        from services.knowledge_service import search_relevant

        query = args.get("query", "")
        if not query:
            return "查询关键词不能为空"

        items = await search_relevant(query=query, limit=5)
        if not items:
            return f"知识库中未找到与「{query}」相关的经验"

        lines = []
        for item in items:
            title = item.get("title", "")
            content = item.get("content", "")
            lines.append(f"- {title}: {content}")

        return "\n".join(lines)
