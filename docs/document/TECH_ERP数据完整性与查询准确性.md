# ERP 数据完整性与查询准确性
**版本 2.0 · 2026-04-18**（经五轮方案审查修正）

---

## 1. 问题总览

### 1.1 数据准确性问题

用户问"今天多少付款订单"，系统返回 9274 笔，但其中 34.7% 是无效数据：

| 类别 | 数量 | 占比 | 是否应计入"付款订单" |
|------|------|------|---------------------|
| 正常订单 | 6,057 | 65.3% | ✅ |
| 空包/刷单(type含10) | 2,539 | 27.4% | ❌ |
| 已关闭/取消 | 614 | 6.6% | ❌ |
| 补发(type含14) | 66 | 0.7% | ❌ |

### 1.2 数据完整性问题

快麦 API 返回的订单有 80+ 个字段，我们只同步了约 35 个。缺失的关键字段：

| 字段 | 影响 |
|------|------|
| `tradeTags`（订单标签列表） | 无法精确识别刷单（只能靠 order_type=10 猜测） |
| `exceptions`（异常状态列表） | 无法区分异常类型（缺货/风控/地址/刷单） |
| `scalping`（刷单标记） | 丢失快麦系统级刷单检测结果 |
| `totalFee`（原价） | 无法计算优惠力度 |
| `weight`（重量） | 无法分析物流成本 |
| 等 57 个字段 | 详见 §3 |

### 1.3 参数提示问题

用户查询参数不足时（如"查库存"没说哪个商品），validate_params 返回技术性错误信息，主 Agent 不知道该问用户还是自己重试。

---

## 2. 目标

1. **订单统计分类展示**——全量查出，按规则分类展示（有效/刷单/已关闭/补发），下游计算默认用有效数据
2. **同步完整字段**——订单 +57 字段，售后 +36 字段
3. **标签同步**——tradeTags 随订单增量同步落库（实时），erp_tags 映射表 12h 同步标签名
4. **参数提示友好化**——validate_params 返回用户可理解的提示（一期走文本转达，二期做 DAG 暂停/恢复）
5. **PlanBuilder 提取 product_code/order_no/include_invalid**——补全参数链路

---

## 3. 数据库设计

### 3.1 订单表扩展（erp_document_items）

#### 3.1.1 ���增列——订单头级别（37列）

所有列加到 hot 表和 archive 表。迁移策略：ALTER TABLE ADD COLUMN IF NOT EXISTS。

迁移前置检查（防止列类型冲突）：
```sql
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
     WHERE table_name='erp_document_items' AND column_name='trade_tags'
     AND data_type != 'jsonb')
  THEN RAISE EXCEPTION 'trade_tags column exists with wrong type';
  END IF;
END $$;
```

```sql
-- ═══ 标签/异常/刷单（最关键的3个字段）═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS trade_tags JSONB;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS exception_tags TEXT[];
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_scalping SMALLINT DEFAULT 0;

-- ═══ 金额/费用 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS total_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS ac_payment NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS actual_post_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS theory_post_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sale_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sale_price NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS packma_cost NUMERIC(12,2);

-- ═══ 状态/标记 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS unified_status VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS stock_status VARCHAR(16);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_handler_memo SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_handler_message SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_package SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_presell SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS seller_flag SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS belong_type SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS convert_type SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS express_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS deliver_status SMALLINT;

-- ═══ 时间 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS audit_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS timeout_action_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS deliver_print_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS express_print_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS pt_consign_time TIMESTAMPTZ;

-- ═══ 物流/仓储 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS weight NUMERIC(10,3);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS net_weight NUMERIC(10,3);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS volume NUMERIC(10,4);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS template_name VARCHAR(64);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS warehouse_id INTEGER;

-- ═══ 拆单/合并 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS split_sid VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS split_type SMALLINT;

-- ═══ 统计字段 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_num INTEGER;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_kind_num INTEGER;

-- ═══ 其他 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS receiver_street VARCHAR(128);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS trade_invoice JSONB;
```

#### 3.1.2 新增��——订单子项级别（20列）

```sql
-- ═══ 优惠/金额 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_discount_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_discount_rate NUMERIC(5,4);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_ac_payment NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_total_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS divide_order_fee NUMERIC(12,2);

-- ═══ 商品信息 ��══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sku_properties_name TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sys_title VARCHAR(256);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sys_sku_properties_name TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS pic_path TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sys_pic_path TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS suits JSONB;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS order_ext JSONB;

-- ═══ 数量/状态 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS gift_num INTEGER DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS stock_num INTEGER;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_net_weight NUMERIC(10,3);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS insufficient_canceled SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_is_cancel SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_is_presell SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_virtual SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS estimate_con_time TIMESTAMPTZ;
```

#### 3.1.3 新增索引

```sql
-- 刷单过滤核心索引
CREATE INDEX IF NOT EXISTS idx_doc_items_scalping
    ON erp_document_items (is_scalping) WHERE is_scalping = 1;

-- 统一状态
CREATE INDEX IF NOT EXISTS idx_doc_items_unified_status
    ON erp_document_items (unified_status);

-- 注意：trade_tags 的 GIN 索引一期不加（一期只写不查）。
-- 二期开放标签查询时再加：
-- CREATE INDEX IF NOT EXISTS idx_doc_items_trade_tags
--     ON erp_document_items USING GIN (trade_tags);
```

### 3.2 售后表扩展（erp_document_items，doc_type='aftersale'）

#### 3.2.1 新增列——售后头级别（22列）

```sql
-- ═══ 关联/标识 ═���═
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS order_sid VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS order_type_ref SMALLINT;
-- order_type_ref 冗余原因：售后关联原订单需要 JOIN 同表，198万行下性能差。
-- order_type 创建时确定不会变，冗余安全。

-- ═══ 买家信息 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(128);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS buyer_phone VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS wangwang_num VARCHAR(64);

-- ═══ 时间 ══���
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS apply_date TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS after_sale_app_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS platform_complete_time TIMESTAMPTZ;

-- ═══ 状态 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS online_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS online_status_text VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS platform_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS handler_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS handler_status_text VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS deal_result SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS advance_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS advance_status_text VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS dest_work_order_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS storage_progress SMALLINT;

-- ═══ 仓库/沟通 ═══
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refund_warehouse_id INTEGER;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS trade_warehouse_name VARCHAR(64);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS message_memos JSONB;
```

#### 3.2.2 新增列——售后子项级别（14列）

```sql
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_refund_money NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_raw_refund_money NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refundable_money NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS properties_name TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_pic_path TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS receive_goods_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_detail_id BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_snapshot_id BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS num_iid BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sku_id BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_gift SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_match SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS suite SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS suite_type SMALLINT DEFAULT 0;
```

### 3.3 RPC 白名单扩展

`erp_global_stats_query` 的 Filter DSL 白名单需要新增：

```sql
'trade_tags', 'exception_tags', 'is_scalping',
'unified_status', 'is_presell', 'order_type_ref',
'online_status', 'handler_status'
```

### 3.4 ���签映射表

> 已有实现：`erp_sync_config_handlers.py` sync_tag (L161-220) + `erp_tags` 表。
> 归在 CONFIG_TYPES，每 12 小时同步。不需要新建。

**标签同步时序说明**：
- **标签 ID（trade_tags JSONB）**：随订单增量同步，无延迟（HIGH_FREQ_TYPES，每轮 ~60s）
- **标签名映射（erp_tags 表）**：每 12h 同步一次，仅影响标签名的 UI 展示
- 标签改名不修改订单 modified 时间戳，老订单的嵌入 tagName 可能过时
- 展示 fallback：trade_tags 内嵌 tagName → erp_tags 表查名字 → 显示"标签#ID"

### 3.5 分类规则表（新建��

```sql
CREATE TABLE IF NOT EXISTS erp_classification_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    shop_id UUID DEFAULT NULL,  -- 预留：NULL=全局规则，非NULL=店铺级覆盖（二期）
    doc_type VARCHAR(32) NOT NULL DEFAULT 'order',
    rule_name VARCHAR(64) NOT NULL,
    rule_icon VARCHAR(8) DEFAULT '🔸',
    priority SMALLINT DEFAULT 0,     -- 数字小=优先匹配
    conditions JSONB NOT NULL,       -- 条件列表，内部 AND
    is_valid_order BOOLEAN DEFAULT FALSE,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_class_rules_org_doc
    ON erp_classification_rules (org_id, doc_type, enabled);
```

一期规则加载只查全局（`shop_id IS NULL`），二期做店铺级覆盖。

---

## 4. ���步层改造

### 4.1 订单同步（erp_sync_handlers.py `_build_order_rows`）

在现有 dict 基础上追加 57 个字段的映射。JSONB 字段加防御性校验：

```python
# JSONB 字段防御性写入
raw_tags = doc.get("tradeTags")
if raw_tags is not None and not isinstance(raw_tags, list):
    logger.warning(
        f"trade_tags unexpected type | "
        f"doc_id={doc.get('sid')} type={type(raw_tags).__name__}"
    )
    raw_tags = None

# 新增字段映射（订单头级别，所有子项共享）
"trade_tags": raw_tags,                                       # JSONB
"exception_tags": [str(e) for e in (doc.get("exceptions") or [])],  # TEXT[]
"is_scalping": doc.get("scalping", 0),
"total_fee": doc.get("totalFee"),
"ac_payment": doc.get("acPayment"),
"actual_post_fee": doc.get("actualPostFee"),
"theory_post_fee": doc.get("theoryPostFee"),
"unified_status": doc.get("unifiedStatus"),
"audit_time": _safe_ts(doc.get("auditTime")),
"timeout_action_time": _safe_ts(doc.get("timeoutActionTime")),
"weight": doc.get("weight"),
"net_weight": doc.get("netWeight"),
"volume": doc.get("volume"),
# ... 其余字段同理
```

**容错策略**：JSONB 解析异常时 log warning + 该字段存 None，不丢弃整条订单（核心字段完整）。

> **tradeTags 真实格式待确认**：Phase 2 开发时先用日志打印一条真实返回值，确认格式后调整。
> 预期格式：`[{"id": 12345, "tagName": "VIP客户", "type": 1}]`（tagName 可能缺失）。

子项级别：
```python
"item_discount_fee": item.get("discountFee"),
"item_discount_rate": item.get("discountRate"),
"sys_title": item.get("sysTitle"),
"suits": item.get("suits"),       # JSONB
"pic_path": item.get("picPath"),
# ... 其余字段同理
```

### 4.2 售后同步（erp_sync_handlers.py `_build_aftersale_rows`）

同理，在 `doc_base` dict 里追加 22 个头字段、在子项 dict 里追��� 14 个字段。

### 4.3 标签同步

> 已有实现，无需新建。sync_tag 在 `erp_sync_config_handlers.py` (L161-220)。
> 归在 CONFIG_TYPES，每 12 小时通过 scheduler 入队执行。upsert 天然幂等。

---

## 5. 查询层改造——订单分类引擎（多租户可配置）

### 5.1 设计原则

**全量查出，分类展示，下游默认用有效数据。**

```
用户："查今天付款订单数量"
系统：
  📊 今日付款订单统计

  总订单数：9,274 笔
  ├── ✅ 有效订单：6,057 笔 | ¥16,059.77
  ├── 🔸 空包/刷单：2,539 笔（27.4%）
  ├── 🔸 已关闭/取消：614 笔
  └── 🔸 补发单：66 笔

  结论：实际成交 6,057 笔，销售金额 ¥16,059.77
  （后续计算请默认使用有效订单数据）
```

用户主动说"包含全部/不排除刷单"时，走全量模式（通过 PlanBuilder 的 `include_invalid` 参数控制）。

### 5.2 分类模型

**互斥分类**：每个订单只属于一个分类，所有分类数量之和 = 总数。

**排除优先 + 有效兜底**：先匹配排除规则（刷单/补发/已关闭），剩余全部归入有效订单。
有效订单不需要写条件——前面的排除规则没命中的，自动归入。

### 5.3 默认规则模板

```python
# config/default_classification_rules.py

DEFAULT_ORDER_RULES = [
    {
        "rule_name": "空包/刷单",
        "rule_icon": "🔸",
        "priority": 10,       # 最优先排除
        "conditions": [{"field": "order_type", "op": "list_has", "value": [10]}],
    },
    {
        "rule_name": "空包/刷单",
        "rule_icon": "🔸",
        "priority": 10,       # 同名同优先级 = OR 语义
        "conditions": [{"field": "is_scalping", "op": "eq", "value": 1}],
        # is_scalping 规则供其他租户自定义用。
        # 蓝创业务：order_type 含 10 = 100% 刷单，is_scalping 只是二次确认。
    },
    {
        "rule_name": "补发单",
        "rule_icon": "🔸",
        "priority": 20,
        "conditions": [{"field": "order_type", "op": "list_has", "value": [14]}],
    },
    {
        "rule_name": "已关闭/取消",
        "rule_icon": "🔸",
        "priority": 30,
        "conditions": [
            {"field": "order_status", "op": "in", "value": ["CLOSED", "CANCEL"]},
        ],
    },
    {
        "rule_name": "有效订单",
        "rule_icon": "✅",
        "is_valid_order": True,
        "priority": 99,        # 兜底：所有排除规则都没命中 → 有效
        "conditions": [],      # 空条件 = 永远匹配
    },
]
```

**priority 含义**：数字小 = 优先匹配。规则按 `priority ASC, created_at ASC` 加载。
**同名规则 = OR 语义**：两条"空包/刷单"规则（priority=10），匹配任一条都归入"空包/刷单"。

> 默认规则基于蓝创业务实践，其他租户可通过 erp_classification_rules 表自定��。

### 5.4 分类引擎

```python
# services/rule_engine/order_classifier.py（~150��）

class OrderClassifier:
    """订单分类引擎。

    - 互斥分类：每个订单只属于一个分类
    - 排除优先：先匹配排除规则，剩余归入有效
    - 内存缓存 5 分钟 TTL（单进程异步架构，无多 worker 一致性问题）
    - 懒加载：第一次查询时自动写入默认规则
    """
    _cache: dict[str, tuple[list, float]] = {}
    CACHE_TTL = 300

    @classmethod
    async def for_org(cls, db, org_id: str) -> "OrderClassifier":
        cached = cls._cache.get(org_id)
        if cached and time.time() < cached[1]:
            return cls(cached[0])

        rules = await db.table("erp_classification_rules") \
            .select("*").eq("org_id", org_id) \
            .eq("doc_type", "order").eq("enabled", True) \
            .is_("shop_id", "null") \
            .order("priority").order("created_at") \
            .execute()

        if not rules.data:
            await cls._init_default_rules(db, org_id)
            rules = await db.table("erp_classification_rules") \
                .select("*").eq("org_id", org_id) \
                .eq("doc_type", "order").eq("enabled", True) \
                .is_("shop_id", "null") \
                .order("priority").order("created_at") \
                .execute()

        cls._cache[org_id] = (rules.data, time.time() + cls.CACHE_TTL)
        return cls(rules.data)

    def classify(self, rows: list[dict]) -> ClassificationResult:
        """对 RPC 返回的分组数据做分类汇总。

        每个 row = {"order_type": "2,3,10,0", "order_status": "...",
                    "doc_count": N, "total_qty": N, "total_amount": N}
        """
        categories: dict[str, dict] = {}
        total = {"doc_count": 0, "total_qty": 0, "total_amount": 0}

        for row in rows:
            total["doc_count"] += row["doc_count"]
            total["total_qty"] += row["total_qty"]
            total["total_amount"] += row["total_amount"]

            matched_name = None
            for rule in self.rules:
                if self._match_all_conditions(row, rule["conditions"]):
                    matched_name = rule["rule_name"]
                    break

            # 有效订单规则 conditions=[] 永远匹配，此处不会为 None。
            # 但保留防御：如果所有规则都禁用了，fallback 到"有效订单"。
            if not matched_name:
                matched_name = "有效订单"

            cat = categories.setdefault(matched_name, {
                "doc_count": 0, "total_qty": 0, "total_amount": 0,
            })
            cat["doc_count"] += row["doc_count"]
            cat["total_qty"] += row["total_qty"]
            cat["total_amount"] += row["total_amount"]

        # 未知 order_type 监控（防御新类型静默归入有效）
        known_types = {"0","2","3","7","8","10","14","33","99"}
        seen_types: set[str] = set()
        for row in rows:
            parts = [x.strip() for x in (row.get("order_type") or "").split(",")]
            seen_types.update(p for p in parts if p)
        unknown = seen_types - known_types
        if unknown:
            logger.warning(f"未知 order_type 出现: {unknown}，请检查是否需要新增排除规则")

        return ClassificationResult(total=total, categories=categories, ...)

    @classmethod
    def invalidate_cache(cls, org_id: str | None = None):
        """手动清缓存（管理员改规则后调用）。"""
        if org_id:
            cls._cache.pop(org_id, None)
        else:
            cls._cache.clear()

    @staticmethod
    def _match_all_conditions(row: dict, conditions: list[dict]) -> bool:
        """条件列表内部 AND。空条件 = 永远匹配（兜底规则）。"""
        if not conditions:
            return True
        return all(
            OrderClassifier._match_condition(row, c)
            for c in conditions
        )

    @staticmethod
    def _match_condition(row: dict, cond: dict) -> bool:
        value = row.get(cond["field"])
        op = cond["op"]
        target = cond["value"]

        # NULL 处理：正向匹配→False，反向匹配→True
        if value is None:
            return op.startswith("not_") or op == "ne"

        if op == "list_has":
            # order_type 是逗号分隔字符串，如 "2,3,10,0"
            # list_has 检查列表中是否存在目标值（非子串匹配）
            parts = [x.strip() for x in str(value).split(",")]
            return any(str(t) in parts for t in target)
        elif op == "list_not_has":
            parts = [x.strip() for x in str(value).split(",")]
            return not any(str(t) in parts for t in target)
        elif op == "in":
            return value in target
        elif op == "not_in":
            return value not in target
        elif op == "eq":
            return value == target
        elif op == "ne":
            return value != target
        return False
```

### 5.5 RPC 改造——按 order_type + order_status 分组

```sql
CREATE OR REPLACE FUNCTION erp_order_stats_grouped(
    p_org_id UUID,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
    p_time_col VARCHAR DEFAULT 'pay_time',
    p_filters JSONB DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    base_q TEXT;
    result JSONB;
BEGIN
    base_q := format(
        'SELECT order_type, order_status, '
        'COUNT(DISTINCT doc_id) AS doc_count, '
        'SUM(quantity) AS total_qty, '
        'SUM(amount) AS total_amount '
        'FROM erp_document_items '
        'WHERE doc_type = ''order'' '
        'AND org_id = %L '
        'AND %I BETWEEN %L AND %L ',
        p_org_id, p_time_col, p_start, p_end
    );

    -- p_filters 复用 erp_global_stats_query 的 Filter DSL 解析逻辑
    IF p_filters IS NOT NULL THEN
        -- （复用现有 _apply_filters 逻辑，此处省略）
        NULL;
    END IF;

    base_q := base_q || ' GROUP BY order_type, order_status';

    EXECUTE 'SELECT jsonb_agg(row_to_json(t)) FROM (' || base_q || ') t'
    INTO result;

    RETURN COALESCE(result, '[]'::jsonb);
END;
$$;
```

**表结构说明**：erp_document_items 是订单-商品宽表（1 个订单 = N 行，每行一个子项）。
同一 doc_id 的所有行共享相同的 order_type/order_status（订单头级别字段）。
- `COUNT(DISTINCT doc_id)` = 订单数（去重）
- `SUM(amount)` = 所有子项实付金额之和 = 订单总额（优惠已按比例分摊到子项）

**RPC 只做原始分组，分类逻辑在应用层。** 原因：分类规则是多租户可配置的，不能写死在 SQL 里。

### 5.6 接入 UnifiedQueryEngine（ToolOutput 协议）

规则引擎注入在 `_summary` 内部，通过 `_post_process` 钩子隔离。
上层（TradeAgent / DAGExecutor）完全不感知规则引擎的存在。

```python
# erp_unified_query.py

class UnifiedQueryEngine:

    async def _summary(self, doc_type, filters, tr, group_by, request_ctx,
                       include_invalid=False):
        if doc_type == "order" and not include_invalid:
            # 分组查询 + 分类
            raw_rows = await self._query_grouped(doc_type, filters, tr)
            result = await self._post_process(doc_type, raw_rows, self.org_id)
            if result:
                return ToolOutput(
                    summary=result.to_display_text(),
                    format=OutputFormat.TABLE,
                    source="trade",
                    data=[{
                        "total": result.total,
                        "valid": result.valid,
                        "categories": result.categories_list,
                    }],
                    metadata={
                        "recommended_key": "valid",
                        "doc_type": "order",
                        "time_range": str(tr),
                    },
                )

        # include_invalid=True 或非 order doc_type → 原有逻辑
        return await self._summary_original(doc_type, filters, tr, group_by, request_ctx)
```

**ToolOutput 数据协议（下游消费规范）**：

```python
ToolOutput(
    summary="📊 今日付款订单统计\n...（树形展示）\n（后续计算请默认使用有��订单数据）",
    data=[{
        "total":      {"doc_count": 9274, "total_qty": 15000, "total_amount": 25000.00},
        "valid":      {"doc_count": 6057, "total_qty": 9800,  "total_amount": 16059.77},
        "categories": [
            {"name": "空包/刷单",   "doc_count": 2539, "total_amount": ...},
            {"name": "已关闭/取消", "doc_count": 614,  "total_amount": ...},
            {"name": "补发单",      "doc_count": 66,   "total_amount": ...},
        ],
    }],
    metadata={"recommended_key": "valid", "doc_type": "order"},
)
```

**三层推荐保障**：
1. summary 文本末尾："后续计算请默认使用有效订单数据"（LLM 自然语言指令）
2. data 结构：`valid` 键名自描述
3. metadata：`recommended_key: valid`（写入 [DATA_REF] 标签，ComputeAgent 可见）

ComputeAgent 需要算占比时，total 和 valid 都在 data 里可取。

### 5.7 RPC 白名单扩展

Filter DSL 新增 `not_like` 操作符：

```sql
WHEN 'not_like' THEN
    base_q := base_q || format(' AND %I NOT ILIKE %L', field_name, val_text);
```

白名单新增：`is_scalping`, `unified_status`, `is_presell`, `online_status`, `handler_status`

> trade_tags / exception_tags 的查询操作符（jsonb_contains / array_overlap）二期再加。
> 一期这两个字段只写不查。

---

## 6. Agent 参数链路补全

### 6.1 PlanBuilder prompt 增加参数提取

```python
# plan_builder.py build_plan_prompt 里新增：
"- product_code: 商品编码（如用户提到了具体编码则提取）\n"
"- order_no: 订单号（如用户提到了则提取）\n"
"- include_invalid: 布尔值，默认 false。仅当用户明确要求"包含全部/不排除刷单"时设为 true。\n"
"  注意：用户问"刷单有多少"不是 include_invalid，而是用 filters 过滤刷单类型。\n"
```

### 6.2 validate_params 友好提示

**一期方案**：validate_params 返回的 prompt 通过 ToolOutput.summary 文本转达给主 Agent，DAG 终止。
用户回答后重新发起查询。

**二期方案**：NEED_INPUT 状态 + DAG 暂停/恢复机制（参数缺失通常在 DAG 第一步，一期无浪费）。

```python
@dataclass(frozen=True)
class ValidationResult:
    status: ValidationStatus
    fields: tuple = ()
    message: str = ""
    prompt: str = ""   # ← 新增：引导主Agent转达给用户的话术
```

各部门 Agent 的 validate_params 返回用户友好的 prompt：

```python
# warehouse_agent.py
if not params.get("product_code") and not params.get("keyword"):
    return ValidationResult.missing(
        ["商品编码或关键词"],
        prompt="您想查哪个商品的库存？请告诉我商品编��或名称。",
    )
```

---

## 7. 开发任务拆分

### Phase 1：数据库迁移
- [ ] 1.1：迁移脚本 081_expand_order_aftersale_fields.sql（前置类型检查 + hot/archive 表加 93 列 + 索引）
- [ ] 1.2：迁移脚本 082_classification_rules.sql（分类规则表，含 shop_id 预留列）
- [ ] 1.3：迁移脚本 083_rpc_grouped_stats.sql（分组统计 RPC + not_like 操作符 + 白名单扩展）
- [ ] 1.4：在服务器执行迁移

### Phase 2：同步层改造
- [ ] 2.1：日志打印 tradeTags/exceptions 真实格式样例，确认后继续
- [ ] 2.2：_build_order_rows 追加 57 字段映射（含 JSONB 防御性校验）
- [ ] 2.3：_build_aftersale_rows 追加 36 字段映射
- [ ] 2.4：跑测试，确认同步不报错

### Phase 3：订单分类引擎
- [ ] 3.1：新建 config/default_classification_rules.py（排除优先+有效兜底规则模板）
- [ ] 3.2：新建 services/rule_engine/order_classifier.py（分类引擎 + 缓存 + 懒加载 + NULL ���理）
- [ ] 3.3：erp_unified_query.py _summary 接入分类引擎（ToolOutput 三层推荐协议）
- [ ] 3.4：RPC 加 not_like 操作符 + 白名单扩展
- [ ] 3.5：单元测试

### Phase 4：参数提示
- [ ] 4.1：PlanBuilder prompt 增加 product_code/order_no/include_invalid
- [ ] 4.2：ValidationResult 加 prompt 字段
- [ ] 4.3：4 个部门 Agent validate_params 加友好提示

### Phase 5：测试 + 部署
- [ ] 5.1：全量测试
- [ ] 5.2：部署 + 触发一次增量同步验证新字段
- [ ] 5.3：执行数据质量检查脚本（§11.2）
- [ ] 5.4：执行性能基准测试（§11.1）
- [ ] 5.5：线上验证"查今天付款订单数量"结果准确性

---

## 8. ��署与回滚策略

### 8.1 数据库迁移

**可逆性**：全部是 ADD COLUMN IF NOT EXISTS，回滚时不需要删列（新列为 NULL 不影响旧代码）。

**部署顺序**：
1. 先执行迁移脚本（加列 + 加 RPC 操作符）
2. 再部署代码（同步写新列 + 查询用新列）
3. 等待一轮同步完成（~1分钟），新数据自动填充新字段

**回滚步骤**：
1. git revert 代码 → 重新部署（旧代码忽略新列）
2. 不需要删列（新列留着不影响）

### 8.2 历史数据回填

迁移脚本执行后，历史订单的新字段为 NULL。回填策略：

- **回填必须通过 scheduler 入队**，禁止直接调用 sync_order 绕过队列（避免与增量同步冲突）
- 订单表：按 pay_time 分天批量入队最近 90 天数据（~27万行）
- 售后表：按 modified 分天入队最近 90 天数据
- 或者接受历史数据为 NULL，只有新同步的数据有完整字段

**archive 表**：新字段全 NULL。分类引擎处理 NULL 时走兜底逻辑（归入有效订单——因为排除规则不会被 NULL 命中）。

### 8.3 架构约束

> **同步模式锁定**：erp_document_items 的同步模式锁定为 delete+insert（按 doc_id 事务）。
> 新增的 93 列使用 DEFAULT 0/NULL，在 delete+insert 下无锁表风险。
> 如果未来改为 ON CONFLICT DO UPDATE（upsert），必须重新评估 DEFAULT 值的行级重写影响。
> 此约束源自 v2.0 方案评审（2026-04-18），变更需单独设计评审。

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 加 93 列后表膨胀 | 中 | 大部分列是 NULL（TOAST 压缩），实际存储增量 <20% |
| 同步写入变慢 | 低 | 列数增加但单行 INSERT 性能影响可忽略；一期不加 GIN ���引 |
| RPC 白名单��漏 | 中 | 上线前用 Filter DSL 测试每个新字段 |
| tradeTags 格式不符预期 | 中 | Phase 2 先打日志确认格式再写入逻辑 |
| 全量回填耗时 | 中 | 通过 scheduler 分天批量入队，避免 429 限流 |
| 分类规则缓存 5 分钟延迟 | 低 | 规则几乎不变；单进程架构无多 worker 不一致；预留 invalidate_cache() 二期 UI 调用 |
| 新 order_type 静默归入有效 | 中 | logger.warning 监控未知类型（set 去重，单次调用仅一条日志）；部署后配置 Sentry 告警关键词 `未知 order_type` |

---

## 10. 设计自检

- [x] 项目上下文已加载（架构/可复用/约束/冲突）
- [x] 迁移模式与现有一致（ALTER TABLE + IF NOT EXISTS + hot/archive 双表）
- [x] 同步改造与现有模式一致（dict 追加字段，事务性 delete+insert）
- [x] RPC 改造向后兼容（新操作符 + 白名单扩展）
- [x] 分类引擎注入在 _post_process 钩子，上层无感知
- [x] ToolOutput 三层推荐保障（summary/data/metadata）
- [x] 所有新增文件预估 ≤500 行
- [x] GIN 索引延迟到二期（一期只写不查）
- [x] 排除优先 + 有效兜底的规则设计（不会遗漏新类���）
- [x] NEED_INPUT 标记为二期（一期靠文本转达）

---

## 11. 性能基准与数据���量

### 11.1 性能基准（Phase 5 部署后执行）

| 操作 | 当前基线 | 迁移后目标 | SLA |
|-----|---------|-----------|-----|
| RPC erp_global_stats_query（单日） | ~50ms | <100ms | <200ms P95 |
| 新 RPC erp_order_stats_grouped | N/A | <150ms | <300ms P95 |
| OrderClassifier.classify（30 组 × 5 规则） | N/A | <1ms | <5ms |
| 单条订单同步（+57 字段） | ~8ms | <15ms | <30ms |
| erp_document_items 表大小 | ~2.1GB | <2.6GB | +25% 以内 |

**测试方法**：执行 20 次取第 19 个值（P95），单线程（单进程异步架构，并发=1）。
**超标应急**：RPC >300ms → 检查执行计划加索引；INSERT >50ms → 检查是否有未预期的索引拖慢。

### 11.2 数据质量检查脚本（Phase 5 部署后执行一次）

```sql
-- 1. 同一 doc_id 不应出现��个 order_type（数据完整性）
SELECT doc_id, COUNT(DISTINCT order_type) AS type_cnt
FROM erp_document_items WHERE doc_type='order'
GROUP BY doc_id HAVING COUNT(DISTINCT order_type) > 1;
-- 预期：0 行

-- 2. 同一 doc_id 不应出现多个 order_status
SELECT doc_id, COUNT(DISTINCT order_status) AS status_cnt
FROM erp_document_items WHERE doc_type='order'
GROUP BY doc_id HAVING COUNT(DISTINCT order_status) > 1;
-- 预期：0 行

-- 3. SUM(amount) per doc_id 应等于 pay_amount（如有）
SELECT doc_id, SUM(amount) AS sum_items, MAX(pay_amount) AS header_amount
FROM erp_document_items WHERE doc_type='order' AND pay_amount IS NOT NULL
GROUP BY doc_id HAVING ABS(SUM(amount) - MAX(pay_amount)) > 0.02;
-- 预期：0 行（允许 0.02 分摊尾差）
```

---

## 12. 审查修订记录

| 版本 | 日期 | 修改内容 |
|-----|------|---------|
| 1.0 | 2026-04-17 | 初版 |
| 2.0 | 2026-04-18 | 五轮方案审查修正：排除优先规则设计、ToolOutput 三层推荐协议、RPC 完整 SQL、GIN 索引延迟二期、NEED_INPUT 标记二期、性能基准/数据质量检查、order_type split 精确匹配、NULL 处理逻辑、JSONB 防御性校验、回填必须走 scheduler |
| 2.1 | 2026-04-18 | 五人评审后修正：操作符 contains→list_has / not_contains→list_not_has、删除"其他/未识别"分类改为未知 order_type 监控日志、新增 invalidate_cache 手动清缓存、§8.3 架构约束（同步模式锁定 delete+insert）、DEFAULT 0 保持不变 |
| 3.0 | 2026-04-18 | 方案评审结论：Filter DSL 保持两份代码+注释同步（FILTER_WHITELIST_SYNC）；Phase 4.1 增加 PlanBuilder 语义区分测试；154列宽表标注为受限于 delete+insert 架构的妥协方案 |
