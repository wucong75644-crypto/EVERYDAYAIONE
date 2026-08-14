"""Runtime-owned media actions used by conversational tool execution."""

from __future__ import annotations

from typing import Any, Dict


class RuntimeMediaToolMixin:
    """Submit media Actions without importing or invoking legacy Providers."""

    async def _generate_image(self, args: Dict[str, Any]) -> "AgentResult":
        return await self._execute_runtime_media_action("generate_image", args)

    async def _generate_video(self, args: Dict[str, Any]) -> "AgentResult":
        return await self._execute_runtime_media_action("generate_video", args)

    async def _execute_runtime_media_action(
        self, tool_name: str, args: Dict[str, Any],
    ) -> "AgentResult":
        from services.agent.agent_result import AgentResult
        from services.agent.runtime.application.chat_action_bridge import (
            ChatActionRequest,
        )

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            label = "视频描述" if tool_name == "generate_video" else "提示词"
            return AgentResult(
                status="error", summary=f"{label}不能为空",
                error_message="Validation: prompt is required",
                metadata={"retryable": True},
            )
        executor = self._runtime_action_executor
        if executor is None:
            return AgentResult(
                status="error",
                summary="该能力正在由 Agent Runtime 处理中，请稍后重试",
                error_message="RUNTIME_CHAT_ACTION_EXECUTOR_NOT_WIRED",
                metadata={"runtime_owned": True, "retryable": True},
            )
        arguments = {
            **dict(args),
            "task_id": str(getattr(self, "_task_id", "")),
            "message_id": str(getattr(self, "_message_id", "")),
            "input_message_id": str(
                getattr(self, "_input_message_id", "")
            ),
            "user_id": self.user_id,
            "org_id": self.org_id,
            "runtime_owned": True,
        }
        result = await executor.execute(ChatActionRequest(
            tool_name=tool_name,
            arguments=arguments,
            task_id=str(getattr(self, "_task_id", "")),
            conversation_id=self.conversation_id,
            message_id=str(getattr(self, "_message_id", "")),
            user_id=self.user_id,
            turn=int(getattr(self, "_turn", 0)),
            tool_call_id=str(getattr(self, "_tool_call_id", "")),
            org_id=self.org_id,
            model_id=getattr(self, "_model_id", None),
        ))
        if isinstance(result, AgentResult):
            return result
        receipt_text = str(result)
        normalized = receipt_text.lower()
        status = (
            "unknown" if "unknown" in normalized else
            "accepted" if "accepted" in normalized or "created" in normalized else
            "pending"
        )
        return AgentResult(
            status=status,
            summary=receipt_text,
            metadata={
                "runtime_owned": True,
                "reconcile_only": True,
                "readback": "runtime_projection",
                "state_source": "runtime_action_receipt",
            },
        )


__all__ = ["RuntimeMediaToolMixin"]
