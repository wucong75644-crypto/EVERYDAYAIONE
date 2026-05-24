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
]
