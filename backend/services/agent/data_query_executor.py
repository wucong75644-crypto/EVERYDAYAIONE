"""
data_query 工具执行器

DuckDB 驱动的数据查询工具，支持三种模式：
- 探索模式（不传 sql）：返回文件结构 data_profile
- 查询模式（传 sql）：执行 SQL，四档分层返回
- 导出模式（传 sql + export）：DuckDB COPY TO xlsx，自动上传

安全三层防线：
1. SQL 白名单 + 分号拦截（查询/导出模式只允许单条 SELECT）
2. 路径校验（_check_path_safety 确保文件在用户 workspace 内）
3. lock_configuration=true — 防止 SQL 注入修改 DuckDB 配置

设计文档：docs/document/TECH_data_query工具设计.md
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import duckdb
from loguru import logger

from services.agent.data_query_cache import detect_encoding, detect_file_type

# SQL 危险关键词（查询模式只允许 SELECT）
_DANGEROUS_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|COPY|ATTACH|DETACH|EXPORT|IMPORT|PRAGMA|LOAD|INSTALL)\b",
    re.IGNORECASE,
)

# 查询超时（秒）
_QUERY_TIMEOUT = 30


def _validate_sql(sql: str) -> str | None:
    """校验 SQL 安全性，返回错误信息或 None。"""
    if ";" in sql:
        return "❌ SQL 安全限制：不支持多语句查询（禁止分号）"
    if _DANGEROUS_SQL_PATTERN.search(sql):
        return (
            "❌ SQL 安全限制：data_query 仅支持 SELECT 查询。\n"
            "禁止的操作：INSERT/UPDATE/DELETE/DROP/CREATE/COPY 等。\n"
            "如需导出文件，请使用 export 参数。"
        )
    return None


class DataQueryExecutor:
    """data_query 工具执行器"""

    def __init__(
        self,
        user_id: str,
        org_id: str | None,
        conversation_id: str,
        workspace_root: str,
    ) -> None:
        self.user_id = user_id
        self.org_id = org_id
        self.conversation_id = conversation_id or "default"

        from core.workspace import resolve_workspace_dir, resolve_staging_dir
        self._workspace_dir = resolve_workspace_dir(
            workspace_root, user_id, org_id,
        )
        self._staging_dir = resolve_staging_dir(
            workspace_root, user_id, org_id, self.conversation_id,
        )
        self._output_dir = str(Path(self._workspace_dir) / "下载")

        # schema 收集：执行后由 tool_executor 读取
        self.last_file_meta: tuple[str, str, str] | None = None
        # (filename, abs_path, schema_text)

    async def execute(
        self,
        file: str,
        sql: str | None = None,
        export: str | None = None,
        sheet: str | None = None,
    ) -> str:
        """执行 data_query 工具，分发到三种模式。"""
        if not file or not file.strip():
            return "❌ 参数错误：file 不能为空"

        try:
            abs_path = self._resolve_file_path(file.strip())
        except FileNotFoundError as e:
            return f"❌ {e}"
        except PermissionError as e:
            return f"❌ 安全限制：{e}"

        file_type = detect_file_type(abs_path)
        if file_type == "unknown":
            return f"❌ 不支持的文件格式：{Path(abs_path).suffix}"

        query_path = abs_path
        sheet_names: list[str] | None = None
        if file_type == "excel":
            query_path, sheet_names = await self._ensure_parquet_cache(
                abs_path, sheet,
            )

        if sql is None and export is None:
            result = await self._explore(query_path, abs_path, sheet_names)
        elif export is not None:
            if sql is None:
                sql = "SELECT * FROM data"
            result = await self._export(query_path, sql, export)
        else:
            result = await self._query(query_path, sql)

        # 收集 schema：所有模式都尝试（探索模式用完整 profile，查询/导出用快速 DESCRIBE）
        if not result.startswith("❌"):
            self._collect_schema(Path(abs_path).name, abs_path, query_path)

        return result

    def _collect_schema(
        self, filename: str, original_path: str, query_path: str,
    ) -> None:
        """从已处理的文件收集 schema 信息（毫秒级 DuckDB DESCRIBE）。"""
        try:
            import duckdb
            conn = duckdb.connect(":memory:")
            path_escaped = query_path.replace("'", "''")
            file_type = detect_file_type(query_path)
            if file_type == "parquet":
                read_fn = f"read_parquet('{path_escaped}')"
            elif file_type == "csv":
                read_fn = f"read_csv_auto('{path_escaped}')"
            else:
                return  # Excel 已转 Parquet，不会到这里
            rows = conn.execute(f"DESCRIBE SELECT * FROM {read_fn}").fetchall()
            conn.close()
            if not rows:
                return
            # 行数：Parquet 从文件头元数据读（微秒级），CSV 用 DuckDB COUNT
            if file_type == "parquet":
                import pyarrow.parquet as pq
                row_count = pq.read_metadata(query_path).num_rows
            else:
                conn2 = duckdb.connect(":memory:")
                row_count = (conn2.execute(
                    f"SELECT COUNT(*) FROM {read_fn}"
                ).fetchone() or (0,))[0]
                conn2.close()
            col_parts = [f"{r[0]}({r[1].lower()})" for r in rows]
            schema_text = (
                f"{filename} | {row_count:,}行 × {len(rows)}列\n"
                f"列: {', '.join(col_parts)}"
            )
            self.last_file_meta = (filename, original_path, schema_text)
        except Exception:
            pass  # schema 收集失败不影响主流程

    # ── 路径解析 ──────────────────────────────────────

    def _resolve_file_path(self, file_input: str) -> str:
        """缓存优先 → 文件系统兜底。

        解析顺序：
        1. 对话级文件缓存（file_list 注册的 文件名→绝对路径 映射，含去空格匹配）
        2. workspace / staging 目录直接查找（未经 file_list 的文件）
        """
        # ── 第一优先：对话级缓存（精确匹配 + 去空格匹配）──
        from services.agent.workspace_file_handles import get_file_cache
        cache = get_file_cache(self.conversation_id)
        cached_path = cache.resolve(file_input)
        if cached_path:
            candidate = Path(cached_path)
            if candidate.exists() and candidate.is_file():
                self._check_path_safety(candidate)
                return str(candidate.resolve())

        # ── 兜底：文件系统直接查找 ──
        ws = Path(self._workspace_dir)
        staging = Path(self._staging_dir)

        for base in (ws, staging):
            candidate = base / file_input
            if candidate.exists() and candidate.is_file():
                self._check_path_safety(candidate)
                return str(candidate.resolve())

        if file_input.startswith("staging/"):
            candidate = ws / file_input
            if candidate.exists() and candidate.is_file():
                self._check_path_safety(candidate)
                return str(candidate.resolve())

        raise FileNotFoundError(f"文件 '{file_input}' 不存在。请检查文件名是否正确。")

    def _check_path_safety(self, candidate: Path) -> None:
        """禁止符号链接 + 路径必须在用户 workspace 内。

        检查顺序：先检查 symlink（原始路径），再 resolve 检查边界。
        """
        if candidate.is_symlink():
            raise PermissionError("不允许访问符号链接")
        resolved = candidate.resolve()
        ws_root = Path(self._workspace_dir).resolve()
        try:
            resolved.relative_to(ws_root)
        except ValueError:
            raise PermissionError("路径越界：不允许访问 workspace 外的文件")

    # ── Excel → Parquet 缓存（委托 data_query_cache） ─

    async def _ensure_parquet_cache(
        self, excel_path: str, sheet: str | None,
    ) -> tuple[str, list[str] | None]:
        from services.agent.data_query_cache import ensure_parquet_cache
        return await ensure_parquet_cache(excel_path, sheet, self._staging_dir)

    # ── DuckDB 安全连接 ──────────────────────────────

    def _create_safe_connection(self) -> duckdb.DuckDBPyConnection:
        """创建 :memory: 连接 + 三层安全配置。

        安全三件套（顺序不可变）：
        1. allowed_directories — 只允许访问用户 workspace
        2. enable_external_access=false — 禁止 SQL 中使用 read_csv/read_parquet 读任意文件
        3. lock_configuration=true — 锁死配置，SQL 注入无法解锁
        """
        con = duckdb.connect(":memory:")
        con.execute("SET memory_limit = '256MB'")
        con.execute("SET threads = 2")
        # 安全三件套（必须在 lock_configuration 之前设置）
        ws_escaped = self._workspace_dir.replace("'", "''")
        con.execute(f"SET allowed_directories = ['{ws_escaped}']")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        return con

    def _create_view(
        self, con: duckdb.DuckDBPyConnection, file_path: str,
    ) -> None:
        """CREATE TEMP VIEW data 指向目标文件。"""
        file_type = detect_file_type(file_path)
        escaped = file_path.replace("'", "''")

        if file_type == "csv":
            encoding = detect_encoding(file_path)
            enc_clause = f", encoding='{encoding}'" if encoding.lower() not in ("utf-8", "ascii") else ""
            con.execute(
                f"CREATE TEMP VIEW data AS SELECT * FROM "
                f"read_csv('{escaped}', auto_detect=true{enc_clause})"
            )
        else:
            con.execute(
                f"CREATE TEMP VIEW data AS SELECT * FROM read_parquet('{escaped}')"
            )

    @staticmethod
    def _get_column_names(con: duckdb.DuckDBPyConnection) -> list[str]:
        try:
            desc = con.execute("SELECT * FROM data LIMIT 0").description
            return [d[0] for d in desc] if desc else []
        except Exception:
            return []

    @staticmethod
    def _execute_with_timeout(
        con: duckdb.DuckDBPyConnection, sql: str, timeout: float = _QUERY_TIMEOUT,
    ) -> duckdb.DuckDBPyRelation:
        """执行 SQL 并在 timeout 秒后 interrupt。

        复用 duckdb_engine.py 的看门狗模式。

        Raises:
            TimeoutError: 查询超时
        """
        interrupted = threading.Event()

        def watchdog() -> None:
            interrupted.set()
            try:
                con.interrupt()
            except Exception:
                pass

        timer = threading.Timer(timeout, watchdog)
        timer.start()
        try:
            result = con.execute(sql)
            return result
        except Exception as e:
            if interrupted.is_set() or "interrupt" in type(e).__name__.lower():
                raise TimeoutError(
                    f"查询超时（{timeout:.0f}s），请简化 SQL 或缩小数据范围"
                ) from e
            raise
        finally:
            timer.cancel()

    # ── 探索模式 ──────────────────────────────────────

    async def _explore(
        self, query_path: str, original_path: str,
        sheet_names: list[str] | None,
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._explore_sync, query_path, original_path, sheet_names,
        )

    def _explore_sync(
        self, query_path: str, original_path: str,
        sheet_names: list[str] | None,
    ) -> str:
        from services.agent.data_profile import build_profile_from_duckdb

        filename = Path(original_path).name
        file_size_kb = Path(query_path).stat().st_size / 1024
        file_type = detect_file_type(query_path)
        start = time.monotonic()

        if file_type == "parquet":
            from core.duckdb_engine import get_duckdb_engine
            profile = get_duckdb_engine().profile_parquet(query_path)
        else:
            profile = self._profile_via_view(query_path)

        elapsed = time.monotonic() - start
        text, _ = build_profile_from_duckdb(profile, filename, file_size_kb, elapsed)

        if sheet_names:
            text += f"\n\n[Sheet 列表] {', '.join(sheet_names)}"
            text += "\n使用 sheet 参数指定：data_query(file=\"...\", sheet=\"Sheet2\")"
        return text

    def _profile_via_view(self, file_path: str) -> dict:
        """用独立连接对非 Parquet 文件做 SUMMARIZE profiling。"""
        from core.duckdb_engine import DuckDBEngine

        con = self._create_safe_connection()
        try:
            self._create_view(con, file_path)
            row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]

            try:
                summary_rows = con.execute("SUMMARIZE SELECT * FROM data").fetchall()
                summary_desc = con.description
            except Exception:
                return {"columns": [], "row_count": row_count, "top_values": {}}

            _, columns_info, _, _ = DuckDBEngine.parse_summarize_rows(
                summary_rows, summary_desc,
            )

            preview_rows: list[dict] = []
            try:
                preview_rows = con.execute(
                    "SELECT * FROM data LIMIT 3"
                ).fetchdf().to_dict("records")
            except Exception:
                pass

            return {
                "columns": columns_info,
                "row_count": row_count,
                "top_values": {},
                "duplicate_count": 0,
                "preview_rows": preview_rows,
            }
        finally:
            con.close()

    # ── 查询模式 ──────────────────────────────────────

    async def _query(self, query_path: str, sql: str) -> str:
        err = _validate_sql(sql)
        if err:
            return err
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._query_sync, query_path, sql)

    def _query_sync(self, query_path: str, sql: str) -> str:
        """查询模式核心：COPY TO Parquet → metadata 读行数 → 按需取数据。

        全程数据不经过 Python 内存（DuckDB 流式写盘），
        小结果（≤100 行）才从 Parquet 读回来格式化。
        """
        from services.agent.data_query_format import (
            format_full_result, format_large_result_from_parquet,
            format_numeric_summary, format_sql_error,
        )

        con = self._create_safe_connection()
        staging = Path(self._staging_dir)
        staging.mkdir(parents=True, exist_ok=True)
        import uuid as _uuid
        result_file = f"query_result_{int(time.time())}_{_uuid.uuid4().hex[:6]}.parquet"
        result_path = staging / result_file
        result_escaped = str(result_path).replace("'", "''")

        try:
            self._create_view(con, query_path)
            columns = self._get_column_names(con)
            start = time.monotonic()

            # 1. SQL 结果直接写 Parquet（流式，内存恒定 ~15MB）
            try:
                self._execute_with_timeout(
                    con,
                    f"COPY ({sql}) TO '{result_escaped}' "
                    f"(FORMAT PARQUET, COMPRESSION SNAPPY)",
                    _QUERY_TIMEOUT,
                )
            except TimeoutError as e:
                result_path.unlink(missing_ok=True)
                return f"❌ {e}"
            except (
                duckdb.InvalidInputException, duckdb.CatalogException,
                duckdb.BinderException, duckdb.ParserException,
            ) as e:
                result_path.unlink(missing_ok=True)
                return format_sql_error(str(e), columns)
            except Exception as e:
                result_path.unlink(missing_ok=True)
                return format_sql_error(str(e), columns)

            # 2. 从 Parquet metadata 读行数（零内存，不加载数据）
            row_count = con.execute(
                f"SELECT num_rows::BIGINT FROM parquet_file_metadata('{result_escaped}')"
            ).fetchone()[0]
            elapsed = time.monotonic() - start

            # 3. 0 行
            if row_count == 0:
                result_path.unlink(missing_ok=True)
                return "查询成功，结果为空（0 行匹配条件）。"

            # 4. 小结果（≤100 行）：从 Parquet 读回 Python，返回完整表格
            if row_count <= 100:
                import pandas as pd
                df = pd.read_parquet(str(result_path))
                result_path.unlink(missing_ok=True)  # 小结果不保留 staging

                if row_count <= 10:
                    return format_full_result(df, row_count, elapsed)
                table = format_full_result(df, row_count, elapsed)
                summary = format_numeric_summary(df)
                return f"{table}\n\n{summary}" if summary else table

            # 5. 大结果（>100 行）：数据已在 staging，从 Parquet 取预览和统计
            text = format_large_result_from_parquet(
                con, result_escaped, result_file, row_count, elapsed,
            )
            if row_count > 1000:
                text += "\n\n💡 结果超过 1000 行，建议缩小查询范围或使用 export 导出。"
            return text
        finally:
            con.close()

    # ── 导出模式 ──────────────────────────────────────

    async def _export(
        self, query_path: str, sql: str, export_filename: str,
    ) -> str:
        err = _validate_sql(sql)
        if err:
            return err

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._export_sync, query_path, sql, export_filename,
        )

        if result.startswith("✅"):
            from services.file_upload import auto_upload
            file_path = Path(self._output_dir) / Path(export_filename).name
            if file_path.exists():
                return await auto_upload(
                    filename=file_path.name,
                    size=file_path.stat().st_size,
                    output_dir=self._output_dir,
                    user_id=self.user_id,
                    org_id=self.org_id,
                )
        return result

    def _export_sync(
        self, query_path: str, sql: str, export_filename: str,
    ) -> str:
        con = self._create_safe_connection()
        try:
            # spatial 扩展（xlsx 导出需要，LOAD 不受 lock_configuration 影响）
            try:
                con.execute("LOAD spatial")
            except Exception:
                try:
                    con.execute("INSTALL spatial; LOAD spatial;")
                except Exception as e:
                    logger.warning(f"spatial extension load failed: {e}")
                    if export_filename.endswith(".xlsx"):
                        return "❌ xlsx 导出需要 DuckDB spatial 扩展，请改用 .csv 格式导出"

            self._create_view(con, query_path)

            output_dir = Path(self._output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = Path(export_filename).name
            output_path = output_dir / safe_name
            if output_path.is_symlink():
                return "❌ 安全限制：输出路径不允许是符号链接"
            output_escaped = str(output_path).replace("'", "''")

            ext = Path(safe_name).suffix.lower()
            if ext == ".xlsx":
                copy_sql = f"COPY ({sql}) TO '{output_escaped}' WITH (FORMAT GDAL, DRIVER 'xlsx')"
            elif ext == ".csv":
                copy_sql = f"COPY ({sql}) TO '{output_escaped}' WITH (FORMAT CSV, HEADER true)"
            elif ext == ".parquet":
                copy_sql = f"COPY ({sql}) TO '{output_escaped}' (FORMAT PARQUET, COMPRESSION SNAPPY)"
            else:
                return f"❌ 不支持的导出格式：{ext}。支持 .xlsx / .csv / .parquet"

            try:
                self._execute_with_timeout(con, copy_sql, _QUERY_TIMEOUT * 2)
            except TimeoutError as e:
                Path(output_path).unlink(missing_ok=True)
                return f"❌ {e}"

            size_kb = output_path.stat().st_size / 1024
            logger.info(f"data_query export | file={safe_name} size={size_kb:.0f}KB")
            return f"✅ 导出完成: {safe_name}（{size_kb:.0f}KB）"
        except Exception as e:
            logger.error(f"data_query export error: {e}")
            return f"❌ 导出失败：{e}"
        finally:
            con.close()
