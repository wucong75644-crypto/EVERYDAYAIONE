"""用户中断与恢复机制 — 集成测试

覆盖 3 个端到端关键场景：
1. 落锚 + history_loader 链路（cancel → persist → history rebuild → 协议合法）
2. cancel API → WS 闸门 → 推送 drop
3. 取消后历史重建的 OpenAI 协议合法性（assistant.tool_calls 必须配对 tool_result）

详见 docs/document/TECH_用户中断与恢复机制.md §八.5 / §十七.6
"""

import json
import time
from unittest.mock import MagicMock

import pytest

from services.cancel_gate import CancelManager
from services import cancel_metrics
from services.handlers.chat_context.content_extractors import (
    extract_oai_messages_from_content,
    extract_interrupt_marker,
)
from services.handlers.interrupt_anchor import (
    INTERRUPTED_TOOL_RESULT,
    TASK_RESUMPTION_TEMPLATE,
    find_orphan_tool_calls,
    fix_orphan_tool_calls,
    persist_interrupt_anchor,
)


def _make_db():
    """构造 mock db，记录所有 .update(payload) 的调用"""
    db = MagicMock()
    db.update_calls = []  # list[(table_name, payload)]
    table_mocks = {}

    def table_dispatch(name):
        if name not in table_mocks:
            chain = MagicMock()
            original_update = chain.update

            def make_update_capture(tbl):
                def update_capture(payload):
                    db.update_calls.append((tbl, payload))
                    return original_update(payload)
                return update_capture

            chain.update = make_update_capture(name)
            table_mocks[name] = chain
        return table_mocks[name]

    db.table = table_dispatch
    return db


def _assistant_with_tools(text, tool_calls):
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": tc[0],
                "type": "function",
                "function": {"name": tc[1], "arguments": tc[2] if len(tc) > 2 else ""},
            }
            for tc in tool_calls
        ],
    }


def _assert_oai_protocol_valid(messages):
    """验证 OpenAI tool_use 协议合法性：每个 assistant.tool_calls[id] 必须在后续 role=tool 中有配对。"""
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if msg.get("role") != "assistant":
            i += 1
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            i += 1
            continue
        expected_ids = {tc["id"] for tc in tool_calls}
        j = i + 1
        found_ids = set()
        while j < n and messages[j].get("role") == "tool":
            tc_id = messages[j].get("tool_call_id")
            if tc_id in expected_ids:
                found_ids.add(tc_id)
            j += 1
        missing = expected_ids - found_ids
        assert not missing, f"协议违反：assistant.tool_calls {missing} 未配对 role=tool"
        i = j


class TestScenario1_AnchorAndHistoryLoaderRoundTrip:
    """场景 1：落锚 + 历史重建端到端"""

    @pytest.mark.asyncio
    async def test_full_roundtrip(self):
        # 1. 模拟一轮 LLM stream 被中断时的内存状态
        messages = [
            {"role": "user", "content": "查最近订单并导出"},
            _assistant_with_tools("我先查最近订单", [("call_A", "erp_query"), ("call_B", "erp_export")]),
        ]
        content_blocks = [
            {"type": "thinking", "text": "需要先查数据"},
            {"type": "text", "text": "我先查最近订单"},
            {
                "type": "tool_step", "tool_name": "erp_query",
                "tool_call_id": "call_A", "status": "completed",
                "input": "{}", "output": "32 条订单",
            },
            {
                "type": "tool_step", "tool_name": "erp_export",
                "tool_call_id": "call_B", "status": "running",
                "input": '{"fmt":"xlsx"}',
            },
        ]

        # 2. 触发落锚
        db = _make_db()
        await persist_interrupt_anchor(
            db=db, task_id="task_X", message_id="msg_X", org_id="org_x",
            messages=messages, content_blocks=content_blocks,
        )

        # 3. 验证 content_blocks 状态变化
        assert content_blocks[3]["status"] == "cancelled"
        assert "cancelled_at" in content_blocks[3]
        # 最后一个 block 必须是 interrupt_marker
        assert content_blocks[-1]["type"] == "interrupt_marker"
        assert content_blocks[-1]["reason"] == "user_cancel"

        # 4. 验证 messages 数组里 orphan tool_call 已补对
        # 协议要求每个 assistant.tool_calls 都有配对 tool_result
        _assert_oai_protocol_valid(messages)
        # call_B 的 tool_result 是 synthetic（call_A 没出现因为我们没在 messages 里加结果）
        # 这里只有 call_A 和 call_B 都是 orphan（messages 里没有 tool result），所以都被补了
        orphan_补对 = [m for m in messages if m.get("role") == "tool"]
        assert len(orphan_补对) == 2
        tc_ids = {m["tool_call_id"] for m in orphan_补对}
        assert tc_ids == {"call_A", "call_B"}

        # 5. 验证 DB 写入顺序：先 messages 后 tasks
        update_names = [name for name, _ in db.update_calls]
        assert update_names == ["messages", "tasks"]
        # 检查 status 值
        msg_update = next(p for n, p in db.update_calls if n == "messages")
        task_update = next(p for n, p in db.update_calls if n == "tasks")
        assert msg_update["status"] == "interrupted"
        assert task_update["status"] == "cancelled"

        # 6. 模拟下一轮历史重建：把落锚后的 content_blocks 喂给 content_extractors
        oai_msgs = extract_oai_messages_from_content(
            content_blocks, role="assistant",
        )
        # cancelled tool_step 生成配对 assistant.tool_calls + role=tool
        _assert_oai_protocol_valid(oai_msgs)
        # 配对的 tool_result 内容应该是 INTERRUPTED_TOOL_RESULT
        cancelled_tool_msgs = [
            m for m in oai_msgs
            if m.get("role") == "tool"
            and INTERRUPTED_TOOL_RESULT.split("'{")[0] in m.get("content", "")
        ]
        assert len(cancelled_tool_msgs) == 1
        assert "erp_export" in cancelled_tool_msgs[0]["content"]

        # 7. interrupt_marker 应该被识别
        marker = extract_interrupt_marker(content_blocks)
        assert marker is not None
        assert marker["reason"] == "user_cancel"


class TestScenario2_CancelApiToGateDrop:
    """场景 2：cancel API → 闸门 → WS 推送 drop"""

    @pytest.mark.asyncio
    async def test_cancel_marks_gate_and_drops_subsequent(self):
        cm = CancelManager()
        cm.register_listener("task_Y")

        # 模拟 task.py 触发 cancel 时的完整链路
        cancel_metrics.mark_cancel_start("task_Y")
        cm.cancel("task_Y", org_id="org_y")

        # 等异步 mark_gate 完成
        import asyncio
        await asyncio.sleep(0.05)

        # 验证三件事都生效
        assert cm.is_signalled("task_Y") is True  # Event set
        assert cm.is_in_gate("task_Y", "org_y") is True  # Gate marked
        # 跨 org 隔离：org_z 不应被中招
        assert cm.is_in_gate("task_Y", "org_z") is False

        # 验证 latency 可计算（task_Y 在已 start 中）
        assert "task_Y" in cancel_metrics._cancel_started_at
        cancel_metrics.record_cancel_latency("task_Y", org_id="org_y")
        # latency record 后清理
        assert "task_Y" not in cancel_metrics._cancel_started_at


class TestScenario3_ProtocolValidityAfterCancel:
    """场景 3：取消后重发的 OpenAI 协议合法性

    防止 Claude Code #3003 类型的会话腐化 bug。
    """

    def test_history_loader_fix_prevents_400(self):
        """history_loader 兜底确保即使 DB 有遗留 orphan 也合法"""
        # 模拟从 DB 加载历史，含 orphan（旧版残留 / DB 写入时崩溃）
        context = [
            {"role": "user", "content": "查订单"},
            _assistant_with_tools(None, [("call_A", "erp_query")]),
            # 缺失：role=tool tool_call_id=call_A
            {"role": "user", "content": "继续"},  # 用户继续发消息
        ]

        # history_loader 自动补对兜底
        fixed = fix_orphan_tool_calls(context)
        _assert_oai_protocol_valid(fixed)

        # 验证补对的位置和内容
        tool_msgs = [m for m in fixed if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_A"
        assert "erp_query" in tool_msgs[0]["content"]

    def test_multi_tool_with_partial_completion(self):
        """多工具并发，部分完成 + 部分 cancelled，协议仍合法"""
        # 模拟落锚后的 content_blocks
        content_blocks = [
            {"type": "text", "text": "我先查这三项"},
            {
                "type": "tool_step", "tool_name": "erp_query",
                "tool_call_id": "call_A", "status": "completed",
                "output": "32 条订单",
            },
            {
                "type": "tool_step", "tool_name": "erp_warehouse",
                "tool_call_id": "call_B", "status": "cancelled",
                "cancelled_at": "2026-06-05T14:30:00+08:00",
            },
            {
                "type": "tool_step", "tool_name": "erp_shops",
                "tool_call_id": "call_C", "status": "cancelled",
                "cancelled_at": "2026-06-05T14:30:00+08:00",
            },
            {
                "type": "interrupt_marker",
                "interrupted_at": "2026-06-05T14:30:00+08:00",
                "reason": "user_cancel",
            },
        ]

        oai_msgs = extract_oai_messages_from_content(content_blocks, role="assistant")
        _assert_oai_protocol_valid(oai_msgs)

        # 完成的工具结果保留
        completed = [m for m in oai_msgs if m.get("role") == "tool" and "32 条订单" in m.get("content", "")]
        assert len(completed) == 1

        # 中断的工具有 synthetic tool_result
        cancelled = [m for m in oai_msgs if m.get("role") == "tool" and "中断了对话" in m.get("content", "")]
        assert len(cancelled) == 2

    def test_find_orphan_in_memory_messages(self):
        """find_orphan_tool_calls 正确识别内存 messages 中的 orphan"""
        messages = [
            _assistant_with_tools(None, [("call_X", "tool1"), ("call_Y", "tool2")]),
            {"role": "tool", "tool_call_id": "call_X", "content": "result_x"},
            # call_Y 是 orphan
        ]
        orphans = find_orphan_tool_calls(messages)
        assert orphans == [("call_Y", "tool2")]


class TestScenario4_TaskResumptionInjection:
    """场景 4：中断后的 TASK RESUMPTION 注入"""

    def test_resumption_template_format(self):
        """模板渲染时间正确"""
        rendered = TASK_RESUMPTION_TEMPLATE.format(ago_text="约 5 分钟前")
        assert "[任务恢复]" in rendered
        assert "约 5 分钟前" in rendered
        assert "工具" in rendered
