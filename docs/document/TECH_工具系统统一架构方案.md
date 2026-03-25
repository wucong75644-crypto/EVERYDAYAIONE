# TECH_工具系统统一架构方案

> **版本**：V5.0 | **日期**：2026-03-23 | **状态**：方案确认，准备开发

---

## 一、一句话总结

**现在**：AI 每次要翻 350 页说明书从 22 个工具 + 178 个 action 里选。
**优化后**：系统自动挑 ~8 个工具 + ~8 个 action，不够时自动补充。同时废弃 v1，全量走 v2。

---

## 二、现状问题

### AI 每次处理的数据量

| 部分 | 数据量 | 占比 |
|------|--------|------|
| 工具说明（22 个工具 + 178 个 action） | ~5,650 字 | 73% |
| 提示词规则 | ~1,300 字 | 17% |
| 用户消息 + 历史 | ~750 字 | 10% |
| **总计** | **~7,700 字** | |

### 工具构成

| 类别 | 数量 | 明细 |
|------|------|------|
| ERP 本地工具 | 10 | local_product_identify 等（priority=1） |
| ERP 远程工具 | 8 | erp_product_query 等（priority=2） |
| 常驻工具 | 6 | code_execute, erp_api_search, search_knowledge, get_conversation_context, route_to_chat, ask_user |
| **合计** | **22** | 不含 v1 废弃工具（model_search, web_search） |

### 导致的问题

1. **AI 选错工具** — 22 个工具眼花，本地和远程分不清
2. **查询慢** — 选了远程 API（2-5 秒）而不是本地数据库（5 毫秒）
3. **查不到** — 远程 API 没数据，本地有但没用上
4. **维护难** — 新增工具要改 3-4 个文件 + 改提示词

---

## 三、优化方案

### 核心思路：三个改动

```
改动 1：统一工具注册（给每个工具和 action 贴标签 + 标优先级）
改动 2：双层智能筛选 + 兜底扩充
        第一层：筛工具（22 → ~8）
        第二层：筛 action（178 → ~8 per tool）
        兜底：AI 调用了不在列表的工具/action 时自动补充
改动 3：废弃 v1，全量走 v2（清理死代码）
```

---

## 四、改动 1：统一工具注册

### 工具注册格式

```python
{
    name: "local_stock_query",
    domain: "erp",
    description: "查询库存数量和状态",
    tags: ["库存", "可售", "锁定", "预占"],   # 语义标签（用于子串匹配）
    priority: 1,                              # 1=本地优先，2=远程
    always_include: False,                    # True=始终包含
}
```

### action 也有标签（关键新增）

现在 erp_product_query 有 35 个 action，每个 action 在 registry 里已有 description。
用 description 做**子串匹配**，筛选后**只保留相关的 action**：

```
erp_product_query 的 35 个 action：

  product_list     → description 含 "商品列表"
  stock_status     → description 含 "库存快照"     ← 用户问"库存"，匹配！
  warehouse_stock  → description 含 "仓库库存"     ← 用户问"库存"，匹配！
  brand_list       → description 含 "品牌"         ← 用户没问品牌，排除
  tag_list         → description 含 "标签"         ← 排除
  sku_list         → description 含 "SKU规格"      ← 排除
  ...

筛选后 AI 只看到：
  erp_product_query(action: enum["stock_status", "warehouse_stock", "product_list"])
  而不是 35 个 action 全列出来
```

### action description 内嵌警告（关键新增）

把原来 350 行提示词中的危险模式警告，内嵌到每个 action 的 description 中：

```python
# 改前
"outstock_query": ApiEntry(description="查询出库详情")

# 改后：警告跟着 action 走，被选中才出现，不浪费 token
"outstock_query": ApiEntry(
    description="查询出库详情（⚠️必须传order_id/system_id，仅传日期范围会超时）"
)

"aftersale_list": ApiEntry(
    description="售后工单列表（⚠️不支持system_id筛选，须用order_id或work_order_id）"
)
```

**效果**：单个工具的 token 从 ~2,600 字降到 ~600 字。

---

## 五、改动 2：双层智能筛选 + 兜底

### 第一层：筛工具（22 → ~8）

```
用户："SEVENTEENLSG01-01 库存多少"
  ↓
分词 → ["SEVENTEENLSG01-01", "库存", "多少"]
  ↓
匹配工具 tags（子串匹配：tag in user_input or tag in synonym_words）：
  "库存" → 命中 local_stock_query、erp_product_query
  编码格式 → 命中 local_product_identify
  ↓
排序规则：先 priority（本地排前面），同 priority 再按命中数
  1. local_product_identify   ← priority=1
  2. local_stock_query        ← priority=1
  3. erp_product_query        ← priority=2
  + 6 个常驻工具
  ↓
AI 看到 ~8 个筛选工具 + 6 个常驻 = ~14 个（比原来 22 个少，且排好序）
```

### 第二层：筛 action（35 → ~8）

```
erp_product_query 被选中后：
  ↓
用匹配词做子串匹配 35 个 action 的 description：
  "库存" in "查询库存数量和状态（总库存/可售/锁定/预占...）" → stock_status ✓
  "库存" in "仓库库存分布" → warehouse_stock ✓
  "库存" in "商品列表" → ✗，不匹配
  其余 action → 不匹配 → 不给 AI 看
  ↓
AI 看到 erp_product_query(action: enum["stock_status", "warehouse_stock", ...])
  只有 ~4 个 action，不是 35 个
```

### 兜底：AI 调了不存在的工具/action → 自动补充

```
AI 工具循环中：
  ↓
AI 返回 tool_call：
  ├─ 工具名在列表中 + action 在列表中 → 正常执行 ✅
  ├─ 工具名不在列表中 → 触发工具扩充 🔄
  │    检查 executor 是否有该工具 handler
  │    ├─ 有 → 补充到列表，抛 ToolExpansionNeeded 异常，重跑这一轮
  │    └─ 没有 → 真的不存在，返回错误
  └─ action 不在列表中 → 触发 action 扩充 🔄
       检查 registry 是否有该 action
       ├─ 有 → 补充到 enum，抛 ToolExpansionNeeded 异常，重跑这一轮
       └─ 没有 → 真的不存在，返回错误

工具扩充和 action 扩充各最多触发 1 次（独立计数），防止无限循环。
```

**为什么这样设计**：
- 不靠 AI 的文字内容判断"缺不缺工具"（不可靠）
- 直接看 AI 的行为——它调了一个不存在的工具名/action，100% 说明它需要
- 用异常机制（ToolExpansionNeeded）而非返回字符串，agent_loop_tools.py 的 `_process_tool_call()` 可以清晰捕获并重跑

---

## 六、完整流程图

```
用户发消息
  ↓
Phase 1：AI 判断意图（不变）
  → 聊天 / ERP查询 / 爬虫 / 图片 / 视频 / 追问
  ↓
Phase 2：工具循环（优化部分，替换原 build_domain_tools()）
  ↓
┌──────────────────────────────────────────────────┐
│ Step 1：双层筛选（代码做，~1ms，不消耗 AI）        │
│                                                  │
│  用户输入 + 同义词扩展                             │
│    ↓                                             │
│  第一层：子串匹配工具 tags → 按 priority 排序       │
│    ↓                                             │
│  第二层：每个选中的工具，筛 action → 只保留匹配的    │
│    ↓                                             │
│  + 常驻工具（6 个）                                │
│    ↓                                             │
│  Level 3 兜底：命中 < 3 → qwen-turbo 语义补充      │
│    ↓                                             │
│  生成精简提示词（~40 行核心规则）                    │
└──────────────────────────────────────────────────┘
  ↓
┌──────────────────────────────────────────────────┐
│ Step 2：AI 选工具执行（多轮循环）                   │
│                                                  │
│  轮次 1：AI 选工具 → 执行 → 结果回传               │
│  轮次 2：AI 继续选 → 执行 → 结果回传               │
│  ...                                             │
│  轮次 N：数据够了 → route_to_chat 汇总回复          │
│                                                  │
│  ⚡ 如果 AI 调了不在列表的工具/action               │
│  → ToolExpansionNeeded 异常 → 补充到列表 → 重跑     │
│  → 工具/action 各最多 1 次                         │
└──────────────────────────────────────────────────┘
  ↓
AI 汇总所有数据 → 回复用户
```

---

## 七、用实际场景走一遍

### 场景 1：简单查询

```
用户："SEVENTEENLSG01-01 库存多少"

Step 1 筛选：
  工具：local_product_identify、local_stock_query、erp_product_query + 常驻
  action：stock_status、warehouse_stock（erp_product_query 只保留这 2 个）

Step 2 AI 执行：
  轮次 1：local_product_identify(code="SEVENTEENLSG01-01") → 识别出商品
  轮次 2：local_stock_query(product_code="SEVENTEENLSG01") → 返回库存
  轮次 3：route_to_chat → 汇总回复

  总耗时 ~1 秒（3 轮 AI 调用 + 本地查询毫秒级）
```

### 场景 2：多任务查询

```
用户："SEVENTEENLSG01-01 库存多少，顺便看下退货情况"

Step 1 筛选：
  同义词扩展 → "退" → ["售后", "退货", "退款"]
  匹配 → ["库存", "退货", "售后", "退款"]
  工具：local_product_identify、local_stock_query、local_aftersale_query、erp_product_query、erp_aftersales_query + 常驻
  → "库存"和"退货"都命中了 ✅

Step 2 AI 执行：
  轮次 1：local_product_identify → 识别商品
  轮次 2：local_stock_query → 库存数据
  轮次 3：local_aftersale_query → 退货数据
  轮次 4：route_to_chat → 汇总回复
```

### 场景 3：兜底扩充（action 级）

```
用户："帮我看下 ABC123 的物流到哪了"

Step 1 筛选：
  同义词扩展 → "到哪了" → ["物流", "快递"]
  工具：local_product_identify、local_doc_query、erp_trade_query + 常驻
  action：erp_trade_query 只保留了 order_list、outstock_query
         但 express_query（物流轨迹）被筛掉了（description 里有"物流"但权重不够）

Step 2 AI 执行：
  轮次 1：local_product_identify → 识别编码
  轮次 2：AI 调 erp_trade_query(action="express_query")
          → express_query 不在当前 action 列表中！
          → 检查 registry 有这个 action → 有
          → 抛 ToolExpansionNeeded → 补充 express_query 到 enum → 重跑
  轮次 2（重跑）：erp_trade_query(action="express_query") → 物流信息 ✅
  轮次 3：route_to_chat → 回复
```

### 场景 4：兜底扩充（工具级）

```
用户："帮我看下这个品牌有哪些商品"

Step 1 筛选：
  Level 1+2 只命中了 local_product_identify（"品牌"没在本地工具 tags 里）
  命中 < 3 → 触发 Level 3：qwen-turbo 语义匹配
  qwen-turbo 返回 → erp_product_query（品牌相关）
  补充到工具列表

Step 2 AI 执行：正常流程
```

---

## 八、排序规则

```python
# ✅ priority 优先 → 本地始终排前面，同级再按命中数
scored.sort(key=lambda x: (priority, -hits))
```

示例：

| 工具 | priority | 命中数 | 排序结果 |
|------|----------|--------|---------|
| local_stock_query | 1 | 1 | 第 1 |
| local_product_identify | 1 | 1 | 第 2 |
| erp_product_query | 2 | 3 | 第 3 |

即使远程工具命中数更高，本地工具仍排前面。

---

## 九、数据对比

### AI 工作量

| 指标 | 现在 | 优化后 | 变化 |
|------|------|--------|------|
| 工具数 | 22 个 | ~8 筛选 + 6 常驻 | -36% |
| action 数 | 178 个 | ~8 个/工具 | -72% |
| 总文字量 | ~7,700 字 | ~2,000 字 | **-74%** |
| 提示词 | 350 行 | ~40 行 | -89% |

### 用户体验

| 指标 | 现在 | 优化后 |
|------|------|--------|
| 商品查询速度 | 2-5 秒 | 5-50 毫秒 |
| 工具选对率 | ~70% | ~90%+ |
| 多任务查询 | 可能漏工具 | 兜底自动补充 |

---

## 十、提示词精简

### 精简策略（三层覆盖，73 条规则零遗漏）

```
原始 350 行提示词拆解：
  A. 工具路由指导（~80行）→ 被工具筛选算法替代 ✅ 删除
  B. 本地/远程优先级（~40行）→ 被 priority 排序替代 ✅ 删除
  C. 输入→工具映射（~30行）→ 被 tags 匹配替代 ✅ 删除
  D. 危险模式警告（~40行）→ 内嵌到 action description 🔒 迁移
  E. 跨工具业务逻辑（~50行）→ 保留在精简提示词中 🔒 保留
  F. 参数陷阱（~40行）→ 被两步模式 param doc 覆盖 ✅ 删除
  G. 归档/回退规则（~20行）→ 保留在精简提示词中 🔒 保留
  H. 行为约束（~30行）→ 保留在精简提示词中 🔒 保留
  I. 状态码/类型映射（~20行）→ 被 param doc enum 覆盖 ✅ 删除
```

### 现在 → 优化后

```
现在：~14,970 字 ≈ 3,663 tokens
优化：~1,600 字 ≈ 400 tokens（降 89%）
```

### 精简后的核心规则（~40 行，全部内容）

```
## 工作模式
1. 两步查询：先传 action 拿参数文档 → 再传 params 执行
2. 简单统计（如"今天多少单"）可直接传 params

## 编码识别
- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型
- 套件(type=1/2)无独立库存 → 查子单品逐个查
- 同一编码每会话只识别一次

## 时间类型
- "多少订单" → time_type=created
- "发了多少" → time_type=consign_time
- 不传 time_type 默认是 modified（通常不是用户想要的）

## 销量计算
- 销量 = sum(每条记录的 num 字段)，不是记录条数

## 售后跨工具
- 默认 → aftersale_list
- 淘宝/天猫 → erp_taobao_query(refund_list)
- 退仓 → refund_warehouse（必传 time_type）

## 归档数据
- 老订单查不到 → query_type=1（归档）
- 老采购查不到 → 换 _history action + 必传 start_date/end_date

## 中继键
- local_doc_query 返回的 sid/order_no/outer_id → 直接用于 API 跨查
- 物流轨迹 → express_query(system_id=sid)
- 操作日志 → order_log(system_ids=sid)

## 同步触发
- 查不到 + 同步状态有⚠ → trigger_erp_sync → 重查
- 查不到 + 同步正常 → 数据确实不存在，告知用户
- 商品/库存查不到 → 不用触发（identify 自动回退 API）

## 规则
- 禁止猜测参数类型，不确定时 ask_user
- 名称搜索无结果 → 必须 ask_user 确认，禁止返回"未找到"
- 数据采集完毕 → route_to_chat 汇总回复
```

### 14 条危险模式 → 内嵌到 action description

不在提示词里，而是嵌入 registry 的 ApiEntry.description 中。只有被选中的 action 才会出现：

```python
# 需要内嵌警告的 action（在 registry 各文件中修改）：
"outstock_query":    "⚠️必须传order_id/system_id，仅传日期范围会超时"
"aftersale_list":    "⚠️不支持system_id筛选，须用order_id或work_order_id"
"stock_in_out":      "⚠️必须传outer_id，否则返回全量数据"
"refund_warehouse":  "⚠️必传time_type参数"
"history_cost_price":"⚠️必传item_id+sku_id"
"batch_stock_list":  "⚠️必传shop_id"
"order_log":         "⚠️只接受system_ids，需先从order_list获取"
# ... 其余 7 条见实施时逐一补充
```

---

## 十一、tags 设计

### ERP 本地工具（10 个）

| 工具 | tags | priority |
|------|------|----------|
| local_product_identify | 商品, 编码, SKU, 条码, 名称, 识别 | 1 |
| local_stock_query | 库存, 可售, 锁定, 预占, 仓库 | 1 |
| local_order_query | 订单, 下单, 买家, 发货 | 1 |
| local_purchase_query | 采购, 采购单, 供应商, 进货 | 1 |
| local_aftersale_query | 售后, 退货, 退款, 换货, 补发 | 1 |
| local_doc_query | 单据, 单号, 快递号, 流水 | 1 |
| local_product_stats | 统计, 趋势, 销量, 对比, 报表 | 1 |
| local_product_flow | 全链路, 流转, 采购到销售 | 1 |
| local_global_stats | 今天多少单, 排名, 平台对比 | 1 |
| local_platform_map_query | 平台映射, 淘宝链接, 店铺商品 | 1 |

### ERP 远程工具（8 个）

| 工具 | tags | priority |
|------|------|----------|
| erp_product_query | 商品, SKU, 库存, 品牌, 分类 | 2 |
| erp_trade_query | 订单, 出库, 物流, 快递, 波次 | 2 |
| erp_purchase_query | 采购, 收货, 上架, 供应商 | 2 |
| erp_aftersales_query | 售后, 退货, 维修, 工单 | 2 |
| erp_warehouse_query | 仓库, 调拨, 盘点, 货位 | 2 |
| erp_info_query | 店铺, 仓库列表, 标签, 客户 | 2 |
| erp_taobao_query | 淘宝, 天猫, 奇门 | 2 |
| erp_execute | 修改, 更新, 创建, 标记 | 2 |

### 常驻工具（6 个，始终包含）

| 工具 | 作用 | always_include |
|------|------|---------------|
| code_execute | 代码沙盒执行 | True |
| erp_api_search | API 文档搜索 | True |
| search_knowledge | 知识库检索 | True |
| get_conversation_context | 获取会话历史 | True |
| route_to_chat | 汇总回复 | True |
| ask_user | 追问用户 | True |

### 注意：无 action 的工具

以下工具没有 action 枚举，只做工具层筛选，不做 action 筛选：
- 全部 10 个本地工具（直接查数据库，参数固定）
- social_crawler（平台/关键词，参数固定）
- code_execute（代码+描述，参数固定）

---

## 十二、筛选算法（三级匹配）

### 匹配架构

```
用户输入："在途跟得上吗"
  ↓
Level 1：同义词表扩展（精确匹配，~0.1ms）
  "跟得上" 在同义词表中 → 扩展出 ["库存", "销量"]
  ↓
Level 2：tags 子串匹配（快速过滤，~1ms）
  用扩展后的词做子串匹配工具 tags 和 action description
  ↓
Level 3：qwen-turbo 语义匹配（兜底补充，~200ms）
  Level 1+2 命中不够时（< 3 个工具），用 qwen-turbo 从候选列表中选工具
  ↓
三级结果合并去重 → 按 priority 排序 → Top 8
```

### 为什么要三级

| 场景 | Level 1 同义词 | Level 2 tags | Level 3 qwen-turbo |
|------|--------------|-------------|---------------------|
| "库存多少" | 不需要扩展 | "库存"直接命中 ✅ | 不需要 |
| "卖了多少" | "卖"→"销量","订单" ✅ | 用扩展词命中 ✅ | 不需要 |
| "跟得上吗" | "跟得上"→"库存","销量" ✅ | 用扩展词命中 ✅ | 不需要 |
| "帮我看下这个" | 无命中 | 无命中 | qwen-turbo 语义理解 ✅ |

**Level 1+2 覆盖 90%，Level 3 兜底 10% 长尾。**

### Level 1：同义词表

```python
# 手写核心业务同义词（~50 条覆盖 80% 场景）
BUSINESS_SYNONYMS = {
    # 动词 → 业务关键词
    "卖": ["销量", "订单", "出库"],
    "退": ["售后", "退货", "退款"],
    "买": ["采购", "进货"],
    "发": ["发货", "物流", "快递"],
    "到": ["物流", "快递", "签收"],
    "赚": ["利润", "毛利", "成本"],
    "亏": ["利润", "成本", "亏损"],

    # 口语 → 业务关键词
    "跟得上": ["库存", "销量"],
    "缺货": ["库存", "预警", "可售"],
    "爆单": ["订单", "销量", "统计"],
    "到哪了": ["物流", "快递"],
    "多少钱": ["价格", "成本", "金额"],
    "卖得好": ["销量", "排名", "统计"],
    "多少单": ["订单", "统计"],
    "多少": ["统计", "数量"],

    # 简称 → 全称
    "淘宝": ["天猫", "淘宝", "奇门"],
    "拼多多": ["拼多多", "PDD"],
    "抖音": ["抖店", "抖音"],
}

def expand_synonyms(user_input: str) -> set[str]:
    """同义词扩展（子串匹配，零依赖）"""
    expanded = set()
    for keyword, synonyms in BUSINESS_SYNONYMS.items():
        if keyword in user_input:
            expanded.update(synonyms)
    return expanded
```

### Level 2：tags + action 子串匹配

```python
def select_tools(domain: str, user_input: str, top_k: int = 8) -> list:
    all_tools = get_domain_tools(domain)

    # Level 1：同义词扩展
    synonym_words = expand_synonyms(user_input)

    # 合并：原始输入 + 同义词扩展
    match_words = {user_input} | synonym_words  # 保持完整字符串，不拆字

    # Level 2：子串匹配工具 tags
    scored = []
    for tool in all_tools:
        if tool.always_include:
            continue  # 常驻工具单独追加
        # 子串匹配：tag 出现在 user_input 或 synonym_words 中
        hits = sum(
            1 for tag in tool.tags
            if tag in user_input or tag in synonym_words
        )
        scored.append((tool.priority, -hits, tool))

    scored.sort()  # priority 优先，同级按命中数
    result = [t for _, _, t in scored[:top_k]]

    # 常驻工具去重追加
    for tool in all_tools:
        if tool.always_include and tool not in result:
            result.append(tool)

    return result
```

### action 筛选（子串匹配 + 权重）

```python
def filter_actions(tool_entry, match_words: set[str], max_actions: int = 8):
    """筛选工具内的 action，只保留相关的"""
    if not tool_entry.actions:
        return tool_entry.schema  # 无 action 的工具直接返回原始 schema

    scored = {}
    for action_name, entry in tool_entry.actions.items():
        score = 0
        for kw in match_words:
            if kw in action_name:
                score += 3  # action 名直接命中（最高权重）
            elif kw in entry.description:
                score += 2  # description 子串命中
        scored[action_name] = score

    # 有命中的按分数排
    hit_actions = [k for k, v in sorted(scored.items(), key=lambda x: -x[1]) if v > 0]

    # 命中太少时兜底补充 top 3 高频 action
    if len(hit_actions) < 3:
        remaining = [k for k in scored if k not in hit_actions][:3 - len(hit_actions)]
        hit_actions.extend(remaining)

    return build_filtered_schema(tool_entry, hit_actions[:max_actions])
```

### Level 3：qwen-turbo 语义匹配（兜底长尾）

Level 1+2 命中不到时，用 qwen-turbo 做语义匹配（不用 embedding/pgvector，零新基础设施）：

```python
async def semantic_tool_match(
    user_input: str,
    candidate_tools: list,
    model: str = "qwen-turbo",
) -> list[str]:
    """Level 1+2 命中不足时，用 qwen-turbo 语义匹配"""
    tool_list = "\n".join(
        f"- {t.name}: {t.description}" for t in candidate_tools
    )
    prompt = (
        f"从以下工具列表中选出与用户问题最相关的工具（只返回工具名，逗号分隔）：\n\n"
        f"用户：{user_input}\n\n"
        f"工具列表：\n{tool_list}"
    )
    # qwen-turbo ~200ms，比 embedding 灵活，理解力更强
    # 超时直接跳过，降级为 Level 1+2 结果
    result = await call_llm(prompt, model=model, timeout=3)
    return parse_tool_names(result)
```

**优势**：
- 零新基础设施（已有 qwen-turbo 配置和 API Key）
- 理解力远超 embedding 余弦相似度（能理解"帮我看下这个"≈"查询"）
- 降级简单：超时/异常 → 直接跳过，用 Level 1+2 结果

### 三级合并流程

```python
async def select_tools_with_semantic(domain: str, user_input: str, top_k: int = 8):
    """三级匹配合并"""

    # Level 1：同义词扩展
    synonym_words = expand_synonyms(user_input)
    match_words = {user_input} | synonym_words

    # Level 2：子串匹配
    all_tools = get_domain_tools(domain)
    non_always = [t for t in all_tools if not t.always_include]
    scored = []
    for tool in non_always:
        hits = sum(1 for tag in tool.tags if tag in user_input or tag in synonym_words)
        scored.append((tool.priority, -hits, hits, tool))
    scored.sort(key=lambda x: (x[0], x[1]))
    level2_result = [t for _, _, _, t in scored[:top_k]]

    # Level 3：qwen-turbo 语义补充（Level 2 命中不足时触发）
    matched_count = sum(1 for _, _, hits, _ in scored[:top_k] if hits > 0)
    if matched_count < 3:
        try:
            semantic_names = await semantic_tool_match(user_input, non_always)
            for name in semantic_names:
                tool = TOOL_REGISTRY.get(name)
                if tool and tool not in level2_result:
                    level2_result.append(tool)
        except Exception:
            pass  # 降级为 Level 1+2 结果

    # 常驻工具追加
    always_tools = [t for t in all_tools if t.always_include]
    for tool in always_tools:
        if tool not in level2_result:
            level2_result.append(tool)

    return level2_result
```

### 兜底扩充（ToolExpansionNeeded 异常机制）

```python
class ToolExpansionNeeded(Exception):
    """AI 调了不在筛选列表但系统支持的工具/action，需要扩充后重跑"""
    pass

def validate_and_expand(tool_call, current_tools, current_actions, expand_state):
    """在 _process_tool_call() 中调用，检查工具/action 是否需要扩充"""
    tool_name = tool_call["function"]["name"]
    args = json.loads(tool_call["function"]["arguments"])
    action = args.get("action")

    # 工具不在筛选列表 → 尝试扩充
    if tool_name not in {t.name for t in current_tools}:
        if not expand_state["tool_expanded"] and executor.has_handler(tool_name):
            new_tool = TOOL_REGISTRY.get(tool_name)
            if new_tool:
                current_tools.append(new_tool)
                expand_state["tool_expanded"] = True
                raise ToolExpansionNeeded(f"补充工具: {tool_name}")
        return f"未知工具: {tool_name}"

    # action 不在筛选列表 → 尝试扩充
    if action and action not in current_actions.get(tool_name, []):
        if not expand_state["action_expanded"]:
            from services.kuaimai.registry import TOOL_REGISTRIES
            registry = TOOL_REGISTRIES.get(tool_name, {})
            if action in registry:
                current_actions[tool_name].append(action)
                expand_state["action_expanded"] = True
                raise ToolExpansionNeeded(f"补充 action: {tool_name}.{action}")
        return f"未知 action: {action}"

    return None  # 校验通过
```

---

## 十三、改动 3：废弃 v1，全量走 v2

### 为什么废弃

- v2（Phase 1 意图路由 + Phase 2 域内工具）已稳定运行
- `agent_loop_v2_enabled` 默认 True，v1 代码路径无流量
- 保留 v1 增加维护成本，新工具筛选只在 v2 生效

### 要删除的

| 文件/代码 | 说明 |
|-----------|------|
| `services/agent_loop.py` 中 `_execute_loop_v1()` | v1 核心循环（~145行） |
| `services/agent_loop.py` 中 v1 分支判断 | `if not v2_enabled` 逻辑 |
| `config/agent_tools.py` 中 `AGENT_TOOLS` | v1 全量工具列表 |
| `config/agent_tools.py` 中 `AGENT_SYSTEM_PROMPT` | v1 系统提示词 |
| `core/config.py` 中 `agent_loop_v2_enabled` | 功能开关 |
| `services/model_search.py` | 废弃工具（v2 Phase 1 自动选模型） |
| `tool_executor.py` 中 `model_search` handler | 废弃 handler |
| `tool_executor.py` 中 `web_search` handler | 废弃（v2 搜索走 intent_router） |
| `tests/test_agent_loop.py` | v1 专属测试（整文件） |
| `tests/test_agent_loop_history.py` | v1 测试 |
| `tests/test_agent_loop_signals.py` | v1 测试 |
| `docs/document/TECH_工具编排_AgentLoop.md` | v1 架构文档 |

### 必须保留的（v2 共用）

| 文件 | 原因 |
|------|------|
| `services/agent_loop.py` 的 `__init__`, `run()` | 入口（改为直接调 v2） |
| `services/agent_loop_tools.py` | Phase 2 用 `_process_tool_call()` |
| `services/agent_loop_infra.py` | `_call_brain()` 等共享基础设施 |
| `services/agent_context.py` | 消息构建，全共享 |
| `config/agent_tools.py` 的 ROUTING_TOOLS, INFO_TOOLS | Phase 2 校验用 |

### 需要更新的

| 文件 | 改动 |
|------|------|
| `services/agent_loop.py` | `_execute_loop()` 去掉 if/else，直接调 v2 |
| `tests/test_agent_loop_infra.py` | 移除 v1 专属 mock |
| `tests/test_agent_tools.py` | 移除对 `AGENT_TOOLS` 常量的引用 |

---

## 十四、实施计划

### 第一步（2 天）：统一注册 + 同义词表 + v1 清理

| 文件 | 做什么 |
|------|--------|
| `config/tool_registry.py`（新建） | ToolEntry 定义 + 全局注册表 + 同义词表 BUSINESS_SYNONYMS |
| `config/erp_tools.py` | ERP 远程工具改为 ToolEntry，加 tags |
| `config/erp_local_tools.py` | 本地工具改为 ToolEntry，加 tags + priority=1 |
| `config/crawler_tools.py` | crawler 改为 ToolEntry |
| `config/code_tools.py` | code 改为 ToolEntry |
| `config/agent_tools.py` | 删 AGENT_TOOLS/AGENT_SYSTEM_PROMPT，保留 ROUTING/INFO_TOOLS |
| `services/agent_loop.py` | 删 v1 路径，去掉 v2_enabled 判断 |
| `core/config.py` | 删 agent_loop_v2_enabled |
| `services/model_search.py` | 删除文件 |
| `services/tool_executor.py` | 删 model_search/web_search handler |
| v1 测试文件（3 个） | 删除 |
| v1 架构文档 | 删除 |

验证：所有 v2 测试通过，功能不变。

### 第二步（3-4 天）：三级筛选 + 兜底 + 提示词精简

| 文件 | 做什么 |
|------|--------|
| `services/tool_selector.py`（新建） | 三级匹配（同义词 + tags + qwen-turbo）+ action 筛选 |
| `services/agent_loop_v2.py` | Phase 2 工具加载改为 select_tools_with_semantic() |
| `services/agent_loop_tools.py` | 加 ToolExpansionNeeded 兜底拦截 |
| `services/tool_executor.py` | 加 `has_handler()` 方法 |
| `config/phase_tools.py` | build_domain_tools() → 调用 tool_selector |
| `config/erp_tools.py` | 提示词精简为 ~40 行 ERP_CORE_PROMPT |
| `config/erp_local_tools.py` | 删除 LOCAL_ROUTING_PROMPT |
| registry 各文件 | action description 内嵌 14 条警告 |

验证：
- 工具数 22 → ~8+6，action 数 178 → ~8/工具
- 总 token 降 74%+
- Level 1："卖了多少" → 同义词扩展 → 命中订单工具 ✅
- Level 2："库存多少" → tags 子串匹配命中 ✅
- Level 3："帮我看下这个" → qwen-turbo 语义匹配命中 ✅
- 兜底：AI 调不存在的工具/action → ToolExpansionNeeded → 自动补充 ✅
- 多任务查询正常 ✅

### 第三步（持续）：监控调优

- 三级匹配各层命中率（哪些查询靠 Level 1/2/3 命中）
- 同义词表补充（根据 Level 3 触发频率，高频的加入同义词表，逐步减少 LLM 调用）
- 兜底触发频率
- 提示词效果 A/B 对比

---

## 十五、文件影响范围

### 新建（2 个文件）

| 文件 | 内容 | 行数 |
|------|------|------|
| `config/tool_registry.py` | ToolEntry + 全局注册表 + 同义词表 | ~150 行 |
| `services/tool_selector.py` | 三级匹配 + action 筛选 + 兜底 | ~200 行 |

### 修改（12 个文件）

| 文件 | 改动量 | 步骤 |
|------|--------|------|
| `config/erp_tools.py` | 中改（ToolEntry + 提示词精简） | 1+2 |
| `config/erp_local_tools.py` | 中改（ToolEntry + 删提示词） | 1+2 |
| `config/crawler_tools.py` | 小改（ToolEntry） | 1 |
| `config/code_tools.py` | 小改（ToolEntry） | 1 |
| `config/agent_tools.py` | 中改（删 v1 常量，保留共享部分） | 1 |
| `config/phase_tools.py` | 小改（接入 tool_selector） | 2 |
| `services/agent_loop.py` | 中改（删 v1 路径） | 1 |
| `services/agent_loop_v2.py` | 中改（接入 tool_selector + 兜底） | 2 |
| `services/agent_loop_tools.py` | 小改（ToolExpansionNeeded 拦截） | 2 |
| `services/tool_executor.py` | 小改（删废弃 handler + 加 has_handler） | 1+2 |
| `core/config.py` | 小改（删 v2_enabled） | 1 |
| registry 各文件 | 小改（action description 内嵌警告） | 2 |

### 删除（7 个文件）

| 文件 | 原因 |
|------|------|
| `services/model_search.py` | v2 不使用 |
| `tests/test_agent_loop.py` | v1 测试 |
| `tests/test_agent_loop_history.py` | v1 测试 |
| `tests/test_agent_loop_signals.py` | v1 测试 |
| `docs/document/TECH_工具编排_AgentLoop.md` | v1 架构文档 |
| config/agent_tools.py 中 `AGENT_TOOLS` | v1 工具列表（代码删除，非删文件） |
| config/agent_tools.py 中 `AGENT_SYSTEM_PROMPT` | v1 提示词（代码删除，非删文件） |

### 不动

- 快麦 API 核心（dispatcher.py, registry/base.py, param_mapper.py, param_doc.py）
- 本地查询实现（erp_local_*.py）
- 数据同步（erp_sync_*.py）
- 企微机器人（wecom/*）— 共用 AgentLoop，自动生效
- API 层（message.py, chat_routing_mixin.py）— import AgentLoop 不变

---

## 十六、风险和兜底

| 风险 | 兜底方案 |
|------|---------|
| 同义词表覆盖不全 | Level 3 qwen-turbo 语义匹配兜底 |
| tags 匹配漏了工具 | 常驻工具 + qwen-turbo 补充 + 兜底扩充 |
| action 筛选漏了关键 action | ToolExpansionNeeded → 自动补充（action 扩充 1 次） |
| qwen-turbo 超时/不可用 | 降级为 Level 1+2（同义词 + tags），仍覆盖 90% |
| 筛选全部失败 | 降级为全量加载（现有逻辑不变） |
| 多任务工具不够 | 兜底扩充：工具 1 次 + action 1 次（独立计数） |
| 提示词精简后规则遗漏 | 40 行核心规则 + action description 内嵌警告，73 条零遗漏 |
| v1 移除后异常 | Mixin 架构保证共享代码不受影响，v2 测试全覆盖 |
