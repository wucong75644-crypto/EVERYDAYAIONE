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

# code_execute 预算（对标 Claude Code BASH_MAX_OUTPUT_DEFAULT = 30000）
# ≤30K: 直接回传（不截断）  >30K: 落盘 staging + 2K 预览
CODE_EXECUTE_BUDGET = 30000

# 不截断的工具（返回本身有界或自有防线）
# - generate_image/video: 返回本身就短
# - file_*: file_read 自有三级防线（L1 字节 256KB / L2 行数 2000 /
#   L3 token 25000）兜底，对齐 Claude Code maxResultSizeChars=Infinity
_NO_TRUNCATE = {
    "generate_image", "generate_video",
    "file_read", "file_write", "file_edit",
}

# 对齐 Claude Code 的 persisted-output 标签（防重入 + 截断检测）
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
# 兼容旧标记（erp_agent.py / tool_loop_executor.py 用于 is_truncated 检测）
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
    """工具结果包装（按工具名分派预算）

    预算分派：
    - code_execute: 30K/20K（对标 Claude Bash 30K，sandbox 50K 是子进程安全网）
    - erp_agent:    4K/2.4K（结论文本，返回给主 Agent）
    - 其他:         3K/1.8K（ERP 内部工具默认预算）
    """
    if tool_name == "code_execute":
        budget_val = 20000 if tight else CODE_EXECUTE_BUDGET
    elif tool_name == "erp_agent":
        budget_val = 2400 if tight else ERP_AGENT_RESULT_BUDGET
    else:
        budget_val = 1800 if tight else ERP_AGENT_BUDGET
    return wrap(tool_name, result, budget=budget_val)



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

    # code_execute 用结构化预览（首尾行 + 统计），其他工具用纯文本预览
    if tool_name == "code_execute":
        preview = _generate_structured_preview(result)
    else:
        preview = (
            f"Preview (first {_PREVIEW_SIZE} chars):\n"
            + _generate_preview(result, _PREVIEW_SIZE)
        )

    return (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"Output too large ({len(result):,} chars). "
        f'Full output saved to: STAGING_DIR + "/{filename}"\n\n'
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


_HEAD_LINES = 8   # 结构化预览：展示前 N 行
_TAIL_LINES = 3   # 结构化预览：展示后 N 行


def _generate_structured_preview(content: str) -> str:
    """生成结构化预览（code_execute 专用）

    展示数据全貌：总量统计 + 首 N 行 + 尾 N 行，
    让 AI 一看就知道数据结构和内容，无需再次读取。
    """
    lines = content.split("\n")
    total_lines = len(lines)
    total_chars = len(content)

    parts = [f"结果概览（{total_chars:,} 字符 / {total_lines:,} 行）:"]

    if total_lines <= _HEAD_LINES + _TAIL_LINES + 2:
        # 行数不多，直接展示全部（不超过 _PREVIEW_SIZE 字符）
        preview_text = _generate_preview(content, _PREVIEW_SIZE)
        parts.append(preview_text)
    else:
        # 首 N 行
        head = lines[:_HEAD_LINES]
        parts.append(f"\n前 {_HEAD_LINES} 行:")
        for line in head:
            # 单行过长时截断到 200 字符
            display = line[:200] + "..." if len(line) > 200 else line
            parts.append(f"  {display}")

        parts.append(f"\n... 省略 {total_lines - _HEAD_LINES - _TAIL_LINES} 行 ...\n")

        # 尾 N 行
        tail = lines[-_TAIL_LINES:]
        parts.append(f"后 {_TAIL_LINES} 行:")
        for line in tail:
            display = line[:200] + "..." if len(line) > 200 else line
            parts.append(f"  {display}")

    return "\n".join(parts)


def _generate_preview(content: str, max_chars: int) -> str:
    """生成预览文本，在换行符处截断（对齐 Claude Code 的 generatePreview）"""
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    last_newline = truncated.rfind("\n")
    # 如果换行符在合理位置（后半段），在换行符处截断
    cut = last_newline if last_newline > max_chars * 0.5 else max_chars
    return content[:cut] + "\n..."
