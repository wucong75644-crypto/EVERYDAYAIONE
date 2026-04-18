# ERP 查询三层校验架构——参数规范化 + 意图完整性 + 结果反馈闭环

**版本 5.4 · 2026-04-18**

---

## 1. 问题发现

### 1.1 现象

用户说"昨天淘宝店铺的订单统计"，系统返回**全平台**数据（10,210 笔），没有过滤淘宝。

### 1.2 根因链路——双重 Bug

```
Bug 1（L2 漏提取）：
  PlanBuilder LLM
    → 输入: "昨天淘宝店铺的订单统计"
    → 输出: params = {doc_type: "order", mode: "summary", time_range: "..."}
    → 漏了: platform ← LLM 没提取

Bug 2（L1 编码不一致）：
  即使 LLM 正确提取 platform: "taobao"
    → _params_to_filters 直接透传 → filter: platform = "taobao"
    → 数据库存的是 "tb"（快麦 API source 字段原值）
    → 查出 0 条 ← 编码对不上
```

### 1.3 platform 编码不一致详情

| 来源 | 淘宝 | 抖音 | 其他（jd/pdd/kuaishou/xhs/1688） |
|------|------|------|------|
| PlanBuilder prompt 定义 | `taobao` | `douyin` | 一致，无问题 |
| 快麦 API `source` 字段 | `tb` | `fxg` | 一致，无问题 |
| 数据库 `platform` 列 | `tb` | `fxg` | 一致，无问题 |
| `_params_to_filters` | **直接透传，无映射** | **直接透传，无映射** | 一致，无问题 |

**数据链路验证**：
- `erp_sync_row_builders.py:45` — `"platform": doc.get("source")` 原样写入
- `erp_sync_config_handlers.py:28-31` — 店铺同步已有 `_PLATFORM_MAP = {"taobao": "tb", "douyin": "fxg", ...}`，说明快麦不同 API 返回的平台编码不统一
- 数据库存储统一为快麦编码体系：`tb/fxg/jd/pdd/kuaishou/xhs/1688`

**结论**：在查询入口加映射（`taobao→tb`, `douyin→fxg`），不改数据库——数据库存的是快麦上游定义的编码，改它就是在对抗数据源。

### 1.4 参数传递链路断裂分析

调研发现，不只是 platform 有问题。PlanBuilder prompt 定义了 9 个参数，但多个参数的**从 LLM 输出到查询引擎的完整链路是断裂的**。

逐个追踪每个参数的完整链路：

| 参数 | ① PlanBuilder prompt 定义 | ② _sanitize_params 保留 | ③ _params_to_filters 转 filter | ④ _dispatch 直接读 | ⑤ 最终消费点 | 状态 |
|------|-------------------------|----------------------|------------------------------|------------------|------------|------|
| mode | ✅ :202 | ✅ :117-118 | — | ✅ 各 agent | `_query_local_data(mode=...)` | ✅ 正常 |
| doc_type | ✅ :201 | ✅ :120-122 | — | ✅ 各 agent | `_query_local_data(doc_type=...)` | ✅ 正常 |
| time_range | ✅ :203 | ✅ :124-126 | ✅ :336-360 → gte/lt | — | UnifiedQueryEngine filter DSL | ✅ 正常 |
| time_col | ✅ :204 | ✅ :128-129 | ✅ :338 | — | filter DSL 时间列名 | ✅ 正常 |
| platform | ✅ :205 | ✅ :131-132 | ✅ :362-366 → eq | — | UnifiedQueryEngine filter DSL | ⚠️ 编码不一致 |
| group_by | ✅ :206 | ✅ :133-134 | — | ✅ trade/aftersale | `_query_local_data(group_by=...)` | ✅ 正常 |
| **product_code** | ✅ :207 | ❌ **丢弃** | ❌ **不转换** | ⚠️ 仅 warehouse | warehouse→`query_stock`; 其他 agent 不消费 | ❌ **断裂** |
| **order_no** | ✅ :208 | ❌ **丢弃** | ❌ **不转换** | ❌ 无 agent 读 | validate_params 检查存在性但不传给查询 | ❌ **断裂** |
| **include_invalid** | ✅ :209-210 | ❌ **丢弃** | — | ❌ **_dispatch 不传** | `_query_local_data(include_invalid=...)` 默认 False | ❌ **断裂×2** |

**断裂详情**：

#### order_no 断裂链路（3 处断点）

```
PlanBuilder prompt 告诉 LLM 提取 order_no（plan_builder.py:208）
  ↓
_sanitize_params 丢弃（plan_builder.py:111-135，clean dict 没有 order_no）  ← 断点①
  ↓ 即使修复了 sanitize，还有：
_params_to_filters 不处理（department_agent.py:325-367，只处理 time_range/platform） ← 断点②
  ↓
_dispatch 不读取（trade_agent.py:90-95，只传 mode/filters/group_by）  ← 断点③
  ↓
validate_params 检查 order_no 存在性（trade_agent.py:60），但查询时不用

用户说"查订单号 123456789012345678" → LLM 提取 order_no → 三处断裂 → 查全部订单
```

**但 order_no 是数据库合法列**：`COLUMN_WHITELIST` 有 `order_no: text`（erp_unified_schema.py:55），`validate_filters` 会放行 `{"field": "order_no", "op": "eq", "value": "..."}` 的 filter。说明**查询引擎支持按 order_no 过滤，是中间层漏写了**。

#### product_code 断裂链路

```
warehouse_agent 路径（唯一有效）：
  params.product_code → _dispatch 直接读（warehouse_agent.py:119）→ query_stock() → local_stock_query
  ✅ 有效（但 _sanitize_params 丢弃了，LLM 提取的到不了这里）

trade/purchase/aftersale 路径（全部断裂）：
  params.product_code → _sanitize_params 丢弃 ← 断点①
  即使不丢弃 → _params_to_filters 不转换为 {"field": "outer_id", ...} ← 断点②
  即使手动构造 filter → 下游可以处理：COLUMN_WHITELIST 有 outer_id: text
```

**注意字段名映射**：PlanBuilder 用 `product_code`，数据库列名是 `outer_id`。`_params_to_filters` 如果要支持 product_code，需要做字段名映射。

#### include_invalid 断裂链路（2 处断点）

```
PlanBuilder prompt 告诉 LLM 何时设 include_invalid=true（plan_builder.py:209-210）
  ↓
_sanitize_params 丢弃（plan_builder.py:111-135）  ← 断点①
  ↓ 即使修复了 sanitize，还有：
_dispatch 不传递（trade_agent.py:90-95 只传 mode/filters/group_by）  ← 断点②
  ↓
_query_local_data 用 kwargs.get("include_invalid", False)（department_agent.py:404）
  ↓ kwargs 里没有 include_invalid（_dispatch 没传）
UnifiedQueryEngine._summary(include_invalid=False)（永远走分类引擎分支）

用户说"包含刷单的订单统计" → LLM 设 include_invalid=true → 两处断裂 → 永远 False
```

**所有 agent 的 _dispatch 都有此问题**（已逐一确认）：
- trade_agent.py:90-95 — 只传 mode/filters/group_by
- aftersale_agent.py:90-96 — 只传 mode/filters/group_by
- purchase_agent.py:105-114 — 只传 mode/filters/group_by
- warehouse_agent.py:116-134 — stock_query 不涉及，receipt/shelf 只传 mode/filters

### 1.5 同类问题汇总

| 已发现的场景 | 根因 | 断裂位置 | 现状 |
|-------------|------|---------|------|
| platform 漏提取 | LLM 没从"淘宝"提取 platform | PlanBuilder LLM | 需要 L2 校验层 |
| platform 编码不一致 | taobao≠tb, douyin≠fxg | `_params_to_filters` 无映射 | 需要 L1 映射 |
| order_no 全链路断裂 | sanitize 丢弃 + 不转 filter + dispatch 不读 | 3 处断点 | 需要修复完整链路 |
| product_code 部分断裂 | sanitize 丢弃 + 非库存查询不转 filter | 2 处断点 | 需要修复完整链路 |
| include_invalid 断裂 | sanitize 丢弃 + _dispatch 不传 | 2 处断点 | 需要修复 sanitize + _dispatch |
| group_by 非标准值 | LLM 传 "store" 而非 "shop" | — | ✅ 已加白名单兜底 |
| doc_type 重复传入 filters | LLM 把 doc_type 放进 filters | — | ✅ 已加 skip 过滤 |
| 查询结果为空但不诊断 | 本地链路无空结果诊断 | — | 需要 L3 反馈层 |

---

## 2. 架构设计——三层校验闭环

### 2.1 全局架构图

```
用户查询: "昨天淘宝店铺的订单统计"
         ↓
┌─────────────────────────────────────┐
│  PlanBuilder (AI 决策)               │
│  → LLM 提取结构化参数                │
│  → 非确定性，可能漏提取/格式错误      │
└──────────────┬──────────────────────┘
               ↓ params (可能不完整)
╔══════════════════════════════════════╗
║  L1: 参数规范化                      ║ ← 执行前
║  → _sanitize_params 修复（透传丢失字段）║
║  → platform 编码映射（taobao→tb）    ║
║  → order_no/product_code → filter DSL ║
║  → "参数能不能正确到达查询引擎"       ║
╠══════════════════════════════════════╣
║  L2: 意图完整性校验                   ║ ← 执行前
║  → 查漏补缺（LLM 漏提取的参数）       ║
║  → 数据来源：用户原始查询文本          ║
║  → "有没有漏提取用户说的信息"          ║
╚══════════════════════════════════════╝
               ↓ 补全后的 params / 追问用户
┌─────────────────────────────────────┐
│  DAGExecutor → DepartmentAgent       │
│  → 转换 + 执行查询                   │
└──────────────┬──────────────────────┘
               ↓ 查询结果
╔══════════════════════════════════════╗
║  L3: 结果反馈                        ║ ← 执行后
║  → 空结果诊断、异常检测、重试引导     ║
║  → "结果对不对，不对怎么修正"         ║
╚══════════════════════════════════════╝
               ↓
         最终结果 / 回到 L1 重试
```

### 2.2 三层职责定义

| 层 | 名称 | 时机 | 数据来源 | 核心问题 | 处理方式 |
|---|------|------|---------|---------|---------|
| **L1** | 参数规范化 | 执行前 | 注册表/数据库 | 参数能不能到达查询引擎 | 修复链路断裂 + 自动纠正 |
| **L2** | 意图完整性 | 执行前 | 用户原始查询 | 有没有漏提取 | 自动补全 / 追问 |
| **L3** | 结果反馈 | 执行后 | 查询结果 | 结果对不对 | 诊断 / 重试 / 追问 |

### 2.3 闭环机制

```
L1 + L2 (执行前) → 执行 → L3 (执行后) → 结果异常？→ 回到 L1 重试
                                        → 结果正常？→ 返回用户
```

---

## 3. L1：参数规范化（执行前）——修复链路断裂

### 3.1 职责

确保 PlanBuilder 输出的每个参数都能**完整到达查询引擎**，不在中间环节丢失或编码错误。

### 3.2 需要修复的 4 个断裂点

#### 断裂 A：_sanitize_params 丢弃合法字段

**位置**：`plan_builder.py:111-135`

**现状**：只保留 mode/doc_type/time_range/time_col/platform/group_by，丢弃 product_code/order_no/include_invalid。

**修复**：透传这 3 个字段。

```python
# plan_builder.py _sanitize_params — 需要新增的透传
if params.get("product_code"):
    clean["product_code"] = params["product_code"]
if params.get("order_no"):
    clean["order_no"] = params["order_no"]
if isinstance(params.get("include_invalid"), bool):
    clean["include_invalid"] = params["include_invalid"]
```

#### 断裂 B：_params_to_filters 不转换 order_no

**位置**：`department_agent.py:325-367`

**现状**：只转换 time_range 和 platform 为 filter DSL。

**已验证 order_no 过滤可行**：
- `COLUMN_WHITELIST` 有 `order_no: text`（erp_unified_schema.py:55）
- `validate_filters()` 校验通过后，`apply_orm_filters()` 调用 `q.eq("order_no", value)`（erp_unified_filters.py:198-225）
- **单表结构**：所有 doc_type 存在同一张 `erp_document_items` 表（迁移脚本 031），order_no 列对所有 agent 都存在，不存在"只在 trade 表有"的问题

**修复**：

```python
# department_agent.py _params_to_filters — 新增 order_no 转换
order_no = params.get("order_no")
if order_no:
    filters.append({
        "field": "order_no", "op": "eq", "value": order_no,
    })
```

#### 断裂 C：_params_to_filters 不转换 product_code

**位置**：`department_agent.py:325-367`

**现状**：product_code 在 warehouse_agent 的 `_dispatch` 中直接读取（warehouse_agent.py:119），走 `query_stock` 专用路径。但 trade/purchase/aftersale 的 `_dispatch` 不读 product_code，只读 `filters`。

**字段名映射验证**：PlanBuilder 参数叫 `product_code`，数据库列名是 `outer_id`（erp_unified_schema.py:37）。

- `FIELD_MAP = {"outer_id": "product_code"}`（department_agent.py:47-51）方向是 `底层字段名→标准字段名`
- 仅在 `_build_output()`（department_agent.py:168-180）中用于**输出映射**（查询结果显示）
- **输入方向不存在映射**，所以 `_params_to_filters` 需要手动把 `product_code` 转为 `outer_id`

**修复**：

```python
# department_agent.py _params_to_filters — 新增 product_code 转换
product_code = params.get("product_code")
if product_code:
    filters.append({
        "field": "outer_id", "op": "eq", "value": product_code,
    })
```

**warehouse_agent 兼容性验证**（已确认不冲突）：

调用时序（department_agent.py:459-487）：
```python
merged = dict(params or {})                    # 复制 params
merged["filters"] = _params_to_filters(merged) # 只新增 "filters" key，不删除已有 key
result = await _dispatch(action, merged, ...)   # merged 里 product_code 仍然存在
```

- `_params_to_filters` 不删除任何 key，只新增 `filters`
- warehouse_agent 在 stock_query 时读 `merged.get("product_code")`（warehouse_agent.py:119），product_code 仍在
- warehouse_agent 在 receipt_query/shelf_query 时走 `_query_local_data`，此时 product_code 转成的 filter（`outer_id eq`）可被正确过滤
- 两条路径不冲突

#### 断裂 D：platform 编码不一致

**位置**：`department_agent.py:362-366`

**现状**：LLM 输出 `taobao`/`douyin`，数据库存 `tb`/`fxg`，`_params_to_filters` 直接透传不映射。

**修复**：

```python
# department_agent.py _params_to_filters
_PLATFORM_ALIAS = {"taobao": "tb", "douyin": "fxg"}

platform = params.get("platform")
if platform:
    platform = _PLATFORM_ALIAS.get(platform, platform)
    filters.append({"field": "platform", "op": "eq", "value": platform})
```

### 3.3 已完成的 L1 校验（数据完整性任务）

| 校验项 | 作用 |
|--------|------|
| group_by 白名单 | 非法值忽略，防 RPC ELSE 分支报错 |
| sort_by 白名单 | 只允许 COLUMN_WHITELIST 中的列 |
| sort_dir 枚举 | 只接受 asc/desc |
| fields 白名单 | detail+export 都校验 |
| doc_type in filters 过滤 | 已由 p_doc_type 处理，不重复进 DSL |
| **kwargs 显式化 | department_agent 不透传未知参数 |
| GROUP_BY_MAP 补充简写 | shop/product/supplier 等 LLM 简写可映射 |

---

## 4. L2：意图完整性校验（执行前）

### 4.1 职责

LLM 可能漏提取用户查询中的关键信息。L2 对比用户原始查询和 LLM 输出的 params，查漏补缺。

### 4.2 注入位置——PlanBuilder.build() 内部

**为什么不放在 erp_agent.py**：

ERPAgent 的职责是"意图识别 + 调度部门 Agent"，不做参数预处理（TECH_多Agent单一职责重构.md 架构决策 A2）。L2 platform 补全的本质是"补全 LLM 漏提取的意图信息"，属于**意图分析**的一部分，应该在 **PlanBuilder** 里完成。

**注入位置**：`plan_builder.py` 的 `build()` 方法（第 244-275 行），在 plan 生成之后、返回之前：

```python
# plan_builder.py - build() 方法
async def build(self, query: str) -> ExecutionPlan:
    # ── 第一级：LLM 规划 ──
    if self._adapter:
        try:
            plan = await self._llm_plan(query)
            _fill_platform(plan, query)   # L2：补全漏提取的 platform
            return plan
        except ...:
            ...
    # ── 第二级：关键词降级 ──
    domain = quick_classify(query)
    if domain:
        plan = ExecutionPlan.single(domain, task=query[:50])
        plan.rounds[0].params = _build_fallback_params(...)
        _fill_platform(plan, query)   # 降级路径也补全
        return plan
    # ── 第三级：abort ──
    ...
```

**选择此位置的原因**：
- PlanBuilder 的职责就是"从用户查询生成完整的执行计划"，L2 补全属于这个职责
- LLM 路径和降级路径都在 `build()` 内部，一个函数覆盖两条路径
- ERPAgent 保持干净——只调 `builder.build(query)`，拿到的已经是补全后的 plan
- `_fill_platform` 是模块级纯函数，不改变 PlanBuilder 类的接口

**可行性已验证**：
- `ExecutionPlan` 和 `Round` 都是普通 `@dataclass`，没有 `frozen=True`（execution_plan.py:21-50）
- `Round.params` 是 `dict` 类型，完全可变
- 降级路径已有 `plan.rounds[0].params = _build_fallback_params(...)` 的赋值（plan_builder.py:259），证明可以直接修改

### 4.3 Phase 1 只做 platform 自动补全

**为什么不在 Phase 1 做 product_code/order_no 的 L2 补全**：

1. **platform 是确定性映射**：中文"淘宝"→`"tb"` 是 100% 确定的。
2. **product_code 正则不可靠**：项目商品编码格式多样（`DBTXL01-02`/`ABC123`/`HM-2026A`），且用户查询是中文文本，正则 `\b` 在中英混排时行为不确定（`\b` 匹配 word boundary，中文字符旁边没有 word boundary）。误匹配风险高。
3. **order_no 正则更不可靠**：淘宝 18 位 / 京东 16 位 / 小红书 P+18 位 / 拼多多日期串，格式差异大。16-19 位纯数字的正则会误匹配手机号、金额等。
4. **product_code/order_no 的 LLM 提取率本身就高**：PlanBuilder prompt 已有明确指令"如用户提到了具体编码则提取"（plan_builder.py:207-208），LLM 漏提取的概率远低于 platform。

**Phase 1 策略**：修复 L1 链路断裂（让 LLM 提取的值能到达查询引擎） + L2 只做 platform 补全。product_code/order_no 的 L2 补全留到 Phase 2，结合实际漏提取频率再决定是否需要。

### 4.4 platform 自动补全逻辑

**中文关键词→DB 编码映射表**：

```python
_CN_TO_PLATFORM = {
    "淘宝": "tb", "天猫": "tb",
    "京东": "jd",
    "拼多多": "pdd",
    "抖音": "fxg", "抖店": "fxg",
    "快手": "kuaishou",
    "小红书": "xhs",
    "1688": "1688",
    "微店": "wd",
}
```

来源验证：与 `PLATFORM_CN`（erp_unified_schema.py:94-98）反转一致，补充了 `天猫→tb`、`抖店→fxg` 两个常用别名。

**补全规则**：
- 遍历 `plan.rounds`，跳过 compute 域（不做数据查询）
- 如果 `params` 已有 platform → 不覆盖（AI 优先原则）
- 扫描 `query` 中匹配的平台名 → 注入 DB 编码，如 `"淘宝"` → `platform="tb"`
- L2 直接注入 DB 编码（`"tb"` 而非 `"taobao"`），跳过 L1 映射

**多平台场景不在 L2 处理**：用户说"淘宝和京东的订单"时，由上游拆分——主 Agent 拆成两次 ERP Agent 调用，或 PlanBuilder 拆成多个 Round（每个 Round 对应一个平台）。到 L2 时每个 Round 只涉及单平台语境。

**与 L1 的协作**：
```
路径 1：LLM 漏提取 platform
  → L2 从 query 检测到"淘宝" → 补全 platform="tb"（DB 编码）
  → L1 _params_to_filters：platform="tb" 直接透传，无需映射

路径 2：LLM 正确提取 platform="taobao"
  → L2 看到 params 已有 platform → 不覆盖
  → L1 _params_to_filters：taobao → _PLATFORM_ALIAS 映射为 "tb"

路径 3：降级路径（LLM 失败）
  → _build_fallback_params 没有 platform
  → L2 从 query 检测到平台名 → 补全
```

### 4.5 设计原则

1. **AI 优先**：先让 LLM 提取，L2 只在 LLM 漏提取时补全
2. **确定性补全**：只补全能 100% 确定的信息（中文平台名→DB 编码），不做模糊匹配
3. **直接注入 DB 编码**：L2 补全的 platform 直接用 DB 值，跳过 L1 映射
4. **不确定就不补**：product_code/order_no 的正则提取不可靠，Phase 1 不做

---

## 5. L3：结果反馈（执行后）

### 5.1 职责

查询执行完成后，校验结果是否合理。不合理时诊断原因并引导修正。

### 5.2 能力清单

| 能力 | 触发条件 | 处理方式 | 示例 |
|------|---------|---------|------|
| **空结果诊断** | 查询返回 0 条 | 分析可能原因，建议修改参数 | "淘宝无数据，是否查全平台？" |
| **异常检测** | 结果数量级明显不对 | 提示用户确认 | 查单店铺返回全平台数量级 |
| **失败重试引导** | 查询报错 | 引导 AI 换参数或追问用户 | RPC 超时→缩小时间范围重试 |

### 5.3 空结果诊断逻辑

```
查询返回 0 条
  ↓
检查 1: 是否有 platform 过滤？
  → 有 → "该平台在此时间段无数据，是否查全平台？"
  → 无 → 继续

检查 2: 时间范围是否合理？
  → 未来日期 → "时间范围超出数据同步范围"
  → 太久远 → "该时间段数据可能已归档"
  → 正常 → 继续

检查 3: 数据同步是否正常？
  → 检查 sync_state 表最后同步时间
  → 超过 1 小时 → "数据同步可能延迟，建议稍后重试"
  → 正常 → "确实无匹配数据"
```

### 5.4 现有实现（远程 API 链路）

| 远程 API 链路 | 本地查询链路 | 状态 |
|-------------|------------|------|
| `diagnose_empty_result()` | 仅返回"无记录" | ❌ 需要补 |
| `FailureReflectionHook` | 无 | ❌ 需要补 |
| `AmbiguityDetectionHook` | 无 | ❌ 需要补 |

---

## 6. 与现有架构的关系

### 6.1 远程 API 链路已有的校验体系

```
远程 API 链路（erp_tools → dispatcher）：
  LLM 参数 → param_guardrails.preprocess_params()  ← L1: 格式纠正
           → param_mapper.map_params()             ← L1: 别名+白名单
           → param_guardrails.apply_code_broadening() ← L1: 编码拓宽
           → 执行 API 调用
           → diagnose_empty_result()               ← L3: 空结果诊断
           → loop_hooks.FailureReflectionHook      ← L3: 失败反思
           → loop_hooks.AmbiguityDetectionHook     ← L3: 歧义追问
```

**远程链路有 L1 和 L3，缺 L2。本地链路 L1 有多处断裂，L2 和 L3 都缺。**

### 6.2 核心文件参考

| 文件 | 可复用内容 |
|------|-----------|
| `param_guardrails.py` (503行) | preprocess_params + diagnose_empty_result |
| `param_mapper.py` (218行) | PARAM_ALIASES + map_params + 同义词 |
| `loop_hooks.py` (299行) | FailureReflectionHook + AmbiguityDetectionHook |
| `erp_unified_schema.py` | PLATFORM_CN（平台映射表）、COLUMN_WHITELIST（列白名单，确认 order_no/outer_id 可过滤） |
| `erp_sync_config_handlers.py:28-31` | _PLATFORM_MAP（快麦编码映射，已验证） |

---

## 7. 实施方案

### 7.1 Phase 1：L1 链路断裂修复 + L2 platform 自动补全

**解决的问题**：
1. `_sanitize_params` 丢弃 product_code/order_no/include_invalid（§3.2 断裂 A）
2. `_params_to_filters` 不转换 order_no/product_code 为 filter DSL（§3.2 断裂 B/C）
3. platform 编码不一致 taobao≠tb（§3.2 断裂 D）
4. `_dispatch` 不传 include_invalid 给 `_query_local_data`（§1.4 include_invalid 断点②）
5. platform 漏提取（L2 自动补全）

**改动文件清单**：

| 文件 | 改动 | 行数估计 |
|------|------|---------|
| `erp_unified_schema.py` | 新增 `PLATFORM_NORMALIZE` 映射表 | +12 行 |
| `plan_builder.py` | `_sanitize_params` 透传 + `_fill_platform` L2 补全 | +20 行 |
| `department_agent.py` | `_params_to_filters` 加 platform 映射 + order_no 转 filter + product_code→outer_id 转 filter | +15 行 |
| `departments/trade_agent.py` | `_dispatch` 透传 include_invalid | +1 行 |
| `departments/aftersale_agent.py` | `_dispatch` 透传 include_invalid | +1 行 |
| `departments/purchase_agent.py` | `_dispatch` 透传 include_invalid | +1 行 |
| `tests/test_plan_builder.py` | _sanitize_params 透传 + L2 platform 补全测试 | +40 行 |
| `tests/test_department_agent.py` | platform 映射 + order_no/product_code filter 转换测试 | +30 行 |
| `tests/test_trade_agent.py` 等 | include_invalid 透传测试 | +15 行 |

**统一平台映射表**（L1 + L2 共用，放在 `erp_unified_schema.py` 模块级——该文件已有 `PLATFORM_CN`，是平台编码的权威位置）：

```python
# erp_unified_schema.py 新增
# LLM 参数值 / 中文关键词 → 数据库 platform 列值
PLATFORM_NORMALIZE: dict[str, str] = {
    # LLM 输出的参数值（来自 PlanBuilder prompt:205）
    "taobao": "tb", "douyin": "fxg",
    # jd/pdd/kuaishou/xhs/1688 两边一致，无需映射
    # L2 补全时从查询文本提取的中文关键词
    "淘宝": "tb", "天猫": "tb",
    "京东": "jd", "拼多多": "pdd",
    "抖音": "fxg", "抖店": "fxg",
    "快手": "kuaishou", "小红书": "xhs",
    "1688": "1688", "微店": "wd",
}
```

- L1 `_params_to_filters`（department_agent.py）：`from ... import PLATFORM_NORMALIZE` → `platform = PLATFORM_NORMALIZE.get(platform, platform)`
- L2 `_fill_platform`（plan_builder.py）：`from ... import PLATFORM_NORMALIZE` → 遍历中文 key 做查询匹配

**依赖方向**：`plan_builder.py → erp_unified_schema.py ← department_agent.py`，共同依赖 schema 定义层，不产生循环依赖。

**include_invalid _dispatch 修复示例**（trade_agent.py）：

```python
# 修复前
async def _dispatch(self, action, params, context):
    return await self.query_orders(
        mode=params.get("mode", "summary"),
        filters=params.get("filters", []),
        group_by=params.get("group_by"),
    )

# 修复后
async def _dispatch(self, action, params, context):
    return await self.query_orders(
        mode=params.get("mode", "summary"),
        filters=params.get("filters", []),
        group_by=params.get("group_by"),
        include_invalid=params.get("include_invalid", False),  # 新增
    )
```

**职责分布**（符合单一职责架构）：

```
erp_unified_schema.py（共享数据层）
  └─ PLATFORM_NORMALIZE 映射表 — L1 和 L2 共用

plan_builder.py（意图分析层）
  ├─ _sanitize_params — 透传 product_code/order_no/include_invalid
  └─ _fill_platform — L2 补全漏提取的 platform（意图分析的一部分）

department_agent.py（参数转换层）
  └─ _params_to_filters — L1 platform 映射 + order_no/product_code 转 filter DSL

departments/trade_agent.py 等（业务域层）
  └─ _dispatch — 透传 include_invalid

erp_agent.py — 不改动（保持"意图识别 + 调度"的纯调度职责）
```

**执行顺序**（Step 1/2/3 无依赖可并行）：

```
Step 1: erp_unified_schema.py — 新增 PLATFORM_NORMALIZE
Step 2: plan_builder.py — 修复 _sanitize_params + L2 _fill_platform
Step 3: department_agent.py — _params_to_filters 加 L1 映射 + order_no/product_code 转换
Step 4: departments/trade_agent.py 等 — _dispatch 透传 include_invalid
Step 5: 测试
```

### 7.2 后续 Phase

| 阶段 | 内容 | 收益 |
|------|------|------|
| **Phase 2** | L2 扩展——根据实际漏提取频率决定是否加 product_code/order_no 补全；L2 冲突检测（域路由冲突追问） | 减少漏提取 |
| **Phase 3** | L3 空结果诊断——复用 diagnose_empty_result 模式 | 查询无结果时给出有用建议 |
| **Phase 4** | L3 失败反思——复用 FailureReflectionHook 模式 | 查询报错时自动重试 |
| **Phase 5** | L1 补全——本地链路格式纠正 | 统一两条链路的 L1 能力 |

### 7.3 验收标准

Phase 1 修复后，以下场景应返回正确结果：

| # | 用户查询 | 修复前行为 | 修复后预期 | 涉及修复点 |
|---|---------|-----------|-----------|-----------|
| 1 | "昨天淘宝的订单统计" | 返回全平台 10,210 笔 | 返回淘宝订单（L2 补全 platform="tb"） | L2 + 断裂 D |
| 2 | "昨天淘宝的订单统计"（LLM 提取了 platform=taobao） | 查 0 条（taobao≠tb） | 返回淘宝订单（L1 映射 taobao→tb） | 断裂 D |
| 3 | "订单号 126036803257340376 的详情" | 返回全部订单，order_no 被丢弃 | 返回该订单 1 条明细 | 断裂 A + B |
| 4 | "商品 DBTXL01 的订单" | 返回全部订单，product_code 被丢弃 | 返回该商品的订单（filter outer_id=DBTXL01） | 断裂 A + C |
| 5 | "商品 DBTXL01 的库存" | warehouse_agent 无法读到 product_code | 返回该商品库存（sanitize 透传 → _dispatch 直接读） | 断裂 A |
| 6 | "包含刷单的订单统计" | include_invalid 被丢弃，永远走分类引擎 | include_invalid=True 传到 UnifiedQueryEngine | 断裂 A + _dispatch |
| 7 | "淘宝和京东的订单" | — | 由上游处理：主 Agent 拆成两次调用或 PlanBuilder 拆多 Round，不在 L2 范围 | 上游拆分 |

### 7.4 include_invalid 完整链路确认

修复后的参数流转（已逐步验证）：

```
LLM 输出 params = {include_invalid: true}
  ↓
_sanitize_params 透传（Phase 1 修复断裂 A）
  clean["include_invalid"] = True
  ↓
plan.rounds[i].params = {include_invalid: True, ...}
  ↓
DepartmentAgent.execute(params={include_invalid: True, ...})
  merged = dict(params)  → merged["include_invalid"] = True
  ↓
_dispatch(action, merged, context)（Phase 1 修复断裂②）
  → self.query_orders(include_invalid=True, ...)
  ↓
_query_local_data(doc_type="order", include_invalid=True, ...)
  签名是 **kwargs → kwargs["include_invalid"] = True
  ↓ department_agent.py:404
  engine.execute(include_invalid=kwargs.get("include_invalid", False))
  → include_invalid=True ✅
  ↓
UnifiedQueryEngine._summary(include_invalid=True)
  → 跳过分类引擎，走通用 erp_global_stats_query ✅
```

### 7.5 设计约束

1. **单一职责不越界**：L2 放在 PlanBuilder（意图分析），L1 放在 DepartmentAgent（参数转换），ERPAgent 不碰参数
2. **修复优先于新增**：先修复已有参数的链路断裂（L1），再做新的意图补全（L2）
3. **AI 优先**：L2 是补全不是替代——确定性提取只做 AI 漏掉的部分
4. **不改动下游**：DAGExecutor/UnifiedQueryEngine 接口不变
5. **共享数据放共享位置**：`PLATFORM_NORMALIZE` 放 `erp_unified_schema.py`，避免跨层 import
6. **可观测**：每次补全都记 logger.info，便于评估效果

---

## 8. 审查修订记录

| 版本 | 日期 | 修改内容 |
|-----|------|---------|
| 1.0 | 2026-04-18 | 初版：问题发现 + L2 意图完整性方案 |
| 2.0 | 2026-04-18 | 三层架构重构：L1/L2/L3 三层闭环 |
| 3.0 | 2026-04-18 | 发现 platform 编码不一致双重 Bug（taobao≠tb）；确认不改数据库 |
| 3.1 | 2026-04-18 | 代码审核：发现 _sanitize_params 丢弃字段 |
| 4.0 | 2026-04-18 | 全面链路审核重写：逐参数追踪完整链路发现 6 处断裂 |
| 4.1 | 2026-04-18 | 实施前验证：8 项前置检查全部通过（见附录 A） |
| 4.2 | 2026-04-18 | 二次深度审核：include_invalid 2处断裂；映射表合并；Phase 1 新增 3 个 agent 修复 |
| 4.3 | 2026-04-18 | 三次验证：include_invalid 完整链路确认 + 单表结构确认 + 多平台由上游拆分 |
| 4.4 | 2026-04-18 | **单一职责审查**：L2 从 erp_agent 移到 plan_builder（ERPAgent 不碰参数）；PLATFORM_NORMALIZE 从 department_agent 移到 erp_unified_schema（避免跨层 import）；erp_agent.py 不再修改；Phase 1 文件清单调整 |
| 5.0 | 2026-04-18 | **Phase 1 实施完成**：6 处链路断裂修复 + L2 platform 补全 + 评审共识（多匹配不补全）；4535 测试全绿（+29） |
| 5.1 | 2026-04-18 | **Phase 3 实施完成**：L3 空结果诊断（`_diagnose_empty` + `_query_local_data` 注入）；4544 测试全绿（+9） |
| 5.2 | 2026-04-18 | **Phase 4 实施完成**：L3 失败诊断（`_diagnose_error` + ERROR 分支重试建议）；4554 测试全绿（+10） |
| 5.3 | 2026-04-18 | **Phase 2+5 实施完成**：L2 域路由冲突检测（`_DOMAIN_DOC_TYPES` 自动纠正）+ L1 格式纠正（time_range 分隔符归一 + 参数去空格）；4564 测试全绿（+10） |
| 5.4 | 2026-04-18 | **Phase 2 补全完成**：L2 product_code/order_no DB 验证补全（`_fill_codes` 正则粗筛+erp_products/erp_document_items 验证存在性）；PlanBuilder 新增 db 参数；4575 测试全绿（+11） |

---

## 附录 A：实施前验证清单（14/14 通过）

| # | 检查项 | 结论 | 证据位置 |
|---|--------|------|---------|
| 1 | FIELD_MAP 方向 | `outer_id→product_code` 是输出映射，输入需反向 | department_agent.py:47-51, 168-180 |
| 2 | order_no 过滤支持 | ✅ validate_filters 用 COLUMN_WHITELIST 校验 + apply_orm_filters 执行 `q.eq("order_no", val)` | erp_unified_filters.py:27-63, 198-225 |
| 3 | 表结构 | 单张 erp_document_items，order_no + outer_id 列对所有 doc_type 都存在 | 迁移脚本 031 + erp_unified_schema.py:37,55 |
| 4 | warehouse_agent 兼容性 | ✅ _params_to_filters 只加 "filters" key 不删已有 key，product_code 保留 | department_agent.py:459-487 |
| 5 | 降级路径结构 | 返回 ExecutionPlan 实例，rounds[0].params = dict，与 LLM 路径一致 | plan_builder.py:257-270 |
| 6 | ExecutionPlan 可变性 | 无 frozen=True，Round.params 是 dict，完全可变 | execution_plan.py:21-50 |
| 7 | PlanBuilder prompt | 确认定义了 product_code/order_no 提取指令 | plan_builder.py:207-208 |
| 8 | 多平台场景 | 由上游拆分（主 Agent 或 PlanBuilder），L2 只处理单平台补全 | plan_builder.py:205, §4.4 |
| 9 | filter 字段白名单 | validate_filters 用的就是 COLUMN_WHITELIST，同一张表控制过滤和排序 | erp_unified_filters.py:40 |
| 10 | _params_to_filters field 值 | 用数据库列名（apply_orm_filters 直接 `q.eq(field, val)` 查 Supabase） | erp_unified_filters.py:198-225 |
| 11 | include_invalid _dispatch 断裂 | 所有 agent 的 _dispatch 都不传 include_invalid，需逐个修复 | trade:90-95, aftersale:90-96, purchase:105-114 |
| 12 | _query_local_data 签名 | `**kwargs` 接收，`kwargs.get("include_invalid", False)` 读取，透传可行 | department_agent.py:371-406 |
| 13 | receipt/shelf 表结构 | 所有 doc_type 查同一张 erp_document_items，outer_id 列对 receipt/shelf 存在 | erp_unified_query.py:336 |
| 14 | PlanBuilder 多平台支持 | prompt 定义 platform 为单值，不支持数组；多平台不补全是正确策略 | plan_builder.py:205 |
