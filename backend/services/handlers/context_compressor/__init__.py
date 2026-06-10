"""上下文压缩器（拆分为 4 个子模块，公共接口经此包重导出）

六层压缩：
- 层1: tool_result_envelope.wrap() — 工具结果截断+信号（chat_tool_mixin / erp_agent）
- 层2: 滑动窗口 N=10（config.chat_context_limit，chat_context_mixin）
- 层3: 对话级滚动摘要（context_summarizer → DB，chat_context_mixin）
- 层4: compact_stale_tool_results / compact_stale_by_user_turns — 旧轮次工具结果归档（零 API）
- 层5: compact_loop_with_summary — 循环内 LLM 摘要（触发式）
- 层6: enforce_budget / enforce_tool_budget / enforce_history_budget — Token 预算兜底

子模块：
- tokens:   token 估算 + 文本提取 + 归档判断 + system prompt 去重
- archive:  层4 工具结果归档（含轮次识别）
- budget:   层6 Token 预算管理
- summary:  层5 LLM 摘要
"""

from services.handlers.context_compressor.tokens import (
    _CHARS_PER_TOKEN,
    _extract_text,
    _is_archived,
    _msg_tokens,
    deduplicate_system_prompts,
    estimate_tokens,
)
from services.handlers.context_compressor.archive import (
    _STAGING_PATH_RE,
    _build_tc_name_map,
    _extract_archive_meta,
    _identify_tool_turns,
    _identify_user_turns,
    compact_stale_by_user_turns,
    compact_stale_tool_results,
)
from services.handlers.context_compressor.budget import (
    _adjust_cut_for_tool_pairs,
    _enforce_history_budget_core,
    _find_reverse_accumulation_cut,
    enforce_budget,
    enforce_history_budget,
    enforce_history_budget_sync,
    enforce_tool_budget,
)
from services.handlers.context_compressor.summary import (
    _LOOP_SUMMARY_PROMPT,
    _build_loop_summary_input,
    compact_loop_with_summary,
)


async def compress_messages_if_needed(
    messages: list,
    conv_source: str = "web",
) -> tuple[list, str]:
    """V3.3: 统一压缩入口 — 跨轮加载 / cache 写入 / 冷启动复用。

    根据 messages 当前 token 大小自动选择压缩层级:
      NORMAL      (< trigger): 不动
      ARCHIVED   :   层 4 归档旧 tool_result
      SUMMARIZED:    层 4 + 层 5 LLM 摘要旧轮对话
      ENFORCED  :    层 4 + 层 5 + 层 6 分桶兜底强制截断

    复用工具循环里同一套压缩函数,保证压缩逻辑单源 SSOT。

    Args:
        messages: OpenAI 格式 messages 列表(原地修改)
        conv_source: "web"(大预算容量触发) | "wecom"(小预算激进)

    Returns:
        (messages, state)
        state: "NORMAL" | "ARCHIVED" | "SUMMARIZED" | "ENFORCED"
    """
    from core.config import get_settings
    _s = get_settings()

    initial_tokens = estimate_tokens(messages)

    if conv_source == "wecom":
        max_tokens = _s.context_max_tokens                  # 32K
        history_budget = _s.context_history_token_budget    # 8K
        tool_budget = _s.context_tool_token_budget          # 6K
        summary_trigger = _s.context_loop_summary_trigger   # 0.8
        keep_turns = _s.context_tool_keep_turns             # 2
    else:
        max_tokens = _s.context_web_max_tokens              # 200K
        history_budget = _s.context_web_history_token_budget
        tool_budget = _s.context_web_tool_token_budget
        summary_trigger = _s.context_web_compact_trigger    # 0.7
        keep_turns = _s.context_web_keep_user_turns         # 10

    # NORMAL 早返回 — 没超 trigger 不动任何东西
    # trigger * max_tokens 是层 4/5 的触发阈值
    if initial_tokens < int(max_tokens * summary_trigger):
        return messages, "NORMAL"

    state = "NORMAL"

    # 层 4 — 归档旧 tool_result(零成本规则提取)
    if conv_source == "wecom":
        compact_stale_tool_results(messages, keep_turns)
    else:
        compact_stale_by_user_turns(
            messages,
            keep_user_turns=keep_turns,
            capacity_trigger=summary_trigger,
            max_tokens=max_tokens,
        )

    tokens_after_layer4 = estimate_tokens(messages)
    if tokens_after_layer4 < initial_tokens:
        state = "ARCHIVED"

    # 层 5 — LLM 摘要(超 trigger * max 才触发)
    if tokens_after_layer4 > max_tokens * summary_trigger:
        try:
            await compact_loop_with_summary(
                messages, max_tokens, summary_trigger,
            )
            state = "SUMMARIZED"
        except Exception as e:  # noqa: BLE001
            # LLM 摘要失败不阻塞,继续走层 6 兜底
            from loguru import logger
            logger.warning(f"compact_loop_with_summary failed: {e}")

    # 层 6 — 分桶兜底(强制截断)
    tokens_after_layer5 = estimate_tokens(messages)
    if tokens_after_layer5 > max_tokens:
        enforce_tool_budget(messages, tool_budget)
        enforce_history_budget_sync(messages, history_budget)
        state = "ENFORCED"

    return messages, state


__all__ = [
    # tokens
    "_CHARS_PER_TOKEN",
    "_extract_text",
    "_is_archived",
    "_msg_tokens",
    "deduplicate_system_prompts",
    "estimate_tokens",
    # archive
    "_STAGING_PATH_RE",
    "_build_tc_name_map",
    "_extract_archive_meta",
    "_identify_tool_turns",
    "_identify_user_turns",
    "compact_stale_by_user_turns",
    "compact_stale_tool_results",
    # budget
    "_adjust_cut_for_tool_pairs",
    "_enforce_history_budget_core",
    "_find_reverse_accumulation_cut",
    "enforce_budget",
    "enforce_history_budget",
    "enforce_history_budget_sync",
    "enforce_tool_budget",
    # summary
    "_LOOP_SUMMARY_PROMPT",
    "_build_loop_summary_input",
    "compact_loop_with_summary",
    # V3.3 统一入口
    "compress_messages_if_needed",
]
