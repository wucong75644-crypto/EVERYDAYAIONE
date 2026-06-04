"""file_xml_renderer 单元测试。

覆盖：
  - 全部顶级节点
  - 动态 sample 行数自适应
  - column_index 顶部映射
  - CDATA 代码示例
  - related_files 整合
  - lxml 解析有效性
  - 空 ai_decision / 空 grain 等降级
"""
from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.agent.file_meta import FileMeta
from services.agent.file_xml_renderer import (
    render_xml,
    sample_segment_sizes,
)


def _build_meta() -> FileMeta:
    """构造一个完整的 FileMeta v2 用于测试。"""
    return FileMeta(
        version="2.0",
        source_file="/mnt/test.xlsx",
        processed_at="2026-06-03T11:24:00",
        summary={"row_count": 500_000, "col_count": 23, "sheet_count": 1},
        schema={
            "序号": {"col": "A", "type": "integer", "null_ratio": 0.0,
                    "min": 1, "max": 500000, "unique_count": 500000},
            "平台订单号": {"col": "B", "type": "string", "null_ratio": 0.0,
                       "unique_count": 187234},
            "销售金额": {"col": "H", "type": "decimal", "null_ratio": 0.0,
                      "min": 0.01, "max": 9999.0},
            "店铺名称": {"col": "C", "type": "string", "null_ratio": 0.0,
                       "categories": ["快乐的小癫子", "快乐的小癫子-拼多多"]},
        },
        sample={
            "head": [
                {"_row": 3, "序号": 1, "平台订单号": "5006827369075309014", "销售金额": 16.5},
                {"_row": 4, "序号": 2, "平台订单号": "5032064954868665226", "销售金额": 26.8},
                {"_row": 5, "序号": 3, "平台订单号": "3207210025490591581", "销售金额": 20.9},
                {"_row": 6, "序号": 4, "平台订单号": "3208411098765432109", "销售金额": 59.6},
                {"_row": 7, "序号": 5, "平台订单号": "3208411098765432109", "销售金额": 59.6},
            ],
            "middle": [
                {"_row": 250001, "序号": 249999, "销售金额": 35.9},
                {"_row": 250002, "序号": 250000, "销售金额": 108.0},
                {"_row": 250003, "序号": 250001, "销售金额": 19.9},
            ],
            "tail": [
                {"_row": 499998, "序号": 499996, "销售金额": 29.9},
                {"_row": 499999, "序号": 499997, "销售金额": 75.0},
                {"_row": 500000, "序号": 499998, "销售金额": 22.5},
                {"_row": 500001, "序号": 499999, "销售金额": 45.0},
                {"_row": 500002, "序号": 500000, "销售金额": 18.5},
            ],
            "boundary": [],
        },
        stats={"missing_values": 0, "duplicates": 0},
        formulas=[],
        issues=[
            {"type": "summary_rows_marked", "action": "标记0个合计行"},
            {"type": "int_cols_fixed", "action": "整数修复3列：序号/销售数量/退货数量"},
            {"type": "column_renamed", "action": "AI 重命名 23 列"},
        ],
        merged_cells=[],
        # V3：grain 字段已删除，order_level 改由 ai_decision.column_semantics 标注
        ai_decision={
            "header_row": 2,
            "data_start_row": 3,
            "header_type": "single",
            "header_note": "Row 1 是大标题行，Row 2 才是真表头",
            "column_semantics": [
                {"letter": "A", "business_name": "序号", "semantic_type": "id"},
                {"letter": "B", "business_name": "平台订单号", "semantic_type": "id",
                 "is_id_column": True},
                {"letter": "C", "business_name": "店铺名称", "semantic_type": "name"},
                {"letter": "H", "business_name": "销售金额", "semantic_type": "amount",
                 "is_order_level": True},
            ],
            "summary_rows": [],
            "data_quality_notes": [
                {"severity": "info", "note": "Row 1 是大标题行，已自动跳过"},
                {"severity": "info", "note": "退款金额列存在负数，属正常业务退款"},
            ],
            "overall_summary": "50 万行销售订单明细数据。",
            "model_used": "qwen-turbo",
            "attempt_count": 1,
            "elapsed_ms": 1240,
            "path_type": "B",
        },
        cleaning_strategy={},
        related_files=[],
    )


# ── 工具函数 ──

class TestSampleSegmentSizes:
    def test_tiers(self):
        assert sample_segment_sizes(100) == (3, 0, 3)
        assert sample_segment_sizes(50_000) == (4, 2, 4)
        assert sample_segment_sizes(500_000) == (5, 3, 5)
        assert sample_segment_sizes(5_000_000) == (6, 6, 6)


# ── XML 渲染基础节点 ──

class TestRenderXmlBasic:
    def test_full_xml_renders(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/staging/cache.parquet",
                         original_path="/mnt/test.xlsx")
        assert xml.startswith("<file_analysis>")
        assert xml.rstrip().endswith("</file_analysis>")
        # 关键节点存在（V3：删 grain 节点）
        for tag in (
            "data_access", "file_meta", "ai_decision", "usage_hints",
            "column_schema", "sample_data", "cleaning_result",
        ):
            assert f"<{tag}" in xml, f"缺少 <{tag}> 节点"

    def test_data_access_at_top(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        # data_access 应排在 file_meta / ai_decision 之前
        assert xml.index("<data_access") < xml.index("<file_meta")
        assert xml.index("<data_access") < xml.index("<ai_decision")

    def test_data_access_has_cdata_quick_start(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert "<![CDATA[" in xml
        assert "duckdb.sql" in xml
        assert "/x.parquet" in xml

    def test_ai_decision_critical_priority(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert '<ai_decision priority="critical"' in xml
        # model + attempt 属性
        assert 'model="qwen-turbo"' in xml
        assert 'attempt="1"' in xml

    def test_summary_rows_empty_marker(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        # AI 确认无汇总行
        assert "<summary_rows/>" in xml or "<summary_rows></summary_rows>" in xml


# ── column_schema + grain ──

class TestColumnSchemaAndGrain:
    def test_columns_have_letter_name_type(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert 'letter="A"' in xml
        assert 'name="序号"' in xml
        # order_level 标签
        assert 'order_level="true"' in xml

    # V3：删 test_grain_fields，grain 章节已废弃，order_level 改由
    # ai_decision.column_semantics[i].is_order_level 标注（test_columns_have_order_level_tag 覆盖）


# ── sample_data + column_index ──

class TestSampleData:
    def test_column_index_at_top_of_sample(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        # column_index 在 sample_data 段内
        si = xml.index("<sample_data")
        ci = xml.index("<column_index>")
        ei = xml.index("</sample_data>")
        assert si < ci < ei
        # 含 letter=name 映射
        assert "A=序号" in xml
        assert "B=平台订单号" in xml

    def test_dynamic_sample_count_for_large_file(self):
        """500K 行文件 → head 5 + mid 3 + tail 5 = 13 行。"""
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        # 应有 head/middle/tail segment
        assert '<segment name="head">' in xml
        assert '<segment name="middle">' in xml
        assert '<segment name="tail">' in xml

    def test_small_file_no_middle_segment(self):
        """小文件 (≤10k) → mid=0，不应有 middle segment。"""
        meta = _build_meta()
        meta.summary["row_count"] = 100
        meta.sample["middle"] = []
        xml = render_xml(meta, parquet_path="/x.parquet")
        # head/tail 仍有；middle segment 不输出（限 0 行）
        assert '<segment name="head">' in xml
        assert '<segment name="middle">' not in xml


# ── usage_hints + code_example CDATA ──

class TestUsageHints:
    def test_critical_hint_with_order_level(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/cache.parquet")
        assert '<usage_hints priority="critical">' in xml
        # critical 警告含订单级 + DISTINCT
        assert "DISTINCT" in xml
        # CDATA 保留 SQL 代码
        assert "<![CDATA[" in xml
        # quoted by parquet path
        assert "/cache.parquet" in xml

    def test_large_file_oom_warning(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        # 50 万行触发 OOM 警告
        assert "OOM" in xml or "SELECT *" in xml

    def test_summary_rows_auto_adds_where_filter(self):
        """AI 标了 summary_rows → SQL code_example 自动加 WHERE _is_summary=false。"""
        meta = _build_meta()
        # 模拟 AI 决策含合计行
        meta.ai_decision["summary_rows"] = [500005]
        # 补全 fixture：销售数量列加进 schema（默认 fixture 不完整）
        meta.schema["销售数量"] = {
            "col": "G", "type": "integer", "null_ratio": 0.0,
            "min": 1, "max": 50,
        }
        xml = render_xml(meta, parquet_path="/cache.parquet")
        # critical 合计行提示必须出现
        assert "_is_summary" in xml
        assert "合计行" in xml
        # 订单级聚合 SQL 自动加 WHERE
        order_sql_block = xml.split('订单级聚合范式')[1].split(']]>')[0]
        assert 'WHERE "_is_summary" = false' in order_sql_block, \
            "订单级聚合 SQL 应自动带 _is_summary 过滤"
        # 明细级聚合 SQL 也加 WHERE
        line_sql_block = xml.split('明细级聚合')[1].split(']]>')[0]
        assert 'WHERE "_is_summary" = false' in line_sql_block, \
            "明细级聚合 SQL 应自动带 _is_summary 过滤"

    def test_no_summary_rows_no_where_filter(self):
        """AI 确认无合计行 → SQL code_example 不加 WHERE（避免引用不存在的列）。"""
        meta = _build_meta()
        meta.ai_decision["summary_rows"] = []  # AI 确认无
        meta.schema["销售数量"] = {
            "col": "G", "type": "integer", "null_ratio": 0.0, "min": 1, "max": 50,
        }
        xml = render_xml(meta, parquet_path="/cache.parquet")
        # SQL code_example 中不应引用 _is_summary 列（列根本不存在于 Parquet 中）
        for marker in ('订单级聚合范式', '明细级聚合'):
            assert marker in xml, f"应有 {marker} code_example"
            block = xml.split(marker)[1].split(']]>')[0]
            assert 'WHERE "_is_summary"' not in block, \
                f"AI 确认无合计行时 SQL 不应引用 _is_summary 列（{marker} block）"


# ── related_files ──

class TestRelatedFiles:
    def test_with_related(self):
        meta = _build_meta()
        related = [
            {
                "type": "join",
                "confidence": 0.85,
                "other_file": "orders.xlsx",
                "common_columns": ["商品编码", "日期"],
                "hint": "可 JOIN 列: 商品编码,日期",
            },
        ]
        xml = render_xml(meta, parquet_path="/x.parquet", related_files=related)
        assert '<related_files priority="high">' in xml
        assert 'type="join"' in xml
        assert "orders.xlsx" in xml
        assert "商品编码" in xml

    def test_no_related_no_node(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert "<related_files" not in xml


# ── cleaning_result ──

class TestCleaningResult:
    def test_ai_decided_and_code_executed_split(self):
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert "<cleaning_result" in xml
        # AI 决策的（summary_rows_marked, column_renamed）→ <ai_decided>
        assert "<ai_decided>" in xml
        # 代码执行的（int_cols_fixed）→ <code_executed>
        assert "<code_executed>" in xml


# ── XML 有效性（lxml 解析） ──

class TestXmlValidity:
    def test_lxml_parses_successfully(self):
        try:
            from lxml import etree
        except ImportError:
            pytest.skip("lxml 未安装")
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        root = etree.fromstring(xml.encode("utf-8"))
        assert root.tag == "file_analysis"

    def test_cdata_preserved(self):
        try:
            from lxml import etree
        except ImportError:
            pytest.skip("lxml 未安装")
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/cache.parquet")
        root = etree.fromstring(xml.encode("utf-8"))
        # 找到 quick_start 节点
        node = root.find(".//quick_start")
        assert node is not None
        assert "duckdb.sql" in node.text

    def test_escapes_special_chars(self):
        """文件路径含 & < > 等字符时应被转义。"""
        meta = _build_meta()
        meta.source_file = "/mnt/a&b<c>d.xlsx"
        try:
            from lxml import etree
        except ImportError:
            pytest.skip("lxml 未安装")
        xml = render_xml(meta, parquet_path="/cache.parquet")
        root = etree.fromstring(xml.encode("utf-8"))
        meta_node = root.find(".//file_meta/path")
        assert "a&b<c>d.xlsx" in meta_node.text


# ── 降级：缺字段时不崩 ──

class TestDegradeGracefully:
    def test_empty_ai_decision(self):
        """ai_decision 为空 dict 时不渲染 ai_decision 节点。"""
        meta = _build_meta()
        meta.ai_decision = {}
        xml = render_xml(meta, parquet_path="/x.parquet")
        # 其他节点仍正常
        assert "<file_meta" in xml
        assert "<column_schema" in xml
        # ai_decision 节点缺失
        assert "<ai_decision" not in xml

    def test_no_grain_section(self):
        """V3：grain 章节已删除，xml 不应再包含 <grain> 节点。"""
        meta = _build_meta()
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert "<grain" not in xml

    def test_empty_sample(self):
        meta = _build_meta()
        meta.sample = {"head": [], "middle": [], "tail": [], "boundary": []}
        xml = render_xml(meta, parquet_path="/x.parquet")
        assert "<sample_data" not in xml
