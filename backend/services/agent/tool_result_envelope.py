"""
工具结果信封 — 统一截断 + 信号层

所有工具返回结果经过此层包装后再放入 messages，确保：
1. LLM 上下文不被过长结果撑爆
2. 截断时明确告知模型（模型做知情决策）
3. 截断标注追加在末尾（保护 tool_loop_context 的正则匹配）

设计原则：
- 不替代 dispatcher 格式化层（4000 字行截断保持原样）
- 只管「结果 → messages」这一段
- 主 Agent / ERP Agent / erp_agent 结果 三种预算分开控制
"""

from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar
from typing import Dict, List, Optional

from loguru import logger


# ============================================================
# 大结果暂存（请求级内存，基于 contextvars 实现并发隔离）
#
# 原理：每个 asyncio.create_task() 自动拷贝 context，
# 不同请求的 _stream_generate 在不同 task 中运行，
# ContextVar 天然隔离，互不干扰。
#
# 注意：default=None 而非 default={}，避免所有 context 共享同一个 dict。
# ============================================================

_persisted_ctx: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "persisted_results", default=None,
)


def _get_store() -> Dict[str, str]:
    """获取当前请求的暂存字典（懒初始化）"""
    store = _persisted_ctx.get()
    if store is None:
        store = {}
        _persisted_ctx.set(store)
    return store


def persist_and_get_key(tool_name: str, result: str) -> str:
    """暂存完整结果到内存，返回 key 供 code_execute 读取"""
    digest = hashlib.md5(result.encode()).hexdigest()[:8]
    key = f"{tool_name}_{digest}"
    _get_store()[key] = result
    return key


def get_persisted(key: str) -> Optional[str]:
    """根据 key 获取暂存的完整结果"""
    return _get_store().get(key)


def clear_persisted() -> None:
    """请求结束后清理暂存（由 ChatHandler.finally 调用）"""
    store = _persisted_ctx.get()
    if store is not None:
        store.clear()
    _persisted_ctx.set(None)


# ============================================================
# 预算配置（字符数，非 token）
# ============================================================

# 主 Agent 工具循环：LLM 上下文有限，压缩激进一些
MAIN_AGENT_BUDGET = 2000

# ERP Agent 内部循环：需要更多上下文做多步推理
ERP_AGENT_BUDGET = 3000

# erp_agent 工具返回给主 Agent：结论文本，适度保留
ERP_AGENT_RESULT_BUDGET = 4000

# 不截断的工具
# - generate_image/video: 返回本身就短
# - code_execute: sandbox 自有 max_result_chars=8000 兜底，不需要二次截断
# - file_*: 返回通常 < 5K，且是 Agent 理解数据的关键信息
_NO_TRUNCATE = {
    "generate_image", "generate_video",
    "code_execute",
    "file_read", "file_write", "file_list", "file_search", "file_info",
}

# 汇总行关键词（ERP 结果以这些开头的行优先保留）
_SUMMARY_LINE_RE = re.compile(
    r"^(?:汇总|合计|共|总计|统计|小计)[：:]",
    re.MULTILINE,
)


# ============================================================
# 核心函数
# ============================================================

def wrap(
    tool_name: str,
    result: str,
    budget: Optional[int] = None,
) -> str:
    """包装工具结果：必要时截断 + 追加信号标注

    Args:
        tool_name: 工具名称
        result: 工具执行的完整结果文本
        budget: 字符预算（None 则按 tool_name 自动选择）

    Returns:
        处理后的结果（可能被截断 + 尾部标注）
    """
    if not result:
        return result

    # 不截断的工具
    if tool_name in _NO_TRUNCATE:
        return result

    # 已经被截断过的结果不再处理（防止双重截断）
    if "⚠ 输出已截断" in result:
        return result

    # 确定预算
    if budget is None:
        budget = _resolve_budget(tool_name)

    # 短结果不处理
    if len(result) <= budget:
        return result

    # 截断 + 信号标注
    truncated = _smart_truncate(tool_name, result, budget)
    return truncated


def wrap_for_erp_agent(tool_name: str, result: str) -> str:
    """ERP Agent 内部工具结果包装（预算 3000）"""
    return wrap(tool_name, result, budget=ERP_AGENT_BUDGET)


def wrap_erp_agent_result(result: str) -> str:
    """erp_agent 工具返回给主 Agent 的结论包装（预算 4000）。

    不仅截断，还加 pass-through prompt：禁止主 Agent 改写 erp_agent
    返回的结构化时间块和数据。这是对"主 Agent 重述时再产生 weekday
    幻觉"的防御性提示词加固，对应 PR2 审查发现的盲点。

    设计文档：docs/document/TECH_ERP时间准确性架构.md §14.7
    """
    truncated = wrap("erp_agent", result, budget=ERP_AGENT_RESULT_BUDGET)
    # 空结果不加 envelope，避免给前端发"只有提示没数据"的奇怪回复
    if not truncated.strip():
        return truncated
    return (
        "[ALREADY_DISPLAYED]\n"
        "⚠ 以下 ERP 查询结果已作为独立卡片直接展示给用户，用户已经看到了原始数据。\n"
        "回复规则：\n"
        "- 用1-2句话直接给出结论（如「今天3625单，2.16万」），让用户不看卡片也能秒懂\n"
        "- 禁止重复卡片中的完整数据列表，只提炼关键数字\n"
        "- 禁止改写日期/星期/数字\n"
        "- 用户没要求对比/趋势时不要主动分析\n\n"
        "─── ERP 结果开始 ───\n"
        f"{truncated}\n"
        "─── ERP 结果结束 ───"
    )


# ============================================================
# 内部函数
# ============================================================

def _resolve_budget(tool_name: str) -> int:
    """根据工具名自动选择预算"""
    if tool_name == "erp_agent":
        return ERP_AGENT_RESULT_BUDGET
    # ERP 相关工具在 ERP Agent 内部用 wrap_for_erp_agent 显式调用，
    # 走到这里说明是主 Agent 直接调用
    return MAIN_AGENT_BUDGET


def _smart_truncate(tool_name: str, result: str, budget: int) -> str:
    """智能截断：按工具类型选择最佳截断策略

    所有策略共同点：截断标注追加在末尾，不影响开头的正则匹配。
    """
    original_len = len(result)

    # ERP 查询结果：保留首行 + 汇总行 + 前N行数据
    if tool_name.startswith(("erp_", "local_")):
        truncated = _truncate_erp(result, budget)
    # 代码执行：保留错误 + 最后几行输出
    elif tool_name == "code_execute":
        truncated = _truncate_code(result, budget)
    # 搜索类：保留前几条
    elif tool_name in ("web_search", "search_knowledge", "erp_api_search", "social_crawler"):
        truncated = _truncate_search(result, budget)
    # 默认：保留前 N 字符
    else:
        truncated = result[:budget]

    # 暂存完整结果供 code_execute 读取
    persist_key = persist_and_get_key(tool_name, result)

    # 追加截断信号（末尾），附带暂存 key
    signal = (
        f"\n⚠ 输出已截断（原始 {original_len} 字符，显示前 {len(truncated)} 字符）。"
        f'完整数据已暂存(key={persist_key})，'
        f'可用 code_execute 调用 get_persisted_result("{persist_key}") 获取。'
    )
    truncated += signal

    logger.debug(
        f"ToolResultEnvelope truncated | tool={tool_name} | "
        f"original={original_len} | truncated={len(truncated)}"
    )
    return truncated


def _truncate_erp(result: str, budget: int) -> str:
    """ERP 结果：首行 + 汇总行 + 尽量多的数据行"""
    lines = result.split("\n")
    if not lines:
        return result[:budget]

    first_line = lines[0]
    # 收集汇总行
    summary_lines = [
        line for line in lines[1:]
        if _SUMMARY_LINE_RE.search(line.strip())
    ]
    # 数据行（非空、非分隔线、非汇总行）
    data_lines = [
        line for line in lines[1:]
        if line.strip()
        and not line.startswith("---")
        and not _SUMMARY_LINE_RE.search(line.strip())
    ]

    # 组装：首行 + 尽量多数据行 + 汇总行
    parts = [first_line]
    used = len(first_line)
    # 预留汇总行空间（不超过预算的一半，防止 reserve > budget 导致无数据行）
    summary_text = "\n".join(summary_lines)
    reserve = len(summary_text) + 100 if summary_lines else 100
    reserve = min(reserve, budget // 2)

    for line in data_lines:
        if used + len(line) + 1 > budget - reserve:
            break
        parts.append(line)
        used += len(line) + 1

    if summary_lines:
        parts.extend(summary_lines)

    return "\n".join(parts)


def _truncate_code(result: str, budget: int) -> str:
    """代码执行结果：错误优先完整保留，否则保留最后几行"""
    # 错误优先
    if result.startswith("❌") or "Error" in result[:200] or "Traceback" in result[:200]:
        return result[:budget]

    lines = result.split("\n")
    if len(lines) <= 15:
        return result  # 行数少直接保留全文（预算检查已在 wrap() 中做过）

    # 保留最后 15 行
    tail = lines[-15:]
    tail_text = "\n".join(tail)
    if len(tail_text) <= budget:
        prefix = f"...(前 {len(lines) - 15} 行已省略)\n"
        return prefix + tail_text
    return tail_text[:budget]


def _truncate_search(result: str, budget: int) -> str:
    """搜索结果：保留前几条"""
    lines = result.split("\n")

    # 按条目分割
    items: List[str] = []
    current: list[str] = []
    for line in lines:
        if re.match(r"^[-\d•]", line.strip()) and current:
            items.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        items.append("\n".join(current))

    # 保留前 N 条，不超预算
    kept: List[str] = []
    used = 0
    for item in items:
        if used + len(item) + 1 > budget:
            break
        kept.append(item)
        used += len(item) + 1

    if not kept:
        return result[:budget]
    return "\n".join(kept)
