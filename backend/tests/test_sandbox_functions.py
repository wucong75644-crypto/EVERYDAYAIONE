"""沙盒执行器工厂测试"""

from unittest.mock import patch

import pytest

from services.sandbox.functions import (
    build_sandbox_executor,
    compute_code_hash,
)


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


class TestAutoUploadSignature:
    """_auto_upload 函数签名测试（filename + size，不读文件内容）"""

    @pytest.mark.asyncio
    async def test_auto_upload_accepts_filename_and_size(self, tmp_path):
        """_auto_upload 接收 (filename, size) 而不是 (content, filename)"""
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            mock_s.return_value.oss_cdn_domain = "cdn.test.com"
            executor = build_sandbox_executor(user_id="u1", org_id="o1")
        # upload_fn 签名是 (filename: str, size: int)
        # CDN 路径在 _auto_upload 内部再次调用 get_settings，需要持续 mock
        with patch("core.config.get_settings") as mock_s2:
            mock_s2.return_value.file_workspace_root = str(tmp_path)
            mock_s2.return_value.oss_cdn_domain = "cdn.test.com"
            result = await executor._upload_fn("report.xlsx", 1024)
        assert "report.xlsx" in result
        assert "1024" in result
        assert "[FILE]" in result
        assert "cdn.test.com" in result


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
