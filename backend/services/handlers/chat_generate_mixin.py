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


def unpack_tool_result(result) -> str:
    """解包工具返回值 → LLM 上下文内容。仅用于 messages[role=tool]。

    Web 端 _stream_generate 和企微端 generate_complete 共用。
    """
    from services.agent.agent_result import AgentResult
    from schemas.multimodal import FileReadResult

    if isinstance(result, AgentResult):
        return result.to_message_content()
    if isinstance(result, FileReadResult):
        return result.text
    if isinstance(result, str):
        return result
    return str(result)


def extract_display_text(result) -> str:
    """提取工具结果的原始文本，用于前端 ToolStepCard 展开区展示。

    不截断、不结构化。工具自身已有尺寸限制，直接透传。
    """
    from services.agent.agent_result import AgentResult
    from schemas.multimodal import FileReadResult

    if isinstance(result, AgentResult):
        return result.summary or ""
    if isinstance(result, FileReadResult):
        return result.text or ""
    if isinstance(result, str):
        return result
    return str(result)


class ChatGenerateMixin:
    """非流式生成能力（被 ChatHandler 继承）"""

    def _get_conv_source(self, conversation_id: str) -> str:
        """读取并缓存 conversations.source 字段。

        缓存到 self._conv_source_cache（dict）按 conversation_id 索引，
        避免每轮压缩都查 DB。读取失败/为空时返回 "" （视为 Web）。

        Returns:
            "wecom" / "" / 其他字符串
        """
        cache = getattr(self, "_conv_source_cache", None)
        if cache is None:
            cache = {}
            self._conv_source_cache = cache
        if conversation_id in cache:
            return cache[conversation_id]

        source = ""
        try:
            conv = (
                self.db.table("conversations")
                .select("source")
                .eq("id", conversation_id)
                .maybe_single()
                .execute()
            )
            if conv and conv.data:
                source = conv.data.get("source") or ""
        except Exception as e:
            logger.warning(
                f"_get_conv_source failed | "
                f"conversation_id={conversation_id} | error={e}"
            )

        cache[conversation_id] = source
        return source

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
        from config.chat_tools import get_core_tools, get_tools_by_names
        from services.adapters.factory import DEFAULT_MODEL_ID, create_chat_adapter
        from services.handlers.media_extractor import extract_media_parts
        from services.handlers.tool_loop_context import ToolLoopContext

        model_id = model_id or DEFAULT_MODEL_ID
        text_content = self._extract_text_content(content)

        # 1. 构建消息（记忆 + 摘要 + 历史 + 用户消息）
        # V3.4: TOOL_SYSTEM_PROMPT 已合并到 PromptBuilder Layer 1, 不再单独注入
        memory_prompt = await self._build_memory_prompt(user_id, text_content)
        messages = await self._build_llm_messages(
            content, user_id, conversation_id, text_content,
            prefetched_memory=memory_prompt,
            permission_mode="auto",  # 企微非流式默认 auto
        )

        # 2. 加载核心工具 (工具提示已在 Layer 1, 不再单独注入)
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
                    # 不截断：input 同时承担 LLM 历史重建（function.arguments 必须合法 JSON）
                    _raw_args = tc.get("arguments", "")
                    if _raw_args:
                        _tool_step["input"] = _raw_args
                    if tc["name"] == "code_execute":
                        try:
                            _ce_args = json.loads(_raw_args or "{}")
                            _ce_code = _ce_args.get("code", "")
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
                for tc, result, is_error, display_text in tool_results:
                    msg_content = unpack_tool_result(result)
                    tool_context.update_from_result(
                        tc["name"], display_text, is_error,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": msg_content,
                    })

                    # 更新 tool_step 状态（对齐 Web 端 chat_handler.py）
                    _tc_id = tc["id"]
                    _tc_start = _tool_step_start_times.get(_tc_id)
                    _elapsed = int((_time.monotonic() - _tc_start) * 1000) if _tc_start else 0
                    for _blk in _content_blocks:
                        if _blk.get("type") == "tool_step" and _blk.get("tool_call_id") == _tc_id:
                            _blk["status"] = "error" if is_error else "completed"
                            _blk["elapsed_ms"] = _elapsed
                            _blk["output"] = display_text
                            break

                # 层4+5: 旧工具结果归档 + 循环内摘要
                # 设计文档：docs/document/TECH_Web端上下文压缩改造.md
                from services.handlers.context_compressor import (
                    compact_stale_tool_results, compact_stale_by_user_turns,
                    compact_loop_with_summary,
                )
                from core.config import get_settings as _get_settings
                _s = _get_settings()

                # 按来源分流——企微小预算激进压缩，Web 大预算容量触发
                # generate_complete 主要被企微调用，但保留分支以兼容未来 Web 非流式入口
                if self._get_conv_source(conversation_id) == "wecom":
                    compact_stale_tool_results(messages, _s.context_tool_keep_turns)
                    if turn >= 3:
                        await compact_loop_with_summary(
                            messages, _s.context_max_tokens,
                            _s.context_loop_summary_trigger,
                        )
                else:
                    compact_stale_by_user_turns(
                        messages,
                        keep_user_turns=_s.context_web_keep_user_turns,
                        capacity_trigger=_s.context_web_compact_trigger,
                        max_tokens=_s.context_web_max_tokens,
                    )
                    if turn >= 3:
                        await compact_loop_with_summary(
                            messages, _s.context_web_max_tokens,
                            _s.context_web_compact_trigger,
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
