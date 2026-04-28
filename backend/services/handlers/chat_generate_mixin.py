"""
ChatHandler 非流式生成 Mixin

提供 generate_complete() 方法：
- 复用 ChatHandler 的上下文构建 + 工具循环
- 不推 WebSocket，收集完整结果返回
- 企微等非 WebSocket 场景使用

依赖宿主类提供：
- self.db, self.org_id
- self._build_llm_messages() (ChatContextMixin)
- self._extract_text_content() (BaseHandler)
- self._build_memory_prompt() (ChatContextMixin)
- self._execute_tool_calls() (ChatToolMixin)
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart, TextPart
from services.handlers.chat_tool_mixin import accumulate_tool_call_delta


# 优雅降级提示（与 ChatHandler 保持一致）
_STOP_MESSAGES = {
    "max_turns": "查询涉及多个步骤，已达到工具调用上限。",
    "max_tokens": "本次查询数据量过大。",
    "wall_timeout": "查询耗时过长。",
}


class ChatGenerateMixin:
    """非流式生成能力（被 ChatHandler 继承）"""

    async def generate_complete(
        self,
        content: List[ContentPart],
        user_id: str,
        conversation_id: str,
        model_id: Optional[str] = None,
    ) -> List[ContentPart]:
        """非流式生成（企微等非 WebSocket 场景）

        复用 ChatHandler 的上下文构建 + 工具循环，但不推 WebSocket。
        返回 List[ContentPart]（TextPart + ImagePart + VideoPart）。
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

        try:
            # 4. 工具循环（与 _stream_generate 相同逻辑，无 WebSocket 推送）
            from services.agent.execution_budget import ExecutionBudget
            from core.config import get_settings as _get_budget_settings
            _bs = _get_budget_settings()
            _budget = ExecutionBudget(
                max_turns=_bs.budget_max_turns,
                max_tokens=_bs.budget_max_tokens,
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
                    break  # 无工具调用，生成完成

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

                tool_results = await self._execute_tool_calls(
                    completed_calls, "wecom_task", conversation_id,
                    "wecom_msg", user_id, turn + 1, messages=messages,
                    budget=_budget,
                )
                from services.agent.agent_result import AgentResult
                for tc, result, is_error in tool_results:
                    if isinstance(result, AgentResult):
                        content = result.to_message_content()
                        tool_context.update_from_result(
                            tc["name"], result.summary, is_error,
                        )
                    else:
                        content = result
                        tool_context.update_from_result(
                            tc["name"], result, is_error,
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": content,
                    })

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

            # 优雅降级
            _stop = _budget.stop_reason
            if _stop and accumulated_text:
                accumulated_text += f"\n\n> ⚠️ {_STOP_MESSAGES.get(_stop, '执行超限')}"
            elif _stop and not accumulated_text:
                accumulated_text = _STOP_MESSAGES.get(_stop, "执行超限，请稍后重试")

        except Exception as e:
            logger.error(f"generate_complete error | error={e}")
            if not accumulated_text:
                return [TextPart(text="生成回复时遇到了问题，请稍后再试。")]
        finally:
            await adapter.close()

        return extract_media_parts(accumulated_text)
