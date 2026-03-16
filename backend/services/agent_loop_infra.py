"""
Agent Loop 基础设施 Mixin

负责 Agent Loop 的基础设施：
- 大脑调用（DashScope / OpenRouter）
- HTTP 客户端管理
- WebSocket 进度通知
- 路由信号 / 意图学习记录

与 AgentLoop 通过 Mixin 继承组合，共享 self._settings / self._client 等属性。
"""

import asyncio
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from config.agent_tools import AGENT_TOOLS


class AgentInfraMixin:
    """Agent 基础设施方法集（Mixin，由 AgentLoop 继承）"""

    # ========================================
    # 大脑调用
    # ========================================

    async def _call_brain(
        self, messages: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """调用大脑（DashScope 或 OpenRouter，均为 OpenAI 兼容 API）"""
        assert self._settings is not None
        client = await self._get_client()

        provider = self._settings.agent_loop_provider
        model = (
            self._settings.agent_loop_openrouter_model
            if provider == "openrouter"
            else self._settings.agent_loop_model
        )

        logger.info(
            f"Brain calling | provider={provider} | model={model}"
        )

        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
        )
        response.raise_for_status()
        data = response.json()

        usage = data.get("usage", {})
        logger.info(
            f"Brain responded | tokens={usage.get('total_tokens', '?')}"
        )
        return data

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端（根据 provider 选择 DashScope 或 OpenRouter）"""
        if self._client is None or self._client.is_closed:
            assert self._settings is not None
            provider = self._settings.agent_loop_provider

            if provider == "openrouter":
                base_url = self._settings.openrouter_base_url
                api_key = self._settings.openrouter_api_key or ""
                extra_headers = {
                    "HTTP-Referer": "https://everydayai.one",
                    "X-Title": self._settings.openrouter_app_title,
                }
            else:
                base_url = self._settings.dashscope_base_url
                api_key = self._settings.dashscope_api_key or ""
                extra_headers = {}

            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **extra_headers,
                },
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self._settings.agent_loop_timeout,
                    write=10.0,
                    pool=5.0,
                ),
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ========================================
    # WebSocket 通知
    # ========================================

    async def _notify_progress(
        self, turn: int, tool_name: str, status: str,
    ) -> None:
        """推送 agent_step 事件给前端（优先通过 task_id 精准推送）"""
        try:
            from schemas.websocket import build_agent_step
            from services.websocket_manager import ws_manager

            msg = build_agent_step(
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                status=status,
                turn=turn,
                task_id=getattr(self, "_task_id", None),
            )
            task_id = getattr(self, "_task_id", None)
            if task_id:
                await ws_manager.send_to_task_subscribers(task_id, msg)
            else:
                await ws_manager.send_to_user(self.user_id, msg)
        except Exception as e:
            logger.debug(f"Agent step notification failed | error={e}")

    # ========================================
    # 信号记录
    # ========================================

    def _record_loop_signal(
        self, result: "AgentResult", input_length: int,
        has_image: bool, has_file: bool = False,
    ) -> None:
        """记录 Agent Loop 路由信号到 knowledge_metrics"""
        async def _do_record() -> None:
            try:
                from services.knowledge_service import record_metric
                await record_metric(
                    task_type="routing",
                    model_id="agent_loop",
                    status="success",
                    user_id=self.user_id,
                    params={
                        "routing_tool": result.generation_type.value,
                        "routed_by": "agent_loop",
                        "recommended_model": result.model,
                        "input_length": input_length,
                        "has_image": has_image,
                        "has_file": has_file,
                        "loop_turns": result.turns_used,
                        "loop_tokens": result.total_tokens,
                    },
                )
            except Exception as e:
                logger.debug(
                    f"Agent loop signal record skipped | error={e}"
                )

        asyncio.create_task(_do_record())

    def _record_ask_user_context(self, ask_message: str) -> None:
        """ask_user 触发时，记录上下文供后续意图学习"""
        user_text = getattr(self, "_user_text", "")
        if not user_text:
            return

        async def _do_record() -> None:
            try:
                from services.intent_learning import record_ask_user_context
                await record_ask_user_context(
                    conversation_id=self.conversation_id,
                    user_id=self.user_id,
                    original_message=user_text,
                    ask_options=ask_message,
                )
            except Exception as e:
                logger.debug(f"Intent pending record skipped | error={e}")

        asyncio.create_task(_do_record())

    def _check_intent_learning(
        self, result: "AgentResult", user_text: str,
    ) -> None:
        """路由成功时，检查是否有 pending 的意图学习"""
        # ask_user 本身不触发确认
        tool_params = result.tool_params or {}
        if tool_params.get("_ask_reason"):
            return
        # 纯兜底（无模型）不触发
        if not result.model:
            return

        confirmed_tool = f"route_to_{result.generation_type.value}"

        async def _do_check() -> None:
            try:
                from services.intent_learning import check_and_record_intent
                await check_and_record_intent(
                    conversation_id=self.conversation_id,
                    user_id=self.user_id,
                    user_response=user_text,
                    confirmed_tool=confirmed_tool,
                )
            except Exception as e:
                logger.debug(f"Intent learning check skipped | error={e}")

        asyncio.create_task(_do_check())
