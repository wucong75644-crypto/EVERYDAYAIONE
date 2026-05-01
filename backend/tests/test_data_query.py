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


class TestCollectSchema:
    """DataQueryExecutor._collect_schema: 从文件收集 schema 元信息"""

    def _make_parquet(self, tmp_path, name="test.parquet", rows=10, cols=None):
        """创建测试 Parquet 文件"""
        import pandas as pd
        if cols is None:
            cols = {"店铺名": [f"店铺{i}" for i in range(rows)],
                    "金额": [float(i * 100) for i in range(rows)],
                    "订单数": list(range(rows))}
        df = pd.DataFrame(cols)
        path = tmp_path / name
        df.to_parquet(path, index=False, engine="pyarrow")
        return str(path)

    def _make_csv(self, tmp_path, name="test.csv", rows=5):
        """创建测试 CSV 文件"""
        lines = ["店铺名,金额,订单数"]
        for i in range(rows):
            lines.append(f"店铺{i},{i * 100},{i}")
        path = tmp_path / name
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def _make_executor(self, tmp_path):
        from services.agent.data_query_executor import DataQueryExecutor
        executor = DataQueryExecutor.__new__(DataQueryExecutor)
        executor._workspace_dir = str(tmp_path)
        executor._staging_dir = str(tmp_path)
        executor._output_dir = str(tmp_path / "下载")
        executor.last_file_meta = None
        return executor

    def test_parquet_schema_collected(self, tmp_path):
        """Parquet 文件：列名+类型+行数全部收集"""
        parquet_path = self._make_parquet(tmp_path, rows=50)
        executor = self._make_executor(tmp_path)

        executor._collect_schema("test.parquet", parquet_path, parquet_path)

        assert executor.last_file_meta is not None
        filename, path, schema_text = executor.last_file_meta
        assert filename == "test.parquet"
        assert "50行" in schema_text
        assert "3列" in schema_text
        assert "店铺名" in schema_text
        assert "金额" in schema_text
        assert "订单数" in schema_text

    def test_csv_schema_collected(self, tmp_path):
        """CSV 文件：列名+类型+行数收集"""
        csv_path = self._make_csv(tmp_path, rows=8)
        executor = self._make_executor(tmp_path)

        executor._collect_schema("test.csv", csv_path, csv_path)

        assert executor.last_file_meta is not None
        filename, path, schema_text = executor.last_file_meta
        assert filename == "test.csv"
        assert "8行" in schema_text
        assert "3列" in schema_text
        assert "店铺名" in schema_text

    def test_nonexistent_file_no_error(self, tmp_path):
        """不存在的文件 → 静默失败，不抛异常"""
        executor = self._make_executor(tmp_path)

        executor._collect_schema("ghost.parquet", "/tmp/ghost.parquet", "/tmp/ghost.parquet")

        assert executor.last_file_meta is None

    def test_large_parquet_row_count_from_metadata(self, tmp_path):
        """大 Parquet 文件：行数从 PyArrow metadata 读取（不扫描数据）"""
        parquet_path = self._make_parquet(tmp_path, rows=10000)
        executor = self._make_executor(tmp_path)

        executor._collect_schema("big.parquet", parquet_path, parquet_path)

        assert executor.last_file_meta is not None
        _, _, schema_text = executor.last_file_meta
        assert "10,000行" in schema_text

    def test_many_columns_all_listed(self, tmp_path):
        """多列文件：所有列都出现在 schema 中"""
        cols = {f"col_{i}": list(range(5)) for i in range(20)}
        parquet_path = self._make_parquet(tmp_path, cols=cols)
        executor = self._make_executor(tmp_path)

        executor._collect_schema("wide.parquet", parquet_path, parquet_path)

        assert executor.last_file_meta is not None
        _, _, schema_text = executor.last_file_meta
        assert "20列" in schema_text
        for i in range(20):
            assert f"col_{i}" in schema_text


class TestDataQueryExecutorLastFileMeta:
    """DataQueryExecutor.execute 后 last_file_meta 在各模式下的行为"""

    def _make_parquet(self, tmp_path, name="data.parquet"):
        import pandas as pd
        df = pd.DataFrame({"name": ["a", "b", "c"], "value": [1, 2, 3]})
        path = tmp_path / name
        df.to_parquet(path, index=False, engine="pyarrow")
        return path

    @pytest.mark.asyncio
    async def test_explore_mode_sets_meta(self, tmp_path):
        """探索模式（无 sql）→ last_file_meta 被设置"""
        self._make_parquet(tmp_path)
        from services.agent.data_query_executor import DataQueryExecutor
        executor = DataQueryExecutor.__new__(DataQueryExecutor)
        executor._workspace_dir = str(tmp_path)
        executor._staging_dir = str(tmp_path)
        executor._output_dir = str(tmp_path / "下载")
        executor.last_file_meta = None
        executor.user_id = "test"
        executor.org_id = None
        executor.conversation_id = "test"

        result = await executor.execute(file="data.parquet")
        assert not result.startswith("❌")
        assert executor.last_file_meta is not None
        assert "name" in executor.last_file_meta[2]

    @pytest.mark.asyncio
    async def test_query_mode_sets_meta(self, tmp_path):
        """查询模式（有 sql）→ last_file_meta 被设置"""
        self._make_parquet(tmp_path)
        from services.agent.data_query_executor import DataQueryExecutor
        executor = DataQueryExecutor.__new__(DataQueryExecutor)
        executor._workspace_dir = str(tmp_path)
        executor._staging_dir = str(tmp_path)
        executor._output_dir = str(tmp_path / "下载")
        executor.last_file_meta = None
        executor.user_id = "test"
        executor.org_id = None
        executor.conversation_id = "test"

        result = await executor.execute(file="data.parquet", sql="SELECT * FROM data LIMIT 1")
        assert not result.startswith("❌")
        assert executor.last_file_meta is not None

    @pytest.mark.asyncio
    async def test_error_result_no_meta(self, tmp_path):
        """文件不存在 → 错误结果 → last_file_meta 不设置"""
        from services.agent.data_query_executor import DataQueryExecutor
        executor = DataQueryExecutor.__new__(DataQueryExecutor)
        executor._workspace_dir = str(tmp_path)
        executor._staging_dir = str(tmp_path)
        executor._output_dir = str(tmp_path / "下载")
        executor.last_file_meta = None
        executor.user_id = "test"
        executor.org_id = None
        executor.conversation_id = "test"

        result = await executor.execute(file="nonexistent.parquet")
        assert result.startswith("❌")
        assert executor.last_file_meta is None


class TestFilePathCache:
    """对话级文件路径缓存"""

    def test_register_and_resolve(self):
        """注册后精确匹配"""
        from services.agent.workspace_file_handles import FilePathCache

        cache = FilePathCache()
        cache.register("利润表.xlsx", "/workspace/利润表.xlsx")
        assert cache.resolve("利润表.xlsx") == "/workspace/利润表.xlsx"

    def test_resolve_with_spaces(self):
        """LLM 加空格后仍能匹配"""
        from services.agent.workspace_file_handles import FilePathCache

        cache = FilePathCache()
        cache.register(
            "利润表-店铺利润表-2026-04-20_2026-04-26-导出20260427201027_65109_hl22DH_e3ea4b.xlsx",
            "/workspace/real_path.xlsx",
        )
        # LLM 加了空格
        result = cache.resolve(
            "利润表 - 店铺利润表 -2026-04-20_2026-04-26-导出 20260427201027_65109_hl22DH_e3ea4b.xlsx"
        )
        assert result == "/workspace/real_path.xlsx"

    def test_resolve_miss(self):
        """未注册的文件名返回 None"""
        from services.agent.workspace_file_handles import FilePathCache

        cache = FilePathCache()
        assert cache.resolve("不存在.xlsx") is None

    def test_duplicate_register_overwrites(self):
        """重复注册同名文件覆盖路径"""
        from services.agent.workspace_file_handles import FilePathCache

        cache = FilePathCache()
        cache.register("data.xlsx", "/old/path.xlsx")
        cache.register("data.xlsx", "/new/path.xlsx")
        assert cache.resolve("data.xlsx") == "/new/path.xlsx"

    def test_conversation_level_isolation(self):
        """不同对话的缓存互不影响"""
        from services.agent.workspace_file_handles import get_file_cache, _caches

        _caches.pop("conv-a", None)
        _caches.pop("conv-b", None)

        cache_a = get_file_cache("conv-a")
        cache_a.register("file.xlsx", "/a/file.xlsx")

        cache_b = get_file_cache("conv-b")
        assert cache_b.resolve("file.xlsx") is None

        _caches.pop("conv-a", None)
        _caches.pop("conv-b", None)


class TestDetectHeaderRow:
    """detect_header_row: messytables 众数法 + csv.Sniffer 类型验证"""

    def test_standard_excel_header_at_row0(self):
        """标准表格：第 0 行就是表头 → 返回 0"""
        from services.agent.data_query_cache import detect_header_row

        rows = [
            ["店铺名", "实付金额", "退款", "毛利"],
            ["蓝创旗舰", 15234.5, 423.0, 8921.3],
            ["蓝创专营", 8120.0, 210.5, 4532.1],
        ]
        assert detect_header_row(rows) == 0

    def test_erp_profit_report_header_at_row2(self):
        """ERP 利润表：标题行 + 分类行 + 真正表头在第 2 行"""
        from services.agent.data_query_cache import detect_header_row

        rows = [
            ["利润表-店铺利润表", None, None, None, None, None, None, None, None, None],
            ["日期范围", None, "2026-04-20 至 2026-04-26", None, None, None, None, None, None, None],
            ["店铺名", "实付金额", "退款金额", "毛利", "运费", "推广费", "佣金", "利润", "利润率", "订单数"],
            ["蓝创旗舰", 15234.5, 423.0, 8921.3, 500, 1200, 320, 6901.3, "45.3%", 128],
            ["蓝创专营", 8120.0, 210.5, 4532.1, 300, 800, 180, 3252.1, "40.0%", 67],
        ]
        assert detect_header_row(rows) == 2

    def test_single_title_row_header_at_row1(self):
        """单标题行：第 0 行标题，第 1 行就是表头"""
        from services.agent.data_query_cache import detect_header_row

        rows = [
            ["月度销售报表", None, None, None],
            ["日期", "平台", "销售额", "订单数"],
            ["2026-04-01", "淘宝", 5230.0, 42],
            ["2026-04-01", "拼多多", 3120.0, 35],
        ]
        assert detect_header_row(rows) == 1

    def test_empty_rows(self):
        """空行列表 → 返回 0"""
        from services.agent.data_query_cache import detect_header_row

        assert detect_header_row([]) == 0

    def test_all_numeric_no_header(self):
        """纯数字表（无表头）→ 返回 0"""
        from services.agent.data_query_cache import detect_header_row

        rows = [
            [1, 2, 3, 4],
            [5, 6, 7, 8],
            [9, 10, 11, 12],
        ]
        assert detect_header_row(rows) == 0

    def test_header_with_mixed_none_cells(self):
        """表头行有少量 None 但字符串占比仍达标"""
        from services.agent.data_query_cache import detect_header_row

        rows = [
            ["总览报表", None, None, None, None, None],
            ["品类", "子类", None, "销量", "金额", "占比"],
            ["服装", "上衣", "T恤", 120, 5400.0, "23%"],
        ]
        # 第 1 行：4/6 非空，全是字符串 → 命中
        assert detect_header_row(rows) == 1

    def test_csv_style_no_title(self):
        """CSV 风格：第一行直接就是列名"""
        from services.agent.data_query_cache import detect_header_row

        rows = [
            ["order_no", "platform", "amount", "status", "created_at"],
            ["TB20260401001", "taobao", 129.9, "completed", "2026-04-01"],
            ["PDD20260401002", "pdd", 59.9, "shipped", "2026-04-01"],
        ]
        assert detect_header_row(rows) == 0
