"""企微 Markdown 适配器单元测试"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.wecom.markdown_adapter import (
    adapt_for_app,
    clean_for_stream,
    split_long_message,
)


# ── clean_for_stream ──────────────────────────────────


class TestCleanForStream:
    """stream 通道清理测试"""

    def test_empty_text(self):
        assert clean_for_stream("") == ""

    def test_plain_text_unchanged(self):
        text = "你好，今天天气不错"
        assert clean_for_stream(text) == text

    def test_standard_markdown_preserved(self):
        text = "# 标题\n\n**加粗** 和 *斜体*\n\n- 列表\n\n```python\nprint('hi')\n```"
        assert clean_for_stream(text) == text

    def test_table_preserved(self):
        text = "| 列1 | 列2 |\n|-----|-----|\n| A | B |"
        assert clean_for_stream(text) == text

    def test_mermaid_removed(self):
        text = "前文\n\n```mermaid\ngraph TD\nA-->B\n```\n\n后文"
        result = clean_for_stream(text)
        assert "mermaid" not in result
        assert "[图表请在 Web 端查看]" in result
        assert "前文" in result
        assert "后文" in result

    def test_mermaid_case_insensitive(self):
        text = "```Mermaid\nsequenceDiagram\nA->>B: Hello\n```"
        result = clean_for_stream(text)
        assert "[图表请在 Web 端查看]" in result

    def test_latex_preserved(self):
        text = "公式 $E = mc^2$ 和 $$\\int_0^1 f(x)dx$$"
        assert clean_for_stream(text) == text


# ── adapt_for_app ─────────────────────────────────────


class TestAdaptForApp:
    """自建应用适配测试"""

    def test_empty_text(self):
        text, msgtype = adapt_for_app("")
        assert text == ""
        assert msgtype == "text"

    def test_plain_text(self):
        text, msgtype = adapt_for_app("你好，今天天气不错")
        assert msgtype == "text"
        assert text == "你好，今天天气不错"

    def test_markdown_heading_detected(self):
        text, msgtype = adapt_for_app("# 标题\n\n正文内容")
        assert msgtype == "markdown_v2"

    def test_markdown_bold_detected(self):
        text, msgtype = adapt_for_app("这是 **加粗** 内容")
        assert msgtype == "markdown_v2"

    def test_code_block_detected(self):
        text, msgtype = adapt_for_app("示例：\n```python\nprint('hi')\n```")
        assert msgtype == "markdown_v2"

    def test_table_detected(self):
        text, msgtype = adapt_for_app("| A | B |\n|---|---|\n| 1 | 2 |")
        assert msgtype == "markdown_v2"

    def test_list_detected(self):
        text, msgtype = adapt_for_app("要点：\n- 第一\n- 第二")
        assert msgtype == "markdown_v2"

    def test_link_detected(self):
        text, msgtype = adapt_for_app("参考 [文档](https://example.com)")
        assert msgtype == "markdown_v2"

    def test_mermaid_removed(self):
        text, msgtype = adapt_for_app("# 标题\n\n```mermaid\ngraph TD\nA-->B\n```")
        assert "[图表请在 Web 端查看]" in text
        assert "mermaid" not in text

    def test_font_color_removed(self):
        text, msgtype = adapt_for_app(
            '# 报告\n\n<font color="warning">警告内容</font>'
        )
        assert "<font" not in text
        assert "警告内容" in text

    def test_strikethrough_removed(self):
        text, msgtype = adapt_for_app("# 标题\n\n~~删除的内容~~")
        assert "~~" not in text
        assert "删除的内容" in text

    def test_latex_preserved(self):
        text, msgtype = adapt_for_app("# 公式\n\n$E = mc^2$")
        assert "$E = mc^2$" in text


# ── split_long_message ────────────────────────────────


class TestSplitLongMessage:
    """长消息分割测试"""

    def test_short_message_no_split(self):
        text = "短消息"
        result = split_long_message(text, max_bytes=100)
        assert result == ["短消息"]

    def test_empty_string(self):
        result = split_long_message("")
        assert result == [""]

    def test_none_returns_empty(self):
        result = split_long_message(None)
        assert result == [""]

    def test_split_by_paragraph(self):
        # 构造两个段落，总字节数超限
        para1 = "A" * 50
        para2 = "B" * 50
        text = f"{para1}\n\n{para2}"
        result = split_long_message(text, max_bytes=60)
        assert len(result) == 2
        assert result[0] == para1
        assert result[1] == para2

    def test_single_long_paragraph_split_by_sentence(self):
        # 多个中文句子组成超长段落
        sentences = ["这是第一句话。", "这是第二句话。", "这是第三句话。"]
        text = "".join(sentences)
        # 每个中文字3字节，每句 7*3=21 字节
        result = split_long_message(text, max_bytes=30)
        assert len(result) >= 2

    def test_hard_split_on_very_long_text(self):
        # 单个超长无断点文本
        text = "中" * 200  # 200 * 3 = 600 字节
        result = split_long_message(text, max_bytes=100)
        assert len(result) >= 6
        # 重组应等于原文
        assert "".join(result) == text

    def test_utf8_byte_boundary(self):
        # 确保不会截断 UTF-8 字符
        text = "中" * 100  # 300 字节
        result = split_long_message(text, max_bytes=100)
        for chunk in result:
            # 每个 chunk 都应是合法 UTF-8
            chunk.encode("utf-8")
            assert len(chunk.encode("utf-8")) <= 100

    def test_preserves_markdown_blocks(self):
        """段落分割不会拆开 Markdown 块"""
        text = "# 标题\n\n段落一内容\n\n段落二内容"
        result = split_long_message(text, max_bytes=2000)
        # 不超限时应保持为一条
        assert len(result) == 1
        assert result[0] == text

    def test_default_max_bytes(self):
        """默认 2000 字节限制"""
        short = "hello"
        result = split_long_message(short)
        assert result == ["hello"]
