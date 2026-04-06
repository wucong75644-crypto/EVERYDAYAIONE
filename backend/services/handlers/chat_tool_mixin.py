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
    r'\[FILE\](https?://\S+?)\|([^|]+)\|([^|]+)\|(\d+)\[/FILE\]'
)

from schemas.websocket import (
    build_tool_result,
    build_tool_confirm_request,
)
from services.websocket_manager import ws_manager


class ChatToolMixin:
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
    ) -> List[tuple]:
        """执行工具调用：安全检查 → 并行/串行分批 → 返回结果

        Args:
            messages: 当前对话 messages（传给 erp_agent 做上下文筛选）

        Returns:
            List of (tool_call_dict, result_text, is_error)
        """
        from config.chat_tools import is_concurrency_safe
        from services.tool_executor import ToolExecutor

        executor = ToolExecutor(
            db=self.db, user_id=user_id,
            conversation_id=conversation_id, org_id=self.org_id,
        )
        # 传递上下文给 erp_agent
        executor._task_id = task_id
        executor._parent_messages = messages
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
            # TODO: 等待用户确认（Phase 3 实现 ws.py 路由 + asyncio.Event）
            # 当前阶段：dangerous 工具暂时拒绝执行，返回提示让 AI 告知用户
            return (
                tc,
                f"⚠ 写操作 {tool_name} 需要用户确认，请告知用户操作内容并等待确认。",
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

        try:
            result = await executor.execute(tool_name, args)
            # 提取 [FILE] 标记 → FilePart 暂存到 ChatHandler（不经过 LLM）
            result = self._extract_file_parts(result)
            # 工具结果压缩（完整数据已推送用户，messages 里只放精简版）
            from services.handlers.context_compressor import compress_tool_result
            result = compress_tool_result(tool_name, result)
            # 通知前端工具完成
            await ws_manager.send_to_task_or_user(
                task_id, user_id,
                build_tool_result(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    success=True,
                    summary=result[:100] if result else "",
                    turn=turn,
                ),
            )
            return (tc, result, False)
        except Exception as e:
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
            return (tc, error_msg, True)


    def _extract_file_parts(self, result: str) -> str:
        """从工具结果中提取 [FILE] 标记，暂存为 FilePart

        [FILE] 标记替换为友好文本（LLM 可读），FilePart 存到
        self._pending_file_parts，在 on_complete 时合并到消息中。
        """
        if not result or "[FILE]" not in result:
            return result

        from schemas.message import FilePart

        def _replace_match(m):
            url, name, mime_type, size = m.groups()
            self._pending_file_parts.append(FilePart(
                url=url, name=name, mime_type=mime_type, size=int(size),
            ))
            return f"📎 文件: {name}"

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


# ============================================================
# 结果摘要（与 dispatcher._GLOBAL_CHAR_BUDGET 对齐）
# ============================================================

_SUMMARY_THRESHOLD = 4000   # 超过此阈值触发摘要
_SUMMARY_PREVIEW = 2000     # 摘要中保留前 N 字符


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


def _summarize_if_needed(tool_name: str, result: str) -> str:
    """大结果自动摘要，引导 AI 用 code_execute 做全量分析

    远程 ERP 工具已有 dispatcher 4000 字符截断，此函数主要处理
    本地工具（local_*）和其他无截断的工具返回。
    """
    if not result or len(result) <= _SUMMARY_THRESHOLD:
        return result

    preview = result[:_SUMMARY_PREVIEW]
    return (
        f"{preview}\n\n"
        f"⚠ 结果较多（{len(result)}字符），以上为部分数据。\n"
        f"如需全量数据分析/导出，可用 code_execute 调用 "
        f"erp_query_all() 获取完整数据并用 pandas 分析。"
    )
