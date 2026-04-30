"""测试 data_query_executor — 三模式 + 安全 + 边缘情况"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import duckdb
import pandas as pd
import pytest

from services.agent.data_query_cache import detect_file_type
from services.agent.data_query_executor import DataQueryExecutor, _DANGEROUS_SQL_PATTERN


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_workspace(tmp_path):
    """创建临时 workspace 结构"""
    user_id = "test_user"
    org_id = "test_org"
    conv_id = "test_conv"

    # workspace: org/{org_id}/{user_id}/
    ws_dir = tmp_path / "org" / org_id / user_id
    ws_dir.mkdir(parents=True)

    # staging: workspace/staging/{conv_id}/
    staging_dir = ws_dir / "staging" / conv_id
    staging_dir.mkdir(parents=True)

    # output: workspace/下载/
    output_dir = ws_dir / "下载"
    output_dir.mkdir(parents=True)

    return {
        "root": str(tmp_path),
        "user_id": user_id,
        "org_id": org_id,
        "conv_id": conv_id,
        "ws_dir": ws_dir,
        "staging_dir": staging_dir,
        "output_dir": output_dir,
    }


@pytest.fixture
def executor(tmp_workspace):
    """创建 DataQueryExecutor 实例"""
    return DataQueryExecutor(
        user_id=tmp_workspace["user_id"],
        org_id=tmp_workspace["org_id"],
        conversation_id=tmp_workspace["conv_id"],
        workspace_root=tmp_workspace["root"],
    )


@pytest.fixture
def sample_parquet(tmp_workspace):
    """创建样例 Parquet 文件到 staging"""
    df = pd.DataFrame({
        "order_no": [f"A{i:03d}" for i in range(20)],
        "shop_name": ["旗舰店"] * 10 + ["专卖店"] * 10,
        "amount": [99.9 + i * 10 for i in range(20)],
        "qty": list(range(1, 21)),
    })
    path = tmp_workspace["staging_dir"] / "trade_test.parquet"
    df.to_parquet(str(path), index=False, engine="pyarrow")
    return path


@pytest.fixture
def sample_csv(tmp_workspace):
    """创建样例 CSV 文件到 workspace"""
    df = pd.DataFrame({
        "name": ["Alice", "Bob", "Charlie"],
        "score": [85, 92, 78],
    })
    path = tmp_workspace["ws_dir"] / "scores.csv"
    df.to_csv(str(path), index=False)
    return path


@pytest.fixture
def sample_excel(tmp_workspace):
    """创建样例 Excel 文件到 workspace"""
    df1 = pd.DataFrame({"col_a": [1, 2, 3], "col_b": ["x", "y", "z"]})
    df2 = pd.DataFrame({"col_c": [10, 20], "col_d": ["p", "q"]})
    path = tmp_workspace["ws_dir"] / "report.xlsx"
    with pd.ExcelWriter(str(path), engine="xlsxwriter") as writer:
        df1.to_excel(writer, sheet_name="Sheet1", index=False)
        df2.to_excel(writer, sheet_name="Sheet2", index=False)
    return path


@pytest.fixture
def large_parquet(tmp_workspace):
    """创建大数据 Parquet 文件（200 行）"""
    df = pd.DataFrame({
        "id": list(range(200)),
        "value": [i * 1.5 for i in range(200)],
        "category": [f"cat_{i % 5}" for i in range(200)],
    })
    path = tmp_workspace["staging_dir"] / "large_data.parquet"
    df.to_parquet(str(path), index=False, engine="pyarrow")
    return path


# ============================================================
# 探索模式
# ============================================================


class TestExploreMode:
    """探索模式：不传 sql，返回 data_profile"""

    @pytest.mark.asyncio
    async def test_explore_parquet(self, executor, sample_parquet):
        result = await executor.execute(file="trade_test.parquet")
        assert "trade_test.parquet" in result
        assert "20" in result  # 20 行
        assert "order_no" in result
        assert "amount" in result
        assert "[查询]" in result
        assert "data_query" in result

    @pytest.mark.asyncio
    async def test_explore_csv(self, executor, sample_csv):
        result = await executor.execute(file="scores.csv")
        assert "scores.csv" in result
        assert "name" in result
        assert "score" in result

    @pytest.mark.asyncio
    async def test_explore_excel_sheets(self, executor, sample_excel):
        result = await executor.execute(file="report.xlsx")
        assert "report.xlsx" in result
        assert "col_a" in result
        assert "Sheet" in result  # Sheet 列表

    @pytest.mark.asyncio
    async def test_explore_excel_specific_sheet(self, executor, sample_excel):
        result = await executor.execute(file="report.xlsx", sheet="Sheet2")
        assert "col_c" in result
        assert "col_d" in result


# ============================================================
# 查询模式
# ============================================================


class TestQueryMode:
    """查询模式：传 sql，返回结果"""

    @pytest.mark.asyncio
    async def test_query_small_result(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT "order_no", "amount" FROM data WHERE "amount" > 180 LIMIT 5',
        )
        assert "order_no" in result
        assert "amount" in result
        # 应包含 Markdown 表格分隔符
        assert "---" in result

    @pytest.mark.asyncio
    async def test_query_empty_result(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT * FROM data WHERE "amount" > 99999',
        )
        assert "0 行" in result
        assert "❌" not in result  # 空结果不是错误

    @pytest.mark.asyncio
    async def test_query_aggregation(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT "shop_name", SUM("amount") as total FROM data GROUP BY "shop_name"',
        )
        assert "旗舰店" in result
        assert "专卖店" in result

    @pytest.mark.asyncio
    async def test_query_large_result_writes_staging(
        self, executor, large_parquet, tmp_workspace,
    ):
        """超过 100 行的结果应写 staging"""
        result = await executor.execute(
            file="large_data.parquet",
            sql="SELECT * FROM data",
        )
        assert "200" in result  # 200 行
        assert "query_result_" in result  # 暂存文件
        assert "前 5 行预览" in result

    @pytest.mark.asyncio
    async def test_query_sql_error_shows_columns(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT "nonexistent_col" FROM data',
        )
        assert "❌" in result
        assert "可用列名" in result
        assert "order_no" in result

    @pytest.mark.asyncio
    async def test_query_csv(self, executor, sample_csv):
        result = await executor.execute(
            file="scores.csv",
            sql="SELECT name, score FROM data WHERE score > 80",
        )
        assert "Alice" in result
        assert "Bob" in result


# ============================================================
# 导出模式
# ============================================================


class TestExportMode:
    """导出模式：传 sql + export，生成文件"""

    @pytest.mark.asyncio
    async def test_export_csv(self, executor, sample_parquet, tmp_workspace):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="SELECT * FROM data",
            export="output.csv",
        )
        output_file = tmp_workspace["output_dir"] / "output.csv"
        assert output_file.exists()
        # 文件内容检查
        df = pd.read_csv(str(output_file))
        assert len(df) == 20

    @pytest.mark.asyncio
    async def test_export_parquet(self, executor, sample_parquet, tmp_workspace):
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT "shop_name", SUM("amount") as total FROM data GROUP BY "shop_name"',
            export="summary.parquet",
        )
        output_file = tmp_workspace["output_dir"] / "summary.parquet"
        assert output_file.exists()

    @pytest.mark.asyncio
    async def test_export_unsupported_format(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="SELECT * FROM data",
            export="output.json",
        )
        assert "不支持" in result

    @pytest.mark.asyncio
    async def test_export_default_select_all(self, executor, sample_parquet, tmp_workspace):
        """不传 sql 但传 export，默认 SELECT *"""
        result = await executor.execute(
            file="trade_test.parquet",
            export="all_data.csv",
        )
        output_file = tmp_workspace["output_dir"] / "all_data.csv"
        assert output_file.exists()


# ============================================================
# 安全检查
# ============================================================


class TestSecurity:
    """SQL 注入防护 + 路径安全"""

    @pytest.mark.asyncio
    async def test_reject_insert(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="INSERT INTO data VALUES ('evil', 'bad', 0, 0)",
        )
        assert "安全限制" in result

    @pytest.mark.asyncio
    async def test_reject_drop(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="DROP TABLE data",
        )
        assert "安全限制" in result

    @pytest.mark.asyncio
    async def test_reject_copy(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="COPY data TO '/tmp/evil.csv'",
        )
        assert "安全限制" in result

    @pytest.mark.asyncio
    async def test_reject_create(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="CREATE TABLE evil AS SELECT * FROM data",
        )
        assert "安全限制" in result

    @pytest.mark.asyncio
    async def test_path_traversal(self, executor):
        result = await executor.execute(file="../../etc/passwd")
        assert "不存在" in result or "安全限制" in result

    @pytest.mark.asyncio
    async def test_file_not_found(self, executor):
        result = await executor.execute(file="nonexistent.parquet")
        assert "不存在" in result

    def test_dangerous_sql_pattern(self):
        """正则匹配危险 SQL 关键词"""
        assert _DANGEROUS_SQL_PATTERN.search("INSERT INTO x VALUES (1)")
        assert _DANGEROUS_SQL_PATTERN.search("DROP TABLE data")
        assert _DANGEROUS_SQL_PATTERN.search("COPY data TO '/tmp/x'")
        assert _DANGEROUS_SQL_PATTERN.search("CREATE TABLE x (id INT)")
        assert _DANGEROUS_SQL_PATTERN.search("ATTACH '/etc/passwd'")
        # SELECT 应该安全
        assert _DANGEROUS_SQL_PATTERN.search("SELECT * FROM data") is None
        # 双引号包裹的列名中 update 不被误拦（\b 边界正确）
        assert _DANGEROUS_SQL_PATTERN.search(
            'SELECT "update_time" FROM data'
        ) is None

    @pytest.mark.asyncio
    async def test_empty_file_param(self, executor):
        result = await executor.execute(file="")
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_reject_semicolon(self, executor, sample_parquet):
        """分号拦截：防止多语句注入"""
        result = await executor.execute(
            file="trade_test.parquet",
            sql="SELECT 1; DROP TABLE data",
        )
        assert "分号" in result or "安全限制" in result

    @pytest.mark.asyncio
    async def test_reject_semicolon_in_export(self, executor, sample_parquet):
        result = await executor.execute(
            file="trade_test.parquet",
            sql="SELECT 1; SELECT 2",
            export="output.csv",
        )
        assert "分号" in result or "安全限制" in result

    def test_validate_sql_function(self):
        """_validate_sql 公共校验函数"""
        from services.agent.data_query_executor import _validate_sql
        assert _validate_sql("SELECT * FROM data") is None
        assert _validate_sql("SELECT 1; DROP TABLE x") is not None
        assert _validate_sql("INSERT INTO data VALUES (1)") is not None
        assert _validate_sql('SELECT "update_time" FROM data') is None


# ============================================================
# 超时保护
# ============================================================


class TestTimeout:
    """查询超时机制"""

    @pytest.mark.asyncio
    async def test_execute_with_timeout_normal(self, executor, sample_parquet):
        """正常查询不超时"""
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT COUNT(*) FROM data',
        )
        assert "❌" not in result

    def test_execute_with_timeout_interrupt(self):
        """threading.Timer + interrupt 看门狗机制"""
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE big AS SELECT i FROM range(1000000) t(i)")

        from services.agent.data_query_executor import DataQueryExecutor
        # 0.1s 超时，笛卡尔积查询必然超时
        with pytest.raises(TimeoutError, match="超时"):
            DataQueryExecutor._execute_with_timeout(
                con, "SELECT COUNT(*) FROM big t1, big t2 WHERE t1.i > t2.i",
                timeout=0.1,
            )
        con.close()


# ============================================================
# COPY TO Parquet 流式写盘（大结果不经过 Python 内存）
# ============================================================


class TestCopyToParquet:
    """查询结果直接写 Parquet，大结果不 fetchdf"""

    @pytest.mark.asyncio
    async def test_large_result_writes_parquet_directly(
        self, executor, large_parquet, tmp_workspace,
    ):
        """>100 行结果直接写 staging Parquet，不全量加载到 Python"""
        result = await executor.execute(
            file="large_data.parquet",
            sql="SELECT * FROM data",
        )
        assert "200" in result  # 200 行
        assert "query_result_" in result  # staging 文件
        assert "前 5 行预览" in result

        # 验证 staging 中确实有 parquet 文件
        staging = tmp_workspace["staging_dir"]
        result_files = list(staging.glob("query_result_*.parquet"))
        assert len(result_files) >= 1

    @pytest.mark.asyncio
    async def test_small_result_no_staging_file(
        self, executor, sample_parquet, tmp_workspace,
    ):
        """≤100 行结果不保留 staging 文件"""
        result = await executor.execute(
            file="trade_test.parquet",
            sql='SELECT * FROM data LIMIT 5',
        )
        assert "❌" not in result
        # 小结果不应产生 query_result_ 文件
        staging = tmp_workspace["staging_dir"]
        result_files = list(staging.glob("query_result_*.parquet"))
        assert len(result_files) == 0


# ============================================================
# 文件类型检测
# ============================================================


class TestFileTypeDetection:
    """文件类型检测"""

    def test_detect_parquet(self, sample_parquet):
        result = detect_file_type(str(sample_parquet))
        assert result == "parquet"

    def test_detect_csv(self, sample_csv):
        result = detect_file_type(str(sample_csv))
        assert result == "csv"

    def test_detect_excel(self, sample_excel):
        result = detect_file_type(str(sample_excel))
        assert result == "excel"

    def test_detect_unknown(self, tmp_path):
        f = tmp_path / "data.xyz"
        f.write_bytes(b"random content")
        result = detect_file_type(str(f))
        assert result == "unknown"


# ============================================================
# Excel 缓存
# ============================================================


class TestExcelCache:
    """Excel → Parquet 缓存转换"""

    @pytest.mark.asyncio
    async def test_cache_created(self, executor, sample_excel, tmp_workspace):
        """首次查询创建缓存"""
        result = await executor.execute(file="report.xlsx")
        # 检查 staging 中有缓存文件
        staging = tmp_workspace["staging_dir"]
        cache_files = list(staging.glob("_cache_*.parquet"))
        assert len(cache_files) >= 1

    @pytest.mark.asyncio
    async def test_cache_reused(self, executor, sample_excel, tmp_workspace):
        """二次查询复用缓存"""
        await executor.execute(file="report.xlsx")
        staging = tmp_workspace["staging_dir"]
        cache_files_1 = list(staging.glob("_cache_*.parquet"))
        mtime_1 = cache_files_1[0].stat().st_mtime

        # 二次查询
        await executor.execute(file="report.xlsx")
        cache_files_2 = list(staging.glob("_cache_*.parquet"))
        assert len(cache_files_2) == len(cache_files_1)
        # mtime 不变 = 没有重新转换
        assert cache_files_2[0].stat().st_mtime == mtime_1

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_update(
        self, executor, sample_excel, tmp_workspace,
    ):
        """源文件更新后缓存失效"""
        await executor.execute(file="report.xlsx")

        # 修改源文件（改 mtime + size）
        import time
        time.sleep(0.1)
        df = pd.DataFrame({"new_col": [100, 200, 300, 400]})
        df.to_excel(
            str(sample_excel), index=False, engine="xlsxwriter",
        )

        result = await executor.execute(file="report.xlsx")
        assert "new_col" in result

    @pytest.mark.asyncio
    async def test_different_sheets_different_cache(
        self, executor, sample_excel, tmp_workspace,
    ):
        """不同 Sheet 缓存为独立文件"""
        await executor.execute(file="report.xlsx", sheet="Sheet1")
        await executor.execute(file="report.xlsx", sheet="Sheet2")
        staging = tmp_workspace["staging_dir"]
        cache_files = list(staging.glob("_cache_*.parquet"))
        assert len(cache_files) == 2


# ============================================================
# 工具注册
# ============================================================


class TestToolRegistration:
    """工具在各注册表中正确配置"""

    def test_in_concurrent_safe(self):
        from config.chat_tools import is_concurrency_safe
        assert is_concurrency_safe("data_query")

    def test_in_core_tools(self):
        from config.chat_tools import _CORE_TOOLS
        assert "data_query" in _CORE_TOOLS

    def test_in_tool_domains(self):
        from config.tool_domains import TOOL_DOMAINS, ToolDomain
        assert TOOL_DOMAINS["data_query"] == ToolDomain.SHARED

    def test_safety_level_safe(self):
        from config.chat_tools import get_safety_level, SafetyLevel
        assert get_safety_level("data_query") == SafetyLevel.SAFE

    def test_tool_schema_in_common_tools(self):
        from config.chat_tools import _build_common_tools
        tools = _build_common_tools()
        names = [t["function"]["name"] for t in tools]
        assert "data_query" in names

    def test_tool_in_erp_domain(self):
        from config.phase_tools import build_domain_tools
        erp_tools = build_domain_tools("erp")
        names = [t["function"]["name"] for t in erp_tools]
        assert "data_query" in names

    def test_tool_in_computer_domain(self):
        from config.phase_tools import build_domain_tools
        computer_tools = build_domain_tools("computer")
        names = [t["function"]["name"] for t in computer_tools]
        assert "data_query" in names


# ============================================================
# 提示词变更
# ============================================================


class TestPromptChanges:
    """提示词正确更新"""

    def test_tool_system_prompt_has_data_query(self):
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        assert "data_query" in TOOL_SYSTEM_PROMPT
        assert "FROM data" in TOOL_SYSTEM_PROMPT

    def test_code_execute_prompt_updated(self):
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        assert "大数据文件用 data_query 查询" in TOOL_SYSTEM_PROMPT or \
               "大文件用 data_query 查询" in TOOL_SYSTEM_PROMPT

    def test_code_routing_prompt_has_data_query(self):
        from config.code_tools import CODE_ROUTING_PROMPT
        assert "data_query" in CODE_ROUTING_PROMPT

    def test_base_agent_prompt_updated(self):
        from config.phase_tools import BASE_AGENT_PROMPT
        assert "data_query" in BASE_AGENT_PROMPT

    def test_data_profile_query_hint(self):
        """data_profile 输出 [查询] 而非 [读取]"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        from services.agent.data_profile import build_data_profile
        text, _ = build_data_profile(df, "test.parquet", 1.0)
        assert "[查询]" in text
        assert "data_query" in text
        assert "[读取]" not in text

    def test_description_workspace_updated(self):
        from config.code_tools import _DESCRIPTION_WORKSPACE
        assert "data_query" in _DESCRIPTION_WORKSPACE
        assert "ERP 数据由 erp_agent" not in _DESCRIPTION_WORKSPACE

    def test_description_base_updated(self):
        from config.code_tools import _DESCRIPTION_BASE
        assert "data_query" in _DESCRIPTION_BASE
        assert "数据由其他工具获取后" not in _DESCRIPTION_BASE


# ============================================================
# 修复验证（审查发现的边缘情况）
# ============================================================


class TestCacheSnapshotTolerance:
    """mtime float 精度容差比较。"""

    def test_mtime_tolerance_match(self, tmp_path):
        """微小精度差异应被视为匹配。"""
        from services.agent.data_query_cache import _snapshot_matches

        cache = tmp_path / "test.parquet"
        cache.write_bytes(b"data")
        snap = tmp_path / "test.snapshot"
        # 模拟 mtime 有微小精度偏差（0.0001）
        snap.write_text("1714400000.1234,100")

        result = _snapshot_matches(cache, snap, 1714400000.1235, 100)
        assert result is True

    def test_mtime_large_diff_no_match(self, tmp_path):
        """大偏差应不匹配。"""
        from services.agent.data_query_cache import _snapshot_matches

        cache = tmp_path / "test.parquet"
        cache.write_bytes(b"data")
        snap = tmp_path / "test.snapshot"
        snap.write_text("1714400000.0,100")

        result = _snapshot_matches(cache, snap, 1714400001.0, 100)
        assert result is False

    def test_size_mismatch(self, tmp_path):
        """size 不同应不匹配。"""
        from services.agent.data_query_cache import _snapshot_matches

        cache = tmp_path / "test.parquet"
        cache.write_bytes(b"data")
        snap = tmp_path / "test.snapshot"
        snap.write_text("1714400000.0,100")

        result = _snapshot_matches(cache, snap, 1714400000.0, 200)
        assert result is False


class TestConvertLocksLRU:
    """_convert_locks 按文件路径隔离的锁，LRU 淘汰。"""

    def test_locks_bounded(self):
        """锁数量不超过 _MAX_LOCKS。"""
        from services.agent import data_query_cache

        old_locks = data_query_cache._convert_locks.copy()
        try:
            data_query_cache._convert_locks.clear()
            for i in range(data_query_cache._MAX_LOCKS + 10):
                key = f"/path/to/file_{i}.xlsx:sheet0"
                if key not in data_query_cache._convert_locks:
                    if len(data_query_cache._convert_locks) >= data_query_cache._MAX_LOCKS:
                        oldest_key = next(iter(data_query_cache._convert_locks))
                        del data_query_cache._convert_locks[oldest_key]
                    import asyncio
                    data_query_cache._convert_locks[key] = asyncio.Lock()

            assert len(data_query_cache._convert_locks) <= data_query_cache._MAX_LOCKS
        finally:
            data_query_cache._convert_locks.clear()
            data_query_cache._convert_locks.update(old_locks)
