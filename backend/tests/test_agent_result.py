"""AgentResult 标准结构单元测试。

覆盖场景：success/error/timeout/ask_user/file_ref/data/insights
设计文档: docs/document/TECH_Agent通信协议结构化.md §2.2
"""

import pytest
from services.agent.agent_result import AgentResult
from services.agent.tool_output import FileRef, ColumnMeta


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
        assert blocks[1]["type"] == "file_ref"
        assert blocks[1]["file_ref"]["path"] == ref.path
        assert blocks[1]["file_ref"]["rows"] == 945
        assert blocks[1]["file_ref"]["format"] == "parquet"
        assert blocks[1]["file_ref"]["size_kb"] == 128  # 131072 // 1024

    def test_success_with_inline_data(self):
        """内联数据：text + data 两个 block"""
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
        assert blocks[1]["type"] == "data"
        assert blocks[1]["data"]["rows"] == 2
        assert blocks[1]["data"]["columns"] == ["shop", "count"]
        assert len(blocks[1]["data"]["records"]) == 2

    def test_file_ref_takes_priority_over_data(self):
        """file_ref 和 data 同时存在时，只输出 file_ref"""
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

        types = [b["type"] for b in blocks]
        assert "file_ref" in types
        assert "data" not in types

    def test_with_insights(self):
        """分析洞察：text + insights"""
        result = AgentResult(
            status="success",
            summary="退货率 15%",
            insights=["HZ001 退货率 30%", "原因集中在尺码不合"],
        )
        blocks = result.to_message_content()

        assert len(blocks) == 2
        assert blocks[1]["type"] == "insights"
        assert len(blocks[1]["insights"]) == 2

    def test_full_result(self):
        """完整结果：text + file_ref + insights（3 个 block）"""
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

        types = [b["type"] for b in blocks]
        assert types == ["text", "file_ref", "insights"]

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
        """内联数据最多 20 行预览"""
        data = [{"id": i, "val": f"row_{i}"} for i in range(50)]
        result = AgentResult(
            status="success",
            summary="50 条数据",
            data=data,
        )
        blocks = result.to_message_content()

        assert blocks[1]["data"]["rows"] == 50
        assert len(blocks[1]["data"]["records"]) == 20

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
            agent_name="erp_agent",
            tokens_used=1500,
            confidence=0.6,
        )
        parsed = json.loads(result.to_json())
        assert parsed["status"] == "success"
        assert len(parsed["summary"]) <= 200
        assert parsed["agent_name"] == "erp_agent"
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
        assert result.agent_name == ""
        assert result.tokens_used == 0
        assert result.confidence == 1.0
        assert result.error_message == ""
        assert result.ask_user_question == ""
        assert result.insights is None
        assert result.follow_up is None
        assert result.metadata == {}
