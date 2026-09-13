"""
ChatHandler 工具执行 Mixin

将工具调用的安全检查、分批并行/串行执行、错误处理等逻辑
从 ChatHandler 主文件中拆分出来，保持单一职责。
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.websocket import (
    build_tool_result,
    build_tool_confirm_request,
    build_content_block_add,
)
from services.websocket_manager import ws_manager
from services.handlers.chat_tool_helpers import (
    accumulate_tool_call_delta,
    partition_tool_calls as _partition_tool_calls,
    resolve_file_ids as _resolve_file_ids,
)
from services.handlers.chat_tool_result_mixin import (
    ChatToolResultMixin,
    ToolResultContext,
)


def _collect_interactive_agent_payloads(
    tool_name: str,
    result: Any,
) -> list[Dict[str, Any]]:
    """收集交互式展示产物，但不把 ERP 数据表当成第二个展示出口。

    ERPAgent 的 TABLE 是给主模型使用的数据契约；交互式 Web/Actor
    仍展示模型最终文本。文件、图片等显式产物继续沿用统一 emit 协议。
    定时任务不经过此函数，仍使用 ToolLoopExecutor 的统一收集路径。
    """
    from services.handlers.emit_payloads import collect_agent_result_payloads

    raw_payloads = getattr(result, "emit_payloads", None)
    if tool_name == "erp_agent":
        return [
            payload
            for payload in (raw_payloads or [])
            if isinstance(payload, dict) and payload.get("kind") != "table"
        ]
    return collect_agent_result_payloads(result)


class ChatToolMixin(ChatToolResultMixin):
    """工具执行 Mixin：安全检查 + 并行/串行分批 + 错误回传"""

    async def _execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        turn: int,
        messages: Optional[List[Dict[str, Any]]] = None,
        budget=None,
        cancellation_event: asyncio.Event | None = None,
        permission_mode: str = "auto",
        agent_domain: str = "general",
    ) -> List[tuple]:
        """执行工具调用：安全检查 → 并行/串行分批 → 返回结果

        Args:
            messages: 当前对话 messages（传给 erp_agent 做上下文筛选）
            budget: ExecutionBudget 实例（约束 sandbox 超时）

        Returns:
            List of (tool_call_dict, result, is_error, display_text)
        """
        from services.tools import ToolCall
        from services.tool_executor import ToolExecutor

        # request_ctx 由入口（HTTP/WS/企微）注入到 handler，全链路不可变
        _request_ctx = getattr(self, "request_ctx", None)
        if _request_ctx is None:
            # 防御性 fallback（不应该走到这里，说明入口未注入）
            from utils.time_context import RequestContext
            _request_ctx = RequestContext.build(
                user_id=user_id, org_id=self.org_id,
                request_id=conversation_id or "",
            )
            logger.warning("request_ctx fallback in _execute_tool_calls — entry point should inject it")

        scope = getattr(self, "execution_scope", None)
        executor_kwargs = dict(
            db=self.db, user_id=user_id,
            conversation_id=conversation_id, org_id=self.org_id,
            request_ctx=_request_ctx,
            workspace_user_id=getattr(self, "_workspace_user_id", user_id),
            resource_manifest=getattr(self, "_resource_manifest", None),
            resource_manifest_loader=getattr(self, "_tool_resource_manifest_loader", None),
            resource_access_boundary=getattr(self, "_resource_access_boundary", None),
            execution_budget=budget,
            cancellation_event=cancellation_event,
            permission_mode=permission_mode, agent_domain=agent_domain, task_id=task_id,
            context_scope=getattr(scope, "context_scope", "user"),
            personal_context_allowed=getattr(self, "_personal_context_allowed", True),
            execution_scope=scope, channel_scope_id=getattr(scope, "channel_scope_id", None),
            tool_entrypoint="model",
            tool_confirmer=lambda call, ctx, decision: ChatToolMixin._confirm_tool_call(
                self, call, ctx, decision, message_id,
            ),
        )
        executor_scope = (task_id, conversation_id, user_id, self.org_id)
        executor = getattr(self, "_tool_executor", None)
        if getattr(self, "_tool_executor_scope", None) != executor_scope or executor is None:
            executor = ToolExecutor(**executor_kwargs)
            self._tool_executor, self._tool_executor_scope = executor, executor_scope
        else:
            # Same request service across model rounds; refresh only trusted
            # round facts, retaining consumed IDs and scoped confirmations.
            for key, value in executor_kwargs.items():
                setattr(executor, key, value)
        executor.tool_runtime.context()  # clear any previous execution identity before restore
        if getattr(self, "_tool_selection_history_task_id", None) == task_id:
            executor.tool_runtime.resource_selections.restore(
                executor, getattr(self, "_tool_selection_history", ()),
            )
        # 每轮上下文
        executor._task_id = task_id
        executor._message_id = message_id
        executor._parent_messages = messages
        # 提取当前用户消息中的图片 URLs（供 image_agent 自动注入）
        executor._current_message_images = self._extract_user_image_urls(messages)
        results: List[tuple] = []

        # Policy determines real batch barriers; malformed calls remain serial.
        by_id = {tc["id"]: tc for tc in tool_calls}
        normalized = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["arguments"]) if tc.get("arguments") else {}
                normalized.append(ToolCall(tc["id"], tc["name"], args))
            except (TypeError, ValueError):
                normalized.append(ToolCall(tc["id"], tc["name"], {}))
        batches = [(len(batch) > 1 or batch[0].decision.parallelizable,
                    [by_id[item.call.call_id] for item in batch])
                   for batch in executor.tool_runtime.batches(normalized)]

        for is_safe, batch in batches:
            if is_safe:
                # 只读工具：并行执行
                tasks = [
                    self._execute_single_tool(
                        tc, executor, task_id, conversation_id,
                        message_id, user_id, turn,
                    )
                    for tc in batch
                ]
                from services.tools.runtime import run_parallel
                batch_results = await run_parallel(tasks)
                results.extend(batch_results)
            else:
                # 写操作：逐个执行（含安全检查）
                for tc in batch:
                    result = await self._execute_single_tool(
                        tc, executor, task_id, conversation_id,
                        message_id, user_id, turn,
                    )
                    results.append(result)

        # ── AgentResult 处理:聚合 emit_payloads (沙盒 IO 统一协议) ──
        from services.tools.result import ToolResult
        for tc, result, _is_error, _display in results:
            if not isinstance(result, ToolResult) or result.kind != "agent":
                continue
            payloads = result.collect_payloads("chat")
            logger.info(
                f"AgentResult emit_payloads check | tool={tc['name']} | "
                f"count={len(payloads)} | "
                f"kinds={[p.get('kind') for p in payloads]}"
            )
            if payloads:
                if not hasattr(self, "_pending_emit_payloads"):
                    self._pending_emit_payloads = []
                self._pending_emit_payloads.extend(payloads)
            # 展示文本(供 content_block_add 推送)
            self._last_erp_display_text = result.display["text"]
            self._last_erp_display_files = payloads
            # token 统计
            self._erp_agent_tokens = (
                getattr(self, "_erp_agent_tokens", 0) + result.chargeable_tokens
            )

        # 清理遗留 _pending_schemas(兼容 fetch_all_pages 等仍写入的场景)
        if executor._pending_schemas:
            executor._pending_schemas.clear()

        return results

    async def _execute_single_tool(
        self,
        tc: Dict[str, Any],
        executor: Any,
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        turn: int,
    ) -> tuple:
        """校验单个工具调用，执行后委托结果分类器处理。"""
        import time
        from dataclasses import replace

        from services.handlers.chat.tool_lifecycle import ActorToolLifecycle
        started_at = time.monotonic()
        args = {}
        result_ctx = ToolResultContext(
            task_id=task_id, conversation_id=conversation_id, message_id=message_id,
            user_id=user_id, tool_name=tc["name"], tool_call_id=tc["id"],
            turn=turn, args=args, elapsed_ms=0,
        )
        try:
            try:
                args = json.loads(tc["arguments"]) if tc.get("arguments") else {}
            except (ValueError, TypeError) as exc:
                raise ValueError("参数解析失败") from exc
            if not isinstance(args, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            result_ctx = replace(result_ctx, args=args)
            runtime = executor.tool_runtime
            envelope = await runtime.execute(
                tc["name"], args, call_id=tc["id"],
                lifecycle=ActorToolLifecycle(self, runtime.context(tc["id"])),
            )
            result = envelope
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result_ctx = replace(result_ctx, elapsed_ms=int((time.monotonic() - started_at) * 1000))
            return await ChatToolResultMixin._process_tool_exception(self, tc, error, result_ctx)
        result_ctx = replace(result_ctx, elapsed_ms=int((time.monotonic() - started_at) * 1000))
        # Delivery errors are outside the business/ledger completion boundary.
        try:
            return await ChatToolResultMixin._process_tool_result(self, tc, result, result_ctx)
        except Exception as error:
            logger.warning(f"tool_result_delivery_failed | call={tc['id']} | error={type(error).__name__}")
            raise

    async def _begin_actor_tool_invocation(
        self,
        *,
        store: Any,
        task_id: str,
        conversation_id: str,
        tool_call_id: str,
        tool_name: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if (
            getattr(self, "_actor_enabled", False) is not True
            or store is None
            or not getattr(self, "_actor_turn_id", None)
            or not getattr(self, "_actor_execution_token", None)
        ):
            return None
        from services.tool_invocation_store import hash_tool_arguments
        mark_stale = getattr(store, "mark_stale", None)
        if mark_stale is not None:
            await asyncio.to_thread(
                mark_stale,
                task_id=task_id,
                turn_id=self._actor_turn_id,
                tool_call_id=tool_call_id,
                execution_token=self._actor_execution_token,
            )
        return await asyncio.to_thread(
            store.begin,
            task_id=task_id,
            conversation_id=conversation_id,
            turn_id=self._actor_turn_id,
            execution_token=self._actor_execution_token,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_hash=hash_tool_arguments(args),
        )

    async def _complete_actor_tool_invocation(
        self,
        *,
        store: Any,
        task_id: str,
        turn_id: str | None,
        tool_call_id: str,
        status: str,
        result: Any,
        error_message: str = "",
    ) -> None:
        if (
            getattr(self, "_actor_enabled", False) is not True
            or store is None
            or not turn_id
            or not getattr(self, "_actor_execution_token", None)
        ):
            return
        from services.tool_invocation_store import serialize_tool_result
        await asyncio.to_thread(
            store.complete,
            task_id=task_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            execution_token=self._actor_execution_token,
            status=status,
            result=serialize_tool_result(result),
            error_message=error_message,
        )

    async def _confirm_tool_call(self, call, context, decision, message_id):
        from dataclasses import asdict
        from services.tools.policy import _digest
        from services.tools.spec import thaw
        # Existing WS fields are retained. The opaque confirmation identifier
        # binds durable approval to the complete server-side call fingerprint.
        confirmation_id = "tool-approval:" + _digest(asdict(decision.confirmation_binding))
        self._tool_actor_user_id = context.actor_user_id
        store = getattr(self, "_actor_command_store", None)
        token = getattr(self, "_actor_execution_token", None)
        if getattr(self, "_actor_enabled", False) is True and store is not None and token:
            commands = await store.load_pending(task_id=context.task_id, execution_token=token)
            for command in commands:
                payload = command.payload or {}
                if (command.command_type.value == "approval_result"
                        and payload.get("tool_call_id") == confirmation_id
                        and payload.get("user_id") == context.actor_user_id):
                    return payload.get("approved") is True
        waiter = asyncio.create_task(ChatToolMixin._wait_for_tool_confirmation(
            self, tool_call_id=confirmation_id, task_id=context.task_id,
            conversation_id=context.conversation_id, actor_user_id=context.actor_user_id,
            timeout=min(60.0, context.budget.remaining) if context.budget else 60.0,
        ))
        try:
            # Install the local listener before publishing the existing dialog.
            await asyncio.sleep(0)
            description = f"AI 要执行写操作: {call.name}"
            if call.name == "file_delete":
                from core.config import get_settings
                from services.file_executor import FileExecutor
                files = FileExecutor(get_settings().file_workspace_root, context.workspace_owner_id,
                                     context.org_id, create_root=False)
                from pathlib import Path
                paths = [str(files.resolve_safe_path(path).relative_to(Path(files.workspace_root)))
                         for path in call.arguments.get("files", ())]
                description = f"删除 {len(paths)} 个文件：\n" + "\n".join(paths)
            await ws_manager.send_to_task_or_user(
                context.task_id, context.actor_user_id,
                build_tool_confirm_request(
                    task_id=context.task_id, conversation_id=context.conversation_id,
                    message_id=message_id, tool_call_id=confirmation_id, tool_name=call.name,
                    arguments=thaw(call.arguments), description=description,
                    safety_level="dangerous",
                ),
            )
            return await waiter
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def _wait_for_tool_confirmation(
        self,
        *,
        tool_call_id: str,
        task_id: str,
        conversation_id: str,
        actor_user_id: str,
        timeout: float,
    ) -> bool:
        """等待确认；Actor 进程与 WebSocket 进程分离时以控制事件恢复。"""
        store = getattr(self, "_actor_command_store", None)
        token = getattr(self, "_actor_execution_token", None)
        if getattr(self, "_actor_enabled", False) is not True or store is None or not token:
            return await ws_manager.wait_for_confirm(
                tool_call_id,
                timeout=timeout,
                task_id=task_id,
                conversation_id=conversation_id,
                actor_user_id=actor_user_id,
            )

        runtime = getattr(self, "_actor_runtime", None)
        if runtime is not None:
            from services.conversation_state import ConversationState
            runtime.set_state(ConversationState.WAITING_APPROVAL)

        local_wait = asyncio.create_task(
            ws_manager.wait_for_confirm(
                tool_call_id,
                timeout=timeout,
                task_id=task_id,
                conversation_id=conversation_id,
                actor_user_id=actor_user_id,
            )
        )
        durable_wait = asyncio.create_task(
            self._poll_durable_tool_confirmation(
                store=store,
                token=token,
                tool_call_id=tool_call_id,
                task_id=task_id,
                timeout=timeout,
                runtime=runtime,
            )
        )
        waits = (local_wait, durable_wait)
        try:
            done, _ = await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                return False
            # Any confirmation failure is closed, including simultaneous results.
            return all(task.result() is True for task in done)
        finally:
            for task in waits:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    async def _poll_durable_tool_confirmation(
        self,
        *,
        store: Any,
        token: str,
        tool_call_id: str,
        task_id: str,
        timeout: float,
        runtime: Any = None,
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        cancellation_event = getattr(self, "_actor_cancellation_event", None)
        while loop.time() < deadline:
            if cancellation_event is not None and cancellation_event.is_set():
                raise asyncio.CancelledError()
            commands = await store.load_pending(
                task_id=task_id,
                execution_token=token,
            )
            for command in commands:
                if command.command_type.value != "approval_result":
                    continue
                payload = command.payload or {}
                if payload.get("tool_call_id") != tool_call_id:
                    continue
                if payload.get("user_id") != getattr(self, "_tool_actor_user_id", None):
                    continue
                approved = payload.get("approved") is True
                if runtime is not None:
                    runtime.push(command)
                elif command.event_id:
                    await store.acknowledge(
                        event_id=command.event_id,
                        task_id=task_id,
                        execution_token=token,
                    )
                return approved
            await asyncio.sleep(min(0.5, max(0.05, deadline - loop.time())))
        return False

    async def _push_tool_step_update(
        self, task_id: str, conversation_id: str, message_id: str,
        user_id: str, tool_name: str, tool_call_id: str,
        success: bool, output: str, elapsed_ms: int,
    ) -> None:
        """推送 tool_step 完成/失败更新到前端（通过 content_block_add）"""
        _step_update: Dict[str, Any] = {
            "type": "tool_step",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "completed" if success else "error",
            "output": output,
            "elapsed_ms": elapsed_ms,
        }
        sink = self.__dict__.get("_execution_sink")
        if sink is None and self.__dict__.get("_actor_enabled") is True:
            sink = self.__dict__.get("_actor_sink")
        on_block_update = getattr(sink, "on_block_update", None)
        if on_block_update is not None:
            result = on_block_update(_step_update)
            if hasattr(result, "__await__"):
                await result
                return
        try:
            await ws_manager.send_to_task_or_user(
                task_id, user_id,
                build_content_block_add(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    block=_step_update,
                ),
            )
        except Exception as e:
            logger.warning(f"tool_step update push failed | tc={tool_call_id} | {e}")

    def _emit_tool_audit(
        self, task_id: str, conversation_id: str, user_id: str,
        tool_name: str, tool_call_id: str, turn: int,
        args: dict, result_length: int, elapsed_ms: int,
        status: str, is_truncated: bool = False,
        *, is_cached: bool = False, execution: dict | None = None,
    ) -> None:
        """[C1] fire-and-forget 审计日志"""
        from services.agent.tool_audit import (
            ToolAuditEntry, build_args_hash, record_tool_audit,
        )
        asyncio.create_task(record_tool_audit(self.db, ToolAuditEntry(
            task_id=task_id, conversation_id=conversation_id,
            user_id=user_id, org_id=self.org_id or "",
            tool_name=tool_name, tool_call_id=tool_call_id,
            turn=turn, args_hash=build_args_hash(args),
            result_length=result_length, elapsed_ms=elapsed_ms,
            status=status, is_truncated=is_truncated, is_cached=is_cached,
            execution=execution or {},
        )))

    @staticmethod
    def _extract_user_image_urls(messages: list) -> list[str]:
        """从 LLM messages 中提取最后一条 user 消息的图片 URLs。"""
        for msg in reversed(messages or []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                return [
                    p["image_url"]["url"]
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "image_url"
                    and isinstance(p.get("image_url"), dict) and p["image_url"].get("url")
                ]
            break
        return []
