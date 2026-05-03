"""
DuckDB 导出引擎 — 进程级单例，直连 PG 流式导出 Parquet。

职责：接收 SQL → 直连 PG 拉数据 → 流式写 Parquet 到指定路径
不负责：路径解析、字段验证、PII 脱敏（由调用方在 SQL 中处理）

失败策略：重试 + 自动重连（不降级，与大厂 OLAP 引擎一致）

设计文档：docs/document/TECH_DuckDB导出引擎.md

用法：
    engine = get_duckdb_engine()
    result = engine.export_to_parquet(query, output_path)
"""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb
from loguru import logger

_lock = threading.Lock()
_engine: DuckDBEngine | None = None

# 连接失败重试次数（网络抖动通常重试一次即可恢复）
_MAX_RETRIES = 2


class DuckDBEngine:
    """嵌入式 DuckDB 实例，ATTACH PG 后通过 COPY TO 流式导出 Parquet。"""

    def __init__(
        self, pg_url: str, memory_limit: str = "256MB", threads: int = 2,
    ):
        self._pg_url = pg_url
        self._memory_limit = memory_limit
        self._threads = threads
        self._conn: duckdb.DuckDBPyConnection | None = None

    # ── 连接管理 ──────────────────────────────────────

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        """懒初始化 DuckDB 连接（含 PG 扩展加载）。"""
        if self._conn is not None:
            return self._conn

        conn = duckdb.connect()
        conn.execute(f"SET memory_limit = '{self._memory_limit}'")
        conn.execute(f"SET threads = {self._threads}")

        conn.execute("INSTALL postgres; LOAD postgres;")
        conn.execute(
            f"ATTACH '{self._pg_url}' AS pg (TYPE postgres, READ_ONLY)"
        )

        self._conn = conn
        logger.info(
            f"DuckDB engine initialized | "
            f"memory={self._memory_limit} threads={self._threads}"
        )
        return conn

    def _reset_conn(self) -> None:
        """销毁当前连接，下次 _get_conn 会重建。"""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            logger.warning("DuckDB connection reset for reconnect")

    # ── 核心导出 ──────────────────────────────────────

    def export_to_parquet(
        self, query: str, output_path: str | Path,
        timeout: float = 25.0,
    ) -> dict[str, int | float | str]:
        """
        执行 SELECT 查询，流式直写 Parquet。

        超时机制：用 threading.Timer + conn.interrupt() 在 timeout 秒后
        中断 DuckDB 查询，释放连接，避免僵尸线程阻塞后续请求。

        失败时自动重连 + 重试（最多 2 次），不降级到旧逻辑。

        Args:
            query: 完整的 SELECT SQL（表名需带 pg.public. 前缀）
            output_path: Parquet 输出路径（写到 staging 目录）
            timeout: 单次查询超时秒数（默认 25s，留 5s 给 ERPAgent 30s 预算）

        Returns:
            {"row_count": int, "size_kb": float, "path": str}

        Raises:
            TimeoutError: 查询超时（被 interrupt 中断）
            Exception: 重试耗尽后抛出原始异常
        """
        output = str(output_path)
        output_escaped = output.replace("'", "''")
        Path(output).parent.mkdir(parents=True, exist_ok=True)

        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            interrupted = threading.Event()
            timer: threading.Timer | None = None
            try:
                conn = self._get_conn()
                # 看门狗：timeout 秒后中断查询，释放连接
                timer = threading.Timer(
                    timeout, self._interrupt_conn, args=(conn, interrupted),
                )
                timer.start()
                conn.execute(f"""
                    COPY ({query}) TO '{output_escaped}' (
                        FORMAT PARQUET,
                        COMPRESSION SNAPPY,
                        ROW_GROUP_SIZE 100000
                    )
                """)

                # 从 parquet 文件元数据读行数（不重新扫描文件内容）
                meta = conn.execute(
                    f"SELECT num_rows::BIGINT "
                    f"FROM parquet_file_metadata('{output_escaped}')"
                ).fetchone()
                row_count = meta[0] if meta else 0
                size_kb = Path(output).stat().st_size / 1024

                logger.info(
                    f"DuckDB export done | rows={row_count:,} "
                    f"size={size_kb:.0f}KB path={output}"
                )
                return {
                    "row_count": row_count, "size_kb": size_kb, "path": output,
                }

            except Exception as e:
                is_interrupt = (
                    interrupted.is_set()
                    or "interrupt" in type(e).__name__.lower()
                )
                if is_interrupt:
                    # 超时：查询被看门狗中断，不重试（重跑大概率还是超时）
                    logger.warning(
                        f"DuckDB export interrupted by timeout ({timeout}s)"
                    )
                    self._reset_conn()
                    Path(output).unlink(missing_ok=True)
                    raise TimeoutError(
                        f"导出超时（{timeout:.0f}秒），请缩小查询范围后重试"
                    ) from e

                # 非超时错误：重连 + 重试
                logger.warning(
                    f"DuckDB export attempt {attempt + 1}/{_MAX_RETRIES} "
                    f"failed | error={e}"
                )
                self._reset_conn()
                Path(output).unlink(missing_ok=True)
                last_err = e
            finally:
                if timer is not None:
                    timer.cancel()

        raise last_err  # type: ignore[misc]

    @staticmethod
    def _interrupt_conn(
        conn: duckdb.DuckDBPyConnection, event: threading.Event,
    ) -> None:
        """看门狗回调：中断 DuckDB 查询并标记。"""
        event.set()
        try:
            conn.interrupt()
            logger.warning("DuckDB query interrupted by watchdog timer")
        except Exception as e:
            logger.debug(f"DuckDB interrupt failed (conn may be closed): {e}")

    # ── Parquet 统计（v6: 导出后摘要，不加载到 Python 内存）──

    def profile_parquet(self, parquet_path: str | Path) -> dict:
        """直接从 parquet 文件算统计摘要（DuckDB 列式扫描，内存 ≈ 0）。

        Returns:
            {
                "columns": [{"name", "type", "null_count", "distinct_count",
                             "min", "max", "avg", "median", "p25", "p75"}...],
                "row_count": int,
                "top_values": {col_name: [{"value", "count"}...]}  # 低基数列
            }
        """
        path_escaped = str(parquet_path).replace("'", "''")
        conn = self._get_conn()

        # 1. SUMMARIZE 拿基础统计（count/min/max/avg/std/null%/unique）
        try:
            summary_rows = conn.execute(
                f"SUMMARIZE SELECT * FROM read_parquet('{path_escaped}')"
            ).fetchall()
            summary_desc = conn.description  # column names
        except Exception as e:
            logger.warning(f"DuckDB SUMMARIZE failed: {e}")
            return {"columns": [], "row_count": 0, "top_values": {}}

        row_count, columns_info, numeric_cols, text_cols_low_card = (
            DuckDBEngine.parse_summarize_rows(summary_rows, summary_desc)
        )

        # 2. 数值列补查 sum / median / p25 / p75
        for col_name in numeric_cols[:5]:
            try:
                q = conn.execute(
                    f"SELECT SUM(\"{col_name}\"), "
                    f"MEDIAN(\"{col_name}\"), "
                    f"QUANTILE_CONT(\"{col_name}\", 0.25), "
                    f"QUANTILE_CONT(\"{col_name}\", 0.75) "
                    f"FROM read_parquet('{path_escaped}')"
                ).fetchone()
                if q:
                    for info in columns_info:
                        if info["name"] == col_name:
                            info["sum"] = float(q[0]) if q[0] is not None else None
                            info["median"] = float(q[1]) if q[1] is not None else None
                            info["p25"] = float(q[2]) if q[2] is not None else None
                            info["p75"] = float(q[3]) if q[3] is not None else None
                            break
            except Exception as e:
                logger.debug(f"DuckDB percentile query failed for {col_name}: {e}")

        # 3. 低基数文本列 top-5
        top_values: dict[str, list[dict]] = {}
        for col_name in text_cols_low_card[:5]:
            try:
                rows = conn.execute(
                    f"SELECT \"{col_name}\", COUNT(*) as cnt "
                    f"FROM read_parquet('{path_escaped}') "
                    f"WHERE \"{col_name}\" IS NOT NULL "
                    f"GROUP BY \"{col_name}\" ORDER BY cnt DESC LIMIT 5"
                ).fetchall()
                top_values[col_name] = [
                    {"value": str(r[0]), "count": int(r[1])} for r in rows
                ]
            except Exception as e:
                logger.debug(f"DuckDB top values query failed for {col_name}: {e}")

        # 4. 时间列跨度天数
        time_cols = [
            c["name"] for c in columns_info
            if c["type"] in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "DATE")
        ]
        for col_name in time_cols[:3]:
            try:
                q = conn.execute(
                    f"SELECT DATEDIFF('day', MIN(\"{col_name}\"), MAX(\"{col_name}\")) "
                    f"FROM read_parquet('{path_escaped}')"
                ).fetchone()
                if q and q[0] is not None:
                    for info in columns_info:
                        if info["name"] == col_name:
                            info["span_days"] = int(q[0])
                            break
            except Exception as e:
                logger.debug(f"DuckDB span_days query failed for {col_name}: {e}")

        # 5. 文本列 avg_length
        all_text_cols = [c["name"] for c in columns_info if c["type"] == "VARCHAR"]
        for col_name in all_text_cols[:5]:
            try:
                q = conn.execute(
                    f"SELECT AVG(LENGTH(\"{col_name}\")) "
                    f"FROM read_parquet('{path_escaped}') "
                    f"WHERE \"{col_name}\" IS NOT NULL"
                ).fetchone()
                if q and q[0] is not None:
                    for info in columns_info:
                        if info["name"] == col_name:
                            info["avg_length"] = round(float(q[0]), 1)
                            break
            except Exception as e:
                logger.debug(f"DuckDB avg_length query failed for {col_name}: {e}")

        # 6. 重复行数（总行数 - 去重行数）
        duplicate_count = 0
        try:
            q = conn.execute(
                f"SELECT {row_count} - COUNT(*) FROM "
                f"(SELECT DISTINCT * FROM read_parquet('{path_escaped}'))"
            ).fetchone()
            if q and q[0] is not None:
                duplicate_count = max(0, int(q[0]))
        except Exception as e:
            logger.debug(f"DuckDB duplicate count failed: {e}")

        # 7. 预览行（前2条 + 随机1条）
        preview_rows: list[dict] = []
        try:
            head_rows = conn.execute(
                f"SELECT * FROM read_parquet('{path_escaped}') LIMIT 2"
            ).fetchdf().to_dict("records")
            preview_rows.extend(head_rows)
            if row_count > 2:
                sample_row = conn.execute(
                    f"SELECT * FROM read_parquet('{path_escaped}') "
                    f"USING SAMPLE 1 ROWS"
                ).fetchdf().to_dict("records")
                preview_rows.extend(sample_row)
        except Exception as e:
            logger.debug(f"DuckDB preview query failed: {e}")

        return {
            "columns": columns_info,
            "row_count": row_count,
            "top_values": top_values,
            "duplicate_count": duplicate_count,
            "preview_rows": preview_rows,
        }

    # ── 共享工具方法 ──────────────────────────────────

    @staticmethod
    def parse_summarize_rows(
        summary_rows: list, summary_desc: list,
    ) -> tuple[int, list[dict], list[str], list[str]]:
        """解析 DuckDB SUMMARIZE 结果为结构化列信息。

        和 profile_parquet 共用同一解析逻辑，消除重复。

        Returns:
            (row_count, columns_info, numeric_cols, text_cols_low_card)
        """
        col_names = [d[0] for d in summary_desc] if summary_desc else []
        name_idx = col_names.index("column_name") if "column_name" in col_names else 0
        type_idx = col_names.index("column_type") if "column_type" in col_names else 1
        min_idx = col_names.index("min") if "min" in col_names else 2
        max_idx = col_names.index("max") if "max" in col_names else 3
        approx_unique_idx = col_names.index("approx_unique") if "approx_unique" in col_names else 4
        avg_idx = col_names.index("avg") if "avg" in col_names else 5
        null_pct_idx = col_names.index("null_percentage") if "null_percentage" in col_names else 7
        count_idx = col_names.index("count") if "count" in col_names else 8

        row_count = 0
        columns_info: list[dict] = []
        numeric_cols: list[str] = []
        text_cols_low_card: list[str] = []

        for row in summary_rows:
            col_name = str(row[name_idx])
            col_type = str(row[type_idx])
            approx_unique = int(row[approx_unique_idx]) if row[approx_unique_idx] is not None else 0
            cnt = int(row[count_idx]) if row[count_idx] is not None else 0
            if cnt > row_count:
                row_count = cnt

            null_pct_raw = row[null_pct_idx]
            null_pct = float(null_pct_raw.replace("%", "")) if isinstance(null_pct_raw, str) else (float(null_pct_raw) if null_pct_raw else 0)
            null_count = int(cnt * null_pct / 100) if cnt > 0 else 0

            info: dict = {
                "name": col_name,
                "type": col_type,
                "distinct_count": approx_unique,
                "null_count": null_count,
                "min": row[min_idx],
                "max": row[max_idx],
            }

            if row[avg_idx] is not None:
                try:
                    info["avg"] = float(row[avg_idx])
                except (ValueError, TypeError):
                    pass

            columns_info.append(info)

            if col_type in ("BIGINT", "INTEGER", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT", "TINYINT"):
                numeric_cols.append(col_name)
            elif col_type == "VARCHAR" and approx_unique <= 100 and cnt > 0 and (approx_unique / cnt < 0.5):
                text_cols_low_card.append(col_name)

        return row_count, columns_info, numeric_cols, text_cols_low_card

    # ── 生命周期 ──────────────────────────────────────

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("DuckDB engine closed")


def get_duckdb_engine() -> DuckDBEngine:
    """进程级单例，懒初始化。每个 Uvicorn worker 一个实例。"""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from core.config import get_settings

                s = get_settings()
                _engine = DuckDBEngine(
                    pg_url=s.database_url,
                    memory_limit=s.duckdb_memory_limit,
                    threads=s.duckdb_threads,
                )
    return _engine
