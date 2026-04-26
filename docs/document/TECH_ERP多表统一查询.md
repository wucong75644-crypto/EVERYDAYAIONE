# TECH_ERP 多表统一查询

> 版本：v2.0 | 日期：2026-04-26 | 状态：方案确认

## 1. 背景

用户问"库存负数的商品有多少"时，系统只能查 `erp_document_items`（上架单据），查不到 `erp_stock_status`（实时库存快照）。
原因：UnifiedQueryEngine 只服务 `erp_document_items` 一张表，其余 ERP 业务表未接入统一查询体系。

**目标**：把所有有业务查询价值的 ERP 表接入 UnifiedQueryEngine，让 LLM 用同一套 filters/sort_by/limit 能力查询任何 ERP 数据。

## 2. 项目上下文

### 架构现状
- UnifiedQueryEngine 是所有 ERP 查询的唯一入口，接收 doc_type + filters → summary 或 export
- 当前 6 个 doc_type（order/purchase/aftersale/receipt/shelf/purchase_return）全部路由到 `erp_document_items` 表
- summary 模式走 RPC 聚合（erp_global_stats_query / erp_order_stats_grouped）
- export 模式走 DuckDB 子进程流式导出
- COLUMN_WHITELIST 是全局的（120 列），不区分 doc_type 归属

### 可复用模块
- `validate_filters()` → `apply_orm_filters()` 管线：已支持 12 种操作符，新表直接复用
- `format_filter_hint()`：统一出口的 filter 条件回显
- `_query_local_data()`：DepartmentAgent 的标准查询入口
- `_query_kwargs()`：从 params 提取 mode/filters/sort_by/limit 的通用方法

### 设计约束
- COLUMN_WHITELIST 当前是全局 dict，不同表有同名但语义不同的字段（如 `outer_id` 在所有表都有）
- RPC 聚合函数（erp_global_stats_query）只查 `erp_document_items`，新表不走 RPC
- DuckDB export 路径硬编码了 `pg.public.erp_document_items`
- `erp_stock_status` 是快照表无时间维度（没有 doc_created_at），不能强制要求 time_range
- `OrgScopedDB` 已注册 erp_products / erp_product_skus / erp_stock_status，多租户隔离自动生效

### 潜在冲突
- 新表的字段名可能与现有 COLUMN_WHITELIST 冲突（如 `purchase_price` 在 stock/products/skus 都有）
- stock/product/sku 表无 doc_created_at 时间列，summary 默认时间范围逻辑不适用
- `_summary_classified()` 分类引擎只对 order doc_type 有意义，新 doc_type 不走此路径

## 3. 新增表分析

### 3.1 各表定位与查询场景

| 表 | doc_type | 行数 | 定位 | 典型查询 |
|---|---------|---:|------|---------|
| erp_stock_status | `stock` | 29k | 实时库存快照 | "库存负数""可用库存<10""缺货商品" |
| erp_products | `product` | 12k | 商品主数据 | "所有虚拟商品""品牌XX的商品""停售商品" |
| erp_product_skus | `sku` | 45k | SKU 明细 | "某商品的SKU列表""SKU价格异常" |
| erp_product_daily_stats | `daily_stats` | 798k | 商品日统计 | "本月各商品销量""退货率最高的商品" |
| erp_product_platform_map | `platform_map` | 427k | 平台-商品映射 | "淘宝在售商品""某商品在哪些平台售卖" |
| erp_batch_stock | `batch_stock` | 0 (待启用) | 批次效期库存 | "快过期的库存""某批次库存量" |
| erp_order_logs | `order_log` | 634k | 订单操作日志 | "某订单的操作记录""谁审核的这个订单" |
| erp_aftersale_logs | `aftersale_log` | 90k | 售后操作日志 | "某退货单的处理过程""售后处理时效" |

### 3.2 各表字段清单

> 排除规则：`id`(自增主键)、`org_id`(多租户自动注入)、`extra_json`(非结构化)、`synced_at`(同步元数据)统一排除不暴露给 LLM。
> 但 `synced_at` 保留在白名单中供内部时间过滤使用。

#### erp_stock_status（25 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| outer_id | text | 商品编码 | |
| sku_outer_id | text | SKU编码 | |
| item_name | text | 商品名称 | |
| properties_name | text | 规格属性 | |
| total_stock | numeric | 总库存 | |
| sellable_num | numeric | 可售数量 | |
| available_stock | numeric | 可用库存 | 总库存-锁定 |
| lock_stock | numeric | 锁定库存 | |
| purchase_num | numeric | 采购在途 | |
| on_the_way_num | numeric | 在途数量 | |
| defective_stock | numeric | 残次品库存 | |
| virtual_stock | numeric | 虚拟库存 | |
| stock_status | integer | 库存状态 | 0=正常/1=缺货/2=预警 |
| purchase_price | numeric | 采购价 | |
| selling_price | numeric | 销售价 | |
| market_price | numeric | 市场价 | |
| allocate_num | numeric | 调拨数量 | |
| refund_stock | numeric | 退货库存 | |
| purchase_stock | numeric | 采购库存 | |
| supplier_codes | text | 供应商编码 | |
| supplier_names | text | 供应商名称 | |
| warehouse_id | text | 仓库ID | |
| stock_modified_time | timestamp | 库存更新时间 | |
| synced_at | timestamp | 同步时间 | 内部用 |
| cid_name | text | 类目名称 | |

#### erp_products（25 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| outer_id | text | 商品编码 | |
| title | text | 商品名称 | |
| item_type | integer | 商品类型 | 0=普通/1=组合/2=赠品 |
| is_virtual | boolean | 是否虚拟 | |
| active_status | integer | 状态 | 1=在售/2=停售 |
| barcode | text | 条码 | |
| purchase_price | numeric | 采购价 | |
| selling_price | numeric | 销售价 | |
| market_price | numeric | 市场价 | |
| weight | numeric | 重量(g) | |
| unit | text | 单位 | |
| is_gift | boolean | 是否赠品 | |
| sys_item_id | text | 系统商品ID | |
| brand | text | 品牌 | |
| shipper | text | 发货人 | |
| remark | text | 备注 | |
| created_at | timestamp | 创建时间 | |
| modified_at | timestamp | 修改时间 | |
| pic_url | text | 图片URL | |
| length | numeric | 长(cm) | |
| width | numeric | 宽(cm) | |
| height | numeric | 高(cm) | |
| classify_name | text | 分类名称 | |
| seller_cat_name | text | 卖家自定义分类 | |
| is_sku_item | boolean | 是否有SKU | |
| synced_at | timestamp | 同步时间 | 内部用 |

#### erp_product_skus（19 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| outer_id | text | 商品编码 | |
| sku_outer_id | text | SKU编码 | |
| properties_name | text | 规格属性 | |
| barcode | text | 条码 | |
| purchase_price | numeric | 采购价 | |
| selling_price | numeric | 销售价 | |
| market_price | numeric | 市场价 | |
| weight | numeric | 重量(g) | |
| unit | text | 单位 | |
| shipper | text | 发货人 | |
| pic_url | text | 图片URL | |
| sys_sku_id | text | 系统SKU-ID | |
| active_status | integer | 状态 | |
| length | numeric | 长(cm) | |
| width | numeric | 宽(cm) | |
| height | numeric | 高(cm) | |
| sku_remark | text | SKU备注 | |
| platform_map_checked_at | timestamp | 平台映射校验时间 | |
| synced_at | timestamp | 同步时间 | 内部用 |

#### erp_product_daily_stats（34 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| stat_date | timestamp | 统计日期 | DB 实际类型 DATE，按 timestamp 处理支持范围过滤 |
| outer_id | text | 商品编码 | |
| sku_outer_id | text | SKU编码 | |
| item_name | text | 商品名称 | |
| purchase_count | integer | 采购单数 | |
| purchase_qty | numeric | 采购数量 | |
| purchase_received_qty | numeric | 采购到货数量 | |
| purchase_amount | numeric | 采购金额 | |
| receipt_count | integer | 收货单数 | |
| receipt_qty | numeric | 收货数量 | |
| shelf_count | integer | 上架单数 | |
| shelf_qty | numeric | 上架数量 | |
| purchase_return_count | integer | 采退单数 | |
| purchase_return_qty | numeric | 采退数量 | |
| purchase_return_amount | numeric | 采退金额 | |
| aftersale_count | integer | 售后总数 | |
| aftersale_refund_count | integer | 仅退款数 | |
| aftersale_return_count | integer | 退货退款数 | |
| aftersale_exchange_count | integer | 换货数 | |
| aftersale_reissue_count | integer | 补发数 | |
| aftersale_reject_count | integer | 拒收数 | |
| aftersale_repair_count | integer | 维修数 | |
| aftersale_other_count | integer | 其他售后数 | |
| aftersale_qty | numeric | 售后数量 | |
| aftersale_amount | numeric | 售后金额 | |
| order_count | integer | 订单数 | |
| order_qty | numeric | 订单数量 | |
| order_amount | numeric | 订单金额 | |
| order_shipped_count | integer | 已发货数 | |
| order_finished_count | integer | 已完成数 | |
| order_refund_count | integer | 退款订单数 | |
| order_cancelled_count | integer | 取消订单数 | |
| order_cost | numeric | 订单成本 | |
| updated_at | timestamp | 更新时间 | |

#### erp_product_platform_map（5 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| outer_id | text | 商品编码 | |
| num_iid | text | 平台商品ID | |
| user_id | text | 店铺用户ID | |
| title | text | 平台商品标题 | |
| synced_at | timestamp | 同步时间 | |

#### erp_batch_stock（11 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| outer_id | text | 商品编码 | |
| sku_outer_id | text | SKU编码 | |
| item_name | text | 商品名称 | |
| batch_no | text | 批次号 | |
| production_date | text | 生产日期 | 存储为字符串 |
| expiry_date | text | 过期日期 | 存储为字符串 |
| shelf_life_days | integer | 保质期(天) | |
| stock_qty | integer | 批次库存数量 | |
| warehouse_name | text | 仓库名称 | |
| shop_id | text | 店铺ID | |
| synced_at | timestamp | 同步时间 | 内部用 |

#### erp_order_logs（6 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| system_id | text | 订单系统ID | |
| operator | text | 操作人 | |
| action | text | 操作类型 | 审核/发货/修改/取消等 |
| content | text | 操作内容 | |
| operate_time | timestamp | 操作时间 | |
| synced_at | timestamp | 同步时间 | 内部用 |

#### erp_aftersale_logs（6 个可查字段）

| 字段 | 类型 | 中文标签 | 说明 |
|-----|------|---------|------|
| work_order_id | text | 工单号 | |
| operator | text | 操作人 | |
| action | text | 操作类型 | 创建/审核/退款/关闭等 |
| content | text | 操作内容 | |
| operate_time | timestamp | 操作时间 | |
| synced_at | timestamp | 同步时间 | 内部用 |

## 4. 架构设计

### 4.1 核心改动：按表分组的列白名单

**现状**：全局 dict，120 列混在一起。
**问题**：新表有同名字段，全局 dict 无法区分。
**方案**：validate_filters 加 `doc_type` 可选参数，内部用 `get_column_whitelist(doc_type)` 查对应表的白名单。现有调用方不传 doc_type 时走旧逻辑（全局白名单），向后兼容。

```python
DOC_TYPE_TABLE: dict[str, str] = {
    # 现有 → erp_document_items
    "order": "erp_document_items", "purchase": "erp_document_items",
    "aftersale": "erp_document_items", "receipt": "erp_document_items",
    "shelf": "erp_document_items", "purchase_return": "erp_document_items",
    # 新增 → 独立表
    "stock": "erp_stock_status",
    "product": "erp_products",
    "sku": "erp_product_skus",
    "daily_stats": "erp_product_daily_stats",
    "platform_map": "erp_product_platform_map",
    "batch_stock": "erp_batch_stock",
    "order_log": "erp_order_logs",
    "aftersale_log": "erp_aftersale_logs",
}

def get_column_whitelist(doc_type: str | None = None) -> dict[str, ColumnMeta]:
    """获取 doc_type 对应的列白名单。None 时返回全局白名单（向后兼容）。"""
    if doc_type is None or doc_type in _DOCUMENT_ITEM_DOC_TYPES:
        return COLUMN_WHITELIST  # 现有全局白名单
    return _TABLE_COLUMNS.get(doc_type, {})
```

### 4.2 查询路径选择

| doc_type | summary 模式 | export 模式 |
|---------|-------------|-------------|
| 现有 6 个 | RPC 聚合（不变） | DuckDB export（不变） |
| stock / product / sku / batch_stock | ORM 直查 + `count="exact"` | ORM 分页查询 |
| daily_stats | ORM + `count="exact"` | DuckDB export（80万行） |
| platform_map | ORM + `count="exact"` | ORM 分页查询 |
| order_log / aftersale_log | ORM + `count="exact"` | ORM 分页查询 |

**summary 聚合方式**（评审修正）：
- 用 Supabase `select("*", count="exact")` 获取 COUNT
- 不做全量加载内存聚合，避免大表 OOM
- 需要 SUM/AVG 的场景后续按需补 RPC

### 4.3 时间范围处理

| 表 | time_range | 时间列 |
|---|-----------|--------|
| erp_document_items（现有） | 必填 | doc_created_at / pay_time / consign_time 等 |
| erp_stock_status | 可选 | stock_modified_time |
| erp_products | 可选 | created_at / modified_at |
| erp_product_skus | 可选 | synced_at |
| erp_product_daily_stats | 必填 | stat_date |
| erp_product_platform_map | 可选 | synced_at |
| erp_batch_stock | 可选 | synced_at |
| erp_order_logs | 推荐 | operate_time |
| erp_aftersale_logs | 推荐 | operate_time |

### 4.4 PII 脱敏

新表不需要 PII 脱敏（无姓名/手机/地址字段）。`build_pii_select` 仅用于 erp_document_items 的 DuckDB export 路径，新表的 ORM 路径不调用。

## 5. 改动清单（7 个文件）

### 5.1 `backend/services/kuaimai/erp_unified_schema.py`

**A. VALID_DOC_TYPES 扩展**：
```python
VALID_DOC_TYPES = {
    "order", "purchase", "aftersale", "receipt", "shelf", "purchase_return",
    "stock", "product", "sku", "daily_stats", "platform_map",
    "batch_stock", "order_log", "aftersale_log",
}
```

**B. 新增 DOC_TYPE_TABLE 映射 + get_column_whitelist() 函数**

**C. 8 张新表的列白名单定义**（共 ~131 个字段）

**D. 新增 REQUIRED_FIELDS / DEFAULT_DETAIL_FIELDS**：每个新 doc_type

**E. DOC_TYPE_CN 补充**：
```python
"stock": "库存", "product": "商品", "sku": "SKU",
"daily_stats": "日统计", "platform_map": "平台映射",
"batch_stock": "批次库存", "order_log": "订单日志", "aftersale_log": "售后日志"
```

### 5.2 `backend/services/kuaimai/erp_unified_query.py`

**A. execute() 路由改造**：
```python
table = DOC_TYPE_TABLE.get(doc_type, "erp_document_items")
if table == "erp_document_items":
    # 现有逻辑完全不变
    ...
else:
    # 新表走 ORM 直查
    if mode == "summary":
        result = await self._summary_orm(table, doc_type, validated, ...)
    else:
        result = await self._export_orm(table, doc_type, validated, ...)
```

**B. 新增 `_summary_orm()`**：ORM 查询 + `count="exact"` 聚合

**C. 新增 `_export_orm()`**：ORM 分页查询 + 写 Parquet

**D. time_range 可选化**：新表（stock/product/sku/batch_stock/platform_map）不强制时间范围

### 5.3 `backend/services/kuaimai/erp_unified_filters.py`

**A. validate_filters 签名扩展**：加 `doc_type: str | None = None` 可选参数
- None → 用全局 COLUMN_WHITELIST（向后兼容）
- 有值 → 用 `get_column_whitelist(doc_type)` 查对应白名单

**B. extract_time_range 适配**：新表无时间列时返回宽松默认值

### 5.4 `backend/services/agent/departments/warehouse_agent.py`

**A. allowed_doc_types 扩展**：加 `"stock"`, `"batch_stock"`

**B. _DOC_TYPE_ACTION_MAP 扩展**：`"stock": "stock_data_query"`, `"batch_stock": "batch_stock_query"`

**C. _dispatch 补充**：新 action 走 `_query_local_data(doc_type=..., ...)`

### 5.5 `backend/services/agent/plan_builder.py`

**A. _PARAM_DEFINITIONS 补充**：新 doc_type 选项 + 各表可用字段说明

**B. few-shot 示例补充**（覆盖 8 张新表的典型查询）

### 5.6 `backend/config/erp_local_tools.py`

**A. local_data 工具 doc_type enum 扩展**：加入 8 个新 doc_type

### 5.7 `backend/services/agent/erp_tool_description.py`

**A. 能力清单补充**：新增查询能力描述

## 6. 端到端数据流

```
用户："库存负数的商品有多少"

① PlanBuilder(千问) 提取：
   doc_type=stock, mode=summary, numeric_filters=[{field:available_stock, op:lt, value:0}]

② _sanitize_params → numeric_filters 透传（list[dict] 白名单）

③ params_to_filters → [{field:available_stock, op:lt, value:0}]

④ warehouse_agent: doc_type=stock → _DOC_TYPE_ACTION_MAP → stock_data_query
   → _query_local_data(doc_type="stock", filters=[...])

⑤ UnifiedQueryEngine.execute(doc_type="stock")
   → DOC_TYPE_TABLE["stock"] = "erp_stock_status"
   → 非 erp_document_items → ORM 直查
   → validate_filters(filters, doc_type="stock") 用 STOCK_COLUMNS 白名单
   → _summary_orm: db.table("erp_stock_status").select("*", count="exact")
     .lt("available_stock", 0).execute()
   → count=159

⑥ "[过滤条件] 可用库存 < 0\n库存查询：共 159 条记录"
```

## 7. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| stock 表不传 time_range | 查全量（29k 行秒级） | execute() 时间逻辑 |
| daily_stats 月度导出（80万行） | 走 DuckDB export | _export() 路径选择 |
| order_logs 按系统ID查（634k） | ORM + operate_time 范围 | validate_filters |
| batch_stock 表为空（0行） | 返回空结果 + "暂无数据" | _summary_orm |
| 新旧表同名字段（outer_id） | 按 doc_type 分组白名单 | get_column_whitelist |
| LLM 对新 doc_type 提取不准 | few-shot 示例 + _classify_action 兜底 | plan_builder + agent |
| summary 需要 SUM 聚合 | 先用 count，后续按需补 RPC | _summary_orm |

## 8. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | 复用 UnifiedQueryEngine，不新增模块 | 低 | — |
| 数据流向 | 与现有 params→filters→engine→result 一致 | 低 | — |
| 扩展性 | ORM 对 29k~45k 没问题；daily_stats 80 万走 DuckDB | 低 | — |
| 耦合度 | validate_filters 加可选参数，侵入性最小 | 低 | None 时走旧逻辑 |
| 一致性 | 新表走 ORM 而非 RPC，模式不完全一致 | 中 | 封装 _summary_orm/_export_orm |
| 可观测性 | 复用 format_filter_hint + 现有日志 | 低 | — |
| 可回滚性 | 纯代码改动，无数据库迁移 | 低 | git revert |

## 9. 执行顺序

1. **erp_unified_schema.py** — 8 张新表列白名单 + DOC_TYPE_TABLE + get_column_whitelist()
2. **erp_unified_filters.py** — validate_filters 加 doc_type 可选参数
3. **erp_unified_query.py** — 新增 _summary_orm / _export_orm + execute 路由
4. **warehouse_agent.py** — allowed_doc_types + _DOC_TYPE_ACTION_MAP + _dispatch
5. **plan_builder.py** — doc_type 描述 + few-shot 示例
6. **erp_local_tools.py + erp_tool_description.py** — 工具定义扩展
7. **测试 + E2E 验证**

## 10. 验证方式

1. 单元测试：validate_filters 对新 doc_type 的白名单校验
2. E2E 测试：
   - "库存负数的商品有多少" → doc_type=stock, 159 条
   - "停售商品列表" → doc_type=product, active_status=2
   - "本月各商品销量Top10" → doc_type=daily_stats, sort_by=order_qty
   - "某商品在哪些平台售卖" → doc_type=platform_map
   - "某订单的操作记录" → doc_type=order_log, system_id=xxx
3. 回归：现有 6 个 doc_type 查询不受影响

## 11. 评审修正记录（v1.0 → v2.0）

| 问题 | 来源 | 修正 |
|------|------|------|
| erp_product_skus 缺 platform_map_checked_at | 字段审查 | 已加入（19 列） |
| erp_products 缺 synced_at | 字段审查 | 已加入（25 列） |
| stat_date 标注为 text | 字段审查 | 改为 timestamp（支持范围过滤） |
| synced_at 排除不一致 | 字段审查 | 统一保留在白名单，标注"内部用" |
| summary 内存聚合风险 | 性能工程师 | 改用 `count="exact"`，不全量加载 |
| erp_batch_stock 未覆盖 | 运维专家 | 已加入（11 列） |
| erp_order_logs/aftersale_logs 未覆盖 | 运维专家 + 用户确认 | 已加入（各 6 列） |
| validate_filters 签名侵入性 | 架构师 | 改为可选参数 doc_type，None 走旧逻辑 |
| 新表无 PII 脱敏 | 风险评估 | 确认新表无 PII 字段，不需要 |

## 12. 设计自检

- [x] 项目上下文 4 点完整
- [x] 8 张新表字段与 information_schema 校对一致
- [x] 评审发现的 8 个问题全部修正
- [x] 边界场景有处理策略
- [x] 架构影响评估无高风险项
- [x] 向后兼容：现有 6 个 doc_type 不受影响
- [x] 无数据库迁移，回滚安全
- [x] 无新增依赖
