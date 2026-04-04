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


# 与 ChatHandler 共享的最大轮次常量
MAX_TOOL_TURNS = 10


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
        tool_context = ToolLoopContext(org_id=self.org_id)
        accumulated_text = ""

        try:
            # 4. 工具循环（与 _stream_generate 相同逻辑，无 WebSocket 推送）
            for turn in range(MAX_TOOL_TURNS):
                current_tools = list(core_tools)
                if tool_context.discovered_tools:
                    discovered = get_tools_by_names(
                        tool_context.discovered_tools, org_id=self.org_id,
                    )
                    core_names = {t["function"]["name"] for t in core_tools}
                    current_tools.extend(
                        t for t in discovered if t["function"]["name"] not in core_names
                    )

                if turn > 0:
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
                )
                for tc, result_text, is_error in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })
                    tool_context.update_from_result(tc["name"], result_text, is_error)

                logger.info(
                    f"generate_complete tool turn {turn + 1} | "
                    f"tools={[c['name'] for c in completed_calls]}"
                )

        except Exception as e:
            logger.error(f"generate_complete error | error={e}")
            if not accumulated_text:
                return [TextPart(text="生成回复时遇到了问题，请稍后再试。")]
        finally:
            await adapter.close()

        return extract_media_parts(accumulated_text)
