"""Knowledge tool handler shared by the synchronous tool executor."""

from __future__ import annotations

from typing import Any, Dict

from services.agent.agent_result import AgentResult


class KnowledgeToolMixin:
    """Execute knowledge searches within the current tenant scope."""

    db: Any
    org_id: str | None

    async def _search_knowledge(self, args: Dict[str, Any]) -> AgentResult:
        """查询 AI 知识库"""
        from services.knowledge_service import search_relevant

        query = args.get("query", "")
        if not query:
            return AgentResult(
                summary="查询关键词不能为空",
                status="error",
                error_message="Validation: query is required",
                metadata={"retryable": True},
            )

        items = await search_relevant(
            query=query, limit=5, org_id=self.org_id, db_source=self.db,
        )
        if not items:
            return AgentResult(
                summary=f"知识库中未找到与「{query}」相关的经验",
                status="empty",
            )

        lines = []
        for item in items:
            title = item.get("title", "")
            content = item.get("content", "")
            lines.append(f"- {title}: {content}")

        return AgentResult(summary="\n".join(lines), status="success")
