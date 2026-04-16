# 技术设计：统一查询引擎（Filter DSL）

> 版本：V1.0 | 日期：2026-04-15  
> 前置：需求分析（已确认）| Qwen 3.5-plus Filter DSL 准确率测试 15/15 通过

---

## 1. 现有代码分析

### 已阅读文件

| 文件 | 关键理解 |
|------|---------|
| `services/kuaimai/erp_local_query.py` (704行) | 6个工具共享 `query_doc_items()` 查 erp_document_items，各自格式化不同 |
| `services/kuaimai/erp_local_doc_query.py` (195行) | 最灵活的查询工具，但要求至少传一个标识参数 |
| `services/kuaimai/erp_local_global_stats.py` (311行) | RPC 聚合，缺 status/product_code 过滤 |
| `services/kuaimai/erp_local_compare_stats.py` (331行) | 双时间段对比，调 RPC 两次 |
| `services/kuaimai/erp_local_db_export.py` (355行) | 最灵活的过滤参数（含 status），但定位为"导出"不是"查询" |
| `services/kuaimai/erp_local_helpers.py` (101行) | `query_doc_items()` + `check_sync_health()` + `cutoff_iso()` |
| `services/agent/erp_tool_executor.py` | `_local_dispatch()` 路由到15个本地工具，`_TIME_AWARE_TOOLS` 集合 |
| `services/agent/erp_agent.py` | `_prepare_tools()` 可见性过滤：`local_*` 前缀全部可见 |
| `services/agent/tool_loop_executor.py` | 工具调用链：JSON解析 → validate_tool_args → execute → wrap结果 |
| `services/agent/tool_args_validator.py` | 类型纠偏：string→object/int/bool，需增加 array 处理 |
| `config/erp_local_tools.py` | 14个工具定义（OpenAI格式），ERP_LOCAL_TOOLS 集合 |
| `config/erp_tools.py` | ERP_ROUTING_PROMPT ~100行路由规则 |
| `config/tool_registry.py` | ToolEntry 注册，priority=1 本地，tags 语义路由 |
| `config/tool_domains.py` | 所有 local_* 归属 ERP 域 |
| `migrations/041_global_stats_time_type.sql` | RPC `erp_global_stats_query` 完整实现 |

### 可复用模块

- `query_doc_items()` → 热+冷表 UNION、去重逻辑，直接复用为统一引擎的 detail 查询核心
- `check_sync_health()` → 所有模式复用
- `RequestContext` / `make_n_days_header()` / `format_time_header()` → 时间事实层复用
- `_mask_pii()` / `_parse_columns()` → export 模式复用
- RPC `erp_global_stats_query` → summary 模式复用（需升级加 p_filters）
- `resolve_staging_dir()` → export 模式复用

### 设计约束

- ERP Agent 大脑用 **qwen3.5-plus**，Filter DSL 准确率已验证 15/15
- 必须兼容 `tool_args_validator.py` 校验链（新增 array 类型纠偏）
- 必须支持 `RequestContext` 透传（时间事实层 SSOT）
- 必须兼容 `tool_result_envelope.py` 截断 + staging 分流
- 保持 `ToolLoopExecutor` 调用链不变（JSON解析 → 校验 → 执行 → hook）

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 7个工具合并为 `local_data` | `erp_tool_executor.py` | dispatch 字典删旧加新 |
| 7个工具定义删除 | `erp_local_tools.py` | 替换为 1 个 local_data 定义 |
| 工具注册删旧加新 | `tool_registry.py` | 7个 ToolEntry 替换为 1 个 |
| 域注册删旧加新 | `tool_domains.py` | 7个域映射替换为 1 个 |
| 路由提示词重写 | `erp_tools.py` | ERP_ROUTING_PROMPT 精简 |
| filters 数组类型纠偏 | `tool_args_validator.py` | 新增 array 类型处理 |
| local_compare 内部重构 | `erp_local_compare_stats.py` | 使用统一引擎 _summary() |
| _TIME_AWARE_TOOLS 更新 | `erp_tool_executor.py` | 删旧7个，加 local_data |
| RPC 升级 | 新迁移脚本 | 加 p_filters JSONB 参数 |
| ERP_LOCAL_TOOLS 集合 | `erp_local_tools.py` | 删旧7个，加 local_data |
| 旧工具测试迁移 | `tests/test_erp_local.py` | 删7个旧测试类，新增 local_data 测试 |
| 导出测试迁移 | `tests/test_erp_local_db_export.py` | 迁移为 export 模式测试 |
| Agent 测试断言 | `tests/test_erp_agent.py` | 工具名断言更新 |
| 工具列表断言 | `tests/test_chat_tools.py` | 工具数量/名称断言更新 |
| 并发安全列表 | `config/chat_tools.py` | 旧工具名替换为 local_data |
| 沙盒函数引用 | `services/sandbox/functions.py` | 检查旧工具名暴露 |
| 死代码清理 | `services/kuaimai/erp_local_helpers.py` | 删除 query_doc_items()（无调用方） |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| filters 为空数组 `[]` | 等价于无过滤，按 doc_type 全量查询（受 limit 和时间默认值约束） | UnifiedQueryEngine |
| filters 被千问双重序列化为 string | `tool_args_validator.py` 新增 array 类型 json.loads 还原 | tool_args_validator |
| field 名不在列白名单中 | 返回错误提示：列出可用字段，让模型重试 | UnifiedQueryEngine._validate_filters() |
| op 与列类型不兼容（如 text 列用 gt） | 返回错误提示：说明该列支持的 op | UnifiedQueryEngine._validate_filters() |
| value 类型与列类型不匹配（如 integer 列传 string） | 尝试自动转换（int()/float()），失败则返回错误 | UnifiedQueryEngine._validate_filters() |
| 查询结果为空 | 附加 sync_health 检查 + 提示 | 所有模式 |
| detail 模式结果超过 limit | 严格截断 + 提示"仅显示前N条" | _detail() |
| export 模式数据量超大（>10000行） | 硬限制 max_rows=10000 + 提示 | _export() |
| summary 模式 RPC 超时 | 30s 超时 → 返回错误让模型降级到 detail | _summary() |
| 热表+冷表 UNION（days>90） | 复用 query_doc_items() 的 UNION 逻辑 | _detail()/_export() |
| PII 字段（手机号/姓名） | 仅 export 模式脱敏，detail 模式不含敏感字段 | _export() |
| 并发查询（同一 org_id） | Supabase 连接池天然支持，无需额外处理 | DB 层 |
| in 操作符 value 为空数组 | 跳过该条件（等价于不过滤） | _build_where() |
| between 操作符 value 格式 | 要求 `[min, max]` 数组，否则返回错误 | _validate_filters() |
| 时间字段值无时区 | 自动追加 `+08:00`（CN_TZ） | _validate_filters() |

---

## 3. 技术栈

- 后端：Python 3.12 + FastAPI
- 数据库：Supabase (PostgreSQL 15) + RPC
- ORM：supabase-py client（链式查询）
- 导出：pandas + pyarrow (Parquet)
- LLM：qwen3.5-plus (DashScope API, function calling)
- 时间：utils.time_context (RequestContext/DateRange/CN_TZ)

---

## 4. 目录结构

### 新增文件

| 文件 | 职责 | 预估行数 |
|------|------|---------|
| `services/kuaimai/erp_unified_query.py` | 统一查询引擎核心（validate + build_where + summary/detail/export） | ~450行 |
| `migrations/080_unified_query_rpc.sql` | RPC 升级：加 p_filters JSONB 参数 | ~80行 |
| `tests/test_unified_query.py` | 统一查询引擎单测 | ~300行 |

### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `config/erp_local_tools.py` | 删7个工具定义，新增 local_data 定义；更新 ERP_LOCAL_TOOLS 集合 |
| `config/tool_registry.py` | 删7个 ToolEntry，新增 local_data ToolEntry（合并 tags） |
| `config/tool_domains.py` | 删7个域映射，新增 local_data → ERP |
| `config/erp_tools.py` | ERP_ROUTING_PROMPT 重写（~100行→~40行） |
| `services/agent/erp_tool_executor.py` | dispatch 字典更新 + _TIME_AWARE_TOOLS 更新 |
| `services/agent/tool_args_validator.py` | 新增 type="array" 的 json.loads 纠偏 |
| `services/kuaimai/erp_local_compare_stats.py` | 内部重构为调用统一引擎 _summary() |

### 删除文件

| 文件 | 行数 | 理由 |
|------|------|------|
| `services/kuaimai/erp_local_doc_query.py` | 195行 | 被 local_data detail 模式替代 |
| `services/kuaimai/erp_local_global_stats.py` | 311行 | 被 local_data summary 模式替代 |
| `services/kuaimai/erp_local_db_export.py` | 355行 | 被 local_data export 模式替代 |

### 精简文件

| 文件 | 改动 |
|------|------|
| `services/kuaimai/erp_local_query.py` | 删除 local_purchase_query/aftersale_query/order_query/product_flow（~470行），保留 stock_query/platform_map_query/shop_list/warehouse_list |

---

## 5. 数据库设计

### RPC 升级：erp_global_stats_query

现有签名不变（向后兼容），新增 `p_filters` 参数：

```sql
CREATE OR REPLACE FUNCTION erp_global_stats_query(
    -- 现有参数保留（向后兼容）
    p_doc_type VARCHAR,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
    p_time_col VARCHAR DEFAULT 'doc_created_at',
    p_shop VARCHAR DEFAULT NULL,
    p_platform VARCHAR DEFAULT NULL,
    p_supplier VARCHAR DEFAULT NULL,
    p_warehouse VARCHAR DEFAULT NULL,
    p_group_by VARCHAR DEFAULT NULL,
    p_limit INT DEFAULT 20,
    p_org_id UUID DEFAULT NULL,
    -- 新增：Filter DSL 参数
    p_filters JSONB DEFAULT NULL
) RETURNS JSONB
```

`p_filters` 格式：
```json
[
  {"field": "order_status", "op": "eq", "value": "SELLER_SEND_GOODS"},
  {"field": "amount", "op": "gt", "value": 500}
]
```

RPC 内部解析逻辑：
```sql
-- 遍历 p_filters，白名单校验 field，构建 WHERE
IF p_filters IS NOT NULL THEN
  FOR i IN 0..jsonb_array_length(p_filters)-1 LOOP
    f := p_filters->i;
    field_name := f->>'field';
    op := f->>'op';
    val := f->'value';
    -- 白名单校验 field_name（防 SQL 注入）
    -- 白名单包含所有 COLUMN_WHITELIST 中的非时间字段
    -- （时间字段已由 p_start/p_end/p_time_col 处理）
    IF field_name IN (
      'order_status','doc_status','outer_id','sku_outer_id',
      'order_no','express_no','buyer_nick','is_cancel',
      'is_refund','is_exception','aftersale_type',
      'refund_status','status_name','order_type',
      'shop_name','platform','supplier_name','warehouse_name',
      'amount','quantity','cost','pay_amount','post_fee',
      'discount_fee','gross_profit','refund_money',
      'item_name','express_company','doc_id','doc_code'
    ) THEN
      CASE op
        WHEN 'eq' THEN base_q := base_q || format(' AND %I = %L', field_name, val#>>'{}');
        WHEN 'ne' THEN base_q := base_q || format(' AND %I != %L', field_name, val#>>'{}');
        WHEN 'gt' THEN base_q := base_q || format(' AND %I > %L', field_name, val#>>'{}');
        WHEN 'gte' THEN base_q := base_q || format(' AND %I >= %L', field_name, val#>>'{}');
        WHEN 'lt' THEN base_q := base_q || format(' AND %I < %L', field_name, val#>>'{}');
        WHEN 'lte' THEN base_q := base_q || format(' AND %I <= %L', field_name, val#>>'{}');
        WHEN 'like' THEN base_q := base_q || format(' AND %I ILIKE %L', field_name, val#>>'{}');
        WHEN 'in' THEN -- val 是数组
          base_q := base_q || format(' AND %I IN (SELECT jsonb_array_elements_text(%L::jsonb))', field_name, val);
        WHEN 'is_null' THEN
          IF (val#>>'{}')::boolean THEN
            base_q := base_q || format(' AND %I IS NULL', field_name);
          ELSE
            base_q := base_q || format(' AND %I IS NOT NULL', field_name);
          END IF;
        ELSE NULL; -- 忽略未知 op
      END CASE;
    END IF;
  END LOOP;
END IF;
```

**索引**：现有索引已覆盖（doc_type+time, outer_id+time, order_status 等），无需新增。

---

## 6. 核心模块设计

### 6.1 UnifiedQueryEngine（`erp_unified_query.py`）

```python
class UnifiedQueryEngine:
    """统一查询引擎 — Filter DSL → 参数化 SQL"""

    # 启动时从 DB information_schema 加载（或硬编码白名单）
    COLUMN_WHITELIST: dict[str, ColumnMeta]  # {列名: {type, nullable}}
    
    # op 与列类型兼容表
    OP_COMPAT: dict[str, set[str]]  # {"text": {"eq","ne","like","in","is_null"}, ...}

    def __init__(self, db, org_id: str | None = None):
        ...

    async def execute(
        self, doc_type: str, mode: str, filters: list[dict],
        group_by: list[str] | None = None,
        sort_by: str | None = None,
        sort_dir: str = "desc",
        fields: list[str] | None = None,
        limit: int = 20,
        time_type: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        request_ctx: RequestContext | None = None,
    ) -> str:
        """统一入口"""
        validated = self._validate_filters(filters)
        time_range = self._extract_time_range(validated, time_type, request_ctx, mode)
        
        if mode == "summary":
            return await self._summary(doc_type, validated, time_range,
                                        group_by, request_ctx)
        elif mode == "detail":
            return await self._detail(doc_type, validated, time_range,
                                       fields, sort_by, sort_dir, limit, request_ctx)
        elif mode == "export":
            return await self._export(doc_type, validated, time_range,
                                       fields, limit, user_id, conversation_id, request_ctx)

    def _validate_filters(self, filters: list[dict]) -> list[ValidatedFilter]:
        """校验每个 filter 的 field/op/value 合法性"""
        # 1. field 在 COLUMN_WHITELIST 中
        # 2. op 与列类型兼容
        # 3. value 类型转换（string→int 等）
        # 4. 返回 ValidatedFilter 列表或抛出 ValueError

    def _extract_time_range(self, filters, time_type, request_ctx, mode="summary"):
        """从 filters 中提取时间范围，用于 RPC 的 p_start/p_end"""
        # 找到 time_col 相关的 gte/lt filters → 转为 start/end
        # 如果没有时间过滤 → 按 mode 默认：
        #   summary → 今天（与原 local_global_stats 一致）
        #   detail → 最近 30 天（与原 local_doc_query 一致）
        #   export → 今天（避免误导出全量）

    async def _summary(self, doc_type, filters, time_range, 
                        group_by, request_ctx) -> str:
        """调 RPC 返回聚合统计"""
        # 1. 构建 RPC 参数（p_doc_type, p_start, p_end, p_filters, p_group_by, ...）
        # 2. 调用 db.rpc("erp_global_stats_query", params)
        # 3. 格式化输出（时间头 + 统计数字 + 健康检查）

    async def _detail(self, doc_type, filters, time_range,
                       fields, sort_by, sort_dir, limit, request_ctx) -> str:
        """ORM 查询返回明细行"""
        # 1. 构建 Supabase 查询（select + where + order + limit）
        # 2. 热表查询 + 冷表 UNION（如果时间跨度 > 90天）
        # 3. 格式化输出（时间头 + 明细表 + 健康检查）

    async def _export(self, doc_type, filters, time_range,
                       fields, limit, user_id, conversation_id, request_ctx) -> str:
        """ORM 批量查询 + Parquet 写入 staging"""
        # 1. 无 fields → 返回字段文档（两步协议 Step 1）
        # 2. 有 fields → 批量查询 + PII 脱敏 + 写 Parquet
        # 3. 返回文件路径 + 元数据 + 预览

    def _build_orm_query(self, table, doc_type, filters, time_range, 
                          fields, sort_by, sort_dir, limit):
        """构建 Supabase ORM 链式查询"""
        # 遍历 filters → 链式 .eq() / .gt() / .ilike() / .in_() / .is_() 等
        # 虚拟字段展开：product_code → .or_(f"outer_id.eq.{v},sku_outer_id.eq.{v}")
        # group_by 列名→RPC 枚举映射（仅 summary 模式）：
        #   shop_name→shop, outer_id→product, platform→platform,
        #   supplier_name→supplier, warehouse_name→warehouse
        # V1 限制：group_by 仅取第一个元素（RPC 不支持多字段分组）

    # detail 模式默认字段（fields 为空时按 doc_type 选择）
    _DEFAULT_DETAIL_FIELDS = {
        "order": ["order_no", "shop_name", "platform", "order_status",
                  "outer_id", "item_name", "quantity", "amount",
                  "pay_time", "consign_time"],
        "purchase": ["doc_code", "supplier_name", "doc_status",
                     "outer_id", "item_name", "quantity",
                     "quantity_received", "amount", "doc_created_at"],
        "aftersale": ["doc_code", "aftersale_type", "refund_status",
                      "outer_id", "item_name", "quantity",
                      "refund_money", "doc_created_at"],
        "receipt": ["doc_code", "supplier_name", "doc_status",
                    "outer_id", "item_name", "quantity",
                    "quantity_received", "doc_created_at"],
        "shelf": ["doc_code", "warehouse_name", "doc_status",
                  "outer_id", "item_name", "quantity", "doc_created_at"],
        "purchase_return": ["doc_code", "supplier_name", "doc_status",
                            "outer_id", "item_name", "quantity",
                            "amount", "doc_created_at"],
    }

    def _format_detail_rows(self, rows, doc_type, fields) -> str:
        """明细行格式化为文本"""

    def _format_summary(self, data, doc_type, time_range, group_by) -> str:
        """聚合结果格式化为文本"""
```

### 6.2 列白名单（Schema 驱动）

```python
# 硬编码白名单（比启动时反射 information_schema 更安全、更可控）
# 新增列时：1. 写迁移加列 2. 在此加一行 3. 重启生效

@dataclass(frozen=True)
class ColumnMeta:
    col_type: str  # "text" | "integer" | "numeric" | "timestamp" | "boolean"
    filterable: bool = True

COLUMN_WHITELIST: dict[str, ColumnMeta] = {
    # 虚拟字段（后端展开为 OR 逻辑）
    "product_code": ColumnMeta("text"),  # → outer_id = X OR sku_outer_id = X
    # 单据基础
    "doc_type": ColumnMeta("text"),
    "doc_id": ColumnMeta("text"),
    "doc_code": ColumnMeta("text"),
    "doc_status": ColumnMeta("text"),
    "order_status": ColumnMeta("text"),
    "status_name": ColumnMeta("text"),
    # 时间
    "doc_created_at": ColumnMeta("timestamp"),
    "doc_modified_at": ColumnMeta("timestamp"),
    "pay_time": ColumnMeta("timestamp"),
    "consign_time": ColumnMeta("timestamp"),
    # 商品
    "outer_id": ColumnMeta("text"),
    "sku_outer_id": ColumnMeta("text"),
    "item_name": ColumnMeta("text"),
    # 数量金额
    "quantity": ColumnMeta("numeric"),
    "amount": ColumnMeta("numeric"),
    "cost": ColumnMeta("numeric"),
    "pay_amount": ColumnMeta("numeric"),
    "post_fee": ColumnMeta("numeric"),
    "discount_fee": ColumnMeta("numeric"),
    "gross_profit": ColumnMeta("numeric"),
    "refund_money": ColumnMeta("numeric"),
    # 关联方
    "shop_name": ColumnMeta("text"),
    "platform": ColumnMeta("text"),
    "supplier_name": ColumnMeta("text"),
    "warehouse_name": ColumnMeta("text"),
    # 订单物流
    "order_no": ColumnMeta("text"),
    "express_no": ColumnMeta("text"),
    "express_company": ColumnMeta("text"),
    "order_type": ColumnMeta("text"),
    # 买家
    "buyer_nick": ColumnMeta("text"),
    # 状态标记
    "is_cancel": ColumnMeta("integer"),
    "is_refund": ColumnMeta("integer"),
    "is_exception": ColumnMeta("integer"),
    "is_halt": ColumnMeta("integer"),
    "is_urgent": ColumnMeta("integer"),
    # 售后
    "aftersale_type": ColumnMeta("text"),
    "refund_status": ColumnMeta("text"),
}
```

### 6.3 tool_args_validator 增强

新增 `type: "array"` 处理（与现有 `type: "object"` 同级）：

```python
# array: LLM 双重序列化 → 尝试 json.loads 还原
if expected_type == "array" and isinstance(value, str):
    try:
        parsed = _json.loads(value)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        logger.warning(
            f"ToolArgsValidator type coerced | tool={tool_name} | "
            f"param={key} | string→array"
        )
        cleaned[key] = parsed
    else:
        return cleaned, (
            f"参数 `{key}` 类型错误：期望 array(list)，"
            f"收到 string。请传 JSON 数组"
        )
```

---

## 7. 工具定义

### local_data 工具 Schema

```python
_tool(
    "local_data",
    "本地数据库统一查询工具。支持查询/统计/导出 erp_document_items 表的所有单据数据。\n"
    "用 filters 数组指定过滤条件，任意字段组合均可。\n\n"
    "常用字段：\n"
    "- order_status/doc_status: 状态(WAIT_AUDIT/WAIT_SEND_GOODS/SELLER_SEND_GOODS/FINISHED/CLOSED)\n"
    "- consign_time: 发货时间  |  pay_time: 付款时间  |  doc_created_at: 创建时间\n"
    "- shop_name: 店铺名  |  platform: 平台(tb/jd/pdd/fxg/kuaishou/xhs/1688)\n"
    "- product_code: 商品编码（自动匹配主编码+SKU编码）\n"
    "- outer_id: 商品主编码（精确）  |  sku_outer_id: SKU编码（精确）\n"
    "- order_no: 平台订单号  |  express_no: 快递单号\n"
    "- supplier_name: 供应商  |  warehouse_name: 仓库\n"
    "- amount: 金额  |  quantity: 数量  |  refund_money: 退款金额\n"
    "- is_refund: 是否退款(0/1)  |  is_cancel: 是否取消(0/1)\n"
    "- aftersale_type: 售后类型(0~9)  |  buyer_nick: 买家昵称\n\n"
    "op: eq(等于) ne(不等于) gt(大于) gte(>=) lt(<) lte(<=) "
    "in(在列表中) like(模糊) is_null(是否空) between(区间)\n\n"
    "示例1：4月14日已发货订单列表\n"
    "  doc_type=order, mode=detail, filters=[\n"
    '    {"field":"order_status","op":"eq","value":"SELLER_SEND_GOODS"},\n'
    '    {"field":"consign_time","op":"gte","value":"2026-04-14 00:00:00"},\n'
    '    {"field":"consign_time","op":"lt","value":"2026-04-15 00:00:00"}]\n\n'
    "示例2：淘宝金额>500订单按店铺统计\n"
    "  doc_type=order, mode=summary, filters=[\n"
    '    {"field":"platform","op":"eq","value":"tb"},\n'
    '    {"field":"amount","op":"gt","value":500}],\n'
    "  group_by=[\"shop_name\"]\n\n"
    "示例3：导出3月拼多多发货订单\n"
    "  doc_type=order, mode=export, filters=[...],\n"
    "  fields=[\"order_no\",\"amount\",\"consign_time\"]",
    {
        "doc_type": _enum(
            "单据类型",
            ["order", "purchase", "aftersale", "receipt",
             "shelf", "purchase_return"],
        ),
        "mode": _enum(
            "输出模式：summary=聚合统计，detail=明细列表，export=导出文件",
            ["summary", "detail", "export"],
        ),
        "filters": {
            "type": "array",
            "description": "过滤条件数组，每个元素 {field, op, value}",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "列名"},
                    "op": {
                        "type": "string",
                        "enum": ["eq", "ne", "gt", "gte", "lt", "lte",
                                 "in", "like", "is_null", "between"],
                    },
                    "value": {"description": "过滤值"},
                },
                "required": ["field", "op", "value"],
            },
        },
        "group_by": {
            "type": "array",
            "items": {"type": "string"},
            "description": "分组字段（mode=summary时），如 [\"shop_name\"] 或 [\"platform\"]",
        },
        # 注：aggregations 参数已移除。summary 模式固定返回三个标准指标
        # （doc_count / total_qty / total_amount），与现有 RPC 行为一致。
        # 自定义聚合如需支持，在 V2 升级 RPC。
        "sort_by": _str("排序字段（mode=detail时）"),
        "sort_dir": _enum("排序方向", ["asc", "desc"]),
        "fields": {
            "type": "array",
            "items": {"type": "string"},
            "description": "返回/导出字段列表。mode=export 不传则返回可用字段文档",
        },
        "limit": _int("返回行数上限（detail默认20，export默认5000，上限10000）"),
        "time_type": _enum(
            "时间列（控制 summary 模式的统计时间维度）。"
            "含「付款」用 pay_time，含「发货」用 consign_time，默认 doc_created_at",
            ["doc_created_at", "pay_time", "consign_time"],
        ),
    },
    ["doc_type", "filters"],  # required
)
```

---

## 8. ERP_ROUTING_PROMPT 精简版

从 ~100 行精简到 ~65 行（保留关键规则，仅删除已合并工具的路由条目）：

```python
ERP_ROUTING_PROMPT = (
    "## 工具选择规则（必须遵守）\n\n"
    "### 层级：local > erp > fetch_all_pages > code_execute\n"
    "- 禁止跳过 local 工具直接用 erp 远程 API，除非 local 明确不支持\n"
    "- code_execute 是纯计算沙盒，不能查数据，只能处理已获取的 staging 数据\n\n"
    "### 核心工具\n"
    "- **local_data**：单据数据统一查询（订单/采购/售后/收货/上架/采退）。\n"
    "  支持 summary（聚合统计）/ detail（明细列表）/ export（导出文件）三种模式。\n"
    "  用 filters 数组指定任意字段组合过滤，用 product_code 字段匹配商品编码（自动匹配主编码+SKU编码）。\n"
    "- **local_compare_stats**：时间维度对比（同比/环比/自定义），禁止调 local_data 两次自行对比\n"
    "- **local_product_identify**：编码识别。其他工具需要精确编码时先调它\n"
    "- **local_stock_query**：库存查询（不同表，不走 local_data）\n"
    "- **local_product_stats**：商品维度统计报表（预聚合表，按编码+时间段）\n"
    "- **local_platform_map_query**：编码↔平台映射\n"
    "- **local_shop_list / local_warehouse_list**：店铺/仓库列表\n"
    "- **trigger_erp_sync**：手动触发数据同步\n"
    "- **fetch_all_pages**：本地没有的数据（如物流轨迹）全量翻页拉取\n"
    "- **erp_* 远程工具**：物流轨迹、操作日志、写入操作等 local 不支持的场景\n\n"
    "### 常见场景\n"
    "- 今天/本周/本月多少单 → local_data(doc_type=order, mode=summary, filters=[时间条件])\n"
    "- 已发货/未发货订单 → local_data(filters=[{field:order_status, op:eq, value:SELLER_SEND_GOODS}])\n"
    "- 按店铺/平台统计 → local_data(mode=summary, group_by=[shop_name])\n"
    "- 按商品排名 → local_data(mode=summary, group_by=[outer_id])\n"
    "- 查某订单详情 → local_data(mode=detail, filters=[{field:order_no, op:eq, value:xxx}])\n"
    "- 某商品流转 → local_data 按 6 种 doc_type 各调一次 summary\n"
    "- 对比/同比/环比 → local_compare_stats\n\n"
    "### 输出格式选择（自动判断）\n"
    "- 统计汇总（总数/金额/占比）→ mode=summary，直接文字回复\n"
    "- 结果 ≤20 条明细 → mode=detail，直接文字回复\n"
    "- 导出 Excel/报表/全量数据 → mode=export → code_execute 生成 Excel\n"
    "- 本地没有的数据（如物流轨迹）导出 → fetch_all_pages → code_execute\n\n"
    "### 编码识别\n"
    "- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型\n"
    "- 用户给模糊名称时必须先调 local_product_identify 确认编码\n"
    "- 套件(type=1/2)无独立库存 → 查子单品逐个查\n"
    "- 同一编码每会话只识别一次\n\n"
    "### 时间规范\n"
    "- 日期用 ISO: 2026-04-14 00:00:00\n"
    "- 含「付款」→ time_type=pay_time / filters 中用 pay_time 字段\n"
    "- 含「发货」→ time_type=consign_time / filters 中用 consign_time 字段\n"
    "- 默认 doc_created_at\n"
    "- 工具返回的时间块（含中文星期）必须逐字复述，禁止自行推算\n"
    "- 销量 = sum(quantity 字段)，不是记录条数\n\n"
    "### 售后跨工具\n"
    "- 默认 → local_data(doc_type=aftersale)\n"
    "- 淘宝/天猫退款详情 → erp_taobao_query(refund_list)\n"
    "- 归档老订单查不到 → 远程 erp_trade_query(query_type=1)\n\n"
    "### 中继键\n"
    "- local_data detail 模式返回的 doc_id/order_no/express_no → 直接用于 API 跨查\n"
    "- 物流轨迹 → express_query(system_id=sid)\n\n"
    "### 降级策略\n"
    "- local 工具返回错误 → 改用 erp 远程工具重试\n"
    "- local 返回 ⚠ 同步警告 → trigger_erp_sync 再重查\n"
    "- 连续 2 次空结果 → ask_user 确认条件\n\n"
    "### 歧义处理（必须用 ask_user）\n"
    "- local_product_identify 返回多条 → ask_user 选择，禁止自行取第一条\n"
    "- 模糊时间（'最近''上次'）→ ask_user 给 2-3 个选项确认\n"
    "- 写操作 → ask_user 确认影响\n\n"
    "### ERP 远程工具协议\n"
    "- 两步查询：先传 action 拿参数文档 → 再传 params 执行\n"
    "- 严格使用工具定义中的参数名，禁止臆造不存在的参数\n"
)
```

---

## 9. 开发任务拆分

### Phase 0：RPC 升级（前置，无依赖）
- [ ] 0.1 编写迁移脚本 `080_unified_query_rpc.sql`：RPC 加 `p_filters JSONB` 参数
- [ ] 0.2 本地执行迁移验证

### Phase 1：统一查询引擎（核心）
- [ ] 1.1 创建 `erp_unified_query.py`：列白名单 + _validate_filters()
- [ ] 1.2 实现 _build_orm_query()：Filter → Supabase 链式查询
- [ ] 1.3 实现 _summary()：调升级后的 RPC
- [ ] 1.4 实现 _detail()：ORM 查询 + 热冷表 UNION + 格式化
- [ ] 1.5 实现 _export()：两步协议 + 批量查询 + PII 脱敏 + Parquet

### Phase 2：工具定义 + 注册
- [ ] 2.1 `erp_local_tools.py`：新增 local_data 定义，删除 7 个旧定义，更新 ERP_LOCAL_TOOLS
- [ ] 2.2 `tool_registry.py`：新增 local_data ToolEntry（合并旧 tags），删除 7 个旧注册
- [ ] 2.3 `tool_domains.py`：新增 local_data → ERP，删除 7 个旧映射

### Phase 3：调度层对接
- [ ] 3.1 `erp_tool_executor.py`：dispatch 字典删旧加新，更新 _TIME_AWARE_TOOLS
- [ ] 3.2 `tool_args_validator.py`：新增 array 类型 json.loads 纠偏
- [ ] 3.3 `erp_tools.py`：ERP_ROUTING_PROMPT 替换为精简版

### Phase 4：local_compare 重构
- [ ] 4.1 `erp_local_compare_stats.py`：内部改为调用 UnifiedQueryEngine._summary()

### Phase 5：测试迁移 + 新增
- [ ] 5.1 统一引擎单测（新增 test_unified_query.py）：validate_filters、build_orm_query、summary/detail/export
- [ ] 5.2 迁移旧测试：test_erp_local.py 中 7 个旧工具测试类 → 转为 local_data 测试场景
- [ ] 5.3 迁移导出测试：test_erp_local_db_export.py → 转为 export 模式测试
- [ ] 5.4 更新断言：test_erp_agent.py / test_chat_tools.py / test_tool_loop_context.py 等工具名断言
- [ ] 5.5 集成测试：通过 ERP Agent 端到端验证 15 个场景（复用 Qwen 测试用例）
- [ ] 5.6 回归测试：全部后端测试全绿

### Phase 6：清理
- [ ] 6.1 删除 3 个旧实现文件 + 精简 erp_local_query.py（~470行）
- [ ] 6.2 删除 erp_local_helpers.py 中的 query_doc_items()（无调用方）
- [ ] 6.3 更新 config/chat_tools.py 并发安全列表 + services/sandbox/functions.py 引用
- [ ] 6.4 更新 FUNCTION_INDEX.md / PROJECT_OVERVIEW.md

### 依赖关系
```
Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
                                         ↓
                                     Phase 5 ──→ Phase 6
```

---

## 10. 依赖变更

无需新增依赖。现有依赖已满足：
- `supabase-py`：ORM 查询
- `pandas` + `pyarrow`：Parquet 导出
- `loguru`：日志

---

## 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 千问构造 filters 偶发错误 | 中 | tool_args_validator 兜底 + 错误信息引导重试 |
| RPC p_filters 解析 SQL 注入 | 高 | 白名单校验 field_name + format() 参数化 |
| 大量旧工具一次替换回归风险 | 高 | 端到端 15 场景测试 + 现有测试全绿才上线 |
| summary 模式 RPC 性能 | 低 | 现有 RPC 已验证毫秒级，加 filters 只多几个 WHERE 条件 |
| export 大数据量内存 | 低 | 复用现有 5000 行批次 + 10000 行硬限制 |
| local_compare 内部重构 | 低 | 只改数据获取方式，对比逻辑不变 |
| ERP_ROUTING_PROMPT 精简后路由准确率 | 中 | 工具从 14→9，路由复杂度大幅降低；精简后更清晰 |

---

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（18个文件 + 1个迁移 + 3个删除）
- [x] 7 类边界场景均有处理策略（空 filters / 双重序列化 / 非法 field / 类型不匹配 / 空结果 / 超大数据 / PII）
- [x] 所有新增文件预估 ≤ 500 行（erp_unified_query.py ~450行）
- [x] 无模糊版本号依赖
- [x] Filter DSL 准确率已实测验证（qwen3.5-plus 15/15）
- [x] 向后兼容：RPC 新参数有默认值，旧调用不受影响
- [x] local_compare 内部重构不影响外部接口
