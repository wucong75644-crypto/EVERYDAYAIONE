"""
ToolOutput 结构化协议 + SessionFileRegistry 单元测试。

覆盖：tool_output.py / session_file_registry.py
设计文档: docs/document/TECH_多Agent单一职责重构.md §4.1 + §4.2
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_tests_dir = Path(__file__).parent
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.tool_output import (
    ColumnMeta,
    FileRef,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.agent.session_file_registry import SessionFileRegistry


# ============================================================
# 测试数据工厂
# ============================================================

def _cols_stock() -> list[ColumnMeta]:
    return [
        ColumnMeta("product_code", "text", "商品编码"),
        ColumnMeta("sellable", "integer", "可售库存"),
        ColumnMeta("onway", "integer", "采购在途"),
    ]


def _data_stock() -> list[dict]:
    return [
        {"product_code": "A001", "sellable": 30, "onway": 50},
        {"product_code": "A001", "sellable": 0, "onway": 0},  # 零值
    ]


def _file_ref(path: str = "/tmp/test.parquet", rows: int = 500) -> FileRef:
    return FileRef(
        path=path,
        filename="test.parquet",
        format="parquet",
        row_count=rows,
        size_bytes=51200,
        columns=_cols_stock(),
        preview='{"product_code":"A001","sellable":30}',
        created_at=time.time(),
    )


# ============================================================
# ToolOutput — TEXT 模式
# ============================================================

class TestToolOutputText:
    def test_text_returns_summary_only(self):
        t = ToolOutput(summary="共8个仓库", source="warehouse")
        assert t.to_tool_content() == "共8个仓库"

    def test_text_no_data_ref_tag(self):
        t = ToolOutput(summary="列表", source="warehouse")
        assert "[DATA_REF]" not in t.to_tool_content()

    def test_text_default_status_is_ok(self):
        t = ToolOutput(summary="x")
        assert t.status == "success"
        assert t.error_message == ""


# ============================================================
# ToolOutput — TABLE 模式（inline）
# ============================================================

class TestToolOutputTable:
    def test_inline_has_data_ref(self):
        t = ToolOutput(
            summary="库存查询完成",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=_cols_stock(),
            data=_data_stock(),
        )
        content = t.to_tool_content()
        assert "[DATA_REF]" in content
        assert "[/DATA_REF]" in content

    def test_inline_required_fields(self):
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=_cols_stock(),
            data=_data_stock(),
        )
        content = t.to_tool_content()
        assert "source: warehouse" in content
        assert "storage: inline" in content
        assert "rows: 2" in content

    def test_inline_columns_with_labels(self):
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="wh",
            columns=_cols_stock(),
            data=[],
        )
        content = t.to_tool_content()
        # label 生效：列名用中文
        assert "商品编码: text" in content
        assert "可售库存: integer" in content

    def test_inline_column_without_label(self):
        cols = [ColumnMeta("id", "integer")]
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="x",
            columns=cols,
            data=[{"id": 1}],
        )
        content = t.to_tool_content()
        assert "- id: integer\n" in content  # 没有 # 标签

    def test_inline_data_json_embedded(self):
        data = [{"code": "A", "qty": 10}]
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="x",
            columns=[ColumnMeta("code", "text"), ColumnMeta("qty", "integer")],
            data=data,
        )
        content = t.to_tool_content()
        assert "data:" in content
        parsed = json.loads(
            content.split("data:\n  ")[1].split("\n[/DATA_REF]")[0]
        )
        assert parsed == data

    def test_inline_preserves_zero_values(self):
        """零值（库存=0）不能被丢弃——方案 §13.4 零值保护"""
        data = [{"product_code": "A001", "sellable": 0}]
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="wh",
            columns=_cols_stock()[:2],
            data=data,
        )
        assert t.data[0]["sellable"] == 0

    def test_inline_over_200_rows_no_data_field(self):
        """超过200行不内联数据"""
        big_data = [{"id": i} for i in range(201)]
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="x",
            columns=[ColumnMeta("id", "integer")],
            data=big_data,
        )
        content = t.to_tool_content()
        assert "rows: 201" in content
        assert "data:" not in content


# ============================================================
# ToolOutput — FILE_REF 模式
# ============================================================

class TestToolOutputFileRef:
    def test_file_ref_required_fields(self):
        fr = _file_ref()
        t = ToolOutput(
            summary="导出完成",
            format=OutputFormat.FILE_REF,
            source="trade",
            file_ref=fr,
        )
        content = t.to_tool_content()
        assert "storage: file" in content
        assert "rows: 500" in content
        assert "path: STAGING_DIR + '/test.parquet'" in content
        assert "format: parquet" in content
        assert "size_kb: 50" in content

    def test_file_ref_preview(self):
        fr = _file_ref()
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.FILE_REF,
            source="x",
            file_ref=fr,
        )
        content = t.to_tool_content()
        assert "preview:" in content
        assert "A001" in content

    def test_file_ref_uses_sandbox_ref_not_path(self):
        """FILE_REF 模式输出 sandbox_ref，不暴露绝对路径"""
        fr = FileRef(
            path="/data/workspace/org/abc/user1/staging/conv99/local_order_123.parquet",
            filename="local_order_123.parquet",
            format="parquet",
            row_count=20,
            size_bytes=2048,
            columns=[ColumnMeta("order_no", "text")],
        )
        t = ToolOutput(
            summary="导出完成",
            format=OutputFormat.FILE_REF,
            source="trade",
            file_ref=fr,
        )
        content = t.to_tool_content()
        # 必须包含 sandbox_ref 格式
        assert "STAGING_DIR + '/local_order_123.parquet'" in content
        # 绝对路径不能泄漏给 LLM
        assert "/data/workspace/" not in content
        assert "org/abc" not in content

    def test_file_ref_columns_from_ref(self):
        """columns 为 None 时从 file_ref.columns 取"""
        fr = _file_ref()
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.FILE_REF,
            source="x",
            columns=None,
            file_ref=fr,
        )
        content = t.to_tool_content()
        # file_ref 的 columns 也用 label
        assert "商品编码: text" in content


# ============================================================
# ToolOutput — metadata 动态字段
# ============================================================

class TestToolOutputMetadata:
    def test_metadata_rendered_in_data_ref(self):
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="aftersale",
            columns=_cols_stock(),
            data=_data_stock(),
            metadata={
                "doc_type": "aftersale",
                "time_range": "2026-03-01 ~ 2026-03-31",
            },
        )
        content = t.to_tool_content()
        assert "doc_type: aftersale" in content
        assert "time_range: 2026-03-01 ~ 2026-03-31" in content

    def test_metadata_none_values_skipped(self):
        t = ToolOutput(
            summary="OK",
            format=OutputFormat.TABLE,
            source="x",
            columns=_cols_stock(),
            data=[],
            metadata={"doc_type": None, "platform": ""},
        )
        content = t.to_tool_content()
        assert "doc_type" not in content
        assert "platform" not in content

    def test_metadata_not_in_text_mode(self):
        t = ToolOutput(
            summary="OK",
            source="x",
            metadata={"doc_type": "order"},
        )
        assert "doc_type" not in t.to_tool_content()


# ============================================================
# ToolOutput — validate (v6)
# ============================================================


class TestToolOutputValidate:
    """v6: ToolOutput.validate() 内部一致性校验"""

    def test_valid_text_output(self):
        t = ToolOutput(summary="OK", source="test")
        assert t.validate() == []

    def test_empty_summary(self):
        t = ToolOutput(summary="", source="test")
        issues = t.validate()
        assert any("summary" in i for i in issues)

    def test_file_ref_missing(self):
        t = ToolOutput(
            summary="OK", source="test",
            format=OutputFormat.FILE_REF, file_ref=None,
        )
        issues = t.validate()
        assert any("file_ref" in i for i in issues)

    def test_table_missing_columns(self):
        t = ToolOutput(
            summary="OK", source="test",
            format=OutputFormat.TABLE, columns=None,
        )
        issues = t.validate()
        assert any("columns" in i for i in issues)

    def test_error_missing_message(self):
        t = ToolOutput(
            summary="失败", source="test",
            status=OutputStatus.ERROR, error_message="",
        )
        issues = t.validate()
        assert any("error_message" in i for i in issues)

    def test_valid_table_output(self):
        t = ToolOutput(
            summary="OK", source="test",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("id", "integer")],
            data=[{"id": 1}],
        )
        assert t.validate() == []


# ============================================================
# ToolOutput — OutputStatus
# ============================================================

class TestOutputStatus:
    def test_error_status(self):
        t = ToolOutput(
            summary="失败",
            status=OutputStatus.ERROR,
            error_message="权限不足",
        )
        assert t.status == "error"
        assert t.error_message == "权限不足"

    def test_partial_status(self):
        t = ToolOutput(summary="部分", status=OutputStatus.PARTIAL)
        assert t.status == "partial"

    def test_empty_status(self):
        t = ToolOutput(summary="无数据", status=OutputStatus.EMPTY)
        assert t.status == "empty"


# ============================================================
# FileRef
# ============================================================

class TestFileRef:
    def test_is_valid_file_not_exists(self):
        fr = _file_ref(path="/nonexistent/file.parquet")
        assert not fr.is_valid()

    def test_is_valid_file_exists(self, tmp_path):
        p = tmp_path / "test.parquet"
        p.write_bytes(b"data")
        fr = FileRef(
            path=str(p),
            filename="test.parquet",
            format="parquet",
            row_count=1,
            size_bytes=4,
            columns=[],
            created_at=time.time(),
        )
        assert fr.is_valid()

    def test_is_valid_expired(self, tmp_path):
        p = tmp_path / "old.parquet"
        p.write_bytes(b"data")
        fr = FileRef(
            path=str(p),
            filename="old.parquet",
            format="parquet",
            row_count=1,
            size_bytes=4,
            columns=[],
            created_at=1.0,  # 很久以前
        )
        assert not fr.is_valid(max_age_seconds=10)

    def test_frozen_dataclass(self):
        fr = _file_ref()
        with pytest.raises(AttributeError):
            fr.row_count = 999  # type: ignore[misc]

    def test_sandbox_ref_format(self):
        """sandbox_ref 返回 STAGING_DIR + '/filename' 格式（对标 OpenAI /mnt/data/）"""
        fr = _file_ref(path="/data/workspace/staging/conv123/trade_123.parquet")
        assert fr.sandbox_ref == "STAGING_DIR + '/test.parquet'"

    def test_sandbox_ref_uses_filename_not_path(self):
        """sandbox_ref 只用 filename，不暴露绝对路径"""
        fr = FileRef(
            path="/absolute/secret/path/staging/conv/report.parquet",
            filename="report.parquet",
            format="parquet",
            row_count=10,
            size_bytes=1024,
            columns=[],
        )
        assert "/absolute/" not in fr.sandbox_ref
        assert "report.parquet" in fr.sandbox_ref

    def test_sandbox_ref_with_domain_prefix(self):
        """域前缀文件名（department_agent 生成）也正常"""
        fr = FileRef(
            path="/tmp/staging/conv/warehouse_1713600000.parquet",
            filename="warehouse_1713600000.parquet",
            format="parquet",
            row_count=200,
            size_bytes=4096,
            columns=[],
        )
        assert fr.sandbox_ref == "STAGING_DIR + '/warehouse_1713600000.parquet'"


# ============================================================
# ColumnMeta
# ============================================================

class TestColumnMeta:
    def test_frozen_dataclass(self):
        cm = ColumnMeta("x", "text")
        with pytest.raises(AttributeError):
            cm.name = "y"  # type: ignore[misc]

    def test_label_optional(self):
        cm = ColumnMeta("id", "integer")
        assert cm.label == ""


# ============================================================
# SessionFileRegistry
# ============================================================

class TestSessionFileRegistry:
    def test_register_and_list(self):
        reg = SessionFileRegistry()
        fr = _file_ref()
        reg.register("warehouse", "local_stock_query", fr)
        assert len(reg.list_all()) == 1

    def test_register_no_overwrite(self):
        """同域同工具注册两次不覆盖（key 含 timestamp）"""
        reg = SessionFileRegistry()
        fr1 = _file_ref(path="/tmp/a.parquet", rows=10)
        fr2 = _file_ref(path="/tmp/b.parquet", rows=20)
        with patch("services.agent.session_file_registry._time") as mock_time:
            mock_time.time.side_effect = [1000, 1001]
            reg.register("warehouse", "local_data", fr1)
            reg.register("warehouse", "local_data", fr2)
        assert len(reg.list_all()) == 2

    def test_get_by_domain(self):
        reg = SessionFileRegistry()
        fr_wh = _file_ref(path="/tmp/wh.parquet")
        fr_pur = _file_ref(path="/tmp/pur.parquet")
        reg.register("warehouse", "stock", fr_wh)
        reg.register("purchase", "order", fr_pur)
        wh_files = reg.get_by_domain("warehouse")
        assert len(wh_files) == 1
        assert wh_files[0].path == "/tmp/wh.parquet"

    def test_get_by_domain_empty(self):
        reg = SessionFileRegistry()
        assert reg.get_by_domain("trade") == []

    def test_get_latest(self):
        reg = SessionFileRegistry()
        fr1 = _file_ref(path="/tmp/first.parquet")
        fr2 = _file_ref(path="/tmp/second.parquet")
        reg.register("a", "t1", fr1)
        reg.register("b", "t2", fr2)
        assert reg.get_latest().path == "/tmp/second.parquet"

    def test_get_latest_empty(self):
        reg = SessionFileRegistry()
        assert reg.get_latest() is None

    def test_to_prompt_text_empty(self):
        reg = SessionFileRegistry()
        assert reg.to_prompt_text() == "当前会话无暂存文件。"

    def test_to_prompt_text_with_files(self):
        reg = SessionFileRegistry()
        reg.register("warehouse", "stock", _file_ref())
        txt = reg.to_prompt_text()
        assert "warehouse" in txt
        assert "product_code" in txt
        assert "test.parquet" in txt

    # ── 序列化 round-trip ──

    def test_snapshot_round_trip(self):
        reg = SessionFileRegistry()
        fr = _file_ref()
        reg.register("warehouse", "stock", fr)
        snap = reg.to_snapshot()
        reg2 = SessionFileRegistry.from_snapshot(snap)
        assert len(reg2.list_all()) == 1
        ref2 = reg2.get_latest()
        assert ref2.row_count == fr.row_count
        assert ref2.columns[0].name == "product_code"
        assert ref2.columns[0].label == "商品编码"
        assert ref2.created_at == fr.created_at

    def test_snapshot_preserves_key(self):
        reg = SessionFileRegistry()
        reg.register("purchase", "order", _file_ref())
        snap = reg.to_snapshot()
        assert snap[0]["key"].startswith("purchase:order:")

    def test_from_snapshot_legacy_relative_path(self):
        """v7 兼容：历史数据中 path 是相对路径，反序列化不崩溃"""
        snap = [{
            "key": "trade:local_data:1713600000",
            "file_ref": {
                "path": "staging/conv123/local_order_1713600000.parquet",
                "filename": "local_order_1713600000.parquet",
                "format": "parquet",
                "row_count": 50,
                "size_bytes": 4096,
                "columns": [{"name": "order_no", "dtype": "text", "label": ""}],
                "preview": "",
                "created_at": 1713600000.0,
            },
        }]
        reg = SessionFileRegistry.from_snapshot(snap)
        ref = reg.get_latest()
        assert ref is not None
        assert ref.filename == "local_order_1713600000.parquet"
        # 相对路径 is_valid() 返回 False（文件不存在），不崩溃
        assert not ref.is_valid()
        # sandbox_ref 仍然正确（只用 filename）
        assert ref.sandbox_ref == "STAGING_DIR + '/local_order_1713600000.parquet'"

    def test_from_snapshot_empty_data(self):
        """空数据反序列化返回空 Registry"""
        reg = SessionFileRegistry.from_snapshot([])
        assert reg.list_all() == []
        reg2 = SessionFileRegistry.from_snapshot(None)
        assert reg2.list_all() == []

    def test_from_snapshot_empty_list(self):
        """老格式兼容：空列表 → 空 Registry"""
        reg = SessionFileRegistry.from_snapshot([])
        assert len(reg.list_all()) == 0

    def test_from_snapshot_none(self):
        """老格式兼容：None → 空 Registry"""
        reg = SessionFileRegistry.from_snapshot(None)
        assert len(reg.list_all()) == 0
