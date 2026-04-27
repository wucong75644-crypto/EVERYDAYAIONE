"""
ChatHandler 工具执行 Mixin

将工具调用的安全检查、分批并行/串行执行、错误处理等逻辑
从 ChatHandler 主文件中拆分出来，保持单一职责。
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

# [FILE] 标记正则：沙盒 upload_file 返回的格式
_FILE_PATTERN = re.compile(
    r'\[FILE\]([^|]+)\|([^|]+)\|([^|]+)\|(\d+)\[/FILE\]'
)

from schemas.websocket import (
    build_tool_result,
    build_tool_confirm_request,
)
from services.websocket_manager import ws_manager


class ChatToolMixin:
    """工具执行 Mixin：安全检查 + 并行/串行分批 + 错误回传"""

    def _ensure_executor(
        self,
        user_id: str,
        conversation_id: str,
    ) -> "ToolExecutor":
        """获取或创建会话级 ToolExecutor（file_handles 等状态跨轮保留）"""
        from services.tool_executor import ToolExecutor

        _request_ctx = getattr(self, "request_ctx", None)
        if _request_ctx is None:
            from utils.time_context import RequestContext
            _request_ctx = RequestContext.build(
                user_id=user_id, org_id=self.org_id,
                request_id=conversation_id or "",
            )

        # 首次调用或参数变更时创建，否则复用
        existing = getattr(self, "_tool_executor", None)
        if (
            existing is not None
            and existing.user_id == user_id
            and existing.conversation_id == conversation_id
        ):
            return existing

        executor = ToolExecutor(
            db=self.db, user_id=user_id,
            conversation_id=conversation_id, org_id=self.org_id,
            request_ctx=_request_ctx,
        )
        self._tool_executor = executor
        return executor

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
    ) -> List[tuple]:
        """执行工具调用：安全检查 → 并行/串行分批 → 返回结果

        Args:
            messages: 当前对话 messages（传给 erp_agent 做上下文筛选）
            budget: ExecutionBudget 实例（约束 sandbox 超时）

        Returns:
            List of (tool_call_dict, result_text, is_error)
        """
        from config.chat_tools import is_concurrency_safe

        # 复用会话级 executor（file_handles 跨轮保留）
        executor = self._ensure_executor(user_id, conversation_id)
        # 每轮更新的上下文（不重建 executor）
        executor._task_id = task_id
        executor._message_id = message_id
        executor._parent_messages = messages
        if budget is not None:
            executor._budget = budget
        executor._pending_file_parts = []
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

        # ── AgentResult 处理（通信协议 §3.2）──
        from services.agent.agent_result import AgentResult
        for i, (tc, result, is_error) in enumerate(results):
            if not isinstance(result, AgentResult):
                continue
            # ① 前端文件卡片通道
            logger.info(
                f"AgentResult file check | tool={tc['name']} | "
                f"collected_files={len(result.collected_files) if result.collected_files else 0} | "
                f"has_pending={hasattr(self, '_pending_file_parts')}"
            )
            if result.collected_files and hasattr(self, "_pending_file_parts"):
                from schemas.message import FilePart
                for f in result.collected_files:
                    self._pending_file_parts.append(FilePart(
                        url=f["url"], name=f["name"],
                        mime_type=f["mime_type"], size=f["size"],
                    ))
                    logger.info(f"FilePart added | name={f['name']} | url={f['url'][:80]}")
            # ② ask_user 冒泡
            if (result.status == "ask_user" and result.ask_user_question
                    and not getattr(self, "_ask_user_pending", None)):
                self._ask_user_pending = {
                    "message": result.ask_user_question,
                    "reason": "need_info",
                    "tool_call_id": tc["id"],
                    "source": result.source,
                }
            # ③ 展示文本（供 content_block_add 推送）
            self._last_erp_display_text = result.summary
            self._last_erp_display_files = result.collected_files or []
            # ④ token 统计
            self._erp_agent_tokens = (
                getattr(self, "_erp_agent_tokens", 0) + result.tokens_used
            )

        # 收集普通工具（非 AgentResult）透传的 FilePart（[FILE] 标记通道）
        if executor._pending_file_parts:
            if hasattr(self, "_pending_file_parts"):
                self._pending_file_parts.extend(executor._pending_file_parts)
            executor._pending_file_parts.clear()

        # 透传图片尺寸（sandbox PIL 读取 → chat_handler image block）
        if hasattr(executor, "_image_dims") and executor._image_dims:
            if not hasattr(self, "_image_dims"):
                self._image_dims = {}
            self._image_dims.update(executor._image_dims)

        return results

    async def _execute_single_tool(
        self,
        tc: Dict[str, Any],
        executor,
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        turn: int,
    ) -> tuple:
        """执行单个工具：安全检查 → 执行 → 返回 (tc, result, is_error)"""
        from config.chat_tools import get_safety_level, SafetyLevel

        tool_name = tc["name"]
        tool_call_id = tc["id"]

        # ask_user 短路：提取追问信息，标记到 handler 级别供冻结逻辑使用
        if tool_name == "ask_user":
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            self._ask_user_pending = {
                "message": args.get("message", "请补充更多信息"),
                "reason": args.get("reason", "need_info"),
                "tool_call_id": tool_call_id,
            }
            return (tc, "OK", False)

        safety = get_safety_level(tool_name)

        # dangerous 级别：需要用户确认
        if safety == SafetyLevel.DANGEROUS:
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            # 发确认请求
            await ws_manager.send_to_task_or_user(
                task_id, user_id,
                build_tool_confirm_request(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=args,
                    description=f"AI 要执行写操作: {tool_name}",
                    safety_level=safety.value,
                ),
            )
            # 等待用户确认（60s 超时）
            approved = await ws_manager.wait_for_confirm(
                tool_call_id, timeout=60.0,
            )
            if not approved:
                return (
                    tc,
                    f"⚠ 用户拒绝或超时未确认写操作 {tool_name}。"
                    f"请告知用户操作未执行，询问是否需要重新确认。",
                    True,
                )

        # confirm 级别：通知用户（不阻塞）
        if safety == SafetyLevel.CONFIRM:
            logger.info(f"Tool confirm notify | tool={tool_name} | task={task_id}")

        # 执行工具
        try:
            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            return (tc, f"参数解析失败: {tc['arguments'][:100]}", True)

        import time as _time
        _audit_start = _time.monotonic()
        try:
            result = await executor.execute(tool_name, args)
            _audit_elapsed = int((_time.monotonic() - _audit_start) * 1000)

            # AgentResult 直接返回（不做 str 操作，由上层处理）
            from services.agent.agent_result import AgentResult
            if isinstance(result, AgentResult):
                raw_summary = result.summary[:100] if result.summary else ""
                await ws_manager.send_to_task_or_user(
                    task_id, user_id,
                    build_tool_result(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        success=not result.is_failure,
                        summary=raw_summary,
                        turn=turn,
                    ),
                )
                self._emit_tool_audit(
                    task_id, conversation_id, user_id, tool_name,
                    tool_call_id, turn, args, len(result.summary),
                    _audit_elapsed, result.status,
                )
                return (tc, result, result.is_failure)

            # FormBlockResult 通道：暂存到 _pending_form_block
            # chat_handler 统一处理（推送 WS + 加 _content_blocks + break）
            # 复用 _pending_file_parts 的已验证模式
            from services.scheduler.chat_task_manager import FormBlockResult
            if isinstance(result, FormBlockResult):
                self._pending_form_block = result.form
                llm_text = result.llm_hint
                await ws_manager.send_to_task_or_user(
                    task_id, user_id,
                    build_tool_result(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        success=True,
                        summary="表单已展示",
                        turn=turn,
                    ),
                )
                self._emit_tool_audit(
                    task_id, conversation_id, user_id, tool_name,
                    tool_call_id, turn, args, len(json.dumps(result.form)),
                    _audit_elapsed, "success",
                )
                return (tc, llm_text, False)

            # 普通工具（str 路径）
            # 提取 [FILE] 标记 → FilePart 暂存到 ChatHandler（不经过 LLM）
            result = self._extract_file_parts(result)
            # 先用完整结果生成 summary 推送前端（用户看到完整摘要）
            raw_summary = result[:100] if result else ""
            # 再截断+信号（messages 里只放精简版给 LLM）
            from services.agent.tool_result_envelope import wrap
            is_truncated = len(result) > 2000 if result else False
            result = wrap(tool_name, result)
            # 通知前端工具完成（summary 基于截断前的原始结果）
            await ws_manager.send_to_task_or_user(
                task_id, user_id,
                build_tool_result(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    success=True,
                    summary=raw_summary,
                    turn=turn,
                ),
            )
            # [C1] 审计日志（fire-and-forget）
            self._emit_tool_audit(
                task_id, conversation_id, user_id, tool_name,
                tool_call_id, turn, args, len(result),
                _audit_elapsed, "success", is_truncated,
            )
            return (tc, result, False)
        except Exception as e:
            _audit_elapsed = int((_time.monotonic() - _audit_start) * 1000)
            logger.error(f"Tool execution error | tool={tool_name} | task={task_id} | error={e}")
            error_msg = f"工具执行失败: {e}"
            await ws_manager.send_to_task_or_user(
                task_id, user_id,
                build_tool_result(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    success=False,
                    summary=str(e)[:100],
                    turn=turn,
                ),
            )
            self._emit_tool_audit(
                task_id, conversation_id, user_id, tool_name,
                tool_call_id, turn, args, len(error_msg),
                _audit_elapsed, "error",
            )
            return (tc, error_msg, True)


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

    def _extract_file_parts(self, result: str) -> str:
        """从工具结果中提取 [FILE] 标记，暂存为 FilePart

        [FILE] 标记替换为占位文本（LLM 可读），FilePart 存到
        self._pending_file_parts，在工具执行后插入 _content_blocks。

        占位文本按 mime 类型区分：
        - 图片：提示"将自动展示"，引导 LLM 只写结论不重复图表数据
        - 其他：保留文件名，提示"下载卡片将自动展示"
        """
        if not result or "[FILE]" not in result:
            return result

        from schemas.message import FilePart

        def _replace_match(m):
            url, name, mime_type, size = m.groups()
            self._pending_file_parts.append(FilePart(
                url=url, name=name, mime_type=mime_type, size=int(size),
            ))
            # LLM 上下文不暴露 URL（防止 LLM 幻觉篡改域名）
            if mime_type.startswith("image/"):
                return "📊 图表已生成（将自动展示给用户，不要在文字中重复描述图表数据）"
            return f"📎 文件已生成: {name}（下载卡片将自动展示，不要重复引用文件名）"

        return _FILE_PATTERN.sub(_replace_match, result)


def _partition_tool_calls(
    tool_calls: List[Dict[str, Any]],
) -> List[tuple]:
    """按并发安全性分批：连续的只读工具合并为一批并行，写操作单独一批串行"""
    from config.chat_tools import is_concurrency_safe

    batches: List[tuple] = []
    current_batch: List[Dict[str, Any]] = []
    current_safe = True

    for tc in tool_calls:
        safe = is_concurrency_safe(tc["name"])
        if safe and current_safe and current_batch:
            current_batch.append(tc)
        elif safe and not current_batch:
            current_safe = True
            current_batch = [tc]
        else:
            if current_batch:
                batches.append((current_safe, current_batch))
            current_batch = [tc]
            current_safe = safe

    if current_batch:
        batches.append((current_safe, current_batch))

    return batches


def accumulate_tool_call_delta(
    acc: Dict[int, Dict[str, Any]], deltas: list,
) -> None:
    """将流式 tool_call 增量累积到 acc 字典中"""
    for tc_delta in deltas:
        idx = tc_delta.index
        if idx not in acc:
            acc[idx] = {"id": "", "name": "", "arguments": ""}
        entry = acc[idx]
        if tc_delta.id:
            entry["id"] = tc_delta.id
        if tc_delta.name:
            entry["name"] = tc_delta.name
        if tc_delta.arguments_delta:
            entry["arguments"] += tc_delta.arguments_delta
