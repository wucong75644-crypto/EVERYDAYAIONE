"""emit 协议合约测试

验证沙盒侧 emit_xxx() 产出 [EMIT]{json}[/EMIT] marker 格式正确,
主进程 tool_loop_executor 能正确解析并路由到对应链路。
"""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.sandbox.emit_protocol import (
    EMIT_MARKER_END,
    EMIT_MARKER_START,
    emit_chart,
    emit_file,
    emit_image,
    emit_table,
)


# 复用 tool_loop_executor 的正则做端到端验证
_EMIT_RE = re.compile(r"\[EMIT\](?P<payload>\{.+?\})\[/EMIT\]", re.DOTALL)


def _capture(fn, *args, **kwargs) -> dict:
    """跑 emit_xxx 捕获 stdout,返回解析后的 payload dict"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    output = buf.getvalue()
    assert EMIT_MARKER_START in output
    assert EMIT_MARKER_END in output
    m = _EMIT_RE.search(output)
    assert m, f"marker 解析失败: {output[:200]}"
    return json.loads(m.group("payload"))


# ============================================================
# emit_chart
# ============================================================

class TestEmitChart:
    def test_basic(self):
        option = {
            "title": {"text": "销售"},
            "xAxis": {"data": ["A", "B"]},
            "series": [{"type": "bar", "data": [1, 2]}],
        }
        payload = _capture(emit_chart, option, title="销售额")
        assert payload["kind"] == "chart"
        assert payload["title"] == "销售额"
        assert payload["option"] == option

    def test_no_title(self):
        payload = _capture(emit_chart, {"series": []})
        assert payload["title"] == ""

    def test_chinese_no_escape(self):
        """ensure_ascii=False:中文不应被转义"""
        payload = _capture(emit_chart, {"title": {"text": "销售额"}}, title="销售")
        assert payload["title"] == "销售"
        assert payload["option"]["title"]["text"] == "销售额"

    def test_option_must_be_dict(self):
        with pytest.raises(TypeError, match="option 必须是 dict"):
            emit_chart("not a dict", title="x")  # type: ignore


# ============================================================
# emit_file
# ============================================================

class TestEmitFile:
    def test_basic(self, tmp_path):
        f = tmp_path / "x.xlsx"
        f.write_bytes(b"fake xlsx" * 100)
        payload = _capture(emit_file, str(f), label="销售报表")
        assert payload["kind"] == "file"
        assert payload["path"] == str(f)
        assert payload["label"] == "销售报表"
        assert payload["name"] == "x.xlsx"
        assert payload["size"] == 900  # 9 字符 * 100

    def test_default_label_basename(self, tmp_path):
        f = tmp_path / "report.csv"
        f.write_text("a,b\n1,2")
        payload = _capture(emit_file, str(f))
        assert payload["label"] == "report.csv"

    def test_missing_file_size_zero(self):
        payload = _capture(emit_file, "/nonexistent/x.csv")
        assert payload["size"] == 0

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="path 不能为空"):
            emit_file("")


# ============================================================
# emit_image
# ============================================================

class TestEmitImage:
    def test_basic(self):
        payload = _capture(emit_image, "下载/chart.png", alt="销售柱形图")
        assert payload["kind"] == "image"
        assert payload["path"] == "下载/chart.png"
        assert payload["alt"] == "销售柱形图"
        assert payload["name"] == "chart.png"

    def test_default_alt_basename(self):
        payload = _capture(emit_image, "x/y/abc.jpg")
        assert payload["alt"] == "abc.jpg"


# ============================================================
# emit_table
# ============================================================

class TestEmitTable:
    def test_list_of_dicts(self):
        data = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        payload = _capture(emit_table, data, title="测试表")
        assert payload["kind"] == "table"
        assert payload["title"] == "测试表"
        assert payload["columns"] == ["a", "b"]
        assert payload["rows"] == data
        assert payload["truncated"] is False

    def test_dataframe(self):
        import pandas as pd
        df = pd.DataFrame({"店铺": ["A", "B"], "销售": [12.5, 8.7]})
        payload = _capture(emit_table, df, title="销售")
        assert payload["kind"] == "table"
        assert payload["columns"] == ["店铺", "销售"]
        assert len(payload["rows"]) == 2

    def test_single_dict(self):
        payload = _capture(emit_table, {"a": 1, "b": 2}, title="x")
        assert payload["rows"] == [{"a": 1, "b": 2}]

    def test_truncated_at_200(self):
        data = [{"x": i} for i in range(300)]
        payload = _capture(emit_table, data)
        assert len(payload["rows"]) == 200
        assert payload["truncated"] is True

    def test_invalid_type(self):
        with pytest.raises(TypeError, match="必须是 DataFrame"):
            emit_table(12345)  # type: ignore


# ============================================================
# tool_loop_executor 解析 marker (端到端合约)
# ============================================================

class TestToolLoopExtractEmits:
    """模拟 _process_emit_markers 的核心解析逻辑"""

    def test_single_marker_in_text(self):
        text = (
            "运行结果:\n"
            "[EMIT]{\"kind\":\"chart\",\"title\":\"销售\",\"option\":{}}[/EMIT]\n"
            "数据已生成"
        )
        matches = list(_EMIT_RE.finditer(text))
        assert len(matches) == 1
        payload = json.loads(matches[0].group("payload"))
        assert payload["kind"] == "chart"

    def test_multiple_markers(self):
        text = (
            "[EMIT]{\"kind\":\"chart\",\"title\":\"A\",\"option\":{}}[/EMIT]\n"
            "中间文字\n"
            "[EMIT]{\"kind\":\"file\",\"path\":\"x.xlsx\",\"label\":\"B\",\"name\":\"x.xlsx\",\"size\":100}[/EMIT]"
        )
        matches = list(_EMIT_RE.finditer(text))
        assert len(matches) == 2
        kinds = [json.loads(m.group("payload"))["kind"] for m in matches]
        assert kinds == ["chart", "file"]

    def test_marker_with_chinese(self):
        # 沙盒输出 ensure_ascii=False,正则要能匹配带中文的 marker
        text = '[EMIT]{"kind":"chart","title":"各店铺销售额","option":{"title":{"text":"销售"}}}[/EMIT]'
        m = _EMIT_RE.search(text)
        assert m
        payload = json.loads(m.group("payload"))
        assert payload["title"] == "各店铺销售额"

    def test_marker_with_newlines_in_option(self):
        """option 里如果带 \\n 转义符号 marker 也能匹配"""
        text = '[EMIT]{"kind":"chart","title":"x","option":{"text":"line1\\nline2"}}[/EMIT]'
        m = _EMIT_RE.search(text)
        assert m
        payload = json.loads(m.group("payload"))
        assert "line1" in payload["option"]["text"]

    def test_no_marker_returns_no_match(self):
        text = "完全没有 marker 的纯文本"
        assert not _EMIT_RE.search(text)
