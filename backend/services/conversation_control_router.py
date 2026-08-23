"""Model-backed semantic router for conversation control commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger

from config.conversation_control_prompt import build_conversation_control_prompt
from config.conversation_control_tools import (
    CONVERSATION_CONTROL_TOOL_NAME,
    build_conversation_control_tools,
)
from services.intent_router import IntentRouter


class ControlAction(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    NONE = "none"


@dataclass(frozen=True)
class ConversationControlState:
    state: str
    has_running_task: bool
    has_paused_task: bool
    latest_task_summary: str = "无"


@dataclass(frozen=True)
class ControlRoutingDecision:
    action: ControlAction
    reason: str = ""
    routed_by: str = "none"
    router_model: str | None = None


class ConversationControlRouter:
    """Use the existing IntentRouter transport with a control-only prompt."""

    async def route(
        self,
        text: str,
        state: ConversationControlState,
        *,
        org_id: str | None = None,
    ) -> ControlRoutingDecision:
        if not text or not text.strip():
            return ControlRoutingDecision(ControlAction.NONE, routed_by="empty")

        from core.config import get_settings

        settings = get_settings()
        if not settings.intent_router_enabled or not settings.dashscope_api_key:
            return ControlRoutingDecision(
                ControlAction.NONE,
                routed_by="router_unavailable",
            )

        prompt = build_conversation_control_prompt(
            state=state.state,
            has_running_task=state.has_running_task,
            has_paused_task=state.has_paused_task,
            latest_task_summary=state.latest_task_summary,
        )
        user_text = f"<user_input>\n{text.strip()}\n</user_input>"
        router = IntentRouter()
        try:
            for model in (
                settings.intent_router_model,
                settings.intent_router_fallback_model,
            ):
                try:
                    payload = await router.call_tool_model(
                        api_key=settings.dashscope_api_key,
                        model=model,
                        system_prompt=prompt,
                        text=user_text,
                        tools=build_conversation_control_tools(),
                        timeout=min(settings.intent_router_timeout, 5.0),
                    )
                    decision = self._parse(payload, state)
                    if decision is not None:
                        return ControlRoutingDecision(
                            action=decision.action,
                            reason=decision.reason,
                            routed_by="model",
                            router_model=model,
                        )
                except Exception as error:
                    logger.warning(
                        "Conversation control router failed | "
                        f"model={model} | error={type(error).__name__}"
                    )
            return ControlRoutingDecision(
                ControlAction.NONE,
                routed_by="router_failed",
            )
        finally:
            await router.close()

    @staticmethod
    def _parse(
        payload: dict[str, Any] | None,
        state: ConversationControlState,
    ) -> ControlRoutingDecision | None:
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            return ControlRoutingDecision(ControlAction.NONE, routed_by="no_tool")

        function = calls[0].get("function") or {}
        if function.get("name") != CONVERSATION_CONTROL_TOOL_NAME:
            return ControlRoutingDecision(ControlAction.NONE, routed_by="unknown_tool")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            action = ControlAction(str(arguments.get("action", "none")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ControlRoutingDecision(ControlAction.NONE, routed_by="invalid_tool")

        if action is ControlAction.PAUSE and not state.has_running_task:
            return ControlRoutingDecision(ControlAction.NONE, routed_by="state_guard")
        if action is ControlAction.RESUME and not state.has_paused_task:
            return ControlRoutingDecision(ControlAction.NONE, routed_by="state_guard")
        return ControlRoutingDecision(
            action=action,
            reason=str(arguments.get("reason") or ""),
            routed_by="model",
        )

