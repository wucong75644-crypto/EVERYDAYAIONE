"""
_register_staging_files 单元测试

验证 data_query / erp_agent 产出的 staging 文件路径
能正确注册到共享路径缓存，供后续工具引用。
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


def _make_executor(tmp_path, conv_id="test_conv"):
    """构建 ToolExecutor（mock DB + 临时 workspace）"""
    from services.tool_executor import ToolExecutor

    ws_dir = tmp_path / "org" / "test_org" / "test_user"
    ws_dir.mkdir(parents=True)
    staging_dir = ws_dir / "staging" / conv_id
    staging_dir.mkdir(parents=True)

    executor = ToolExecutor(
        db=MagicMock(), user_id="test_user",
        conversation_id=conv_id, org_id="test_org",
    )
    return executor, ws_dir, staging_dir


class TestRegisterStagingFromFileRef:
    """从 AgentResult.file_ref 注册"""

    def test_registers_from_file_ref(self, tmp_path):
        from services.agent.agent_result import AgentResult
        from services.agent.workspace_file_handles import get_file_cache

        executor, _, staging_dir = _make_executor(tmp_path)

        # 创建 staging 文件
        staging_file = staging_dir / "trade_123.parquet"
        staging_file.write_bytes(b"PAR1")

        # 构造带 file_ref 的 AgentResult
        file_ref = MagicMock()
        file_ref.path = str(staging_file)
        file_ref.filename = "trade_123.parquet"

        result = AgentResult(
            summary="test", status="success", file_ref=file_ref,
        )

        with patch.object(executor, "_get_staging_dir", return_value=str(staging_dir)):
            executor._register_staging_files(result)

        cache = get_file_cache("test_conv")
        assert cache.resolve("trade_123.parquet") == str(staging_file)

    def test_skips_nonexistent_file_ref(self, tmp_path):
        from services.agent.agent_result import AgentResult
        from services.agent.workspace_file_handles import get_file_cache

        executor, _, staging_dir = _make_executor(tmp_path, conv_id="conv_nofile")

        file_ref = MagicMock()
        file_ref.path = "/nonexistent/path.parquet"
        file_ref.filename = "path.parquet"

        result = AgentResult(
            summary="test", status="success", file_ref=file_ref,
        )

        with patch.object(executor, "_get_staging_dir", return_value=str(staging_dir)):
            executor._register_staging_files(result)

        cache = get_file_cache("conv_nofile")
        assert cache.resolve("path.parquet") is None


class TestRegisterStagingFromSummary:
    """从 summary 文本正则提取注册"""

    def test_registers_from_summary_text(self, tmp_path):
        from services.agent.agent_result import AgentResult
        from services.agent.workspace_file_handles import get_file_cache

        executor, _, staging_dir = _make_executor(tmp_path, conv_id="conv_summary")

        staging_file = staging_dir / "query_result_abc.parquet"
        staging_file.write_bytes(b"PAR1")

        result = AgentResult(
            summary=(
                "共 50 行\n"
                "[文件已存入 staging | "
                "读取: pd.read_parquet(STAGING_DIR + '/query_result_abc.parquet') | "
                "50行 | parquet | 5KB]"
            ),
            status="success",
        )

        with patch.object(executor, "_get_staging_dir", return_value=str(staging_dir)):
            executor._register_staging_files(result)

        cache = get_file_cache("conv_summary")
        resolved = cache.resolve("query_result_abc.parquet")
        assert resolved == str(staging_file)

    def test_skips_empty_result(self, tmp_path):
        """空结果不报错"""
        executor, _, _ = _make_executor(tmp_path, conv_id="conv_empty")
        executor._register_staging_files(None)  # type: ignore

    def test_skips_no_staging_match(self, tmp_path):
        from services.agent.agent_result import AgentResult
        from services.agent.workspace_file_handles import get_file_cache

        executor, _, staging_dir = _make_executor(tmp_path, conv_id="conv_nomatch")

        result = AgentResult(
            summary="普通文本，没有 staging 引用", status="success",
        )

        with patch.object(executor, "_get_staging_dir", return_value=str(staging_dir)):
            executor._register_staging_files(result)

        cache = get_file_cache("conv_nomatch")
        assert cache.count == 0
