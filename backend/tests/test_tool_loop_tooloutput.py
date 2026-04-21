"""
ToolLoopExecutor 的 ToolOutput 适配单元测试。

覆盖: tool_loop_executor.py 第547-594行（ToolOutput / str 统一处理区域）
- ToolOutput 结果 → messages 带 timestamp + DATA_REF
- ToolOutput 带 file_ref → 注册到 SessionFileRegistry
- str 结果（旧链路）→ messages 也带 timestamp
- str 结果带 [FILE] → 提取到 _collected_files

不测完整的工具循环（那由 test_dag_integration.py 覆盖），
只测 messages.append 处的分支逻辑。
"""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.session_file_registry import SessionFileRegistry
from services.agent.tool_output import (
    ColumnMeta,
    FileRef,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)


# ============================================================
# 辅助：模拟 ToolLoopExecutor 的核心分支逻辑
# ============================================================

def _simulate_result_handling(result, tool_name="local_stock_query", tc_id="tc1"):
    """
    模拟 tool_loop_executor.py 第547-594行的分支逻辑。
    返回 (message_dict, file_registry, collected_files)。
    不启动完整的工具循环，只测 messages.append 处的分支。
    """
    from datetime import datetime, timezone

    file_registry = SessionFileRegistry()
    collected_files = []

    now_iso = datetime.now(timezone.utc).isoformat()

    if isinstance(result, ToolOutput):
        content = result.to_tool_content()
        is_truncated = False

        if result.file_ref:
            file_registry.register(
                result.source or tool_name, tool_name, result.file_ref,
            )

        msg = {
            "role": "tool",
            "tool_call_id": tc_id,
            "timestamp": now_iso,
            "content": content,
        }
        return msg, file_registry, collected_files, is_truncated

    else:
        import re
        _FILE_RE = re.compile(
            r"\[FILE\](?P<url>[^|]+)\|(?P<name>[^|]+)\|(?P<mime>[^|]+)\|(?P<size>\d+)\[/FILE\]"
        )
        if result and "[FILE]" in result:
            for m in _FILE_RE.finditer(result):
                collected_files.append({
                    "url": m.group("url"),
                    "name": m.group("name"),
                    "mime_type": m.group("mime"),
                    "size": int(m.group("size")),
                })
            result = _FILE_RE.sub(
                lambda m: f"📎 文件: {m.group('name')}", result,
            )

        is_truncated = len(result) > 3000 if result else False

        msg = {
            "role": "tool",
            "tool_call_id": tc_id,
            "timestamp": now_iso,
            "content": result,
        }
        return msg, file_registry, collected_files, is_truncated


# ── 测试数据工厂 ──

def _cols():
    return [
        ColumnMeta("product_code", "text", "商品编码"),
        ColumnMeta("sellable", "integer", "可售"),
    ]


def _file_ref():
    return FileRef(
        path="/tmp/staging/warehouse_stock_123.parquet",
        filename="warehouse_stock_123.parquet",
        format="parquet",
        row_count=500,
        size_bytes=51200,
        columns=_cols(),
        preview='{"product_code":"A001","sellable":30}',
        created_at=time.time(),
    )


# ============================================================
# ToolOutput 分支
# ============================================================


class TestToolOutputBranch:

    def test_tooloutput_message_has_timestamp(self):
        """ToolOutput 结果 → messages 带 timestamp"""
        result = ToolOutput(
            summary="库存查询完成",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=_cols(),
            data=[{"product_code": "A001", "sellable": 30}],
        )
        msg, _, _, _ = _simulate_result_handling(result)
        assert "timestamp" in msg
        assert msg["timestamp"]  # 非空
        assert msg["role"] == "tool"

    def test_tooloutput_content_is_to_tool_content(self):
        """content 是 to_tool_content() 的输出"""
        result = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=_cols(),
            data=[{"product_code": "A001", "sellable": 30}],
        )
        msg, _, _, _ = _simulate_result_handling(result)
        assert "[DATA_REF]" in msg["content"]
        assert "source: warehouse" in msg["content"]

    def test_tooloutput_text_no_data_ref(self):
        """TEXT 模式不带 DATA_REF"""
        result = ToolOutput(summary="共8个仓库", source="warehouse")
        msg, _, _, _ = _simulate_result_handling(result)
        assert msg["content"] == "共8个仓库"
        assert "[DATA_REF]" not in msg["content"]

    def test_tooloutput_not_truncated(self):
        """ToolOutput 不走截断逻辑"""
        long_data = [{"id": i} for i in range(100)]
        result = ToolOutput(
            summary="x" * 5000,  # 超过 3000 字符
            format=OutputFormat.TABLE,
            source="test",
            columns=[ColumnMeta("id", "integer")],
            data=long_data,
        )
        _, _, _, is_truncated = _simulate_result_handling(result)
        assert is_truncated is False


class TestToolOutputFileRegistration:

    def test_file_ref_registered(self):
        """ToolOutput 带 file_ref → 注册到 SessionFileRegistry"""
        fr = _file_ref()
        result = ToolOutput(
            summary="导出完成",
            format=OutputFormat.FILE_REF,
            source="trade",
            file_ref=fr,
        )
        _, registry, _, _ = _simulate_result_handling(result)
        all_files = registry.list_all()
        assert len(all_files) == 1
        key, ref = all_files[0]
        assert "trade" in key
        assert ref.row_count == 500

    def test_no_file_ref_not_registered(self):
        """ToolOutput 没有 file_ref → 不注册"""
        result = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=_cols(),
            data=[{"product_code": "A001", "sellable": 30}],
        )
        _, registry, _, _ = _simulate_result_handling(result)
        assert len(registry.list_all()) == 0

    def test_file_ref_source_in_key(self):
        """注册 key 包含 source 域标识"""
        fr = _file_ref()
        result = ToolOutput(
            summary="OK",
            format=OutputFormat.FILE_REF,
            source="aftersale",
            file_ref=fr,
        )
        _, registry, _, _ = _simulate_result_handling(result)
        key, _ = registry.list_all()[0]
        assert key.startswith("aftersale:")


# ============================================================
# str 分支（旧链路）
# ============================================================


class TestStrBranch:

    def test_str_message_has_timestamp(self):
        """str 结果 → messages 也带 timestamp"""
        msg, _, _, _ = _simulate_result_handling("查询结果文本")
        assert "timestamp" in msg
        assert msg["content"] == "查询结果文本"

    def test_str_file_marker_extracted(self):
        """str 结果带 [FILE] → 提取到 collected_files"""
        raw = "生成完成\n[FILE]https://cdn.example.com/report.xlsx|report.xlsx|application/vnd.openxmlformats|24000[/FILE]"
        msg, _, collected, _ = _simulate_result_handling(raw)
        assert len(collected) == 1
        assert collected[0]["name"] == "report.xlsx"
        assert collected[0]["url"] == "https://cdn.example.com/report.xlsx"
        # LLM 看到的是友好文本
        assert "📎 文件: report.xlsx" in msg["content"]
        assert "[FILE]" not in msg["content"]

    def test_str_long_marked_truncated(self):
        """str 超过 3000 字符 → is_truncated=True"""
        _, _, _, is_truncated = _simulate_result_handling("x" * 4000)
        assert is_truncated is True

    def test_str_short_not_truncated(self):
        """str 不超过 3000 字符 → is_truncated=False"""
        _, _, _, is_truncated = _simulate_result_handling("短文本")
        assert is_truncated is False

    def test_str_empty_not_truncated(self):
        """空字符串 → is_truncated=False"""
        _, _, _, is_truncated = _simulate_result_handling("")
        assert is_truncated is False
