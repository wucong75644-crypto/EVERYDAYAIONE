"""沙盒执行器工厂测试

build_sandbox_executor 构建 SandboxExecutor 实例（子进程隔离模式）。
"""

from unittest.mock import patch

import pytest

from services.sandbox.functions import (
    build_sandbox_executor,
    compute_code_hash,
)


class TestBuildSandboxExecutor:
    """build_sandbox_executor 工厂函数测试"""

    def test_creates_executor_with_paths(self):
        """工厂函数正确设置 workspace/staging/output 路径"""
        executor = build_sandbox_executor(
            user_id="u1", org_id="org1", conversation_id="conv1",
        )
        assert executor._workspace_dir is not None
        assert executor._staging_dir is not None
        assert executor._output_dir is not None

    def test_custom_timeout(self):
        executor = build_sandbox_executor(timeout=60.0)
        assert executor._timeout == 60.0

    def test_custom_max_result_chars(self):
        executor = build_sandbox_executor(max_result_chars=5000)
        assert executor._max_result_chars == 5000

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

    def test_output_dir_under_workspace(self):
        """OUTPUT_DIR 是 workspace 下的 '下载/' 目录"""
        executor = build_sandbox_executor(
            user_id="u1", org_id="org1",
        )
        assert executor._output_dir.endswith("下载")
        assert executor._workspace_dir in executor._output_dir

    def test_staging_dir_includes_conversation_id(self):
        """STAGING_DIR 包含 conversation_id"""
        executor = build_sandbox_executor(
            user_id="u1", org_id="org1", conversation_id="conv-123",
        )
        assert "conv-123" in executor._staging_dir

    def test_upload_fn_injected(self):
        """upload_fn 被注入（用于文件自动上传）"""
        executor = build_sandbox_executor(user_id="u1", org_id="org1")
        assert executor._upload_fn is not None
        assert callable(executor._upload_fn)


class TestKernelManagerInjection:
    """kernel_manager 参数透传验证"""

    def test_kernel_manager_injected(self):
        """传入 kernel_manager 后 executor 正确接收"""
        mock_km = object()  # 任意对象
        executor = build_sandbox_executor(
            user_id="u1", org_id="org1",
            conversation_id="conv1",
            kernel_manager=mock_km,
        )
        assert executor._kernel_manager is mock_km
        assert executor._conversation_id == "conv1"

    def test_kernel_manager_default_none(self):
        """不传 kernel_manager 时默认 None"""
        executor = build_sandbox_executor(
            user_id="u1", org_id="org1",
            conversation_id="conv1",
        )
        assert executor._kernel_manager is None

    def test_conversation_id_passed_through(self):
        """conversation_id 透传到 executor"""
        executor = build_sandbox_executor(
            user_id="u1", org_id="org1",
            conversation_id="conv-abc-123",
        )
        assert executor._conversation_id == "conv-abc-123"


class TestAutoUploadSignature:
    """_auto_upload 函数签名测试（filename + size，不读文件内容）"""

    @pytest.mark.asyncio
    async def test_auto_upload_accepts_filename_and_size(self, tmp_path):
        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            mock_s.return_value.oss_cdn_domain = "cdn.test.com"
            executor = build_sandbox_executor(user_id="u1", org_id="o1")
        with patch("core.config.get_settings") as mock_s2:
            mock_s2.return_value.file_workspace_root = str(tmp_path)
            mock_s2.return_value.oss_cdn_domain = "cdn.test.com"
            result = await executor._upload_fn("report.xlsx", 1024)
        assert "report.xlsx" in result
        assert "1024" in result
        assert "[FILE]" in result
        assert "cdn.test.com" in result


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
