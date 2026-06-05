"""strip_attachments_xml 专项测试

覆盖：
- 正确剥离系统生成的 <attachments> 块（双特征 count + hint）
- 不误剥用户字面输入的 <attachments> 字符串
- 多块连续 / 嵌套 / 多行
- list 形式 content 每个 text part 都剥
- extract_text_from_content 末尾接入后的端到端表现

设计文档：docs/document/TECH_messages数组结构净化.md
"""

from services.handlers.chat_context.content_extractors import (
    extract_text_from_content,
    strip_attachments_xml,
)


# ============ 单元测试 strip_attachments_xml ============


class TestStripBasic:
    """基础剥离能力"""

    def test_empty_string(self):
        assert strip_attachments_xml("") == ""

    def test_none_safe(self):
        assert strip_attachments_xml(None) is None

    def test_no_attachments_returns_as_is(self):
        assert strip_attachments_xml("hello world") == "hello world"

    def test_single_attachment_stripped(self):
        text = (
            '分析下\n\n<attachments count="1" hint="status 字段是行动指引">\n'
            "  <file>\n"
            "    <name>账单.xlsx</name>\n"
            "    <status>未分析</status>\n"
            "  </file>\n"
            "</attachments>"
        )
        assert strip_attachments_xml(text) == "分析下"

    def test_trailing_whitespace_cleaned(self):
        text = '请帮忙\n\n<attachments count="1" hint="x">x</attachments>\n\n  '
        assert strip_attachments_xml(text) == "请帮忙"

    def test_only_attachments_returns_empty(self):
        text = '<attachments count="1" hint="x"><file></file></attachments>'
        assert strip_attachments_xml(text) == ""


class TestStripMultiple:
    """多块场景"""

    def test_two_attachments_consecutive(self):
        text = (
            'foo\n\n<attachments count="1" hint="a">A</attachments>\n\n'
            '<attachments count="2" hint="b">B</attachments>'
        )
        assert strip_attachments_xml(text) == "foo"

    def test_three_files_inside_one_attachment(self):
        """单 attachments 块含多个 file 子元素，整体剥离"""
        text = (
            '分析这三个\n\n<attachments count="3" hint="x">\n'
            "  <file><name>a.xlsx</name></file>\n"
            "  <file><name>b.csv</name></file>\n"
            "  <file><name>c.pdf</name></file>\n"
            "</attachments>"
        )
        assert strip_attachments_xml(text) == "分析这三个"


class TestStripNotMisapplied:
    """不误剥（系统生成特征验证）"""

    def test_user_typed_attachments_without_count(self):
        """用户字面输入 <attachments> 但缺 count/hint 特征 — 保留"""
        text = "看这段 <attachments>foo</attachments> 怎么解析"
        assert strip_attachments_xml(text) == text

    def test_user_typed_attachments_with_only_count(self):
        """只有 count 没 hint — 不剥（缺少系统生成特征）"""
        text = '<attachments count="3">foo</attachments>'
        assert strip_attachments_xml(text) == text

    def test_user_typed_attachments_with_only_hint(self):
        """只有 hint 没 count — 不剥"""
        text = '<attachments hint="foo">bar</attachments>'
        assert strip_attachments_xml(text) == text

    def test_partial_xml_text_not_stripped(self):
        """半段文本提到 <attachments — 保留"""
        text = "这个 <attachments 标签是干什么的"
        assert strip_attachments_xml(text) == text


# ============ 端到端：extract_text_from_content 接入后 ============


class TestExtractTextStripsAttachments:
    """extract_text_from_content 末尾接入 strip_attachments_xml 后的行为"""

    def test_string_content_with_xml(self):
        content = (
            '分析下\n\n<attachments count="1" hint="x">'
            '<file><name>a.xlsx</name></file></attachments>'
        )
        assert extract_text_from_content(content) == "分析下"

    def test_list_content_each_text_part_stripped(self):
        content = [
            {
                "type": "text",
                "text": (
                    '请看\n\n<attachments count="1" hint="x">'
                    '<file><name>a.xlsx</name></file></attachments>'
                ),
            },
        ]
        assert extract_text_from_content(content) == "请看"

    def test_list_multiple_text_parts_all_stripped(self):
        content = [
            {
                "type": "text",
                "text": (
                    '第一段\n\n<attachments count="1" hint="x">'
                    '<file></file></attachments>'
                ),
            },
            {
                "type": "text",
                "text": (
                    '第二段\n\n<attachments count="1" hint="x">'
                    '<file></file></attachments>'
                ),
            },
        ]
        assert extract_text_from_content(content) == "第一段 第二段"

    def test_json_string_array_format_stripped(self):
        """DB content 是 JSON 字符串 → 解析为 list → 内部 text part 也剥"""
        import json as _json
        content = _json.dumps([
            {
                "type": "text",
                "text": (
                    '聚合下\n\n<attachments count="1" hint="x">'
                    '<file></file></attachments>'
                ),
            },
        ])
        assert extract_text_from_content(content) == "聚合下"

    def test_user_typed_attachments_preserved_through_extract(self):
        """端到端验证：用户字面输入不被误剥"""
        content = "看这段 <attachments>foo</attachments> 怎么解析"
        assert extract_text_from_content(content) == content
