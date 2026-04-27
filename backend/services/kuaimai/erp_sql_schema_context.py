"""ERP SQL 兜底——表结构与枚举上下文（启动时编译一次）。

为千问 LLM 生成 SQL 提供紧凑的 DDL + 枚举映射。
三层上下文总计 ~3200 token，千问 128k 窗口完全可用。

设计文档：docs/document/TECH_ERP查询架构重构.md §20.2
"""
from __future__ import annotations

from services.kuaimai.erp_unified_schema import PLATFORM_CN, DOC_TYPE_CN

# ── 第一层：表结构（~2000 token） ──────────────────────

ERP_SCHEMA_CONTEXT = """## 可查询的表

### erp_document_items（订单/采购/售后/收货/上架/采退 主表，近3个月）
- 按 doc_type 区分：order/purchase/aftersale/receipt/shelf/purchase_return
- 主键：id | 唯一：(org_id, doc_type, doc_id, outer_id, sku_outer_id)
- 时间列：doc_created_at（默认）, pay_time, consign_time, delivery_date, finished_at
- 重要索引：(doc_type, outer_id, doc_created_at DESC), (platform), (shop_name, doc_type), (order_no)
核心列：
  doc_type, doc_id, doc_code, doc_status, order_status,
  outer_id(商品编码), sku_outer_id(SKU编码), item_name(商品名称),
  quantity(数量), amount(金额), cost(成本), gross_profit(毛利),
  shop_name(店铺), platform(平台编码), supplier_name(供应商), warehouse_name(仓库),
  order_no(订单号), buyer_nick(买家昵称), order_type(订单类型),
  pay_time(付款时间), consign_time(发货时间), doc_created_at(创建时间),
  is_cancel(是否取消), is_refund(是否退款),
  aftersale_type(售后类型), refund_status(退款状态)

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
"""


# ── 第二层：枚举值（~500 token） ──────────────────────

# 动态生成 platform 编码映射字符串
_platform_pairs = ", ".join(f"{k}={v}" for k, v in PLATFORM_CN.items())
_doc_type_pairs = ", ".join(f"{k}={v}" for k, v in DOC_TYPE_CN.items() if k not in (
    "stock", "product", "sku", "daily_stats", "platform_map", "batch_stock",
))

ENUM_CONTEXT = f"""## 枚举值速查

platform 编码：{_platform_pairs}
doc_type 值：{_doc_type_pairs}
order_status：WAIT_PAY(待付款), WAIT_SEND(待发货), SEND(已发货), FINISH(已完成), CLOSED(已关闭)
aftersale_type：1=退款, 2=退货, 3=补发, 4=换货, 5=发货前退款
refund_status：0=无退款, 1=退款中, 2=退款成功, 3=退款关闭
order_type：0=普通, 4=线下, 7=合并, 8=拆分, 13=换货, 14=补发（逗号分隔多值）
布尔字段（is_cancel等）：0=否, 1=是
"""


# ── SQL 生成 Prompt 模板 ──────────────────────────────

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
FROM erp_document_items WHERE doc_type='order' AND org_id='{{org_id}}'
  AND doc_created_at >= '...' GROUP BY platform ORDER BY "金额" DESC LIMIT 200;

-- 趋势查询
SELECT stat_date AS "日期", SUM(order_count) AS "订单数", SUM(order_amount) AS "销售额"
FROM erp_product_daily_stats WHERE org_id='{{org_id}}' AND stat_date >= '...'
GROUP BY stat_date ORDER BY stat_date LIMIT 200;

-- 跨表比率
SELECT ROUND(SUM(aftersale_count)::numeric / NULLIF(SUM(order_count), 0) * 100, 2) AS "退货率(%)"
FROM erp_product_daily_stats WHERE org_id='{{org_id}}' AND stat_date >= '...' LIMIT 200;

只输出 SQL：
"""
