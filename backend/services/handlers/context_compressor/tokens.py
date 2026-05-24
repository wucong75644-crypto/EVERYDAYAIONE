"""Token 估算 + 文本提取 + 归档判断 + System Prompt 去重。

所有依赖此模块的子模块（archive/budget/summary）共用的基础工具。
本模块不依赖任何其他 context_compressor 子模块。
"""

from typing import Any, Dict, List


# 中英混合约 2.5 字符/token
_CHARS_PER_TOKEN = 2.5


def _extract_text(content: Any) -> str:
    """从 message content 提取纯文本（兼容 str 和 list[dict] 两种格式）。

    AgentResult.to_message_content() 返回 list[dict]，压缩器各环节
    需要统一用此函数提取文本，避免对 list 做字符串操作导致 TypeError。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content) if content else ""


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


def _msg_tokens(msg: Dict[str, Any]) -> int:
    """单条消息的 token 估算"""
    return estimate_tokens([msg])


def _is_archived(msg: Dict[str, Any]) -> bool:
    """检查消息是否已被归档（兼容 str / list[dict] 两种 content 格式）"""
    text = _extract_text(msg.get("content", ""))
    return text.startswith("[已归档")


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
