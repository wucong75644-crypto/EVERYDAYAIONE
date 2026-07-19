"""ToolLoopExecutor 的工具执行阶段与 Validation 旁路观察。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from loguru import logger

from services.agent.loop_types import HookContext
from services.agent.runtime.validation import (
    ValidationRuntime,
    resolve_tool_effect,
)
from services.agent.tool_output import ToolOutput


class ToolLoopExecutionMixin:
    """保持工具执行协议不变，并旁路记录统一 Validation Receipt。"""

    def _start_validation_observation(self, task_id: str) -> None:
        self._validation_runtime = ValidationRuntime(task_id=task_id)
        self._validation_comparisons: list[dict[str, Any]] = []

    def _compare_validation_decision(
        self,
        *,
        old_decision: Any,
        model_step: int,
    ) -> None:
        """比较新旧控制意图；仅记录，不参与旧循环判断。"""
        try:
            receipts = [
                receipt
                for receipt in self._validation_runtime.receipts
                if receipt.model_step == model_step
            ]
            if not receipts:
                return
            continue_decisions = {
                "continue",
                "retry_model",
                "retry_transport",
            }
            new_decisions = [receipt.decision.value for receipt in receipts]
            new_action = (
                "continue"
                if all(item in continue_decisions for item in new_decisions)
                else "stop"
            )
            old_value = getattr(old_decision, "value", str(old_decision))
            old_action = "continue" if old_value == "continue" else "stop"
            comparison = {
                "task_id": self._validation_runtime.task_id,
                "model_step": model_step,
                "old_decision": old_value,
                "new_decisions": new_decisions,
                "old_action": old_action,
                "new_action": new_action,
                "aligned": old_action == new_action,
            }
            self._validation_comparisons.append(comparison)
            logger.info(
                "validation_decision_shadow | "
                f"task_id={comparison['task_id']} | "
                f"model_step={model_step} | old={old_value} | "
                f"new={','.join(new_decisions)} | "
                f"aligned={comparison['aligned']}"
            )
        except Exception as exc:
            logger.warning(
                "validation_decision_shadow_failed | "
                f"model_step={model_step} | error={type(exc).__name__}"
            )

    def _observe_validation_result(
        self,
        *,
        result: Any,
        tool_call_id: str,
        tool_name: str,
        hook_ctx: HookContext,
        audit_status: str,
        elapsed_ms: int,
    ) -> None:
        try:
            validated, decision = self._validation_runtime.observe_result(
                result,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                model_step=hook_ctx.turn,
                turns_remaining=max(self.config.max_turns - hook_ctx.turn, 0),
                audit_status=audit_status,
                effect=resolve_tool_effect(tool_name),
                duration_ms=elapsed_ms,
            )
            logger.info(
                "validation_result_classified | "
                f"owner=tool_loop | tool={tool_name} | "
                f"result_class={validated.result_class.value} | "
                f"decision={decision.value}"
            )
        except Exception as exc:
            logger.warning(
                "validation_observation_failed | "
                f"owner=tool_loop | tool={tool_name} | "
                f"task_id={hook_ctx.task_id or ''} | "
                f"error={type(exc).__name__}"
            )

    async def _request_user_confirm(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_call_id: str,
        hook_ctx: HookContext,
    ) -> str | None:
        """向前端发起写操作确认；无任务或观察异常不阻塞工具执行。"""
        if not hook_ctx.task_id:
            return None

        try:
            from schemas.websocket_builders import build_tool_confirm_request
            from services.websocket_manager import ws_manager

            await ws_manager.send_to_task_or_user(
                hook_ctx.task_id,
                hook_ctx.user_id,
                build_tool_confirm_request(
                    task_id=hook_ctx.task_id,
                    conversation_id=hook_ctx.conversation_id,
                    message_id="",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=args,
                    description=f"AI 要执行写操作: {tool_name}",
                    safety_level="dangerous",
                    timeout=60,
                ),
            )
            approved = await ws_manager.wait_for_confirm(
                tool_call_id, timeout=60.0,
            )
            if approved:
                logger.info(
                    f"Tool confirm approved | tool={tool_name} | "
                    f"tool_call_id={tool_call_id}"
                )
                return None
            logger.info(
                f"Tool confirm rejected/timeout | tool={tool_name} | "
                f"tool_call_id={tool_call_id}"
            )
            return (
                f"⚠ 用户拒绝或超时未确认写操作 {tool_name}。"
                f"请告知用户操作未执行，询问是否需要重新确认。"
            )
        except Exception as exc:
            logger.warning(
                f"Tool confirm error | tool={tool_name} | error={exc}"
            )
            return None

    async def _execute_tools(
        self,
        completed: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        turn_text: str,
        hook_ctx: HookContext,
        turn_prompt_tokens: int = 0,
        turn_completion_tokens: int = 0,
    ) -> str:
        """执行一轮工具调用，保持既有预处理、并行和后处理顺序。"""
        messages = hook_ctx.messages
        self._turn_tool_outcomes.clear()
        messages.append({
            "role": "assistant",
            "content": turn_text or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in completed
            ],
        })

        ready, accumulated, exit_hit = await self._prepare_tools(
            completed, selected_tools, turn_text, hook_ctx,
        )
        if exit_hit or not ready:
            return accumulated

        results = await self._invoke_tools(ready, hook_ctx)
        return await self._process_tool_results(
            results,
            selected_tools,
            accumulated,
            hook_ctx,
            turn_prompt_tokens,
            turn_completion_tokens,
        )

    async def _prepare_tools(
        self,
        completed: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        turn_text: str,
        hook_ctx: HookContext,
    ) -> tuple[List[Tuple[Dict, str, Dict]], str, bool]:
        from config.chat_tools import SafetyLevel, get_safety_level
        from services.agent.tool_args_validator import validate_tool_args

        accumulated = turn_text
        ready: List[Tuple[Dict, str, Dict]] = []
        for tc in completed:
            tool_name = tc["name"]
            hook_ctx.tools_called.append(tool_name)
            if tool_name in self.strategy.exit_signals:
                messages = hook_ctx.messages
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": "OK",
                })
                return ready, turn_text if turn_text else "", True
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError as exc:
                logger.warning(
                    f"ToolLoop bad JSON | tool={tool_name} | error={exc}"
                )
                result = f"工具参数JSON格式错误: {exc}，请检查参数格式"
                hook_ctx.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                accumulated = result
                continue
            if get_safety_level(tool_name) == SafetyLevel.DANGEROUS:
                confirm_result = await self._request_user_confirm(
                    tool_name, args, tc["id"], hook_ctx,
                )
                if confirm_result is not None:
                    hook_ctx.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": confirm_result,
                    })
                    accumulated = confirm_result
                    continue
            args, validation_error = validate_tool_args(
                tool_name, args, selected_tools,
            )
            if validation_error:
                hook_ctx.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": validation_error,
                })
                accumulated = validation_error
                continue
            ready.append((tc, tool_name, args))
        return ready, accumulated, False

    async def _invoke_tools(
        self,
        ready: List[Tuple[Dict, str, Dict]],
        hook_ctx: HookContext,
    ) -> list[tuple[Dict, str, Dict, Any, str, bool, int]]:
        from services.agent.tool_loop_helpers import invoke_tool_with_cache

        async def invoke_safe(
            tc: Dict, tool_name: str, args: Dict,
        ) -> tuple[Dict, str, Dict, Any, str, bool, int]:
            try:
                result, status, cached, elapsed_ms = (
                    await invoke_tool_with_cache(
                        self.executor,
                        self._cache,
                        tool_name,
                        args,
                        hook_ctx.budget,
                        self.config.tool_timeout,
                    )
                )
                return (
                    tc, tool_name, args, result,
                    status, cached, elapsed_ms,
                )
            except Exception as exc:
                logger.opt(exception=True).error(
                    f"ToolLoop parallel error | tool={tool_name} | error={exc}"
                )
                return (
                    tc, tool_name, args, f"工具执行失败: {exc}",
                    "error", False, 0,
                )

        for _tc, tool_name, args in ready:
            for hook in self.hooks:
                await hook.on_tool_start(hook_ctx, tool_name, args)
        if len(ready) == 1:
            return [await invoke_safe(*ready[0])]
        results = await asyncio.gather(
            *[invoke_safe(tc, tool_name, args)
              for tc, tool_name, args in ready],
        )
        logger.info(
            f"ToolLoop parallel done | count={len(results)} | "
            f"tools={[result[1] for result in results]}"
        )
        return results

    async def _process_tool_results(
        self,
        results: list[tuple[Dict, str, Dict, Any, str, bool, int]],
        selected_tools: List[Dict[str, Any]],
        accumulated: str,
        hook_ctx: HookContext,
        turn_prompt_tokens: int,
        turn_completion_tokens: int,
    ) -> str:
        steer_hit = False
        for index, item in enumerate(results):
            (
                tc, tool_name, args, result,
                audit_status, is_cached, elapsed_ms,
            ) = item
            content, is_truncated = self._tool_content(result, tool_name)
            hook_ctx.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "content": content,
            })
            accumulated = content
            self._turn_tool_outcomes.append(
                (tool_name, result, audit_status),
            )
            self._observe_validation_result(
                result=result,
                tool_call_id=tc["id"],
                tool_name=tool_name,
                hook_ctx=hook_ctx,
                audit_status=audit_status,
                elapsed_ms=elapsed_ms,
            )
            for hook in self.hooks:
                await hook.on_tool_end(
                    hook_ctx,
                    tool_name,
                    args,
                    result,
                    audit_status,
                    elapsed_ms,
                    is_cached,
                    is_truncated,
                    tc["id"],
                    turn_prompt_tokens=turn_prompt_tokens,
                    turn_completion_tokens=turn_completion_tokens,
                )
            if hook_ctx.task_id and not steer_hit:
                steer_hit = self._apply_steer(
                    hook_ctx, results, index,
                )
                if steer_hit:
                    break
            if self.strategy.enable_tool_expansion:
                from services.agent.tool_loop_helpers import inject_tool
                inject_tool(
                    tool_name,
                    selected_tools,
                    self.all_tools,
                    self.strategy.exit_signals,
                    hook_ctx.org_id,
                )
        return accumulated

    def _tool_content(
        self,
        result: Any,
        tool_name: str,
    ) -> tuple[Any, bool]:
        if isinstance(result, ToolOutput):
            warnings = result.validate()
            if warnings:
                logger.warning(
                    f"ToolOutput validation | tool={tool_name} | "
                    f"issues={warnings}"
                )
            self._register_result_files(result, tool_name)
            return result.to_tool_content(), False

        self._register_result_files(result, tool_name)
        from services.agent.tool_result_envelope import (
            PERSISTED_OUTPUT_TAG,
            wrap_for_erp_agent,
        )
        content = wrap_for_erp_agent(
            tool_name, result or "", tight=False,
        )
        truncated = bool(content) and (
            PERSISTED_OUTPUT_TAG in content or "⚠ 输出过长" in content
        )
        return content, truncated

    @staticmethod
    def _apply_steer(
        hook_ctx: HookContext,
        results: list[tuple[Dict, str, Dict, Any, str, bool, int]],
        index: int,
    ) -> bool:
        from services.websocket_manager import ws_manager

        steer = ws_manager.check_steer(hook_ctx.task_id)
        if not steer:
            return False
        logger.info(
            f"ToolLoop steer | task={hook_ctx.task_id} | msg={steer[:50]}"
        )
        for remaining in results[index + 1:]:
            hook_ctx.messages.append({
                "role": "tool",
                "tool_call_id": remaining[0]["id"],
                "content": "⚠ 用户发送了新消息，跳过此工具调用。",
            })
        hook_ctx.messages.append({"role": "user", "content": steer})
        return True
