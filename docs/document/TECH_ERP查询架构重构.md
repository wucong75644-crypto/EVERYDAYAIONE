# TECH_ERP 查询架构重构

> 版本：v2.2 | 日期：2026-04-27 | 状态：方案设计中

## 1. 背景

### 1.1 原始问题
用户问"4月金额最高5笔订单"，系统走 DuckDB 导出路径扫描 34 万行超时。根因：统计/查询/导出三种需求混在两个 mode（summary/export）里，所有明细查询都走 DuckDB 远程扫描，即使用户只要 5 行。

### 1.2 更深层问题
行业调研发现，现有查询引擎只有**基础查询**和**聚合统计**两种能力，缺少趋势分析、对比分析、预警查询、跨表关联等电商 ERP 常见的分析能力。这些需求目前要么做不了，要么靠导出数据 + code_execute 迂回计算，链路长、延迟高、容易出错。

### 1.3 目标
1. 统计和查询统一走 PG 直查，导出独立为文件通道
2. **新增 7 类分析查询能力**（趋势/对比/占比/跨域指标/预警/分布 + 基础查询优化），覆盖电商 ERP 90%+ 的数据分析场景，全部作为 ERP Agent 原生能力
3. 复杂计算由主 Agent 编排 code_execute 兜底（仅限极少数超出引擎能力的场景）

## 2. 项目上下文

### 架构现状
- UnifiedQueryEngine 是所有 ERP 查询的唯一入口，两种 mode：summary（RPC 聚合）/ export（DuckDB 流式导出）
- summary 走 PG 侧 RPC `erp_global_stats_query`，只返回聚合数字（COUNT DISTINCT + SUM，单维度 GROUP BY）
- export 走 DuckDB subprocess，写 parquet 到 staging，返回 FileRef
- 新表（stock/product 等 8 张）已走 ORM 直查路径（summary_orm / export_orm）
- 旧表（erp_document_items）的所有明细查询都走 DuckDB，包括 limit=5 的场景
- **daily_stats 表**已按"商品×日期"预聚合了订单/采购/售后/收货/上架/采退的计数和金额

### 可复用模块
- `apply_orm_filters()`：ValidatedFilter → Supabase ORM 链式调用，新旧表通用
- `validate_filters(doc_type=)`：按表白名单校验，已支持 14 个 doc_type
- `resolve_export_path()`：staging 路径生成
- `build_profile_from_duckdb()`：parquet 文件统计摘要
- `export_orm()`：ORM 查询 → parquet 写入，新表已验证可用
- `_cleanup_staging_delayed()`：15 分钟延迟清理 staging 目录
- `erp_global_stats_query` RPC：PG 侧聚合，支持 DSL filter + 单维度 GROUP BY

### 关键发现：daily_stats 是跨表分析的突破口
`erp_product_daily_stats` 表已经按"商品×日期"预聚合了 6 类单据的计数和金额：

```
stat_date | outer_id | order_count | order_amount | order_cost |
          |          | aftersale_count | aftersale_amount |
          |          | purchase_count | purchase_amount |
          |          | receipt_count | shelf_count | purchase_return_count | ...
```

这意味着很多看似需要"跨表 JOIN"的场景，**不需要真正的 JOIN**：
- "退货率" = `SUM(aftersale_count) / SUM(order_count)` → 同一行的两个字段
- "每天的销售额趋势" = `GROUP BY stat_date, SUM(order_amount)` → 单表聚合
- "库存周转天数" = stock.available_qty / daily_stats 日均 order_qty → 只需两次简单查询

### 设计约束
- DuckDB postgres_scanner 生产实测安全上限约 30000 行
- RPC `erp_global_stats_query` 只支持 erp_document_items 表的聚合
- Supabase ORM 不支持 GROUP BY / JOIN / DATE_TRUNC / AVG / 窗口函数
- PII 脱敏（receiver_name/mobile/address）只在 DuckDB export 路径实现
- 旧表 export 有特殊字段翻译（平台编码→中文），在 DuckDB SQL 中做

### 潜在冲突
- 旧表走 PG 直查时，PII 脱敏和字段翻译需要从 DuckDB SQL 迁移到 Python 层
- summary RPC 返回聚合数字（doc_count/total_qty/total_amount），无法返回明细行
- 新增分析查询需要新的 PG RPC 函数（数据库迁移）

## 3. 能力全景——现有 vs 目标

### 3.1 现有能力（2 种 mode）

| 能力 | 实现方式 | 限制 |
|------|---------|------|
| 条件筛选（WHERE） | ORM filter / RPC DSL | ✅ 完善 |
| 聚合统计（COUNT/SUM + 单维度 GROUP BY） | RPC | 只支持 6 个维度，无 AVG/MIN/MAX |
| 排序 + Top-N | DuckDB ORDER BY + LIMIT | 必须走 DuckDB，小数据也慢 |
| 大批量导出 | DuckDB → Parquet | 30k 行上限 |
| 订单分类统计 | RPC + OrderClassifier | 仅 order doc_type |

### 3.2 目标能力（9 种查询类型）

| # | 查询类型 | 用户场景举例 | 实现引擎 |
|---|---------|------------|---------|
| 1 | **基础查询**（已有，优化路由） | "4月金额最高5笔订单" | PG ORM 直查 |
| 2 | **聚合统计**（已有，扩展维度） | "各平台的订单数和总金额" | PG RPC（扩展多维 + AVG/MIN/MAX） |
| 3 | **趋势分析**（新增） | "每天的销售额""按月看订单量" | PG RPC（DATE_TRUNC + GROUP BY） |
| 4 | **对比分析**（新增） | "这个月比上个月怎么样""同比增长" | PG RPC × 2 + Python 计算增长率 |
| 5 | **占比/排名分析**（新增） | "各平台销售额占比""ABC商品分类" | PG RPC 聚合 + Python 计算占比/累计 |
| 6 | **跨域指标**（新增） | "退货率""毛利率""发货时效""进销存" | daily_stats RPC + stock ORM + 专用 RPC |
| 7 | **预警查询**（新增） | "哪些SKU快卖断了""滞销品""采购超期" | stock ORM + daily_stats 规则 + 采购表规则 |
| 8 | **分布分析**（新增） | "订单金额分布""客单价区间" | PG RPC（CASE WHEN 分桶） |
| 9 | **大批量导出**（已有，不变） | "导出4月全部订单" | DuckDB → Parquet |

## 4. 核心架构设计

### 4.1 统一路由模型

```
用户问题 → ERPAgent → PlanBuilder 提取参数
                              │
                         query_type 识别
                              │
         ┌──────────┬─────────┼─────────┬──────────┬──────────┐
         ▼          ▼         ▼         ▼          ▼          ▼
      基础查询   聚合统计   趋势分析   对比分析   跨域指标    预警查询
      (detail)  (summary)  (trend)   (compare) (cross)    (alert)
         │          │         │         │          │          │
         ▼          ▼         ▼         ▼          ▼          ▼
      PG ORM    PG RPC    PG RPC    RPC×2      daily_stats  stock+
      直查      聚合      时间分桶   +Python    RPC聚合     daily_stats
         │          │         │         │          │          │
         └──────────┴─────────┴─────────┴──────────┴──────────┘
                              │
                     ┌────────┼────────────┐
                     ▼        ▼            ▼
              ≤200行     200~30k行     >30k行
              PG inline   DuckDB       PG COPY
             (+staging)   Parquet      流式 Parquet
                                       (无上限)
```

### 4.2 execute() 参数扩展

**现在**：`mode = summary | export`

**改后**：新增 `query_type` 参数，`mode` 保留但语义收窄为"输出格式偏好"：

```python
async def execute(
    self,
    doc_type: str,
    mode: str,                              # "summary" | "export"（输出偏好）
    filters: list[dict],
    # --- 已有参数 ---
    group_by: list[str] | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int = 20,
    # --- 新增参数 ---
    query_type: str = "auto",               # 查询类型（见下表）
    time_granularity: str | None = None,     # 趋势分析：day / week / month
    compare_range: str | None = None,        # 对比分析：mom（环比） / yoy（同比）
    metrics: list[str] | None = None,        # 指标列表：count / amount / qty / avg_amount / ...
    alert_type: str | None = None,           # 预警类型：low_stock / slow_moving / overstock
    # --- 保留参数 ---
    extra_fields: list[str] | None = None,
    time_type: str | None = None,
    include_invalid: bool = False,
    **_kwargs,
) -> ToolOutput:
```

### 4.3 query_type 路由表

| query_type | 触发条件 | 实际执行 | 返回行数 | 需要迁移 |
|-----------|---------|---------|---------|---------|
| `auto` | 默认值，按 mode/limit 自动推断 | 兼容现有逻辑 | 不定 | 否 |
| `detail` | 明细查询 + limit≤200 | PG ORM 直查 | ≤200 | 否 |
| `summary` | 聚合统计 | PG RPC（扩展版） | ≤100 | 是（扩展 RPC） |
| `trend` | 有 time_granularity | PG RPC `erp_trend_query` | ≤366 | 是（新 RPC） |
| `compare` | 有 compare_range | 两次 RPC + Python 计算 | ≤100 | 否（复用现有 RPC） |
| `ratio` | 需要占比/排名 | RPC 聚合 + Python 计算占比 | ≤100 | 否 |
| `cross` | 跨域指标（退货率/周转） | daily_stats RPC + stock ORM | ≤100 | 是（新 RPC） |
| `alert` | 有 alert_type | stock ORM + daily_stats 规则 | ≤500 | 否 |
| `distribution` | 需要分桶/分布 | PG RPC CASE WHEN | ≤20 | 是（新 RPC） |
| `export` | 200 < limit ≤ 30000 | DuckDB → Parquet（现有路径） | ≤30000 | 否 |
| `export_large` | limit > 30000 | PG COPY 流式 → Parquet（新路径） | **无上限** | 否 |

**auto 推断规则**：
```python
def _resolve_query_type(query_type, mode, limit, time_granularity, compare_range, alert_type, metrics, distribution_field):
    if query_type != "auto":
        return query_type
    if alert_type:
        return "alert"
    if distribution_field:
        return "distribution"
    if time_granularity:
        return "trend"
    if compare_range:
        return "compare"
    if metrics and any(m in CROSS_METRICS for m in metrics):
        return "cross"
    if mode == "export" and limit <= 200:
        return "detail"
    if mode == "export" and limit > 200:
        return "export"
    return "summary"
```

## 5. 各查询类型详细设计

### 5.1 基础查询（detail）—— PG ORM 直查

**解决的问题**：用户说"前5笔订单"，不再走 DuckDB 扫描 34 万行。

**实现**（与 v1.0 方案的 `_query_pg()` 相同）：
```python
async def _query_detail(self, doc_type, filters, tr, sort_by, sort_dir, limit, ...):
    """PG ORM 直查——≤200行明细"""
    q = self.db.table(table).select(select_fields, count="exact")
    q = q.eq("org_id", self.org_id)
    if doc_type_col:
        q = q.eq("doc_type", doc_type)
    q = apply_time_range(q, tr)
    q = apply_orm_filters(q, validated_filters)
    q = q.order(sort_by, desc=(sort_dir == "desc")).limit(limit)
    rows = q.execute().data

    rows = mask_pii(rows)           # PII 脱敏
    rows = translate_fields(rows)   # 字段翻译
    # 写 staging + 返回 inline
```

**旧表特殊处理**（与 v1.0 相同）：
- PII 脱敏：`mask_pii()` Python 层逐行脱敏
- 字段翻译：`translate_fields()` 用 PLATFORM_CN 等映射
- 归档表：分别查主表+归档表，Python 合并

### 5.2 聚合统计（summary）—— 扩展 RPC

**解决的问题**：现有 RPC 只支持 COUNT/SUM + 单维度 GROUP BY，缺少 AVG/MIN/MAX 和多维分组。

**扩展点**：

| 维度 | 现在 | 扩展后 |
|------|------|--------|
| 聚合函数 | COUNT DISTINCT + SUM | + AVG + MIN + MAX |
| GROUP BY | 单维度（6个枚举值） | 多维度（列表） |
| 返回字段 | doc_count/total_qty/total_amount | + avg_amount + min_amount + max_amount + distinct_buyer |

**方案**：修改 `erp_global_stats_query` RPC，新增返回字段：

```sql
-- 迁移文件：xxx_extend_stats_rpc.sql
-- 在现有 RPC 基础上新增聚合列
SELECT
    COUNT(DISTINCT doc_id) AS doc_count,
    SUM(quantity) AS total_qty,
    SUM(amount) AS total_amount,
    -- 新增
    AVG(amount) AS avg_amount,
    MIN(amount) AS min_amount,
    MAX(amount) AS max_amount,
    COUNT(DISTINCT buyer_nick) AS distinct_buyer,
    SUM(cost) AS total_cost,
    SUM(gross_profit) AS total_profit
FROM ...
```

**多维 GROUP BY**：扩展 RPC 支持 `p_group_by` 为数组（最多 2 维），SQL 中动态拼接 GROUP BY 列。

**metrics 参数**：LLM 提取用户关注的指标，引擎只返回对应列（减少上下文占用）：
```python
metrics = ["count", "amount", "avg_amount"]  # 用户问"平均客单价"
# → RPC 返回中只包含 doc_count, total_amount, avg_amount
```

### 5.3 趋势分析（trend）—— 新 RPC

**解决的问题**："每天的销售额""按月看订单量趋势""退货量每周变化"——这是老板/运营问得最多的问题类型。

**实现策略**：

| 数据源 | 适用场景 | 性能 |
|--------|---------|------|
| daily_stats 表 | 按天粒度的所有指标（订单/采购/售后等） | **极快**（已预聚合） |
| erp_document_items + DATE_TRUNC | 按周/月粒度，或需要细分过滤条件 | 中等 |

**优先走 daily_stats**（大多数趋势查询）：

```sql
-- 新 RPC：erp_trend_query
CREATE OR REPLACE FUNCTION erp_trend_query(
    p_org_id UUID,
    p_start DATE,
    p_end DATE,
    p_granularity TEXT DEFAULT 'day',      -- day / week / month
    p_metrics TEXT[] DEFAULT '{order_count,order_amount}',
    p_group_by TEXT DEFAULT NULL,           -- NULL / outer_id / 其他维度
    p_outer_id TEXT DEFAULT NULL,           -- 按商品过滤
    p_limit INT DEFAULT 100
) RETURNS JSONB AS $$
DECLARE
    v_date_expr TEXT;
    v_select_cols TEXT;
    v_result JSONB;
BEGIN
    -- 日期截断
    v_date_expr := CASE p_granularity
        WHEN 'day'   THEN 'stat_date'
        WHEN 'week'  THEN 'date_trunc(''week'', stat_date)::date'
        WHEN 'month' THEN 'date_trunc(''month'', stat_date)::date'
    END;

    -- 动态构建 SELECT（只返回用户要的 metrics）
    -- metrics 白名单：order_count, order_amount, order_qty, order_cost,
    --                  aftersale_count, aftersale_amount,
    --                  purchase_count, purchase_amount,
    --                  receipt_count, shelf_count, ...

    EXECUTE format(
        'SELECT jsonb_agg(row_to_json(t)) FROM (
            SELECT %s AS period, %s
            FROM erp_product_daily_stats
            WHERE org_id = $1
              AND stat_date >= $2 AND stat_date < $3
              %s
            GROUP BY %s
            ORDER BY %s
            LIMIT $4
        ) t',
        v_date_expr,
        v_select_cols,    -- SUM(order_count) AS order_count, SUM(order_amount) AS order_amount, ...
        v_where_extra,    -- AND outer_id = p_outer_id（如果有）
        v_date_expr,
        v_date_expr
    ) INTO v_result USING p_org_id, p_start, p_end, p_limit;

    RETURN COALESCE(v_result, '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
```

**返回结构**：
```json
[
    {"period": "2026-04-01", "order_count": 42, "order_amount": 15800.00},
    {"period": "2026-04-02", "order_count": 38, "order_amount": 12300.00},
    ...
]
```

**Python 层**：
```python
async def _query_trend(self, doc_type, filters, tr, time_granularity, metrics, group_by, limit):
    """趋势分析——按天/周/月聚合"""
    result = await self.db.rpc("erp_trend_query", {
        "p_org_id": self.org_id,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_granularity": time_granularity,   # day / week / month
        "p_metrics": metrics or ["order_count", "order_amount"],
        "p_group_by": group_by[0] if group_by else None,
        "p_outer_id": outer_id_filter,
        "p_limit": min(limit, 366),          # 最多一年的数据点
    }).execute()

    return ToolOutput(
        summary=format_trend_summary(result.data, time_granularity, metrics),
        format=OutputFormat.TABLE,
        data=result.data,
        columns=build_trend_columns(metrics),
        metadata={"query_type": "trend", "granularity": time_granularity},
    )
```

**边界处理**：
- 粒度 = day 且跨度 > 1 年 → 自动降为 month
- 粒度 = week 且跨度 < 7 天 → 自动升为 day
- 无 daily_stats 数据的日期 → 补零（Python 层 fillna）

### 5.4 对比分析（compare）—— 两次查询 + Python

**解决的问题**："这个月比上个月怎么样""同比增长多少""4月 vs 3月各平台对比"。

**实现策略**：不需要新 RPC。复用现有 `erp_global_stats_query` 或 `erp_trend_query`，调两次，Python 算差值。

```python
async def _query_compare(self, doc_type, filters, tr, compare_range, metrics, group_by, limit):
    """对比分析——环比/同比"""
    # 计算对比时间范围
    current_range = tr
    if compare_range == "mom":  # 环比（month over month）
        prev_range = shift_time_range(tr, months=-1)
    elif compare_range == "yoy":  # 同比（year over year）
        prev_range = shift_time_range(tr, years=-1)
    elif compare_range == "wow":  # 周环比
        prev_range = shift_time_range(tr, weeks=-1)

    # 并行查询两个时间段
    current_task = self._query_summary_internal(doc_type, filters, current_range, metrics, group_by)
    prev_task = self._query_summary_internal(doc_type, filters, prev_range, metrics, group_by)
    current_data, prev_data = await asyncio.gather(current_task, prev_task)

    # Python 计算差值和增长率
    compared = compute_comparison(current_data, prev_data, metrics)
    # compared = [
    #   {
    #     "group_key": "淘宝",
    #     "current_order_count": 150, "prev_order_count": 120,
    #     "order_count_change": 30, "order_count_growth": "25.0%",
    #     "current_order_amount": 50000, "prev_order_amount": 42000,
    #     "order_amount_change": 8000, "order_amount_growth": "19.0%",
    #   },
    #   ...
    # ]

    return ToolOutput(
        summary=format_compare_summary(compared, compare_range),
        format=OutputFormat.TABLE,
        data=compared,
        metadata={
            "query_type": "compare",
            "compare_range": compare_range,
            "current_period": f"{current_range.start_iso} ~ {current_range.end_iso}",
            "prev_period": f"{prev_range.start_iso} ~ {prev_range.end_iso}",
        },
    )
```

**返回示例**（"4月 vs 3月各平台销售额"）：
```
淘宝：4月 ¥50,000 → 3月 ¥42,000  ↑ 19.0%
抖音：4月 ¥32,000 → 3月 ¥35,000  ↓ 8.6%
拼多多：4月 ¥18,000 → 3月 ¥15,000  ↑ 20.0%
```

### 5.5 占比/排名分析（ratio）—— RPC + Python

**解决的问题**："各平台销售额占比""前20%的SKU贡献了多少销售额（帕累托）""ABC商品分类"。

**实现策略**：RPC 拿到分组聚合数据后，Python 层计算占比和累计。

```python
async def _query_ratio(self, doc_type, filters, tr, metrics, group_by, sort_by, limit):
    """占比/排名分析"""
    # 先拿分组聚合数据（复用现有 summary）
    raw_data = await self._query_summary_internal(
        doc_type, filters, tr, metrics, group_by, limit=limit or 100
    )

    metric_col = metrics[0] if metrics else "total_amount"

    # 计算占比
    total = sum(row[metric_col] for row in raw_data)
    for row in raw_data:
        row["ratio"] = round(row[metric_col] / total * 100, 1) if total else 0

    # 计算累计占比（帕累托/ABC）
    sorted_data = sorted(raw_data, key=lambda x: x[metric_col], reverse=True)
    cumulative = 0
    for row in sorted_data:
        cumulative += row[metric_col]
        row["cumulative_ratio"] = round(cumulative / total * 100, 1) if total else 0
        # ABC 分类
        if row["cumulative_ratio"] <= 80:
            row["abc_class"] = "A"
        elif row["cumulative_ratio"] <= 95:
            row["abc_class"] = "B"
        else:
            row["abc_class"] = "C"

    return ToolOutput(
        summary=format_ratio_summary(sorted_data, metric_col),
        format=OutputFormat.TABLE,
        data=sorted_data,
        metadata={"query_type": "ratio", "total": total},
    )
```

### 5.6 跨域指标（cross）—— daily_stats RPC

**解决的问题**："退货率""库存周转天数""毛利率""客单价""复购率""售后率"——这些指标需要跨 doc_type 计算，是电商老板最关心的核心指标。

**关键洞察**：`daily_stats` 表已经把 6 类单据的数据预聚合在同一行，**不需要真正的 JOIN**。

**支持的跨域指标（20 个）**：

| 指标 | 计算公式 | 数据来源 |
|------|---------|---------|
| **销售类** | | |
| 退货率 | aftersale_return_count / order_count | daily_stats |
| 退款率 | aftersale_refund_count / order_count | daily_stats |
| 换货率 | aftersale_exchange_count / order_count | daily_stats |
| 售后率 | aftersale_count / order_count | daily_stats |
| 客单价 | order_amount / order_count | daily_stats |
| 复购率 | COUNT(buyer购买≥2次) / COUNT(DISTINCT buyer) | erp_document_items（专用 RPC） |
| **利润类** | | |
| 毛利率 | (order_amount - order_cost) / order_amount | daily_stats |
| 毛利额 | order_amount - order_cost | daily_stats |
| **采购类** | | |
| 采购达成率 | receipt_count / purchase_count | daily_stats |
| 上架率 | shelf_count / receipt_count | daily_stats |
| 供应商到货率 | 已收货采购单数 / 采购总单数（按 supplier 分组） | erp_document_items（RPC） |
| 供应商退货率 | purchase_return_count / purchase_count（按 supplier 分组） | daily_stats |
| **库存类** | | |
| 库存周转天数 | available_qty / 日均 order_qty | stock + daily_stats |
| 动销率 | 有销量SKU数 / 总SKU数 | daily_stats + product |
| **履约类** | | |
| 平均发货时长 | AVG(consign_time - pay_time)（小时） | erp_document_items（专用 RPC） |
| 当日发货率 | COUNT(consign_time - pay_time < 24h) / COUNT(已发货) | erp_document_items（专用 RPC） |
| **进销存（复合视图）** | | |
| 商品进销存 | 进=SUM(purchase_qty), 销=SUM(order_qty), 存=available_qty | daily_stats + stock |

**新 RPC：erp_cross_metric_query**

```sql
CREATE OR REPLACE FUNCTION erp_cross_metric_query(
    p_org_id UUID,
    p_start DATE,
    p_end DATE,
    p_metric TEXT,                -- 指标名称
    p_group_by TEXT DEFAULT NULL, -- 分组维度：outer_id / NULL（总体）
    p_granularity TEXT DEFAULT NULL, -- 趋势粒度：day / week / month / NULL（汇总）
    p_outer_id TEXT DEFAULT NULL,
    p_limit INT DEFAULT 50
) RETURNS JSONB AS $$
DECLARE
    v_result JSONB;
    v_numerator TEXT;
    v_denominator TEXT;
    v_date_expr TEXT;
BEGIN
    -- 指标公式映射
    CASE p_metric
        WHEN 'return_rate' THEN
            v_numerator := 'SUM(aftersale_return_count)';
            v_denominator := 'NULLIF(SUM(order_count), 0)';
        WHEN 'refund_rate' THEN
            v_numerator := 'SUM(aftersale_refund_count)';
            v_denominator := 'NULLIF(SUM(order_count), 0)';
        WHEN 'aftersale_rate' THEN
            v_numerator := 'SUM(aftersale_count)';
            v_denominator := 'NULLIF(SUM(order_count), 0)';
        WHEN 'gross_margin' THEN
            v_numerator := 'SUM(order_amount) - SUM(order_cost)';
            v_denominator := 'NULLIF(SUM(order_amount), 0)';
        WHEN 'avg_order_value' THEN
            v_numerator := 'SUM(order_amount)';
            v_denominator := 'NULLIF(SUM(order_count), 0)';
        WHEN 'purchase_fulfillment' THEN
            v_numerator := 'SUM(receipt_count)';
            v_denominator := 'NULLIF(SUM(purchase_count), 0)';
        WHEN 'shelf_rate' THEN
            v_numerator := 'SUM(shelf_count)';
            v_denominator := 'NULLIF(SUM(receipt_count), 0)';
        -- ... 更多指标
    END CASE;

    -- 构建查询
    EXECUTE format(
        'SELECT jsonb_agg(row_to_json(t)) FROM (
            SELECT
                %s AS period,
                %s AS group_key,
                ROUND((%s)::numeric / (%s)::numeric * 100, 2) AS metric_value,
                (%s)::numeric AS numerator,
                (%s)::numeric AS denominator
            FROM erp_product_daily_stats
            WHERE org_id = $1
              AND stat_date >= $2 AND stat_date < $3
              %s
            GROUP BY %s
            ORDER BY %s
            LIMIT $4
        ) t',
        v_date_expr, v_group_expr,
        v_numerator, v_denominator,
        v_numerator, v_denominator,
        v_where_extra,
        v_group_clause,
        v_order_clause
    ) INTO v_result USING p_org_id, p_start, p_end, p_limit;

    RETURN COALESCE(v_result, '[]'::jsonb);
END;
$$ LANGUAGE plpgsql;
```

**Python 层**：
```python
async def _query_cross(self, doc_type, filters, tr, metrics, group_by, time_granularity, limit):
    """跨域指标——利用 daily_stats 预聚合"""
    metric = metrics[0]  # 如 "return_rate"

    # 特殊处理：库存周转天数需要 stock + daily_stats
    if metric == "inventory_turnover":
        return await self._query_inventory_turnover(filters, tr, group_by, limit)

    # 特殊处理：动销率需要 daily_stats + product
    if metric == "sell_through_rate":
        return await self._query_sell_through_rate(filters, tr, limit)

    # 通用路径：daily_stats RPC
    result = await self.db.rpc("erp_cross_metric_query", {
        "p_org_id": self.org_id,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_metric": metric,
        "p_group_by": group_by[0] if group_by else None,
        "p_granularity": time_granularity,
        "p_outer_id": outer_id_filter,
        "p_limit": limit,
    }).execute()

    return ToolOutput(
        summary=format_cross_summary(result.data, metric),
        data=result.data,
        metadata={"query_type": "cross", "metric": metric},
    )
```

**库存周转天数**（需要两个数据源）：
```python
async def _query_inventory_turnover(self, filters, tr, group_by, limit):
    """库存周转天数 = 当前库存 / 日均销量"""
    # 1. 当前库存（stock 表）
    stock_data = await self._query_stock_by_product(filters, limit)

    # 2. 日均销量（daily_stats 近30天）
    thirty_days_ago = tr.end - timedelta(days=30)
    daily_sales = await self.db.rpc("erp_trend_query", {
        "p_org_id": self.org_id,
        "p_start": thirty_days_ago.isoformat(),
        "p_end": tr.end_iso,
        "p_metrics": ["order_qty"],
        "p_group_by": "outer_id",
    }).execute()

    # 3. Python 合并计算
    sales_map = {r["group_key"]: r["order_qty"] / 30 for r in daily_sales.data}
    result = []
    for item in stock_data:
        daily_avg = sales_map.get(item["outer_id"], 0)
        turnover_days = round(item["available_qty"] / daily_avg, 1) if daily_avg > 0 else float("inf")
        result.append({
            "outer_id": item["outer_id"],
            "item_name": item.get("item_name", ""),
            "available_qty": item["available_qty"],
            "daily_avg_sales": round(daily_avg, 2),
            "turnover_days": turnover_days,
            "risk_level": "危险" if turnover_days < 7 else "警告" if turnover_days < 14 else "正常",
        })

    result.sort(key=lambda x: x["turnover_days"])
    return ToolOutput(
        summary=format_turnover_summary(result),
        data=result[:limit],
        metadata={"query_type": "cross", "metric": "inventory_turnover"},
    )
```

**复购率**（需要 erp_document_items 原始数据）：
```sql
-- 新 RPC：erp_repurchase_rate（或嵌入 erp_cross_metric_query）
-- 子查询：先按 buyer_nick 统计购买次数，再算比例
SELECT
    ROUND(
        COUNT(DISTINCT CASE WHEN cnt >= 2 THEN buyer_nick END)::numeric /
        NULLIF(COUNT(DISTINCT buyer_nick), 0) * 100, 2
    ) AS metric_value,
    COUNT(DISTINCT buyer_nick) AS total_buyers,
    COUNT(DISTINCT CASE WHEN cnt >= 2 THEN buyer_nick END) AS repeat_buyers
FROM (
    SELECT buyer_nick, COUNT(DISTINCT doc_id) AS cnt
    FROM erp_document_items
    WHERE doc_type = 'order' AND org_id = p_org_id
      AND doc_created_at >= p_start AND doc_created_at < p_end
      AND buyer_nick IS NOT NULL AND buyer_nick != ''
    GROUP BY buyer_nick
) sub;
```

**平均发货时长**（需要 erp_document_items 原始数据）：
```sql
-- 嵌入 erp_cross_metric_query 或独立 RPC
SELECT
    ROUND(AVG(EXTRACT(EPOCH FROM (consign_time - pay_time)) / 3600)::numeric, 1) AS avg_ship_hours,
    ROUND(
        COUNT(CASE WHEN consign_time - pay_time < INTERVAL '24 hours' THEN 1 END)::numeric /
        NULLIF(COUNT(*), 0) * 100, 2
    ) AS same_day_rate
FROM erp_document_items
WHERE doc_type = 'order' AND org_id = p_org_id
  AND pay_time IS NOT NULL AND consign_time IS NOT NULL
  AND doc_created_at >= p_start AND doc_created_at < p_end;
```

**商品进销存**（daily_stats + stock 复合视图）：
```python
async def _query_inventory_flow(self, filters, tr, group_by, limit):
    """商品进销存——进了多少/卖了多少/还剩多少"""
    # 1. 进+销（daily_stats 聚合）
    flow_data = await self.db.rpc("erp_trend_query", {
        "p_org_id": self.org_id,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_metrics": ["purchase_qty", "purchase_received_qty", "order_qty",
                       "purchase_return_qty", "aftersale_return_count"],
        "p_group_by": "outer_id",
        "p_limit": limit or 100,
    }).execute()

    # 2. 存（stock 表当前库存）
    stock_data = await self._query_stock_by_product(filters, limit=1000)
    stock_map = {s["outer_id"]: s for s in stock_data}

    # 3. 合并
    result = []
    for row in flow_data.data:
        oid = row["group_key"]
        stock = stock_map.get(oid, {})
        result.append({
            "outer_id": oid,
            "item_name": stock.get("item_name", ""),
            "purchased": row.get("purchase_qty", 0),          # 采购量
            "received": row.get("purchase_received_qty", 0),   # 收货量
            "sold": row.get("order_qty", 0),                   # 销售量
            "returned": row.get("aftersale_return_count", 0),  # 退货量
            "current_stock": stock.get("available_qty", 0),    # 当前库存
            "net_flow": row.get("purchase_received_qty", 0) - row.get("order_qty", 0),  # 净流入
        })

    return ToolOutput(
        summary=format_inventory_flow_summary(result),
        data=result,
        metadata={"query_type": "cross", "metric": "inventory_flow"},
    )
```

**供应商评估**（多指标组合）：
```python
async def _query_supplier_evaluation(self, filters, tr, limit):
    """供应商评估——到货率/退货率/交期"""
    # 利用 daily_stats 按 supplier 不直接可用（daily_stats 无 supplier 列）
    # 需要走 erp_global_stats_query RPC（group_by=supplier）
    purchase_stats = await self._query_summary_internal(
        "purchase", filters, tr, ["count", "amount"], ["supplier"], limit
    )
    purchase_return_stats = await self._query_summary_internal(
        "purchase_return", filters, tr, ["count", "amount"], ["supplier"], limit
    )

    # 合并计算
    return_map = {r["group_key"]: r for r in purchase_return_stats}
    result = []
    for row in purchase_stats:
        supplier = row["group_key"]
        ret = return_map.get(supplier, {})
        purchase_count = row.get("doc_count", 0)
        return_count = ret.get("doc_count", 0)
        result.append({
            "supplier_name": supplier,
            "purchase_count": purchase_count,
            "purchase_amount": row.get("total_amount", 0),
            "return_count": return_count,
            "return_rate": round(return_count / purchase_count * 100, 2) if purchase_count else 0,
        })

    result.sort(key=lambda x: x["return_rate"], reverse=True)
    return ToolOutput(
        summary=format_supplier_summary(result),
        data=result,
        metadata={"query_type": "cross", "metric": "supplier_evaluation"},
    )
```

### 5.7 预警查询（alert）—— 规则引擎

**解决的问题**："哪些SKU快卖断了""滞销商品有哪些""超卖风险商品"。

**支持的预警类型**：

| alert_type | 规则 | 数据源 |
|-----------|------|--------|
| `low_stock` | available_qty < safety_stock 或 < 日均销量×7 | stock + daily_stats |
| `slow_moving` | 近 N 天零销量的 SKU | daily_stats |
| `overstock` | available_qty > 日均销量×90 | stock + daily_stats |
| `out_of_stock` | available_qty = 0 且近30天有销量 | stock + daily_stats |
| `purchase_overdue` | 采购单超期未到货（delivery_date < today 且 doc_status 非已完成） | erp_document_items |

```python
async def _query_alert(self, alert_type, filters, tr, limit):
    """预警查询——规则引擎"""

    if alert_type == "low_stock":
        # 查库存 + 近30天日均销量
        stock = await self._query_stock_all(filters)
        daily_sales = await self._get_daily_avg_sales(days=30)

        alerts = []
        for item in stock:
            daily_avg = daily_sales.get(item["outer_id"], 0)
            days_left = item["available_qty"] / daily_avg if daily_avg > 0 else float("inf")
            if days_left < 14:  # 两周内卖完
                alerts.append({
                    "outer_id": item["outer_id"],
                    "item_name": item.get("item_name", ""),
                    "available_qty": item["available_qty"],
                    "daily_avg_sales": round(daily_avg, 2),
                    "days_left": round(days_left, 1),
                    "severity": "critical" if days_left < 3 else "warning" if days_left < 7 else "info",
                    "suggestion": f"建议补货 {round(daily_avg * 30 - item['available_qty'])} 件（30天用量）",
                })

        alerts.sort(key=lambda x: x["days_left"])
        return ToolOutput(
            summary=format_alert_summary(alerts, "low_stock"),
            data=alerts[:limit],
            metadata={"query_type": "alert", "alert_type": alert_type, "total_alerts": len(alerts)},
        )

    elif alert_type == "slow_moving":
        # 近 N 天零销量（默认30天）
        active_skus = await self._get_active_skus(days=30)      # daily_stats 有销量的
        all_skus = await self._get_all_product_skus(filters)     # product 表所有 SKU
        slow = [s for s in all_skus if s["outer_id"] not in active_skus]
        # 附加库存量，帮助判断风险
        stock_map = await self._get_stock_map()
        for s in slow:
            s["available_qty"] = stock_map.get(s["outer_id"], {}).get("available_qty", 0)
            s["severity"] = "critical" if s["available_qty"] > 100 else "warning" if s["available_qty"] > 0 else "info"
        slow.sort(key=lambda x: x["available_qty"], reverse=True)  # 库存越多越紧急（资金占用）
        return ToolOutput(
            summary=format_alert_summary(slow, "slow_moving"),
            data=slow[:limit],
            metadata={"query_type": "alert", "alert_type": alert_type, "total_alerts": len(slow)},
        )

    elif alert_type == "purchase_overdue":
        # 采购单超期未到货
        today = date.today().isoformat()
        q = self.db.table("erp_document_items") \
            .select("doc_code,supplier_name,item_name,outer_id,quantity,delivery_date,doc_created_at", count="exact") \
            .eq("org_id", self.org_id) \
            .eq("doc_type", "purchase") \
            .lt("delivery_date", today) \
            .not_.in_("doc_status", ["已完成", "已关闭", "已取消"]) \
            .order("delivery_date", desc=False) \
            .limit(limit or 100)
        rows = q.execute().data
        for r in rows:
            overdue_days = (date.today() - date.fromisoformat(r["delivery_date"])).days
            r["overdue_days"] = overdue_days
            r["severity"] = "critical" if overdue_days > 14 else "warning" if overdue_days > 7 else "info"
        return ToolOutput(
            summary=format_alert_summary(rows, "purchase_overdue"),
            data=rows,
            metadata={"query_type": "alert", "alert_type": alert_type, "total_alerts": len(rows)},
        )

    elif alert_type == "overstock":
        # 库存 > 日均销量×90（资金占用风险）
        stock = await self._query_stock_all(filters)
        daily_sales = await self._get_daily_avg_sales(days=30)
        alerts = []
        for item in stock:
            daily_avg = daily_sales.get(item["outer_id"], 0)
            if daily_avg > 0 and item["available_qty"] > daily_avg * 90:
                alerts.append({
                    "outer_id": item["outer_id"],
                    "item_name": item.get("item_name", ""),
                    "available_qty": item["available_qty"],
                    "daily_avg_sales": round(daily_avg, 2),
                    "days_of_stock": round(item["available_qty"] / daily_avg, 0),
                    "excess_qty": round(item["available_qty"] - daily_avg * 90),
                    "severity": "warning",
                })
        alerts.sort(key=lambda x: x["days_of_stock"], reverse=True)
        return ToolOutput(
            summary=format_alert_summary(alerts, "overstock"),
            data=alerts[:limit],
            metadata={"query_type": "alert", "alert_type": alert_type, "total_alerts": len(alerts)},
        )

    elif alert_type == "out_of_stock":
        # 库存=0 但近30天有销量（热销断货）
        stock = await self._query_stock_all(filters)
        active_skus = await self._get_active_skus(days=30)
        zero_but_active = [
            {**s, "severity": "critical"}
            for s in stock
            if s["available_qty"] == 0 and s["outer_id"] in active_skus
        ]
        return ToolOutput(
            summary=format_alert_summary(zero_but_active, "out_of_stock"),
            data=zero_but_active[:limit],
            metadata={"query_type": "alert", "alert_type": alert_type, "total_alerts": len(zero_but_active)},
        )
```

### 5.8 大批量导出（export）—— 双引擎 + 取消上限

**v1.0 的问题**：DuckDB postgres_scanner 远程扫描上限 ~30k 行，超限只能拒绝。

**v2.1 改进**：30k 以下保留 DuckDB（成熟稳定），30k 以上改用 PG COPY 流式（无上限）。

```
limit > 200 时进入导出路径：
  ├─ 200 < limit ≤ 30000 → DuckDB 导出（现有路径，不改）
  └─ limit > 30000 → PG COPY 流式导出（新路径，无上限）
```

**超限分片建议删除** —— 用户想导出 50 万行就导出 50 万行，不需要再建议分片。

#### 5.8.1 PG COPY 流式导出——技术实现

**前置条件**（全部已满足）：
- `psycopg[binary,pool]==3.3.3` ✅ 已安装
- `pyarrow==23.0.1` ✅ 已安装
- `DATABASE_URL=postgresql://...@127.0.0.1:5432/...` ✅ 本机直连
- PII 脱敏映射（`_PII_SQL_MAP`）✅ 已有（erp_duckdb_helpers.py）
- 字段翻译映射（`PLATFORM_CN` / `_STATUS_CN` 等）✅ 已有

**核心流程**：

```
① 构建 SELECT SQL（复用 build_export_where + build_pii_select 的 Python 版）
② psycopg3 async 直连本机 PG（DATABASE_URL）
③ COPY (SELECT ... UNION ALL archive ...) TO STDOUT
④ async for row in copy.rows()：流式逐行读取
⑤ 每 BATCH_SIZE 行：PII 脱敏 + 字段翻译 → pyarrow Table → 写入 parquet
⑥ 内存恒定 ~5 MB（不管总行数多少）
⑦ 完成 → FileRef → 返回
```

**实现代码**：

```python
# 新文件：backend/services/kuaimai/erp_copy_export.py

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg

from core.config import get_settings
from loguru import logger
from services.kuaimai.erp_duckdb_helpers import (
    resolve_export_path, build_export_where,
)
from services.kuaimai.erp_unified_schema import (
    PLATFORM_CN, _FIELD_LABEL_CN, ValidatedFilter, TimeRange,
)

BATCH_SIZE = 10_000  # 每批 1 万行，内存 ≈ 5 MB


# ── PII 脱敏（Python 层，对应 DuckDB 路径的 build_pii_select）──

_PII_FIELDS = {"receiver_name", "receiver_mobile", "receiver_phone", "receiver_address"}

def _mask_pii_value(field: str, value: str | None) -> str | None:
    """单字段 PII 脱敏——与 DuckDB SQL 版逻辑完全一致。"""
    if value is None:
        return None
    if field == "receiver_name" and len(value) >= 2:
        return value[0] + "*" * (len(value) - 1)
    if field in ("receiver_mobile", "receiver_phone") and len(value) >= 7:
        return value[:3] + "****" + value[-4:]
    if field == "receiver_address" and len(value) >= 6:
        return value[:6] + "****"
    return value


# ── 字段翻译（Python 层，映射表复用 erp_duckdb_helpers 已有常量）──

from services.kuaimai.erp_duckdb_helpers import (
    _STATUS_CN, _ORDER_TYPE_CN, _AFTERSALE_TYPE_CN,
    _REFUND_STATUS_CN, _GOOD_STATUS_CN,
)

_BOOL_FIELDS = {
    "is_cancel", "is_refund", "is_exception", "is_halt",
    "is_urgent", "is_scalping", "is_presell",
}

def _translate_row(row: dict) -> dict:
    """字段翻译——与 DuckDB SQL CASE 表达式逻辑一致。"""
    if "platform" in row and row["platform"]:
        row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])
    if "doc_status" in row and row["doc_status"]:
        row["doc_status"] = _STATUS_CN.get(row["doc_status"], row["doc_status"])
    if "order_status" in row and row["order_status"]:
        row["order_status"] = _STATUS_CN.get(row["order_status"], row["order_status"])
    if "order_type" in row and row["order_type"]:
        parts = str(row["order_type"]).split(",")
        translated = [_ORDER_TYPE_CN.get(p.strip(), None) for p in parts]
        row["order_type"] = "/".join(filter(None, translated)) or row["order_type"]
    if "aftersale_type" in row and row["aftersale_type"] is not None:
        row["aftersale_type"] = _AFTERSALE_TYPE_CN.get(
            str(row["aftersale_type"]), str(row["aftersale_type"])
        )
    if "refund_status" in row and row["refund_status"] is not None:
        row["refund_status"] = _REFUND_STATUS_CN.get(
            str(row["refund_status"]), str(row["refund_status"])
        )
    if "good_status" in row and row["good_status"] is not None:
        row["good_status"] = _GOOD_STATUS_CN.get(
            str(row["good_status"]), str(row["good_status"])
        )
    for bf in _BOOL_FIELDS:
        if bf in row and row[bf] is not None:
            row[bf] = "是" if row[bf] in (1, True, "1") else "否"
    return row


# ── 核心导出函数 ──

async def copy_streaming_export(
    doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    org_id: str | None,
    columns: list[str],
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int | None = None,
    push_thinking: Any = None,
) -> dict:
    """PG COPY 流式导出——突破 DuckDB 30k 行限制。

    本机 PG 直连，COPY TO STDOUT 流式读取，
    pyarrow 分批写入 parquet，内存恒定 ~5 MB。

    Returns:
        {"row_count": int, "size_kb": float, "path": str}
    """
    settings = get_settings()
    t0 = asyncio.get_event_loop().time()

    # 1. 构建 SQL（复用现有 WHERE 构建逻辑）
    where = build_export_where(doc_type, filters, tr, org_id)
    select_cols = ", ".join(columns)

    # 归档表支持：90 天前的数据在 archive 表
    need_archive = _need_archive(tr)
    if need_archive:
        sql = (
            f"SELECT {select_cols} FROM erp_document_items WHERE {where} "
            f"UNION ALL "
            f"SELECT {select_cols} FROM erp_document_items_archive WHERE {where}"
        )
    else:
        sql = f"SELECT {select_cols} FROM erp_document_items WHERE {where}"

    if sort_by and sort_by in columns:
        sql += f" ORDER BY {sort_by} {sort_dir}"
    if limit:
        sql += f" LIMIT {limit}"

    copy_sql = f"COPY ({sql}) TO STDOUT"

    # 2. 构建 pyarrow schema（所有列用 string，翻译后的值都是文本）
    cn_columns = [_FIELD_LABEL_CN.get(c, c) for c in columns]
    arrow_schema = pa.schema([(cn, pa.string()) for cn in cn_columns])

    # 3. 流式导出
    staging_path = _resolve_staging(doc_type, org_id)
    writer = pq.ParquetWriter(str(staging_path), arrow_schema, compression="snappy")
    total_rows = 0
    batch_rows: list[dict] = []

    if push_thinking:
        await push_thinking("正在连接数据库...")

    async with await psycopg.AsyncConnection.connect(
        settings.database_url,
        autocommit=True,  # COPY 不需要事务
    ) as conn:
        async with conn.cursor() as cur:
            async with cur.copy(copy_sql) as copy:
                # set_types 让 psycopg3 解析为 Python 对象（而非原始字符串）
                # 这里用文本格式，所有值返回为字符串，翻译后直接写 parquet
                async for row in copy.rows():
                    row_dict = dict(zip(columns, row))

                    # PII 脱敏
                    for pii_field in _PII_FIELDS:
                        if pii_field in row_dict:
                            row_dict[pii_field] = _mask_pii_value(
                                pii_field, str(row_dict[pii_field]) if row_dict[pii_field] else None
                            )

                    # 字段翻译
                    row_dict = _translate_row(row_dict)

                    # 列名翻译（英文 → 中文）
                    cn_row = {
                        _FIELD_LABEL_CN.get(k, k): (str(v) if v is not None else None)
                        for k, v in row_dict.items()
                    }
                    batch_rows.append(cn_row)

                    if len(batch_rows) >= BATCH_SIZE:
                        _write_batch(writer, batch_rows, arrow_schema)
                        total_rows += len(batch_rows)
                        batch_rows.clear()

                        # 进度报告
                        if push_thinking:
                            elapsed = asyncio.get_event_loop().time() - t0
                            await push_thinking(
                                f"正在导出... {total_rows:,} 行（{elapsed:.0f}s）"
                            )

                # 最后一批
                if batch_rows:
                    _write_batch(writer, batch_rows, arrow_schema)
                    total_rows += len(batch_rows)

    writer.close()
    size_kb = staging_path.stat().st_size / 1024
    elapsed = asyncio.get_event_loop().time() - t0

    logger.info(
        f"COPY streaming export done | rows={total_rows:,} "
        f"size={size_kb:.0f}KB elapsed={elapsed:.1f}s"
    )

    if push_thinking:
        if size_kb > 1024:
            await push_thinking(f"导出完成：{total_rows:,} 行，{size_kb/1024:.1f}MB（{elapsed:.0f}s）")
        else:
            await push_thinking(f"导出完成：{total_rows:,} 行，{size_kb:.0f}KB（{elapsed:.0f}s）")

    return {
        "row_count": total_rows,
        "size_kb": round(size_kb, 1),
        "path": str(staging_path),
    }


def _write_batch(writer: pq.ParquetWriter, rows: list[dict], schema: pa.Schema):
    """将一批行写入 parquet。"""
    table = pa.Table.from_pylist(rows, schema=schema)
    writer.write_table(table)


def _need_archive(tr: TimeRange) -> bool:
    """判断是否需要查归档表（时间范围早于 90 天前）。"""
    from datetime import timedelta
    try:
        start = datetime.fromisoformat(tr.start_iso.replace("Z", "+00:00"))
        cutoff = datetime.now(start.tzinfo) - timedelta(days=90)
        return start < cutoff
    except (ValueError, AttributeError):
        return False


def _resolve_staging(doc_type: str, org_id: str | None) -> Path:
    """生成 staging 路径（复用现有逻辑）。"""
    import time
    from core.config import get_settings
    settings = get_settings()
    staging_dir = Path(settings.file_workspace_root) / "staging" / "copy_export"
    staging_dir.mkdir(parents=True, exist_ok=True)
    filename = f"export_{doc_type}_{int(time.time())}.parquet"
    return staging_dir / filename
```

#### 5.8.2 与 DuckDB 路径的对比

| 维度 | DuckDB 导出（200~30k） | COPY 流式（>30k） |
|------|----------------------|-------------------|
| 连接方式 | DuckDB ATTACH PG（子进程隔离） | psycopg3 直连本机 PG |
| 内存模型 | DuckDB 物化后写 parquet（256MB 上限） | 流式 batch，恒定 ~5 MB |
| PII 脱敏 | SQL CASE 表达式（build_pii_select） | Python `_mask_pii_value()`（同一映射） |
| 字段翻译 | SQL CASE 表达式（_SPECIAL_CASE_MAP） | Python `_translate_row()`（同一映射） |
| 归档表 | DuckDB UNION ALL | PG 子查询 UNION ALL |
| 行数上限 | ~30k（网络+内存限制） | **无上限**（本机流式） |
| 进度报告 | 文件大小监控（5s 间隔） | 行数计数（每 batch 报告） |
| 超时 | threading.Timer + interrupt | asyncio.wait_for |
| 文件统计 | DuckDB SUMMARIZE（profile_parquet） | DuckDB SUMMARIZE（复用同一方法） |

#### 5.8.3 为什么保留 DuckDB 路径（200~30k）

不全部切到 COPY 流式的原因：
1. DuckDB 路径**已生产验证 6 个月**，PII 脱敏和字段翻译在 SQL 层做，经过了完整测试
2. DuckDB 的 COPY TO parquet 是**原子操作**，比 pyarrow 分批写更高效（对 ≤30k 行场景）
3. DuckDB 子进程**内存隔离**，不影响 chat worker
4. 30k 以下数据量，两种方式体验差异不大（都是几秒完成）

**渐进式替换策略**：先在 >30k 场景启用 COPY 流式，验证稳定后再考虑统一。

#### 5.8.4 性能预估（本机 localhost）

| 数据量 | COPY 流式耗时 | 内存 | parquet 大小 |
|--------|-------------|------|-------------|
| 3 万行 | ~2 秒 | ~5 MB | ~8 MB |
| 10 万行 | ~5 秒 | ~5 MB | ~25 MB |
| 30 万行 | ~15 秒 | ~5 MB | ~80 MB |
| 50 万行 | ~25 秒 | ~5 MB | ~130 MB |
| 100 万行 | ~50 秒 | ~5 MB | ~250 MB |

本机 localhost 传输速度远快于远程，预估比 DuckDB 远程扫描快 3-5 倍。

#### 5.8.5 费用影响

| 资源 | 消耗 | 费用 |
|------|------|------|
| 网络流量 | localhost 回环，不经公网 | **$0** |
| 内存 | 恒定 ~5 MB | 忽略 |
| CPU | PII 脱敏 + parquet 编码 | 忽略 |
| PG 负载 | 顺序扫描 + COPY 流式（不锁表） | 低 |
| **总额外成本** | | **$0** |

### 5.9 分布直方图（distribution）—— RPC CASE WHEN 分桶

**解决的问题**："订单金额在 0-100 / 100-500 / 500+ 的各有多少""客单价分布"。

**实现策略**：在 RPC 中用 CASE WHEN 分桶，返回各区间的计数。

**query_type = "distribution"**（新增第 9 种查询类型）

```python
async def _query_distribution(self, doc_type, filters, tr, metrics, limit):
    """分布直方图——按数值区间分桶"""
    metric = metrics[0] if metrics else "amount"

    # 预定义分桶规则
    BUCKET_RULES = {
        "amount": [0, 50, 100, 200, 500, 1000, 5000, float("inf")],
        "quantity": [0, 1, 5, 10, 50, 100, float("inf")],
        "days_left": [0, 3, 7, 14, 30, 90, float("inf")],  # 库存天数分布
    }
    buckets = BUCKET_RULES.get(metric, [0, 100, 500, 1000, 5000, float("inf")])

    result = await self.db.rpc("erp_distribution_query", {
        "p_org_id": self.org_id,
        "p_doc_type": doc_type,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_field": metric,         # 分桶字段
        "p_buckets": buckets[:-1],  # 不含 inf
        "p_filters": json.dumps(filters),
    }).execute()

    return ToolOutput(
        summary=format_distribution_summary(result.data, metric),
        format=OutputFormat.TABLE,
        data=result.data,
        metadata={"query_type": "distribution", "field": metric, "buckets": buckets},
    )
```

**RPC 实现**：
```sql
-- erp_distribution_query
-- 动态生成 CASE WHEN 分桶
-- 返回：[{"bucket": "0-50", "count": 120}, {"bucket": "50-100", "count": 85}, ...]
SELECT
    CASE
        WHEN amount < 50 THEN '0-50'
        WHEN amount < 100 THEN '50-100'
        WHEN amount < 200 THEN '100-200'
        WHEN amount < 500 THEN '200-500'
        WHEN amount < 1000 THEN '500-1000'
        ELSE '1000+'
    END AS bucket,
    COUNT(*) AS count,
    SUM(amount) AS bucket_total
FROM erp_document_items
WHERE doc_type = p_doc_type AND org_id = p_org_id
  AND doc_created_at >= p_start AND doc_created_at < p_end
GROUP BY bucket
ORDER BY MIN(amount);
```

## 6. PlanBuilder 参数提取扩展

LLM 需要从用户自然语言中提取新增参数。扩展 PlanBuilder 提示词：

### 6.1 新增参数关键词映射

```python
# plan_builder.py 扩展
QUERY_TYPE_KEYWORDS = {
    "trend": ["趋势", "变化", "走势", "每天", "每周", "每月", "曲线", "波动"],
    "compare": ["对比", "比较", "同比", "环比", "增长", "下降", "变化率", "vs", "上个月", "去年"],
    "ratio": ["占比", "比例", "份额", "ABC", "帕累托", "贡献度", "百分比"],
    "cross": ["退货率", "毛利率", "客单价", "复购率", "售后率", "周转", "达成率", "动销率",
              "发货时效", "发货时长", "进销存", "供应商评估"],
    "alert": ["预警", "断货", "缺货", "滞销", "超卖", "卖断", "补货", "快没了", "库存不足",
              "采购超期", "未到货"],
    "distribution": ["分布", "区间", "直方图", "分段", "范围分布", "金额分布"],
}

TIME_GRANULARITY_KEYWORDS = {
    "day": ["每天", "日", "逐日", "daily", "按天"],
    "week": ["每周", "周", "逐周", "weekly", "按周"],
    "month": ["每月", "月", "逐月", "monthly", "按月", "月度"],
}

COMPARE_RANGE_KEYWORDS = {
    "mom": ["环比", "上个月", "上月", "月环比", "比上个月"],
    "yoy": ["同比", "去年", "同期", "去年同月", "年同比"],
    "wow": ["周环比", "上周", "比上周"],
}

METRIC_KEYWORDS = {
    # 销售类
    "return_rate": ["退货率", "退货比例"],
    "refund_rate": ["退款率", "退款比例"],
    "aftersale_rate": ["售后率", "售后比例"],
    "avg_order_value": ["客单价", "均价", "平均订单金额"],
    "repurchase_rate": ["复购率", "回头客", "复购"],
    # 利润类
    "gross_margin": ["毛利率", "毛利", "利润率"],
    # 采购类
    "purchase_fulfillment": ["采购达成率", "到货率"],
    "supplier_evaluation": ["供应商评估", "供应商考核", "供应商退货率"],
    # 库存类
    "inventory_turnover": ["库存周转", "周转天数", "周转率"],
    "sell_through_rate": ["动销率", "动销"],
    "inventory_flow": ["进销存", "进出存", "进货出货库存"],
    # 履约类
    "avg_ship_time": ["发货时效", "发货时长", "平均发货", "发货速度"],
    "same_day_rate": ["当日发货率", "当天发货"],
}

ALERT_TYPE_KEYWORDS = {
    "low_stock": ["缺货", "断货", "库存不足", "快没了", "补货"],
    "slow_moving": ["滞销", "卖不动", "零销量", "不动销"],
    "overstock": ["积压", "库存过多", "超库存"],
    "out_of_stock": ["售罄", "卖完了", "没库存"],
    "purchase_overdue": ["采购超期", "采购未到", "逾期未到货", "催货"],
}
```

### 6.2 LLM 提取 prompt 扩展

在现有 PlanBuilder 的 few-shot 示例中新增：

```
用户：每天的销售额趋势
→ doc_type=daily_stats, query_type=trend, time_granularity=day, metrics=[order_amount]

用户：这个月比上个月各平台销售额怎么样
→ doc_type=order, query_type=compare, compare_range=mom, group_by=[platform], metrics=[amount]

用户：哪些商品退货率最高
→ doc_type=daily_stats, query_type=cross, metrics=[return_rate], group_by=[outer_id], sort_by=metric_value, sort_dir=desc

用户：哪些SKU快卖断了
→ query_type=alert, alert_type=low_stock

用户：各平台销售额占多少
→ doc_type=order, query_type=ratio, group_by=[platform], metrics=[amount]

用户：商品的ABC分类
→ doc_type=daily_stats, query_type=ratio, group_by=[outer_id], metrics=[order_amount]

用户：发货一般多久
→ query_type=cross, metrics=[avg_ship_time]

用户：这个商品的进销存情况
→ query_type=cross, metrics=[inventory_flow], outer_id=xxx

用户：订单金额分布
→ doc_type=order, query_type=distribution, metrics=[amount]

用户：哪些采购单超期了
→ query_type=alert, alert_type=purchase_overdue

用户：供应商退货率排名
→ query_type=cross, metrics=[supplier_evaluation], sort_by=return_rate, sort_dir=desc

用户：复购率多少
→ query_type=cross, metrics=[repurchase_rate]
```

## 7. 端到端数据流

### 场景 1："4月金额最高5笔订单"（detail）
```
① PlanBuilder → query_type=detail(auto推断), doc_type=order, sort_by=amount, limit=5
② execute() → _resolve_query_type → "detail"
③ _query_detail() → ORM 直查 PG → 5 行（秒级）
④ mask_pii + translate_fields → 写 staging → inline 返回
```

### 场景 2："每天的销售额趋势"（trend）
```
① PlanBuilder → query_type=trend, time_granularity=day, metrics=[order_amount]
② execute() → "trend"
③ _query_trend() → RPC erp_trend_query(daily_stats) → 27 行（当月每天）
④ 补零 + 格式化 → inline 返回（可渲染为折线图）
```

### 场景 3："这个月比上个月各平台销售额对比"（compare）
```
① PlanBuilder → query_type=compare, compare_range=mom, group_by=[platform]
② execute() → "compare"
③ _query_compare():
   → 并行查 4月 RPC + 3月 RPC
   → Python 计算差值和增长率
④ 返回：淘宝 ↑19% / 抖音 ↓8.6% / 拼多多 ↑20%
```

### 场景 4："各平台的退货率"（cross）
```
① PlanBuilder → query_type=cross, metrics=[return_rate], group_by=[platform]
   注意：不再走 NEED_CODE 兜底！
② execute() → "cross"
③ _query_cross():
   → RPC erp_cross_metric_query(daily_stats, metric=return_rate, group_by=outer_id→platform)
   注意：daily_stats 按 outer_id 存，平台信息需要 JOIN product 表
   → 方案：先按 outer_id 聚合，再从 erp_products 取 platform 映射，Python 端按 platform 二次聚合
④ 返回：淘宝退货率 3.2% / 抖音退货率 8.5% / 拼多多退货率 5.1%
```

### 场景 5："哪些SKU快卖断了"（alert）
```
① PlanBuilder → query_type=alert, alert_type=low_stock
② execute() → "alert"
③ _query_alert():
   → stock 表查当前库存
   → daily_stats 近30天算日均销量
   → 规则判断：days_left < 14 → 预警
④ 返回：3个SKU库存告急（XX 还能卖2天，YY 还能卖5天...）
```

### 场景 6："复购率多少"（cross - 复购率）
```
① PlanBuilder → query_type=cross, metrics=[repurchase_rate]
② execute() → "cross"
③ _query_cross() → metric=repurchase_rate → 走专用 RPC（buyer_nick 子查询）
④ 返回：4月复购率 23.5%（总客户 1,200，复购客户 282）
```

### 场景 7："发货一般多快"（cross - 发货时效）
```
① PlanBuilder → query_type=cross, metrics=[avg_ship_time]
② execute() → "cross"
③ _query_cross() → 走发货时效 RPC（AVG consign_time - pay_time）
④ 返回：平均发货时长 18.3 小时，当日发货率 72.5%
```

### 场景 8："这个商品进销存情况"（cross - 进销存）
```
① PlanBuilder → query_type=cross, metrics=[inventory_flow], outer_id=xxx
② execute() → "cross"
③ _query_inventory_flow():
   → daily_stats 聚合（purchase_qty, order_qty）
   → stock 表查 available_qty
④ 返回：采购 500 件 / 已收 480 件 / 销售 350 件 / 当前库存 130 件
```

### 场景 9："订单金额分布"（distribution）
```
① PlanBuilder → query_type=distribution, doc_type=order, metrics=[amount]
② execute() → "distribution"
③ _query_distribution() → RPC CASE WHEN 分桶
④ 返回：0-50元 120单 / 50-100元 85单 / 100-200元 63单 / ...
```

### 场景 10："哪些采购单超期了"（alert - 采购超期）
```
① PlanBuilder → query_type=alert, alert_type=purchase_overdue
② execute() → "alert"
③ _query_alert() → ORM 查 delivery_date < today 且未完成
④ 返回：3笔采购超期（PO-001 超期12天/PO-005 超期5天/...）
```

### 场景 11："导出4月全部订单"（export，≤30k 走 DuckDB）
```
① PlanBuilder → mode=export, limit=30000
② execute() → limit ≤ 30000 → "export"
③ DuckDB 路径不变 → Parquet → FileRef
```

### 场景 12："导出全年50万行订单"（export_large，>30k 走 COPY 流式）
```
① PlanBuilder → mode=export, limit=500000
② execute() → limit > 30000 → "export_large"
③ copy_streaming_export():
   → psycopg3 直连本机 PG（DATABASE_URL）
   → COPY (SELECT ... WHERE ... UNION ALL archive ...) TO STDOUT
   → async for row in copy.rows()：逐行流式
   → 每 10000 行 → PII 脱敏 + 字段翻译 → pyarrow → parquet
   → 内存恒定 ~5 MB，总耗时 ~25 秒
④ 返回 FileRef（~130 MB parquet）
```

## 8. 旧表 PG 直查的特殊处理

（与 v1.0 相同，此处简化）

### 8.1 PII 脱敏
`mask_pii()` Python 层逐行脱敏，复用 erp_unified_schema.py 已有实现。

### 8.2 字段翻译
Python 层用 `PLATFORM_CN` / `DOC_TYPE_CN` 等已有映射表翻译。新增 `erp_field_translator.py`。

### 8.3 归档表查询
PG 直查路径分别查主表+归档表 + Python 合并。limit≤200 时开销可忽略。

## 9. daily_stats 维度扩展——加 platform + shop_name 列

**问题**：daily_stats 按 `(stat_date, outer_id, sku_outer_id)` 聚合，没有 `platform` 和 `shop_name` 字段。用户问"各平台退货率""各店铺的销售额趋势"时，需要这些维度。

**决策：方案 C（一劳永逸）—— daily_stats 加 platform + shop_name 列**

| 方案 | 实现 | 优缺点 |
|------|------|--------|
| ~~A. RPC 内 JOIN product 表~~ | `JOIN erp_products ON outer_id` | 每次查询都要 JOIN，platform 来自 product 而非订单实际平台 |
| ~~B. Python 端二次聚合~~ | 多次往返 + Python 聚合 | 数据量大时慢，代码复杂 |
| **C. daily_stats 加 platform + shop_name 列** | 聚合时直接写入 | **一劳永逸**，所有趋势/跨域查询变成单表聚合 |

**选择方案 C 的理由**：
1. platform 低基数（~5-8 个值），shop_name 中基数（~10-50 个店铺），行数增长可控
2. 聚合时 platform/shop_name 直接从 erp_document_items 取（订单实际平台，而非商品注册平台），更准确
3. 所有 trend/cross/alert 按 platform/shop 分组变成单表 GROUP BY，零 JOIN，性能最优
4. 后续不需要反复改 RPC 来支持新的分组维度

### 9.1 迁移方案

```sql
-- 迁移文件：xxx_daily_stats_add_dimensions.sql

-- 1. 加列
ALTER TABLE erp_product_daily_stats
    ADD COLUMN platform VARCHAR(32),
    ADD COLUMN shop_name VARCHAR(256);

-- 2. 重建唯一约束（原来是 stat_date + outer_id + sku_outer_id）
ALTER TABLE erp_product_daily_stats
    DROP CONSTRAINT IF EXISTS uq_daily_stats;
ALTER TABLE erp_product_daily_stats
    ADD CONSTRAINT uq_daily_stats
    UNIQUE (org_id, stat_date, outer_id, COALESCE(sku_outer_id, ''), COALESCE(platform, ''), COALESCE(shop_name, ''));

-- 3. 新增索引
CREATE INDEX IF NOT EXISTS idx_daily_stats_platform
    ON erp_product_daily_stats (org_id, platform, stat_date);
CREATE INDEX IF NOT EXISTS idx_daily_stats_shop
    ON erp_product_daily_stats (org_id, shop_name, stat_date);
```

### 9.2 历史数据回填

```python
async def backfill_daily_stats_dimensions(db, org_id):
    """一次性回填历史 daily_stats 的 platform + shop_name"""
    # 从 erp_document_items 按 (stat_date, outer_id, platform, shop_name) 重新聚合
    # 写入新的 daily_stats 行（ON CONFLICT UPDATE）
    # 预计耗时：10-30 分钟（视数据量）
```

### 9.3 聚合逻辑修改

修改 `erp_aggregate_daily_stats()` 定时任务，GROUP BY 加上 `platform, shop_name`：

```sql
-- 现在：GROUP BY stat_date, outer_id, sku_outer_id
-- 改后：GROUP BY stat_date, outer_id, sku_outer_id, platform, shop_name
```

### 9.4 对 RPC 的影响

`erp_trend_query` 和 `erp_cross_metric_query` 的 `p_group_by` 现在可以直接支持：
- `platform` → `GROUP BY d.platform`（单表，不需要 JOIN）
- `shop` → `GROUP BY d.shop_name`（单表，不需要 JOIN）
- `outer_id` → `GROUP BY d.outer_id`（原有）

```sql
-- 按平台查退货率——纯单表聚合
SELECT
    platform AS group_key,
    ROUND(SUM(aftersale_count)::numeric / NULLIF(SUM(order_count), 0) * 100, 2) AS metric_value
FROM erp_product_daily_stats
WHERE org_id = p_org_id
  AND stat_date >= p_start AND stat_date < p_end
GROUP BY platform
ORDER BY metric_value DESC;
```

### 9.5 行数增长评估

| 维度 | 原粒度 | 新粒度 | 行数倍增 |
|------|--------|--------|---------|
| 原始 | stat_date × outer_id × sku | — | 1x（基准） |
| + platform | stat_date × outer_id × sku × platform | 大部分商品 1 个平台 | ~1.5x |
| + shop_name | stat_date × outer_id × sku × platform × shop | 每平台通常 1-3 个店 | ~2-3x |

daily_stats 目前数据量不大（按天×商品），2-3 倍增长完全在 PG 承受范围内。

## 10. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| limit=5 + sort_by + 34万行底层数据 | PG 直查（Top-N HeapSort 秒级） | _query_detail |
| limit=200 边界值 | ≤200 走 PG，201+ 走 DuckDB | execute() 路由 |
| trend + 跨度>1年 | 自动降为 month 粒度 | _query_trend |
| trend + 跨度<7天 + 粒度=week | 自动升为 day | _query_trend |
| compare 的 prev 时间段无数据 | 返回 prev=0，growth="+∞" | _query_compare |
| cross 指标分母=0 | NULLIF(denominator, 0) → NULL → 显示"N/A" | RPC |
| alert + 无 daily_stats 数据 | 返回空预警 + 提示"统计数据不足" | _query_alert |
| daily_stats 缺某天数据（同步失败） | trend 补零，cross 跳过该天 | Python 层 |
| 商品无 platform 映射 | cross 按 platform 分组时归入"未知" | RPC JOIN |
| query_type=auto + 无法推断 | fallback 为 summary | _resolve_query_type |
| PII 字段在 PG 直查路径 | mask_pii() 逐行脱敏 | _query_detail |
| 归档数据（>90天） | 分别查主表+归档表，Python 合并 | _query_detail |
| 超限导出（>30000行） | 分片建议 | preflight |

## 11. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| **数据库** | | |
| daily_stats 加 platform + shop_name | 迁移文件（新增） | ALTER TABLE + 索引 + 唯一约束重建 |
| daily_stats 聚合逻辑 | erp_stats_query.py / 定时任务 | GROUP BY 加 platform + shop_name |
| 历史数据回填 | erp_daily_stats_backfill.py（新增） | 一次性脚本 |
| 新 RPC：erp_trend_query | 迁移文件（新增） | daily_stats 时间分桶 + 多指标聚合 |
| 新 RPC：erp_cross_metric_query | 迁移文件（新增） | 20 个指标公式 + platform/shop 分组 |
| 新 RPC：erp_distribution_query | 迁移文件（新增） | CASE WHEN 分桶 |
| 扩展 RPC：erp_global_stats_query | 迁移文件（修改） | AVG/MIN/MAX + 多维 GROUP BY + 复购率 |
| **引擎层** | | |
| execute() 路由逻辑 | erp_unified_query.py | 新增 query_type 参数 + 路由分发 |
| 9 种查询类型实现 | erp_analytics.py（新增） | _query_trend/compare/ratio/cross/alert/distribution |
| PlanBuilder 参数提取 | plan_builder.py | query_type / time_granularity / compare_range / metrics / alert_type |
| DepartmentAgent 透传 | trade_agent.py 等 | 透传新参数到 execute() |
| OutputStatus.NEED_CODE | tool_output.py | 新增枚举值 |
| **COPY 流式导出** | | |
| PG COPY 流式导出 | erp_copy_export.py（新增） | COPY TO STDOUT + pyarrow 分批写 |
| PII 脱敏 Python 层 | erp_copy_export.py | _mask_pii_value()，复用现有映射 |
| 字段翻译 Python 层 | erp_copy_export.py | _translate_row()，复用现有映射 |
| preflight 改造 | erp_query_preflight.py | 删除超限拒绝，>30k 走 COPY |
| **旧表 PG 直查** | | |
| PII 脱敏 Python 层 | erp_unified_schema.py | mask_pii() 已有，确认覆盖 |
| 旧表字段翻译 Python 层 | erp_field_translator.py（新增） | 复用 PLATFORM_CN 等映射 |
| 归档表 ORM 查询 | erp_orm_query.py | 新增 _query_with_archive() |
| staging 写入规则 | erp_orm_query.py | 有行写 staging，纯聚合不写 |
| **测试** | | |
| 各类型单元测试 | 7 个新增测试文件 | 每个查询类型对应测试 |
| daily_stats 维度测试 | test_daily_stats_dimensions.py | 回填验证 + 维度聚合 |

## 12. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | query_type 路由在 execute() 内，每种类型独立方法 | 低 | 方法独立，互不影响 |
| 数据库迁移 | 1 表变更 + 4 RPC 新增/扩展 | 中 | ALTER TABLE 不锁表；RPC 纯新增，可单独 DROP |
| daily_stats 依赖 | trend/cross/alert 都依赖 daily_stats 数据质量 | 中 | 已有定时聚合 + 监控；缺数据时补零/跳过 |
| PlanBuilder 复杂度 | 新增 5 个参数提取 | 中 | 关键词硬匹配 + LLM few-shot，分层降级 |
| 性能 | 所有分析查询走 PG 聚合，结果行数少 | 低 | RPC 走索引，daily_stats 有 (stat_date, outer_id) 索引 |
| daily_stats 回填 | 历史数据需补 platform+shop_name | 中 | 一次性脚本，可重跑，不影响现有数据 |
| 可回滚性 | RPC 新增可 DROP，代码 git revert | 低 | daily_stats 新列可保留不回滚 |
| 兼容性 | query_type=auto 兼容现有调用 | 低 | 老参数完全兼容 |

## 13. 方案对比

| 维度 | 方案A：引擎内路由（推荐） | 方案B：每种分析独立工具 |
|------|------------------------|----------------------|
| 实现思路 | execute() 内 query_type 分发到各 _query_* | erp_trend_tool / erp_compare_tool / ... 独立工具 |
| 对 LLM 的影响 | PlanBuilder 多提取几个参数，调用方式不变 | LLM 需要从 13+ 个工具中选择正确的 |
| 对现有代码侵入性 | 中（改 execute + 新增方法） | 高（新增工具 + 注册 + 提示词 + Agent 逻辑） |
| 可维护性 | 路由逻辑集中，共享 filter/time/auth | 工具分散，共享逻辑需要提取 |
| LLM 准确率影响 | 低（参数提取 vs 工具选择，前者更简单） | 高（工具越多，选错概率越大） |
| 扩展性 | 新增 query_type + _query_* 方法即可 | 新增工具 + 注册 + 测试 |

**推荐方案 A**：所有查询能力收敛在 UnifiedQueryEngine 内。理由：
1. LLM 不需要在更多工具中选择（已经有 13 个工具，不宜再增加）
2. 共享 filter 校验、时间处理、多租户隔离等基础设施
3. 对外接口（execute）签名兼容

## 14. 改动文件清单

### 数据库迁移（新增）
| 文件 | 预估行数 | 职责 |
|------|---------|------|
| xxx_daily_stats_add_dimensions.sql | ~30 | daily_stats 加 platform + shop_name 列 + 索引 |
| xxx_erp_trend_query.sql | ~80 | 趋势分析 RPC（DATE_TRUNC + daily_stats） |
| xxx_erp_cross_metric_query.sql | ~150 | 跨域指标 RPC（20 个指标公式 + 分组） |
| xxx_erp_distribution_query.sql | ~60 | 分布分析 RPC（CASE WHEN 分桶） |
| xxx_extend_global_stats.sql | ~50 | 扩展现有 RPC（AVG/MIN/MAX + 多维 GROUP BY + 复购率子查询） |

### 修改文件
| 文件 | 行数 | 改动 |
|------|------|------|
| erp_unified_query.py | ~527→~450 | execute() 路由 + query_type 分发（分析方法提取到 erp_analytics.py） |
| erp_unified_schema.py | ~798 | 新增 METRIC_FORMULAS / ALERT_THRESHOLDS / query_type 枚举 |
| erp_orm_query.py | ~246 | 扩展支持旧表 ORM + PII 脱敏 + 字段翻译 |
| plan_builder.py | ~??? | 新增 query_type/time_granularity/compare_range/metrics/alert_type 提取 |
| erp_query_preflight.py | ~49 | 超限时返回分片建议 |
| tool_output.py | ~143 | 新增 OutputStatus.NEED_CODE |
| erp_agent.py | ~514 | 透传新参数 |
| department agents | 各~100 | 透传新参数 |

### 新增文件
| 文件 | 预估行数 | 职责 |
|------|---------|------|
| erp_field_translator.py | ~120 | 旧表字段翻译（Python 层） |
| erp_analytics.py | ~450 | 9 种查询类型实现（_query_trend/_query_compare/_query_ratio/_query_cross/_query_alert/_query_distribution） |
| erp_copy_export.py | ~200 | PG COPY 流式导出（>30k 行，替代超限拒绝） |
| erp_sql_fallback.py | ~200 | SQL 兜底（LLM 生成 SQL + 安全校验 + 只读执行） |
| erp_sql_schema_context.py | ~150 | SQL 生成上下文（表结构 DDL + 枚举值，启动时编译） |
| erp_daily_stats_backfill.py | ~80 | 历史 daily_stats 回填 platform + shop_name 一次性脚本 |

### 测试文件
| 文件 | 改动 |
|------|------|
| test_unified_query.py | 适配新路由 + 各查询类型单元测试 |
| test_trend_query.py（新增） | 趋势分析测试（按天/周/月 + 补零 + 边界） |
| test_compare_query.py（新增） | 对比分析测试（环比/同比 + 增长率计算） |
| test_cross_metric.py（新增） | 跨域指标测试（退货率/周转/毛利 + 分母为零） |
| test_alert_query.py（新增） | 预警查询测试（缺货/滞销/超卖/采购超期 + 阈值边界） |
| test_distribution_query.py（新增） | 分布分析测试（金额分桶 + 自定义区间） |
| test_orm_query.py | 旧表 ORM 路径 + PII 脱敏 |
| test_rpc_trend.py（新增） | RPC erp_trend_query 集成测试 |
| test_rpc_cross_metric.py（新增） | RPC erp_cross_metric_query 集成测试 |
| test_daily_stats_dimensions.py（新增） | daily_stats platform/shop_name 维度测试 |
| test_copy_export.py（新增） | COPY 流式导出测试（PII脱敏/字段翻译/归档表/大数据量） |
| test_sql_fallback.py（新增） | SQL 兜底测试（安全校验 + 生成质量 + 端到端） |

## 15. 任务拆分

### Phase 0：daily_stats 维度扩展（前置依赖）
1. 迁移文件：daily_stats 加 platform + shop_name 列 + 索引
2. 修改 `erp_aggregate_daily_stats()` GROUP BY 加 platform + shop_name
3. 历史数据回填脚本（erp_daily_stats_backfill.py）
4. 部署迁移 + 执行回填 + 验证

### Phase 1：PG 直查通道（核心基础）
5. `_query_detail()` 实现——旧表 ORM 直查 + inline 返回
6. execute() 路由改造——query_type 分发 + auto 推断
7. 旧表 PII 脱敏 Python 层（mask_pii 复用）
8. 旧表字段翻译 Python 层（erp_field_translator.py）
9. staging 写入（有行写，纯聚合不写）

### Phase 2：聚合统计扩展
10. RPC 扩展——AVG/MIN/MAX + 多维 GROUP BY + COUNT DISTINCT buyer_nick（数据库迁移）
11. _query_summary 适配新 RPC 返回结构
12. metrics 参数支持（只返回用户关注的指标）

### Phase 3：趋势分析
13. 新 RPC `erp_trend_query`（数据库迁移），支持 platform/shop_name 分组
14. `_query_trend()` 实现 + 补零逻辑
15. PlanBuilder 扩展——time_granularity 提取

### Phase 4：对比分析
16. `_query_compare()` 实现——并行两次 RPC + Python 计算
17. PlanBuilder 扩展——compare_range 提取
18. 增长率计算函数（format_compare_summary）

### Phase 5：占比/排名分析
19. `_query_ratio()` 实现——占比 + 累计 + ABC 分类
20. PlanBuilder 扩展——ratio 关键词识别

### Phase 6：跨域指标（20 个指标）
21. 新 RPC `erp_cross_metric_query`（数据库迁移）——含 daily_stats 指标公式
22. `_query_cross()` 通用指标查询——退货率/毛利率/客单价/采购达成率等
23. `_query_inventory_turnover()` 特殊处理——stock + daily_stats
24. `_query_sell_through_rate()` 特殊处理——daily_stats + product
25. `_query_inventory_flow()` 进销存视图——daily_stats + stock
26. `_query_supplier_evaluation()` 供应商评估——RPC group_by=supplier
27. 复购率 RPC（buyer_nick 子查询）
28. 发货时效 RPC（consign_time - pay_time）
29. PlanBuilder 扩展——metric 关键词识别

### Phase 7：预警查询（5 种预警）
30. `_query_alert()` 实现——规则引擎
31. low_stock / slow_moving / overstock / out_of_stock 四种库存规则
32. purchase_overdue 采购超期规则
33. PlanBuilder 扩展——alert_type 关键词识别

### Phase 8：分布分析
34. 新 RPC `erp_distribution_query`（数据库迁移）
35. `_query_distribution()` 实现——分桶 + 预定义区间
36. PlanBuilder 扩展——distribution 关键词识别

### Phase 9：COPY 流式大导出
37. `erp_copy_export.py` 实现——COPY TO STDOUT + pyarrow 流式写 parquet
38. `_mask_pii_value()` + `_translate_row()` Python 层脱敏翻译（复用现有映射表）
39. execute() 路由：limit > 30000 → copy_streaming_export()
40. preflight 改造：删除超限拒绝，>30k 走 COPY 流式

### Phase 10：SQL 兜底（ERP Agent 内部闭环）
41. `erp_sql_schema_context.py` — 从现有 schema 常量编译紧凑 DDL 上下文（~2000 token）
42. `erp_sql_fallback.py` — SQL 生成 prompt + 五重安全校验 + 只读执行
43. erp_agent.py — _execute() 末尾加 _should_try_sql + _sql_fallback 分支

### Phase 11：Agent 链路打通（5 个断裂点）
44. erp_tool_description.py — 新增 9 种查询类型 + 20 个指标 + 5 种预警 + SQL 兜底能力描述
45. plan_builder.py — _PARAM_DEFINITIONS 新增 5 个参数定义
46. plan_builder.py — 新增 10 个分析类 few-shot 示例
47. plan_builder.py — _sanitize_params 新增 5 个参数白名单
48. plan_fill.py — fill_query_type() 关键词兜底
49. department_agent.py — _query_kwargs + _query_local_data 透传 5 个新参数
50. WarehouseAgent._dispatch 新增分析类查询路由

### Phase 12：测试 + 验证
51. 各查询类型单元测试（9 种）
52. RPC 集成测试（5 个新 RPC）
53. COPY 流式导出测试（3 万 / 10 万 / 50 万行）
54. SQL 兜底测试（安全校验 + 生成质量 + 端到端）
55. daily_stats 维度回填验证
56. **参数流转测试**（验证 5 个新参数从 LLM → 引擎每层不丢失）
57. E2E 全链路测试（12 种场景各一个）
58. 回归测试（现有 summary/export 不受影响）

## 16. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| daily_stats 数据缺失（同步失败） | 高 | trend 补零 + cross 跳过 + alert 提示"数据不足" + 监控同步状态 |
| daily_stats 回填失败 | 中 | 脚本可重跑，幂等设计（ON CONFLICT UPDATE） |
| daily_stats 行数膨胀（加 platform+shop） | 低 | ~2-3x 增长，PG 完全承受；有索引覆盖 |
| RPC 迁移出错 | 中 | 新增 RPC 不改现有，可单独 DROP 回滚 |
| PlanBuilder 参数提取不准 | 中 | 关键词硬匹配兜底 + query_type=auto 降级到现有逻辑 |
| erp_analytics.py 超 500 行 | 中 | 9 种查询类型各一个方法，可按类型进一步拆分 |
| 旧表 ORM 查询性能 | 低 | 仅 ≤200 行走 ORM，有索引 |
| 复购率 RPC 性能（buyer_nick 子查询） | 中 | erp_document_items 有 buyer_nick 索引，时间范围限定后数据量可控 |
| 发货时效 NULL 值 | 低 | 过滤 pay_time IS NOT NULL AND consign_time IS NOT NULL |
| alert 规则阈值不合理 | 低 | 可配置常量（ALERT_THRESHOLDS），生产验证后调整 |
| compare 跨年数据缺失 | 低 | prev 期间无数据时标注"无历史数据" |

## 17. 部署与回滚

- **数据库迁移**：5 个迁移文件（1 表结构变更 + 4 RPC）
  - daily_stats 加 platform + shop_name 列（ALTER TABLE，不锁表）
  - 4 个 RPC 函数新增/扩展
- **数据回填**：历史 daily_stats 需要回填 platform + shop_name（一次性脚本，预计 10-30 分钟）
- **新增依赖**：无（psycopg3 + pyarrow 已有）
- **API 兼容**：execute() 签名新增参数均有默认值，向后兼容
- **导出行为变更**：>30k 从"拒绝+分片建议"改为"直接导出"，用户体验提升
- **回滚步骤**：
  1. 代码 git revert
  2. DROP FUNCTION erp_trend_query / erp_cross_metric_query / erp_distribution_query
  3. 恢复 erp_global_stats_query 旧版本（如果改了的话）
  4. daily_stats 的 platform + shop_name 列可保留（不影响现有逻辑）

## 18. 能力覆盖对照表

| 电商常见分析需求 | v1.0（原方案） | v2.2（本方案） |
|----------------|--------------|--------------|
| **基础查询** | | |
| "4月金额最高5笔订单" | DuckDB（慢） | PG 直查（快） ✅ |
| "导出4月全部订单"（≤3万） | DuckDB | DuckDB（不变） ✅ |
| "导出全年50万行订单" | ❌ 超限拒绝 | PG COPY 流式（无上限） ✅ |
| **聚合统计** | | |
| "各平台订单数和总金额" | RPC（单维） | RPC（多维 + AVG/MIN/MAX） ✅ |
| "平台+店铺组合统计" | ❌ 不支持 | RPC 多维 GROUP BY ✅ |
| "不同客户数" | ❌ 不支持 | COUNT DISTINCT buyer_nick ✅ |
| **趋势分析** | | |
| "每天的销售额" | ❌ 不支持 | trend（daily_stats） ✅ |
| "按月看订单量趋势" | ❌ 不支持 | trend（DATE_TRUNC month） ✅ |
| "每个平台的退货率趋势" | ❌ 不支持 | cross + trend（daily_stats + platform 列） ✅ |
| **对比分析** | | |
| "这个月比上个月怎么样" | ❌ 不支持 | compare（RPC×2 + Python） ✅ |
| "同比增长多少" | ❌ 不支持 | compare（yoy） ✅ |
| **占比/排名** | | |
| "各平台销售额占比" | 部分（需手算） | ratio（RPC + Python） ✅ |
| "商品ABC分类" | ❌ 不支持 | ratio（累计占比） ✅ |
| **跨域指标（20个）** | | |
| "各平台的退货率" | NEED_CODE（慢） | cross（daily_stats RPC） ✅ |
| "毛利率" | ❌ 不支持 | cross（daily_stats） ✅ |
| "客单价" | ❌ 不支持 | cross（daily_stats） ✅ |
| "库存周转天数" | ❌ 不支持 | cross（stock + daily_stats） ✅ |
| "动销率" | ❌ 不支持 | cross（daily_stats + product） ✅ |
| "复购率" | ❌ 不支持 | cross（buyer_nick 子查询 RPC） ✅ |
| "发货时效/当日发货率" | ❌ 不支持 | cross（consign_time - pay_time RPC） ✅ |
| "商品进销存" | ❌ 不支持 | cross（daily_stats + stock 复合视图） ✅ |
| "供应商评估（退货率/到货率）" | ❌ 不支持 | cross（RPC group_by=supplier） ✅ |
| "采购达成率/上架率" | ❌ 不支持 | cross（daily_stats） ✅ |
| **预警查询（5种）** | | |
| "哪些SKU快卖断了" | ❌ 不支持 | alert low_stock ✅ |
| "滞销商品" | ❌ 不支持 | alert slow_moving ✅ |
| "超卖/积压风险" | ❌ 不支持 | alert overstock ✅ |
| "热销断货" | ❌ 不支持 | alert out_of_stock ✅ |
| "采购超期未到货" | ❌ 不支持 | alert purchase_overdue ✅ |
| **分布分析** | | |
| "订单金额分布" | ❌ 不支持 | distribution（CASE WHEN 分桶） ✅ |
| "客单价区间" | ❌ 不支持 | distribution ✅ |

### 有意不做（成本过高 / 需要 ML）

| 需求 | 不做原因 |
|------|---------|
| RFM 客户分层 | 三维计算+分箱，需要完整 buyer 行为数据，ROI 不高 |
| 移动平均（7日/30日MA） | 需要窗口函数，PG RPC 中实现复杂，可后期用 DuckDB |
| 销量预测 | 需要时间序列 ML 模型（Prophet/ARIMA），独立项目 |

## 19. Agent 链路打通方案

### 19.1 现有链路与断裂点分析

```
用户 → 主Agent → ERPAgent.execute(task)
                    │
                ① _extract_plan(task)
                    ├─ PlanBuilder LLM → {domain, params}    ← 【断裂点1】LLM 不认识新参数
                    └─ _sanitize_params(params)               ← 简单参数自动透传，OK
                    │
                ② _execute_plan(plan)
                    └─ DepartmentAgent.execute(task, params=step.params)
                        ├─ _params_to_filters(params)         ← 语义参数→filter DSL，OK
                        ├─ _query_kwargs(params)              ← 【断裂点2】新参数被过滤掉
                        └─ _query_local_data(doc_type, **kw)
                            └─ engine.execute(...)            ← 【断裂点3】新参数传不进引擎
                    │
                ③ _build_multi_result()
                    └─ AgentResult                            ← 【断裂点4】新返回格式未处理
                    │
                ④ 返回主Agent
                    └─ 主Agent 决定下一步                      ← 【断裂点5】不知道新能力存在
```

### 19.2 五个断裂点的修复方案

#### 断裂点1：PlanBuilder 不认识新参数

**影响文件**：`plan_builder.py`

**修复**：在 `_PARAM_DEFINITIONS` 中新增参数定义 + few-shot 示例

```python
# plan_builder.py — 新增参数定义（追加到 _PARAM_DEFINITIONS）

_NEW_PARAM_DEFINITIONS = """
## 分析类参数（v2.2 新增）

- query_type: 查询类型。可选值：
  - "summary"（统计聚合，默认）
  - "trend"（趋势分析，需配合 time_granularity）
  - "compare"（对比分析，需配合 compare_range）
  - "ratio"（占比/排名/ABC分类）
  - "cross"（跨域指标：退货率/毛利率/客单价/周转天数等）
  - "alert"（预警查询，需配合 alert_type）
  - "distribution"（分布直方图）
  - "detail"（明细查询，≤200行）
  用户问趋势用 trend，问对比用 compare，问指标用 cross，问预警用 alert。
  不确定时留空，引擎自动推断。

- time_granularity: 趋势分析的时间粒度。
  "day"=按天 | "week"=按周 | "month"=按月
  仅 query_type=trend 时有效。

- compare_range: 对比分析的对比周期。
  "mom"=环比（vs上月） | "yoy"=同比（vs去年同期） | "wow"=周环比（vs上周）
  仅 query_type=compare 时有效。

- metrics: 关注的指标列表（字符串数组）。可选值：
  销售类：return_rate / refund_rate / aftersale_rate / avg_order_value / repurchase_rate
  利润类：gross_margin
  采购类：purchase_fulfillment / supplier_evaluation
  库存类：inventory_turnover / sell_through_rate / inventory_flow
  履约类：avg_ship_time / same_day_rate
  示例：用户问"退货率"→ metrics=["return_rate"]

- alert_type: 预警类型。可选值：
  "low_stock"=缺货预警 | "slow_moving"=滞销预警 | "overstock"=积压预警
  | "out_of_stock"=热销断货 | "purchase_overdue"=采购超期
  仅 query_type=alert 时有效。
"""
```

**新增 few-shot 示例**（追加到 `build_multi_extract_prompt`）：

```python
_NEW_FEW_SHOT_EXAMPLES = [
    # 趋势分析
    {
        "query": "每天的销售额趋势",
        "output": {"steps": [{"domain": "warehouse", "params": {
            "doc_type": "daily_stats", "query_type": "trend",
            "time_granularity": "day", "metrics": ["order_amount"]
        }}]}
    },
    # 环比对比
    {
        "query": "这个月比上个月各平台销售额怎么样",
        "output": {"steps": [{"domain": "trade", "params": {
            "doc_type": "order", "query_type": "compare",
            "compare_range": "mom", "group_by": ["platform"],
            "metrics": ["amount"]
        }}]}
    },
    # 跨域指标
    {
        "query": "各平台退货率",
        "output": {"steps": [{"domain": "warehouse", "params": {
            "doc_type": "daily_stats", "query_type": "cross",
            "metrics": ["return_rate"], "group_by": ["platform"]
        }}]}
    },
    # 预警
    {
        "query": "哪些商品快卖断了",
        "output": {"steps": [{"domain": "warehouse", "params": {
            "query_type": "alert", "alert_type": "low_stock"
        }}]}
    },
    # 占比/ABC
    {
        "query": "商品ABC分类",
        "output": {"steps": [{"domain": "warehouse", "params": {
            "doc_type": "daily_stats", "query_type": "ratio",
            "group_by": ["outer_id"], "metrics": ["order_amount"]
        }}]}
    },
    # 分布
    {
        "query": "订单金额分布",
        "output": {"steps": [{"domain": "trade", "params": {
            "doc_type": "order", "query_type": "distribution",
            "metrics": ["amount"]
        }}]}
    },
    # 库存周转
    {
        "query": "库存周转天数最短的10个商品",
        "output": {"steps": [{"domain": "warehouse", "params": {
            "query_type": "cross", "metrics": ["inventory_turnover"],
            "sort_by": "turnover_days", "sort_dir": "asc", "limit": 10
        }}]}
    },
    # 进销存
    {
        "query": "这个月的进销存情况",
        "output": {"steps": [{"domain": "warehouse", "params": {
            "query_type": "cross", "metrics": ["inventory_flow"]
        }}]}
    },
    # 复购率
    {
        "query": "4月复购率多少",
        "output": {"steps": [{"domain": "trade", "params": {
            "doc_type": "order", "query_type": "cross",
            "metrics": ["repurchase_rate"]
        }}]}
    },
    # 发货时效
    {
        "query": "平均发货时长",
        "output": {"steps": [{"domain": "trade", "params": {
            "doc_type": "order", "query_type": "cross",
            "metrics": ["avg_ship_time"]
        }}]}
    },
]
```

#### 断裂点2：_query_kwargs 过滤掉新参数

**影响文件**：`department_agent.py:438`

**修复**：在 `_query_kwargs()` 中新增透传字段

```python
@staticmethod
def _query_kwargs(params: dict) -> dict[str, Any]:
    kw = {
        "mode": params.get("mode", "summary"),
        "filters": params.get("filters", []),
    }
    # 现有参数
    for key in ("group_by", "include_invalid", "extra_fields",
                "sort_by", "sort_dir", "limit"):
        val = params.get(key)
        if val is not None:
            kw[key] = val

    # ── v2.2 新增：分析类参数透传 ──
    for key in ("query_type", "time_granularity", "compare_range",
                "metrics", "alert_type"):
        val = params.get(key)
        if val is not None:
            kw[key] = val

    if "extra_fields" not in kw and params.get("fields"):
        kw["extra_fields"] = params["fields"]
    return kw
```

#### 断裂点3：_query_local_data 未传新参数到引擎

**影响文件**：`department_agent.py:380`

**修复**：在 `_query_local_data()` 的 `engine.execute()` 调用中透传新参数

```python
async def _query_local_data(self, doc_type: str, **kwargs: Any) -> ToolOutput:
    engine = UnifiedQueryEngine(db=self.db, org_id=self.org_id)
    result = await engine.execute(
        doc_type=doc_type,
        mode=kwargs.get("mode", "summary"),
        filters=kwargs.get("filters", []),
        group_by=kwargs.get("group_by"),
        sort_by=kwargs.get("sort_by"),
        sort_dir=kwargs.get("sort_dir", "desc"),
        extra_fields=kwargs.get("extra_fields"),
        limit=kwargs.get("limit", 20),
        time_type=kwargs.get("time_type"),
        include_invalid=kwargs.get("include_invalid", False),
        # ── v2.2 新增：分析类参数 ──
        query_type=kwargs.get("query_type", "auto"),
        time_granularity=kwargs.get("time_granularity"),
        compare_range=kwargs.get("compare_range"),
        metrics=kwargs.get("metrics"),
        alert_type=kwargs.get("alert_type"),
        # ── 保留 ──
        request_ctx=self.request_ctx,
        user_id=self._user_id,
        conversation_id=self._conversation_id,
        push_thinking=getattr(self, "_push_thinking", None),
    )
    return result
```

#### 断裂点4：_build_multi_result 未处理新返回格式

**影响**：不同 query_type 返回不同格式的 data，聚合时需要区分。

**修复原则**：**不需要大改**。因为：
- 所有查询类型都返回统一的 `ToolOutput(summary, data, columns, file_ref, metadata)`
- `metadata.query_type` 标识了查询类型，主 Agent 可以读取
- 趋势/对比/占比/预警的结果都是 `data: list[dict]`，格式一致
- `_build_multi_result` 的单步透传和多步 merge 逻辑不需要改

**唯一需要改的**：`metadata` 中注入 `query_type`，让主 Agent 知道结果类型

```python
# erp_agent.py — _build_multi_result 中
# 已有的 metadata 透传逻辑不变，只确保 query_type 被包含在 metadata 中
# 这已经由 engine.execute() 的各 _query_* 方法自动做了（在 metadata 中写入 query_type）
```

#### 断裂点5：主 Agent 不知道新能力

**影响文件**：`erp_tool_description.py`

**修复**：更新能力描述，让主 Agent 知道 ERP Agent 能做趋势/对比/预警等

```python
# erp_tool_description.py — get_capability_manifest() 扩展

def get_capability_manifest() -> dict:
    return {
        # ... 现有字段 ...

        # ── v2.2 新增 ──
        "query_types": {
            "summary": "统计聚合（COUNT/SUM/AVG + 分组）",
            "trend": "趋势分析（按天/周/月的指标走势）",
            "compare": "对比分析（环比/同比增长率）",
            "ratio": "占比分析（百分比/帕累托/ABC分类）",
            "cross": "跨域指标（退货率/毛利率/客单价/周转/进销存/发货时效/复购率/供应商评估）",
            "alert": "预警查询（缺货/滞销/积压/断货/采购超期）",
            "distribution": "分布直方图（金额/数量区间分布）",
            "detail": "明细查询（返回具体行数据）",
            "export": "大批量导出（Parquet文件，无行数上限）",
        },
        "cross_metrics": [
            "return_rate（退货率）", "refund_rate（退款率）",
            "gross_margin（毛利率）", "avg_order_value（客单价）",
            "repurchase_rate（复购率）", "inventory_turnover（库存周转天数）",
            "sell_through_rate（动销率）", "inventory_flow（进销存）",
            "avg_ship_time（发货时效）", "supplier_evaluation（供应商评估）",
        ],
        "alert_types": [
            "low_stock（缺货预警）", "slow_moving（滞销预警）",
            "overstock（积压预警）", "out_of_stock（热销断货）",
            "purchase_overdue（采购超期）",
        ],
    }
```

**工具描述文本扩展**（`build_tool_description()`）：

```python
# 在 "能力" 段落追加：

分析能力（v2.2 新增）：
- 趋势分析：每天/每周/每月的销售额、订单量、退货量等走势
- 对比分析：环比（vs上月）、同比（vs去年）增长率
- 占比分析：各平台/商品/店铺的销售额占比、ABC商品分类
- 跨域指标：退货率、毛利率、客单价、库存周转天数、进销存、
  发货时效、复购率、供应商评估（共20个指标）
- 预警查询：缺货预警、滞销预警、积压预警、采购超期
- 分布分析：订单金额区间分布、客单价分布
- 大数据导出：50万行以上数据直接导出，无行数限制
```

### 19.3 返回格式规范

所有查询类型共用统一的 `AgentResult` 结构，通过 `metadata.query_type` 区分：

```python
# 统一返回结构——主 Agent 按 query_type 决定如何使用结果

AgentResult(
    summary="...",                        # 人类可读摘要（必有）
    status="success",                     # success | error | empty
    format=OutputFormat.TABLE,            # TABLE（内联）| FILE_REF（大文件）
    data=[{...}, ...],                    # 结构化数据（≤200行内联，主Agent可读）
    columns=[ColumnMeta(...)],            # 列元信息
    file_ref=FileRef(...) or None,        # staging 文件（有数据行时写入）
    metadata={
        "query_type": "trend",            # ★ 标识查询类型
        "doc_type": "daily_stats",
        "time_range": "04-01 ~ 04-27",
        "row_count": 27,
        # 各类型专属元数据：
        "granularity": "day",             # trend 专属
        "compare_range": "mom",           # compare 专属
        "current_period": "...",          # compare 专属
        "prev_period": "...",             # compare 专属
        "metric": "return_rate",          # cross 专属
        "alert_type": "low_stock",        # alert 专属
        "total_alerts": 5,                # alert 专属
        "total": 150000,                  # ratio 专属（总量）
        "buckets": [0,50,100,...],         # distribution 专属
    },
)
```

**各查询类型的 data 格式规范**：

| query_type | data 结构 | 示例 |
|-----------|----------|------|
| detail | `[{order_no, amount, ...}]` | 明细行 |
| summary | `[{group_key, doc_count, total_amount, avg_amount}]` | 分组聚合 |
| trend | `[{period, order_count, order_amount}]` | 时间序列，period=日期 |
| compare | `[{group_key, current_*, prev_*, *_change, *_growth}]` | 当前 vs 上期 |
| ratio | `[{group_key, total_amount, ratio, cumulative_ratio, abc_class}]` | 占比+累计+ABC |
| cross | `[{group_key, metric_value, numerator, denominator}]` | 指标值+分子分母 |
| alert | `[{outer_id, item_name, severity, days_left, suggestion}]` | 预警条目+建议 |
| distribution | `[{bucket, count, bucket_total}]` | 区间+计数 |
| export | 无 data（文件在 file_ref） | FileRef |

### 19.4 跨域查询的域路由规则

新增查询类型需要正确路由到对应的 department：

| query_type | 路由到哪个 domain | 原因 |
|-----------|------------------|------|
| trend | **warehouse**（doc_type=daily_stats） | daily_stats 在 warehouse 域 |
| compare | **按 doc_type 路由**（order→trade, purchase→purchase） | 复用现有 RPC |
| ratio | **按 doc_type 路由** | 复用现有 RPC 聚合结果 |
| cross（大部分） | **warehouse**（doc_type=daily_stats） | daily_stats 预聚合数据 |
| cross（复购率） | **trade**（doc_type=order） | 需要 buyer_nick |
| cross（发货时效） | **trade**（doc_type=order） | 需要 pay_time/consign_time |
| cross（供应商评估） | **purchase** | 需要 supplier 分组 |
| cross（进销存） | **warehouse** | daily_stats + stock |
| alert | **warehouse** | stock + daily_stats |
| distribution | **按 doc_type 路由** | 原始表分桶 |

**PlanBuilder 域路由规则更新**：

```python
# plan_builder.py — _DOMAIN_DOC_TYPES 扩展
_DOMAIN_DOC_TYPES = {
    "trade": {"order", "order_log"},
    "purchase": {"purchase", "purchase_return"},
    "aftersale": {"aftersale", "aftersale_log"},
    "warehouse": {"receipt", "shelf", "stock", "product", "sku",
                  "daily_stats", "batch_stock", "platform_map"},
}

# 新增：query_type 到 domain 的推荐映射
_QUERY_TYPE_DOMAIN_HINT = {
    "trend": "warehouse",          # daily_stats
    "alert": "warehouse",          # stock + daily_stats
    "cross:inventory_turnover": "warehouse",
    "cross:inventory_flow": "warehouse",
    "cross:sell_through_rate": "warehouse",
    "cross:repurchase_rate": "trade",
    "cross:avg_ship_time": "trade",
    "cross:supplier_evaluation": "purchase",
    # 其他 cross 指标默认 warehouse（daily_stats）
}
```

### 19.5 _sanitize_params 处理新参数

```python
# plan_builder.py — _sanitize_params 新增处理

def _sanitize_params(params: dict) -> dict:
    # ... 现有逻辑 ...

    # ── v2.2：分析类参数校验 ──

    # query_type 白名单
    qt = params.get("query_type")
    if qt and qt not in {"auto", "detail", "summary", "trend", "compare",
                          "ratio", "cross", "alert", "distribution"}:
        params.pop("query_type", None)

    # time_granularity 白名单
    tg = params.get("time_granularity")
    if tg and tg not in {"day", "week", "month"}:
        params.pop("time_granularity", None)

    # compare_range 白名单
    cr = params.get("compare_range")
    if cr and cr not in {"mom", "yoy", "wow"}:
        params.pop("compare_range", None)

    # metrics 转 list + 白名单
    m = params.get("metrics")
    if isinstance(m, str):
        params["metrics"] = [m]
    if isinstance(m, list):
        VALID_METRICS = {
            "count", "amount", "qty", "avg_amount", "cost",
            "return_rate", "refund_rate", "aftersale_rate",
            "gross_margin", "avg_order_value", "repurchase_rate",
            "inventory_turnover", "sell_through_rate", "inventory_flow",
            "avg_ship_time", "same_day_rate",
            "purchase_fulfillment", "supplier_evaluation",
        }
        params["metrics"] = [x for x in params["metrics"] if x in VALID_METRICS]

    # alert_type 白名单
    at = params.get("alert_type")
    if at and at not in {"low_stock", "slow_moving", "overstock",
                          "out_of_stock", "purchase_overdue"}:
        params.pop("alert_type", None)

    return params
```

### 19.6 plan_fill.py 关键词兜底

当 LLM 没有提取 query_type 时，L2 关键词层兜底：

```python
# plan_fill.py — 新增 fill_query_type()

def fill_query_type(params: dict, query: str) -> None:
    """L2 兜底：从文本关键词推断 query_type（LLM 未提取时）"""
    if params.get("query_type") and params["query_type"] != "auto":
        return  # LLM 已提取，不覆盖

    # 关键词优先级：alert > cross > trend > compare > ratio > distribution
    for keyword in ("预警", "断货", "缺货", "滞销", "快没了", "采购超期"):
        if keyword in query:
            params["query_type"] = "alert"
            # 同时尝试补 alert_type
            if not params.get("alert_type"):
                _fill_alert_type(params, query)
            return

    for keyword in ("退货率", "毛利率", "客单价", "复购率", "周转", "进销存",
                     "发货时效", "动销率", "供应商评估", "达成率"):
        if keyword in query:
            params["query_type"] = "cross"
            if not params.get("metrics"):
                _fill_metric(params, query)
            return

    for keyword in ("趋势", "每天", "每周", "每月", "走势", "变化", "曲线"):
        if keyword in query:
            params["query_type"] = "trend"
            if not params.get("time_granularity"):
                _fill_time_granularity(params, query)
            return

    for keyword in ("环比", "同比", "比上个月", "比去年", "增长率"):
        if keyword in query:
            params["query_type"] = "compare"
            if not params.get("compare_range"):
                _fill_compare_range(params, query)
            return

    for keyword in ("占比", "比例", "ABC", "帕累托", "贡献度"):
        if keyword in query:
            params["query_type"] = "ratio"
            return

    for keyword in ("分布", "区间", "直方图"):
        if keyword in query:
            params["query_type"] = "distribution"
            return
```

### 19.7 连锁修改完整清单

| # | 文件 | 改动内容 | 断裂点 |
|---|------|---------|--------|
| 1 | `erp_tool_description.py` | 新增 query_types / cross_metrics / alert_types 能力描述 | 断裂点5 |
| 2 | `plan_builder.py` _PARAM_DEFINITIONS | 新增 query_type / time_granularity / compare_range / metrics / alert_type 参数定义 | 断裂点1 |
| 3 | `plan_builder.py` few-shot | 新增 10 个分析查询示例 | 断裂点1 |
| 4 | `plan_builder.py` _sanitize_params | 新增 5 个参数的白名单校验 | 断裂点1 |
| 5 | `plan_fill.py` | 新增 fill_query_type() 关键词兜底 | 断裂点1 |
| 6 | `department_agent.py` _query_kwargs | 新增 5 个参数透传 | 断裂点2 |
| 7 | `department_agent.py` _query_local_data | engine.execute() 透传 5 个新参数 | 断裂点3 |
| 8 | `erp_unified_query.py` execute() | 接收 5 个新参数 + query_type 路由 | 引擎层 |
| 9 | `erp_analytics.py`（新增） | 9 种查询类型的具体实现 | 引擎层 |
| 10 | `erp_agent.py` _build_multi_result | metadata 中透传 query_type（已自动包含） | 断裂点4 |

### 19.8 参数流转验证矩阵

确保每个新参数从 LLM 提取到引擎执行，每一层都不丢失：

| 参数 | LLM提取 | _sanitize | PlanStep | _query_kwargs | _query_local_data | engine.execute |
|------|---------|-----------|----------|---------------|-------------------|----------------|
| query_type | ✅ prompt定义 | ✅ 白名单 | ✅ 透传 | ✅ **需新增** | ✅ **需新增** | ✅ **需新增** |
| time_granularity | ✅ prompt定义 | ✅ 白名单 | ✅ 透传 | ✅ **需新增** | ✅ **需新增** | ✅ **需新增** |
| compare_range | ✅ prompt定义 | ✅ 白名单 | ✅ 透传 | ✅ **需新增** | ✅ **需新增** | ✅ **需新增** |
| metrics | ✅ prompt定义 | ✅ list化+白名单 | ✅ 透传 | ✅ **需新增** | ✅ **需新增** | ✅ **需新增** |
| alert_type | ✅ prompt定义 | ✅ 白名单 | ✅ 透传 | ✅ **需新增** | ✅ **需新增** | ✅ **需新增** |

**需新增** = 当前代码没有，必须在本次改动中加上。

### 19.9 WarehouseAgent 域扩展

大部分新查询类型路由到 warehouse 域（daily_stats / stock），需确认 WarehouseAgent 的能力：

```python
# warehouse_agent.py — 现有 allowed_doc_types
allowed_doc_types = [
    "receipt", "shelf", "stock", "product", "sku",
    "daily_stats", "batch_stock", "platform_map",
]
# ✅ daily_stats 已在白名单中，不需要改

# 但 _dispatch 需要新增路由：
async def _dispatch(self, action, params, context):
    doc_type = params.get("doc_type", "receipt")
    query_type = params.get("query_type", "auto")

    # 新增：分析类查询直接走 _query_local_data（引擎内部路由）
    if query_type in ("trend", "cross", "alert", "distribution", "ratio"):
        return await self._query_local_data(
            doc_type=doc_type or "daily_stats",
            **self._query_kwargs(params),
        )

    # 现有路由不变
    return await self._query_existing(action, params, context)
```

## 20. SQL 兜底——ERP Agent 内部闭环

### 20.1 定位与触发条件

**定位**：结构化查询的最后一道防线。9 种 query_type + 2 种导出路径覆盖了 90%+ 场景，剩下的长尾查询（复杂条件组合、非标分析）由 SQL 兜底。

**触发条件**（全部满足才触发）：
```python
def _should_try_sql(self, result: AgentResult, query: str) -> bool:
    """判断是否启用 SQL 兜底"""
    # 必须条件：结构化查询确实失败了
    if result.status not in ("error", "empty"):
        return False

    # 排除条件：这些场景不该兜底
    if "超时" in result.summary or "timeout" in result.error_message:
        return False  # 超时说明数据量大，SQL 也会超时
    if "参数" in result.summary or "doc_type" in result.error_message:
        return False  # 参数错误应该让用户修正，不该猜测 SQL
    if result.metadata.get("query_type") == "alert":
        return False  # 预警查询走规则引擎，SQL 兜底没意义

    return True
```

### 20.2 千问能看到什么——Prompt 上下文设计

这是整个方案的核心。LLM 生成 SQL 的质量 = 上下文质量。

**分三层喂上下文**：

#### 第一层：表结构（静态，启动时编译一次，~2000 token）

```python
# 新文件：erp_sql_schema_context.py
# 启动时从现有 schema 常量编译为紧凑的 DDL 摘要

ERP_SCHEMA_CONTEXT = """
## 可查询的表

### erp_document_items（订单/采购/售后/收货/上架/采退 主表，近3个月）
- 按 doc_type 区分：order/purchase/aftersale/receipt/shelf/purchase_return
- 主键：id | 唯一：(org_id, doc_type, doc_id, outer_id, sku_outer_id)
- 时间列：doc_created_at（默认）, pay_time, consign_time, delivery_date, finished_at
- 重要索引：(doc_type, outer_id, doc_created_at DESC), (platform), (shop_name, doc_type), (order_no)
核心列：
  doc_type, doc_id, doc_code, doc_status, order_status, outer_id(商品编码), sku_outer_id(SKU编码),
  item_name(商品名), quantity(数量), amount(金额), cost(成本), gross_profit(毛利),
  shop_name(店铺), platform(平台编码), supplier_name(供应商), warehouse_name(仓库),
  order_no(订单号), buyer_nick(买家昵称), order_type(订单类型),
  pay_time(付款时间), consign_time(发货时间), doc_created_at(创建时间),
  is_cancel(是否取消), is_refund(是否退款), aftersale_type(售后类型), refund_status(退款状态)

### erp_document_items_archive（同上，>90天冷数据，结构完全相同）
- 查历史数据时需要 UNION ALL 两张表

### erp_product_daily_stats（按商品×日期预聚合的统计表）
- 唯一：(org_id, stat_date, outer_id, sku_outer_id)
核心列：
  stat_date(日期), outer_id, sku_outer_id, item_name,
  order_count(订单数), order_qty(销售量), order_amount(销售额), order_cost(成本),
  order_shipped_count(已发货), order_finished_count(已完成),
  order_refund_count(退款数), order_cancelled_count(取消数),
  aftersale_count(售后总数), aftersale_refund_count(仅退款), aftersale_return_count(退货),
  aftersale_exchange_count(换货), aftersale_reissue_count(补发),
  purchase_count(采购数), purchase_qty(采购量), purchase_amount(采购额),
  receipt_count(收货数), shelf_count(上架数)

### erp_stock_status（SKU级实时库存快照）
核心列：
  outer_id, sku_outer_id, item_name, sku_name,
  quantity(总库存), available_qty(可用库存), order_lock(订单锁定),
  warehouse_name, warehouse_id

### erp_products（商品主数据 SPU级）
核心列：
  outer_id, title(商品名), platform, shop_name, category_name,
  price(价格), cost(成本), weight, created_at

### erp_product_skus（SKU明细）
核心列：
  outer_id, sku_outer_id, sku_name, properties_name(规格),
  price, cost, barcode
"""
```

#### 第二层：枚举值（静态，~500 token）

```python
ENUM_CONTEXT = """
## 枚举值速查

platform 编码：tb=淘宝, jd=京东, pdd=拼多多, fxg=抖音, kuaishou=快手, xhs=小红书, ali1688=1688
doc_type 值：order, purchase, aftersale, receipt, shelf, purchase_return
order_status：WAIT_PAY(待付款), WAIT_SEND(待发货), SEND(已发货), FINISH(已完成), CLOSED(已关闭)
aftersale_type：1=退款, 2=退货, 3=补发, 4=换货, 5=发货前退款
refund_status：0=无退款, 1=退款中, 2=退款成功, 3=退款关闭
order_type：0=普通, 4=线下, 7=合并, 8=拆分, 13=换货, 14=补发（逗号分隔多值）
布尔字段（is_cancel等）：0=否, 1=是
"""
```

#### 第三层：动态上下文（每次查询不同，~300 token）

```python
def _build_dynamic_context(
    self, query: str, failed_result: AgentResult, plan: ExecutionPlan,
) -> str:
    """构建本次查询的动态上下文"""
    parts = [
        f"## 用户问题\n{query}",
        f"\n## 结构化查询尝试结果\n"
        f"- 状态：{failed_result.status}\n"
        f"- 错误信息：{failed_result.summary[:200]}",
    ]

    # 已尝试的参数（让 LLM 知道哪些条件是对的，避免重复错误）
    if plan and plan.steps:
        step = plan.steps[0]
        parts.append(f"\n## 已尝试的查询参数\n```json\n{json.dumps(step.params, ensure_ascii=False, indent=2)}\n```")

    # 安全约束
    parts.append(f"\n## 安全约束\n"
                 f"- 必须包含 WHERE org_id = '{self.org_id}'\n"
                 f"- 只允许 SELECT，禁止 INSERT/UPDATE/DELETE/DROP\n"
                 f"- 必须包含 LIMIT（最大 1000 行）\n"
                 f"- 时间范围必须在 2 年以内")

    return "\n".join(parts)
```

### 20.3 SQL 生成 Prompt

```python
SQL_GENERATION_PROMPT = """你是 ERP 数据库查询专家。用户的结构化查询失败了，请根据用户问题直接生成 PostgreSQL SELECT SQL。

{schema_context}

{enum_context}

{dynamic_context}

## 输出要求
1. 只输出一条 SELECT SQL，不要解释
2. 必须包含 WHERE org_id = '{org_id}'
3. 必须包含 LIMIT（默认 200，用户要求更多时最大 1000）
4. 如果需要查历史数据（>90天前），用 UNION ALL 联合 erp_document_items_archive
5. 列别名用中文（如 amount AS "金额"）
6. 数值聚合用 ROUND(..., 2)
7. 平台编码翻译用 CASE WHEN platform = 'tb' THEN '淘宝' ... END AS "平台"
8. 时间列用 doc_created_at，除非用户明确指定了其他时间（如付款时间→pay_time）
9. 排序默认 doc_created_at DESC，除非用户指定了其他排序

## 常见 SQL 模式参考
-- 统计聚合
SELECT platform AS "平台", COUNT(DISTINCT doc_id) AS "单据数", SUM(amount) AS "金额"
FROM erp_document_items WHERE doc_type='order' AND org_id='{org_id}' AND doc_created_at >= '...'
GROUP BY platform ORDER BY "金额" DESC;

-- 趋势查询
SELECT stat_date AS "日期", SUM(order_count) AS "订单数", SUM(order_amount) AS "销售额"
FROM erp_product_daily_stats WHERE org_id='{org_id}' AND stat_date >= '...'
GROUP BY stat_date ORDER BY stat_date;

-- 跨表比率
SELECT SUM(aftersale_count)::numeric / NULLIF(SUM(order_count), 0) * 100 AS "退货率(%)"
FROM erp_product_daily_stats WHERE org_id='{org_id}' AND stat_date >= '...';

只输出 SQL：
"""
```

### 20.4 SQL 安全校验（执行前）

生成的 SQL 执行前必须过安全检查：

```python
import re

_DANGEROUS_KEYWORDS = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC)\b',
    re.IGNORECASE,
)
_ORG_ID_CHECK = re.compile(r"org_id\s*=\s*'[0-9a-f\-]+'", re.IGNORECASE)
_LIMIT_CHECK = re.compile(r'\bLIMIT\s+\d+', re.IGNORECASE)

def validate_generated_sql(sql: str, org_id: str) -> tuple[bool, str]:
    """校验 LLM 生成的 SQL 是否安全

    Returns:
        (is_valid, error_message)
    """
    # 1. 禁止写操作
    if _DANGEROUS_KEYWORDS.search(sql):
        return False, "SQL 包含危险关键字（INSERT/UPDATE/DELETE/DROP 等）"

    # 2. 必须包含 org_id 过滤（多租户隔离）
    if not _ORG_ID_CHECK.search(sql):
        return False, "SQL 缺少 org_id 过滤条件"

    # 3. org_id 值必须匹配当前用户
    if org_id not in sql:
        return False, f"SQL 中的 org_id 与当前用户不匹配"

    # 4. 必须有 LIMIT
    if not _LIMIT_CHECK.search(sql):
        return False, "SQL 缺少 LIMIT 限制"

    # 5. LIMIT 不超过 1000
    limit_match = _LIMIT_CHECK.search(sql)
    if limit_match:
        limit_val = int(re.search(r'\d+', limit_match.group()).group())
        if limit_val > 1000:
            return False, f"LIMIT {limit_val} 超过上限 1000"

    # 6. 只允许 SELECT
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
        return False, "只允许 SELECT / WITH 查询"

    return True, ""
```

### 20.5 SQL 执行（psycopg3 只读直连）

```python
async def _execute_readonly_sql(self, sql: str) -> list[dict]:
    """只读执行 SQL，返回字典列表

    安全约束：
    - 连接级只读（SET default_transaction_read_only = on）
    - 语句级超时（statement_timeout = 30s）
    - 行数上限 1000（SQL 中已有 LIMIT，这里双重保险）
    """
    settings = get_settings()

    async with await psycopg.AsyncConnection.connect(
        settings.database_url,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    ) as conn:
        async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute(sql)
            rows = await cur.fetchmany(1000)  # 双重保险：最多 1000 行
            columns = [desc.name for desc in cur.description] if cur.description else []

    return rows, columns
```

### 20.6 完整兜底流程

```python
async def _sql_fallback(
    self, query: str, failed_result: AgentResult, plan: ExecutionPlan,
) -> AgentResult | None:
    """SQL 兜底——结构化查询失败后的最后防线

    流程：
    1. 编译上下文（schema + 枚举 + 动态上下文）
    2. 千问生成 SQL（一次 LLM 调用）
    3. 安全校验（禁写 + org_id + LIMIT）
    4. 只读执行（psycopg3 + 30s 超时）
    5. 包装返回 AgentResult
    """
    logger.info(f"SQL fallback triggered | query={query[:80]}")

    # 1. 编译 prompt
    dynamic_ctx = self._build_dynamic_context(query, failed_result, plan)
    prompt = SQL_GENERATION_PROMPT.format(
        schema_context=ERP_SCHEMA_CONTEXT,
        enum_context=ENUM_CONTEXT,
        dynamic_context=dynamic_ctx,
        org_id=self.org_id,
    )

    # 2. 千问生成 SQL
    try:
        sql = await self._call_llm_for_sql(prompt)  # 复用现有千问调用链路
    except Exception as e:
        logger.warning(f"SQL generation failed: {e}")
        return None

    if not sql or not sql.strip():
        return None

    # 清理：去掉 markdown 代码块标记
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1] if "\n" in sql else sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]
    sql = sql.strip().rstrip(";")

    # 3. 安全校验
    is_valid, error = validate_generated_sql(sql, self.org_id)
    if not is_valid:
        logger.warning(f"SQL validation failed: {error} | sql={sql[:200]}")
        return None

    # 4. 执行
    try:
        rows, columns = await self._execute_readonly_sql(sql)
    except Exception as e:
        logger.warning(f"SQL execution failed: {e} | sql={sql[:200]}")
        return None

    if not rows:
        return None  # SQL 执行成功但无数据，不覆盖原结果

    # 5. 包装返回
    row_count = len(rows)
    summary = f"通过 SQL 查询获得 {row_count} 条结果"

    # 写 staging（如果有数据行）
    file_ref = None
    if row_count > 0:
        file_ref = await self._write_sql_result_to_staging(rows, columns)

    logger.info(f"SQL fallback success | rows={row_count} sql={sql[:100]}")

    return AgentResult(
        status="success",
        summary=summary,
        format=OutputFormat.TABLE if row_count <= 200 else OutputFormat.FILE_REF,
        data=rows[:200] if row_count <= 200 else None,
        columns=[ColumnMeta(name=c, dtype="text", label=c) for c in columns],
        file_ref=file_ref,
        metadata={
            "query_type": "sql_fallback",
            "sql": sql,  # 记录实际执行的 SQL（审计 + 调试）
            "original_error": failed_result.summary[:200],
        },
    )
```

### 20.7 上下文 token 预算

| 层级 | 内容 | 预估 token |
|------|------|-----------|
| 第一层：表结构 | 5 张核心表的紧凑 DDL | ~2000 |
| 第二层：枚举值 | 7 组枚举映射 | ~500 |
| 第三层：动态上下文 | 用户问题 + 失败原因 + 已尝试参数 | ~300 |
| SQL 生成指令 | 输出要求 + 3 个 SQL 示例 | ~400 |
| **总计** | | **~3200 token** |

千问 qwen-plus 的上下文窗口 128k token，3200 token 的 prompt 完全在能力范围内。

### 20.8 为什么千问能写好 SQL

| 因素 | 说明 |
|------|------|
| **表结构完整** | 5 张核心表的列名+中文注释+类型全部提供 |
| **枚举值精确** | platform/status/aftersale_type 等编码全部给出 |
| **失败上下文** | LLM 知道结构化查询哪里失败了，避免重复错误 |
| **SQL 示例** | 给了 3 种常见模式（聚合/趋势/比率），千问可以类比 |
| **安全兜底** | 即使写错也有校验拦截（禁写 + org_id + LIMIT） |
| **场景简单** | 兜底场景是结构化路径的漏网之鱼，通常是简单条件组合，不是超复杂分析 |

### 20.9 边界与限制

| 场景 | 处理 |
|------|------|
| 千问生成的 SQL 语法错误 | psycopg 执行报错 → 返回 None → 用原始失败结果 |
| SQL 校验不通过 | 返回 None → 用原始失败结果 |
| SQL 执行超时（>30s） | statement_timeout 自动断 → 返回 None |
| SQL 返回 0 行 | 返回 None → 用原始失败结果（不覆盖 empty 诊断） |
| 千问 LLM 调用失败 | 返回 None → 降级透明 |
| SQL 注入风险 | validate_generated_sql 五重校验 + 只读连接 |
| 多租户泄露 | org_id 强制匹配校验 + PG 连接级只读 |

**原则：SQL 兜底是尽力而为，不增加任何新的失败模式。** 兜底失败 = 透明降级 = 返回原始失败结果。

### 20.10 连锁修改

| 文件 | 改动 |
|------|------|
| erp_sql_fallback.py（新增 ~200 行） | _sql_fallback + validate_generated_sql + _execute_readonly_sql |
| erp_sql_schema_context.py（新增 ~150 行） | ERP_SCHEMA_CONTEXT + ENUM_CONTEXT（启动时编译） |
| erp_agent.py | _execute() 末尾加 if 分支调用 _sql_fallback |
| test_sql_fallback.py（新增） | SQL 校验测试 + 端到端兜底测试 |

### 20.11 任务拆分

已整合到 §15 Phase 10（步骤 41-43）和 Phase 12（步骤 54）。

## 21. 并行执行计划（多对话同时开发）

### 21.1 依赖关系图

```
Phase 0 (daily_stats维度)
    │
    ├──→ Phase 3 (趋势分析) ──→ Phase 4 (对比分析)
    ├──→ Phase 6 (跨域指标)
    └──→ Phase 7 (预警查询)

Phase 1 (PG直查) ──→ Phase 2 (聚合扩展)

Phase 5 (占比/排名) ← 依赖 Phase 2 的 RPC 聚合结果
Phase 8 (分布分析) ← 独立，只依赖迁移文件

Phase 9 (COPY导出) ← 完全独立
Phase 10 (SQL兜底) ← 完全独立
Phase 11 (Agent链路) ← 依赖 Phase 1-10 的接口定义（但代码层面独立）

Phase 12 (测试) ← 依赖所有 Phase 完成
```

### 21.2 可并行的任务组（6 个对话同时开）

**前提**：Phase 0 先单独执行（数据库迁移 + 回填），约 1-2 小时。完成后开始并行。

---

#### 对话 A：引擎核心——PG 直查 + 聚合扩展 + 占比排名
```
Phase 1 → Phase 2 → Phase 5
涉及文件：
  - erp_unified_query.py（execute 路由 + _query_detail）
  - erp_field_translator.py（新增）
  - erp_orm_query.py（旧表 ORM 扩展）
  - 迁移文件：xxx_extend_global_stats.sql
  - erp_analytics.py 中的 _query_ratio()
步骤：5-12, 19-20
预估：2-3 天
```

#### 对话 B：趋势 + 对比分析
```
Phase 3 → Phase 4
涉及文件：
  - 迁移文件：xxx_erp_trend_query.sql
  - erp_analytics.py 中的 _query_trend() + _query_compare()
  - format 辅助函数（format_trend_summary / format_compare_summary）
步骤：13-18
前置依赖：Phase 0 完成（daily_stats 有 platform 列）
预估：1.5-2 天
```

#### 对话 C：跨域指标（20 个）
```
Phase 6
涉及文件：
  - 迁移文件：xxx_erp_cross_metric_query.sql
  - erp_analytics.py 中的 _query_cross / _query_inventory_turnover /
    _query_sell_through_rate / _query_inventory_flow / _query_supplier_evaluation
  - 复购率 + 发货时效的专用 RPC SQL
步骤：21-29
前置依赖：Phase 0 完成
预估：2-3 天（指标最多，最重的一组）
```

#### 对话 D：预警 + 分布分析
```
Phase 7 + Phase 8
涉及文件：
  - erp_analytics.py 中的 _query_alert() + _query_distribution()
  - 迁移文件：xxx_erp_distribution_query.sql
步骤：30-36
前置依赖：Phase 0 完成（预警需要 daily_stats）
预估：1.5-2 天
```

#### 对话 E：COPY 流式导出 + SQL 兜底
```
Phase 9 + Phase 10
涉及文件：
  - erp_copy_export.py（新增）
  - erp_sql_fallback.py（新增）
  - erp_sql_schema_context.py（新增）
  - erp_agent.py（SQL 兜底分支）
步骤：37-43
完全独立，不依赖任何其他 Phase
预估：2 天
```

#### 对话 F：Agent 链路打通
```
Phase 11
涉及文件：
  - erp_tool_description.py
  - plan_builder.py
  - plan_fill.py
  - department_agent.py
  - warehouse_agent.py（_dispatch 扩展）
步骤：44-50
代码层面独立（改的是 Agent 层，不是引擎层）
但需要知道引擎接口定义（参数名 + 返回格式）—— 已在技术文档中明确
预估：1.5 天
```

### 21.3 时间线

```
Day 0：Phase 0（daily_stats 迁移 + 回填）—— 单独执行

Day 1-3：6 个对话并行
  ┌─ 对话 A：PG 直查 + 聚合 + 占比 (Phase 1→2→5)
  ├─ 对话 B：趋势 + 对比 (Phase 3→4)
  ├─ 对话 C：跨域指标 (Phase 6)
  ├─ 对话 D：预警 + 分布 (Phase 7+8)
  ├─ 对话 E：COPY 导出 + SQL 兜底 (Phase 9+10)
  └─ 对话 F：Agent 链路打通 (Phase 11)

Day 4：Phase 12（集成测试）—— 所有对话合并后执行
```

### 21.4 每个对话的启动 prompt 模板

每个对话需要给到：
1. **技术文档链接**：`docs/document/TECH_ERP查询架构重构.md`（完整方案）
2. **具体 Phase 编号**：明确执行哪几个 Phase
3. **涉及文件列表**：明确改哪些文件
4. **接口契约**：新参数名 + 返回格式（从 §19.3 和 §19.8 复制）
5. **不要改的文件**：明确其他对话负责的文件

### 21.5 文件冲突风险

| 文件 | 谁改 | 冲突风险 |
|------|------|---------|
| erp_unified_query.py | 对话 A（路由） | 低（只改 execute 方法） |
| erp_analytics.py（新增） | 对话 A/B/C/D | **高**——4 个对话都往这里加方法 |
| plan_builder.py | 对话 F | 低（独占） |
| department_agent.py | 对话 F | 低（独占） |
| erp_agent.py | 对话 E（SQL 兜底分支） | 低（只加末尾 if） |
| 迁移文件 | 对话 A/B/C/D | 低（各自独立迁移文件） |

**erp_analytics.py 冲突解决方案**：
- 方案 1：每个对话各建自己的文件，最后合并
  - 对话 B → `erp_analytics_trend.py`
  - 对话 C → `erp_analytics_cross.py`
  - 对话 D → `erp_analytics_alert.py`
- 方案 2：erp_analytics.py 预先创建骨架（空方法签名），各对话填充实现

**推荐方案 1**——各建文件，避免 git 冲突，最后合并或保持拆分（每个文件 <500 行）。

## 22. 设计自检

- [x] 项目上下文已加载，含 daily_stats 关键发现
- [x] 9 种查询类型 + 2 种导出路径全部有详细设计和代码示例
- [x] 跨域指标 20 个，覆盖销售/利润/采购/库存/履约 5 大类
- [x] 预警 5 种类型，覆盖库存 4 种 + 采购 1 种
- [x] COPY 流式导出完整实现（erp_copy_export.py ~200 行，PII 脱敏 + 字段翻译 + 归档表）
- [x] **SQL 兜底闭环**：ERP Agent 内部生成 SQL + 五重安全校验 + 只读执行（§20 完整方案）
- [x] **SQL prompt 上下文**：三层设计（表结构 2000token + 枚举 500token + 动态 300token = ~3200 token）
- [x] 连锁修改已全部纳入任务拆分（13 Phase / 58 步）
- [x] 边界场景有处理策略（13 个场景）
- [x] 架构影响评估无高风险项
- [x] 方案对比已做，推荐方案 A（引擎内路由）
- [x] 文件行数控制：erp_unified_query.py 路由器 + erp_analytics.py 分析实现
- [x] 大导出方案：>30k 走 PG COPY 流式（psycopg3 + pyarrow，无上限，本机零费用）
- [x] 保留 DuckDB（200~30k）+ 新增 COPY 流式（>30k）渐进替换策略
- [x] 数据库迁移：5 个文件（1 表结构 + 4 RPC），可单独回滚
- [x] daily_stats 维度问题：方案 C 一劳永逸（加 platform + shop_name 列 + 历史回填）
- [x] 复购率：buyer_nick 子查询 RPC 实现
- [x] 发货时效：consign_time - pay_time AVG RPC 实现
- [x] 进销存：daily_stats + stock 复合视图
- [x] 供应商评估：RPC group_by=supplier 多指标组合
- [x] 分布直方图：CASE WHEN 分桶 RPC
- [x] 采购到货预警：ORM 规则判断
- [x] PlanBuilder 参数提取有关键词映射 + 23 个 few-shot 示例（原13+新10）
- [x] 向后兼容：query_type=auto 降级到现有逻辑
- [x] **Agent 链路打通**：5 个断裂点全部修复（§19 完整方案）
- [x] **参数流转验证矩阵**：5 个新参数每一层都有对应的透传代码
- [x] **返回格式规范**：8 种 data 结构 + metadata.query_type 标识
- [x] **域路由规则**：新查询类型到 domain 的映射明确
- [x] **L2 关键词兜底**：fill_query_type() 覆盖所有新查询类型
- [x] 有意不做项已标注原因（RFM/移动平均/销量预测）
