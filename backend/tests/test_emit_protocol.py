"""emit 协议合约测试(流派 2 多字段 IPC, 2026-06 起)

验证沙盒侧 emit_xxx() 通过 buffer 收集 payload(不污染 kernel JSON-Line 通道),
build_*_payload 纯函数构造 payload 结构正确。

之前的 [EMIT] marker 协议已废弃,产物走 kernel_worker IPC 独立字段
(stdout 与 emit_payloads 分离),见 docs/document/TECH_沙盒IO统一协议.md
"""
from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.sandbox.emit_protocol import (
    build_chart_payload,
    build_file_payload,
    build_image_payload,
    build_table_payload,
    install_emit_in_globals,
)


# ============================================================
# Buffer 模式 (沙盒生产路径) - 不污染 kernel JSON-Line 协议通道
# ============================================================

class TestInstallEmitInGlobals:
    """生产路径:emit_xxx 通过闭包绑定 buffer,不打 print"""

    def test_install_creates_4_functions(self):
        g: dict = {}
        buf: list = []
        install_emit_in_globals(g, buf)
        assert "emit_chart" in g
        assert "emit_file" in g
        assert "emit_image" in g
        assert "emit_table" in g
        assert callable(g["emit_chart"])

    def test_emit_chart_appends_to_buffer_not_stdout(self, tmp_path, capsys):
        """关键:emit_chart 调用后 buffer 收到 payload,但 stdout 一片干净。

        kernel 协议通道纯净是流派 2 IPC 字段分离的前提。
        """
        g: dict = {}
        buf: list = []
        install_emit_in_globals(g, buf)

        g["emit_chart"]({"series": [{"type": "bar"}]}, title="测试")

        # buffer 收到 payload
        assert len(buf) == 1
        assert buf[0]["kind"] == "chart"
        assert buf[0]["title"] == "测试"

        # 关键合约:stdout 没污染(kernel 协议通道纯净)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_emit_file_buffer(self, tmp_path):
        g: dict = {}
        buf: list = []
        install_emit_in_globals(g, buf)

        f = tmp_path / "x.csv"
        f.write_text("a,b\n1,2")
        g["emit_file"](str(f), label="测试")

        assert len(buf) == 1
        assert buf[0]["kind"] == "file"
        assert buf[0]["label"] == "测试"
        assert buf[0]["size"] > 0

    def test_multiple_emits_accumulate(self):
        g: dict = {}
        buf: list = []
        install_emit_in_globals(g, buf)

        g["emit_chart"]({"x": 1}, title="A")
        g["emit_chart"]({"x": 2}, title="B")
        g["emit_image"]("p.png", alt="图片")

        assert len(buf) == 3
        assert [p["kind"] for p in buf] == ["chart", "chart", "image"]


# ============================================================
# payload 构造函数(纯函数,无副作用)
# ============================================================

class TestBuildPayloadPureFunctions:
    """payload 构造函数(纯函数,无副作用)"""

    def test_build_chart_payload(self):
        p = build_chart_payload({"x": 1}, title="A")
        # 手动 emit_chart → spec_format 固定 echarts(plotly/vegalite 走 emit_auto_hooks)
        assert p == {
            "kind": "chart",
            "spec_format": "echarts",
            "title": "A",
            "option": {"x": 1},
        }

    def test_build_chart_payload_validates_dict(self):
        with pytest.raises(TypeError, match="必须是 dict"):
            build_chart_payload("not dict", "x")  # type: ignore

    def test_build_file_payload(self, tmp_path):
        f = tmp_path / "y.txt"
        f.write_text("hi")
        p = build_file_payload(str(f))
        assert p["kind"] == "file"
        assert p["size"] == 2

    def test_build_file_payload_missing_file_size_zero(self):
        p = build_file_payload("/nonexistent/x.csv")
        assert p["kind"] == "file"
        assert p["size"] == 0

    def test_build_file_payload_empty_path_raises(self):
        with pytest.raises(ValueError, match="path 不能为空"):
            build_file_payload("")

    def test_build_image_payload(self):
        p = build_image_payload("a/b.png", alt="image")
        assert p["kind"] == "image"
        assert p["name"] == "b.png"

    def test_build_image_payload_default_alt_basename(self):
        p = build_image_payload("x/y/abc.jpg")
        assert p["alt"] == "abc.jpg"

    def test_build_image_payload_empty_path_raises(self):
        with pytest.raises(ValueError, match="path 不能为空"):
            build_image_payload("")

    def test_build_table_payload_dataframe(self):
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        p = build_table_payload(df, title="t")
        assert p["kind"] == "table"
        assert p["columns"] == ["a", "b"]
        assert len(p["rows"]) == 2

    def test_build_table_payload_list_of_dicts(self):
        data = [{"name": "张三", "score": 100}, {"name": "李四", "score": 90}]
        p = build_table_payload(data, title="测试表")
        assert p["kind"] == "table"
        assert "name" in p["columns"]
        assert len(p["rows"]) == 2

    def test_build_table_payload_single_dict(self):
        p = build_table_payload({"k": "v", "n": 1})
        assert p["kind"] == "table"
        assert p["columns"] == ["k", "n"]
        assert len(p["rows"]) == 1

    def test_build_table_payload_truncated_at_200(self):
        data = [{"i": i} for i in range(250)]
        p = build_table_payload(data)
        assert p["truncated"] is True
        assert len(p["rows"]) == 200

    def test_build_table_payload_invalid_type_raises(self):
        with pytest.raises(TypeError, match="必须是 DataFrame"):
            build_table_payload(42)  # type: ignore
