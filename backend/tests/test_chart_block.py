"""交互式图表（ECharts）链路测试

覆盖：
1. ChartPart schema 序列化/反序列化
2. SandboxExecutor .echart.json 检测 + JSON 读取
3. _extract_file_parts chart 占位文本
4. chat_handler chart block 构造逻辑
"""

import json
import time

import pytest

from schemas.message import ChartPart, ContentPart
from services.sandbox.executor import SandboxExecutor


# ============================================================
# ChartPart Schema
# ============================================================

class TestChartPartSchema:
    """ChartPart 序列化/反序列化"""

    def test_chart_part_basic(self):
        cp = ChartPart(
            option={"series": [{"type": "line", "data": [1, 2, 3]}]},
            title="测试图表",
            chart_type="line",
        )
        d = cp.model_dump()
        assert d["type"] == "chart"
        assert d["option"]["series"][0]["type"] == "line"
        assert d["title"] == "测试图表"
        assert d["chart_type"] == "line"

    def test_chart_part_defaults(self):
        cp = ChartPart(option={"series": []})
        assert cp.title == ""
        assert cp.chart_type == ""

    def test_chart_part_in_content_part(self):
        """ChartPart 可作为 ContentPart 联合类型的成员"""
        data = {"type": "chart", "option": {"series": []}}
        from pydantic import TypeAdapter
        adapter = TypeAdapter(ContentPart)
        parsed = adapter.validate_python(data)
        assert isinstance(parsed, ChartPart)
        assert parsed.type == "chart"

    def test_chart_part_accepts_spec_format(self):
        """ChartPart 支持 spec_format 字段(plotly/vegalite/echarts)。

        Bug: 之前 ChartPart 没有 spec_format 字段,导致 plotly/vegalite
        spec 在持久化时丢失 spec_format,前端默认走 ECharts 渲染失败。
        """
        cp = ChartPart(
            option={"data": [{"type": "bar"}]},
            title="plotly 柱状图",
            spec_format="plotly",
        )
        d = cp.model_dump()
        assert d["spec_format"] == "plotly"

    def test_chart_part_spec_format_default_echarts(self):
        """未指定 spec_format 时默认 echarts(向后兼容)"""
        cp = ChartPart(option={"series": []})
        assert cp.spec_format == "echarts"

    def test_chart_part_unknown_spec_format_is_preserved_as_fallback(self):
        cp = ChartPart(option={"future": True}, spec_format="future-engine")
        assert cp.spec_format == "unknown"

    def test_chart_part_roundtrip_with_spec_format(self):
        """ChartPart spec_format 序列化往返"""
        original = ChartPart(
            option={"data": [{"type": "bar"}], "layout": {}},
            spec_format="vegalite",
        )
        dumped = original.model_dump()
        restored = ChartPart(**dumped)
        assert restored.spec_format == "vegalite"


# ============================================================
# SandboxExecutor .echart.json 检测
# ============================================================

# ============================================================
# 沙盒 IO 统一协议:emit_chart 取代旧 _scan_chart_options + .echart.json 链路
# 旧链路 TestChartDetection 已删除,守护测试见 test_emit_protocol.py
# ============================================================


# ============================================================
# _build_block_from_payload(沙盒 IO 统一协议:emit_payloads → content block)
# ============================================================

class TestBuildBlockFromPayload:
    """chat_handler._build_block_from_payload 把 emit_payload 转 block"""

    def test_chart_payload_to_block(self):
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "chart",
            "title": "销售趋势",
            "option": {
                "title": {"text": "销售趋势"},
                "series": [{"type": "bar", "data": [1, 2, 3]}],
            },
        })
        assert block["type"] == "chart"
        assert block["title"] == "销售趋势"
        assert block["chart_type"] == "bar"

    def test_chart_payload_transmits_spec_format_plotly(self):
        """plotly emit 的 spec_format 必须透传到 block (前端按此选 PlotlyBlock)。

        Bug: _build_block_from_payload 之前丢了 spec_format 字段,
        前端拿到 undefined 默认走 ECharts → plotly spec 渲染失败。
        """
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "chart",
            "spec_format": "plotly",
            "title": "plotly 图",
            "option": {"data": [{"type": "bar"}], "layout": {}},
        })
        assert block["spec_format"] == "plotly"

    def test_chart_payload_transmits_spec_format_vegalite(self):
        """vegalite emit 的 spec_format 必须透传到 block"""
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "chart",
            "spec_format": "vegalite",
            "title": "altair 图",
            "option": {"$schema": "https://vega.github.io/schema/vega-lite/v5.json"},
        })
        assert block["spec_format"] == "vegalite"

    def test_chart_payload_no_spec_format_defaults_echarts(self):
        """未指定 spec_format (老的手动 emit_chart) 默认 echarts,向后兼容。"""
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "chart",
            "title": "ECharts 图",
            "option": {"series": [{"type": "bar", "data": [1, 2, 3]}]},
        })
        assert block.get("spec_format") == "echarts"

    def test_table_payload_to_block(self):
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "table",
            "title": "TOP10",
            "columns": ["name", "count"],
            "rows": [{"name": "A", "count": 1}],
            "truncated": False,
        })
        assert block["type"] == "table"
        assert block["columns"] == ["name", "count"]
        assert block["rows"][0]["name"] == "A"

    def test_image_payload_to_block_with_dims(self):
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "image",
            "url": "https://cdn/img.png",
            "name": "img.png",
            "width": 1024, "height": 768,
            "workspace_path": "下载/img.png",
        })
        assert block["type"] == "image"
        assert block["url"] == "https://cdn/img.png"
        assert block["width"] == 1024
        assert block["height"] == 768
        assert block["workspace_path"] == "下载/img.png"

    def test_failed_image_payload_includes_retry(self):
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "image",
            "url": None,
            "failed": True,
            "error": "timeout",
            "retry_context": {"prompt": "test"},
        })
        assert block["failed"] is True
        assert block["error"] == "timeout"
        assert block["retry_context"] == {"prompt": "test"}

    def test_file_payload_to_block_keeps_dual_path(self):
        """双轨字段:url(CDN) + workspace_path(本地相对路径) 都保留"""
        from services.handlers.emit_payloads import build_block_from_payload
        block = build_block_from_payload({
            "kind": "file",
            "url": "https://cdn/x.xlsx",
            "name": "x.xlsx",
            "mime_type": "application/vnd",
            "size": 12345,
            "workspace_path": "下载/x.xlsx",
        })
        assert block["type"] == "file"
        assert block["url"] == "https://cdn/x.xlsx"
        assert block["workspace_path"] == "下载/x.xlsx"

    def test_unknown_kind_returns_none(self):
        from services.handlers.emit_payloads import build_block_from_payload
        assert build_block_from_payload({"kind": "unknown"}) is None


# ============================================================
# chart → result_parts 持久化（_content_blocks dict → ChartPart）
# ============================================================

class TestChartPersistence:
    """_content_blocks 中 chart dict → ChartPart 对象"""

    def test_chart_dict_to_chart_part(self):
        """chart block dict 正确转为 ChartPart"""
        block = {
            "type": "chart",
            "option": {"series": [{"type": "line", "data": [10, 20]}]},
            "title": "趋势图",
            "chart_type": "line",
        }
        cp = ChartPart(
            option=block["option"],
            title=block.get("title", ""),
            chart_type=block.get("chart_type", ""),
        )
        assert cp.type == "chart"
        assert cp.title == "趋势图"
        assert cp.chart_type == "line"
        assert cp.option["series"][0]["data"] == [10, 20]

    def test_chart_part_serialization_roundtrip(self):
        """ChartPart → model_dump → 重新构建 → 字段一致"""
        original = ChartPart(
            option={"title": {"text": "测试"}, "series": [{"type": "bar"}]},
            title="测试",
            chart_type="bar",
        )
        dumped = original.model_dump()
        restored = ChartPart(**dumped)
        assert restored.type == original.type
        assert restored.title == original.title
        assert restored.option == original.option

    def test_chart_part_missing_optional_fields(self):
        """title 和 chart_type 缺失时使用默认值"""
        block = {"type": "chart", "option": {"series": []}}
        cp = ChartPart(
            option=block["option"],
            title=block.get("title", ""),
            chart_type=block.get("chart_type", ""),
        )
        assert cp.title == ""
        assert cp.chart_type == ""

    def test_chart_part_constructed_from_block_keeps_spec_format(self):
        """block dict 含 spec_format 时,构造 ChartPart 必须保留。

        Bug: chat_handler.py 1014-1020 result_parts.append(ChartPart(...))
        之前未传 spec_format,持久化时丢失。
        """
        block = {
            "type": "chart",
            "option": {"data": [{"type": "bar"}]},
            "title": "plotly 图",
            "chart_type": "bar",
            "spec_format": "plotly",
        }
        cp = ChartPart(
            option=block["option"],
            title=block.get("title", ""),
            chart_type=block.get("chart_type", ""),
            spec_format=block.get("spec_format", "echarts"),
        )
        assert cp.spec_format == "plotly"
