"""
统一结果类型（AgentResult）端到端路径验证。

模拟两条真实路径，确保数据在每一跳正确传输：
1. 正常 ERP 查询路径：DepartmentAgent → ERPAgent → ChatToolMixin → ChatHandler → messages
2. 定时任务路径：工具 → ToolLoopExecutor → to_tool_content() → LLM messages

设计文档: docs/document/TECH_Agent通信协议结构化.md
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.agent.agent_result import AgentResult
from services.agent.tool_output import (
    ColumnMeta, FileRef, OutputFormat, OutputStatus, ToolOutput,
)


# ============================================================
# 路径 1：正常 ERP 查询（DepartmentAgent → ERPAgent → 主 Agent）
# ============================================================


class TestERPQueryPath:
    """模拟：用户问"昨天淘宝退货多少" → 完整数据流"""

    def test_department_agent_returns_agent_result(self, tmp_path):
        """DepartmentAgent._build_output() 返回的 ToolOutput 实际是 AgentResult"""
        from services.agent.departments.warehouse_agent import WarehouseAgent

        agent = WarehouseAgent(db=MagicMock(), staging_dir=str(tmp_path))
        result = agent._build_output(
            rows=[{"sku": "A001", "qty": 100}],
            summary="库存数据",
            columns=[ColumnMeta("sku", "text"), ColumnMeta("qty", "integer")],
            staging_dir=str(tmp_path),
        )

        # ToolOutput 别名 → 实际是 AgentResult
        assert isinstance(result, AgentResult)
        assert isinstance(result, ToolOutput)

        # 结构化字段正确
        assert result.format == OutputFormat.FILE_REF
        assert result.file_ref is not None
        assert result.source == "warehouse"
        assert result.status == "success"  # OutputStatus.OK → "success"

    def test_department_agent_error_status_is_str(self):
        """错误状态自动转为 str"""
        result = ToolOutput(
            summary="查询失败: 超时",
            source="trade",
            status=OutputStatus.ERROR,
            error_message="查询失败: 超时",
        )
        assert result.status == "error"
        assert isinstance(result.status, str)

    def test_department_agent_empty_status_is_str(self):
        """空结果状态自动转为 str"""
        result = ToolOutput(
            summary="未找到数据",
            source="aftersale",
            status=OutputStatus.EMPTY,
        )
        assert result.status == "empty"

    def test_erp_agent_converts_loop_result_to_agent_result(self):
        """ERPAgent._convert_result() 从 LoopResult 构建 AgentResult"""
        from services.agent.erp_agent import ERPAgent

        # 模拟 LoopResult
        mock_loop_result = MagicMock()
        mock_loop_result.text = "共 23 笔退货，金额 ¥1,234"
        mock_loop_result.total_tokens = 500
        mock_loop_result.is_llm_synthesis = True
        mock_loop_result.exit_via_ask_user = False
        mock_loop_result.collected_files = []

        result = ERPAgent._convert_result(mock_loop_result)

        # 最终返回 AgentResult
        assert isinstance(result, AgentResult)
        assert result.status == "success"
        assert result.source == "erp_agent"
        assert "23 笔退货" in result.summary

    def test_chat_handler_injects_list_dict_content(self):
        """ChatHandler 从 AgentResult.to_message_content() 拿到 list[dict] 注入 messages"""
        result = AgentResult(
            summary="共 23 笔退货",
            status="success",
            source="erp_agent",
            file_ref=FileRef(
                path="/tmp/test.parquet", filename="test.parquet",
                format="parquet", row_count=23, size_bytes=4096,
                columns=[ColumnMeta("order_no", "text")],
            ),
        )

        # 模拟 ChatHandler 行为 (chat_handler.py:586-600)
        content = result.to_message_content()

        # 必须是 list[dict]，每项 type="text"
        assert isinstance(content, list)
        assert len(content) >= 2  # summary + file_ref
        assert all(b["type"] == "text" for b in content)
        assert "23 笔退货" in content[0]["text"]
        assert "STAGING_DIR" in content[1]["text"]
        assert "23行" in content[1]["text"]

        # 模拟注入 messages
        messages = []
        messages.append({
            "role": "tool",
            "tool_call_id": "call_123",
            "content": content,
        })
        assert messages[0]["content"] == content
        assert isinstance(messages[0]["content"], list)

    def test_chat_tool_mixin_processes_agent_result(self):
        """ChatToolMixin 正确处理 AgentResult 的文件/ask_user/token"""
        result = AgentResult(
            summary="共 50 条订单",
            status="success",
            source="erp_agent",
            tokens_used=500,
            collected_files=[{
                "url": "/tmp/a.xlsx", "name": "a.xlsx",
                "mime_type": "application/xlsx", "size": 2048,
            }],
        )

        # 模拟 ChatToolMixin 行为 (chat_tool_mixin.py:104-132)
        assert isinstance(result, AgentResult)
        assert result.collected_files is not None
        assert len(result.collected_files) == 1
        assert result.collected_files[0]["name"] == "a.xlsx"
        assert result.tokens_used == 500
        assert result.status != "ask_user"

    def test_ask_user_bubbles_with_source(self):
        """ask_user 冒泡时 source 字段正确传递"""
        result = AgentResult(
            summary="需要确认查询范围",
            status="ask_user",
            source="erp_agent",
            ask_user_question="请问要查哪个平台？",
        )

        # 模拟 ChatToolMixin 行为 (chat_tool_mixin.py:118-125)
        assert result.status == "ask_user"
        ask_info = {
            "message": result.ask_user_question,
            "reason": "need_info",
            "tool_call_id": "call_456",
            "source": result.source,
        }
        assert ask_info["source"] == "erp_agent"
        assert ask_info["message"] == "请问要查哪个平台？"


# ============================================================
# 路径 2：定时任务（工具 → ToolLoopExecutor → LLM）
# ============================================================


class TestScheduledTaskPath:
    """模拟：定时任务 → ToolLoopExecutor 工具循环 → to_tool_content() → 内部 LLM"""

    def test_tool_returns_tooloutput_is_agent_result(self):
        """工具返回 ToolOutput(...)，实际创建 AgentResult，isinstance 通过"""
        result = ToolOutput(
            summary="库存 128 件",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("sku", "text"), ColumnMeta("qty", "integer")],
            data=[{"sku": "A001", "qty": 128}],
        )

        # ToolLoopExecutor:554 的 isinstance 检查
        assert isinstance(result, ToolOutput)
        assert isinstance(result, AgentResult)

    def test_validate_works_on_tooloutput(self):
        """ToolLoopExecutor:556 调用 result.validate()"""
        result = ToolOutput(
            summary="库存数据",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("sku", "text")],
            data=[{"sku": "A001"}],
        )
        issues = result.validate()
        assert issues == []

    def test_validate_catches_issues(self):
        """validate 能发现一致性问题"""
        result = ToolOutput(
            summary="",  # 空 summary
            format=OutputFormat.FILE_REF,  # 但没有 file_ref
            source="warehouse",
        )
        issues = result.validate()
        assert len(issues) >= 2  # summary 为空 + file_ref 缺失

    def test_to_tool_content_text_format(self):
        """TEXT 格式 → to_tool_content() 返回纯文本 str"""
        result = ToolOutput(
            summary="共 8 个仓库：仓库A, 仓库B, ...",
            source="warehouse",
        )

        # ToolLoopExecutor:563 调 to_tool_content()
        content = result.to_tool_content()
        assert isinstance(content, str)
        assert content == "共 8 个仓库：仓库A, 仓库B, ..."
        assert "[DATA_REF]" not in content

    def test_to_tool_content_table_format(self):
        """TABLE 格式 → to_tool_content() 返回含 [DATA_REF] 的 str"""
        result = ToolOutput(
            summary="库存查询结果",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[
                ColumnMeta("sku", "text", "商品编码"),
                ColumnMeta("qty", "integer", "数量"),
            ],
            data=[{"sku": "A001", "qty": 128}, {"sku": "B002", "qty": 56}],
            metadata={"product_code": "A001"},
        )

        content = result.to_tool_content()
        assert isinstance(content, str)
        assert "[DATA_REF]" in content
        assert "[/DATA_REF]" in content
        assert "source: warehouse" in content
        assert "storage: inline" in content
        assert "rows: 2" in content
        assert "sku: text  # 商品编码" in content
        assert "qty: integer  # 数量" in content
        assert "product_code: A001" in content
        # 数据以 JSON 格式内联
        assert '"sku": "A001"' in content or '"sku":"A001"' in content

    def test_to_tool_content_file_ref_format(self, tmp_path):
        """FILE_REF 格式 → to_tool_content() 包含路径和大小"""
        fpath = tmp_path / "warehouse_123.parquet"
        fpath.write_bytes(b"x" * 2048)
        ref = FileRef(
            path=str(fpath), filename="warehouse_123.parquet",
            format="parquet", row_count=500, size_bytes=2048,
            columns=[ColumnMeta("sku", "text")],
            preview="前3行预览...",
        )
        result = ToolOutput(
            summary="已查到 500 条库存数据",
            format=OutputFormat.FILE_REF,
            source="warehouse",
            file_ref=ref,
        )

        content = result.to_tool_content()
        assert isinstance(content, str)
        assert "storage: file" in content
        assert "rows: 500" in content
        assert "STAGING_DIR + '/warehouse_123.parquet'" in content
        assert "format: parquet" in content
        assert "size_kb: 2" in content
        assert "前3行预览..." in content

    def test_tool_loop_message_injection(self):
        """模拟 ToolLoopExecutor 完整的结果注入流程"""
        result = ToolOutput(
            summary="库存查询结果",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("sku", "text")],
            data=[{"sku": "A001"}],
        )

        # Step 1: isinstance 检查 (line 554)
        assert isinstance(result, ToolOutput)

        # Step 2: validate (line 556)
        warnings = result.validate()
        assert warnings == []

        # Step 3: to_tool_content (line 563)
        content = result.to_tool_content()
        assert isinstance(content, str)
        assert "[DATA_REF]" in content

        # Step 4: 文件注册 (line 567-569)
        source_for_registry = result.source or "unknown"
        assert source_for_registry == "warehouse"

        # Step 5: 注入 messages (line 572-577)
        messages = []
        messages.append({
            "role": "tool",
            "tool_call_id": "tc_001",
            "timestamp": "2026-04-21T10:00:00+00:00",
            "content": content,
        })
        assert isinstance(messages[0]["content"], str)
        assert "[DATA_REF]" in messages[0]["content"]


# ============================================================
# 跨路径：context 传递（DepartmentAgent 间数据共享）
# ============================================================


class TestCrossAgentContext:
    """DepartmentAgent._extract_field_from_context(list[ToolOutput]) 跨域数据传递"""

    def test_extract_from_inline_data(self):
        """从内联数据提取字段值"""
        from services.agent.departments.warehouse_agent import WarehouseAgent

        # 上游 Agent 返回的 ToolOutput（实际是 AgentResult）
        upstream = ToolOutput(
            summary="库存数据",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("product_code", "text"), ColumnMeta("qty", "integer")],
            data=[
                {"product_code": "A001", "qty": 100},
                {"product_code": "B002", "qty": 200},
            ],
        )

        agent = WarehouseAgent(db=MagicMock())
        values = agent._extract_field_from_context([upstream], "product_code")
        assert values == ["A001", "B002"]

    def test_extract_from_file_ref(self, tmp_path):
        """从 FILE_REF 的 parquet 文件提取字段值"""
        import pandas as pd
        from services.agent.departments.warehouse_agent import WarehouseAgent

        # 写一个真实 parquet
        fpath = tmp_path / "trade_123.parquet"
        df = pd.DataFrame({"product_code": ["X001", "X002"], "amount": [100, 200]})
        df.to_parquet(fpath, index=False)

        upstream = ToolOutput(
            summary="订单数据",
            format=OutputFormat.FILE_REF,
            source="trade",
            file_ref=FileRef(
                path=str(fpath), filename="trade_123.parquet",
                format="parquet", row_count=2, size_bytes=fpath.stat().st_size,
                columns=[ColumnMeta("product_code", "text"), ColumnMeta("amount", "numeric")],
            ),
        )

        agent = WarehouseAgent(db=MagicMock())
        values = agent._extract_field_from_context([upstream], "product_code")
        assert set(values) == {"X001", "X002"}


# ============================================================
# 格式一致性：to_message_content vs to_tool_content
# ============================================================


class TestSerializationConsistency:
    """同一个结果，两种序列化输出各自正确"""

    def test_same_result_different_formats(self):
        """同一份数据，to_message_content 和 to_tool_content 各自输出正确格式"""
        result = AgentResult(
            summary="共 23 笔退货，金额 ¥1,234",
            status="success",
            format=OutputFormat.TABLE,
            source="aftersale",
            columns=[ColumnMeta("order_no", "text"), ColumnMeta("amount", "numeric")],
            data=[{"order_no": "TB001", "amount": 100}],
        )

        # to_message_content → list[dict]（给主 Agent）
        blocks = result.to_message_content()
        assert isinstance(blocks, list)
        assert all(isinstance(b, dict) for b in blocks)
        assert blocks[0]["type"] == "text"
        assert "23 笔退货" in blocks[0]["text"]

        # to_tool_content → str（给工具循环 LLM）
        content = result.to_tool_content()
        assert isinstance(content, str)
        assert "[DATA_REF]" in content
        assert "source: aftersale" in content

        # 两种格式的 summary 内容一致
        assert blocks[0]["text"] in content

    def test_text_format_both_simple(self):
        """TEXT 格式下两种序列化都很简洁"""
        result = AgentResult(
            summary="共 8 个仓库",
            format=OutputFormat.TEXT,
            source="warehouse",
        )

        # to_message_content → 只有一个 text block
        blocks = result.to_message_content()
        assert len(blocks) == 1
        assert blocks[0]["text"] == "共 8 个仓库"

        # to_tool_content → 直接返回 summary
        assert result.to_tool_content() == "共 8 个仓库"
