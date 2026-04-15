"""
tool_result_envelope 单元测试

覆盖：wrap / wrap_for_erp_agent / wrap_erp_agent_result
      阈值分流（staging 落盘 + 摘要生成）
      防重入 / 边界值 / 并发隔离

设计文档：docs/document/TECH_工具结果分流架构.md
"""

import asyncio

import pytest

from services.agent.tool_result_envelope import (
    wrap,
    wrap_for_erp_agent,
    wrap_erp_agent_result,
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
        """超阈值时落盘 staging + 返回摘要"""
        result = "标题行\n" + "x" * (MAIN_AGENT_BUDGET + 500)
        wrapped = wrap("some_tool", result)
        assert len(wrapped) < len(result)
        assert STAGED_MARKER in wrapped
        assert "read_file" in wrapped
        assert "数据来源: some_tool" in wrapped

    def test_no_truncate_tools_pass_through(self):
        result = "x" * 50000
        assert wrap("generate_image", result) == result
        assert wrap("generate_video", result) == result
        assert wrap("code_execute", result) == result

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

    def test_summary_contains_metadata(self):
        """摘要包含工具名和时间戳"""
        result = "共 50 个店铺\n" + "\n".join(f"店铺{i}: " + "x" * 50 for i in range(100))
        wrapped = wrap("local_shop_list", result)
        assert "数据来源: local_shop_list" in wrapped
        assert "获取时间:" in wrapped

    def test_summary_preserves_first_line(self):
        """摘要保留首行"""
        result = "订单查询结果(共50单)\n" + "\n".join(f"行{i}" for i in range(200))
        wrapped = wrap_for_erp_agent("local_order_query", result)
        assert "订单查询结果(共50单)" in wrapped

    def test_summary_preserves_summary_lines(self):
        """摘要保留汇总行"""
        lines = (
            ["库存汇总"] +
            [f"商品{i}: 100件" for i in range(200)] +
            ["合计：20000件"]
        )
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("local_stock_query", result)
        assert "合计：20000件" in wrapped

    def test_summary_has_row_count(self):
        """摘要包含数据行数"""
        lines = ["标题"] + [f"数据行{i}: " + "x" * 50 for i in range(100)]
        result = "\n".join(lines)
        wrapped = wrap("some_tool", result)
        assert "共 101 行数据" in wrapped

    def test_summary_has_preview_lines(self):
        """摘要包含前几行预览"""
        lines = ["标题"] + [f"数据行{i}: 内容" for i in range(100)]
        result = "\n".join(lines)
        wrapped = wrap("some_tool", result)
        assert "数据行0: 内容" in wrapped


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

    def test_erp_agent_result_budget(self):
        result = "标题\n" + "x" * (ERP_AGENT_RESULT_BUDGET + 100)
        wrapped = wrap_erp_agent_result(result)
        assert STAGED_MARKER in wrapped

    def test_erp_agent_result_pass_through_envelope(self):
        """wrap_erp_agent_result 包裹 pass-through 提示词"""
        result = "[当前期] 2026-04-10 周五（今天） 订单 1769 笔"
        wrapped = wrap_erp_agent_result(result)
        assert "─── ERP 结果开始 ───" in wrapped
        assert "─── ERP 结果结束 ───" in wrapped
        assert "禁止改写" in wrapped
        assert result in wrapped

    def test_erp_agent_result_empty_no_envelope(self):
        wrapped = wrap_erp_agent_result("")
        assert wrapped == ""
        wrapped = wrap_erp_agent_result("   ")
        assert "─── ERP 结果开始 ───" not in wrapped

    def test_erp_agent_budget_larger_than_main(self):
        assert ERP_AGENT_BUDGET > MAIN_AGENT_BUDGET

    def test_erp_agent_result_budget_largest(self):
        assert ERP_AGENT_RESULT_BUDGET >= ERP_AGENT_BUDGET


# ============================================================
# code_execute / file_* 免截断
# ============================================================

class TestNoTruncate:

    def test_code_execute_no_truncate(self):
        lines = [f"line{i}: " + "x" * 290 for i in range(10)]
        result = "\n".join(lines)
        wrapped = wrap("code_execute", result)
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
        assert "read_file" in wrapped

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
        """分流写入的文件，通过 rel_path 能被 FileExecutor.resolve_safe_path 解析到"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir
        from services.file_executor import FileExecutor

        org_id, user_id, conv_id = "org1", "user1", "conv-test"

        # 设置 staging_dir 并触发分流
        staging = resolve_staging_dir(str(tmp_path), user_id, org_id, conv_id)
        set_staging_dir(staging)

        result = "标题行\n" + "数据行\n" * 500
        wrapped = wrap("local_shop_list", result)

        # 从 wrapped 中提取 rel_path
        import re
        match = re.search(r'read_file\("([^"]+)"\)', wrapped)
        assert match, f"摘要中未找到 read_file 路径: {wrapped[:200]}"
        rel_path = match.group(1)

        # FileExecutor 能解析到实际文件
        fe = FileExecutor(
            workspace_root=str(tmp_path),
            user_id=user_id,
            org_id=org_id,
        )
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
# _delayed_cleanup_staging
# ============================================================

class TestDelayedCleanupStaging:
    """chat_handler._delayed_cleanup_staging 延迟清理测试"""

    @pytest.mark.asyncio
    async def test_cleanup_removes_staging_dir(self, tmp_path):
        """清理后 staging 目录被删除"""
        from pathlib import Path
        from core.workspace import resolve_staging_dir
        from services.handlers.chat_handler import _delayed_cleanup_staging
        from unittest.mock import patch

        staging = Path(resolve_staging_dir(
            str(tmp_path), "u1", "org1", "conv-cleanup",
        ))
        staging.mkdir(parents=True)
        (staging / "data.txt").write_text("test data")

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            await _delayed_cleanup_staging("conv-cleanup", "u1", "org1", delay=0)

        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_dir(self, tmp_path):
        """staging 目录不存在时不报错"""
        from services.handlers.chat_handler import _delayed_cleanup_staging
        from unittest.mock import patch

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            # 不应抛异常
            await _delayed_cleanup_staging("nonexistent-conv", "u1", "org1", delay=0)
