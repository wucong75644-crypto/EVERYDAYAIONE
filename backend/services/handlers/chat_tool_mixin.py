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
        budget=None,
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
        executor,
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        turn: int,
    ) -> tuple:
        """执行单个工具：安全检查 → 执行 → 返回 (tc, result, is_error, display_text)"""
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
            # 等待用户确认（60s 超时）
            approved = await ws_manager.wait_for_confirm(
                tool_call_id, timeout=60.0,
            )
            if not approved:
                _reject_msg = (
                    f"⚠ 用户拒绝或超时未确认写操作 {tool_name}。"
                    f"请告知用户操作未执行，询问是否需要重新确认。"
                )
                return (tc, _reject_msg, True, _reject_msg)

        # confirm 级别：通知用户（不阻塞）
        if safety == SafetyLevel.CONFIRM:
            logger.info(f"Tool confirm notify | tool={tool_name} | task={task_id}")

        # 执行工具
        try:
            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            _err = f"参数解析失败: {tc['arguments'][:100]}"
            return (tc, _err, True, _err)

        # get_file 翻译：文件名→绝对路径（按工具类型选 usage）
        args = _resolve_file_ids(args, conversation_id, tool_name)

        import time as _time
        _audit_start = _time.monotonic()
        try:
            result = await executor.execute(tool_name, args)
            _audit_elapsed = int((_time.monotonic() - _audit_start) * 1000)

            # AgentResult 直接返回（不做 str 操作，由上层处理）
            from services.agent.agent_result import AgentResult
            from services.handlers.chat_generate_mixin import extract_display_text
            if isinstance(result, AgentResult):
                display_text = extract_display_text(result)
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
                await self._push_tool_step_update(
                    task_id, conversation_id, message_id, user_id,
                    tool_name, tool_call_id,
                    success=not result.is_failure,
                    output=display_text,
                    elapsed_ms=_audit_elapsed,
                )
                self._emit_tool_audit(
                    task_id, conversation_id, user_id, tool_name,
                    tool_call_id, turn, args, len(result.summary),
                    _audit_elapsed, result.status,
                )
                return (tc, result, result.is_failure, display_text)

            # FormBlockResult 通道：暂存到 _pending_form_block
            from services.scheduler.chat_task_manager import FormBlockResult
            if isinstance(result, FormBlockResult):
                self._pending_form_block = result.form
                llm_text = result.llm_hint
                _form_display = "表单已展示"
                await ws_manager.send_to_task_or_user(
                    task_id, user_id,
                    build_tool_result(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        success=True,
                        summary=_form_display,
                        turn=turn,
                    ),
                )
                await self._push_tool_step_update(
                    task_id, conversation_id, message_id, user_id,
                    tool_name, tool_call_id,
                    success=True, output=_form_display, elapsed_ms=_audit_elapsed,
                )
                self._emit_tool_audit(
                    task_id, conversation_id, user_id, tool_name,
                    tool_call_id, turn, args, len(json.dumps(result.form)),
                    _audit_elapsed, "success",
                )
                return (tc, llm_text, False, _form_display)

            # FileReadResult（图片多模态）：直接透传给 chat_handler 处理
            from schemas.multimodal import FileReadResult
            if isinstance(result, FileReadResult):
                display_text = extract_display_text(result)
                raw_summary = result.text[:100] if result.text else ""
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
                await self._push_tool_step_update(
                    task_id, conversation_id, message_id, user_id,
                    tool_name, tool_call_id,
                    success=True, output=display_text,
                    elapsed_ms=_audit_elapsed,
                )
                self._emit_tool_audit(
                    task_id, conversation_id, user_id, tool_name,
                    tool_call_id, turn, args, len(result.text),
                    _audit_elapsed, "success",
                )
                return (tc, result, False, display_text)

            # 普通工具(str 路径) - emit 协议已统一,不再解析 [FILE] marker
            display_text = extract_display_text(result)
            raw_summary = result[:100] if result else ""
            # 截断+信号（messages 里只放精简版给 LLM）
            from services.agent.tool_result_envelope import (
                wrap_for_erp_agent, PERSISTED_OUTPUT_TAG,
            )
            result = wrap_for_erp_agent(tool_name, result)
            is_truncated = (
                PERSISTED_OUTPUT_TAG in result
                or "⚠ 输出过长" in result
            ) if result else False
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
            await self._push_tool_step_update(
                task_id, conversation_id, message_id, user_id,
                tool_name, tool_call_id,
                success=True, output=display_text,
                elapsed_ms=_audit_elapsed,
            )
            self._emit_tool_audit(
                task_id, conversation_id, user_id, tool_name,
                tool_call_id, turn, args, len(result),
                _audit_elapsed, "success", is_truncated,
            )
            return (tc, result, False, display_text)
        except Exception as e:
            _audit_elapsed = int((_time.monotonic() - _audit_start) * 1000)
            logger.error(f"Tool execution error | tool={tool_name} | task={task_id} | error={e}")
            error_msg = f"工具执行失败: {e}"
            display_text = str(e)
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
            await self._push_tool_step_update(
                task_id, conversation_id, message_id, user_id,
                tool_name, tool_call_id,
                success=False, output=display_text, elapsed_ms=_audit_elapsed,
            )
            self._emit_tool_audit(
                task_id, conversation_id, user_id, tool_name,
                tool_call_id, turn, args, len(error_msg),
                _audit_elapsed, "error",
            )
            return (tc, error_msg, True, display_text)


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


def _resolve_file_ids(
    args: Dict[str, Any], conversation_id: str, tool_name: str = "",
) -> Dict[str, Any]:
    """工具层无感拦截：按工具类型从注册表取正确路径 + 自检。

    不同工具取不同地址：
    - file_analyze → workspace（源文件）
    - file_delete → workspace
    - 其他 → 不翻译（code_execute 在沙盒内用 get_file）

    自检拦截（get_file 内部）：
    - 文件未注册 → FileNotFoundError
    - code 但没 parquet → FileNotFoundError（提示调 file_analyze）
    - 文件不存在 → FileNotFoundError
    拦截后错误回传给 LLM，LLM 自行重试。
    """
    from services.agent.file_path_cache import get_file_cache

    _ANALYZE_TOOLS = {"file_analyze"}
    _DELETE_TOOLS = {"file_delete"}
    if tool_name in _ANALYZE_TOOLS:
        usage = "analyze"
    elif tool_name in _DELETE_TOOLS:
        usage = "delete"
    else:
        return args

    cache = get_file_cache(conversation_id)

    # path 参数 — 用 get_file 自检拦截
    path_val = args.get("path")
    if isinstance(path_val, str) and path_val:
        try:
            resolved = cache.resolve_path(path_val, usage=usage)
            logger.debug(f"get_file | {tool_name} | {path_val} → {resolved}")
            args["path"] = resolved
        except FileNotFoundError:
            # 自检失败时静默透传（让工具内部的 resolve_safe_path 兜底）
            pass

    # files 参数 — 逐个自检
    files_val = args.get("files")
    if isinstance(files_val, list):
        translated = []
        for item in files_val:
            if isinstance(item, str):
                try:
                    resolved = cache.resolve_path(item, usage=usage)
                    logger.debug(f"get_file | {tool_name} | {item} → {resolved}")
                    translated.append(resolved)
                except FileNotFoundError:
                    translated.append(item)
            else:
                translated.append(item)
        args["files"] = translated

    return args


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
