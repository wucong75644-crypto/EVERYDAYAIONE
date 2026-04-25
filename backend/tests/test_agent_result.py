"""AgentResult 统一结果类型单元测试。

覆盖场景：success/error/timeout/ask_user/file_ref/data/insights
         __post_init__ / to_tool_content / validate / ToolOutput 别名
设计文档: docs/document/TECH_Agent通信协议结构化.md
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from services.agent.agent_result import AgentResult
from services.agent.tool_output import (
    FileRef, ColumnMeta, OutputFormat, OutputStatus,
)


# ============================================================
# to_message_content() 测试
# ============================================================


class TestToMessageContent:
    """AgentResult.to_message_content() → list[dict]"""

    def test_success_text_only(self):
        """纯文本结果：只有 text block"""
        result = AgentResult(
            status="success",
            summary="昨天淘宝退货共 23 笔",
        )
        blocks = result.to_message_content()

        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "昨天淘宝退货共 23 笔"

    def test_success_with_file_ref(self):
        """文件引用：text + file_ref 两个 block"""
        ref = FileRef(
            path="staging/conv123/trade_20260420.parquet",
            filename="trade_20260420.parquet",
            format="parquet",
            row_count=945,
            size_bytes=131072,
            columns=[ColumnMeta(name="order_no", dtype="str", label="订单号")],
        )
        result = AgentResult(
            status="success",
            summary="共 945 条订单已导出",
            file_ref=ref,
        )
        blocks = result.to_message_content()

        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "text"  # file_ref 以 text 形式输出
        assert ref.sandbox_ref in blocks[1]["text"]
        assert "945行" in blocks[1]["text"]
        assert "parquet" in blocks[1]["text"]

    def test_success_with_inline_data(self):
        """内联数据：text + data（text 形式）两个 block"""
        result = AgentResult(
            status="success",
            summary="按店铺统计退货",
            data=[
                {"shop": "旗舰店", "count": 15},
                {"shop": "专营店", "count": 8},
            ],
            columns=[
                ColumnMeta(name="shop", dtype="str", label="店铺"),
                ColumnMeta(name="count", dtype="int", label="数量"),
            ],
        )
        blocks = result.to_message_content()

        assert len(blocks) == 2
        assert blocks[1]["type"] == "text"
        assert "2行" in blocks[1]["text"]
        # label 生效：列名和 data key 都翻译为中文
        assert "店铺" in blocks[1]["text"]
        assert "数量" in blocks[1]["text"]

    def test_file_ref_takes_priority_over_data(self):
        """file_ref 和 data 同时存在时，只输出 file_ref 不输出 data"""
        ref = FileRef(
            path="staging/test.parquet",
            filename="test.parquet",
            format="parquet",
            row_count=500,
            size_bytes=51200,
            columns=[],
        )
        result = AgentResult(
            status="success",
            summary="数据已导出",
            file_ref=ref,
            data=[{"a": 1}],
        )
        blocks = result.to_message_content()

        # 2 个 block：摘要 + 文件引用（data 不输出）
        assert len(blocks) == 2
        assert ref.sandbox_ref in blocks[1]["text"]

    def test_with_insights(self):
        """分析洞察：text + insights（text 形式）"""
        result = AgentResult(
            status="success",
            summary="退货率 15%",
            insights=["HZ001 退货率 30%", "原因集中在尺码不合"],
        )
        blocks = result.to_message_content()

        assert len(blocks) == 2
        assert blocks[1]["type"] == "text"
        assert "HZ001 退货率 30%" in blocks[1]["text"]
        assert "尺码不合" in blocks[1]["text"]

    def test_full_result(self):
        """完整结果：所有 block 都是 type=text"""
        ref = FileRef(
            path="staging/test.parquet",
            filename="test.parquet",
            format="parquet",
            row_count=945,
            size_bytes=131072,
            columns=[],
        )
        result = AgentResult(
            status="success",
            summary="共 945 条",
            file_ref=ref,
            insights=["异常商品 3 个"],
        )
        blocks = result.to_message_content()

        assert len(blocks) == 3
        assert all(b["type"] == "text" for b in blocks)

    def test_error_result(self):
        """错误结果：只有 text block"""
        result = AgentResult(
            status="error",
            summary="查询超时",
            error_message="域 trade 查询超时",
        )
        blocks = result.to_message_content()

        assert len(blocks) == 1
        assert blocks[0]["text"] == "查询超时"

    def test_timeout_result(self):
        """超时结果"""
        result = AgentResult(
            status="timeout",
            summary="查询超时（30秒），请缩小范围",
        )
        blocks = result.to_message_content()

        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"

    def test_ask_user_result(self):
        """追问结果"""
        result = AgentResult(
            status="ask_user",
            summary="需要确认查询范围",
            ask_user_question="请问要查哪个平台？",
        )
        blocks = result.to_message_content()

        assert len(blocks) == 1
        assert result.ask_user_question == "请问要查哪个平台？"

    def test_inline_data_preview_limit(self):
        """内联数据预览最多 5 行"""
        data = [{"id": i, "val": f"row_{i}"} for i in range(50)]
        result = AgentResult(
            status="success",
            summary="50 条数据",
            data=data,
        )
        blocks = result.to_message_content()

        assert "50行" in blocks[1]["text"]
        # 预览只含前 5 行数据
        assert "row_4" in blocks[1]["text"]
        assert "row_10" not in blocks[1]["text"]

    def test_empty_data_no_data_block(self):
        """空数据列表不输出 data block"""
        result = AgentResult(
            status="success",
            summary="无数据",
            data=[],
        )
        blocks = result.to_message_content()

        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"

    def test_no_insights_no_insights_block(self):
        """insights 为 None 不输出 insights block"""
        result = AgentResult(
            status="success",
            summary="正常",
            insights=None,
        )
        blocks = result.to_message_content()

        types = [b["type"] for b in blocks]
        assert "insights" not in types


# ============================================================
# to_text() 测试
# ============================================================


class TestToText:
    """AgentResult.to_text() → str（供 tool_context 等消费）"""

    def test_text_only(self):
        result = AgentResult(status="success", summary="共 23 笔")
        assert result.to_text() == "共 23 笔"

    def test_with_file_ref(self):
        ref = FileRef(
            path="staging/test.parquet",
            filename="test.parquet",
            format="parquet",
            row_count=945,
            size_bytes=131072,
            columns=[],
        )
        result = AgentResult(
            status="success", summary="已导出", file_ref=ref,
        )
        text = result.to_text()
        assert "staging/test.parquet" in text
        assert "945行" in text

    def test_with_insights(self):
        result = AgentResult(
            status="success",
            summary="退货率 15%",
            insights=["HZ001 异常", "尺码问题"],
        )
        text = result.to_text()
        assert "洞察" in text
        assert "HZ001 异常" in text


# ============================================================
# to_json() 测试
# ============================================================


class TestToJson:
    """AgentResult.to_json() → JSON 字符串"""

    def test_serializes(self):
        import json

        result = AgentResult(
            status="success",
            summary="测试" * 200,
            source="erp_agent",
            tokens_used=1500,
            confidence=0.6,
        )
        parsed = json.loads(result.to_json())
        assert parsed["status"] == "success"
        assert len(parsed["summary"]) <= 200
        assert parsed["source"] == "erp_agent"
        assert parsed["tokens_used"] == 1500
        assert parsed["confidence"] == 0.6


# ============================================================
# 字段完整性测试
# ============================================================


class TestFieldCompleteness:
    """AgentResult 字段覆盖 ERPAgentResult 的所有关键字段"""

    def test_has_confidence(self):
        result = AgentResult(status="success", summary="ok", confidence=0.6)
        assert result.confidence == 0.6

    def test_has_collected_files(self):
        files = [{"url": "/tmp/a.parquet", "name": "a.parquet",
                  "mime_type": "application/octet-stream", "size": 1024}]
        result = AgentResult(
            status="success", summary="ok", collected_files=files,
        )
        assert result.collected_files == files

    def test_has_ask_user_question(self):
        result = AgentResult(
            status="ask_user", summary="需确认",
            ask_user_question="查哪个平台？",
        )
        assert result.ask_user_question == "查哪个平台？"

    def test_has_metadata(self):
        result = AgentResult(
            status="success", summary="ok",
            metadata={"_degraded": True},
        )
        assert result.metadata["_degraded"] is True

    def test_default_values(self):
        result = AgentResult(status="success", summary="ok")
        assert result.file_ref is None
        assert result.data is None
        assert result.columns is None
        assert result.collected_files is None
        assert result.source == ""
        assert result.tokens_used == 0
        assert result.confidence == 1.0
        assert result.error_message == ""
        assert result.ask_user_question == ""
        assert result.insights is None
        assert result.follow_up is None
        assert result.metadata == {}
        assert result.format == OutputFormat.TEXT


# ============================================================
# __post_init__ 状态归一化
# ============================================================


class TestPostInit:
    """__post_init__ 自动转换 OutputStatus → str + "ok" → "success" """

    def test_output_status_enum_to_str(self):
        """OutputStatus.ERROR → "error" """
        r = AgentResult(summary="失败", status=OutputStatus.ERROR)
        assert r.status == "error"
        assert isinstance(r.status, str)

    def test_output_status_ok_to_success(self):
        """OutputStatus.OK → "success"（"ok" 归一化为 "success"）"""
        r = AgentResult(summary="成功", status=OutputStatus.OK)
        assert r.status == "success"

    def test_output_status_empty(self):
        """OutputStatus.EMPTY → "empty" """
        r = AgentResult(summary="无数据", status=OutputStatus.EMPTY)
        assert r.status == "empty"

    def test_output_status_partial(self):
        """OutputStatus.PARTIAL → "partial" """
        r = AgentResult(summary="部分", status=OutputStatus.PARTIAL)
        assert r.status == "partial"

    def test_str_ok_normalized(self):
        """字符串 "ok" → "success" """
        r = AgentResult(summary="test", status="ok")
        assert r.status == "success"

    def test_str_error_unchanged(self):
        """字符串 "error" 不变"""
        r = AgentResult(summary="test", status="error")
        assert r.status == "error"

    def test_default_status_is_success(self):
        """不传 status 时默认 "success" """
        r = AgentResult(summary="test")
        assert r.status == "success"


# ============================================================
# to_tool_content() 工具循环 LLM 序列化
# ============================================================


class TestToToolContent:
    """to_tool_content() → str（给工具循环内部 LLM）"""

    def test_text_format_returns_summary(self):
        """TEXT 格式直接返回 summary"""
        r = AgentResult(summary="共8个仓库", format=OutputFormat.TEXT)
        assert r.to_tool_content() == "共8个仓库"
        assert "[DATA_REF]" not in r.to_tool_content()

    def test_table_format_has_data_ref(self):
        """TABLE 格式包含 [DATA_REF] 标签"""
        r = AgentResult(
            summary="库存数据",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("sku", "text", "商品编码")],
            data=[{"sku": "A001"}],
        )
        content = r.to_tool_content()
        assert "[DATA_REF]" in content
        assert "[/DATA_REF]" in content
        assert "source: warehouse" in content
        assert "storage: inline" in content
        assert "rows: 1" in content
        # label 生效：列名和 data key 都翻译为中文
        assert "商品编码: text" in content
        assert '"商品编码": "A001"' in content

    def test_file_ref_format_has_path(self, tmp_path):
        """FILE_REF 格式包含文件路径和大小"""
        fpath = tmp_path / "test.parquet"
        fpath.write_bytes(b"x" * 1024)
        ref = FileRef(
            path=str(fpath), filename="test.parquet",
            format="parquet", row_count=100, size_bytes=1024,
            columns=[ColumnMeta("id", "integer")],
        )
        r = AgentResult(
            summary="数据已导出",
            format=OutputFormat.FILE_REF,
            source="trade",
            file_ref=ref,
        )
        content = r.to_tool_content()
        assert "storage: file" in content
        assert "rows: 100" in content
        assert "format: parquet" in content
        assert "STAGING_DIR" in content

    def test_metadata_included(self):
        """metadata 字段出现在 [DATA_REF] 中"""
        r = AgentResult(
            summary="test",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("x", "integer")],
            data=[{"x": 1}],
            metadata={"doc_type": "order", "time_range": "昨天"},
        )
        content = r.to_tool_content()
        assert "doc_type: order" in content
        assert "time_range: 昨天" in content

    def test_inline_data_json(self):
        """内联数据以 JSON 格式输出"""
        data = [{"id": 1, "name": "测试"}]
        r = AgentResult(
            summary="test",
            format=OutputFormat.TABLE,
            source="test",
            columns=[ColumnMeta("id", "integer"), ColumnMeta("name", "text")],
            data=data,
        )
        content = r.to_tool_content()
        assert '"id": 1' in content or '"id":1' in content


# ============================================================
# validate() 一致性校验
# ============================================================


class TestValidate:
    """validate() → list[str] 校验规则"""

    def test_valid_text_no_issues(self):
        """正常 TEXT 结果无违规"""
        r = AgentResult(summary="ok", source="test")
        assert r.validate() == []

    def test_empty_summary(self):
        r = AgentResult(summary="", source="test")
        issues = r.validate()
        assert any("summary" in i for i in issues)

    def test_file_ref_missing(self):
        """FILE_REF 格式但缺 file_ref"""
        r = AgentResult(
            summary="test", format=OutputFormat.FILE_REF, source="test",
        )
        issues = r.validate()
        assert any("file_ref" in i for i in issues)

    def test_table_missing_columns(self):
        """TABLE 格式但缺 columns"""
        r = AgentResult(
            summary="test", format=OutputFormat.TABLE, source="test",
        )
        issues = r.validate()
        assert any("columns" in i for i in issues)

    def test_error_missing_error_message(self):
        """ERROR 状态但缺 error_message"""
        r = AgentResult(summary="失败", status="error", source="test")
        issues = r.validate()
        assert any("error_message" in i or "ERROR" in i for i in issues)

    def test_error_with_message_ok(self):
        """ERROR 状态有 error_message 则通过"""
        r = AgentResult(
            summary="失败", status="error",
            error_message="连接超时", source="test",
        )
        issues = r.validate()
        assert not any("ERROR" in i for i in issues)


# ============================================================
# ToolOutput 别名兼容性
# ============================================================


class TestToolOutputAlias:
    """ToolOutput = AgentResult 别名正确工作"""

    def test_isinstance_compatible(self):
        """isinstance(AgentResult实例, ToolOutput) 为 True"""
        from services.agent.tool_output import ToolOutput
        r = AgentResult(summary="test")
        assert isinstance(r, ToolOutput)

    def test_tooloutput_creates_agent_result(self):
        """ToolOutput(...) 实际创建 AgentResult"""
        from services.agent.tool_output import ToolOutput
        r = ToolOutput(summary="test", source="warehouse")
        assert type(r).__name__ == "AgentResult"
        assert r.summary == "test"
        assert r.source == "warehouse"

    def test_tooloutput_with_output_status(self):
        """ToolOutput 构造传 OutputStatus 枚举自动转 str"""
        from services.agent.tool_output import ToolOutput
        r = ToolOutput(
            summary="无数据",
            status=OutputStatus.EMPTY,
            source="warehouse",
        )
        assert r.status == "empty"

    def test_tooloutput_default_status(self):
        """ToolOutput 不传 status 默认 "success" """
        from services.agent.tool_output import ToolOutput
        r = ToolOutput(summary="ok")
        assert r.status == "success"

    def test_tooloutput_has_to_tool_content(self):
        """ToolOutput 实例可调 to_tool_content()"""
        from services.agent.tool_output import ToolOutput
        r = ToolOutput(
            summary="共8个仓库",
            format=OutputFormat.TEXT,
            source="warehouse",
        )
        assert r.to_tool_content() == "共8个仓库"

    def test_tooloutput_has_to_message_content(self):
        """ToolOutput 实例可调 to_message_content()（返回 list[dict]）"""
        from services.agent.tool_output import ToolOutput
        r = ToolOutput(summary="test")
        blocks = r.to_message_content()
        assert isinstance(blocks, list)
        assert blocks[0]["type"] == "text"
