# TECH_工具系统统一架构方案

> **版本**：V4.1 | **日期**：2026-03-23 | **状态**：方案确认

---

## 一、一句话总结

**现在**：AI 每次要翻 350 页说明书从 18 个工具 + 178 个 action 里选。
**优化后**：系统自动挑 ~8 个工具 + ~8 个 action，不够时自动补充。

---

## 二、现状问题

### AI 每次处理的数据量

| 部分 | 数据量 | 占比 |
|------|--------|------|
| 工具说明（18 个工具 + 178 个 action） | ~5,650 字 | 73% |
| 提示词规则 | ~1,300 字 | 17% |
| 用户消息 + 历史 | ~750 字 | 10% |
| **总计** | **~7,700 字** | |

### 导致的问题

1. **AI 选错工具** — 18 个工具眼花，本地和远程分不清
2. **查询慢** — 选了远程 API（2-5 秒）而不是本地数据库（5 毫秒）
3. **查不到** — 远程 API 没数据，本地有但没用上
4. **维护难** — 新增工具要改 3-4 个文件 + 改提示词

---

## 三、优化方案

### 核心思路：两个改动

```
改动 1：统一工具注册（给每个工具和 action 贴标签 + 标优先级）
改动 2：双层智能筛选 + 兜底扩充
        第一层：筛工具（18 → ~8）
        第二层：筛 action（178 → ~8 per tool）
        兜底：AI 调用了不在列表的工具/action 时自动补充
```

---

## 四、改动 1：统一工具注册

### 工具注册格式

```python
{
    name: "local_stock_query",
    domain: "erp",
    description: "查询库存数量和状态",
    tags: ["库存", "可售", "锁定", "预占"],   # 语义标签
    priority: 1,                              # 1=本地优先，2=远程
    always_include: False,                    # True=始终包含
}
```

### action 也有标签（关键新增）

现在 erp_product_query 有 35 个 action，每个 action 在 registry 里已有 description。
用 description 做匹配，筛选后**只保留相关的 action**：

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

**效果**：单个工具的 token 从 ~2,600 字降到 ~600 字。

---

## 五、改动 2：双层智能筛选 + 兜底

### 第一层：筛工具（18 → ~8）

```
用户："SEVENTEENLSG01-01 库存多少"
  ↓
分词 → ["SEVENTEENLSG01-01", "库存", "多少"]
  ↓
匹配工具 tags：
  "库存" → 命中 local_stock_query、erp_product_query
  编码格式 → 命中 local_product_identify
  ↓
排序规则：先 priority（本地排前面），同 priority 再按命中数
  1. local_product_identify   ← priority=1
  2. local_stock_query        ← priority=1
  3. erp_product_query        ← priority=2
  + 常驻工具
  ↓
AI 看到 ~8 个工具
```

### 第二层：筛 action（35 → ~8）

```
erp_product_query 被选中后：
  ↓
用分词结果匹配 35 个 action 的 description：
  "库存" → stock_status ✓、warehouse_stock ✓
  "商品" → product_list ✓、product_detail ✓
  其余 31 个 action → 不匹配 → 不给 AI 看
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
  │    系统自动把该工具加入列表，重跑这一轮
  └─ action 不在列表中 → 触发 action 扩充 🔄
       系统自动把该 action 加入工具的 enum，重跑这一轮

兜底最多触发 1 次，防止无限循环。
```

**为什么这样设计**：
- 不靠 AI 的文字内容判断"缺不缺工具"（不可靠）
- 直接看 AI 的行为——它调了一个不存在的工具名/action，100% 说明它需要

---

## 六、完整流程图

```
用户发消息
  ↓
Phase 1：AI 判断意图（不变）
  → 聊天 / ERP查询 / 爬虫 / 图片 / 视频 / 追问
  ↓
Phase 2：工具循环（优化部分）
  ↓
┌──────────────────────────────────────────────────┐
│ Step 1：双层筛选（代码做，~1ms，不消耗 AI）        │
│                                                  │
│  用户输入分词                                     │
│    ↓                                             │
│  第一层：匹配工具 tags → 按 priority 排序 → Top 8  │
│    ↓                                             │
│  第二层：每个选中的工具，筛 action → 只保留匹配的    │
│    ↓                                             │
│  + 常驻工具（code_execute, route_to_chat, ask_user）│
│    ↓                                             │
│  生成精简提示词（~20 行核心规则）                    │
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
│  → 自动补充到列表（最多 1 次）→ 重跑这一轮          │
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
  分词 → ["库存", "退货"]
  工具：local_product_identify、local_stock_query、local_aftersale_query、erp_product_query + 常驻
  → "库存"和"退货"都命中了，8 个工具够用 ✅

Step 2 AI 执行：
  轮次 1：local_product_identify → 识别商品
  轮次 2：local_stock_query → 库存数据
  轮次 3：local_aftersale_query → 退货数据
  轮次 4：route_to_chat → 汇总回复
```

### 场景 3：兜底扩充

```
用户："帮我看下 ABC123 的物流到哪了"

Step 1 筛选：
  分词 → ["ABC123", "物流"]
  工具：local_product_identify、local_doc_query、erp_trade_query + 常驻
  action：erp_trade_query 只保留了 order_list、outstock_query
         但 express_query（物流轨迹）被筛掉了

Step 2 AI 执行：
  轮次 1：local_product_identify → 识别编码
  轮次 2：AI 调 erp_trade_query(action="express_query")
          → express_query 不在当前 action 列表中！
          → 系统自动补充 express_query 到 enum → 重跑
  轮次 2（重跑）：erp_trade_query(action="express_query") → 物流信息 ✅
  轮次 3：route_to_chat → 回复
```

---

## 八、排序规则（修复漏洞 4）

```python
# ❌ 错误：命中数优先 → 远程工具可能排在本地前面
scored.sort(key=lambda x: (-hits, priority))

# ✅ 正确：priority 优先 → 本地始终排前面，同级再按命中数
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
| 工具数 | 18 个 | ~8 个 | -56% |
| action 数 | 178 个 | ~8 个/工具 | -72% |
| 总文字量 | ~7,700 字 | ~2,000 字 | **-74%** |
| 提示词 | 350 行 | ~20 行 | -94% |

### 用户体验

| 指标 | 现在 | 优化后 |
|------|------|--------|
| 商品查询速度 | 2-5 秒 | 5-50 毫秒 |
| 工具选对率 | ~70% | ~90%+ |
| 多任务查询 | 可能漏工具 | 兜底自动补充 |

---

## 十、提示词精简

### 现在 → 优化后

```
现在：~14,970 字 ≈ 3,663 tokens
优化：~600 字 ≈ 150 tokens（降 96%）
```

### 精简后的核心规则（全部内容）

```
## 工作模式
1. 两步查询：先传 action 拿参数文档 → 再传 params 执行
2. 简单统计（如"今天多少单"）可直接传 params

## 编码识别
- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型
- 套件(type=1/2)无独立库存 → 查子单品逐个查

## 时间类型
- "多少订单" → time_type=created
- "发了多少" → time_type=consign_time

## 规则
- 禁止猜测参数类型，不确定时 ask_user
- 数据采集完毕 → route_to_chat 汇总回复
```

**350 行的规则为什么能删**：
- 工具选择指导 → 智能筛选已排好序，不需要提示词指导
- 本地/远程优先级 → priority 排序保证
- 高频易混淆场景 → 工具少了不会混淆
- 必填参数陷阱 → 两步模式 Step 1 已返回参数文档
- action 选择说明 → action 已筛选，只剩相关的

---

## 十一、tags 设计

### ERP 本地工具

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

### ERP 远程工具

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

### 常驻工具（始终包含）

| 工具 | always_include |
|------|---------------|
| code_execute | True |
| erp_api_search | True |
| route_to_chat | True |
| ask_user | True |

---

## 十二、筛选算法

### 工具筛选

```python
def select_tools(domain, user_input, top_k=8):
    all_tools = get_domain_tools(domain)
    user_words = tokenize(user_input)

    scored = []
    for tool in all_tools:
        hits = len(user_words & set(tool.tags))
        scored.append((tool.priority, -hits, tool))  # priority 优先

    scored.sort()
    result = [tool for _, _, tool in scored[:top_k]]

    # 常驻工具去重后追加
    for tool in all_tools:
        if tool.always_include and tool not in result:
            result.append(tool)

    return result
```

### action 筛选

```python
def filter_actions(tool_entry, user_words, max_actions=8):
    """筛选工具内的 action，只保留和用户输入相关的"""
    if not tool_entry.actions:
        return tool_entry.schema  # 无 action 的工具直接返回

    scored = {}
    for action_name, action_entry in tool_entry.actions.items():
        # 用 action 的 description 匹配用户分词
        desc_words = set(tokenize(action_entry.description))
        hits = len(user_words & desc_words)
        scored[action_name] = hits

    # 取匹配度最高的 Top N action
    top_actions = sorted(scored, key=lambda k: -scored[k])[:max_actions]

    # 生成精简 schema：action enum 只包含 top_actions
    return build_filtered_schema(tool_entry, top_actions)
```

### 兜底扩充

```python
async def execute_with_fallback(tool_calls, current_tools, current_actions):
    """执行工具调用，不存在的工具/action 自动补充"""
    expanded = False

    for tc in tool_calls:
        tool_name = tc["function"]["name"]
        args = json.loads(tc["function"]["arguments"])
        action = args.get("action")

        # 工具不在列表 → 补充工具
        if tool_name not in current_tools:
            new_tool = TOOL_REGISTRY.get(tool_name)
            if new_tool and not expanded:
                current_tools.append(new_tool)
                expanded = True  # 最多补充 1 次
                return "RETRY"   # 重跑这一轮

        # action 不在 enum → 补充 action
        if action and action not in current_actions.get(tool_name, []):
            if not expanded:
                current_actions[tool_name].append(action)
                expanded = True
                return "RETRY"

        # 正常执行
        await execute(tc)
```

---

## 十三、实施计划

### 第一步（2 天）：统一注册

| 文件 | 做什么 |
|------|--------|
| `config/tool_registry.py`（新建） | ToolEntry 定义 + 全局注册表 |
| `config/erp_tools.py` | ERP 远程工具改为 ToolEntry，加 tags |
| `config/erp_local_tools.py` | 本地工具改为 ToolEntry，加 tags + priority=1 |
| `config/crawler_tools.py` | crawler 改为 ToolEntry |
| `config/code_tools.py` | code 改为 ToolEntry |
| `config/agent_tools.py` | v1 兼容适配 |

验证：所有现有测试通过，功能不变。

### 第二步（2-3 天）：双层筛选 + 兜底 + 提示词精简

| 文件 | 做什么 |
|------|--------|
| `services/tool_selector.py`（新建） | 工具筛选 + action 筛选 + 兜底逻辑 |
| `services/agent_loop_v2.py` | 工具加载改为 select_tools() + 兜底循环 |
| `config/phase_tools.py` | 调整工具构建入口 |
| `config/erp_tools.py` | 提示词精简为 ERP_CORE_PROMPT |
| `config/erp_local_tools.py` | 删除 LOCAL_ROUTING_PROMPT |

验证：
- 工具数 18 → ~8，action 数 178 → ~8/工具
- 总 token 降 74%
- 兜底扩充：AI 调不存在的工具/action 时自动补充
- 多任务查询正常

### 第三步（持续）：监控调优

- 工具/action 筛选命中率
- 兜底触发频率
- top_k 参数调优

---

## 十四、文件影响范围

### 要改（10 个文件）

| 文件 | 改动量 | 步骤 |
|------|--------|------|
| `config/tool_registry.py` | 新建 ~100 行 | 第一步 |
| `config/erp_tools.py` | 中改 | 第一步+第二步 |
| `config/erp_local_tools.py` | 中改 | 第一步+第二步 |
| `config/crawler_tools.py` | 小改 | 第一步 |
| `config/code_tools.py` | 小改 | 第一步 |
| `config/agent_tools.py` | 小改 | 第一步 |
| `services/tool_selector.py` | 新建 ~120 行 | 第二步 |
| `services/agent_loop_v2.py` | 改 ~20 行 | 第二步 |
| `config/phase_tools.py` | 小改 | 第二步 |
| 测试文件 | 新增 | 第一步+第二步 |

### 不动

- AI 引擎（agent_loop.py, agent_loop_infra.py, agent_loop_tools.py）
- 快麦 API（dispatcher.py, registry/*, param_mapper.py）
- 本地查询（erp_local_*.py）
- 数据同步（erp_sync_*.py）
- 企微机器人（wecom/*）— 共用 AgentLoop，自动生效

---

## 十五、风险和兜底

| 风险 | 兜底方案 |
|------|---------|
| tags 匹配漏了工具 | 常驻工具 + 兜底自动补充 |
| action 筛选漏了关键 action | 兜底检测 AI 调用不存在的 action → 自动补充 |
| 筛选服务出错 | 降级为全量加载（现有逻辑不变） |
| 多任务工具不够 | 兜底最多扩充 1 次 |
| v1 降级 | to_openai_schema() 自动转格式 |
| 提示词精简后规则遗漏 | 保留核心规则（两步查询/编码识别/时间类型） |
