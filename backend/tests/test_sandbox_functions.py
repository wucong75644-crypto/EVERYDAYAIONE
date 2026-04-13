"""沙盒数据源函数测试"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.sandbox.functions import (
    build_sandbox_executor,
    compute_code_hash,
    erp_query,
    erp_query_all,
)


class TestErpQuery:
    """erp_query 单页查询测试"""

    @pytest.mark.asyncio
    async def test_basic_query(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"tid": "123"}], "total": 1,
        }
        result = await erp_query(
            "erp_trade_query", "order_list",
            {"status": "WAIT_SELLER_SEND_GOODS"},
            _dispatcher=mock_dispatcher,
        )
        assert result["total"] == 1
        assert result["list"][0]["tid"] == "123"
        mock_dispatcher.execute_raw.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_dispatcher(self):
        result = await erp_query("erp_trade_query", "order_list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_params(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"list": [], "total": 0}
        result = await erp_query(
            "erp_trade_query", "order_list",
            _dispatcher=mock_dispatcher,
        )
        assert result["total"] == 0
        # 应传入空 dict
        mock_dispatcher.execute_raw.assert_called_once_with(
            "erp_trade_query", "order_list", {},
        )


class TestErpQueryAll:
    """erp_query_all 全量翻页测试"""

    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": i} for i in range(50)], "total": 50,
        }
        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        # 50 < 100 (page_size) → 只查一页
        assert len(result["list"]) == 50
        assert mock_dispatcher.execute_raw.call_count == 1

    @pytest.mark.asyncio
    async def test_multi_page(self):
        """多页翻页终止"""
        call_count = 0

        async def mock_raw(tool, action, params):
            nonlocal call_count
            call_count += 1
            page = params.get("page", 1)
            if page <= 2:
                return {"list": [{"id": i} for i in range(100)]}
            else:
                # 第3页返回30条（< page_size=100）→ 终止
                return {"list": [{"id": i} for i in range(30)]}

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert len(result["list"]) == 230  # 100 + 100 + 30
        assert result["total"] == 230

    @pytest.mark.asyncio
    async def test_max_pages_limit(self):
        """max_pages 上限"""
        async def mock_raw(tool, action, params):
            return {"list": [{"id": 1}] * 100}  # 永远满页

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            max_pages=3,
            _dispatcher=mock_dispatcher,
        )
        assert len(result["list"]) == 300  # 3 页 × 100
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_error_on_first_page(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"error": "接口错误"}
        result = await erp_query_all(
            "erp_trade_query", "order_list",
            _dispatcher=mock_dispatcher,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_error_on_later_page_returns_partial(self):
        """后续页错误时返回已拉取的数据"""
        call_count = 0

        async def mock_raw(tool, action, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"list": [{"id": i} for i in range(100)]}
            return {"error": "接口错误"}

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        # 应返回第一页的数据
        assert len(result["list"]) == 100

    @pytest.mark.asyncio
    async def test_semaphore_concurrency(self):
        """并发控制信号量"""
        semaphore = asyncio.Semaphore(2)

        async def mock_raw(tool, action, params):
            return {"list": [{"id": 1}] * 50}  # < page_size → 终止

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
            _semaphore=semaphore,
        )
        assert len(result["list"]) == 50

    @pytest.mark.asyncio
    async def test_no_dispatcher(self):
        result = await erp_query_all("erp_trade_query", "order_list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_total_from_first_page(self):
        """第一页 API 返回的 total 字段被保留为 api_total"""
        async def mock_raw(tool, action, params):
            return {"list": [{"id": 1}] * 50, "total": 8211}

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert result["api_total"] == 8211
        assert result["total"] == 50  # 实际拉取数

    @pytest.mark.asyncio
    async def test_api_total_zero(self):
        """total=0 时 api_total 应为 0，不被跳过"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [], "total": 0,
        }
        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert result["api_total"] == 0

    @pytest.mark.asyncio
    async def test_api_total_missing(self):
        """API 未返回 total 字段时，结果不包含 api_total"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}] * 50,
        }
        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert "api_total" not in result
        assert result["total"] == 50

    @pytest.mark.asyncio
    async def test_api_total_non_numeric(self):
        """total 为非数字字符串时，不设置 api_total"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}] * 50, "total": "invalid",
        }
        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert "api_total" not in result

    @pytest.mark.asyncio
    async def test_api_total_from_totalCount_field(self):
        """支持 totalCount 字段作为 fallback"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}] * 50, "totalCount": 999,
        }
        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert result["api_total"] == 999


class TestBuildSandboxExecutor:
    """build_sandbox_executor 工厂函数测试"""

    def test_creates_executor_with_functions(self):
        executor = build_sandbox_executor()
        # 沙盒只注册 read_file（upload_file 已删除，文件输出走 OUTPUT_DIR）
        assert "read_file" in executor._registered_funcs
        assert "upload_file" not in executor._registered_funcs
        # 数据获取函数已移除
        assert "erp_query" not in executor._registered_funcs
        assert "erp_query_all" not in executor._registered_funcs
        assert "web_search" not in executor._registered_funcs
        assert "search_knowledge" not in executor._registered_funcs
        assert "write_file" not in executor._registered_funcs
        assert "list_dir" not in executor._registered_funcs

    def test_custom_timeout(self):
        executor = build_sandbox_executor(timeout=60.0)
        assert executor._timeout == 60.0

    def test_custom_max_result_chars(self):
        executor = build_sandbox_executor(max_result_chars=5000)
        assert executor._max_result_chars == 5000

    @pytest.mark.asyncio
    async def test_erp_query_removed_from_sandbox(self, tmp_path):
        """erp_query 已从沙盒移除，调用应 NameError"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor()
        code = "data = await erp_query('erp_trade_query', 'shop_list')"
        result = await executor.execute(code, "测试已移除函数")
        assert "erp_query" in result  # NameError 信息中包含函数名

    @pytest.mark.asyncio
    async def test_read_file_restricted_to_staging(self, tmp_path):
        """read_file 只允许读取 staging 目录"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor()
        code = "result = await read_file('some/other/path.json')\nprint(result)"
        result = await executor.execute(code, "测试路径限制")
        assert "staging" in result  # 错误提示中包含 staging

    def test_file_write_removed(self):
        """write_file 已从沙盒移除"""
        executor = build_sandbox_executor(
            user_id="test-user", org_id="test-org",
        )
        assert "write_file" not in executor._registered_funcs

    def test_upload_file_removed(self):
        """upload_file 已删除（文件输出走 OUTPUT_DIR，ossfs 自动同步）"""
        executor = build_sandbox_executor(user_id="u1", org_id="o1")
        assert "upload_file" not in executor._registered_funcs

    def test_workspace_dir_injected_for_org_user(self, tmp_path):
        """企业用户 workspace_dir 注入正确路径"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor(
                user_id="u1", org_id="org1",
            )
        assert executor._workspace_dir is not None
        assert "org/org1/u1" in executor._workspace_dir

    def test_workspace_dir_injected_for_personal_user(self, tmp_path):
        """个人用户 workspace_dir 注入 personal/{hash} 路径"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor(user_id="u1")
        assert executor._workspace_dir is not None
        assert "personal/" in executor._workspace_dir


# ============================================================
# 沙盒函数执行测试
# ============================================================


class TestSandboxFunctions:
    """沙盒注册函数测试"""

    @pytest.mark.asyncio
    async def test_list_dir_removed_from_sandbox(self, tmp_path):
        """list_dir 已从沙盒移除"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor(user_id="sandbox-test")
        code = "result = await list_dir('.')"
        result = await executor.execute(code, "测试已移除函数")
        assert "list_dir" in result  # NameError

    @pytest.mark.asyncio
    async def test_read_file_parquet_blocked(self, tmp_path):
        """read_file 对 .parquet 后缀返回友好提示"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor()
        code = "result = await read_file('staging/test.parquet')\nprint(result)"
        result = await executor.execute(code, "测试parquet拦截")
        assert "pd.read_parquet" in result

    def test_staging_dir_injected(self, tmp_path):
        """STAGING_DIR 变量注入到沙盒 globals"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor(conversation_id="test-conv")
        g = executor._build_globals()
        assert "STAGING_DIR" in g
        assert "test-conv" in g["STAGING_DIR"]

    def test_output_dir_injected(self, tmp_path):
        """OUTPUT_DIR 指向 workspace 下的 '下载/' 文件夹"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            executor = build_sandbox_executor(conversation_id="test-conv")
        g = executor._build_globals()
        assert "OUTPUT_DIR" in g
        assert g["OUTPUT_DIR"].endswith("下载")


class TestComputeCodeHash:
    """compute_code_hash 测试"""

    def test_same_code_same_hash(self):
        code = "x = 1 + 1"
        assert compute_code_hash(code) == compute_code_hash(code)

    def test_different_code_different_hash(self):
        assert compute_code_hash("x = 1") != compute_code_hash("x = 2")

    def test_strips_whitespace(self):
        assert compute_code_hash("  x = 1  ") == compute_code_hash("x = 1")

    def test_returns_12_chars(self):
        result = compute_code_hash("test")
        assert len(result) == 12
