"""
schemas/multimodal.py FileReadResult 类 sanity 测试

P1 新增的中立多模态返回类型，被 chat_handler / chat_tool_mixin /
chat_generate_mixin 跨模块识别。dataclass 字段稳定性是协议保障。
"""

from schemas.multimodal import FileReadResult


class TestFileReadResult:

    def test_default_text_type(self):
        """默认 type='text', text='', image_url=''"""
        r = FileReadResult()
        assert r.type == "text"
        assert r.text == ""
        assert r.image_url == ""

    def test_image_type_construction(self):
        """type='image' + image_url 的图片多模态结果"""
        r = FileReadResult(
            type="image",
            text="logo.png — 图片已注入视觉",
            image_url="https://cdn.example.com/logo.png",
        )
        assert r.type == "image"
        assert "logo.png" in r.text
        assert r.image_url == "https://cdn.example.com/logo.png"

    def test_text_type_with_content(self):
        """type='text' + text 的文本结果（如 PDF 提取后文本）"""
        r = FileReadResult(type="text", text="文档内容...")
        assert r.type == "text"
        assert r.text == "文档内容..."
        assert r.image_url == ""

    def test_isinstance_check(self):
        """chat_handler 用 isinstance(result, FileReadResult) 判断
        是否注入多模态。必须 isinstance 通过。"""
        r = FileReadResult(type="image", text="", image_url="https://x.png")
        assert isinstance(r, FileReadResult)

    def test_str_returns_repr_like(self):
        """dataclass 自带 __repr__，不破坏调试"""
        r = FileReadResult(type="image", image_url="https://x.png")
        s = repr(r)
        assert "FileReadResult" in s
        assert "image" in s
