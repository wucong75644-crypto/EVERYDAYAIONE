"""Knowledge, HTTP client, and metrics helpers for IntentRouter."""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from services.intent_router import RoutingDecision


class IntentRouterRuntimeMixin:
    """Runtime helpers kept separate from routing decisions."""

    _client: httpx.AsyncClient | None

    async def _enhance_with_knowledge(
        self, text: str, org_id: str | None = None, db_source: Any = None,
    ) -> str:
        from services.intent_router import ROUTER_SYSTEM_PROMPT

        try:
            from services.knowledge_service import search_relevant

            knowledge_items = await search_relevant(
                query=text, limit=5, org_id=org_id, db_source=db_source,
            )
            if knowledge_items:
                knowledge_text = "\n".join(
                    f"- {item['title']}: {item['content']}"
                    for item in knowledge_items
                )
                return (
                    ROUTER_SYSTEM_PROMPT
                    + f"\n\n你已掌握的经验知识：\n{knowledge_text}"
                )
        except Exception as exc:
            logger.debug(f"Knowledge injection skipped | error={exc}")
        return ROUTER_SYSTEM_PROMPT

    async def _get_client(
        self, api_key: str, timeout: float,
    ) -> httpx.AsyncClient:
        from services.intent_router import DASHSCOPE_BASE_URL

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=DASHSCOPE_BASE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=5.0, read=timeout, write=10.0, pool=5.0,
                ),
            )
        return self._client

    @staticmethod
    def _record_routing_signal(
        decision: "RoutingDecision",
        user_id: str,
        input_length: int,
        has_image: bool,
        router_model: str = "keyword",
        org_id: str | None = None,
        db_source: Any = None,
    ) -> None:
        async def _do_record() -> None:
            try:
                from services.knowledge_service import record_metric

                await record_metric(
                    db_source=db_source,
                    task_type="routing",
                    model_id=router_model,
                    status="success",
                    user_id=user_id,
                    org_id=org_id,
                    params={
                        "routing_tool": decision.raw_tool_name,
                        "routed_by": decision.routed_by,
                        "recommended_model": decision.recommended_model,
                        "input_length": input_length,
                        "has_image": has_image,
                    },
                )
            except Exception as exc:
                logger.debug(
                    f"Routing signal record skipped | error={exc}"
                )

        asyncio.create_task(_do_record())
