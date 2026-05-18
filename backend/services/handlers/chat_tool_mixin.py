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
    r'\[FILE\]([^|]+)\|([^|]+)\|([^|]+)\|(\d+)(?:\|([^[\]]+))?\[/FILE\]'
)

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
        executor._pending_file_parts = []
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

        # ── AgentResult 处理（通信协议 §3.2）──
        from services.agent.agent_result import AgentResult
        for i, (tc, result, is_error, _display) in enumerate(results):
            if not isinstance(result, AgentResult):
                continue
            # ① 前端文件卡片通道
            logger.info(
                f"AgentResult file check | tool={tc['name']} | "
                f"collected_files={len(result.collected_files) if result.collected_files else 0} | "
                f"has_pending={hasattr(self, '_pending_file_parts')}"
            )
            if result.collected_files and hasattr(self, "_pending_file_parts"):
                from schemas.message import FilePart, ImagePart
                for f in result.collected_files:
                    # ImageAgent 返回 ImagePart 格式（type=image, url/width/height/alt）
                    if f.get("type") == "image":
                        self._pending_file_parts.append(ImagePart(
                            url=f.get("url"),
                            width=f.get("width"),
                            height=f.get("height"),
                            alt=f.get("alt", ""),
                            failed=f.get("failed") or None,
                            error=f.get("error") or None,
                            retry_context=f.get("retry_context") or None,
                        ))
                        logger.info(f"ImagePart added | alt={f.get('alt', '')[:30]} | failed={f.get('failed')} | url={f.get('url', 'None')[:80] if f.get('url') else 'None'}")
                    else:
                        # 其他工具返回 FilePart 格式（url/name/mime_type/size/workspace_path）
                        self._pending_file_parts.append(FilePart(
                            url=f["url"], name=f["name"],
                            mime_type=f["mime_type"], size=f.get("size"),
                            workspace_path=f.get("workspace_path"),
                        ))
                        logger.info(f"FilePart added | name={f['name']} | url={f['url'][:80]}")
            # ③ 展示文本（供 content_block_add 推送）
            self._last_erp_display_text = result.summary
            self._last_erp_display_files = result.collected_files or []
            # ④ token 统计
            self._erp_agent_tokens = (
                getattr(self, "_erp_agent_tokens", 0) + result.tokens_used
            )
            # schema 注入已移除（对齐 Claude 模式：AI 在沙盒自主探索）

        # 清理遗留 _pending_schemas（兼容 fetch_all_pages 等仍写入的场景）
        if executor._pending_schemas:
            executor._pending_schemas.clear()

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

        # 透传 ECharts 配置（sandbox JSON 读取 → chat_handler chart block）
        if hasattr(executor, "_chart_options") and executor._chart_options:
            if not hasattr(self, "_chart_options"):
                self._chart_options = {}
            self._chart_options.update(executor._chart_options)

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
                # 提取 [FILE] 标记 → FilePart 暂存（code_execute 生成的文件）
                if result.summary and "[FILE]" in result.summary:
                    result.summary = self._extract_file_parts(result.summary)
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
            from services.file_executor import FileReadResult
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

            # 普通工具（str 路径）
            # 提取 [FILE] 标记 → FilePart 暂存到 ChatHandler（不经过 LLM）
            result = self._extract_file_parts(result)
            # display_text 取 wrap 之前的完整结果（前端展示用）
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
            url, name, mime_type, size, ws_path = m.groups()
            self._pending_file_parts.append(FilePart(
                url=url, name=name, mime_type=mime_type, size=int(size),
                workspace_path=ws_path,
            ))
            # LLM 上下文不暴露 URL（防止 LLM 幻觉篡改域名）
            if name.endswith(".echart.json"):
                return "📊 交互式图表已生成（将自动展示给用户，不要在文字中重复描述图表数据）"
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


def _resolve_file_ids(
    args: Dict[str, Any], conversation_id: str, tool_name: str = "",
) -> Dict[str, Any]:
    """归一化匹配：按工具类型把文件名翻译为正确的绝对路径。

    不同工具取不同地址：
    - file_analyze / file_read → workspace（源文件）
    - file_delete → workspace
    - 其他 → 默认不翻译（code_execute 在沙盒内用 get_file）
    """
    from services.agent.file_path_cache import get_file_cache

    # 确定 usage
    _ANALYZE_TOOLS = {"file_analyze", "file_read"}
    _DELETE_TOOLS = {"file_delete"}
    if tool_name in _ANALYZE_TOOLS:
        usage = "analyze"
    elif tool_name in _DELETE_TOOLS:
        usage = "delete"
    else:
        return args  # 非文件工具不翻译

    cache = get_file_cache(conversation_id)

    # path 参数（file_analyze / file_read）
    path_val = args.get("path")
    if isinstance(path_val, str) and path_val:
        resolved = cache.resolve(path_val, usage=usage)
        if resolved:
            logger.debug(f"get_file translate | {tool_name} | {path_val} → {resolved}")
            args["path"] = resolved

    # files 参数（file_delete）
    files_val = args.get("files")
    if isinstance(files_val, list):
        translated = []
        for item in files_val:
            if isinstance(item, str):
                resolved = cache.resolve(item, usage=usage)
                if resolved:
                    logger.debug(f"get_file translate | {tool_name} | {item} → {resolved}")
                    translated.append(resolved)
                else:
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
