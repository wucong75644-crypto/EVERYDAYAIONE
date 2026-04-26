# TECH_ERP查询能力补全

> 版本：v1.0 | 日期：2026-04-26 | 状态：方案确认

## 1. 背景

用户问"库存不足10件的商品"时，ERP Agent 无法在 SQL 层过滤数量，只能导出全量数据再筛选，导致超时。

调研+代码审核发现：底层 `erp_unified_filters.py` 已完整支持 `gt/lt/gte/lte/between/ne/is_null` 全部操作符，但上层 PlanBuilder prompt 和 param_converter 只暴露了 `eq/like/in` 三种。本次一次性补全所有缺失的查询能力。

## 2. 现有能力 vs 缺口

### 已有能力
| 类型 | 操作 | 示例 |
|------|------|------|
| 文本精确 | `eq` | 买家昵称 = "张三" |
| 文本模糊 | `like` | 店铺名 LIKE "%旗舰店%" |
| 多值匹配 | `in` | 编码 IN ("A","B","C") |
| 枚举映射 | `eq` | "退货退款" → aftersale_type = "2" |
| 时间范围 | `gte` + `lt` | 2026-04-25 ~ 2026-04-26 |
| 布尔标记 | `eq` | is_cancel = 1 |
| 分组统计 | `group_by` | 按店铺/平台/商品统计 |

### 本次补全的 6 种能力

| # | 能力 | 用户怎么问 | 新参数名 | 底层状态 |
|---|------|-----------|---------|---------|
| 1 | 数值比较+区间 | "库存<10""金额100~500" | `numeric_filters` | ✅ 已有 |
| 2 | 排序+TopN | "销量最高的10个" | `sort_by` + `limit` | ✅ 已有（prompt 暴露） |
| 3 | 空值判断 | "没有快递单号的" | `null_fields` | ✅ 已有 |
| 4 | 单值否定 | "非淘宝平台" | `exclude_filters` → `ne` | ✅ 已有 |
| 5 | 多值排除 | "除了淘宝和拼多多" | `exclude_filters` → `not_in` | ⚠️ 需补底层 |
| 6 | 占比计算 | "各平台占比" | 不改代码 | prompt 引导自算 |

## 3. 架构审核

### 三条查询路径的操作符支持情况

| 操作符 | ORM (Supabase) | DuckDB Export | RPC Summary |
|--------|----------------|---------------|-------------|
| gt/gte/lt/lte | ✅ | ✅ | ✅ |
| between | ✅ | ✅ | ✅ |
| ne | ✅ | ✅ | ✅ |
| is_null | ✅ | ✅ | ✅ |
| not_in | ❌ 需加 | ❌ 需加 | ❌ 需加 |

### `_sanitize_params()` 兼容性

行 175 只允许 `list[str]`，`numeric_filters`/`exclude_filters` 是 `list[dict]` → 会被丢弃。需加白名单。

## 3.1 审计发现的 4 个数据流断裂点

| 断裂点 | 位置 | 问题 | 影响 |
|-------|------|------|------|
| **1** | `_sanitize_params()` 行 175 | `list[dict]` 被跳过 | numeric_filters/exclude_filters 第 2 层丢失 |
| **2** | `params_to_filters()` 末尾 | 缺新参数处理代码 | numeric_filters/exclude_filters/null_fields 无法转为 filter |
| **3** | `_summary()` 签名 | 未接收 sort_by/limit | summary 模式排序/分页失效，limit 硬编码 20 |
| **4** | `_export()` SQL 生成 | sort_by 未使用，limit 被 EXPORT_MAX 覆盖 | 用户指定的排序/条数被忽略 |

## 4. 改动清单（8 个文件 + 1 个迁移）

### 第一层：底层补 `not_in`（4 处）

#### 4.1 `backend/services/kuaimai/erp_unified_schema.py`

`OP_COMPAT` 中 text/integer/numeric 加 `"not_in"`：

```python
"text": {"eq", "ne", "like", "in", "not_in", "is_null"},
"integer": {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "between"},
"numeric": {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "between"},
```

#### 4.2 `backend/services/kuaimai/erp_unified_filters.py`

`apply_orm_filters()` 加 `not_in` 分支：

```python
elif f.op == "not_in" and isinstance(val, list) and val:
    q = q.not_.in_(f.field, val)
```

#### 4.3 `backend/services/kuaimai/erp_duckdb_helpers.py`

`build_export_where()` 加 `not_in` 分支：

```python
elif f.op == "not_in" and isinstance(f.value, list) and f.value:
    in_vals = ", ".join(f"'{_sql_escape(x)}'" for x in f.value)
    clauses.append(f"{f.field} NOT IN ({in_vals})")
```

#### 4.4 `backend/migrations/102_rpc_add_not_in_op.sql`

RPC 函数加 `WHEN 'not_in'`（与 `in` 对称）：

```sql
WHEN 'not_in' THEN
    IF jsonb_typeof(val) = 'array' AND jsonb_array_length(val) > 0 THEN
        base_q := base_q || format(
            ' AND %I NOT IN (SELECT jsonb_array_elements_text(%L::jsonb))',
            field_name, val::text
        );
    END IF;
```

### 第二层：上层打通（3 个文件）

#### 4.5 `backend/services/agent/plan_builder.py`

**A. `_PARAM_DEFINITIONS` 新增 3 个参数段：**

```
【数值过滤（可选，用户提到数量/金额/重量等数值条件时提取）】
- numeric_filters: 数值条件数组，格式 [{"field":"字段名","op":"操作符","value":数值}]
  field 可选: quantity(数量) / amount(金额) / price(单价) / cost(成本) / weight(重量) /
    pay_amount(实付) / gross_profit(毛利) / refund_money(退款额) / post_fee(运费)
  op 可选: gt(大于) / gte(>=) / lt(<) / lte(<=) / between(区间)
  value: 数字；between 时为 [min, max]
  关键词映射：不足/低于/少于/小于/以下 → lt；超过/多于/大于/以上 → gt；之间/到 → between

【否定/排除过滤（可选，用户说"不是/非/除了/排除"时提取）】
- exclude_filters: 排除条件数组，格式 [{"field":"字段名","value":"排除值"}]
  单值: [{"field":"platform","value":"taobao"}] → platform != taobao
  多值: [{"field":"platform","value":["taobao","pdd"]}] → platform NOT IN (taobao, pdd)

【空值检查（可选，用户说"没有/为空/缺少/未填"时提取）】
- null_fields: 要筛选为空的字段名列表，如 ["express_no"]
```

**B. 展示控制段补充 sort/limit：**

```
- sort_by: 排序字段（如 quantity/amount，默认按时间降序）
- sort_dir: asc(升序) / desc(降序，默认)
- limit: 返回条数上限（"前10名"→limit:10，默认20）
```

**C. few-shot 示例追加 4 个**（数值比较/排序TopN/否定排除/空值判断）

**D. `_sanitize_params()` 加 `list[dict]` 白名单：**

```python
_LIST_DICT_PARAMS = {"numeric_filters", "exclude_filters"}

elif isinstance(value, list) and value and key in _LIST_DICT_PARAMS:
    if all(isinstance(v, dict) for v in value):
        clean[key] = value
```

#### 4.6 `backend/services/agent/param_converter.py`

`params_to_filters()` 末尾新增 3 段（约 25 行）：

```python
# ── 数值条件过滤 ──
NUMERIC_FILTER_FIELDS = {
    "quantity", "amount", "price", "cost", "weight",
    "pay_amount", "gross_profit", "refund_money",
    "post_fee", "discount_fee", "total_fee", "sale_price",
    "real_qty", "actual_return_qty",
}
NUMERIC_OPS = {"gt", "gte", "lt", "lte", "between"}

for nf in params.get("numeric_filters", []):
    field, op, value = nf.get("field"), nf.get("op"), nf.get("value")
    if field in NUMERIC_FILTER_FIELDS and op in NUMERIC_OPS and value is not None:
        filters.append({"field": field, "op": op, "value": value})

# ── 否定/排除过滤 ──
for ef in params.get("exclude_filters", []):
    field, value = ef.get("field"), ef.get("value")
    if field and value is not None:
        if isinstance(value, list):
            filters.append({"field": field, "op": "not_in", "value": value})
        else:
            filters.append({"field": field, "op": "ne", "value": value})

# ── 空值判断 ──
for nf in params.get("null_fields", []):
    if isinstance(nf, str) and nf.strip():
        filters.append({"field": nf, "op": "is_null", "value": True})
```

#### 4.7 `backend/tests/test_param_converter.py`

新增 5 个测试类共 15 个用例：

| 测试类 | 用例 |
|--------|------|
| `TestNumericFilters` | qty<10, amount between, 非法字段忽略, 非法op忽略, 空数组 |
| `TestExcludeFilters` | ne 单值, not_in 多值, 空数组 |
| `TestNullFields` | 单字段, 多字段, 空数组 |
| `TestSortAndLimit` | sort_by 透传, limit 透传 |
| `TestMixedFilters` | 所有过滤类型共存 |

### 第三层：同步修改已有测试（2 个文件）

#### 4.8 `backend/tests/test_unified_query.py`

`TestOpCompat.test_text_supports_eq_like_in`（行 54）做了精确相等断言：
```python
assert {"eq", "ne", "like", "in", "is_null"} == text_ops
```
加 `not_in` 后必须同步更新为：
```python
assert {"eq", "ne", "like", "in", "not_in", "is_null"} == text_ops
```

同时新增 `test_not_in_calls_not_in_` 测试 `apply_orm_filters` 的 `not_in` 分支。

#### 4.9 `backend/tests/test_erp_duckdb_helpers.py`

新增 `test_not_in_generates_not_in_sql` 验证 `build_export_where` 的 `NOT IN` SQL 生成。

#### 4.10 `backend/tests/test_rpc_filter_whitelist.py`

**不需要改动**。白名单验证的是**字段名**同步，`not_in` 是操作符，不影响字段白名单。
已验证：`RPC_ORDER_STATS_FILTER_FIELDS` == `RPC_BASE_Q_COLUMNS` == SQL 迁移白名单，三处同步不受操作符变更影响。

### 第四层：查询引擎 sort_by/limit 打通（1 个文件）

> **审计发现**：`execute()` 接收了 sort_by/limit 参数，但 `_summary()` 和 `_export()` 内部没有使用。

#### 4.11 `backend/services/kuaimai/erp_unified_query.py`

**断裂点 A — summary 模式（行 155-159）：**

`_summary()` 方法签名未接收 sort_by/limit，RPC 调用中 `p_limit` 硬编码为 20。

修复：将 sort_by/sort_dir/limit 传给 `_summary()`，RPC 参数 `p_limit` 改用传入值：

```python
# execute() 中
return await self._summary(
    doc_type, validated, tr, group_by, request_ctx,
    include_invalid=include_invalid,
    sort_by=sort_by, sort_dir=sort_dir, limit=limit,  # 新增
)

# _summary() 签名新增
async def _summary(
    self, ..., sort_by: str | None = None,
    sort_dir: str = "desc", limit: int = 20,
) -> ToolOutput:
    ...
    params["p_limit"] = limit  # 替换硬编码 20
```

**断裂点 B — export 模式（行 373-375）：**

DuckDB SQL 中 ORDER BY 固定用 time_col DESC，sort_by 参数被忽略。LIMIT 使用 EXPORT_MAX（100万）而非传入的 limit。

修复：sort_by 有值时替代默认排序，limit 与 EXPORT_MAX 取较小值：

```python
# _export() 签名新增 sort_by/sort_dir
async def _export(
    self, ..., limit: int, sort_by: str | None = None,
    sort_dir: str = "desc", ...
) -> ToolOutput:
    ...
    # ORDER BY：优先用 sort_by，降级到 time_col
    if sort_by and sort_by in COLUMN_WHITELIST:
        order_col = sort_by
        order_dir = sort_dir.upper()
    else:
        order_col = _FIELD_LABEL_CN.get(tr.time_col, tr.time_col)
        order_dir = "DESC"

    # LIMIT：用户指定 limit 与安全上限取小值
    max_rows = min(limit, EXPORT_MAX) if limit else EXPORT_MAX
```

## 5. 不需要改动的文件

| 文件 | 原因 |
|------|------|
| `department_agent.py` | `_query_kwargs()` 已正确提取 sort_by/limit，透传无问题 |
| `erp_tool_description.py` | 能力描述不暴露内部过滤实现 |
| `chat_tools.py` | 主 Agent 提示词不变 |

## 6. 端到端数据流

```
用户："除了淘宝以外，库存不足10件的商品，按数量排序"

① PlanBuilder(千问) 提取参数：
{
  "domain": "warehouse",
  "params": {
    "doc_type": "shelf", "mode": "export",
    "time_range": "2026-04-26 ~ 2026-04-26",
    "numeric_filters": [{"field":"quantity","op":"lt","value":10}],
    "exclude_filters": [{"field":"platform","value":"taobao"}],
    "sort_by": "quantity", "sort_dir": "asc", "limit": 50
  }
}

② _sanitize_params() → 保留（numeric_filters/exclude_filters 在白名单）

③ param_converter.params_to_filters() → [
  {"field":"doc_created_at","op":"gte","value":"2026-04-26T00:00:00"},
  {"field":"doc_created_at","op":"lt","value":"2026-04-27T00:00:00"},
  {"field":"quantity","op":"lt","value":10},
  {"field":"platform","op":"ne","value":"taobao"}
]

④ validate_filters() → ValidatedFilter 列表（已支持所有 op）

⑤ apply_orm_filters() / build_export_where() → SQL:
  WHERE doc_created_at >= ... AND quantity < 10 AND platform != 'taobao'
  ORDER BY quantity ASC LIMIT 50

⑥ 返回少量结果 → 秒级响应 ✅
```

## 7. 执行顺序

1. **先跑迁移** `102_rpc_add_not_in_op.sql` → 生产 DB 支持 not_in
2. **改底层 3 文件** → schema + filters + duckdb（纯增量，不影响现有查询）
3. **改上层 2 文件** → plan_builder prompt + param_converter（纯增量）
4. **跑全量测试** → 确认 5300+ 测试不回归
5. **部署 + 生产验证**

## 8. 验证方式

1. 新增测试：`python -m pytest tests/test_param_converter.py -v -k "Numeric or Exclude or Null or Sort or Mixed"`
2. 全量回归：`python -m pytest tests/ -q`
3. 生产验证查询：
   - "库存不足10件的商品" → 看 numeric_filters 参数，秒级返回
   - "除了淘宝的本月订单" → 看 exclude_filters 参数
   - "没有快递单号的已发货订单" → 看 null_fields 参数
   - "金额最高的10笔订单" → 看 sort_by + limit 参数

## 9. 后续迭代（本次不做）

| 能力 | 复杂度 | 依赖 |
|------|--------|------|
| 同比/环比 | 中 | 需两次查询做差 |
| 累计值 | 中 | 窗口函数 |
| 维度下钻 | 低 | 追加 group_by |
| 去重计数 | 中 | RPC 改 COUNT(DISTINCT) |
| 交叉对比 | 中 | 两次查询横向对比 |
| 波动归因 | 高 | 自动多维 group_by |
| 移动平均 | 高 | 窗口函数 |
