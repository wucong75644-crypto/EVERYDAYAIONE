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
    ) -> dict[str, int | float | str]:
        """
        执行 SELECT 查询，流式直写 Parquet。

        失败时自动重连 + 重试（最多 2 次），不降级到旧逻辑。

        Args:
            query: 完整的 SELECT SQL（表名需带 pg.public. 前缀）
            output_path: Parquet 输出路径（写到 staging 目录）

        Returns:
            {"row_count": int, "size_kb": float, "path": str}

        Raises:
            Exception: 重试耗尽后抛出原始异常
        """
        output = str(output_path)
        output_escaped = output.replace("'", "''")
        Path(output).parent.mkdir(parents=True, exist_ok=True)

        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                conn = self._get_conn()
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
                last_err = e
                logger.warning(
                    f"DuckDB export attempt {attempt + 1}/{_MAX_RETRIES} "
                    f"failed | error={e}"
                )
                # 销毁连接，下次循环 _get_conn 会重建（自动重连）
                self._reset_conn()
                # 清理可能写了一半的文件
                Path(output).unlink(missing_ok=True)

        raise last_err  # type: ignore[misc]

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
