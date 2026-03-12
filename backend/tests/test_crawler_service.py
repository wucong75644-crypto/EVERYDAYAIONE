"""
CrawlerService + crawler_tools 单元测试

覆盖：CrawlerService 所有方法、CrawlItem/CrawlResult 数据模型、
      错误类层次、crawler_tools 工具定义与验证、_safe_int 辅助函数
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.crawler.models import (
    PLATFORM_NAMES,
    SUPPORTED_PLATFORMS,
    CrawlItem,
    CrawlResult,
)
from services.crawler.errors import (
    CrawlerError,
    CrawlerLoginRequiredError,
    CrawlerNotInstalledError,
    CrawlerProcessError,
    CrawlerTimeoutError,
)
from services.crawler.service import CrawlerService, _safe_int


# ============================================================
# Helpers
# ============================================================


def _make_service(**overrides) -> CrawlerService:
    """创建 CrawlerService 实例（mock Settings）"""
    defaults = {
        "crawler_dir": "/tmp/fake_crawler",
        "crawler_timeout": 60,
        "crawler_max_notes": 20,
        "crawler_headless": True,
        "crawler_login_type": "cookie",
        "crawler_cookies_xhs": "web_session=abc",
        "crawler_cookies_dy": None,
        "crawler_cookies_ks": None,
        "crawler_cookies_bili": None,
        "crawler_cookies_wb": None,
        "crawler_cookies_tieba": None,
        "crawler_cookies_zhihu": None,
    }
    defaults.update(overrides)
    mock_settings = MagicMock(**defaults)
    with patch("services.crawler.service.get_settings", return_value=mock_settings):
        return CrawlerService()


def _sample_item(**overrides) -> CrawlItem:
    defaults = {
        "platform": "xhs",
        "title": "测试标题",
        "content": "测试内容",
        "author": "测试作者",
        "liked_count": 100,
    }
    defaults.update(overrides)
    return CrawlItem(**defaults)


# ============================================================
# TestSafeInt
# ============================================================


class TestSafeInt:

    def test_normal_int(self):
        assert _safe_int(42) == 42

    def test_string_int(self):
        assert _safe_int("123") == 123

    def test_none(self):
        assert _safe_int(None) == 0

    def test_invalid_string(self):
        assert _safe_int("abc") == 0

    def test_empty_string(self):
        assert _safe_int("") == 0

    def test_float_string(self):
        assert _safe_int("3.14") == 0


# ============================================================
# TestModels
# ============================================================


class TestModels:

    def test_supported_platforms_count(self):
        assert len(SUPPORTED_PLATFORMS) == 7

    def test_platform_names_match(self):
        for code in SUPPORTED_PLATFORMS:
            assert code in PLATFORM_NAMES

    def test_crawl_item_defaults(self):
        item = CrawlItem(platform="xhs", title="标题")
        assert item.content == ""
        assert item.author == ""
        assert item.liked_count == 0
        assert item.images == []
        assert item.tags == []

    def test_crawl_result_defaults(self):
        result = CrawlResult(platform="xhs")
        assert result.items == []
        assert result.total_found == 0
        assert result.error is None

    def test_crawl_result_with_error(self):
        result = CrawlResult(platform="dy", error="超时")
        assert result.error == "超时"


# ============================================================
# TestErrors
# ============================================================


class TestErrors:

    def test_hierarchy(self):
        assert issubclass(CrawlerNotInstalledError, CrawlerError)
        assert issubclass(CrawlerTimeoutError, CrawlerError)
        assert issubclass(CrawlerLoginRequiredError, CrawlerError)
        assert issubclass(CrawlerProcessError, CrawlerError)

    def test_timeout_error_message(self):
        e = CrawlerTimeoutError(120, "xhs")
        assert "120" in str(e)

    def test_login_required_message(self):
        e = CrawlerLoginRequiredError("小红书")
        assert "小红书" in str(e)

    def test_process_error_attrs(self):
        e = CrawlerProcessError(1, "error output")
        assert e.returncode == 1
        assert e.stderr == "error output"


# ============================================================
# TestCrawlerServiceAvailability
# ============================================================


class TestCrawlerServiceAvailability:

    def test_not_available_missing_python(self):
        service = _make_service(crawler_dir="/nonexistent")
        assert service.is_available() is False

    @patch("pathlib.Path.exists")
    def test_available_when_files_exist(self, mock_exists):
        mock_exists.return_value = True
        service = _make_service()
        assert service.is_available() is True


# ============================================================
# TestGetCookieForPlatform
# ============================================================


class TestGetCookieForPlatform:

    def test_xhs_cookie_exists(self):
        mock_settings = MagicMock(crawler_cookies_xhs="session=abc")
        service = _make_service(crawler_cookies_xhs="session=abc")
        with patch("services.crawler.service.get_settings", return_value=mock_settings):
            assert service.get_cookie_for_platform("xhs") == "session=abc"

    def test_unknown_platform(self):
        mock_settings = MagicMock()
        service = _make_service()
        with patch("services.crawler.service.get_settings", return_value=mock_settings):
            assert service.get_cookie_for_platform("unknown") == ""

    def test_none_cookie_returns_empty(self):
        mock_settings = MagicMock(crawler_cookies_dy=None)
        service = _make_service(crawler_cookies_dy=None)
        with patch("services.crawler.service.get_settings", return_value=mock_settings):
            assert service.get_cookie_for_platform("dy") == ""


# ============================================================
# TestBuildCLIArgs
# ============================================================


class TestBuildCLIArgs:

    def test_cookie_mode_with_cookie(self):
        mock_settings = MagicMock(
            crawler_cookies_xhs="session=xyz",
            crawler_cookies_dy=None, crawler_cookies_ks=None,
            crawler_cookies_bili=None, crawler_cookies_wb=None,
            crawler_cookies_tieba=None, crawler_cookies_zhihu=None,
        )
        service = _make_service(
            crawler_login_type="cookie",
            crawler_cookies_xhs="session=xyz",
        )
        with patch("services.crawler.service.get_settings", return_value=mock_settings):
            args = service._build_cli_args(
                platform="xhs",
                keywords=["防晒霜"],
                max_notes=10,
                crawl_type="search",
                output_dir="/tmp/out",
            )
        assert "--lt" in args
        lt_idx = args.index("--lt")
        assert args[lt_idx + 1] == "cookie"
        assert "--cookies" in args
        cookie_idx = args.index("--cookies")
        assert args[cookie_idx + 1] == "session=xyz"

    def test_qrcode_mode(self):
        service = _make_service(crawler_login_type="qrcode")
        args = service._build_cli_args(
            platform="dy",
            keywords=["美食"],
            max_notes=5,
            crawl_type="search",
            output_dir="/tmp/out",
        )
        lt_idx = args.index("--lt")
        assert args[lt_idx + 1] == "qrcode"
        assert "--cookies" not in args

    def test_cookie_mode_without_cookie(self):
        mock_settings = MagicMock(
            crawler_cookies_xhs=None,
            crawler_cookies_dy=None, crawler_cookies_ks=None,
            crawler_cookies_bili=None, crawler_cookies_wb=None,
            crawler_cookies_tieba=None, crawler_cookies_zhihu=None,
        )
        service = _make_service(
            crawler_login_type="cookie",
            crawler_cookies_xhs=None,
        )
        with patch("services.crawler.service.get_settings", return_value=mock_settings):
            args = service._build_cli_args(
                platform="xhs",
                keywords=["测试"],
                max_notes=5,
                crawl_type="search",
                output_dir="/tmp/out",
            )
        lt_idx = args.index("--lt")
        assert args[lt_idx + 1] == "cookie"
        cookie_idx = args.index("--cookies")
        assert args[cookie_idx + 1] == ""

    def test_basic_args_present(self):
        service = _make_service()
        args = service._build_cli_args(
            platform="bili",
            keywords=["游戏", "评测"],
            max_notes=15,
            crawl_type="search",
            output_dir="/tmp/out",
        )
        assert "--platform" in args
        assert "bili" in args
        assert "--type" in args
        assert "search" in args
        assert "--keywords" in args
        assert "游戏,评测" in args
        assert "--save_data_option" in args
        assert "json" in args


# ============================================================
# TestNormalizeItem
# ============================================================


class TestNormalizeItem:

    def test_normal_item(self):
        service = _make_service()
        raw = {
            "title": "好物推荐",
            "desc": "这是一个很好的产品",
            "nickname": "用户A",
            "liked_count": 500,
            "comment_count": "30",
            "note_url": "https://xhs.com/note/123",
            "image_list": "https://img1.jpg,https://img2.jpg",
            "tag_list": "美妆,护肤",
            "time": "2026-03-01",
            "source_keyword": "防晒霜",
        }
        item = service._normalize_item(raw, "xhs")
        assert item is not None
        assert item.title == "好物推荐"
        assert item.content == "这是一个很好的产品"
        assert item.author == "用户A"
        assert item.liked_count == 500
        assert item.comment_count == 30
        assert item.url == "https://xhs.com/note/123"
        assert len(item.images) == 2
        assert len(item.tags) == 2

    def test_missing_title_returns_none(self):
        service = _make_service()
        raw = {"desc": ""}
        assert service._normalize_item(raw, "xhs") is None

    def test_title_truncated(self):
        service = _make_service()
        raw = {"title": "A" * 300}
        item = service._normalize_item(raw, "xhs")
        assert len(item.title) == 255

    def test_content_truncated(self):
        service = _make_service()
        raw = {"title": "标题", "desc": "B" * 3000}
        item = service._normalize_item(raw, "xhs")
        assert len(item.content) == 2000

    def test_empty_image_list(self):
        service = _make_service()
        raw = {"title": "标题", "image_list": ""}
        item = service._normalize_item(raw, "xhs")
        assert item.images == []

    def test_images_capped_at_5(self):
        service = _make_service()
        raw = {
            "title": "标题",
            "image_list": ",".join(f"https://img{i}.jpg" for i in range(10)),
        }
        item = service._normalize_item(raw, "xhs")
        assert len(item.images) == 5

    def test_tags_capped_at_10(self):
        service = _make_service()
        raw = {
            "title": "标题",
            "tag_list": ",".join(f"tag{i}" for i in range(15)),
        }
        item = service._normalize_item(raw, "xhs")
        assert len(item.tags) == 10

    def test_fallback_to_desc_for_title(self):
        service = _make_service()
        raw = {"title": "", "desc": "描述文本"}
        item = service._normalize_item(raw, "xhs")
        assert item is not None
        assert item.title == "描述文本"


# ============================================================
# TestParseResults
# ============================================================


class TestParseResults:

    def test_parse_valid_json(self, tmp_path):
        service = _make_service()
        json_dir = tmp_path / "xhs" / "json"
        json_dir.mkdir(parents=True)
        data = [
            {"title": "笔记1", "desc": "内容1", "nickname": "A"},
            {"title": "笔记2", "desc": "内容2", "nickname": "B"},
        ]
        (json_dir / "search_contents_2026.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8",
        )
        items = service._parse_results(str(tmp_path), "xhs")
        assert len(items) == 2
        assert items[0].title == "笔记1"

    def test_parse_missing_dir(self, tmp_path):
        service = _make_service()
        items = service._parse_results(str(tmp_path), "xhs")
        assert items == []

    def test_parse_corrupted_json(self, tmp_path):
        service = _make_service()
        json_dir = tmp_path / "xhs" / "json"
        json_dir.mkdir(parents=True)
        (json_dir / "search_contents_2026.json").write_text(
            "not valid json!!!", encoding="utf-8",
        )
        items = service._parse_results(str(tmp_path), "xhs")
        assert items == []

    def test_parse_single_item_not_list(self, tmp_path):
        service = _make_service()
        json_dir = tmp_path / "dy" / "json"
        json_dir.mkdir(parents=True)
        data = {"title": "单条", "desc": "内容"}
        (json_dir / "search_contents_2026.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8",
        )
        items = service._parse_results(str(tmp_path), "dy")
        assert len(items) == 1


# ============================================================
# TestFormatForBrain
# ============================================================


class TestFormatForBrain:

    def test_empty_items(self):
        service = _make_service()
        assert service.format_for_brain([]) == "未找到相关内容"

    def test_basic_formatting(self):
        service = _make_service()
        items = [
            _sample_item(title="防晒霜推荐", author="美妆达人", liked_count=500),
        ]
        result = service.format_for_brain(items)
        assert "小红书" in result
        assert "防晒霜推荐" in result
        assert "美妆达人" in result
        assert "赞500" in result

    def test_max_chars_truncation(self):
        service = _make_service()
        items = [_sample_item(title=f"标题{i}", content="A" * 200) for i in range(20)]
        result = service.format_for_brain(items, max_chars=500)
        assert "未显示" in result
        assert len(result) <= 600  # 允许少量超出（最后一条）

    def test_content_preview_truncated(self):
        service = _make_service()
        items = [_sample_item(content="C" * 300)]
        result = service.format_for_brain(items)
        assert "..." in result

    def test_no_stats_if_zero(self):
        service = _make_service()
        items = [_sample_item(liked_count=0, collected_count=0, comment_count=0)]
        result = service.format_for_brain(items)
        assert "赞" not in result
        assert "藏" not in result

    def test_url_included(self):
        service = _make_service()
        items = [_sample_item(url="https://example.com/note/1")]
        result = service.format_for_brain(items)
        assert "https://example.com/note/1" in result


# ============================================================
# TestExecute — 完整流程（mock subprocess）
# ============================================================


class TestExecute:

    @pytest.mark.asyncio
    async def test_not_installed(self):
        service = _make_service()
        with pytest.raises(CrawlerNotInstalledError):
            await service.execute(platform="xhs", keywords=["test"])

    @pytest.mark.asyncio
    async def test_unsupported_platform(self):
        service = _make_service()
        with patch.object(service, "is_available", return_value=True):
            result = await service.execute(platform="taobao", keywords=["test"])
        assert result.error is not None
        assert "不支持" in result.error

    @pytest.mark.asyncio
    async def test_timeout_returns_error_result(self):
        service = _make_service()
        with (
            patch.object(service, "is_available", return_value=True),
            patch.object(
                service, "_run_subprocess",
                side_effect=CrawlerTimeoutError(60, "xhs"),
            ),
        ):
            result = await service.execute(platform="xhs", keywords=["test"])
        assert result.error is not None
        assert "超时" in result.error

    @pytest.mark.asyncio
    async def test_login_required_returns_error_result(self):
        service = _make_service()
        with (
            patch.object(service, "is_available", return_value=True),
            patch.object(
                service, "_run_subprocess",
                side_effect=CrawlerLoginRequiredError("小红书"),
            ),
        ):
            result = await service.execute(platform="xhs", keywords=["test"])
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_successful_execute(self, tmp_path):
        service = _make_service()
        with (
            patch.object(service, "is_available", return_value=True),
            patch.object(
                service, "_run_subprocess",
                new_callable=AsyncMock, return_value=("", ""),
            ),
            patch("tempfile.mkdtemp", return_value=str(tmp_path)),
            patch("shutil.rmtree"),
        ):
            # 写入模拟 JSON 结果
            json_dir = tmp_path / "xhs" / "json"
            json_dir.mkdir(parents=True)
            data = [{"title": "结果1", "desc": "内容"}]
            (json_dir / "search_contents_2026.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8",
            )
            result = await service.execute(
                platform="xhs", keywords=["测试"], max_notes=5,
            )

        assert result.error is None
        assert result.total_found == 1
        assert result.items[0].title == "结果1"

    @pytest.mark.asyncio
    async def test_max_notes_capped(self):
        """max_notes 被 _max_notes 上限限制"""
        service = _make_service(crawler_max_notes=10)
        with (
            patch.object(service, "is_available", return_value=True),
            patch.object(
                service, "_run_subprocess",
                new_callable=AsyncMock, return_value=("", ""),
            ),
            patch("tempfile.mkdtemp", return_value="/tmp/test"),
            patch("shutil.rmtree"),
            patch.object(service, "_parse_results", return_value=[]),
        ):
            result = await service.execute(
                platform="xhs", keywords=["test"], max_notes=100,
            )
        assert result.total_found == 0


# ============================================================
# TestCrawlerTools — 工具定义验证
# ============================================================


class TestCrawlerTools:

    def test_tool_set(self):
        from config.crawler_tools import CRAWLER_INFO_TOOLS
        assert "social_crawler" in CRAWLER_INFO_TOOLS

    def test_tool_schema_required_fields(self):
        from config.crawler_tools import CRAWLER_TOOL_SCHEMAS
        schema = CRAWLER_TOOL_SCHEMAS["social_crawler"]
        assert "platform" in schema["required"]
        assert "keywords" in schema["required"]

    def test_build_crawler_tools_structure(self):
        from config.crawler_tools import build_crawler_tools
        tools = build_crawler_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool["type"] == "function"
        func = tool["function"]
        assert func["name"] == "social_crawler"
        params = func["parameters"]["properties"]
        assert "platform" in params
        assert "keywords" in params
        platforms = params["platform"]["enum"]
        assert set(platforms) == SUPPORTED_PLATFORMS

    def test_routing_prompt_not_empty(self):
        from config.crawler_tools import CRAWLER_ROUTING_PROMPT
        assert "social_crawler" in CRAWLER_ROUTING_PROMPT
        assert len(CRAWLER_ROUTING_PROMPT) > 50

    def test_validate_tool_call_valid(self):
        from config.agent_tools import validate_tool_call
        assert validate_tool_call(
            "social_crawler", {"platform": "xhs", "keywords": "测试"},
        ) is True

    def test_validate_tool_call_missing_required(self):
        from config.agent_tools import validate_tool_call
        assert validate_tool_call(
            "social_crawler", {"platform": "xhs"},
        ) is False

    def test_social_crawler_in_info_tools(self):
        from config.agent_tools import INFO_TOOLS
        assert "social_crawler" in INFO_TOOLS

    def test_social_crawler_in_all_tools(self):
        from config.agent_tools import ALL_TOOLS
        assert "social_crawler" in ALL_TOOLS
