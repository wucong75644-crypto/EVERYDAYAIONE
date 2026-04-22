"""
工具执行摘要（Tool Execution Digest）

从工具循环 messages 数组中提取结构化摘要，持久化到 generation_params.tool_digest，
下轮加载历史时注入 LLM 上下文，补全跨轮信息断裂。

设计文档：docs/document/TECH_工具执行摘要.md（待补）
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger


# staging 路径正则：匹配 STAGING_DIR + "/tool_result_xxx.txt" 或完整路径
_STAGING_RE = re.compile(
    r'STAGING_DIR\s*\+\s*"/(tool_result_[^"]+\.txt)"'
)

# 归档消息中保留的 staging 路径正则
_ARCHIVED_STAGING_RE = re.compile(
    r'数据文件:\s*STAGING_DIR\s*\+\s*"/(tool_result_[^"]+\.txt)"'
)

# 错误标记（字面量匹配，不是正则）
_ERROR_MARKERS = ("❌", "执行错误", "查询超时", "执行异常")

# digest JSON 序列化最大字节数（超出则裁剪 hint）
_MAX_DIGEST_BYTES = 800

# 单条 hint 最大长度
_HINT_MAX_LEN = 50

# 最多保留的工具调用记录数
_MAX_TOOL_ENTRIES = 8


def build_tool_digest(
    messages: List[Dict[str, Any]],
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """从工具循环 messages 中提取执行摘要。

    扫描 assistant(tool_calls) + 对应的 tool result，提取：
    - 工具名 + 参数摘要
    - staging 文件路径（从原文或归档元数据中提取）
    - 成功/失败状态

    Args:
        messages: 工具循环内的完整消息列表
        conversation_id: 会话 ID

    Returns:
        摘要 dict 或 None（无工具调用时）
    """
    # 建立 tool_call_id → tool result 映射
    result_map: Dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id:
                content = msg.get("content", "")
                # content 可能是 list[dict]（AgentResult.to_message_content()），转为字符串
                if isinstance(content, list):
                    content = "\n".join(
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                result_map[tc_id] = content if isinstance(content, str) else str(content)

    # 扫描所有 assistant 消息中的 tool_calls
    entries: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls", [])
        if not tool_calls:
            continue

        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "unknown")
            args_str = func.get("arguments", "{}")

            # 提取参数摘要（query/code 字段优先）
            hint = _extract_hint(name, args_str)

            # 从对应的 tool result 提取 staging 路径 + 状态
            tc_id = tc.get("id", "")
            result_text = result_map.get(tc_id, "")
            staged = _extract_staging_path(result_text)
            ok = not _is_error(result_text)

            entry: Dict[str, Any] = {"name": name, "hint": hint, "ok": ok}
            if staged:
                entry["staged"] = staged
            entries.append(entry)

    if not entries:
        return None

    # 去重：同名工具 + 相同 hint 只保留一条
    entries = _deduplicate(entries)

    # 截断
    entries = entries[:_MAX_TOOL_ENTRIES]

    digest: Dict[str, Any] = {
        "tools": entries,
        "staging_dir": f"staging/{conversation_id}",
    }

    # 大小控制：超限则逐步裁剪 hint
    _enforce_size_limit(digest)

    return digest


def format_tool_digest(digest: Dict[str, Any]) -> str:
    """将摘要格式化为 LLM 可读的文本注解。

    注入到历史 assistant 消息尾部，让 LLM 知道上轮做了什么。

    Args:
        digest: build_tool_digest 返回的摘要 dict

    Returns:
        格式化文本
    """
    if not digest or not digest.get("tools"):
        return ""

    lines = ["\n\n[上轮工具执行记录]"]

    for t in digest["tools"]:
        status = "✓" if t.get("ok", True) else "✗"
        line = f"- {status} {t['name']}"
        if t.get("hint"):
            line += f": {t['hint']}"
        if t.get("staged"):
            line += f" → {t['staged']}"
        lines.append(line)

    staging_dir = digest.get("staging_dir", "")
    if staging_dir:
        lines.append(
            f"数据目录: {staging_dir}（数据文件 15 分钟内有效，超时需重新查询）"
        )

    return "\n".join(lines)


# ============================================================
# 内部函数
# ============================================================


def _extract_hint(tool_name: str, args_str: str) -> str:
    """从工具参数 JSON 中提取关键字段作为摘要。"""
    try:
        args = json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return args_str[:_HINT_MAX_LEN] if args_str else ""

    # 按优先级取关键字段
    for key in ("query", "question", "code", "keyword", "prompt"):
        val = args.get(key)
        if val and isinstance(val, str):
            return val[:_HINT_MAX_LEN]

    # 兜底：取第一个字符串值
    for val in args.values():
        if isinstance(val, str) and val:
            return val[:_HINT_MAX_LEN]

    return ""


def _extract_staging_path(result_text: str) -> Optional[str]:
    """从工具结果中提取 staging 文件路径。

    支持两种来源：
    1. 原始 <persisted-output> 标签中的路径
    2. 归档后保留的 "数据文件: ..." 元数据
    """
    if not result_text:
        return None

    # 原始格式
    match = _STAGING_RE.search(result_text)
    if match:
        return match.group(1)

    # 归档格式
    match = _ARCHIVED_STAGING_RE.search(result_text)
    if match:
        return match.group(1)

    return None


def _is_error(result_text: str) -> bool:
    """判断工具结果是否为错误。"""
    if not result_text:
        return False
    # 归档消息不算错误
    if result_text.startswith("[已归档]"):
        return False
    for marker in _ERROR_MARKERS:
        if marker in result_text:
            return True
    return False


def _deduplicate(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去重：同名工具 + 相同 hint 只保留最后一条。"""
    seen: Dict[str, int] = {}
    result: List[Dict[str, Any]] = []
    for entry in entries:
        key = f"{entry['name']}:{entry.get('hint', '')}"
        if key in seen:
            # 替换为最新的（可能有更新的 staging 路径）
            result[seen[key]] = entry
        else:
            seen[key] = len(result)
            result.append(entry)
    return result


def _enforce_size_limit(digest: Dict[str, Any]) -> None:
    """确保 digest JSON 序列化不超过 _MAX_DIGEST_BYTES。

    超限时逐步裁剪 hint 字段。
    """
    serialized = json.dumps(digest, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= _MAX_DIGEST_BYTES:
        return

    # 第一步：裁剪所有 hint 到 20 字符
    for t in digest["tools"]:
        if t.get("hint") and len(t["hint"]) > 20:
            t["hint"] = t["hint"][:20]

    serialized = json.dumps(digest, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= _MAX_DIGEST_BYTES:
        return

    # 第二步：删除所有 hint
    for t in digest["tools"]:
        t.pop("hint", None)

    logger.debug(
        f"ToolDigest size enforced | final_size={len(json.dumps(digest, ensure_ascii=False))}"
    )
