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


# ============================================================
# SandboxExecutor .echart.json 检测
# ============================================================

class TestChartDetection:
    """沙盒 auto_upload 中 .echart.json 检测"""

    @pytest.mark.asyncio
    async def test_echart_json_detected(self, tmp_path):
        """正常 .echart.json → _chart_options 填充"""
        option = {"title": {"text": "销售趋势"}, "series": [{"type": "line", "data": [1, 2]}]}
        (tmp_path / "trend.echart.json").write_text(
            json.dumps(option, ensure_ascii=False), encoding="utf-8",
        )

        uploaded = []

        async def mock_upload(filename, size):
            uploaded.append(filename)
            return f"[FILE]https://cdn/f/{filename}|{filename}|application/json|{size}[/FILE]"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()

        assert "trend.echart.json" in uploaded
        assert hasattr(executor, "_chart_options")
        assert "trend.echart.json" in executor._chart_options
        assert executor._chart_options["trend.echart.json"]["title"]["text"] == "销售趋势"

    @pytest.mark.asyncio
    async def test_regular_json_not_chart(self, tmp_path):
        """普通 .json 文件不触发 chart 检测"""
        (tmp_path / "data.json").write_text('{"key": "value"}', encoding="utf-8")

        async def mock_upload(filename, size):
            return f"[FILE]https://cdn/{filename}|{filename}|application/json|{size}[/FILE]"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()

        assert not hasattr(executor, "_chart_options") or "data.json" not in getattr(executor, "_chart_options", {})

    @pytest.mark.asyncio
    async def test_oversized_echart_json_skipped(self, tmp_path):
        """超过 500KB 的 .echart.json 不加入 _chart_options（降级为 file block）"""
        big_data = {"series": [{"data": list(range(100000))}]}
        content = json.dumps(big_data)
        assert len(content) > 512_000  # 确认超限

        (tmp_path / "big.echart.json").write_text(content, encoding="utf-8")

        async def mock_upload(filename, size):
            return f"[FILE]https://cdn/{filename}|{filename}|application/json|{size}[/FILE]"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()

        chart_opts = getattr(executor, "_chart_options", {})
        assert "big.echart.json" not in chart_opts

    @pytest.mark.asyncio
    async def test_invalid_echart_json_skipped(self, tmp_path):
        """无效 JSON 的 .echart.json 不加入 _chart_options"""
        (tmp_path / "bad.echart.json").write_text("{invalid json}", encoding="utf-8")

        async def mock_upload(filename, size):
            return f"[FILE]https://cdn/{filename}|{filename}|application/json|{size}[/FILE]"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()

        chart_opts = getattr(executor, "_chart_options", {})
        assert "bad.echart.json" not in chart_opts

    @pytest.mark.asyncio
    async def test_image_and_chart_coexist(self, tmp_path):
        """同时有 PNG 和 .echart.json 时各自独立检测"""
        # PNG 文件（1x1 白色像素）
        import struct
        png_header = (
            b'\x89PNG\r\n\x1a\n'  # PNG signature
            + b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
            + b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'
            + b'\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        (tmp_path / "chart.png").write_bytes(png_header)
        (tmp_path / "trend.echart.json").write_text(
            json.dumps({"series": [{"type": "bar"}]}), encoding="utf-8",
        )

        uploaded = []

        async def mock_upload(filename, size):
            uploaded.append(filename)
            return f"[FILE]https://cdn/{filename}|{filename}|image/png|{size}[/FILE]"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()

        assert "chart.png" in uploaded
        assert "trend.echart.json" in uploaded
        # PNG → _image_dims
        assert hasattr(executor, "_image_dims")
        assert "chart.png" in executor._image_dims
        # JSON → _chart_options
        assert hasattr(executor, "_chart_options")
        assert "trend.echart.json" in executor._chart_options


# ============================================================
# _extract_file_parts chart 占位文本
# ============================================================

class TestExtractFilePartsChart:
    """_extract_file_parts 对 .echart.json 的占位文本"""

    def test_echart_placeholder_text(self):
        """chart 文件应使用交互式图表占位文本"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin._pending_file_parts = []

        result = mixin._extract_file_parts(
            "[FILE]https://cdn/f/trend.echart.json|trend.echart.json|application/json|1234[/FILE]"
        )
        assert "交互式图表已生成" in result
        assert len(mixin._pending_file_parts) == 1
        assert mixin._pending_file_parts[0].name == "trend.echart.json"

    def test_image_placeholder_unchanged(self):
        """图片文件占位文本不受影响"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin._pending_file_parts = []

        result = mixin._extract_file_parts(
            "[FILE]https://cdn/f/chart.png|chart.png|image/png|5678[/FILE]"
        )
        assert "图表已生成" in result
        assert "交互式" not in result


# ============================================================
# AgentResult [FILE] 提取（chat_tool_mixin AgentResult 分支）
# ============================================================

class TestAgentResultFileExtraction:
    """AgentResult.summary 中的 [FILE] 标记应被提取为 FilePart"""

    def test_agent_result_file_extracted(self):
        """AgentResult.summary 含 [FILE] → FilePart 暂存 + summary 替换为占位文本"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin._pending_file_parts = []

        original = (
            "计算完成\n"
            "[FILE]https://cdn/f/trend.echart.json|trend.echart.json|application/json|2048[/FILE]"
        )
        result = mixin._extract_file_parts(original)

        assert "交互式图表已生成" in result
        assert "[FILE]" not in result
        assert len(mixin._pending_file_parts) == 1
        assert mixin._pending_file_parts[0].name == "trend.echart.json"
        assert mixin._pending_file_parts[0].size == 2048

    def test_agent_result_multiple_files(self):
        """多个 [FILE] 标记全部提取"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin._pending_file_parts = []

        original = (
            "[FILE]https://cdn/a.echart.json|a.echart.json|application/json|100[/FILE]\n"
            "中间文字\n"
            "[FILE]https://cdn/b.xlsx|b.xlsx|application/vnd.ms-excel|5000[/FILE]"
        )
        result = mixin._extract_file_parts(original)

        assert len(mixin._pending_file_parts) == 2
        assert mixin._pending_file_parts[0].name == "a.echart.json"
        assert mixin._pending_file_parts[1].name == "b.xlsx"
        # chart 用交互式占位，Excel 用文件占位
        assert "交互式图表已生成" in result
        assert "文件已生成: b.xlsx" in result

    def test_no_file_marker_unchanged(self):
        """无 [FILE] 标记的 summary 原样返回"""
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin._pending_file_parts = []

        original = "计算完成，结果如下"
        result = mixin._extract_file_parts(original)

        assert result == original
        assert len(mixin._pending_file_parts) == 0


# ============================================================
# chart block 构造逻辑（chat_handler _pending_file_parts → block）
# ============================================================

class TestChartBlockConstruction:
    """chat_handler 中 _pending_file_parts + _chart_options → chart block"""

    def test_chart_block_from_file_part(self):
        """FilePart + _chart_options 匹配 → 构造 type=chart block"""
        from schemas.message import FilePart

        fp = FilePart(
            url="https://cdn/f/trend.echart.json",
            name="trend.echart.json",
            mime_type="application/json",
            size=1024,
        )
        chart_options = {
            "trend.echart.json": {
                "title": {"text": "销售趋势"},
                "series": [{"type": "bar", "data": [1, 2, 3]}],
            }
        }

        # 模拟 chat_handler 的 block 构造逻辑
        _charts = chart_options
        if fp.name in _charts:
            _opt = _charts[fp.name]
            _ct = ""
            if isinstance(_opt.get("series"), list) and _opt["series"]:
                _ct = _opt["series"][0].get("type", "")
            block = {
                "type": "chart",
                "option": _opt,
                "title": _opt.get("title", {}).get("text", ""),
                "chart_type": _ct,
            }

        assert block["type"] == "chart"
        assert block["title"] == "销售趋势"
        assert block["chart_type"] == "bar"
        assert block["option"]["series"][0]["data"] == [1, 2, 3]

    def test_non_chart_json_stays_file_block(self):
        """普通 JSON 文件（不在 _chart_options 中）→ file block"""
        from schemas.message import FilePart

        fp = FilePart(
            url="https://cdn/f/data.json",
            name="data.json",
            mime_type="application/json",
            size=500,
        )
        _charts = {}  # 空，没有 chart option

        # 模拟 chat_handler 逻辑
        if fp.name in _charts:
            block = {"type": "chart"}
        elif fp.mime_type.startswith("image/"):
            block = {"type": "image"}
        else:
            block = {"type": "file", "url": fp.url, "name": fp.name}

        assert block["type"] == "file"
        assert block["name"] == "data.json"

    def test_chart_type_extracted_from_series(self):
        """chart_type 从 series[0].type 提取"""
        option = {"series": [{"type": "pie", "data": []}]}
        _ct = ""
        if isinstance(option.get("series"), list) and option["series"]:
            _ct = option["series"][0].get("type", "")
        assert _ct == "pie"

    def test_empty_series_no_crash(self):
        """series 为空时 chart_type 为空字符串"""
        option = {"series": []}
        _ct = ""
        if isinstance(option.get("series"), list) and option["series"]:
            _ct = option["series"][0].get("type", "")
        assert _ct == ""


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
