"""
tool_result_envelope 单元测试

覆盖：wrap / wrap_for_erp_agent
      阈值分流（staging 落盘 + 摘要生成）
      防重入 / 边界值 / 并发隔离

设计文档：docs/document/TECH_工具结果分流架构.md
"""

import asyncio

import pytest

from services.agent.tool_result_envelope import (
    wrap,
    wrap_for_erp_agent,
    set_staging_dir,
    clear_staging_dir,
    MAIN_AGENT_BUDGET,
    ERP_AGENT_BUDGET,
    ERP_AGENT_RESULT_BUDGET,
    STAGED_MARKER,
)


@pytest.fixture(autouse=True)
def _setup_staging(tmp_path):
    """每个测试自动设置 staging_dir 到临时目录"""
    staging = str(tmp_path / "staging" / "test-conv")
    set_staging_dir(staging)
    yield staging
    clear_staging_dir()


# ============================================================
# wrap() 基础行为
# ============================================================

class TestWrapBasic:

    def test_none_returns_none(self):
        assert wrap("tool", None) is None

    def test_empty_returns_empty(self):
        assert wrap("tool", "") == ""

    def test_short_result_unchanged(self):
        result = "库存100件"
        assert wrap("local_stock_query", result) == result

    def test_exact_budget_unchanged(self):
        result = "x" * MAIN_AGENT_BUDGET
        assert wrap("some_tool", result) == result

    def test_over_budget_staged_with_summary(self):
        """超阈值时落盘 staging + 返回 persisted-output 格式"""
        result = "标题行\n" + "x" * (MAIN_AGENT_BUDGET + 500)
        wrapped = wrap("some_tool", result)
        assert len(wrapped) < len(result)
        assert "<persisted-output>" in wrapped
        assert "</persisted-output>" in wrapped
        assert "STAGING_DIR" in wrapped
        assert "Preview" in wrapped

    def test_no_truncate_tools_pass_through(self):
        result = "x" * 50000
        assert wrap("generate_image", result) == result
        assert wrap("generate_video", result) == result
        # code_execute 已移出 _NO_TRUNCATE，走 30K 预算

    def test_code_execute_within_budget_pass_through(self):
        """code_execute ≤30K: 不截断，直接回传"""
        from services.agent.tool_result_envelope import CODE_EXECUTE_BUDGET
        result = "x" * (CODE_EXECUTE_BUDGET - 100)
        wrapped = wrap_for_erp_agent("code_execute", result)
        assert wrapped == result

    def test_code_execute_over_budget_persisted(self, _setup_staging):
        """code_execute >30K: 落盘 staging + 预览"""
        from services.agent.tool_result_envelope import CODE_EXECUTE_BUDGET
        result = "行1: " + "x" * 200 + "\n" + "x" * (CODE_EXECUTE_BUDGET + 5000)
        wrapped = wrap_for_erp_agent("code_execute", result)
        assert STAGED_MARKER in wrapped
        assert "行1:" in wrapped  # 结构化预览包含首行
        assert "结果概览" in wrapped

    def test_double_wrap_skipped(self):
        """已分流的结果不再二次处理"""
        first = wrap("some_tool", "标题\n" + "x" * 5000)
        assert STAGED_MARKER in first
        second = wrap("erp_agent", first)
        assert second.count(STAGED_MARKER) == 1
        assert second == first


# ============================================================
# staging 落盘验证
# ============================================================

class TestStagingPersist:

    def test_staging_file_created(self, _setup_staging):
        """超阈值时 staging 文件被创建"""
        from pathlib import Path
        result = "标题行\n" + "数据" * 2000
        wrapped = wrap("local_stock_query", result)
        # 检查文件存在
        staging_dir = Path(_setup_staging)
        files = list(staging_dir.glob("tool_result_*.txt"))
        assert len(files) == 1
        # 文件内容是完整原始数据
        assert files[0].read_text(encoding="utf-8") == result

    def test_staging_file_content_matches(self, _setup_staging):
        """staging 文件内容与原始结果完全一致"""
        from pathlib import Path
        result = "商品列表\n" + "\n".join(f"商品{i}: 数据" * 10 for i in range(200))
        wrap("local_product_stats", result)
        staging_dir = Path(_setup_staging)
        files = list(staging_dir.glob("tool_result_*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == result

    def test_same_result_reuses_file(self, _setup_staging):
        """相同结果（hash相同）复用同一文件"""
        from pathlib import Path
        result = "标题\n" + "x" * 5000
        wrap("tool_a", result)
        wrap("tool_a", result)
        staging_dir = Path(_setup_staging)
        files = list(staging_dir.glob("tool_result_tool_a_*.txt"))
        assert len(files) == 1  # 同 hash 只有一个文件

    def test_different_results_different_files(self, _setup_staging):
        """不同结果产生不同文件"""
        from pathlib import Path
        wrap("tool_a", "标题\n" + "x" * 5000)
        wrap("tool_a", "标题\n" + "y" * 5000)
        staging_dir = Path(_setup_staging)
        files = list(staging_dir.glob("tool_result_tool_a_*.txt"))
        assert len(files) == 2

    def test_staging_dir_not_set_raises(self):
        """staging_dir 未设置时抛 RuntimeError"""
        clear_staging_dir()
        with pytest.raises(RuntimeError, match="staging_dir 未设置"):
            wrap("some_tool", "x" * 5000)


# ============================================================
# 摘要生成验证
# ============================================================

class TestBuildSummary:

    def test_persisted_output_format(self):
        """超阈值结果使用 <persisted-output> 标签格式"""
        result = "共 50 个店铺\n" + "\n".join(f"店铺{i}: " + "x" * 50 for i in range(100))
        wrapped = wrap("local_shop_list", result)
        assert "<persisted-output>" in wrapped
        assert "</persisted-output>" in wrapped
        assert "Output too large" in wrapped
        assert "STAGING_DIR" in wrapped

    def test_preview_preserves_first_line(self):
        """preview 包含首行"""
        result = "订单查询结果(共50单)\n" + "\n".join(f"行{i}: " + "x" * 30 for i in range(200))
        wrapped = wrap_for_erp_agent("local_order_query", result)
        assert "订单查询结果(共50单)" in wrapped

    def test_preview_has_data(self):
        """preview 包含前几行数据"""
        lines = ["标题"] + [f"数据行{i}: 内容内容" for i in range(200)]
        result = "\n".join(lines)
        wrapped = wrap("some_tool", result)
        assert "数据行0: 内容内容" in wrapped
        assert "Preview" in wrapped


# ============================================================
# 三种预算
# ============================================================

class TestBudgetLevels:

    def test_main_agent_budget(self):
        result = "标题\n" + "x" * (MAIN_AGENT_BUDGET + 100)
        wrapped = wrap("some_tool", result)
        assert STAGED_MARKER in wrapped

    def test_erp_agent_internal_budget(self):
        result = "标题\n" + "x" * (ERP_AGENT_BUDGET + 100)
        wrapped = wrap_for_erp_agent("local_stock_query", result)
        assert STAGED_MARKER in wrapped

    def test_erp_agent_budget_larger_than_main(self):
        assert ERP_AGENT_BUDGET > MAIN_AGENT_BUDGET

    def test_erp_agent_result_budget_largest(self):
        assert ERP_AGENT_RESULT_BUDGET >= ERP_AGENT_BUDGET


# ============================================================
# file_* 免截断 + code_execute 预算分流
# ============================================================

class TestNoTruncate:

    def test_code_execute_small_no_truncate(self):
        """code_execute 小结果（<30K）不截断"""
        lines = [f"line{i}: " + "x" * 290 for i in range(10)]
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("code_execute", result)
        assert wrapped == result

    def test_file_read_no_truncate(self):
        result = "文件: data.csv | 共 50000 行\n" + "\n".join(
            f"  {i}\tcol_{i}_data_value" for i in range(200)
        )
        assert len(result) > MAIN_AGENT_BUDGET
        assert wrap("file_read", result) == result

    def test_file_list_no_truncate(self):
        result = "目录: . | 共 50 项\n" + "\n".join(
            f"  [文件] report_{i}.xlsx\t5.0MB" for i in range(50)
        )
        assert wrap("file_list", result) == result

    def test_file_info_no_truncate(self):
        result = "路径: big_report.xlsx\n类型: 文件\n大小: 56.2MB"
        assert wrap("file_info", result) == result


# ============================================================
# persist_and_get_key + get_persisted（保留兼容）
# ============================================================

class TestPersistedBasicFlow:

    def setup_method(self):
        from services.agent.tool_result_envelope import clear_persisted
        clear_persisted()

    def test_persist_and_retrieve(self):
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted,
        )
        key = persist_and_get_key("local_stock_query", "完整库存数据" * 100)
        assert key.startswith("local_stock_query_")
        assert get_persisted(key) == "完整库存数据" * 100

    def test_get_nonexistent_returns_none(self):
        from services.agent.tool_result_envelope import get_persisted
        assert get_persisted("nonexistent_key") is None

    def test_clear_removes_all(self):
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted, clear_persisted,
        )
        key = persist_and_get_key("tool", "data")
        clear_persisted()
        assert get_persisted(key) is None


# ============================================================
# staging_dir ContextVar 管理
# ============================================================

class TestStagingDirContextVar:

    def test_set_and_get(self, tmp_path):
        from services.agent.tool_result_envelope import get_staging_dir
        path = str(tmp_path / "staging" / "conv123")
        set_staging_dir(path)
        assert get_staging_dir() == path

    def test_clear(self):
        from services.agent.tool_result_envelope import get_staging_dir
        clear_staging_dir()
        assert get_staging_dir() is None


# ============================================================
# 非 ERP 工具分流
# ============================================================

class TestNonErpToolStaging:

    def test_web_search_staged(self):
        items = [f"- 搜索结果{i}: 详细描述" + "x" * 200 for i in range(20)]
        result = "\n".join(items)
        wrapped = wrap("web_search", result)
        assert STAGED_MARKER in wrapped
        assert "STAGING_DIR" in wrapped

    def test_social_crawler_staged(self):
        items = [f"• 帖子{i}: 内容" + "x" * 200 for i in range(20)]
        result = "\n".join(items)
        wrapped = wrap("social_crawler", result)
        assert STAGED_MARKER in wrapped

    def test_short_search_unchanged(self):
        result = "- 结果1: xxx\n- 结果2: yyy"
        assert wrap("web_search", result) == result


# ============================================================
# 并发隔离（contextvars）
# ============================================================

class TestPersistedConcurrentIsolation:

    def setup_method(self):
        from services.agent.tool_result_envelope import clear_persisted
        clear_persisted()

    @pytest.mark.asyncio
    async def test_concurrent_tasks_isolated(self):
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted, clear_persisted,
        )

        results_a = []
        results_b = []

        async def task_a():
            key = persist_and_get_key("tool_a", "data_from_request_A")
            await asyncio.sleep(0.01)
            val = get_persisted(key)
            results_a.append(val)
            clear_persisted()

        async def task_b():
            key = persist_and_get_key("tool_b", "data_from_request_B")
            clear_persisted()
            results_b.append(get_persisted(key))

        await asyncio.gather(
            asyncio.create_task(task_a()),
            asyncio.create_task(task_b()),
        )

        assert results_a[0] == "data_from_request_A"
        assert results_b[0] is None

    @pytest.mark.asyncio
    async def test_clear_only_affects_own_context(self):
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted, clear_persisted,
        )

        seen_by_writer = []

        async def writer():
            key = persist_and_get_key("tool", "important_data")
            await asyncio.sleep(0.05)
            seen_by_writer.append(get_persisted(key))
            clear_persisted()

        async def cleaner():
            await asyncio.sleep(0.01)
            clear_persisted()

        await asyncio.gather(
            asyncio.create_task(writer()),
            asyncio.create_task(cleaner()),
        )

        assert seen_by_writer[0] == "important_data"


# ============================================================
# _persist_to_staging 路径与 FileExecutor 对齐
# ============================================================

class TestStagingPathAlignment:
    """验证 staging 落盘文件能被沙盒 read_file（FileExecutor）正确读取"""

    def test_staged_file_resolvable_by_file_executor(self, tmp_path):
        """分流写入的文件能被 FileExecutor 和 scoped open 正确读取"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir
        from services.file_executor import FileExecutor

        org_id, user_id, conv_id = "org1", "user1", "conv-test"

        # 设置 staging_dir 并触发分流
        staging = resolve_staging_dir(str(tmp_path), user_id, org_id, conv_id)
        set_staging_dir(staging)

        result = "标题行\n" + "数据行\n" * 500
        wrapped = wrap("local_shop_list", result)

        # 从 wrapped 中提取 filename
        import re
        match = re.search(r'STAGING_DIR \+ "/([^"]+)"', wrapped)
        assert match, f"摘要中未找到 STAGING_DIR 文件名: {wrapped[:200]}"
        filename = match.group(1)

        # staging 文件存在且内容正确
        staged_file = Path(staging) / filename
        assert staged_file.exists()
        assert staged_file.read_text(encoding="utf-8") == result

        # FileExecutor 也能通过相对路径解析到
        fe = FileExecutor(
            workspace_root=str(tmp_path),
            user_id=user_id,
            org_id=org_id,
        )
        rel_path = f"staging/{conv_id}/{filename}"
        resolved = fe.resolve_safe_path(rel_path)
        assert resolved.exists()
        assert resolved.read_text(encoding="utf-8") == result

    def test_staged_file_in_user_workspace(self, tmp_path):
        """staging 文件在用户 workspace 目录下（用户隔离）"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir, resolve_workspace_dir

        staging = resolve_staging_dir(str(tmp_path), "u1", "org1", "conv1")
        set_staging_dir(staging)

        result = "标题\n" + "x" * 5000
        wrap("some_tool", result)

        ws_dir = resolve_workspace_dir(str(tmp_path), "u1", "org1")
        # staging 文件必须在用户 workspace 下
        staged_files = list(Path(staging).glob("*.txt"))
        assert len(staged_files) == 1
        assert str(staged_files[0]).startswith(ws_dir)


# ============================================================
# _async_cleanup_staging（原 _delayed_cleanup_staging，已重构为文件级 TTL 清理）
# ============================================================

class TestAsyncCleanupStaging:
    """chat_handler._async_cleanup_staging 文件级清理测试"""

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_files(self, tmp_path):
        """清理后超 TTL 的文件被删除"""
        import os
        import time
        from pathlib import Path
        from core.workspace import resolve_staging_dir
        from services.handlers.chat_handler import _async_cleanup_staging
        from unittest.mock import patch, MagicMock

        staging = Path(resolve_staging_dir(
            str(tmp_path), "u1", "org1", "conv-cleanup",
        ))
        staging.mkdir(parents=True)
        old_file = staging / "data.txt"
        old_file.write_text("test data")
        # 设置 mtime 为 2 天前
        old_time = time.time() - 2 * 86400
        os.utime(old_file, (old_time, old_time))

        mock_settings = MagicMock()
        mock_settings.file_workspace_root = str(tmp_path)
        mock_settings.staging_file_ttl_seconds = 86400
        mock_settings.staging_max_size_mb = 500

        with patch("core.config.get_settings", return_value=mock_settings):
            await _async_cleanup_staging("conv-cleanup", "u1", "org1")

        assert not old_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_dir(self, tmp_path):
        """staging 目录不存在时不报错"""
        from services.handlers.chat_handler import _async_cleanup_staging
        from unittest.mock import patch, MagicMock

        mock_settings = MagicMock()
        mock_settings.file_workspace_root = str(tmp_path)
        mock_settings.staging_file_ttl_seconds = 86400
        mock_settings.staging_max_size_mb = 500

        with patch("core.config.get_settings", return_value=mock_settings):
            # 不应抛异常
            await _async_cleanup_staging("nonexistent-conv", "u1", "org1")


# ============================================================
# _scoped_open — workspace-scoped open 安全测试
# ============================================================

class TestScopedOpen:
    """验证沙盒内 open() 的路径解析和安全边界"""

    def _build_executor(self, tmp_path):
        """构建绑定 workspace 的 SandboxExecutor"""
        from pathlib import Path
        from core.workspace import resolve_workspace_dir, resolve_staging_dir

        ws_dir = resolve_workspace_dir(str(tmp_path), "u1", "org1")
        staging = resolve_staging_dir(str(tmp_path), "u1", "org1", "conv1")
        output = str(Path(ws_dir) / "下载")

        from services.sandbox.executor import SandboxExecutor
        return SandboxExecutor(
            timeout=5.0,
            workspace_dir=ws_dir,
            staging_dir=staging,
            output_dir=output,
        )

    @pytest.mark.asyncio
    async def test_relative_path_resolves_to_workspace(self, tmp_path):
        """open('staging/conv1/file.txt') 自动解析到 workspace 下"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir

        executor = self._build_executor(tmp_path)
        staging = Path(resolve_staging_dir(str(tmp_path), "u1", "org1", "conv1"))
        staging.mkdir(parents=True)
        (staging / "data.txt").write_text("hello world")

        result = await executor.execute(
            'result = open("staging/conv1/data.txt").read()\nprint(result)',
            description="test relative open",
        )
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_staging_dir_absolute_path(self, tmp_path):
        """open(STAGING_DIR + '/file.txt') 使用绝对路径正常读取"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir

        executor = self._build_executor(tmp_path)
        staging = Path(resolve_staging_dir(str(tmp_path), "u1", "org1", "conv1"))
        staging.mkdir(parents=True)
        (staging / "data.txt").write_text("absolute works")

        result = await executor.execute(
            'result = open(STAGING_DIR + "/data.txt").read()\nprint(result)',
            description="test STAGING_DIR open",
        )
        assert "absolute works" in result

    @pytest.mark.asyncio
    async def test_absolute_path_outside_workspace_blocked(self, tmp_path):
        """open('/etc/hostname') 绝对路径越界被拒绝"""
        executor = self._build_executor(tmp_path)
        result = await executor.execute(
            'open("/etc/hostname").read()',
            description="test absolute path block",
        )
        assert "文件访问被拒绝" in result or "PermissionError" in result

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_path):
        """open('/etc/passwd') 绝对路径穿越被拒绝（逃出所有白名单目录）"""
        executor = self._build_executor(tmp_path)
        result = await executor.execute(
            'open("/etc/passwd").read()',
            description="test path traversal block",
        )
        assert "文件访问被拒绝" in result or "不在允许的目录内" in result or "PermissionError" in result

    @pytest.mark.asyncio
    async def test_write_in_output_dir(self, tmp_path):
        """open(OUTPUT_DIR + '/test.txt', 'w') 在输出目录写文件允许"""
        executor = self._build_executor(tmp_path)
        result = await executor.execute(
            'f = open(OUTPUT_DIR + "/test.txt", "w")\nf.write("written")\nf.close()\nprint("ok")',
            description="test write in OUTPUT_DIR",
        )
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_path_read_text_with_staging_dir(self, tmp_path):
        """Path(STAGING_DIR + '/file').read_text() 绝对路径正常读取"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir

        executor = self._build_executor(tmp_path)
        staging = Path(resolve_staging_dir(str(tmp_path), "u1", "org1", "conv1"))
        staging.mkdir(parents=True)
        (staging / "data.txt").write_text("path works")

        result = await executor.execute(
            'from pathlib import Path\nresult = Path(STAGING_DIR + "/data.txt").read_text()\nprint(result)',
            description="test Path.read_text with STAGING_DIR",
        )
        assert "path works" in result


# ============================================================
# v6: tight 两档切换测试
# ============================================================


class TestTightBudgetSwitch:
    """v6: wrap_for_erp_agent 的 tight 参数两档预算切换"""

    def test_normal_budget_3000(self, tmp_path):
        """tight=False 时预算 3000"""
        from services.agent.tool_result_envelope import (
            wrap_for_erp_agent, set_staging_dir, clear_staging_dir,
        )
        set_staging_dir(str(tmp_path))
        try:
            short = "x" * 2000
            result = wrap_for_erp_agent("local_data", short, tight=False)
            assert result == short  # 2000 < 3000，不截断
        finally:
            clear_staging_dir()

    def test_tight_budget_1800(self, tmp_path):
        """tight=True 时预算 1800"""
        from services.agent.tool_result_envelope import (
            wrap_for_erp_agent, set_staging_dir, clear_staging_dir,
        )
        set_staging_dir(str(tmp_path))
        try:
            medium = "x" * 2500
            result_normal = wrap_for_erp_agent("local_data", medium, tight=False)
            result_tight = wrap_for_erp_agent("local_data", medium, tight=True)
            # 2500 < 3000 → normal 不截断
            assert result_normal == medium
            # 2500 > 1800 → tight 截断
            assert len(result_tight) < len(medium)
        finally:
            clear_staging_dir()

    def test_tight_default_false(self, tmp_path):
        """不传 tight 默认 False（向后兼容）"""
        from services.agent.tool_result_envelope import (
            wrap_for_erp_agent, set_staging_dir, clear_staging_dir,
        )
        set_staging_dir(str(tmp_path))
        try:
            short = "x" * 2000
            result = wrap_for_erp_agent("local_data", short)
            assert result == short
        finally:
            clear_staging_dir()


# ============================================================
# v7: 按工具名分派预算
# ============================================================


class TestBudgetDispatch:
    """wrap_for_erp_agent 按 tool_name 分派不同预算"""

    def test_code_execute_normal_budget_30k(self, tmp_path):
        """code_execute: tight=False → 30K 预算"""
        from services.agent.tool_result_envelope import (
            wrap_for_erp_agent, set_staging_dir, clear_staging_dir,
            CODE_EXECUTE_BUDGET,
        )
        set_staging_dir(str(tmp_path))
        try:
            # 29K：在 30K 预算内，不截断
            result = "x" * 29000
            assert wrap_for_erp_agent("code_execute", result) == result
        finally:
            clear_staging_dir()

    def test_code_execute_tight_budget_20k(self, tmp_path):
        """code_execute: tight=True → 20K 预算"""
        from services.agent.tool_result_envelope import (
            wrap_for_erp_agent, set_staging_dir, clear_staging_dir,
            PERSISTED_OUTPUT_TAG,
        )
        set_staging_dir(str(tmp_path))
        try:
            # 25K > 20K tight 预算 → 落盘
            result = "x" * 25000
            wrapped = wrap_for_erp_agent("code_execute", result, tight=True)
            assert PERSISTED_OUTPUT_TAG in wrapped
        finally:
            clear_staging_dir()

    def test_erp_agent_budget_4k(self, tmp_path):
        """erp_agent: tight=False → 4K 预算"""
        from services.agent.tool_result_envelope import (
            wrap_for_erp_agent, set_staging_dir, clear_staging_dir,
        )
        set_staging_dir(str(tmp_path))
        try:
            # 3500：在 4K 预算内，不截断
            result = "x" * 3500
            assert wrap_for_erp_agent("erp_agent", result) == result
            # 5000 > 4K → 落盘
            big = "x" * 5000
            wrapped = wrap_for_erp_agent("erp_agent", big)
            assert "<persisted-output>" in wrapped
        finally:
            clear_staging_dir()

    def test_other_tools_budget_3k(self):
        """其他工具: tight=False → 3K 预算（ERP 内部默认）"""
        from services.agent.tool_result_envelope import wrap_for_erp_agent
        # 2500 < 3K → 不截断
        result = "x" * 2500
        assert wrap_for_erp_agent("local_order_query", result) == result

    def test_is_truncated_detects_persisted_tag(self):
        """is_truncated 应检测 <persisted-output> 标签"""
        from services.agent.tool_result_envelope import PERSISTED_OUTPUT_TAG
        content = f"{PERSISTED_OUTPUT_TAG}\npreview\n</persisted-output>"
        is_truncated = PERSISTED_OUTPUT_TAG in content
        assert is_truncated is True

    def test_is_truncated_detects_sandbox_truncation(self):
        """is_truncated 应检测沙盒截断标记"""
        content = "data...\n\n⚠ 输出过长，已截断（省略 10000 字符）。"
        is_truncated = "⚠ 输出过长" in content
        assert is_truncated is True

    def test_is_truncated_false_for_normal_content(self):
        """正常内容 is_truncated 应为 False"""
        content = "形状: (308, 1404)\n列名: ['科目', '店铺A_2024-01']"
        is_truncated = (
            "<persisted-output>" in content
            or "⚠ 输出过长" in content
        )
        assert is_truncated is False
