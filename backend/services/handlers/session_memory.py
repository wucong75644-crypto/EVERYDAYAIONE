"""
会话级增量记忆提取

对标：Claude Code Session Memory Compact
- 后台 fire-and-forget 运行
- 固定章节结构，增量 patch（就地修改 dict，不重新 set ContextVar）
- 存储在 ContextVar（请求级）

章节结构（4 章节，对标 Claude 9 章节精简版）：
1. 话题线索：用户讨论了什么
2. 关键实体：数字/编码/ID/金额/日期
3. 已查结论：ERP 查询确认的事实
4. 待处理：未完成的任务

设计文档：docs/document/TECH_上下文工程重构.md §八
"""

import asyncio
import json
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from loguru import logger


# 请求级存储（每次对话请求独立）
_session_memory: ContextVar[Optional[Dict[str, List[str]]]] = ContextVar(
    "_session_memory", default=None
)
# 防止多个 fire-and-forget task 并发修改同一个 dict
_extract_lock: ContextVar[Optional[asyncio.Lock]] = ContextVar(
    "_extract_lock", default=None
)

_EMPTY_MEMORY = {
    "topics": [],
    "entities": [],
    "conclusions": [],
    "pending": [],
}

# 注意：这是普通字符串，不是 f-string。其中的 { } 是 JSON 字面量，不可改为 f-string。
_EXTRACTION_PROMPT = """从以下对话片段中提取信息，按 JSON 格式输出：

{
  "topics": ["新增的话题（如有）"],
  "entities": ["新出现的数字/编码/ID/金额/日期，原样保留"],
  "conclusions": ["新确认的事实/查询结论"],
  "pending": ["新增的未完成任务"]
}

只输出新增内容，不重复已有的。某项无新增输出空数组。
只输出 JSON，不加任何前缀后缀。"""


def init_session_memory() -> Dict[str, List[str]]:
    """在请求入口（chat_handler.start）调用，初始化 ContextVar。

    必须在主协程中调用。asyncio.create_task 继承快照后，
    子 task 修改 dict 内容（就地修改引用对象）对主协程可见。
    注意：子 task 中禁止调用 _session_memory.set()，只能就地修改。
    """
    mem = {k: list(v) for k, v in _EMPTY_MEMORY.items()}
    _session_memory.set(mem)
    _extract_lock.set(asyncio.Lock())
    return mem


def get_session_memory() -> Dict[str, List[str]]:
    """获取当前会话的增量记忆（必须先调过 init_session_memory）"""
    mem = _session_memory.get()
    if mem is None:
        return init_session_memory()
    return mem


def format_session_memory() -> Optional[str]:
    """将增量记忆格式化为可注入的摘要文本

    如果所有章节都为空，返回 None。
    """
    mem = get_session_memory()
    if not any(mem.values()):
        return None

    parts = []
    if mem["topics"]:
        parts.append("### 话题线索\n" + "\n".join(f"- {t}" for t in mem["topics"]))
    if mem["entities"]:
        parts.append("### 关键实体\n" + "\n".join(f"- {e}" for e in mem["entities"]))
    if mem["conclusions"]:
        parts.append("### 已确认结论\n" + "\n".join(f"- {c}" for c in mem["conclusions"]))
    if mem["pending"]:
        parts.append("### 待处理事项\n" + "\n".join(f"- {p}" for p in mem["pending"]))

    return "\n\n".join(parts)


async def extract_incremental(
    new_messages: List[Dict[str, Any]],
) -> None:
    """从新消息中增量提取信息到 session_memory（fire-and-forget）

    使用 qwen-turbo 做轻量提取（~50ms，成本极低）。
    失败静默跳过（不影响主流程）。
    使用 asyncio.Lock 防止多个 fire-and-forget task 并发修改产生重复条目。
    """
    lock = _extract_lock.get()
    if lock is None:
        return  # init_session_memory 未调用，跳过

    async with lock:
        await _extract_incremental_inner(new_messages)


async def _extract_incremental_inner(
    new_messages: List[Dict[str, Any]],
) -> None:
    """增量提取内部实现（lock 保护下调用）"""
    try:
        from core.config import settings
        from services.context_summarizer import _call_summary_model

        # 构建提取输入
        text_parts = []
        for msg in new_messages:
            role = {"user": "用户", "assistant": "AI", "tool": "工具结果"}.get(
                msg.get("role", ""), msg.get("role", "")
            )
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            if content:
                text_parts.append(f"{role}: {content[:300]}")

        if not text_parts:
            return

        input_text = "\n".join(text_parts)

        # 将已有实体传给 LLM，避免重复提取
        existing_mem = get_session_memory()
        if any(existing_mem.values()):
            existing_hint = (
                "\n\n已有记录（不要重复）：\n"
                + "\n".join(
                    f"- {k}: {', '.join(v[:5])}"
                    for k, v in existing_mem.items() if v
                )
            )
            input_text += existing_hint

        result = await _call_summary_model(
            settings.context_summary_model,
            input_text,
            system_prompt_override=_EXTRACTION_PROMPT,
        )
        if not result:
            return

        # 解析 JSON
        extracted = json.loads(result)
        mem = get_session_memory()

        # 增量合并（去重 + FIFO 淘汰，就地修改 dict）
        # 每个章节最多 20 条，满了踢最早的（FIFO），保证新信息始终能进来
        MAX_ITEMS_PER_KEY = 20
        for key in ("topics", "entities", "conclusions", "pending"):
            new_items = extracted.get(key, [])
            if isinstance(new_items, list):
                existing = set(mem[key])
                for item in new_items:
                    if isinstance(item, str) and item.strip() and item not in existing:
                        if len(mem[key]) >= MAX_ITEMS_PER_KEY:
                            mem[key].pop(0)  # FIFO：踢最早的
                        mem[key].append(item.strip())

        logger.debug(
            f"Session memory updated | "
            f"topics={len(mem['topics'])} | entities={len(mem['entities'])} | "
            f"conclusions={len(mem['conclusions'])} | pending={len(mem['pending'])}"
        )

    except json.JSONDecodeError:
        logger.debug("Session memory extraction: invalid JSON, skipping")
    except Exception as e:
        logger.debug(f"Session memory extraction failed, skipping | error={e}")
