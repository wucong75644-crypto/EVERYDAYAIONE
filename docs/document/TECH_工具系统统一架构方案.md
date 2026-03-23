# TECH_工具系统统一架构方案

> **版本**：V4.0 | **日期**：2026-03-23 | **状态**：方案确认

---

## 一、一句话总结

**现在**：AI 每次要翻 350 页说明书从 18 个工具里选一个。
**优化后**：系统自动挑 8 个最相关的工具给 AI，不够时自动补充。

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

1. **AI 选错工具** — 18 个工具眼花，本地工具和远程工具分不清
2. **查询慢** — 选了远程 API（2-5 秒）而不是本地数据库（5 毫秒）
3. **查不到** — 远程 API 没有的数据，本地数据库有但没用上
4. **维护难** — 新增工具要改 3-4 个文件 + 改提示词

---

## 三、优化方案

### 核心思路：两个改动

```
改动 1：统一工具注册（给每个工具贴标签 + 标优先级）
改动 2：智能筛选 + 兜底扩充（AI 只看相关工具，不够时自动补）
```

### 改动 1：统一工具注册

所有工具按统一格式注册，带 tags 和 priority：

```python
{
    name: "local_stock_query",          # 工具名
    domain: "erp",                      # 所属领域
    description: "查询库存数量和状态",    # 一句话描述
    tags: ["库存", "可售", "锁定", "预占"],  # 语义标签（用于筛选匹配）
    priority: 1,                        # 1=本地优先排前面，2=远程排后面
    always_include: False,              # True=始终包含（如 route_to_chat）
}
```

**新增工具只要注册一条**，系统自动筛选、自动排序，不需要改提示词。

### 改动 2：智能筛选 + 兜底扩充

#### 正常流程（95% 的情况）

```
用户："SEVENTEENLSG01-01 库存多少"
  ↓
系统用 tags 匹配用户输入（代码做，不消耗 AI）：
  "库存" → 命中 local_stock_query、erp_product_query
  "编码" → 命中 local_product_identify
  ↓
按 priority 排序，取 Top 8：
  1. local_product_identify   ← priority=1，本地，排前面
  2. local_stock_query        ← priority=1，本地，排前面
  3. erp_product_query        ← priority=2，远程，排后面
  4. local_product_stats      ← 相关性次高
  5. code_execute             ← 常驻（always_include=True）
  6. erp_api_search           ← 常驻
  7. route_to_chat            ← 常驻
  8. ask_user                 ← 常驻
  ↓
AI 看到 8 个工具 → 轻松选对 → 5 毫秒返回结果
```

#### 兜底流程（5% 的极端情况）

```
用户："SEVENTEENLSG01-01 库存多少，顺便看下这个月退货情况"
  ↓
第一轮筛选：匹配到"库存"相关的 8 个工具
  → AI 查了库存 ✅
  → AI 想查退货，但售后工具没在列表里
  → AI 回复："我需要查售后数据但当前没有售后查询工具"
  ↓
系统检测到"工具不足"信号
  ↓
自动用 AI 的回复重新筛选 → 补充售后工具
  ↓
第二轮：AI 用新补充的工具查退货 ✅
  ↓
全部完成 → 汇总回复用户
```

**用户完全无感知**，系统自动处理。

---

## 四、完整流程图

```
用户发消息
  ↓
Phase 1：AI 判断意图（不变，6 个选项）
  → 聊天 / ERP查询 / 爬虫 / 图片 / 视频 / 追问
  ↓
Phase 2：工具循环（优化部分）
  ↓
┌─────────────────────────────────────────────┐
│ Step 1：智能筛选（代码做，不消耗 AI）          │
│                                             │
│  用户输入分词 → 匹配工具 tags                 │
│  → 按 priority 排序（本地排前，远程排后）      │
│  → 取 Top 8 + 常驻工具                       │
│  → 生成精简提示词（~20 行）                   │
└─────────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────────┐
│ Step 2：AI 选工具执行（多轮循环）              │
│                                             │
│  轮次 1：AI 选工具 → 执行 → 结果回传          │
│  轮次 2：AI 继续选 → 执行 → 结果回传          │
│  ...                                        │
│  轮次 N：数据够了 → route_to_chat 汇总回复    │
│                                             │
│  如果 AI 说"缺工具" → 触发兜底扩充            │
│  → 重新筛选补充工具 → 继续循环                │
└─────────────────────────────────────────────┘
  ↓
AI 汇总所有数据 → 回复用户
```

---

## 五、数据对比

### AI 工作量

| 指标 | 现在 | 优化后 | 变化 |
|------|------|--------|------|
| 每次看的工具数 | 18 个 | ~8 个 | -56% |
| 每次读的文字量 | ~7,700 字 | ~3,450 字 | -55% |
| 提示词规则 | 350 行 | ~20 行 | -94% |
| 工具说明 | 178 个 action | ~50 个 action | -72% |

### 用户体验

| 指标 | 现在 | 优化后 | 变化 |
|------|------|--------|------|
| 商品查询速度 | 2-5 秒 | 5-50 毫秒 | 快 100 倍 |
| 工具选对率 | ~70% | ~90%+ | +20% |
| 多任务查询 | 可能漏工具 | 兜底自动补充 | 更可靠 |

### 开发维护

| 指标 | 现在 | 优化后 | 变化 |
|------|------|--------|------|
| 新增一个工具 | 改 3-4 个文件 + 改提示词 | 注册 1 条带标签 | 简单 |
| 新增一个领域 | 写工具 + 写提示词 + 改多文件 | 注册工具 + 标签 | 简单 |

---

## 六、提示词精简

### 现在（每次全量加载）

```
BASE_AGENT_PROMPT           ~170 字
ERP_ROUTING_PROMPT          ~11,000 字   ← 75%，太长
LOCAL_ROUTING_PROMPT        ~3,300 字
CODE_ROUTING_PROMPT         ~500 字
────────────────────────────
总计 ~14,970 字 ≈ 3,663 tokens
```

### 优化后（只保留核心规则）

```python
ERP_CORE_PROMPT = """
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
"""
# ~600 字 ≈ 150 tokens（降 89%）
```

**可以删除的提示词**（功能已由代码保证）：
- 本地/远程工具选择指导 → 智能筛选已排好序
- LOCAL_ROUTING_PROMPT 全部 → 筛选器自动选本地工具
- 高频易混淆场景 → 工具少了不会混淆
- 必填参数陷阱 → 两步模式 Step 1 已返回参数文档

---

## 七、tags 设计示例

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

### 常驻工具

| 工具 | always_include |
|------|---------------|
| code_execute | True |
| erp_api_search | True |
| route_to_chat | True |
| ask_user | True |

### 其他领域

| 工具 | domain | tags | priority |
|------|--------|------|----------|
| social_crawler | crawler | 小红书, 抖音, 搜索, 口碑 | 1 |
| code_execute | code | 计算, 统计, 聚合, Python | 1 |

---

## 八、筛选算法

```python
def select_tools(domain, user_input, top_k=8):
    """根据用户输入筛选最相关的工具"""

    # 1. 获取该领域所有工具
    all_tools = get_domain_tools(domain)

    # 2. 用户输入分词
    user_words = jieba.cut(user_input)

    # 3. 计算每个工具的匹配度 = tags 命中数
    scored = []
    for tool in all_tools:
        hits = len(set(user_words) & set(tool.tags))
        scored.append((hits, tool.priority, tool))

    # 4. 排序：先按命中数降序，再按 priority 升序（本地排前面）
    scored.sort(key=lambda x: (-x[0], x[1]))

    # 5. 取 Top K
    result = [tool for _, _, tool in scored[:top_k]]

    # 6. 确保常驻工具始终包含
    for tool in all_tools:
        if tool.always_include and tool not in result:
            result.append(tool)

    return result
```

### 兜底扩充

```python
async def phase2_loop_with_fallback(domain, user_input, messages):
    """Phase 2 循环 + 工具不足时自动扩充"""

    tools = select_tools(domain, user_input)

    for turn in range(max_turns):
        response = await call_brain(messages, tools)

        # 检查 AI 是否表示工具不足
        if ai_says_need_more_tools(response):
            # 用 AI 的回复重新筛选，补充新工具
            ai_text = extract_text(response)
            extra_tools = select_tools(domain, ai_text)
            for t in extra_tools:
                if t not in tools:
                    tools.append(t)
            continue  # 用扩充后的工具列表重新跑

        # 正常执行工具
        execute_tools(response)

        # 检查是否完成
        if is_routing_decision(response):
            break
```

---

## 九、实施计划

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

### 第二步（2-3 天）：智能筛选 + 兜底 + 提示词精简

| 文件 | 做什么 |
|------|--------|
| `services/tool_selector.py`（新建） | tags 筛选 + 兜底扩充逻辑 |
| `services/agent_loop_v2.py` | 工具加载从全量改为 select_tools() |
| `config/phase_tools.py` | 调整工具构建入口 |
| `config/erp_tools.py` | 提示词精简为 ERP_CORE_PROMPT |
| `config/erp_local_tools.py` | 删除 LOCAL_ROUTING_PROMPT |

验证：
- 工具数从 18 降到 ~8
- 总 token 降 55%
- 多任务查询兜底扩充生效
- 混合意图正常处理

### 第三步（持续）：监控调优

- 工具选择准确率日志
- tags 效果调优
- top_k 参数调优

---

## 十、文件影响范围

### 要改（10 个文件）

| 文件 | 改动量 | 步骤 |
|------|--------|------|
| `config/tool_registry.py` | 新建 ~100 行 | 第一步 |
| `config/erp_tools.py` | 中改 | 第一步+第二步 |
| `config/erp_local_tools.py` | 中改 | 第一步+第二步 |
| `config/crawler_tools.py` | 小改 | 第一步 |
| `config/code_tools.py` | 小改 | 第一步 |
| `config/agent_tools.py` | 小改 | 第一步 |
| `services/tool_selector.py` | 新建 ~80 行 | 第二步 |
| `services/agent_loop_v2.py` | 改几行 | 第二步 |
| `config/phase_tools.py` | 小改 | 第二步 |
| 测试文件 | 新增测试 | 第一步+第二步 |

### 不动

- AI 引擎（agent_loop.py, agent_loop_infra.py, agent_loop_tools.py）
- 快麦 API（dispatcher.py, registry/*, param_mapper.py）
- 本地查询（erp_local_*.py）
- 数据同步（erp_sync_*.py）
- 企微机器人（wecom/*）— 共用 AgentLoop，自动生效

---

## 十一、风险和兜底

| 风险 | 兜底方案 |
|------|---------|
| tags 筛选漏了关键工具 | 常驻工具始终包含 + AI 反馈后自动扩充 |
| 筛选服务出错 | 降级为全量加载（回到现在的逻辑） |
| 多任务工具不够 | 兜底扩充自动补充缺失工具 |
| v1 降级 | ToolEntry.to_openai_schema() 自动转格式 |
| 提示词精简后规则遗漏 | 保留核心规则（两步查询/编码识别/时间类型） |
