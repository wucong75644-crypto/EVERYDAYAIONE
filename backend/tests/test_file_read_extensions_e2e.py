"""
file_read PDF/图片扩展 E2E 测试

端到端验证 file_read 对 PDF、图片、文本的完整处理链路：
  ToolExecutor.execute("file_read", ...) → FileExecutor.file_read()
    → _read_pdf() / _read_image() / 文本三级防线

覆盖场景：
  1. PDF 直读：全读/按页/范围/逗号/混合/大 PDF 拒绝/超大 PDF 预检/扫描件/加密/空 PDF/页码错误
  2. 图片多模态：CDN URL/base64 fallback/大图降级/各格式/FileReadResult 结构
  3. 文本文件：不受影响（回归保护）
  4. 二进制文件：非 PDF/图片仍拒绝（回归保护）
  5. 链路贯通：ToolExecutor → FileExecutor → FileReadResult → ChatHandler 类型识别
"""

import base64
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.file_read_extensions import FileReadResult


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path)


@pytest.fixture
def user_workspace(workspace):
    user_dir = Path(workspace) / "org" / "test_org" / "test_user"
    user_dir.mkdir(parents=True, exist_ok=True)
    return str(user_dir)


@pytest.fixture
def executor(workspace):
    """无隔离的 FileExecutor（直接测试）"""
    from services.file_executor import FileExecutor
    return FileExecutor(workspace_root=workspace)


@pytest.fixture
def tool_executor(workspace):
    """ToolExecutor（模拟 Agent 调用链路）"""
    from services.agent.tool_executor import ToolExecutor

    mock_settings = MagicMock()
    mock_settings.file_workspace_enabled = True
    mock_settings.file_workspace_root = workspace
    mock_settings.sandbox_enabled = True
    mock_settings.sandbox_timeout = 30.0
    mock_settings.sandbox_max_result_chars = 8000
    mock_settings.oss_cdn_domain = None  # 无 CDN，走 base64

    with patch("core.config.get_settings", return_value=mock_settings):
        te = ToolExecutor(
            db=MagicMock(),
            user_id="test_user",
            conversation_id="conv_001",
            org_id="test_org",
        )
        yield te


def _make_pdf(path: Path, pages: int, content_fn=None):
    """用 reportlab 创建测试 PDF"""
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for i in range(pages):
        text = content_fn(i) if content_fn else f"Page{i+1}Content"
        c.drawString(100, 750, text)
        c.showPage()
    c.save()


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


# ============================================================
# 1. PDF 直读
# ============================================================


class TestPdfRead:
    """PDF file_read 全场景测试"""

    @pytest.mark.asyncio
    async def test_pdf_full_read_small(self, executor, workspace):
        """≤10 页 PDF 无 pages 参数自动全读"""
        pdf = Path(workspace, "small.pdf")
        _make_pdf(pdf, 3)
        result = await executor.file_read("small.pdf")

        assert isinstance(result, str)
        assert "PDF 3 页" in result
        assert "Page1Content" in result
        assert "Page2Content" in result
        assert "Page3Content" in result
        assert "第 1 页" in result
        assert "第 3 页" in result

    @pytest.mark.asyncio
    async def test_pdf_single_page(self, executor, workspace):
        """pages='2' 只读第 2 页"""
        pdf = Path(workspace, "multi.pdf")
        _make_pdf(pdf, 5)
        result = await executor.file_read("multi.pdf", pages="2")

        assert "Page2Content" in result
        assert "Page1Content" not in result
        assert "Page3Content" not in result

    @pytest.mark.asyncio
    async def test_pdf_page_range(self, executor, workspace):
        """pages='2-4' 读第 2~4 页"""
        pdf = Path(workspace, "range.pdf")
        _make_pdf(pdf, 5)
        result = await executor.file_read("range.pdf", pages="2-4")

        assert "Page2Content" in result
        assert "Page3Content" in result
        assert "Page4Content" in result
        assert "Page1Content" not in result
        assert "Page5Content" not in result

    @pytest.mark.asyncio
    async def test_pdf_comma_pages(self, executor, workspace):
        """pages='1,3,5' 读不连续页"""
        pdf = Path(workspace, "comma.pdf")
        _make_pdf(pdf, 5)
        result = await executor.file_read("comma.pdf", pages="1,3,5")

        assert "Page1Content" in result
        assert "Page3Content" in result
        assert "Page5Content" in result
        assert "Page2Content" not in result
        assert "Page4Content" not in result

    @pytest.mark.asyncio
    async def test_pdf_mixed_pages(self, executor, workspace):
        """pages='1-2,5' 混合范围 + 单页"""
        pdf = Path(workspace, "mixed.pdf")
        _make_pdf(pdf, 5)
        result = await executor.file_read("mixed.pdf", pages="1-2,5")

        assert "Page1Content" in result
        assert "Page2Content" in result
        assert "Page5Content" in result
        assert "Page3Content" not in result

    @pytest.mark.asyncio
    async def test_pdf_large_requires_pages(self, executor, workspace):
        """>10 页 PDF 无 pages 参数拒绝"""
        pdf = Path(workspace, "large.pdf")
        _make_pdf(pdf, 15)
        result = await executor.file_read("large.pdf")

        assert "超过" in result
        assert "pages" in result

    @pytest.mark.asyncio
    async def test_pdf_large_with_pages_ok(self, executor, workspace):
        """>10 页 PDF 带 pages 参数可读"""
        pdf = Path(workspace, "large2.pdf")
        _make_pdf(pdf, 15)
        result = await executor.file_read("large2.pdf", pages="1-5")

        assert "Page1Content" in result
        assert "Page5Content" in result

    @pytest.mark.asyncio
    async def test_pdf_max_20_pages(self, executor, workspace):
        """单次读取超过 20 页拒绝"""
        pdf = Path(workspace, "huge.pdf")
        _make_pdf(pdf, 25)
        result = await executor.file_read("huge.pdf", pages="1-25")

        assert "超过单次上限" in result
        assert "20" in result

    @pytest.mark.asyncio
    async def test_pdf_size_limit(self, executor, workspace):
        """>10MB PDF 直接拒绝（不尝试解析）"""
        pdf = Path(workspace, "fat.pdf")
        pdf.write_bytes(b"%PDF-" + b"x" * (11 * 1024 * 1024))
        result = await executor.file_read("fat.pdf")

        assert "过大" in result
        assert "硬上限" in result

    @pytest.mark.asyncio
    async def test_pdf_corrupted(self, executor, workspace):
        """损坏 PDF 返回脱敏错误（不泄露路径）"""
        pdf = Path(workspace, "corrupt.pdf")
        pdf.write_bytes(b"not a pdf at all")
        result = await executor.file_read("corrupt.pdf")

        assert "无法打开" in result
        # 不应泄露服务端绝对路径
        assert str(workspace) not in result

    @pytest.mark.asyncio
    async def test_pdf_empty(self, executor, workspace):
        """空 PDF（0 页）"""
        from reportlab.pdfgen import canvas
        pdf = Path(workspace, "empty.pdf")
        c = canvas.Canvas(str(pdf))
        c.save()
        result = await executor.file_read("empty.pdf")

        # reportlab 生成的 PDF 至少有 1 页空白页
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_pdf_scanned_detection(self, executor, workspace):
        """扫描件页（无可提取文本）标记警告"""
        from reportlab.pdfgen import canvas
        pdf = Path(workspace, "scan.pdf")
        c = canvas.Canvas(str(pdf))
        # 不写文字，只画一条线模拟扫描件
        c.line(0, 0, 100, 100)
        c.showPage()
        c.drawString(100, 750, "TextPage")
        c.showPage()
        c.save()
        result = await executor.file_read("scan.pdf")

        assert "无可提取文本" in result
        assert "TextPage" in result


class TestPdfPagesParsing:
    """_parse_pages 边界场景（FileOperationError 异常）"""

    @pytest.mark.asyncio
    async def test_page_zero(self, executor, workspace):
        from services.file_executor import FileOperationError
        _make_pdf(Path(workspace, "t.pdf"), 3)
        with pytest.raises(FileOperationError, match="必须从 1"):
            await executor.file_read("t.pdf", pages="0")

    @pytest.mark.asyncio
    async def test_page_negative(self, executor, workspace):
        from services.file_executor import FileOperationError
        _make_pdf(Path(workspace, "t.pdf"), 3)
        with pytest.raises(FileOperationError):
            await executor.file_read("t.pdf", pages="-1")

    @pytest.mark.asyncio
    async def test_page_out_of_range(self, executor, workspace):
        from services.file_executor import FileOperationError
        _make_pdf(Path(workspace, "t.pdf"), 3)
        with pytest.raises(FileOperationError, match="超出范围"):
            await executor.file_read("t.pdf", pages="99")

    @pytest.mark.asyncio
    async def test_page_not_number(self, executor, workspace):
        from services.file_executor import FileOperationError
        _make_pdf(Path(workspace, "t.pdf"), 3)
        with pytest.raises(FileOperationError, match="格式错误"):
            await executor.file_read("t.pdf", pages="abc")

    @pytest.mark.asyncio
    async def test_page_reversed_range(self, executor, workspace):
        from services.file_executor import FileOperationError
        _make_pdf(Path(workspace, "t.pdf"), 5)
        with pytest.raises(FileOperationError, match="起始页不能大于结束页"):
            await executor.file_read("t.pdf", pages="5-1")

    @pytest.mark.asyncio
    async def test_page_duplicate_dedup(self, executor, workspace):
        """重复页码自动去重"""
        _make_pdf(Path(workspace, "t.pdf"), 3)
        result = await executor.file_read("t.pdf", pages="1,1,2,2")
        # 应该只出现一次第 1 页和第 2 页
        assert result.count("第 1 页") == 1
        assert result.count("第 2 页") == 1

    @pytest.mark.asyncio
    async def test_page_empty_string(self, executor, workspace):
        """空字符串 pages='' 等价于无 pages（自动全读）"""
        _make_pdf(Path(workspace, "t.pdf"), 3)
        result = await executor.file_read("t.pdf", pages="")
        # 空字符串 falsy，走 auto-read 分支
        assert "Page1Content" in result

    @pytest.mark.asyncio
    async def test_page_trailing_comma(self, executor, workspace):
        """尾部逗号不应报错"""
        _make_pdf(Path(workspace, "t.pdf"), 3)
        result = await executor.file_read("t.pdf", pages="1,2,")
        assert "Page1Content" in result
        assert "Page2Content" in result


# ============================================================
# 2. 图片多模态
# ============================================================


class TestImageRead:
    """图片 file_read 全场景测试"""

    @pytest.mark.asyncio
    async def test_image_png_returns_file_read_result(self, executor, workspace):
        """PNG 返回 FileReadResult(type='image')"""
        Path(workspace, "test.png").write_bytes(_TINY_PNG)
        result = await executor.file_read("test.png")

        assert isinstance(result, FileReadResult)
        assert result.type == "image"
        assert result.image_url  # 有 URL（CDN 或 base64）
        assert "图片" in result.text
        assert "1×1px" in result.text
        assert "模型已接收" in result.text

    @pytest.mark.asyncio
    async def test_image_jpg(self, executor, workspace):
        """JPG 扩展名也走图片链路"""
        # JFIF header
        Path(workspace, "photo.jpg").write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
        )
        result = await executor.file_read("photo.jpg")
        assert isinstance(result, FileReadResult)
        assert result.type == "image"

    @pytest.mark.asyncio
    async def test_image_jpeg(self, executor, workspace):
        """JPEG 扩展名"""
        Path(workspace, "pic.jpeg").write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
        )
        result = await executor.file_read("pic.jpeg")
        assert isinstance(result, FileReadResult)

    @pytest.mark.asyncio
    async def test_image_gif(self, executor, workspace):
        """GIF 扩展名"""
        Path(workspace, "anim.gif").write_bytes(b"GIF89a" + b"\x00" * 100)
        result = await executor.file_read("anim.gif")
        assert isinstance(result, FileReadResult)

    @pytest.mark.asyncio
    async def test_image_webp(self, executor, workspace):
        """WebP 扩展名"""
        Path(workspace, "modern.webp").write_bytes(b"RIFF" + b"\x00" * 100)
        result = await executor.file_read("modern.webp")
        assert isinstance(result, FileReadResult)

    @pytest.mark.asyncio
    async def test_image_base64_fallback_no_cdn(self, executor, workspace):
        """无 CDN 配置时小图片走 base64 data URL"""
        Path(workspace, "small.png").write_bytes(_TINY_PNG)

        with patch.object(executor, "get_cdn_url", return_value=None):
            result = await executor.file_read("small.png")

        assert isinstance(result, FileReadResult)
        assert result.type == "image"
        assert result.image_url.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_image_cdn_url_preferred(self, executor, workspace):
        """有 CDN 配置时优先用 CDN URL"""
        Path(workspace, "cdn.png").write_bytes(_TINY_PNG)

        with patch.object(executor, "get_cdn_url", return_value="https://cdn.example.com/cdn.png"):
            result = await executor.file_read("cdn.png")

        assert isinstance(result, FileReadResult)
        assert result.image_url == "https://cdn.example.com/cdn.png"
        assert not result.image_url.startswith("data:")

    @pytest.mark.asyncio
    async def test_image_large_no_cdn_degraded(self, executor, workspace):
        """大图片（>2MB）无 CDN 时降级为纯文本元信息"""
        large_img = Path(workspace, "huge.png")
        large_img.write_bytes(b"\x89PNG" + b"\x00" * (3 * 1024 * 1024))

        with patch.object(executor, "get_cdn_url", return_value=None):
            result = await executor.file_read("huge.png")

        assert isinstance(result, FileReadResult)
        assert result.type == "text"  # 降级为文本
        assert result.image_url == ""  # 无 URL
        assert "过大" in result.text or "无法直接查看" in result.text

    @pytest.mark.asyncio
    async def test_image_dimensions_in_text(self, executor, workspace):
        """图片元信息包含宽高"""
        Path(workspace, "dim.png").write_bytes(_TINY_PNG)
        result = await executor.file_read("dim.png")

        assert isinstance(result, FileReadResult)
        assert "1×1px" in result.text

    @pytest.mark.asyncio
    async def test_svg_treated_as_binary(self, executor, workspace):
        """SVG MIME 是 image/svg+xml，不在图片扩展名列表中，走二进制拒绝"""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        Path(workspace, "icon.svg").write_text(svg_content)
        result = await executor.file_read("icon.svg")

        # SVG 不在 _IMAGE_EXTENSIONS 也不在 _TEXT_EXTENSIONS，走二进制拒绝
        assert isinstance(result, str)
        assert "二进制" in result or "code_execute" in result


# ============================================================
# 3. 文本文件回归保护
# ============================================================


class TestTextFileRegression:
    """确保 PDF/图片改动不影响文本文件读取"""

    @pytest.mark.asyncio
    async def test_txt_normal(self, executor, workspace):
        Path(workspace, "readme.txt").write_text("Hello\nWorld")
        result = await executor.file_read("readme.txt")
        assert isinstance(result, str)
        assert "Hello" in result
        assert "World" in result

    @pytest.mark.asyncio
    async def test_csv_normal(self, executor, workspace):
        Path(workspace, "data.csv").write_text("name,age\nAlice,30")
        result = await executor.file_read("data.csv")
        assert isinstance(result, str)
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_json_normal(self, executor, workspace):
        Path(workspace, "config.json").write_text('{"key": "value"}')
        result = await executor.file_read("config.json")
        assert isinstance(result, str)
        assert "key" in result

    @pytest.mark.asyncio
    async def test_md_normal(self, executor, workspace):
        Path(workspace, "notes.md").write_text("# Title\n\nContent")
        result = await executor.file_read("notes.md")
        assert isinstance(result, str)
        assert "Title" in result

    @pytest.mark.asyncio
    async def test_pages_param_ignored_for_text(self, executor, workspace):
        """文本文件传 pages 参数不报错（被忽略）"""
        Path(workspace, "plain.txt").write_text("line1\nline2")
        result = await executor.file_read("plain.txt", pages="1-3")
        assert isinstance(result, str)
        assert "line1" in result


# ============================================================
# 4. 二进制文件回归保护
# ============================================================


class TestBinaryFileRegression:
    """确保非 PDF/图片二进制文件仍被拒绝"""

    @pytest.mark.asyncio
    async def test_xlsx_rejected(self, executor, workspace):
        Path(workspace, "report.xlsx").write_bytes(b"PK\x03\x04")
        result = await executor.file_read("report.xlsx")
        assert isinstance(result, str)
        assert "二进制" in result or "code_execute" in result

    @pytest.mark.asyncio
    async def test_parquet_rejected(self, executor, workspace):
        Path(workspace, "data.parquet").write_bytes(b"PAR1" + b"\x00" * 100)
        result = await executor.file_read("data.parquet")
        assert isinstance(result, str)
        assert "二进制" in result or "code_execute" in result

    @pytest.mark.asyncio
    async def test_docx_rejected(self, executor, workspace):
        Path(workspace, "doc.docx").write_bytes(b"PK\x03\x04")
        result = await executor.file_read("doc.docx")
        assert isinstance(result, str)
        assert "二进制" in result or "code_execute" in result

    @pytest.mark.asyncio
    async def test_unknown_binary_rejected(self, executor, workspace):
        Path(workspace, "data.bin").write_bytes(b"\x00\x01\x02\x03")
        result = await executor.file_read("data.bin")
        assert isinstance(result, str)
        assert "二进制" in result


# ============================================================
# 5. ToolExecutor 链路贯通
# ============================================================


class TestToolExecutorIntegration:
    """ToolExecutor.execute() → FileExecutor → FileReadResult 完整链路"""

    @pytest.mark.asyncio
    async def test_tool_executor_pdf_read(self, tool_executor, user_workspace):
        """ToolExecutor 调用 file_read 读 PDF → AgentResult"""
        from services.agent.agent_result import AgentResult
        _make_pdf(Path(user_workspace, "contract.pdf"), 2)

        result = await tool_executor.execute("file_read", {"path": "contract.pdf"})
        assert isinstance(result, AgentResult)
        assert "Page1Content" in result.summary

    @pytest.mark.asyncio
    async def test_tool_executor_pdf_with_pages(self, tool_executor, user_workspace):
        """ToolExecutor 调用 file_read 带 pages → AgentResult"""
        from services.agent.agent_result import AgentResult
        _make_pdf(Path(user_workspace, "report.pdf"), 5)

        result = await tool_executor.execute(
            "file_read", {"path": "report.pdf", "pages": "3"}
        )
        assert isinstance(result, AgentResult)
        assert "Page3Content" in result.summary
        assert "Page1Content" not in result.summary

    @pytest.mark.asyncio
    async def test_tool_executor_image_read(self, tool_executor, user_workspace):
        """ToolExecutor 调用 file_read 读图片 → FileReadResult 透传"""
        Path(user_workspace, "screenshot.png").write_bytes(_TINY_PNG)

        result = await tool_executor.execute(
            "file_read", {"path": "screenshot.png"}
        )
        assert isinstance(result, FileReadResult)
        assert result.type == "image"
        assert result.image_url

    @pytest.mark.asyncio
    async def test_tool_executor_text_read(self, tool_executor, user_workspace):
        """ToolExecutor 读文本文件返回 AgentResult"""
        from services.agent.agent_result import AgentResult
        Path(user_workspace, "notes.txt").write_text("hello world")

        result = await tool_executor.execute(
            "file_read", {"path": "notes.txt"}
        )
        assert isinstance(result, AgentResult)
        assert "hello world" in result.summary

    @pytest.mark.asyncio
    async def test_tool_executor_file_list_then_read_pdf(
        self, tool_executor, user_workspace
    ):
        """完整用户场景：file_list → 看到 PDF → file_read 读内容"""
        from services.agent.agent_result import AgentResult
        _make_pdf(Path(user_workspace, "invoice.pdf"), 2)
        Path(user_workspace, "memo.txt").write_text("meeting notes")

        # Step 1: file_list
        list_result = await tool_executor.execute("file_list", {})
        assert isinstance(list_result, AgentResult)
        assert "invoice.pdf" in list_result.summary
        assert "memo.txt" in list_result.summary

        # Step 2: file_read PDF
        pdf_result = await tool_executor.execute(
            "file_read", {"path": "invoice.pdf"}
        )
        assert "Page1Content" in pdf_result.summary

        # Step 3: file_read text（回归）
        txt_result = await tool_executor.execute(
            "file_read", {"path": "memo.txt"}
        )
        assert "meeting notes" in txt_result.summary


# ============================================================
# 6. ChatHandler FileReadResult 类型识别
# ============================================================


class TestChatHandlerTypeRecognition:
    """验证 ChatHandler 工具结果处理能正确识别 FileReadResult"""

    def test_file_read_result_is_not_str(self):
        """FileReadResult 不是 str，不会被 isinstance(result, str) 误匹配"""
        result = FileReadResult(type="image", text="test", image_url="http://x.png")
        assert not isinstance(result, str)

    def test_file_read_result_text_type(self):
        """type='text' 的 FileReadResult 应被正确处理"""
        result = FileReadResult(type="text", text="图片过大")
        assert result.type == "text"
        assert result.image_url == ""

    def test_file_read_result_image_type(self):
        """type='image' 的 FileReadResult 包含 image_url"""
        result = FileReadResult(
            type="image",
            text="图片信息",
            image_url="data:image/png;base64,abc",
        )
        assert result.type == "image"
        assert result.image_url.startswith("data:")

    def test_file_read_result_summary_slicing(self):
        """result.text[:500] 不报错（ChatHandler 用于 summary）"""
        result = FileReadResult(type="image", text="x" * 1000)
        summary = result.text[:500]
        assert len(summary) == 500

    def test_file_read_result_empty_text(self):
        """空 text 不报错"""
        result = FileReadResult(type="image", text="", image_url="http://x.png")
        summary = result.text[:100]
        assert summary == ""


# ============================================================
# 7. _parse_pages 纯单元测试（直接调静态方法）
# ============================================================


class TestParsePagesDirect:
    """直接调用 _parse_pages 静态方法，不依赖 PDF 文件"""

    def _parse(self, pages_str: str, total: int = 10):
        from services.file_read_extensions import FileReadExtensionsMixin
        return FileReadExtensionsMixin._parse_pages(pages_str, total)

    def test_single_page(self):
        assert self._parse("3") == [2]

    def test_range(self):
        assert self._parse("2-5") == [1, 2, 3, 4]

    def test_comma_list(self):
        assert self._parse("1,3,5") == [0, 2, 4]

    def test_mixed(self):
        assert self._parse("1-3,7,9-10") == [0, 1, 2, 6, 8, 9]

    def test_dedup(self):
        assert self._parse("1,1,2,1-3") == [0, 1, 2]

    def test_sorted(self):
        assert self._parse("5,1,3") == [0, 2, 4]

    def test_first_page(self):
        assert self._parse("1") == [0]

    def test_last_page(self):
        assert self._parse("10") == [9]

    def test_full_range(self):
        assert self._parse("1-10") == list(range(10))

    def test_page_zero_error(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError, match="必须从 1"):
            self._parse("0")

    def test_page_negative_error(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError):
            self._parse("-1")

    def test_page_over_total_error(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError, match="超出范围"):
            self._parse("11")

    def test_range_over_total_error(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError):
            self._parse("5-15")

    def test_reversed_range_error(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError, match="起始页不能大于结束页"):
            self._parse("5-3")

    def test_non_number_error(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError, match="格式错误"):
            self._parse("abc")

    def test_empty_string(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError, match="未指定有效页码"):
            self._parse("")

    def test_trailing_comma_ok(self):
        assert self._parse("1,2,") == [0, 1]

    def test_spaces_stripped(self):
        assert self._parse(" 1 , 3 ") == [0, 2]

    def test_range_with_spaces(self):
        assert self._parse(" 2 - 4 ") == [1, 2, 3]

    def test_single_page_total(self):
        """只有 1 页的 PDF"""
        assert self._parse("1", total=1) == [0]

    def test_single_page_out_of_range(self):
        from services.file_executor import FileOperationError
        with pytest.raises(FileOperationError, match="超出范围"):
            self._parse("2", total=1)


# ============================================================
# 8. ChatHandler 图片注入逻辑
# ============================================================


class TestChatHandlerImageInjection:
    """验证 ChatHandler 工具结果循环对 FileReadResult 的处理逻辑"""

    def test_image_result_collected_to_pending(self):
        """type='image' 的 FileReadResult，image_url 应被收集"""
        result = FileReadResult(
            type="image",
            text="图片: test.png",
            image_url="https://cdn.example.com/test.png",
        )
        # 模拟 chat_handler 中的收集逻辑
        pending = []
        if isinstance(result, FileReadResult):
            content = result.text
            if result.type == "image" and result.image_url:
                pending.append(result.image_url)

        assert content == "图片: test.png"
        assert len(pending) == 1
        assert pending[0] == "https://cdn.example.com/test.png"

    def test_text_result_not_collected(self):
        """type='text' 的 FileReadResult（降级），不应收集 image_url"""
        result = FileReadResult(
            type="text",
            text="图片过大",
            image_url="",
        )
        pending = []
        if isinstance(result, FileReadResult):
            content = result.text
            if result.type == "image" and result.image_url:
                pending.append(result.image_url)

        assert content == "图片过大"
        assert len(pending) == 0

    def test_multiple_images_collected(self):
        """多张图片全部收集"""
        results = [
            FileReadResult(type="image", text="img1", image_url="https://cdn/1.png"),
            FileReadResult(type="image", text="img2", image_url="https://cdn/2.png"),
            FileReadResult(type="text", text="pdf content"),  # 非图片
        ]
        pending = []
        for r in results:
            if isinstance(r, FileReadResult) and r.type == "image" and r.image_url:
                pending.append(r.image_url)

        assert len(pending) == 2
        assert "https://cdn/1.png" in pending
        assert "https://cdn/2.png" in pending

    def test_image_injection_message_format(self):
        """图片注入的 user 消息格式正确"""
        pending_urls = ["https://cdn/a.png", "https://cdn/b.jpg"]
        img_parts = [
            {"type": "text", "text": "[系统：以下是 file_read 返回的图片]"},
        ]
        for url in pending_urls:
            img_parts.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
        msg = {"role": "user", "content": img_parts}

        assert msg["role"] == "user"
        assert len(msg["content"]) == 3  # 1 text + 2 images
        assert msg["content"][0]["type"] == "text"
        assert "[系统" in msg["content"][0]["text"]
        assert msg["content"][1]["type"] == "image_url"
        assert msg["content"][1]["image_url"]["url"] == "https://cdn/a.png"
        assert msg["content"][2]["image_url"]["url"] == "https://cdn/b.jpg"

    def test_no_images_no_injection(self):
        """无图片时不注入"""
        pending = []
        messages = []
        if pending:
            messages.append({"role": "user", "content": "should not appear"})

        assert len(messages) == 0


# ============================================================
# 9. file_search 发现 PDF/图片后 file_read 可读
# ============================================================


class TestSearchThenRead:
    """file_search 找到文件 → file_read 读取"""

    @pytest.mark.asyncio
    async def test_search_find_pdf_then_read(self, executor, workspace):
        _make_pdf(Path(workspace, "合同.pdf"), 2)

        search = await executor.file_search("合同")
        assert "合同.pdf" in search

        read = await executor.file_read("合同.pdf")
        assert "Page1Content" in read

    @pytest.mark.asyncio
    async def test_search_find_image_then_read(self, executor, workspace):
        Path(workspace, "截图.png").write_bytes(_TINY_PNG)

        search = await executor.file_search("截图")
        assert "截图.png" in search

        read = await executor.file_read("截图.png")
        assert isinstance(read, FileReadResult)
        assert read.type == "image"
