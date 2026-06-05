"""用户中断与恢复机制 — 常量与工具函数。

详见 docs/document/TECH_用户中断与恢复机制.md

业界证据：
- LiteLLM Message Sanitization: orphan tool_call 自动补对
- Cline `responses.ts`: taskResumption 模板
- Claude Code: `[Request interrupted by user for tool use]`
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from loguru import logger


INTERRUPTED_TOOL_RESULT = (
    "[系统: 用户在工具 '{tool_name}' 执行完成前中断了对话。"
    "该工具结果未知，请视为未成功。]"
)


TASK_RESUMPTION_TEMPLATE = (
    "[任务恢复] 此任务在 {ago_text} 被用户中断。可能未完成，请重新评估任务上下文。\n\n"
    "注意：如果之前调用的工具没有收到结果，请假设该工具未成功执行；"
    "根据当前需要判断是否重试。"
)


def find_orphan_tool_calls(
    messages: List[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    """扫描 messages，返回所有未配对的 (tool_call_id, tool_name)。

    用于落锚阶段：取消瞬间扫描内存 messages，给所有未配对的 tool_call 补对。
    """
    tool_call_ids: Dict[str, str] = {}
    seen_results: Set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tc_id = tc.get("id")
                fn = tc.get("function") or {}
                if tc_id and fn.get("name"):
                    tool_call_ids[tc_id] = fn["name"]
        elif msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                seen_results.add(tc_id)

    return [
        (tid, tname)
        for tid, tname in tool_call_ids.items()
        if tid not in seen_results
    ]


def _append_partial_block(
    content_blocks: List[Dict[str, Any]],
    block_type: str,
    partial_text: str,
) -> None:
    """把 partial 文本作为 dedup-aware block 追加到 content_blocks 末尾。

    参考 InputArea.tsx 的 committed_len 算未提交 delta，避免重复。
    """
    if not partial_text:
        return
    committed_len = sum(
        len(blk.get("text", "")) for blk in content_blocks
        if blk.get("type") == block_type
    )
    delta = partial_text[committed_len:]
    if delta.strip():
        content_blocks.append({"type": block_type, "text": delta})


def _mark_running_tools_cancelled(
    content_blocks: List[Dict[str, Any]],
    cancelled_at: str,
) -> int:
    """把所有 status='running' 的 tool_step 改为 cancelled。返回改写的数量。"""
    count = 0
    for blk in content_blocks:
        if blk.get("type") == "tool_step" and blk.get("status") == "running":
            blk["status"] = "cancelled"
            blk["cancelled_at"] = cancelled_at
            count += 1
    return count


async def persist_interrupt_anchor(
    db: Any,
    task_id: str,
    message_id: str,
    org_id: str | None,
    messages: List[Dict[str, Any]],
    content_blocks: List[Dict[str, Any]],
    partial_text: str = "",
    partial_thinking: str = "",
) -> None:
    """落锚原子操作：把中断瞬间的快照原子写入 DB。

    步骤（顺序与 §四.2 / §十七.3 一致）：
    1. partial_thinking / partial_text 追加到 content_blocks（dedup-aware）
    2. content_blocks 里所有 running tool_step → cancelled + cancelled_at
    3. messages 数组里 orphan tool_call 追加 synthetic tool_result
    4. content_blocks 末尾追加 interrupt_marker
    5. 写 messages 表（content + status='interrupted'）— 主表
    6. 写 tasks 表（accumulated_blocks + status='cancelled'）— 副表

    设计原则：
    - 先主后副：messages 表写成功才算落锚成功；tasks 表失败由 reconcile 自愈
    - 不上 DB transaction：Supabase 跨表事务复杂度高，靠"主表为准 + 重建"机制
    - fire-and-forget 风格：调用方 await 等待完成，但 tasks 副表失败不抛错

    详见 docs/document/TECH_用户中断与恢复机制.md §四.2 / §十七.3
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    _append_partial_block(content_blocks, "thinking", partial_thinking)
    _append_partial_block(content_blocks, "text", partial_text)

    cancelled_tool_count = _mark_running_tools_cancelled(content_blocks, now_iso)

    orphans = find_orphan_tool_calls(messages)
    for tc_id, tc_name in orphans:
        messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": INTERRUPTED_TOOL_RESULT.format(tool_name=tc_name),
        })

    content_blocks.append({
        "type": "interrupt_marker",
        "interrupted_at": now_iso,
        "reason": "user_cancel",
    })

    try:
        db.table("messages").update({
            "content": content_blocks,
            "status": "interrupted",
        }).eq("id", message_id).execute()
    except Exception as e:
        logger.error(
            f"Persist interrupt anchor (messages) failed | "
            f"msg={message_id} | task={task_id} | org={org_id} | error={e}"
        )
        return

    try:
        db.table("tasks").update({
            "accumulated_blocks": content_blocks,
            "status": "cancelled",
        }).eq("external_task_id", task_id).execute()
    except Exception as e:
        logger.warning(
            f"Persist interrupt anchor (tasks) failed | "
            f"task={task_id} | error={e} (reconcile will self-heal)"
        )

    logger.info(
        f"Interrupt anchor persisted | task={task_id} | org={org_id} | "
        f"orphans={len(orphans)} | cancelled_tools={cancelled_tool_count} | "
        f"blocks={len(content_blocks)}"
    )


async def reconcile_interrupted_messages(
    db: Any,
    lookback_hours: int = 1,
    limit: int = 100,
) -> Dict[str, int]:
    """Worker 启动时调用：自愈双轨持久化不一致问题。

    扫描 messages.status='interrupted' 的近期消息，以 messages 表为
    Single Source of Truth 重建 tasks.accumulated_blocks。

    限制：
    - 默认仅扫描最近 1 小时（避免历史数据全表扫）
    - 默认上限 100 条（启动延迟可控）

    详见 docs/document/TECH_用户中断与恢复机制.md §17.4
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()

    try:
        interrupted = (
            db.table("messages")
            .select("id, content, conversation_id")
            .eq("status", "interrupted")
            .gte("created_at", cutoff)
            .limit(limit)
            .execute()
        )
    except Exception as e:
        logger.warning(f"Reconcile scan failed | error={e}")
        return {"scanned": 0, "reconciled": 0}

    rows = interrupted.data or []
    scanned = len(rows)
    reconciled = 0

    for msg in rows:
        msg_id = msg["id"]
        msg_content = msg.get("content")

        try:
            task_q = (
                db.table("tasks")
                .select("external_task_id, accumulated_blocks, status")
                .eq("assistant_message_id", msg_id)
                .execute()
            )
        except Exception:
            continue

        if not task_q.data:
            continue

        task = task_q.data[0]
        if (task.get("accumulated_blocks") == msg_content
                and task.get("status") == "cancelled"):
            continue

        try:
            db.table("tasks").update({
                "accumulated_blocks": msg_content,
                "status": "cancelled",
            }).eq("external_task_id", task["external_task_id"]).execute()
            reconciled += 1
            logger.info(
                f"Reconciled task | ext_task_id={task['external_task_id']} | "
                f"msg_id={msg_id}"
            )
        except Exception as e:
            logger.warning(
                f"Reconcile update failed | "
                f"task={task['external_task_id']} | error={e}"
            )

    if scanned > 0:
        logger.info(
            f"Reconcile complete | scanned={scanned} | reconciled={reconciled}"
        )

    return {"scanned": scanned, "reconciled": reconciled}


def fix_orphan_tool_calls(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """扫描历史 messages，自动补对未配对的 tool_call。

    顺序遍历，遇到 assistant.tool_calls 后必须紧跟对应 role=tool。
    若下一条不是配对 tool，立即插入 synthetic tool_result。

    设计原则（参考 LiteLLM）：
    - 不删除孤儿 tool_call（保留语义"我尝试过 X"）
    - 用 synthetic tool_result 补对，让 LLM 看到"未成功"明确信号

    用于 history_loader 兜底阶段：防御性补对，处理存量脏数据 / 边界情况。
    """
    fixed: List[Dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        fixed.append(msg)

        if msg.get("role") != "assistant":
            i += 1
            continue

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            i += 1
            continue

        expected_ids: List[str] = []
        tool_names: Dict[str, str] = {}
        for tc in tool_calls:
            tc_id = tc.get("id")
            fn = tc.get("function") or {}
            name = fn.get("name")
            if tc_id and name:
                expected_ids.append(tc_id)
                tool_names[tc_id] = name

        j = i + 1
        seen: Set[str] = set()
        while j < n and messages[j].get("role") == "tool":
            tc_id = messages[j].get("tool_call_id")
            if tc_id in tool_names:
                fixed.append(messages[j])
                seen.add(tc_id)
            else:
                fixed.append(messages[j])
            j += 1

        for tc_id in expected_ids:
            if tc_id not in seen:
                fixed.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": INTERRUPTED_TOOL_RESULT.format(
                        tool_name=tool_names[tc_id]
                    ),
                })

        i = j

    return fixed
