# 查询预检防御层（Query Preflight Guard）

> 版本：v2.0 | 日期：2026-04-26 | 状态：方案设计

## 一、问题

当前 `UnifiedQueryEngine.execute()` 收到请求后，直接进入 summary 或 export 路径执行，**没有任何代价预估**。

### 1.1 生产实测数据（2026-04-26）

在生产服务器（4核 / 7.3GB / DuckDB 256MB+2线程）上对 4 月订单（30.4 万行）的 DuckDB 远程扫描基准测试：

| 操作 | 耗时 | 说明 |
|------|------|------|
| COUNT(*) | **58.7s** | 光数行数就要 1 分钟 |
| SELECT LIMIT 5（无排序）| **0.2s** | 提前终止，秒回 |
| ORDER BY + LIMIT 5 | **40.0s** | 必须拉全表排序 |
| COPY TO parquet（有排序）| **50.3s** | 排序 + 写文件 |
| COPY TO parquet（无排序）| **0.7s** | 提前终止，秒回 |

**根因**：DuckDB 通过 `postgres_scan` 远程拉数据，30 万行从 PG 拉到 DuckDB 需要 ~60 秒。只要涉及全表操作（COUNT / ORDER BY / 聚合），就必须拉全量。这是网络 I/O 瓶颈，加内存加线程无法改善。

**后果**：
- 生产 7 天内 19 次超时（14 次 DuckDB 内部 25s 超时 + 5 次子进程 120s 超时）
- 4 月订单 export **100% 失败**（加上 PII 脱敏/CASE 翻译等复杂 SQL，120s 不够）
- DuckDB 实际安全上限约 **3 万行**（历史成功最大 29,422 行）

### 1.2 典型失败场景

```
用户: "4月金额最高的5笔订单明细"
→ PlanBuilder: mode=export, sort_by=amount, sort_dir=desc, limit=5  ← 参数正确
→ DuckDB: 扫描 30.4 万行 → ORDER BY → LIMIT 5  ← 120s 超时失败
→ 实际只需 5 行结果
```

## 二、方案：EXPLAIN 预检 + 三级路由

在 summary 和 export **两个模式之前**，加一层统一的预检门卫。

### 2.1 架构

```
请求进入 execute()
  ↓
参数校验（现有逻辑不变）
  ↓
┌──────────────────────────────────┐
│  QueryPreflightGuard              │  ← 新增
│  1. 复用已有 WHERE 条件           │
│  2. EXPLAIN 估算 plan_rows        │
│  3. 三级路由决策                  │
└──────────────────────────────────┘
  ↓                 ↓                  ↓
小结果集          中等结果集          大结果集
(<1,000行)       (1K ~ 30K行)       (>30,000行)
  ↓                 ↓                  ↓
PG 直查          单次 DuckDB        分批 DuckDB + 合并
(秒回)          (现有路径)          (仅 export 场景)
```

### 2.2 预检手段：EXPLAIN

```sql
EXPLAIN (FORMAT JSON)
SELECT 1 FROM erp_document_items
WHERE doc_type='order' AND pay_time >= '2026-04-01' AND pay_time < '2026-05-01'
```

- 零成本：不执行查询，基于 PostgreSQL 统计信息估算，<5ms
- 返回 `plan_rows`（估算行数），精度约 ±2x，够用于分级路由
- 用现有 `self.db` 连接，无额外开销
- 失败时静默降级走原有路径（预检不能成为新故障点）

### 2.3 三级路由

| 预估行数 | 路径 | 适用模式 | 做法 | 耗时 |
|---------|------|---------|------|------|
| < 1,000 | **快路径** | summary + export | PG 直查（ORM SELECT + ORDER BY + LIMIT） | <1s |
| 1,000 ~ 30,000 | **标准路径** | summary + export | 走现有 summary RPC / 单次 DuckDB export（不变） | 5~25s |
| > 30,000 | **分批路径** | **仅 export** | 按时间切片分批 DuckDB 导出，合并 parquet | 30s~数分钟 |
| > 30,000 | **标准路径** | summary | RPC 聚合不受影响，正常走 | 现有耗时 |

**阈值说明**：
- **1,000**：PG 直查 ORDER BY + LIMIT 在索引下毫秒级，1000 行内存 JSON 很小
- **30,000**：生产验证的 DuckDB 安全上限（历史最大成功 29,422 行）
- EXPLAIN 精度 ±2x，阈值已留余量

### 2.4 快路径实现（< 1,000 行）

不管原始 mode 是 summary 还是 export，都走 PG 直查：

```python
async def _fast_query(self, doc_type, filters, tr, sort_by, sort_dir, limit,
                      extra_fields, ...) -> ToolOutput:
    """小结果集快路径：PG ORM 直查，跳过 RPC/DuckDB。"""
    # 1. 用 self.db（Supabase client）构建 SELECT
    # 2. WHERE 条件复用已有的 filter → SQL 转换逻辑
    # 3. ORDER BY + LIMIT 由 PostgreSQL 执行（有索引优化）
    # 4. 返回 ToolOutput（与 summary/export 格式兼容）
```

- summary 请求 → PG 侧聚合返回（替代 RPC）
- export 请求 → PG 直查返回行数据，生成 FileRef（小 parquet 或内联）

### 2.5 分批路径实现（> 30,000 行，仅 export）

当预估行数超过 30,000 且 mode=export 时，按时间切片分批导出：

```python
async def _batch_export(self, doc_type, filters, tr, estimated_rows,
                        extra_fields, limit, ...) -> ToolOutput:
    """大结果集分批导出：按时间切片，每批 ≤ 30,000 行。"""
    # 1. 计算切片数：ceil(estimated_rows / 30000)
    # 2. 将 time_range 均匀切分为 N 个子区间
    # 3. 每个子区间走单次 DuckDB export（复用现有 _export 逻辑）
    # 4. 合并所有 parquet 分片为一个文件
    # 5. 排序：最后一次 DuckDB 本地排序（读本地 parquet 比远程 PG 快得多）
    # 6. 返回 ToolOutput + FileRef
```

**关键设计**：
- 每批无排序导出（0.7s/batch），排序在最后合并时做（本地 parquet 读取，毫秒级）
- 切片依据时间列（有索引），每批只拉对应时间段的数据
- limit 参数在合并后应用（先全量导出再截断，保证排序正确性）
- 进度推送：每完成一个批次推 thinking（"正在导出第 3/10 批..."）

**性能预估**：
- 30 万行 → 10 批 × 0.7s ≈ 7~10s（远程无排序拉取）+ 合并排序 ~2s ≈ **12s 总计**
- 对比现在：120s 超时失败 → 12s 成功

### 2.6 OutputStatus 扩展

```python
class OutputStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    REJECTED = "rejected"  # 新增：预检拒绝（保留，极端场景兜底）
```

> REJECTED 作为兜底：当分批导出也预估超时（如 > 500 万行），返回结构化拒绝 + 建议。

## 三、改动范围

### 3.1 文件清单

| 文件 | 改动 | 行数估算 |
|------|------|---------|
| `erp_query_preflight.py`（新建） | PreflightGuard：EXPLAIN + 路由决策 | ~80 |
| `erp_unified_query.py` | execute() 插入预检 + `_fast_query()` + `_batch_export()` | +120 |
| `tool_output.py` | OutputStatus 加 REJECTED | +1 |
| `erp_agent.py` | _build_multi_result 处理 REJECTED 透传 | +10 |
| `chat_tools.py` | 主 Agent 提示词补充 REJECTED 处理策略 | +5 |

### 3.2 新建文件位置

```
backend/services/kuaimai/erp_query_preflight.py
```

归入 `kuaimai/` 目录（与 erp_unified_query.py 同级），职责单一：预检估算 + 路由决策。

## 四、执行计划

### Phase 1：预检基础设施
- 新建 `erp_query_preflight.py`（EXPLAIN 估算 + 三级路由决策）
- 在 `execute()` 入口调用预检，返回路由建议
- EXPLAIN 失败 → 静默降级走原有路径
- 单测覆盖三个路由分支

### Phase 2：快路径（< 1,000 行）
- 实现 `_fast_query()` — PG ORM 直查
- summary + export 小结果集统一走快路径
- 单测 + 对比验证（快路径 vs 原路径结果一致性）

### Phase 3：分批导出（> 30,000 行）
- 实现 `_batch_export()` — 时间切片 + 无排序分批拉取 + 合并 + 最终排序
- 进度推送（thinking 通道）
- 单测 + 生产级数据量验证（30 万行目标 < 30s）

### Phase 4：拒绝兜底 + 提示词协同
- OutputStatus 加 REJECTED（> 500 万行兜底）
- ERPAgent 透传 REJECTED → 主 Agent
- 主 Agent 提示词补充性能意识策略
- 端到端测试

## 五、风险与边界

| 风险 | 应对 |
|------|------|
| EXPLAIN plan_rows 不准（±2x） | 阈值留余量（1000 / 30000），不追求精确 |
| EXPLAIN 报错 | catch 降级走原有路径，预检不能成为新故障点 |
| 快路径返回格式不一致 | 复用 ToolOutput 统一格式，export 快路径仍生成 FileRef |
| 分批切片不均匀（某天数据特别多） | 先均匀切时间，后续可优化为按 COUNT 自适应切片 |
| 归档表 EXPLAIN 不准 | 归档场景（_need_archive=True）跳过快路径，走标准/分批路径 |
| 分批合并后文件过大 | > 500 万行走 REJECTED 兜底 |
| 分批导出中途某批失败 | 已完成的批次保留，重试失败批次，全部失败则报错 |

## 六、验证标准

- [ ] "4月金额最高5笔订单明细" → 快路径，<2s（现在 120s 超时）
- [ ] "导出本月全部订单" → 分批导出，<30s（现在 120s 超时）
- [ ] "昨天淘宝订单统计" → 快路径或标准路径，无感知变化
- [ ] "导出最近3天订单" → 标准单次 DuckDB（约 3000 行），不变
- [ ] EXPLAIN 失败 → 降级走原有路径，不报错
- [ ] 分批导出进度 → thinking 推送"正在导出第 N/M 批..."
- [ ] 分批导出结果 → 与单次全量导出结果一致（行数、排序、字段）
