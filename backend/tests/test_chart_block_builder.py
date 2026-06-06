"""ECharts 独立 chart block 构造器合约测试。

路径协议 v2 后 .echart.json 改写 staging 中转,不再走 FilePart 触发 chat_handler
渲染。本测试守护 build_orphan_chart_blocks:确保 _chart_options 能正确转成
type=chart 的 content block 推给前端,以及向后兼容旧链路去重。
"""
from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


from services.handlers.chart_block_builder import build_orphan_chart_blocks


class TestBuildOrphanChartBlocks:
    """build_orphan_chart_blocks 合约"""

    def test_empty_chart_options(self):
        """无 chart options 返回空列表"""
        assert build_orphan_chart_blocks({}, []) == []
        assert build_orphan_chart_blocks({}, [{"type": "text", "text": "x"}]) == []

    def test_single_chart_generates_block(self):
        """正常 ECharts option → chart block"""
        opt = {
            "title": {"text": "销售趋势"},
            "series": [{"type": "bar", "data": [1, 2, 3]}],
        }
        blocks = build_orphan_chart_blocks({"trend.echart.json": opt}, [])
        assert len(blocks) == 1
        assert blocks[0]["type"] == "chart"
        assert blocks[0]["option"] == opt
        assert blocks[0]["title"] == "销售趋势"
        assert blocks[0]["chart_type"] == "bar"

    def test_multiple_charts(self):
        """多个 chart 各自生成 block"""
        charts = {
            "a.echart.json": {
                "title": {"text": "A"},
                "series": [{"type": "line"}],
            },
            "b.echart.json": {
                "title": {"text": "B"},
                "series": [{"type": "pie"}],
            },
        }
        blocks = build_orphan_chart_blocks(charts, [])
        assert len(blocks) == 2
        titles = {b["title"] for b in blocks}
        assert titles == {"A", "B"}

    def test_skip_existing_chart_by_title(self):
        """已存在同 title chart block(旧 FilePart 链路)不重复生成"""
        existing = [
            {"type": "chart", "title": "已有图表", "option": {}},
        ]
        charts = {
            "old.echart.json": {
                "title": {"text": "已有图表"},
                "series": [{"type": "bar"}],
            },
            "new.echart.json": {
                "title": {"text": "新图表"},
                "series": [{"type": "line"}],
            },
        }
        blocks = build_orphan_chart_blocks(charts, existing)
        assert len(blocks) == 1
        assert blocks[0]["title"] == "新图表"

    def test_chart_without_title(self):
        """无 title 的 chart 也应生成 block(title='')"""
        opt = {"series": [{"type": "scatter"}]}
        blocks = build_orphan_chart_blocks({"x.echart.json": opt}, [])
        assert len(blocks) == 1
        assert blocks[0]["title"] == ""
        assert blocks[0]["chart_type"] == "scatter"

    def test_title_as_list_format(self):
        """ECharts title 也支持 list 格式(多标题图)"""
        opt = {
            "title": [{"text": "主标题", "subtext": "副"}],
            "series": [{"type": "bar"}],
        }
        blocks = build_orphan_chart_blocks({"x.echart.json": opt}, [])
        assert blocks[0]["title"] == "主标题"

    def test_no_series_chart_type_empty(self):
        """series 缺失或为空时 chart_type 为空字符串"""
        opt = {"title": {"text": "空图"}}
        blocks = build_orphan_chart_blocks({"x.echart.json": opt}, [])
        assert blocks[0]["chart_type"] == ""

    def test_non_dict_option_skipped(self):
        """非 dict 的 option(异常情况)直接跳过,不报错"""
        charts = {
            "broken.echart.json": "not a dict",  # type: ignore
            "good.echart.json": {
                "title": {"text": "OK"},
                "series": [{"type": "bar"}],
            },
        }
        blocks = build_orphan_chart_blocks(charts, [])  # type: ignore
        assert len(blocks) == 1
        assert blocks[0]["title"] == "OK"

    def test_existing_text_blocks_not_treated_as_chart(self):
        """existing_blocks 里非 chart 的 block(text/image/file)不影响 chart 去重"""
        existing = [
            {"type": "text", "text": "hello"},
            {"type": "image", "url": "x.png"},
            {"type": "file", "name": "y.txt"},
        ]
        charts = {
            "x.echart.json": {
                "title": {"text": "新图"},
                "series": [{"type": "bar"}],
            },
        }
        blocks = build_orphan_chart_blocks(charts, existing)
        assert len(blocks) == 1
        assert blocks[0]["title"] == "新图"
