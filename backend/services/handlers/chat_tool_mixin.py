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
        runtime_state=None,
    ) -> List[tuple]:
        """执行工具调用：安全检查 → 并行/串行分批 → 返回结果

        Args:
            messages: 当前对话 messages（传给 erp_agent 做上下文筛选）
            budget: ExecutionBudget 实例（约束 sandbox 超时）

        Returns:
            List of (tool_call_dict, result, is_error, display_text)
        """
        from config.chat_tools import is_concurrency_safe
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

        executor = ToolExecutor(
            db=self.db, user_id=user_id,
            conversation_id=conversation_id, org_id=self.org_id,
            request_ctx=_request_ctx,
            workspace_user_id=getattr(self, "_workspace_user_id", user_id),
            resource_manifest=getattr(self, "_resource_manifest", None),
            runtime_state=runtime_state,
            personal_context_allowed=getattr(
                self,
                "_personal_context_allowed",
                True,
            ),
        )
        # 每轮上下文
        executor._task_id = task_id
        executor._message_id = message_id
        executor._parent_messages = messages
        if budget is not None:
            executor._budget = budget
        # 提取当前用户消息中的图片 URLs（供 image_agent 自动注入）
        executor._current_message_images = self._extract_user_image_urls(messages)
        results: List[tuple] = []

        # 按并发安全性分批
        batches = _partition_tool_calls(tool_calls)

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
                batch_results = await asyncio.gather(*tasks)
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
        from services.agent.agent_result import AgentResult
        for tc, result, _is_error, _display in results:
            if not isinstance(result, AgentResult):
                continue
            payloads = result.emit_payloads or []
            logger.info(
                f"AgentResult emit_payloads check | tool={tc['name']} | "
                f"count={len(payloads)} | "
                f"kinds={[p.get('kind') for p in payloads]}"
            )
            if payloads:
                if not hasattr(self, "_pending_emit_payloads"):
                    self._pending_emit_payloads = []
                self._pending_emit_payloads.extend(payloads)
                if not hasattr(self, "_asset_emissions"):
                    self._asset_emissions = []
                self._asset_emissions.extend(
                    payload for payload in payloads
                    if payload.get("_asset_source_kind")
                )
            # 展示文本(供 content_block_add 推送)
            self._last_erp_display_text = result.summary
            self._last_erp_display_files = payloads
            # token 统计
            self._erp_agent_tokens = (
                getattr(self, "_erp_agent_tokens", 0) + result.tokens_used
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

        prepared = await ChatToolMixin._prepare_tool_arguments(
            self, tc, task_id, conversation_id, message_id, user_id,
        )
        if isinstance(prepared, tuple):
            return prepared
        args = _resolve_file_ids(
            prepared, conversation_id, tc["name"],
        )
        started_at = time.monotonic()
        try:
            result = await executor.execute(tc["name"], args)
            elapsed_ms = int(
                (time.monotonic() - started_at) * 1000
            )
            return await ChatToolResultMixin._process_tool_result(
                self, tc,
                result,
                ToolResultContext(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_id=user_id,
                    tool_name=tc["name"],
                    tool_call_id=tc["id"],
                    turn=turn,
                    args=args,
                    elapsed_ms=elapsed_ms,
                ),
            )
        except Exception as error:
            elapsed_ms = int(
                (time.monotonic() - started_at) * 1000
            )
            return await ChatToolResultMixin._process_tool_exception(
                self, tc,
                error,
                ToolResultContext(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_id=user_id,
                    tool_name=tc["name"],
                    tool_call_id=tc["id"],
                    turn=turn,
                    args=args,
                    elapsed_ms=elapsed_ms,
                ),
            )

    async def _prepare_tool_arguments(
        self,
        tc: Dict[str, Any],
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
    ) -> Dict[str, Any] | tuple:
        from config.chat_tools import SafetyLevel, get_safety_level

        safety = get_safety_level(tc["name"])
        try:
            args = (
                json.loads(tc["arguments"])
                if tc["arguments"] else {}
            )
        except json.JSONDecodeError:
            error = f"参数解析失败: {tc['arguments'][:100]}"
            return tc, error, True, error
        if safety != SafetyLevel.DANGEROUS:
            if safety == SafetyLevel.CONFIRM:
                logger.info(
                    f"Tool confirm notify | tool={tc['name']} "
                    f"| task={task_id}"
                )
            return args
        await ws_manager.send_to_task_or_user(
            task_id,
            user_id,
            build_tool_confirm_request(
                task_id=task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                tool_call_id=tc["id"],
                tool_name=tc["name"],
                arguments=args,
                description=f"AI 要执行写操作: {tc['name']}",
                safety_level=safety.value,
            ),
            org_id=self.org_id,
        )
        approved = await ws_manager.wait_for_confirm(
            tc["id"], user_id, self.org_id, timeout=60.0,
        )
        if approved:
            return args
        rejected = (
            f"⚠ 用户拒绝或超时未确认写操作 {tc['name']}。"
            "请告知用户操作未执行，询问是否需要重新确认。"
        )
        return tc, rejected, True, rejected

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
        try:
            await ws_manager.send_to_task_or_user(
                task_id, user_id,
                build_content_block_add(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    block=_step_update,
                ),
                org_id=self.org_id,
            )
        except Exception as e:
            logger.warning(f"tool_step update push failed | tc={tool_call_id} | {e}")

    def _emit_tool_audit(
        self, task_id: str, conversation_id: str, user_id: str,
        tool_name: str, tool_call_id: str, turn: int,
        args: dict, result_length: int, elapsed_ms: int,
        status: str, is_truncated: bool = False,
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
            status=status, is_truncated=is_truncated,
        )))

    @staticmethod
    def _extract_user_image_urls(messages: list) -> list[str]:
        """从 LLM messages 中提取最后一条 user 消息的图片 URLs。"""
        for msg in reversed(messages):
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
