# TECH: DuckDB 导出引擎替换方案

> **状态**：待确认 | **等级**：A级（核心导出逻辑重构）| **日期**：2026-04-16

---

## 一、问题背景

### 1.1 用户反馈的事故

4月14日用户要求导出当日已发货订单报表，Agent 两轮回答数据不一致：

| 轮次 | Agent 报告 | 实际数据 | 问题 |
|------|-----------|---------|------|
| 第一轮 | 5,000 条 | 9,598 行 | 被 `EXPORT_DEFAULT=5000` 截断，Agent 没提示 |
| 第二轮 | 1,490 单 / 8,772 条 | 8,429 单 / 9,598 行 | 查询条件变了（加了状态过滤），Agent 没说明 |

### 1.2 当前导出流程及问题

```
Agent 调 local_data(mode=export)
  → UnifiedQueryEngine._export_query()
    → 分批查 PG（每批 5000，上限 10000）
    → 全部攒到 Python list all_rows[]     ← 问题2：内存线性增长
  → _write_parquet(all_rows)              ← pandas df.to_parquet() 再复制一次内存
  → 文件写入 staging/{conv_id}/xxx.parquet
  → 返回 staging 路径给 Agent
  → Agent 调 code_execute，沙盒内 pd.read_parquet(STAGING_DIR + '/xxx.parquet')
  → 转 Excel 写到 OUTPUT_DIR
  → 自动上传 → 用户下载
```

**关键路径说明（staging 文件流转）：**

```
                       写入                         读取
导出引擎 ──────→ staging/conv_id/xxx.parquet ──────→ code_execute 沙盒
  (现在: pandas)     ↑ 路径不变                        ↑ pd.read_parquet() 不变
  (改后: DuckDB)     ↑ 格式不变(Parquet Snappy)        ↑ 完全无感知
```

三个目录的职责（DuckDB 改造不影响）：

| 目录 | 沙盒变量 | 权限 | 用途 |
|------|---------|------|------|
| `staging/{conv_id}/` | `STAGING_DIR` | 只读 | 导出工具预存的数据文件（Parquet） |
| `workspace/` | `WORKSPACE_DIR` | 只读 | 用户上传的文件（Excel 等） |
| `workspace/下载/` | `OUTPUT_DIR` | 可写 | 沙盒生成的文件，自动上传到 OSS/CDN |

### 1.3 三个核心问题

| # | 问题 | 根因 | 影响 |
|---|------|------|------|
| 1 | **数据截断无提示** | `EXPORT_DEFAULT=5000`，Agent 拿到 5000 行不知道还有更多 | 用户拿到不完整报表 |
| 2 | **内存随数据量线性增长** | `all_rows.extend(batch)` 攒全量到内存 | 季度 100 万行 ≈ 186MB 纯数据，加 Python 对象开销 ≈ 500MB+ |
| 3 | **大查询占用主库连接时间长** | Python 逐批 fetch 慢，连接占用时间长 | 百万行扫描期间其他查询排队 |

### 1.4 当前数据规模

| 指标 | 数值 |
|------|------|
| erp_document_items 热表 | **1420 万行，7.8 GB**（数据 3.8G + 索引 4G） |
| order 类型 | **142 万行** |
| 月均订单明细 | 15 万 ~ 47 万行（波动大） |
| 季度（2026-Q1） | **95.7 万行，62.7 万单** |
| 年度估算 | 300 万 ~ 500 万行 |
| 平均行宽 | 186 bytes |
| PG shared_buffers | 640 MB |
| PG work_mem | 8 MB |
| 服务器 | 阿里云单实例，2 个 Uvicorn worker |
| PG 连接 | 上限 200，当前使用 12（活跃 1，空闲 11） |

---

## 二、方案选型

### 2.1 备选方案对比

| 方案 | 解决截断 | 解决内存 | 加速导出 | 成本 | 复杂度 |
|------|---------|---------|---------|------|--------|
| A. 提高 EXPORT_MAX + Python 流式写入 | 是 | 是 | 否（仍逐行fetch） | 0 | 低（~30行） |
| **B. DuckDB 替换导出引擎** | **是** | **是** | **是（5-10倍）** | **0** | **中（~150行）** |
| C. PG 只读副本 | 否（治标） | 否 | 否 | ~200元/月 | 低 |
| D. ClickHouse 数据仓库 | 是 | 是 | 是（最快） | ~500+元/月 | 高 |

**选择方案 B（DuckDB）**：零成本、一步到位解决三个问题，且为未来数据分析打基础。

### 2.2 为什么选 DuckDB

| 对比项 | 现在（Python + PG 逐批 fetch） | DuckDB |
|--------|-------------------------------|--------|
| 内存模型 | `all_rows[]` 全量攒到内存 | 内部流式处理，内存恒定 |
| 导出速度 | 逐行 fetch + Python 序列化 | 列式批量传输，快 5-10 倍 |
| 输出格式 | Python → pandas → pyarrow → Parquet | 原生 `COPY TO` 直写 Parquet |
| 行数上限 | 硬编码 `EXPORT_MAX=10000` | 无上限（保留安全上限 100 万） |
| 成本 | - | 0 元，嵌入式引擎，`pip install duckdb` |
| 运维 | - | 零运维，无独立进程，进程内嵌入 |
| PG 兼容 | - | 原生 postgres 扩展，直连读现有表和索引 |
| 数据实时性 | - | 直连主库实时查询，无延迟 |

---

## 三、新导出流程

### 3.1 流程对比

```
===== 现在 =====
Agent 调 local_data(mode=export)
  → Python 分批查 PG（每批 5000，LIMIT 10000）
  → all_rows.extend(batch)       ← 内存线性增长
  → pandas DataFrame → to_parquet()  ← 再复制一次
  → 写入 staging/conv_id/local_order_xxx.parquet
  → Agent 拿到路径，调 code_execute 转 Excel

===== 改后 =====
Agent 调 local_data(mode=export)        ← 接口不变
  → DuckDB COPY (SELECT ... FROM pg.erp_document_items WHERE ...)
      TO 'staging/conv_id/local_order_xxx.parquet'    ← 同样的路径
      (FORMAT PARQUET, COMPRESSION SNAPPY)             ← 同样的格式
    ↑ DuckDB 内部：流式拉取 → 列式压缩 → 追加写文件 → 内存恒定
  → 从 parquet 元数据读行数（不重新扫描）
  → Agent 拿到路径，调 code_execute 转 Excel          ← 完全不变
```

### 3.2 代码对比

```python
# ===== 现在：Python 攒全量 =====
all_rows = []
while offset < max_rows:                    # ← EXPORT_DEFAULT=5000 截断
    batch = db.table("erp_document_items")
        .select(cols).eq("doc_type", doc_type)
        .range(offset, offset + 4999).execute()
    all_rows.extend(batch.data)              # ← 内存线性增长
    offset += len(batch.data)
df = pd.DataFrame(all_rows)
df.to_parquet(staging_path, engine="pyarrow")  # ← 再复制一次内存

# ===== 改后：DuckDB 流式直写 =====
engine = get_duckdb_engine()
engine.export_to_parquet(
    query=f"SELECT {cols} FROM pg.public.erp_document_items WHERE {where}",
    output_path=staging_path,  # ← 同样写到 staging 目录
)
# 内部自动流式：拉一批 → 压缩 → 写文件 → 释放 → 拉下一批
# 100 万行和 1000 行占的内存一样
```

### 3.3 上下游完全无感知

| 组件 | 是否受影响 | 原因 |
|------|-----------|------|
| Agent 工具定义（erp_local_tools.py） | 不变 | mode=export schema 不变 |
| 工具分发（erp_tool_executor.py） | 不变 | `_dispatch_local_data` → `engine.execute()` 不变 |
| **staging 目录** | **不变** | **同路径 `staging/{conv_id}/xxx.parquet`，DuckDB 只是换了"谁来写"** |
| **code_execute 沙盒** | **不变** | **`pd.read_parquet(STAGING_DIR + '/xxx.parquet')` 照读，Snappy 压缩兼容** |
| OUTPUT_DIR 输出 | 不变 | 沙盒写 Excel 到 OUTPUT_DIR → 自动上传流程不变 |
| summary / detail 模式 | 不变 | 只改 export 模式 |

---

## 四、影响范围分析

### 4.1 需要修改的文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `backend/requirements.txt` | 新增 `duckdb>=1.2.0` | 新依赖（~20MB） |
| `backend/core/config.py` | 新增 2 个配置字段 | `duckdb_memory_limit`、`duckdb_threads` |
| `backend/core/duckdb_engine.py` | **新建**（~80 行） | DuckDB 连接管理，进程级单例 |
| `backend/services/kuaimai/erp_unified_query.py` | 重写 `_export` + `_export_query` | 核心改动：Python 批量 → DuckDB COPY |
| `backend/services/kuaimai/erp_unified_schema.py` | 修改常量 | `EXPORT_MAX` 从 10000 → 1000000；删除 `EXPORT_DEFAULT`/`EXPORT_BATCH` |

### 4.2 不需要改动的文件

| 文件 | 原因 |
|------|------|
| `config/erp_local_tools.py` | 工具 schema 不变 |
| `config/erp_tools.py` | 路由提示词不变 |
| `services/agent/erp_tool_executor.py` | 分发逻辑不变 |
| `services/agent/tool_executor.py` | code_execute 不变 |
| `services/sandbox/functions.py` | 沙盒环境不变（STAGING_DIR 路径不变） |
| `services/sandbox/executor.py` | 沙盒执行器不变 |
| `core/workspace.py` | staging 路径解析不变 |

### 4.3 边界场景

| 场景 | 处理方式 |
|------|---------|
| DuckDB 连接 PG 失败 | 引擎内部自动重连 + 重试（最多 2 次），不降级到旧逻辑（旧逻辑有截断 bug） |
| 导出行数 = 0 | 与现有逻辑一致，返回"无数据" + 同步健康检查 |
| 超大导出（>100 万行） | `EXPORT_MAX=1000000` 安全上限 + 截断提示告知用户 |
| 超大导出内存 | DuckDB `memory_limit=256MB`，超出自动溢出到磁盘（temp_directory） |
| 并发导出 | DuckDB 进程级单例 + 线程安全，多个导出请求串行执行 |
| org_id 多租户隔离 | SQL WHERE 条件带 `org_id`，与现有一致 |
| 冷表数据（>3个月） | DuckDB UNION ALL 热表 + 冷表（`erp_document_items_archive`） |
| PII 脱敏 | 从 Python `mask_pii()` 迁移到 SQL CASE WHEN，效果一致 |

---

## 五、详细设计

### 5.1 新建 `core/duckdb_engine.py`

```python
"""
DuckDB 导出引擎 - 进程级单例，直连 PG 流式导出 Parquet。

职责：接收 SQL → 直连 PG 拉数据 → 流式写 Parquet 到指定路径
不负责：路径解析、字段验证、PII 脱敏（由调用方处理）

用法：
    engine = get_duckdb_engine()
    result = engine.export_to_parquet(sql, output_path)
"""
import duckdb
import threading
from pathlib import Path
from loguru import logger

_lock = threading.Lock()
_engine: "DuckDBEngine | None" = None


class DuckDBEngine:

    def __init__(self, pg_url: str, memory_limit: str = "256MB", threads: int = 2):
        self._pg_url = pg_url
        self._memory_limit = memory_limit
        self._threads = threads
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        """懒初始化 DuckDB 连接（含 PG 扩展加载）"""
        if self._conn is None:
            self._conn = duckdb.connect()
            self._conn.execute(f"SET memory_limit='{self._memory_limit}'")
            self._conn.execute(f"SET threads={self._threads}")
            self._conn.execute("INSTALL postgres; LOAD postgres;")
            self._conn.execute(
                f"ATTACH '{self._pg_url}' AS pg (TYPE postgres, READ_ONLY)"
            )
            logger.info(
                f"DuckDB engine initialized | memory={self._memory_limit} "
                f"threads={self._threads}"
            )
        return self._conn

    def export_to_parquet(self, query: str, output_path: str | Path) -> dict:
        """
        执行 SELECT 查询，流式直写 Parquet 到 output_path。

        返回: {"row_count": int, "size_kb": float, "path": str}
        """
        conn = self._get_conn()
        output = str(output_path)

        conn.execute(f"""
            COPY ({query}) TO '{output}' (
                FORMAT PARQUET,
                COMPRESSION SNAPPY,
                ROW_GROUP_SIZE 100000
            )
        """)

        # 从 parquet 元数据读行数（不需要重新扫描文件内容）
        meta = conn.execute(
            f"SELECT sum(num_rows)::BIGINT as cnt FROM parquet_metadata('{output}')"
        ).fetchone()
        row_count = meta[0] if meta else 0
        size_kb = Path(output).stat().st_size / 1024

        logger.info(f"DuckDB export done | rows={row_count} size={size_kb:.0f}KB")
        return {"row_count": row_count, "size_kb": size_kb, "path": output}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("DuckDB engine closed")


def get_duckdb_engine() -> DuckDBEngine:
    """进程级单例，懒初始化"""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from core.config import get_settings
                s = get_settings()
                _engine = DuckDBEngine(
                    pg_url=s.database_url,
                    memory_limit=getattr(s, "duckdb_memory_limit", "256MB"),
                    threads=getattr(s, "duckdb_threads", 2),
                )
    return _engine
```

### 5.2 修改 `erp_unified_query.py` —— `_export` 方法

核心变化：`_export_query` + `_write_parquet` 合并为一次 DuckDB `COPY TO`

```python
async def _export(self, doc_type, filters, tr, fields, limit,
                  user_id, conversation_id, request_ctx) -> str:
    type_name = DOC_TYPE_CN.get(doc_type, doc_type)

    # 字段验证（不变）
    if not fields:
        return generate_field_doc(doc_type)
    safe_fields = [c for c in fields if c in EXPORT_COLUMN_NAMES]
    if not safe_fields:
        return "无有效字段，请参考字段文档"

    # 构建 staging 路径（复用现有 resolve_staging_dir，路径格式不变）
    staging_dir, rel_path, staging_path, filename = _resolve_export_path(
        doc_type, user_id, self.org_id, conversation_id
    )

    # 构建 SQL（含 PII 脱敏 + 冷热表 UNION）
    select_sql = self._build_export_select(safe_fields)
    where_sql = self._build_export_where(doc_type, filters, tr)
    need_archive = _need_archive(tr)

    if need_archive:
        query = f"""
            SELECT {select_sql} FROM pg.public.erp_document_items WHERE {where_sql}
            UNION ALL
            SELECT {select_sql} FROM pg.public.erp_document_items_archive WHERE {where_sql}
            ORDER BY {tr.time_col} DESC
        """
    else:
        query = f"""
            SELECT {select_sql} FROM pg.public.erp_document_items
            WHERE {where_sql} ORDER BY {tr.time_col} DESC
        """

    # 安全上限（LIMIT 注入到 SQL）
    max_rows = min(limit or EXPORT_MAX, EXPORT_MAX)
    query = f"SELECT * FROM ({query}) sub LIMIT {max_rows}"

    # DuckDB 流式导出 → 同样的 staging 路径
    # 失败时引擎内部自动重连 + 重试（最多 2 次），不降级到旧逻辑
    import asyncio as _asyncio
    start = _time.monotonic()
    from core.duckdb_engine import get_duckdb_engine
    engine = get_duckdb_engine()
    try:
        result = await _asyncio.to_thread(engine.export_to_parquet, query, staging_path)
    except Exception as e:
        logger.error(f"DuckDB export failed after retries | error={e}", exc_info=True)
        return f"导出失败（已重试）: {e}"
    row_count = result["row_count"]
    size_kb = result["size_kb"]
    elapsed = _time.monotonic() - start

    if row_count == 0:
        staging_path.unlink(missing_ok=True)
        health = check_sync_health(self.db, [doc_type], org_id=self.org_id)
        body = f"{type_name}无数据\n{health}".strip()
    else:
        preview = _read_parquet_preview(staging_path, n=3)
        body = (
            f"[数据已暂存] {rel_path}\n"
            f"共 {row_count:,} 条记录（Parquet，{size_kb:.0f}KB），"
            f"耗时 {elapsed:.3f}秒。\n"
            f"如需处理请调 code_execute，"
            f"用 df = pd.read_parquet(STAGING_DIR + '/{filename}') 读取。\n\n"
            f"前3条预览：\n{preview}"
        )
        if row_count >= max_rows:
            body += f"\n\n⚠️ 已达导出上限 {max_rows:,} 行，实际数据可能更多。请缩小时间范围重新导出。"

    time_header = format_time_header(ctx=request_ctx, range_=tr.date_range, kind="导出窗口")
    return f"{time_header}\n\n{body}" if time_header else body
```

### 5.3 PII 脱敏迁移到 SQL 层

现有 Python 的 `mask_pii()` 逐行处理 → 改为 SQL `CASE WHEN`，在 DuckDB 查询时直接脱敏：

```python
# PII 字段列表
_PII_FIELDS = {"receiver_name", "receiver_mobile", "receiver_phone"}

def _build_export_select(self, safe_fields: list[str]) -> str:
    """构建 SELECT 列表，PII 字段自动加脱敏表达式"""
    cols = []
    for f in safe_fields:
        if f == "receiver_name":
            cols.append("CASE WHEN receiver_name IS NOT NULL "
                        "THEN substr(receiver_name,1,1)||'***' END AS receiver_name")
        elif f in ("receiver_mobile", "receiver_phone"):
            cols.append(f"CASE WHEN {f} IS NOT NULL "
                        f"THEN substr({f},1,3)||'****'||substr({f},8) END AS {f}")
        else:
            cols.append(f)
    return ", ".join(cols)
```

### 5.4 冷热表 UNION 支持

根据时间范围自动判断是否需要查归档表（复用现有 `_need_archive(tr)` 函数）：

```sql
-- 时间范围超出热表（近3个月）时，自动 UNION 冷表
SELECT {cols} FROM pg.public.erp_document_items WHERE {where}
UNION ALL
SELECT {cols} FROM pg.public.erp_document_items_archive WHERE {where}
ORDER BY consign_time DESC
LIMIT 1000000
```

### 5.5 配置新增（`core/config.py`）

```python
# DuckDB 导出引擎配置
duckdb_memory_limit: str = "256MB"   # 最大内存（超出自动溢出到磁盘）
duckdb_threads: int = 2              # 工作线程数（不超过服务器 CPU 核心的一半）
```

### 5.6 常量修改（`erp_unified_schema.py`）

```python
# 旧：
EXPORT_BATCH = 5000      # 删除（DuckDB 内部自动分批）
EXPORT_DEFAULT = 5000    # 删除（不再需要默认上限）
EXPORT_MAX = 10000       # 改为 1_000_000

# 新：
EXPORT_MAX = 1_000_000   # 安全上限，防止误查全表
```

### 5.7 辅助函数

```python
def _resolve_export_path(doc_type, user_id, org_id, conversation_id):
    """复用现有 workspace 路径解析，确保写入 staging 目录"""
    from core.config import get_settings
    from core.workspace import resolve_staging_dir, resolve_staging_rel_path

    settings = get_settings()
    conv_id = conversation_id or "default"
    staging_dir = Path(resolve_staging_dir(
        settings.file_workspace_root,
        user_id=user_id or "", org_id=org_id,
        conversation_id=conv_id,
    ))
    staging_dir.mkdir(parents=True, exist_ok=True)

    ts = int(_time.time())
    filename = f"local_{doc_type}_{ts}.parquet"
    staging_path = staging_dir / filename
    rel_path = resolve_staging_rel_path(conversation_id=conv_id, filename=filename)

    return staging_dir, rel_path, staging_path, filename


def _read_parquet_preview(path, n=3) -> str:
    """从 Parquet 文件读前 N 行预览（不加载全量）"""
    import pandas as pd
    df = pd.read_parquet(path).head(n)
    return df.to_string(index=False, max_colwidth=30)
```

---

## 六、DuckDB 资源控制

### 6.1 内存

| 配置 | 值 | 说明 |
|------|---|------|
| `memory_limit` | 256MB | DuckDB 进程内最大内存占用 |
| 超出策略 | 自动溢出到磁盘 | DuckDB 内建 temp_directory，无需配置 |
| 服务器总内存（估算） | ~4-8 GB | 256MB 占比 3-6%，安全 |

### 6.2 连接

| 项目 | 说明 |
|------|------|
| DuckDB → PG | `READ_ONLY` ATTACH，DuckDB 自己维护的独立连接（不走应用连接池） |
| 新增连接数 | +1 个持久 PG 连接（每个 Uvicorn worker 一个） |
| 当前连接余量 | 200（上限）- 12（在用）= 188，+2 完全没压力 |

### 6.3 CPU

| 配置 | 值 | 说明 |
|------|---|------|
| `threads` | 2 | 限制 DuckDB 并行线程数 |
| 原因 | 服务器同时跑 2 个 Uvicorn worker + PG + ERP 同步，不能抢太多 CPU |

---

## 七、实施计划

| Phase | 内容 | 涉及文件 |
|-------|------|---------|
| **Phase 1** | 新增 DuckDB 依赖 + 引擎单例 + 配置 | `requirements.txt`、`core/config.py`、`core/duckdb_engine.py`（新建） |
| **Phase 2** | 重写 `_export` 方法 + PII SQL 化 + 冷热表 UNION | `erp_unified_query.py`、`erp_unified_schema.py` |
| **Phase 3** | 重试 + 自动重连（替代降级方案） | `core/duckdb_engine.py` |
| ~~Phase 4~~ | ~~降级回退~~ 已删除 — 旧逻辑有截断 bug，降级等于没修 | - |
| **Phase 5** | 测试验证 | 单测 + 4月14日真实数据 + Q1 季度导出验证 |

**总计：改动 4 个文件 + 新建 1 个文件**

### 风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|---------|
| DuckDB postgres 扩展连不上阿里云 PG | 低 | 引擎内部自动重连 + 重试 2 次；Phase 1 已验证连通性 |
| DuckDB 进程内存泄漏 | 低 | 进程级单例复用 + `memory_limit` 硬上限 |
| Parquet 压缩格式沙盒不兼容 | 极低 | 统一用 Snappy（与现有 pyarrow 默认一致），`pd.read_parquet()` 原生支持 |
| 服务器 CPU 架构不兼容 | 极低 | DuckDB 同时支持 x86 和 ARM（阿里云 ECS 均可） |

---

## 八、验收标准

- [ ] 4月14日发货数据导出：**9,598 行完整输出**（不截断）
- [ ] 2026-Q1 季度数据导出：~95.7 万行成功，内存峰值 < 300MB
- [ ] 导出耗时：单日 < 3 秒，季度 < 15 秒
- [ ] staging 文件路径格式不变，`pd.read_parquet()` 正常读取
- [ ] DuckDB 连接失败时自动重连 + 重试，重试耗尽返回明确错误
- [ ] 数据截断时有明确 `⚠️` 提示
- [ ] 现有 summary / detail 模式完全不受影响
- [ ] 前后端测试全绿
