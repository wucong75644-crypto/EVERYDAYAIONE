"""
工具结果信封 — 阈值分流 + staging 落盘 + 摘要生成

所有工具返回结果经过此层包装后再放入 messages，确保：
1. 小结果直接放入 LLM context（不截断）
2. 大结果落盘 staging 文件，LLM context 只放摘要 + 文件路径
3. 沙盒通过 read_file("staging/xxx.txt") 读取完整数据

对标 OpenAI Code Interpreter / Claude Code 的架构模式：
- 大数据不进 context，走文件交换
- 沙盒只做纯计算，数据通过文件传递

设计文档：docs/document/TECH_工具结果分流架构.md
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


# ============================================================
# staging 目录路径（请求级，ContextVar 并发隔离）
#
# 由最外层入口设置（chat_handler / scheduled_task_agent），
# 子调用（ERPAgent）自动继承或兜底 set。
# 只在最外层 clear，子调用不 clear。
# ============================================================

_staging_dir_ctx: ContextVar[Optional[str]] = ContextVar(
    "staging_dir", default=None,
)


def set_staging_dir(path: str) -> None:
    """设置当前请求的 staging 目录路径"""
    _staging_dir_ctx.set(path)


def get_staging_dir() -> Optional[str]:
    """获取当前请求的 staging 目录路径"""
    return _staging_dir_ctx.get()


def clear_staging_dir() -> None:
    """清理 staging 目录路径（仅最外层 finally 调用）"""
    _staging_dir_ctx.set(None)


# ============================================================
# 大结果暂存（请求级内存，保留兼容）
#
# persist_and_get_key / get_persisted / clear_persisted
# 不再从截断信号中引用，但保留函数供其他模块使用。
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
    """暂存完整结果到内存，返回 key（保留兼容，不再从截断信号引用）"""
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

# 对齐 Claude Code 的 persisted-output 标签（防重入 + 截断检测）
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
# 兼容旧标记（erp_agent.py 等用于 is_truncated 检测）
STAGED_MARKER = PERSISTED_OUTPUT_TAG

# Preview 大小（对齐 Claude Code 的 PREVIEW_SIZE_BYTES = 2000）
_PREVIEW_SIZE = 2000


# ============================================================
# 核心函数
# ============================================================

def wrap(
    tool_name: str,
    result: str,
    budget: Optional[int] = None,
) -> str:
    """包装工具结果：小结果原样返回，大结果落盘 staging + 生成摘要

    Args:
        tool_name: 工具名称
        result: 工具执行的完整结果文本
        budget: 字符预算（None 则按 tool_name 自动选择）

    Returns:
        处理后的结果（原样 或 摘要+staging路径）
    """
    if not result:
        return result

    # 不截断的工具
    if tool_name in _NO_TRUNCATE:
        return result

    # 已经分流过的结果不再处理（防重入）
    if PERSISTED_OUTPUT_TAG in result:
        return result

    # 确定预算
    if budget is None:
        budget = _resolve_budget(tool_name)

    # 短结果不处理
    if len(result) <= budget:
        return result

    # 超阈值 → staging 落盘 + 摘要
    return _stage_and_summarize(tool_name, result, budget)


def wrap_for_erp_agent(tool_name: str, result: str, tight: bool = False) -> str:
    """ERP Agent 内部工具结果包装（正常 3000 / 紧张 1800）"""
    budget_val = 1800 if tight else ERP_AGENT_BUDGET
    return wrap(tool_name, result, budget=budget_val)


def wrap_erp_agent_result(result: str) -> str:
    """erp_agent 工具返回给主 Agent 的结论包装（预算 4000）。

    先走 wrap() 分流，再套"禁止改写"信封。
    信封包在摘要外面是正确的——摘要里的数字/日期同样需要保护。

    设计文档：docs/document/TECH_ERP时间准确性架构.md §14.7
    """
    truncated = wrap("erp_agent", result, budget=ERP_AGENT_RESULT_BUDGET)
    # 空结果不加 envelope，避免给前端发"只有提示没数据"的奇怪回复
    if not truncated.strip():
        return truncated
    return (
        "⚠ 以下是 ERP 数据查询的最终结果，已包含**正确的中文星期/日期/数字**。\n"
        "**禁止改写**其中的日期或星期（如「2026-04-10 周五」必须逐字保留）。\n"
        "你只能在前后加简短的中文序言或总结，"
        "结构化时间块（[当前期]/[基线期]/[查询窗口]/[统计区间] 等）和数据本身必须原文输出。\n\n"
        "─── ERP 结果开始 ───\n"
        f"{truncated}\n"
        "─── ERP 结果结束 ───"
    )


# ============================================================
# 内部函数
# ============================================================

def _resolve_budget(tool_name: str, tight: bool = False) -> int:
    """根据工具名自动选择预算。

    v6: tight=True 时收紧预算（上下文紧张，对标 LangChain 三档策略）。
    """
    if tool_name == "erp_agent":
        return 2400 if tight else ERP_AGENT_RESULT_BUDGET  # 4000→2400
    return 1200 if tight else MAIN_AGENT_BUDGET  # 2000→1200


def _stage_and_summarize(tool_name: str, result: str, budget: int) -> str:
    """超阈值分流：落盘 staging + 生成摘要 + 路径提示"""
    staging_dir = get_staging_dir()
    if staging_dir is None:
        raise RuntimeError(
            f"staging_dir 未设置，无法分流工具结果（tool={tool_name}）。"
            "请确保在工具循环入口调用了 set_staging_dir()。"
        )

    # 落盘 staging 文件
    rel_path = _persist_to_staging(staging_dir, tool_name, result)
    filename = rel_path.split("/")[-1]
    # 对齐 Claude Code 的 <persisted-output> 标签格式
    preview = _generate_preview(result, _PREVIEW_SIZE)
    return (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"Output too large ({len(result)} chars). "
        f'Full output saved to: STAGING_DIR + "/{filename}"\n\n'
        f"Preview (first {_PREVIEW_SIZE} chars):\n"
        f"{preview}\n"
        f"{PERSISTED_OUTPUT_CLOSING_TAG}"
    )


def _persist_to_staging(staging_dir: str, tool_name: str, result: str) -> str:
    """将完整结果写入 staging 文件，返回相对路径（供 read_file 使用）

    相对路径格式：staging/{conv_id}/{filename}
    FileExecutor.resolve_safe_path 以用户 workspace_dir 为 root 解析。
    """
    Path(staging_dir).mkdir(parents=True, exist_ok=True)

    digest = hashlib.md5(result.encode()).hexdigest()[:8]
    safe_tool = tool_name.replace("/", "_").replace("..", "_")
    filename = f"tool_result_{safe_tool}_{digest}.txt"
    file_path = (Path(staging_dir) / filename).resolve()

    file_path.write_text(result, encoding="utf-8")

    # staging_dir 格式：{workspace_dir}/staging/{conv_id}
    # 取最后两段（staging/{conv_id}）+ filename 构成相对路径
    parts = Path(staging_dir).parts
    # 倒数第二个是 "staging"，倒数第一个是 conv_id
    rel_path = f"staging/{parts[-1]}/{filename}"

    logger.info(
        f"ToolResultEnvelope staged | tool={tool_name} | "
        f"chars={len(result)} | path={rel_path}"
    )
    return rel_path


def _generate_preview(content: str, max_chars: int) -> str:
    """生成预览文本，在换行符处截断（对齐 Claude Code 的 generatePreview）"""
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    last_newline = truncated.rfind("\n")
    # 如果换行符在合理位置（后半段），在换行符处截断
    cut = last_newline if last_newline > max_chars * 0.5 else max_chars
    return content[:cut] + "\n..."
