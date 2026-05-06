"""
ChatHandler 非流式生成 Mixin

提供 generate_complete() 方法：
- 复用 ChatHandler 的上下文构建 + 工具循环
- 不推 WebSocket，收集完整结果返回
- 企微等非 WebSocket 场景使用
- 收集 tool_step 块 + 构建 tool_digest（与 Web 端 _stream_generate 对齐）

依赖宿主类提供：
- self.db, self.org_id
- self._build_llm_messages() (ChatContextMixin)
- self._extract_text_content() (BaseHandler)
- self._build_memory_prompt() (ChatContextMixin)
- self._execute_tool_calls() (ChatToolMixin)
"""

import json
import time as _time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart, TextPart
from services.handlers.chat_tool_mixin import accumulate_tool_call_delta


# 优雅降级提示（与 ChatHandler 保持一致，有 Final Synthesis Turn 后仅作兜底）
_STOP_MESSAGES = {
    "wrap_up_budget": "接近执行上限，正在总结当前进展。",
    "max_turns": "已达到单次对话工具调用上限。",
    "max_tokens": "本次任务消耗的资源过大，请缩小范围或分步进行。",
    "wall_timeout": "任务耗时过长，请稍后重试。",
}


@dataclass
class GenerateResult:
    """generate_complete 的返回结果，携带 tool_step 和 tool_digest。"""
    parts: List[ContentPart]
    content_blocks: List[Dict[str, Any]] = field(default_factory=list)
    tool_digest: Optional[Dict[str, Any]] = None


def unpack_tool_result(result) -> tuple:
    """统一解包工具返回值 → (message_content, summary_text)。

    Web 端 _stream_generate 和企微端 generate_complete 共用，
    避免新增 result 类型时两处不同步。

    Returns:
        (msg_content, summary_text):
            msg_content — 塞进 messages[role=tool] 的内容
            summary_text — 写入 tool_step.summary 的摘要（≤500字）
    """
    from services.agent.agent_result import AgentResult
    from services.file_executor import FileReadResult

    if isinstance(result, AgentResult):
        return result.to_message_content(), (result.summary or "")[:500]
    if isinstance(result, FileReadResult):
        return result.text, result.text[:500]
    if isinstance(result, str):
        return result, result[:500]
    # 兜底：未知类型强转 str
    s = str(result)
    return s, s[:500]


class ChatGenerateMixin:
    """非流式生成能力（被 ChatHandler 继承）"""

    async def generate_complete(
        self,
        content: List[ContentPart],
        user_id: str,
        conversation_id: str,
        model_id: Optional[str] = None,
    ) -> GenerateResult:
        """非流式生成（企微等非 WebSocket 场景）

        复用 ChatHandler 的上下文构建 + 工具循环，但不推 WebSocket。
        返回 GenerateResult（parts + content_blocks + tool_digest），
        与 Web 端 _stream_generate 的持久化结构对齐。
        """
        from config.chat_tools import get_core_tools, get_tools_by_names, get_tool_system_prompt
        from services.adapters.factory import DEFAULT_MODEL_ID, create_chat_adapter
        from services.handlers.media_extractor import extract_media_parts
        from services.handlers.tool_loop_context import ToolLoopContext

        model_id = model_id or DEFAULT_MODEL_ID
        text_content = self._extract_text_content(content)

        # 1. 构建消息（记忆 + 摘要 + 历史 + 用户消息）
        memory_prompt = await self._build_memory_prompt(user_id, text_content)
        messages = await self._build_llm_messages(
            content, user_id, conversation_id, text_content,
            prefetched_memory=memory_prompt,
        )

        # 2. 注入工具提示 + 加载核心工具
        tool_prompt = get_tool_system_prompt()
        if tool_prompt:
            messages.append({"role": "system", "content": tool_prompt})
        core_tools = get_core_tools(org_id=self.org_id)

        # 3. 创建适配器
        adapter = create_chat_adapter(model_id, org_id=self.org_id, db=self.db)
        tool_context = ToolLoopContext(org_id=self.org_id, agent_domain="general")
        accumulated_text = ""
        _content_blocks: List[Dict[str, Any]] = []

        try:
            # 4. 工具循环（与 _stream_generate 相同逻辑，无 WebSocket 推送）
            from services.agent.execution_budget import ExecutionBudget
            from core.config import get_settings as _get_budget_settings
            _bs = _get_budget_settings()
            _budget = ExecutionBudget(
                max_turns=_bs.budget_max_turns,
                max_wall_time=_bs.budget_max_wall_time,
            )

            while not _budget.stop_reason:
                _budget.use_turn()
                turn = _budget.turns_used - 1
                current_tools = list(core_tools)
                if tool_context.discovered_tools:
                    from config.tool_domains import filter_tools_for_domain
                    discovered = get_tools_by_names(
                        tool_context.discovered_tools, org_id=self.org_id,
                    )
                    discovered = filter_tools_for_domain(discovered, "general")
                    core_names = {t["function"]["name"] for t in core_tools}
                    current_tools.extend(
                        t for t in discovered if t["function"]["name"] not in core_names
                    )

                if turn > 0:
                    from services.handlers.context_compressor import deduplicate_system_prompts
                    deduplicate_system_prompts(messages)
                    ctx_prompt = tool_context.build_context_prompt()
                    if ctx_prompt:
                        messages.append({"role": "system", "content": ctx_prompt})

                turn_text = ""
                tool_calls_acc: Dict[int, Dict[str, Any]] = {}

                async for chunk in adapter.stream_chat(
                    messages=messages, tools=current_tools,
                ):
                    if chunk.content:
                        turn_text += chunk.content
                        accumulated_text += chunk.content
                    if chunk.tool_calls:
                        accumulate_tool_call_delta(tool_calls_acc, chunk.tool_calls)

                if not tool_calls_acc:
                    # 无工具调用，记录最终文本块
                    if turn_text:
                        _content_blocks.append({"type": "text", "text": turn_text})
                    break

                # 执行工具
                completed_calls = sorted(
                    tool_calls_acc.values(), key=lambda x: x.get("id", ""),
                )
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant", "content": turn_text or None,
                }
                assistant_msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in completed_calls
                ]
                messages.append(assistant_msg)

                # 收集 tool_step(running)（对齐 Web 端 chat_handler.py L700-719）
                _tool_step_start_times: Dict[str, float] = {}
                for tc in completed_calls:
                    _tool_step: Dict[str, Any] = {
                        "type": "tool_step",
                        "tool_name": tc["name"],
                        "tool_call_id": tc["id"],
                        "status": "running",
                    }
                    if tc["name"] == "code_execute":
                        try:
                            _ce_args = json.loads(tc.get("arguments", "{}"))
                            _ce_code = _ce_args.get("code", "")[:2000]
                            if _ce_code:
                                _tool_step["code"] = _ce_code
                        except Exception:
                            pass
                    _content_blocks.append(_tool_step)
                    _tool_step_start_times[tc["id"]] = _time.monotonic()

                tool_results = await self._execute_tool_calls(
                    completed_calls, "wecom_task", conversation_id,
                    "wecom_msg", user_id, turn + 1, messages=messages,
                    budget=_budget,
                )
                for tc, result, is_error in tool_results:
                    msg_content, summary_text = unpack_tool_result(result)
                    tool_context.update_from_result(
                        tc["name"], summary_text, is_error,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": msg_content,
                    })

                    # 更新 tool_step 状态（对齐 Web 端 chat_handler.py L773-787）
                    _tc_id = tc["id"]
                    _tc_start = _tool_step_start_times.get(_tc_id)
                    _elapsed = int((_time.monotonic() - _tc_start) * 1000) if _tc_start else 0
                    for _blk in _content_blocks:
                        if _blk.get("type") == "tool_step" and _blk.get("tool_call_id") == _tc_id:
                            _blk["status"] = "error" if is_error else "completed"
                            _blk["elapsed_ms"] = _elapsed
                            _blk["summary"] = summary_text
                            break

                # 层4+5: 旧工具结果归档 + 循环内摘要
                from services.handlers.context_compressor import (
                    compact_stale_tool_results, compact_loop_with_summary,
                )
                from core.config import get_settings as _get_settings
                _s = _get_settings()
                compact_stale_tool_results(messages, _s.context_tool_keep_turns)
                if turn >= 3:
                    await compact_loop_with_summary(
                        messages, _s.context_max_tokens,
                        _s.context_loop_summary_trigger,
                    )

                logger.info(
                    f"generate_complete tool turn {turn + 1} | "
                    f"tools={[c['name'] for c in completed_calls]}"
                )

            # 优雅降级：Final Synthesis Turn
            _stop = _budget.stop_reason
            if _stop:
                from services.agent.stop_policy import synthesize_wrap_up
                _synthesis = await synthesize_wrap_up(
                    adapter=adapter,
                    messages=messages,
                    reason=_STOP_MESSAGES.get(_stop, _stop),
                )
                if _synthesis:
                    accumulated_text = _synthesis
                elif accumulated_text:
                    accumulated_text += f"\n\n> ⚠️ {_STOP_MESSAGES.get(_stop, '执行超限')}"
                else:
                    accumulated_text = _STOP_MESSAGES.get(_stop, "执行超限，请稍后重试")

            # 构建 tool_digest（对齐 Web 端 chat_handler.py L1112-1118）
            _tool_digest = None
            if _budget.turns_used > 1:
                from services.handlers.tool_digest import build_tool_digest
                try:
                    _tool_digest = build_tool_digest(messages, conversation_id)
                except Exception as _digest_err:
                    logger.warning(f"generate_complete tool_digest build failed | error={_digest_err}")

        except Exception as e:
            logger.error(f"generate_complete error | error={e}")
            if not accumulated_text:
                return GenerateResult(parts=[TextPart(text="生成回复时遇到了问题，请稍后再试。")])
        finally:
            await adapter.close()

        return GenerateResult(
            parts=extract_media_parts(accumulated_text),
            content_blocks=_content_blocks,
            tool_digest=_tool_digest,
        )
