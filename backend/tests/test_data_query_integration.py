"""
data_query 跨模块集成测试

验证 data_query + schema_filter + staging_cleaner + session_file_registry
四个模块联动的完整链路。

链路1：ERP 数据分析（erp_agent → staging → data_query → 小结果）
链路2：工作区文件分析（Excel → 缓存 → data_query 探索 → 查询）
链路3：全量导出（staging → data_query export → xlsx）
链路4：schema 过滤 + staging 清理联动
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

# ── 公共 fixture ──


@pytest.fixture()
def workspace(tmp_path):
    """模拟完整的用户 workspace 目录结构。"""
    ws = tmp_path / "personal" / "test_user"
    staging = ws / "staging" / "conv_test"
    output = ws / "下载"
    ws.mkdir(parents=True)
    staging.mkdir(parents=True)
    output.mkdir(parents=True)
    return {
        "root": str(tmp_path),
        "workspace": str(ws),
        "staging": str(staging),
        "output": str(output),
    }


@pytest.fixture()
def sample_erp_data(workspace):
    """模拟 erp_agent 写入 staging 的 Parquet 文件。"""
    df = pd.DataFrame({
        "店铺名称": ["白桃汽水杂货铺"] * 50 + ["蓝色海洋旗舰店"] * 30 + ["星光数码专营"] * 20,
        "金额": [round(x * 10.5, 2) for x in range(100)],
        "日期": pd.date_range("2026-04-01", periods=100, freq="D"),
        "订单状态": ["PAID"] * 80 + ["CLOSED"] * 15 + ["CANCEL"] * 5,
        "is_scalping": [0] * 90 + [1] * 10,
    })
    path = Path(workspace["staging"]) / "trade_1714400000.parquet"
    df.to_parquet(str(path), index=False, engine="pyarrow")
    return str(path), df


@pytest.fixture()
def sample_excel(workspace):
    """模拟用户上传到工作区的 Excel 文件。"""
    df = pd.DataFrame({
        "产品": [f"SKU_{i:04d}" for i in range(200)],
        "销售额": [round(x * 99.9, 2) for x in range(200)],
        "月份": ["2026-01"] * 50 + ["2026-02"] * 50 + ["2026-03"] * 50 + ["2026-04"] * 50,
    })
    path = Path(workspace["workspace"]) / "销售报表.xlsx"
    df.to_excel(str(path), index=False, engine="openpyxl")
    return str(path), df


def _make_executor(workspace):
    """创建 DataQueryExecutor（mock workspace 解析）。"""
    from services.agent.data_query_executor import DataQueryExecutor

    with patch("core.workspace.resolve_workspace_dir",
               return_value=workspace["workspace"]), \
         patch("core.workspace.resolve_staging_dir",
               return_value=workspace["staging"]):
        return DataQueryExecutor(
            user_id="test_user",
            org_id=None,
            conversation_id="conv_test",
            workspace_root=workspace["root"],
        )


# ══════════════════════════════════════════════════════
# 链路1：ERP 数据分析
# erp_agent 写 staging → data_query 探索 → SQL 查询 → 小结果
# ══════════════════════════════════════════════════════


class TestERPDataAnalysis:
    """ERP 数据分析全链路。"""

    @pytest.mark.asyncio
    async def test_explore_staging_parquet(self, workspace, sample_erp_data):
        """探索 erp_agent 产出的 staging 文件。"""
        executor = _make_executor(workspace)
        result = await executor.execute(file="trade_1714400000.parquet")

        assert "100" in result  # 100 行
        assert "店铺名称" in result
        assert "金额" in result
        assert "日期" in result

    @pytest.mark.asyncio
    async def test_query_with_aggregation(self, workspace, sample_erp_data):
        """SQL 聚合查询 staging 文件。"""
        executor = _make_executor(workspace)
        result = await executor.execute(
            file="trade_1714400000.parquet",
            sql='SELECT "店铺名称", COUNT(*) as cnt, SUM("金额") as total '
                'FROM data GROUP BY "店铺名称" ORDER BY total DESC',
        )

        assert "白桃汽水杂货铺" in result
        assert "蓝色海洋旗舰店" in result
        assert "星光数码专营" in result
        # 3 行聚合结果，应该直接返回完整表格
        assert "|" in result  # Markdown 表格格式

    @pytest.mark.asyncio
    async def test_query_with_filter(self, workspace, sample_erp_data):
        """带条件过滤查询。"""
        executor = _make_executor(workspace)
        result = await executor.execute(
            file="trade_1714400000.parquet",
            sql='SELECT COUNT(*) as cnt FROM data WHERE is_scalping = 0',
        )

        assert "90" in result  # 90 条非刷单

    @pytest.mark.asyncio
    async def test_empty_result(self, workspace, sample_erp_data):
        """空结果不应被误判为错误。"""
        executor = _make_executor(workspace)
        result = await executor.execute(
            file="trade_1714400000.parquet",
            sql='SELECT * FROM data WHERE "金额" > 999999',
        )

        assert "0 行" in result or "空" in result
        assert "❌" not in result


# ══════════════════════════════════════════════════════
# 链路2：工作区文件分析
# Excel → Parquet 缓存 → data_query 探索 → SQL 查询
# ══════════════════════════════════════════════════════


class TestWorkspaceFileAnalysis:
    """工作区文件分析全链路。"""

    @pytest.mark.asyncio
    async def test_explore_excel(self, workspace, sample_excel):
        """探索 Excel 文件（自动转 Parquet 缓存）。"""
        executor = _make_executor(workspace)
        result = await executor.execute(file="销售报表.xlsx")

        assert "200" in result  # 200 行
        assert "产品" in result
        assert "销售额" in result
        assert "月份" in result

    @pytest.mark.asyncio
    async def test_explore_creates_cache(self, workspace, sample_excel):
        """探索后 staging 中应该有缓存文件。"""
        executor = _make_executor(workspace)
        await executor.execute(file="销售报表.xlsx")

        staging = Path(workspace["staging"])
        cache_files = list(staging.glob("_cache_*销售报表*.parquet"))
        assert len(cache_files) == 1

    @pytest.mark.asyncio
    async def test_query_uses_cache(self, workspace, sample_excel):
        """第二次查询应该走缓存，不重复转换。"""
        executor = _make_executor(workspace)

        # 第一次（转换）
        await executor.execute(file="销售报表.xlsx")
        staging = Path(workspace["staging"])
        cache_files = list(staging.glob("_cache_*销售报表*.parquet"))
        cache_mtime = cache_files[0].stat().st_mtime

        # 等一小段时间确保 mtime 有差异
        await asyncio.sleep(0.1)

        # 第二次（应走缓存）
        await executor.execute(
            file="销售报表.xlsx",
            sql='SELECT "月份", SUM("销售额") as total FROM data GROUP BY "月份"',
        )

        # 缓存文件 mtime 不变（没有重新转换）
        new_mtime = cache_files[0].stat().st_mtime
        assert new_mtime == cache_mtime

    @pytest.mark.asyncio
    async def test_query_excel_with_sql(self, workspace, sample_excel):
        """对 Excel 文件执行 SQL 查询。"""
        executor = _make_executor(workspace)
        result = await executor.execute(
            file="销售报表.xlsx",
            sql='SELECT "月份", COUNT(*) as cnt, SUM("销售额") as total '
                'FROM data GROUP BY "月份" ORDER BY "月份"',
        )

        assert "2026-01" in result
        assert "2026-04" in result
        assert "|" in result  # 4 行，完整 Markdown 表格


# ══════════════════════════════════════════════════════
# 链路3：全量导出
# staging → data_query export → xlsx 文件
# ══════════════════════════════════════════════════════


class TestExportMode:
    """全量导出链路。"""

    @pytest.mark.asyncio
    async def test_export_csv(self, workspace, sample_erp_data):
        """导出为 CSV（不依赖 spatial 扩展）。"""
        executor = _make_executor(workspace)

        with patch("services.file_upload.auto_upload",
                   new_callable=AsyncMock,
                   return_value="[FILE]https://cdn/test.csv|test.csv|text/csv|1234[/FILE]"):
            result = await executor.execute(
                file="trade_1714400000.parquet",
                sql="SELECT * FROM data",
                export="导出测试.csv",
            )

        assert "❌" not in result
        output_file = Path(workspace["output"]) / "导出测试.csv"
        assert output_file.exists()
        # 验证行数
        df = pd.read_csv(str(output_file))
        assert len(df) == 100

    @pytest.mark.asyncio
    async def test_export_with_filter(self, workspace, sample_erp_data):
        """导出带过滤条件的子集。"""
        executor = _make_executor(workspace)

        with patch("services.file_upload.auto_upload",
                   new_callable=AsyncMock,
                   return_value="[FILE]https://cdn/test.csv|test.csv|text/csv|1234[/FILE]"):
            result = await executor.execute(
                file="trade_1714400000.parquet",
                sql='SELECT * FROM data WHERE "订单状态" = \'PAID\'',
                export="已付款订单.csv",
            )

        assert "❌" not in result
        output_file = Path(workspace["output"]) / "已付款订单.csv"
        df = pd.read_csv(str(output_file))
        assert len(df) == 80  # 80 条 PAID


# ══════════════════════════════════════════════════════
# 链路4：schema 过滤 + staging 清理联动
# ══════════════════════════════════════════════════════


class TestSchemaAndCleanupIntegration:
    """schema 持久化 + staging 清理联动。"""

    def test_registry_protects_files_from_cleanup(self, workspace, sample_erp_data):
        """registry 中的文件不会被 staging 清理删除。"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_output import FileRef
        from services.staging_cleaner import cleanup_staging

        parquet_path, _ = sample_erp_data
        registry = SessionFileRegistry()

        # 注册文件到 registry
        file_ref = FileRef(
            path=parquet_path,
            filename="trade_1714400000.parquet",
            format="parquet",
            row_count=100,
            size_bytes=os.path.getsize(parquet_path),
            columns=[],
        )
        registry.register("trade", "local_data", file_ref, schema_text="test schema")

        # 把文件 mtime 改成 48 小时前（超过 TTL）
        old_time = time.time() - 48 * 3600
        os.utime(parquet_path, (old_time, old_time))

        # 清理
        stats = cleanup_staging(
            staging_dir=workspace["staging"],
            registry=registry,
            ttl_seconds=86400,
        )

        # 文件应该被保护，不被删
        assert Path(parquet_path).exists()
        assert stats["protected"] >= 1

    def test_orphan_files_deleted_after_ttl(self, workspace):
        """不在 registry 中的过期文件会被清理。"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.staging_cleaner import cleanup_staging

        # 创建一个孤儿文件
        orphan = Path(workspace["staging"]) / "orphan_old.parquet"
        orphan.write_bytes(b"fake parquet data")
        old_time = time.time() - 48 * 3600
        os.utime(str(orphan), (old_time, old_time))

        registry = SessionFileRegistry()
        stats = cleanup_staging(
            staging_dir=workspace["staging"],
            registry=registry,
            ttl_seconds=86400,
        )

        assert not orphan.exists()
        assert stats["deleted"] >= 1

    def test_registry_lru_eviction(self, workspace):
        """registry 注册超过 20 条时自动淘汰最旧的。"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_output import FileRef

        registry = SessionFileRegistry()
        # 注册 25 条（registry 内部自动 LRU 淘汰到 20 条上限）
        for i in range(25):
            ref = FileRef(
                path=f"/tmp/file_{i}.parquet",
                filename=f"file_{i}.parquet",
                format="parquet",
                row_count=10,
                size_bytes=1000,
                columns=[],
            )
            registry.register(f"domain_{i}", "query", ref, schema_text=f"schema {i}")

        # registry 内部自动淘汰，最终保留 20 条
        remaining = registry.list_all()
        assert len(remaining) == 20

        # 最早注册的（domain_0~4）应该被淘汰
        keys = [k for k, _ in remaining]
        assert not any("domain_0:" in k for k in keys)
        assert not any("domain_4:" in k for k in keys)
        # 最近注册的应该保留
        assert any("domain_24:" in k for k in keys)

    def test_schema_text_survives_registry(self, workspace, sample_erp_data):
        """schema_text 注册后可以通过 get_schema_entries 取到。"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_output import FileRef

        parquet_path, _ = sample_erp_data
        registry = SessionFileRegistry()
        file_ref = FileRef(
            path=parquet_path,
            filename="trade_1714400000.parquet",
            format="parquet",
            row_count=100,
            size_bytes=os.path.getsize(parquet_path),
            columns=[],
        )

        schema_text = "[数据已暂存] trade_1714400000.parquet\n共 100 条 | 5 列"
        registry.register("trade", "local_data", file_ref, schema_text=schema_text)

        entries = registry.get_schema_entries()
        assert len(entries) >= 1
        # 检查 schema_text 能取到
        found = any(schema_text in entry[2] for entry in entries)
        assert found


# ══════════════════════════════════════════════════════
# 链路5：SQL 安全边界
# ══════════════════════════════════════════════════════


class TestSecurityIntegration:
    """跨模块安全验证。"""

    @pytest.mark.asyncio
    async def test_sql_injection_blocked(self, workspace, sample_erp_data):
        """SQL 注入在 data_query 层被拦截。"""
        executor = _make_executor(workspace)
        result = await executor.execute(
            file="trade_1714400000.parquet",
            sql="DROP TABLE data; SELECT 1",
        )

        assert "❌" in result
        assert "安全" in result or "禁止" in result or "SELECT" in result

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, workspace, sample_erp_data):
        """路径穿越在 data_query 层被拦截。"""
        executor = _make_executor(workspace)
        result = await executor.execute(file="../../etc/passwd")

        assert "❌" in result

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, workspace):
        """不存在的文件返回友好错误。"""
        executor = _make_executor(workspace)
        result = await executor.execute(file="不存在的文件.parquet")

        assert "❌" in result
        assert "不存在" in result
