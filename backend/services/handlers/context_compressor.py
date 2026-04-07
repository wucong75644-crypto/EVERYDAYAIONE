"""
上下文压缩器

三层压缩：
- 层1: tool_result_envelope.wrap() — 工具结果截断+信号（在 chat_tool_mixin / erp_agent 中调用）
- 层2: 滑动窗口 N=5 轮（由 config.chat_context_limit 控制，在 chat_context_mixin 中实现）
- 层3: 滚动摘要（由 context_summarizer 实现，增强 prompt）
- 层4: enforce_budget — Token 预算兜底 + deduplicate_system_prompts
"""

from typing import Any, Dict, List

from loguru import logger


# ============================================================
# 层4: Token 预算管理 + System Prompt 去重
# ============================================================

# 中英混合约 2.5 字符/token
_CHARS_PER_TOKEN = 2.5


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算 messages 列表的总 token 数（基于字符数，偏保守）"""
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
                    total_chars += len(str(part.get("url", "")))
        # tool_calls 参数也计入
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            total_chars += len(func.get("arguments", ""))
    return int(total_chars / _CHARS_PER_TOKEN)


def deduplicate_system_prompts(messages: List[Dict[str, Any]]) -> None:
    """移除工具循环中累积的重复 system prompt（原地修改）

    tool_context.build_context_prompt() 每轮 append 新的 system 消息，
    新一条包含旧一条的全部信息，旧的完全冗余。
    只保留最新一条含"已识别编码"/"已用工具"的 system 消息。
    """
    # 找到所有工具循环上下文 system 消息的索引
    ctx_indices = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if "已识别编码" in content or "已用工具" in content or "失败工具" in content:
                ctx_indices.append(i)

    # 只保留最后一条，删除更早的
    if len(ctx_indices) > 1:
        for idx in reversed(ctx_indices[:-1]):
            messages.pop(idx)


def enforce_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """超预算时逐步降级（原地修改）

    策略1: 去重 system prompts
    策略2: 从最旧的历史消息开始移除
    """
    # 策略1
    deduplicate_system_prompts(messages)
    if estimate_tokens(messages) <= max_tokens:
        return

    # 策略2: 移除最旧的非 system 消息（跳过 system 和最后几条）
    # 保护最后 6 条消息（当前轮的 user + assistant + tool）
    protected_tail = 6
    removable = []
    for i, msg in enumerate(messages):
        if i >= len(messages) - protected_tail:
            break
        if msg.get("role") in ("user", "assistant", "tool"):
            removable.append(i)

    # 从最旧的开始移除
    for idx in removable:
        if estimate_tokens(messages) <= max_tokens:
            break
        messages[idx]["content"] = "[已归档]"

    if estimate_tokens(messages) > max_tokens:
        logger.warning(
            f"Context still over budget after enforcement | "
            f"tokens={estimate_tokens(messages)} | max={max_tokens}"
        )
