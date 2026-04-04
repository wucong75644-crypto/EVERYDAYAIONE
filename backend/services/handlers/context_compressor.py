"""
上下文压缩器

四层压缩：
- 层1: compress_tool_result — 工具结果即时压缩（每次工具调用后）
- 层2: 滑动窗口 N=5 轮（由 config.chat_context_limit 控制，在 chat_context_mixin 中实现）
- 层3: 滚动摘要（由 context_summarizer 实现，增强 prompt）
- 层4: enforce_budget — Token 预算兜底 + deduplicate_system_prompts
"""

import re
from typing import Any, Dict, List

from loguru import logger


# ============================================================
# 层1: 工具结果即时压缩
# ============================================================

# ERP 工具名前缀
_ERP_PREFIXES = ("erp_", "local_")

# 不需要压缩的工具（返回本身就短）
_NO_COMPRESS = {"generate_image", "generate_video", "erp_agent"}

# 汇总行关键词（ERP 结果通常以这些开头的行是汇总）
_SUMMARY_LINE_PATTERNS = re.compile(
    r"^(?:汇总|合计|共|总计|统计|小计)[：:]",
    re.MULTILINE,
)

# 压缩后的最大字符数（兜底）
_COMPRESS_MAX_CHARS = 500


def compress_tool_result(tool_name: str, result: str) -> str:
    """层1: 按工具类型差异化压缩工具结果

    完整数据已推送给用户 + 存 DB，messages 里只需要精简结论。
    LLM 需要更多细节时会自己重查。

    Args:
        tool_name: 工具名称
        result: 工具执行的完整结果文本

    Returns:
        精简后的结果（~200-500 字符）
    """
    if not result:
        return result

    # 短结果不压缩
    if len(result) <= _COMPRESS_MAX_CHARS:
        return result

    # 不需要压缩的工具
    if tool_name in _NO_COMPRESS:
        return result

    # ERP 查询结果：保留首行 + 汇总行
    if tool_name.startswith(_ERP_PREFIXES):
        return _compress_erp_result(tool_name, result)

    # 代码执行：保留最后 10 行 + 完整错误
    if tool_name == "code_execute":
        return _compress_code_result(result)

    # 搜索类：保留前 3 条
    if tool_name in ("web_search", "search_knowledge", "erp_api_search"):
        return _compress_search_result(result)

    # 爬虫：保留前 3 条
    if tool_name == "social_crawler":
        return _compress_search_result(result)

    # 文件操作：前 500 字符
    if tool_name.startswith("file_"):
        return result[:_COMPRESS_MAX_CHARS] + f"\n...(共{len(result)}字符，已省略)"

    # 默认：前 500 字符
    return result[:_COMPRESS_MAX_CHARS] + f"\n...(共{len(result)}字符，已省略)"


def _compress_erp_result(tool_name: str, result: str) -> str:
    """ERP 查询结果压缩：首行标题 + 汇总/统计行"""
    lines = result.split("\n")

    # 首行（通常是标题/概要）
    first_line = lines[0].strip() if lines else ""

    # 查找汇总行
    summary_lines = []
    for line in lines:
        if _SUMMARY_LINE_PATTERNS.search(line.strip()):
            summary_lines.append(line.strip())

    # 如果有汇总行，用首行 + 汇总行
    if summary_lines:
        compressed = first_line + "\n" + "\n".join(summary_lines)
        return compressed[:_COMPRESS_MAX_CHARS]

    # 无汇总行：首行 + 前 3 行数据 + 总行数提示
    data_lines = [l for l in lines[1:] if l.strip() and not l.startswith("---")]
    preview = data_lines[:3]
    total = len(data_lines)
    compressed = first_line
    if preview:
        compressed += "\n" + "\n".join(preview)
    if total > 3:
        compressed += f"\n...(共{total}行，已省略{total - 3}行)"
    return compressed[:_COMPRESS_MAX_CHARS]


def _compress_code_result(result: str) -> str:
    """代码执行结果压缩：保留最后 10 行 + 完整错误"""
    # 错误优先完整保留
    if result.startswith("❌") or "Error" in result[:200]:
        return result[:_COMPRESS_MAX_CHARS]

    lines = result.split("\n")
    if len(lines) <= 10:
        return result

    tail = lines[-10:]
    return f"...(前{len(lines) - 10}行已省略)\n" + "\n".join(tail)


def _compress_search_result(result: str) -> str:
    """搜索结果压缩：保留前 3 条"""
    lines = result.split("\n")

    # 按条目分割（通常以 - 或 数字. 开头）
    items = []
    current = []
    for line in lines:
        if re.match(r"^[-\d•]", line.strip()) and current:
            items.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        items.append("\n".join(current))

    if len(items) <= 3:
        return result

    preview = "\n".join(items[:3])
    return preview + f"\n...(共{len(items)}条结果，已省略{len(items) - 3}条)"


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
