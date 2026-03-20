## 技术设计：ERP 本地优先统一查询架构 V2

> 核心思路：本地库是跨API的JOIN引擎，API是专项能力补充。
> 本地表已存储 order_no/doc_code/express_no/supplier_name/shop_name 等多维度字段，
> 但现有工具只暴露了 outer_id 一个入口维度。本次升级释放全部查询能力。

---

### 1. 现有代码分析

**已阅读文件**：
- `config/erp_local_tools.py`：8个本地工具定义 + Schema + 路由提示词
- `config/erp_tools.py`：8个API工具定义 + ERP_ROUTING_PROMPT
- `config/agent_tools.py`：工具注册中心，`INFO_TOOLS` 自动合并 `ERP_LOCAL_TOOLS`
- `services/tool_executor.py`：工具执行器，`_local_dispatch()` 分发本地工具
- `services/kuaimai/erp_sync_service.py`：同步核心，`sync(sync_type)` 入口
- `services/kuaimai/erp_sync_worker.py`：后台 Worker，每60s自动同步
- `services/kuaimai/erp_local_identify.py`：本地编码识别
- `services/kuaimai/erp_local_query.py`：6个本地查询函数
- `services/kuaimai/erp_local_helpers.py`：`check_sync_health()` 共享工具
- `services/kuaimai/erp_sync_handlers.py`：6个单据同步处理器（字段映射）
- `services/kuaimai/erp_sync_master_handlers.py`：4个主数据同步处理器
- `services/kuaimai/registry/*.py`：全部10个注册表（89个API）
- `migrations/029_erp_local_index_system.sql`：表结构 + 索引

**核心发现**：

1. **erp_document_items 表已存但未利用的查询维度**：
   - `order_no` — 平台订单号（**已有索引** idx_doc_items_order_no）
   - `shop_name` — 店铺名称（**已有索引** idx_doc_items_shop）
   - `doc_code` — 采购/收货/上架/采退单号（已存，无索引）
   - `express_no` — 快递单号（已存，无索引）
   - `supplier_name` — 供应商名称（已存，无索引，**且 API 也不支持按供应商查采购**）
   - `doc_id` — system_id（PK 一部分，但 local_order_query 不返回给 AI）

2. **API 跨类型查询的局限**：
   - 订单 API 不支持按 outer_id 查（按编码查订单只有本地能做）
   - 采购 API 不支持按 supplier 查（按供应商查采购只有本地能做）
   - 售后 API 不支持按 system_id 查（必须用 order_id）
   - 不同 API 的主键完全不互通

3. **中转钥匙断裂**：local_order_query 不返回 system_id(doc_id)，导致跨物流/日志查询多一次 API 调用

4. **API 单条查询能力**：
   - product → `item.single.get(outerId)` ✓
   - stock → `stock_status(outer_id)` ✓
   - platform_map → `erp.item.outerid.list.get(outerIds)` ✓
   - 6个单据类型 → 只支持时间范围增量，不支持单条

**可复用模块**：
- `ErpSyncService.sync(sync_type)` — 增量/全量同步，直接复用
- `check_sync_health()` — 同步健康检查
- `query_doc_items()` — erp_document_items 查询基础函数
- `ERP_LOCAL_TOOLS` set + `build_local_tools()` + `_local_dispatch()` — 工具注册机制

**设计约束**：
- 新工具遵循 `func(db, **args)` 签名
- 新工具通过 `ERP_LOCAL_TOOLS` 自动注册到 `INFO_TOOLS`，无需改 `agent_tools.py`
- upsert 幂等，与 Worker 并发安全

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 local_doc_query 工具 | `erp_local_tools.py` | ERP_LOCAL_TOOLS、build_local_tools()、LOCAL_TOOL_SCHEMAS |
| 新增 local_global_stats 工具 | `erp_local_tools.py` | 同上 |
| 新增 trigger_erp_sync 工具 | `erp_local_tools.py` | 同上 |
| 新增 3 个 dispatch 分发 | `tool_executor.py` | _local_dispatch() 的 dispatch dict |
| local_doc_query 实现 | 新文件 `erp_local_doc_query.py` | 查询逻辑 + 格式化 |
| local_global_stats 实现 | 新文件 `erp_local_global_stats.py` | 聚合逻辑 + 格式化 |
| trigger_erp_sync 实现 | `tool_executor.py` 内联 | ~20 行 |
| local_product_identify 增强 | `erp_local_identify.py` | 未识别时 API 兜底 |
| 路由提示词全面重写 | `erp_local_tools.py` | LOCAL_ROUTING_PROMPT |
| 补索引 | 新迁移文件 | 3 个新索引 |
| `agent_tools.py` | **无需修改** | 自动合并 |
| `erp_tools.py` | **无需修改** | LOCAL_ROUTING_PROMPT 已拼接 |
| 现有 8 个本地工具 | **保留不动** | 向后兼容 |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 商品编码本地查不到 | identify 自动调 item.single.get API 兜底，有则写入返回，无则"不存在" | erp_local_identify |
| 库存本地查不到 | 同上，stock_status API 支持按编码单条查 | 可选增强 |
| 单据本地查不到+同步正常 | 数据确实不存在，直接告知用户 | 路由提示词 |
| 单据本地查不到+同步异常 | AI 调 trigger_erp_sync → 增量同步 → 重查 | trigger_erp_sync |
| 首次部署无数据 | trigger_erp_sync 触发初始全量同步（可能分钟级） | ErpSyncService |
| 同步超时（>120s） | asyncio.wait_for 保护，返回"后台继续同步" | trigger_erp_sync |
| 与 Worker 并发同步 | upsert 幂等，最多重复拉取 | ErpSyncService |
| 无效 sync_type | 返回错误 + 有效类型列表 | trigger_erp_sync |
| ERP 未配置 | identify API 兜底优雅降级为"本地无数据" | erp_local_identify |
| 多维度组合查询无结果 | 附 sync health，AI 判断是否触发同步 | local_doc_query |
| 模糊搜索结果过多 | LIMIT 50 截断 + 提示"结果较多请缩小范围" | local_doc_query |
| 归档数据（>90天） | query_doc_items 自动 UNION 冷表（已实现） | erp_local_helpers |
| 供应商名模糊匹配 | ILIKE 模糊搜 + LIMIT 保护 | local_doc_query |

---

### 3. 技术栈

- 后端：Python 3.x + FastAPI（现有）
- 数据库：Supabase PostgreSQL（现有表 + 3 个新索引）
- 同步引擎：ErpSyncService（现有，直接复用）
- 无新增依赖

---

### 4. 目录结构

#### 新增文件（3个）
- `backend/services/kuaimai/erp_local_doc_query.py`：多维度单据查询（~200行）
- `backend/services/kuaimai/erp_local_global_stats.py`：全局统计/排名（~150行）
- `backend/migrations/031_erp_local_query_indexes.sql`：补索引

#### 修改文件（3个）
- `backend/config/erp_local_tools.py`：新增3个工具定义 + 路由提示词重写
- `backend/services/tool_executor.py`：新增3个 dispatch 条目
- `backend/services/kuaimai/erp_local_identify.py`：未识别时 API 兜底

#### 保留不动
- 现有 8 个本地工具（local_purchase_query 等）全部保留，向后兼容
- `agent_tools.py`、`erp_tools.py` 无需改动

---

### 5. 数据库设计

#### 新增索引（迁移文件 031）

```sql
-- 按快递单号查订单
CREATE INDEX IF NOT EXISTS idx_doc_items_express
  ON erp_document_items (express_no)
  WHERE express_no IS NOT NULL;

-- 按采购/收货单号查单据
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_code
  ON erp_document_items (doc_code, doc_type)
  WHERE doc_code IS NOT NULL;

-- 按供应商查采购/收货/采退（API 不支持此维度！）
CREATE INDEX IF NOT EXISTS idx_doc_items_supplier
  ON erp_document_items (supplier_name, doc_type)
  WHERE supplier_name IS NOT NULL;
```

已有索引（无需新增）：
- `idx_doc_items_order_no` — 按 order_no 查
- `idx_doc_items_shop` — 按 shop_name + doc_type 查
- `idx_doc_items_outer` — 按 outer_id/sku_outer_id + doc_type 查

---

### 6. 工具定义设计

#### 工具1：local_doc_query（多维度单据查询）

```python
_tool(
    "local_doc_query",
    "多维度单据查询。支持按订单号/快递号/采购单号/供应商/店铺查询单据，"
    "返回完整信息含所有关联ID（sid/order_no/express_no/outer_id），"
    "可直接用于跨工具查询（如拿 sid 查物流）。毫秒级响应。",
    {
        "product_code": _str("商品编码（主编码或SKU编码）"),
        "order_no": _str("平台订单号（淘宝/京东/拼多多等平台单号）"),
        "doc_code": _str("单据编号（采购单号/收货单号/上架单号/采退单号）"),
        "express_no": _str("快递单号"),
        "supplier_name": _str("供应商名称（模糊搜索）"),
        "shop_name": _str("店铺名称（模糊搜索）"),
        "doc_type": _enum(
            "单据类型过滤",
            ["order", "purchase", "receipt", "shelf",
             "aftersale", "purchase_return"],
        ),
        "status": _str("状态过滤"),
        "days": _int("查询最近N天，默认30"),
    },
    [],  # 无强制必填，但至少传一个查询维度
)
```

**返回格式**（关键：始终暴露所有中转钥匙）：

```
查询结果（共3笔订单）：

1. 订单 sid=9876543210 | order_no=TB1234567890123456
   商品: ABC123(保温杯) × 2件 ¥99.00 | SKU: ABC123-红色
   状态: 已发货 | 快递: SF789012(顺丰) | 发货: 2026-03-20
   店铺: 淘宝旗舰店 | 平台: tb

2. 订单 sid=9876543211 | order_no=TB1234567890123457
   商品: ABC123(保温杯) × 1件 ¥49.50 | SKU: ABC123-蓝色
   状态: 待发货 | 店铺: 淘宝旗舰店 | 平台: tb

📊 汇总：3笔 | 5件 | ¥247.50
```

采购场景返回：
```
查询结果（供应商"张三"近30天采购）：

1. 采购单 doc_code=PO20260315 | doc_id=88001
   商品: ABC123 × 100件 ¥15/件 | 已到货: 60件(60%)
   状态: 部分到货 | 供应商: 张三贸易

2. 采购单 doc_code=PO20260310 | doc_id=88002
   商品: DEF456 × 50件 ¥8/件 | 已到货: 50件(100%)
   状态: 已完成 | 供应商: 张三贸易

📊 汇总：2笔 | 150件 | 已到货110件(73.3%)
```

#### 工具2：local_global_stats（全局统计/排名）

```python
_tool(
    "local_global_stats",
    "全局统计查询（无需商品编码）。支持按时间/类型/店铺/供应商"
    "统计订单数/销售额/退货数等，支持排名。本地查询。",
    {
        "doc_type": _enum(
            "统计类型",
            ["order", "purchase", "aftersale", "receipt",
             "shelf", "purchase_return"],
        ),
        "date": _str("统计日期(YYYY-MM-DD)，默认今天"),
        "period": _enum("统计周期", ["day", "week", "month"]),
        "shop_name": _str("按店铺过滤（模糊匹配）"),
        "platform": _str("按平台过滤(tb/jd/pdd/dy/xhs)"),
        "supplier_name": _str("按供应商过滤（模糊匹配）"),
        "warehouse_name": _str("按仓库过滤"),
        "rank_by": _enum(
            "排名维度（返回TOP10）",
            ["count", "quantity", "amount"],
        ),
        "group_by": _enum(
            "分组维度",
            ["product", "shop", "platform", "supplier", "warehouse"],
        ),
    },
    ["doc_type"],
)
```

**返回格式**：

```
今日订单统计（2026-03-21）：

总计: 156笔 | 销量 423件 | 金额 ¥45,678.00

按平台:
  淘宝: 80笔 ¥25,120 | 拼多多: 50笔 ¥12,340 | 抖音: 26笔 ¥8,218

按状态:
  待发货: 42笔 | 已发货: 98笔 | 已完成: 16笔
```

排名场景：
```
本月售后TOP10（按笔数）：

1. ABC123(保温杯) — 15笔 | 退货12 退款3
2. DEF456(手机壳) — 12笔 | 退货8 换货4
3. GHI789(数据线) — 8笔 | 退货5 仅退款3
...
```

#### 工具3：trigger_erp_sync（单据同步兜底）

```python
_tool(
    "trigger_erp_sync",
    "手动触发ERP数据同步。仅在本地查询无数据且同步状态异常时使用。"
    "商品/库存查不到不要用此工具（local_product_identify 会自动兜底）。"
    "同步完成后重新调用原查询工具。",
    {
        "sync_type": _enum(
            "同步类型",
            ["order", "purchase", "receipt", "shelf",
             "aftersale", "purchase_return",
             "product", "stock", "supplier", "platform_map"],
        ),
    },
    ["sync_type"],
)
```

#### local_product_identify 增强（改现有工具）

无需改工具定义，只改内部实现：未识别时自动调 `item.single.get(outerId=code)` → 有则写入本地 → 返回。

---

### 7. 路由提示词设计

```python
LOCAL_ROUTING_PROMPT = (
    "## 本地查询工具\n"
    "本地工具直接查数据库，毫秒级响应。\n\n"

    "### 输入识别 → 工具选择\n"
    "- 商品编码/条码/商品名 → local_product_identify（编码识别入口）\n"
    "- 商品编码 + 查库存 → local_stock_query\n"
    "- 商品编码 + 查采购/订单/售后/流转 → local_doc_query(product_code=XX, doc_type=YY)\n"
    "- 商品编码 + 查统计趋势 → local_product_stats\n"
    "- 商品编码 + 查全链路 → local_product_flow\n"
    "- 商品编码 + 查平台映射 → local_platform_map_query\n"
    "- 平台订单号 → local_doc_query(order_no=XX)\n"
    "- 快递单号 → local_doc_query(express_no=XX)\n"
    "- 采购/收货单号 → local_doc_query(doc_code=XX)\n"
    "- 供应商名 + 查采购 → local_doc_query(supplier_name=XX, doc_type=purchase)\n"
    "- 店铺名 + 查订单/售后 → local_doc_query(shop_name=XX, doc_type=YY)\n"
    "- 全局统计(今天多少单/退货排名/各平台对比) → local_global_stats\n\n"

    "### 查询优先级\n"
    "1. 所有查询先走本地工具（毫秒级）\n"
    "2. 商品/库存查不到 → local_product_identify 自动 API 兜底，无需额外操作\n"
    "3. 单据查不到 + 同步状态异常(返回中有⚠标记) → trigger_erp_sync → 重查\n"
    "4. 单据查不到 + 同步状态正常 → 数据确实不存在，直接告知用户\n"
    "5. 超出本地能力（物流轨迹/操作日志/买家查询/仓库操作/写操作）\n"
    "   → 用本地返回的 sid/order_no/outer_id 精准调 API\n\n"

    "### 中转钥匙用法\n"
    "local_doc_query 返回中包含 sid(system_id)、order_no、express_no、outer_id 等字段，\n"
    "可直接用于跨工具查询：\n"
    "- 查物流轨迹 → 用返回的 sid 调 express_query(system_id=sid)\n"
    "- 查操作日志 → 用返回的 sid 调 order_log(system_ids=sid)\n"
    "- 查退货入库 → 用返回的 doc_id(工单号) 调 refund_warehouse(work_order_ids=XX)\n"
    "- 查商品详情 → 用返回的 outer_id 调 local_product_identify\n\n"

    "### trigger_erp_sync 使用规则\n"
    "- 仅在「单据查不到 + 同步状态异常」时触发\n"
    "- 商品/库存查不到 → 不要用此工具（identify 自动兜底）\n"
    "- 同步状态正常(无⚠标记) → 不触发，数据就是不存在\n"
    "- 用户说「刚录入」「刚下单」→ 可触发\n"
    "- sync_type: 订单→order, 采购→purchase, 售后→aftersale, "
    "收货→receipt, 上架→shelf, 采退→purchase_return\n\n"

    "### 仍需 API 的场景\n"
    "- 物流轨迹详情 → express_query(system_id)\n"
    "- 操作日志 → order_log(system_ids) / aftersale_log(work_order_id)\n"
    "- 退货入库详情 → refund_warehouse(work_order_ids)\n"
    "- 买家维度查询 → order_list(buyer=XX)\n"
    "- 各仓库存分布 → warehouse_stock(outer_id)\n"
    "- 仓库操作(调拨/盘点/加工) → erp_warehouse_query\n"
    "- 写操作 → erp_execute\n"
    "- 波次/唯一码 → erp_trade_query\n"
)
```

---

### 8. 调用链对比（12个典型场景）

| # | 场景 | 改前 | 改后 | 节省 |
|---|------|------|------|------|
| 1 | 商品→库存 | 3轮/0API | 3轮/0API | — |
| 2 | 订单号→详情 | 4轮/1API+1文档 | **2轮/0API** | 2轮+1API |
| 3 | 商品→物流轨迹 | 5轮/2API | **4轮/1API** | 1轮+1API |
| 4 | 采购单号→到货 | 4轮/1API+1文档 | **2轮/0API** | 2轮+1API |
| 5 | 供应商→采购 | ❌ 死路 | **2轮/0API** | 从0到1 |
| 6 | 今天多少单 | 2轮/1API | **2轮/0API** | 1API |
| 7 | 快递号→订单 | 4轮/1API+1文档 | **2轮/0API** | 2轮+1API |
| 8 | 商品→退货入库 | 4轮/1API | 4轮/1API | — |
| 9 | 店铺→退款 | 4轮/2API+1文档 | **2轮/0API** | 2轮+2API |
| 10 | 退货排名 | ❌ 做不到 | **2轮/0API** | 从0到1 |
| 11 | 新商品查不到 | 2轮/返回失败 | **2轮/自动兜底** | 体验质变 |
| 12 | 买家→订单 | 2轮/1API | 2轮/1API | — |

---

### 9. 核心实现伪代码

#### 9.1 local_doc_query（erp_local_doc_query.py）

```python
async def local_doc_query(
    db: Client,
    product_code: str | None = None,
    order_no: str | None = None,
    doc_code: str | None = None,
    express_no: str | None = None,
    supplier_name: str | None = None,
    shop_name: str | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    days: int = 30,
) -> str:
    """多维度单据查询，返回完整信息含所有中转钥匙"""
    if not any([product_code, order_no, doc_code, express_no,
                supplier_name, shop_name]):
        return "请至少提供一个查询条件"

    cutoff = cutoff_iso(days)
    q = db.table("erp_document_items").select("*")

    # 查询维度（多条件 AND 组合）
    if product_code:
        q = q.or_(f"outer_id.eq.{product_code},sku_outer_id.eq.{product_code}")
    if order_no:
        q = q.eq("order_no", order_no)
    if doc_code:
        q = q.eq("doc_code", doc_code)
    if express_no:
        q = q.eq("express_no", express_no)
    if supplier_name:
        q = q.ilike("supplier_name", f"%{supplier_name}%")
    if shop_name:
        q = q.ilike("shop_name", f"%{shop_name}%")
    if doc_type:
        q = q.eq("doc_type", doc_type)
    if status:
        q = q.or_(f"doc_status.eq.{status},order_status.eq.{status}")

    q = q.gte("doc_created_at", cutoff).order("doc_created_at", desc=True).limit(50)
    rows = q.execute().data or []

    if not rows:
        # 确定需要检查哪些同步类型
        types = [doc_type] if doc_type else ["order", "purchase", "aftersale"]
        health = check_sync_health(db, types)
        return f"未查到匹配记录（近{days}天）\n{health}".strip()

    return _format_doc_results(rows)


def _format_doc_results(rows: list[dict]) -> str:
    """格式化结果，按 doc_id 聚合，暴露所有中转钥匙"""
    # 按 doc_id 聚合
    docs: dict[str, list[dict]] = {}
    for r in rows:
        docs.setdefault(r["doc_id"], []).append(r)

    lines = [f"查询结果（共{len(docs)}笔单据）：\n"]
    for doc_id, items in list(docs.items())[:20]:  # 最多展示20笔
        first = items[0]
        dt = first.get("doc_type", "")
        # 始终暴露所有中转钥匙
        keys = [f"sid={doc_id}"]
        if first.get("order_no"):
            keys.append(f"order_no={first['order_no']}")
        if first.get("doc_code"):
            keys.append(f"doc_code={first['doc_code']}")
        if first.get("express_no"):
            keys.append(f"express={first['express_no']}({first.get('express_company','')})")

        lines.append(f"{_type_name(dt)} {' | '.join(keys)}")
        for item in items:
            lines.append(
                f"  商品: {item.get('outer_id','')}({item.get('item_name','')}) "
                f"× {item.get('quantity','')}件 ¥{item.get('amount','')}"
            )
        if first.get("supplier_name"):
            lines.append(f"  供应商: {first['supplier_name']}")
        lines.append(f"  状态: {first.get('doc_status','')} | "
                      f"时间: {str(first.get('doc_created_at',''))[:10]}")
        lines.append("")

    # 汇总
    total_qty = sum(r.get("quantity") or 0 for r in rows)
    total_amt = sum(float(r.get("amount") or 0) for r in rows)
    lines.append(f"📊 汇总：{len(docs)}笔 | {total_qty}件 | ¥{total_amt:,.2f}")

    # 同步健康
    types = list({r.get("doc_type", "") for r in rows})
    health = check_sync_health(db, types)
    if health:
        lines.append(health)
    return "\n".join(lines)
```

#### 9.2 local_global_stats（erp_local_global_stats.py）

```python
async def local_global_stats(
    db: Client,
    doc_type: str,
    date: str | None = None,
    period: str = "day",
    shop_name: str | None = None,
    platform: str | None = None,
    supplier_name: str | None = None,
    warehouse_name: str | None = None,
    rank_by: str | None = None,
    group_by: str | None = None,
) -> str:
    """全局统计/排名（无需 product_code）"""
    start, end = _calc_period(date, period)

    q = (db.table("erp_document_items").select("*")
         .eq("doc_type", doc_type)
         .gte("doc_created_at", start)
         .lte("doc_created_at", end))

    if shop_name:
        q = q.ilike("shop_name", f"%{shop_name}%")
    if platform:
        q = q.eq("platform", platform)
    if supplier_name:
        q = q.ilike("supplier_name", f"%{supplier_name}%")
    if warehouse_name:
        q = q.ilike("warehouse_name", f"%{warehouse_name}%")

    rows = q.limit(5000).execute().data or []

    if not rows:
        health = check_sync_health(db, [doc_type])
        return f"{doc_type} 在{period}内无记录\n{health}".strip()

    if rank_by:
        return _format_ranking(rows, rank_by, period)
    if group_by:
        return _format_grouped(rows, group_by, period)
    return _format_summary(rows, doc_type, period)
```

#### 9.3 local_product_identify 增强（erp_local_identify.py 修改）

```python
# 在 _identify_by_code 函数的 "4. 未识别" 分支前插入：

# 4. API 兜底：单条查询确认是否存在
try:
    from services.kuaimai.client import KuaiMaiClient
    client = KuaiMaiClient()
    if client.is_configured:
        await client.load_cached_token()
        data = await client.request_with_retry(
            "item.single.get", {"outerId": code}
        )
        if data and data.get("outerId"):
            # 写入本地 erp_products
            _upsert_product_from_api(db, data)
            # 重新走本地查询
            result = db.table("erp_products").select("*").eq("outer_id", code).limit(1).execute()
            if result.data:
                return _format_product(db, code, result.data[0])
        await client.close()
except Exception as e:
    logger.debug(f"API fallback failed | code={code} | {e}")

# 5. 确认不存在
return f"编码识别: {code}\n✗ 该编码在ERP中不存在（本地+API均未找到）"
```

#### 9.4 trigger_erp_sync（tool_executor.py 内联）

```python
async def _trigger_erp_sync(self, db: Client, sync_type: str) -> str:
    """手动触发 ERP 同步（带超时保护 + 新鲜度检查）"""
    import asyncio
    import time

    VALID = {"product","stock","supplier","platform_map",
             "order","purchase","receipt","shelf","aftersale","purchase_return"}
    if sync_type not in VALID:
        return f"✗ 无效类型: {sync_type}，可选: {', '.join(sorted(VALID))}"

    # 新鲜度检查：2分钟内同步过则跳过
    state = db.table("erp_sync_state").select("last_run_at").eq(
        "sync_type", sync_type).execute()
    if state.data and state.data[0].get("last_run_at"):
        from datetime import datetime, timezone
        last = datetime.fromisoformat(
            str(state.data[0]["last_run_at"]).replace("Z","+00:00"))
        if (datetime.now(timezone.utc) - last).total_seconds() < 120:
            return f"ℹ {sync_type} 2分钟内刚同步过，数据已是最新"

    start = time.monotonic()
    try:
        from services.kuaimai.erp_sync_service import ErpSyncService
        svc = ErpSyncService(db)
        await asyncio.wait_for(svc.sync(sync_type), timeout=120)
        elapsed = time.monotonic() - start
        st = svc._get_sync_state(sync_type)
        total = st.get("total_synced", 0) if st else 0
        return f"✓ {sync_type} 同步完成（耗时 {elapsed:.1f}s，累计 {total} 条）"
    except asyncio.TimeoutError:
        return f"⏱ {sync_type} 同步超时（>120s），后台 Worker 会继续同步"
    except Exception as e:
        return f"✗ {sync_type} 同步失败: {e}"
```

---

### 10. 开发任务拆分

#### 阶段1：数据库索引（前置）
- [ ] 任务1.1：编写迁移 `031_erp_local_query_indexes.sql`（3个新索引）
- [ ] 任务1.2：执行迁移验证索引生效

#### 阶段2：local_doc_query 实现
- [ ] 任务2.1：新建 `erp_local_doc_query.py`，实现多维度查询 + 格式化
- [ ] 任务2.2：`erp_local_tools.py` 添加 local_doc_query 工具定义 + Schema
- [ ] 任务2.3：`tool_executor.py` 添加 local_doc_query dispatch

#### 阶段3：local_global_stats 实现
- [ ] 任务3.1：新建 `erp_local_global_stats.py`，实现全局统计/排名/分组
- [ ] 任务3.2：`erp_local_tools.py` 添加 local_global_stats 工具定义 + Schema
- [ ] 任务3.3：`tool_executor.py` 添加 local_global_stats dispatch

#### 阶段4：local_product_identify 增强
- [ ] 任务4.1：`erp_local_identify.py` 的 `_identify_by_code` 增加 API 兜底逻辑
- [ ] 任务4.2：新增 `_upsert_product_from_api` 辅助函数（API数据写入本地）

#### 阶段5：trigger_erp_sync 实现
- [ ] 任务5.1：`tool_executor.py` 添加 `_trigger_erp_sync` 方法（含新鲜度检查+超时保护）
- [ ] 任务5.2：`erp_local_tools.py` 添加 trigger_erp_sync 工具定义 + Schema

#### 阶段6：路由提示词
- [ ] 任务6.1：`erp_local_tools.py` 重写 `LOCAL_ROUTING_PROMPT`

#### 阶段7：测试
- [ ] 任务7.1：local_doc_query 单元测试（各维度入口 + 组合查询 + 空结果）
- [ ] 任务7.2：local_global_stats 单元测试（统计/排名/分组）
- [ ] 任务7.3：local_product_identify API 兜底测试
- [ ] 任务7.4：trigger_erp_sync 测试（正常/超时/新鲜度跳过）
- [ ] 任务7.5：运行全量现有测试确保无回归

---

### 11. 依赖变更

无需新增依赖。

---

### 12. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| local_doc_query 查询范围过大（无条件查全表） | 中 | 至少传一个查询维度 + LIMIT 50 + days 默认30 |
| local_global_stats 数据量大聚合慢 | 中 | LIMIT 5000 行 + 有索引支撑 |
| identify API 兜底增加延迟 | 低 | 仅在本地4步全不命中时触发（<5%场景）|
| 新索引影响写入性能 | 极低 | 条件索引(WHERE NOT NULL)，只索引有值的行 |
| trigger_erp_sync 与 Worker 并发 | 低 | 新鲜度检查(2分钟) + upsert 幂等 |
| 工具数量增加（8→11）AI 选择困难 | 低 | 路由提示词明确指导输入类型→工具映射 |
| 模糊搜索（ILIKE）性能 | 低 | 已有 pg_trgm 扩展 + LIMIT 保护 |

---

### 13. 设计自检

- [x] 连锁修改已全部纳入任务拆分（6个文件 + 3个新文件）
- [x] 13 个边界场景均有处理策略
- [x] 新文件预估：erp_local_doc_query.py ~200行，erp_local_global_stats.py ~150行，均不超标
- [x] 无新增依赖
- [x] 现有 8 个本地工具全部保留，向后兼容
- [x] 12 个典型场景调用链对比验证
- [x] API 能力矩阵全量分析（89个API，10个注册表）
- [x] 已确认哪些 API 支持单条查（product/stock/platform_map），哪些只能增量（6个单据类型）

---

### 14. 文档更新清单

- [ ] FUNCTION_INDEX.md — 新增 3 个函数
- [ ] PROJECT_OVERVIEW.md — erp_local_doc_query.py / erp_local_global_stats.py
- [ ] TECH_ERP数据本地索引系统.md — 补充多维度查询章节

---

**确认后进入开发（`/everydayai-implementation`）**

请指定开发任务或按阶段顺序执行：
- 阶段1：数据库索引
- 阶段2：local_doc_query
- 阶段3：local_global_stats
- 阶段4：identify 增强
- 阶段5：trigger_erp_sync
- 阶段6：路由提示词
- 阶段7：测试
