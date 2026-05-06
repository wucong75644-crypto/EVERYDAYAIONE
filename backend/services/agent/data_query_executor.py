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

from services.agent.agent_result import AgentResult
from services.agent.data_query_cache import detect_encoding, detect_file_type

# SQL 危险关键词（查询模式只允许 SELECT）
_DANGEROUS_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|COPY|ATTACH|DETACH|EXPORT|IMPORT|PRAGMA|LOAD|INSTALL)\b",
    re.IGNORECASE,
)

# 查询超时（秒）
_QUERY_TIMEOUT = 30


def _validate_sql(sql: str) -> AgentResult | None:
    """校验 SQL 安全性，返回 AgentResult 错误或 None（通过）。"""
    if ";" in sql:
        return AgentResult(
            summary="SQL 安全限制：不支持多语句查询（禁止分号）",
            status="error",
            error_message="SQL validation: semicolons not allowed",
            metadata={"retryable": True},
        )
    if _DANGEROUS_SQL_PATTERN.search(sql):
        return AgentResult(
            summary=(
                "SQL 安全限制：data_query 仅支持 SELECT 查询。\n"
                "禁止的操作：INSERT/UPDATE/DELETE/DROP/CREATE/COPY 等。\n"
                "如需导出文件，请使用 export 参数。"
            ),
            status="error",
            error_message="SQL validation: only SELECT allowed",
            metadata={"retryable": True},
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
    ) -> AgentResult:
        """执行 data_query 工具，分发到探索/查询两种模式。"""
        if not file or not file.strip():
            return AgentResult(
                summary="参数错误：file 不能为空",
                status="error",
                error_message="Validation: file is required",
                metadata={"retryable": True},
            )

        try:
            abs_path = self._resolve_file_path(file.strip())
        except FileNotFoundError as e:
            return AgentResult(
                summary=str(e),
                status="error",
                error_message=f"FileNotFoundError: {e}",
                metadata={"retryable": True},
            )
        except PermissionError as e:
            return AgentResult(
                summary=f"安全限制：{e}",
                status="error",
                error_message=f"PermissionError: {e}",
                metadata={"retryable": False},
            )

        file_type = detect_file_type(abs_path)
        if file_type == "unknown":
            return AgentResult(
                summary=f"不支持的文件格式：{Path(abs_path).suffix}",
                status="error",
                error_message=f"Unsupported format: {Path(abs_path).suffix}",
                metadata={"retryable": False},
            )

        query_path = abs_path
        sheet_names: list[str] | None = None
        if file_type == "excel":
            query_path, sheet_names = await self._ensure_parquet_cache(
                abs_path, sheet,
            )

        # export 参数已废弃——导出给用户统一走 code_execute
        if export is not None:
            return AgentResult(
                summary=(
                    "data_query 不支持 export 参数。\n"
                    "导出文件请用 code_execute：\n"
                    "  df = pd.read_parquet(STAGING_DIR + '/文件名')\n"
                    "  df.to_excel(OUTPUT_DIR + '/报表.xlsx', index=False, engine='xlsxwriter')"
                ),
                status="error",
                error_message="export parameter removed, use code_execute",
                metadata={"retryable": True},
            )

        if sql is None:
            result = await self._explore(query_path, abs_path, sheet_names)
        else:
            # L1 归一化：NFKC 全角→半角（中文逗号→英文逗号等）
            from services.agent.input_normalizer import InputNormalizer
            sql = InputNormalizer.normalize(sql) or sql
            result = await self._query(query_path, sql)

        # 收集 schema：成功时尝试（探索模式用完整 profile，查询/导出用快速 DESCRIBE）
        if not result.is_failure:
            self._collect_schema(
                Path(abs_path).name, abs_path, query_path, sheet_names,
            )

        # Excel→Parquet 缓存路径 + schema 提示（让 code_execute 可直接写代码）
        if not result.is_failure and query_path != abs_path:
            cache_name = Path(query_path).name
            cache_schema = ""
            try:
                import duckdb as _dq
                _con = _dq.connect(":memory:")
                _escaped = query_path.replace("'", "''")
                pq_cols = _con.execute(
                    f"SELECT column_name, data_type FROM parquet_schema('{_escaped}')"
                ).fetchall()
                pq_rows = _con.execute(
                    f"SELECT num_rows::BIGINT FROM parquet_file_metadata('{_escaped}')"
                ).fetchone()[0]
                _con.close()
                col_preview = ", ".join(f"{n}({t})" for n, t in pq_cols[:15])
                if len(pq_cols) > 15:
                    col_preview += f" (+{len(pq_cols)-15}列)"
                cache_schema = f" | {pq_rows}行 × {len(pq_cols)}列\n[列: {col_preview}]"
            except Exception:
                pass
            result.summary += (
                f"\n[完整数据] pd.read_parquet(STAGING_DIR + '/{cache_name}'){cache_schema}"
            )

        return result

    def _collect_schema(
        self, filename: str, original_path: str, query_path: str,
        sheet_names: list[str] | None = None,
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
            if sheet_names and len(sheet_names) > 1:
                preview = sheet_names[:5]
                more = f" ...等共{len(sheet_names)}个Sheet" if len(sheet_names) > 5 else ""
                schema_text += (
                    f"\n[多Sheet Excel] {', '.join(preview)}{more}"
                    f"\n注意: 当前schema仅为第1个Sheet，其他Sheet可能包含不同数据，"
                    f"用 data_query(sheet=\"SheetName\") 切换"
                )
            self.last_file_meta = (filename, original_path, schema_text)
        except Exception:
            pass  # schema 收集失败不影响主流程

    # ── 路径解析 ──────────────────────────────────────

    def _resolve_file_path(self, file_input: str) -> str:
        """缓存优先 → 直接查找 → 递归搜索兜底。

        解析顺序：
        1. 对话级文件缓存（file_list 注册的 文件名→绝对路径 映射，含去空格匹配）
        2. workspace / staging 目录直接查找（未经 file_list 的文件）
        3. workspace 内递归搜索（LLM 只传文件名但文件在子目录时）
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

        # ── 第二优先：文件系统直接查找 ──
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

        # ── 第三优先：workspace 内递归搜索（子目录文件兜底，限制扫描量）──
        filename = Path(file_input).name
        matches: list[Path] = []
        _MAX_SCAN = 1000  # 最多扫描 1000 个文件，防止大工作区卡住
        scanned = 0
        for p in ws.rglob(filename):
            scanned += 1
            if scanned > _MAX_SCAN:
                break
            if p.is_file() and "staging" not in p.relative_to(ws).parts:
                matches.append(p)
                if len(matches) > 5:
                    break  # 同名文件太多，无需继续
        if len(matches) == 1:
            self._check_path_safety(matches[0])
            return str(matches[0].resolve())
        if len(matches) > 1:
            paths = [str(m.relative_to(ws)) for m in matches[:5]]
            raise FileNotFoundError(
                f"找到多个同名文件 '{filename}'，请指定完整路径：\n"
                + "\n".join(f"  - {p}" for p in paths)
            )

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
    ) -> AgentResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._explore_sync, query_path, original_path, sheet_names,
        )

    def _explore_sync(
        self, query_path: str, original_path: str,
        sheet_names: list[str] | None,
    ) -> AgentResult:
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

        # 附加可用路径
        try:
            rel_path = str(Path(original_path).relative_to(self._workspace_dir))
        except ValueError:
            rel_path = filename

        if sheet_names and len(sheet_names) > 1:
            # 多 sheet：扫描结构，给出概览
            from services.agent.data_query_cache import (
                scan_sheet_structures, detect_same_structure,
            )
            try:
                structures = scan_sheet_structures(original_path)
            except Exception:
                structures = [{"name": n, "columns": [], "row_count": 0} for n in sheet_names]

            same = detect_same_structure(structures)
            text += f"\n\n[Sheet 概览] 共 {len(structures)} 个 Sheet"

            if same:
                # 结构相同：显示公共列 + 各 sheet 行数
                total_rows = sum(s["row_count"] for s in structures)
                cols_preview = ", ".join(structures[0]["columns"][:8])
                if len(structures[0]["columns"]) > 8:
                    cols_preview += f" (+{len(structures[0]['columns'])-8}列)"
                names_preview = ", ".join(s["name"] for s in structures[:5])
                if len(structures) > 5:
                    names_preview += f" ... 等{len(structures)}个"
                text += f"\n- 结构相同（{len(structures)}个）: {names_preview}"
                text += f"\n- 每个 Sheet {structures[0]['row_count']}行 × {len(structures[0]['columns'])}列 | 合计 {total_rows:,}行"
                text += f"\n- 列: {cols_preview}"
                text += f"\n- 合并读取: data_query(file=\"{rel_path}\", sheet=\"*\")"
                text += f"\n- 单个读取: data_query(file=\"{rel_path}\", sheet=\"{structures[0]['name']}\")"
            else:
                # 结构不同：逐个显示
                for s in structures[:10]:
                    cols = ", ".join(s["columns"][:5])
                    if len(s["columns"]) > 5:
                        cols += f" (+{len(s['columns'])-5}列)"
                    text += f"\n- \"{s['name']}\" | {s['row_count']}行 × {len(s['columns'])}列 | 列: {cols}"
                if len(structures) > 10:
                    text += f"\n- ... 等{len(structures)}个 Sheet"
                text += f"\n- 读取指定 Sheet: data_query(file=\"{rel_path}\", sheet=\"Sheet名\")"
        else:
            text += (
                f"\n\n后续操作:"
                f"\n- 查询数据: data_query(file=\"{rel_path}\", sql=\"SELECT ... FROM data\")"
                f"\n- 全量读取: data_query(file=\"{rel_path}\", sql=\"SELECT * FROM data\")"
            )

        return AgentResult(summary=text, status="success")

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

    async def _query(self, query_path: str, sql: str) -> AgentResult:
        err = _validate_sql(sql)
        if err:
            return err
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._query_sync, query_path, sql)

    def _query_sync(self, query_path: str, sql: str) -> AgentResult:
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
                return AgentResult(
                    summary=str(e),
                    status="timeout",
                    error_message=f"TimeoutError: {e}",
                )
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
                return AgentResult(
                    summary="查询成功，结果为空（0 行匹配条件）。",
                    status="empty",
                )

            # 4. 小结果（≤100 行）：从 Parquet 读回 Python，返回完整表格 + 保留 staging
            if row_count <= 100:
                import pandas as pd
                df = pd.read_parquet(str(result_path))
                # 保留 staging 文件（确保 code_execute 后续能读到）

                if row_count <= 10:
                    text = format_full_result(df, row_count, elapsed)
                else:
                    table = format_full_result(df, row_count, elapsed)
                    summary = format_numeric_summary(df)
                    text = f"{table}\n\n{summary}" if summary else table

                # 附加 staging 引用 + schema（让 code_execute 直接写代码）
                size_kb = result_path.stat().st_size / 1024
                col_schema = ", ".join(
                    f"{c}({df[c].dtype})" for c in list(df.columns)[:15]
                )
                if len(df.columns) > 15:
                    col_schema += f" (+{len(df.columns)-15}列)"
                text += (
                    f"\n\n[文件已存入 staging | "
                    f"读取: pd.read_parquet(STAGING_DIR + '/{result_file}') | "
                    f"{row_count}行 × {len(df.columns)}列 | parquet | {size_kb:.0f}KB]\n"
                    f"[列: {col_schema}]"
                )
                return AgentResult(summary=text, status="success")

            # 5. 大结果（>100 行）：数据已在 staging，从 Parquet 取预览和统计
            text = format_large_result_from_parquet(
                con, result_escaped, result_file, row_count, elapsed,
            )
            # 附加标准 staging 引用 + schema
            size_kb = result_path.stat().st_size / 1024
            try:
                pq_cols = con.execute(
                    f"SELECT column_name, data_type FROM parquet_schema('{result_escaped}')"
                ).fetchall()
                col_schema = ", ".join(
                    f"{name}({dtype})" for name, dtype in pq_cols[:15]
                )
                if len(pq_cols) > 15:
                    col_schema += f" (+{len(pq_cols)-15}列)"
                col_count = len(pq_cols)
            except Exception:
                col_schema = ""
                col_count = 0
            text += (
                f"\n\n[文件已存入 staging | "
                f"读取: pd.read_parquet(STAGING_DIR + '/{result_file}') | "
                f"{row_count}行 × {col_count}列 | parquet | {size_kb:.0f}KB]"
            )
            if col_schema:
                text += f"\n[列: {col_schema}]"
            if row_count > 1000:
                text += "\n\n💡 结果超过 1000 行，建议缩小查询范围或使用 export 导出。"
            return AgentResult(summary=text, status="success")
        finally:
            con.close()

    # ── 导出模式 ──────────────────────────────────────

    async def _export(
        self, query_path: str, sql: str, export_filename: str,
    ) -> AgentResult:
        err = _validate_sql(sql)
        if err:
            return err

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._export_sync, query_path, sql, export_filename,
        )

        if not result.is_failure:
            from services.file_upload import auto_upload
            file_path = Path(self._output_dir) / Path(export_filename).name
            if file_path.exists():
                upload_text = await auto_upload(
                    filename=file_path.name,
                    size=file_path.stat().st_size,
                    output_dir=self._output_dir,
                    user_id=self.user_id,
                    org_id=self.org_id,
                )
                # auto_upload 尚未结构化（Phase 2），用前缀判断成功/失败
                if upload_text.startswith("❌"):
                    return AgentResult(
                        summary=upload_text.lstrip("❌ "),
                        status="error",
                        error_message=upload_text,
                        metadata={"retryable": False},
                    )
                return AgentResult(summary=upload_text, status="success")
        return result

    def _create_export_connection(self, need_excel: bool = False) -> duckdb.DuckDBPyConnection:
        """创建导出专用连接（扩展必须在 lock_configuration 之前加载）"""
        con = duckdb.connect(":memory:")
        con.execute("SET memory_limit = '256MB'")
        con.execute("SET threads = 2")
        # xlsx 扩展必须在 lock 之前加载
        if need_excel:
            try:
                con.execute("LOAD excel")
            except Exception:
                con.execute("INSTALL excel; LOAD excel;")
        ws_escaped = self._workspace_dir.replace("'", "''")
        con.execute(f"SET allowed_directories = ['{ws_escaped}']")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        return con

    def _export_sync(
        self, query_path: str, sql: str, export_filename: str,
    ) -> AgentResult:
        need_excel = export_filename.endswith(".xlsx")
        try:
            con = self._create_export_connection(need_excel=need_excel)
        except Exception as e:
            logger.warning(f"excel extension load failed: {e}")
            return AgentResult(
                summary="xlsx 导出需要 DuckDB excel 扩展，加载失败，请改用 .csv 格式导出",
                status="error",
                error_message=f"excel extension unavailable: {e}",
                metadata={"retryable": False},
            )
        try:
            self._create_view(con, query_path)

            output_dir = Path(self._output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = Path(export_filename).name
            output_path = output_dir / safe_name
            if output_path.is_symlink():
                return AgentResult(
                    summary="安全限制：输出路径不允许是符号链接",
                    status="error",
                    error_message="Security: symlink output path",
                    metadata={"retryable": False},
                )
            output_escaped = str(output_path).replace("'", "''")

            ext = Path(safe_name).suffix.lower()
            if ext == ".xlsx":
                copy_sql = f"COPY ({sql}) TO '{output_escaped}' (FORMAT XLSX)"
            elif ext == ".csv":
                copy_sql = f"COPY ({sql}) TO '{output_escaped}' WITH (FORMAT CSV, HEADER true)"
            elif ext == ".parquet":
                # parquet 是中间数据格式，不应导出给用户
                # 查询模式自动存 staging parquet，用 sql 参数即可
                return AgentResult(
                    summary=(
                        "parquet 是中间数据格式，不支持导出给用户。\n"
                        "- 给用户下载：改用 export=\"报表.xlsx\" 或 export=\"数据.csv\"\n"
                        "- 中间计算用：不传 export，用 sql 参数查询（结果自动存 staging）"
                    ),
                    status="error",
                    error_message="Export: parquet not allowed for user download",
                    metadata={"retryable": True},
                )
            else:
                return AgentResult(
                    summary=f"不支持的导出格式：{ext}。支持 .xlsx / .csv",
                    status="error",
                    error_message=f"Unsupported export format: {ext}",
                    metadata={"retryable": True},
                )

            try:
                self._execute_with_timeout(con, copy_sql, _QUERY_TIMEOUT * 2)
            except TimeoutError as e:
                Path(output_path).unlink(missing_ok=True)
                return AgentResult(
                    summary=str(e),
                    status="timeout",
                    error_message=f"Export timeout: {e}",
                )

            size_kb = output_path.stat().st_size / 1024
            logger.info(f"data_query export | file={safe_name} size={size_kb:.0f}KB")
            return AgentResult(
                summary=f"导出完成: {safe_name}（{size_kb:.0f}KB）",
                status="success",
            )
        except Exception as e:
            logger.error(f"data_query export error: {e}")
            return AgentResult(
                summary=f"导出失败：{e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )
        finally:
            con.close()
