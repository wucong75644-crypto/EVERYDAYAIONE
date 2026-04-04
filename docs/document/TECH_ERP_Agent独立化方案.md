# 技术方案：ERP Agent 独立化

> **状态**：方案设计中  
> **日期**：2026-04-04  
> **等级**：A级（核心架构改造）  
> **前置**：单循环 Agent 架构（Phase 1+2）✅ + 工具注册七层架构 ✅

## 一、背景

单循环 Agent 工具循环 + 七层架构完成后，benchmark 准确率仅 40-50%。

根因：通用 Agent 直接持有 26 个工具 → AI 注意力分散 → 选错/不选。
旧架构 Phase2 ERP 准确率接近 100%，靠的是：专用提示词 + 同义词匹配 + 工具预过滤。

行业共识：超过 3-5 个业务领域时必须拆多 Agent（Microsoft/LangChain/Oracle 2026 指南）。
Claude Code 也是这个模式：AgentTool → 子 Agent 独立运行 → 返回结果给父 Agent。

## 二、架构设计

### 2.1 整体架构

```
主 Agent（ChatHandler）— 通用路由
  工具列表（6 个）：
  ├─ erp_agent         ← ERP 独立 Agent（核心）
  ├─ web_search        ← 互联网搜索
  ├─ search_knowledge  ← 知识库搜索
  ├─ generate_image    ← 图片生成
  ├─ generate_video    ← 视频生成
  └─ code_execute      ← 代码执行

  主 Agent 只做一件事：判断用户要什么 → 分发给对应工具/Agent
```

### 2.2 ERP Agent 内部架构

```
erp_agent（独立 Agent）
  ├─ 输入：用户原始文本 + 对话历史摘要
  ├─ 专用系统提示词（ERP_ROUTING_PROMPT，~1500 tokens）
  ├─ 同义词预处理（expand_synonyms → 理解"丁单""酷存""够不够卖"）
  ├─ 工具预过滤（3 级选择算法 → 从 17 个工具中选 3-5 个最相关的）
  ├─ 独立工具循环（max 5 轮）
  │   ├─ Turn1: 调 LLM with 过滤后的 3-5 个工具 → 选工具
  │   ├─ Turn2: 执行工具 → 结果回 LLM → 决定下一步
  │   └─ Turn3: 数据采集完毕 → 返回结论
  └─ 输出：结论文本（给主 Agent 用于最终回答）
```

### 2.3 主 Agent 的 erp_agent 工具定义

```python
{
    "type": "function",
    "function": {
        "name": "erp_agent",
        "description": (
            "ERP 数据查询专员。用户问任何涉及订单、库存、采购、售后、"
            "发货、物流、商品、销量、统计、仓储的问题时，调用此工具。"
            "支持口语化表达（'丁单'=订单，'酷存'=库存，'够不够卖'=库存查询）。"
            "内部自动识别编码、选择最优工具、多步查询，返回完整数据结论。"
        ),
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用户的原始问题（保持原文，不要改写）",
                },
            },
        },
    },
}
```

### 2.4 ERP Agent 内部流程（复用旧架构精华）

```python
class ERPAgent:
    """ERP 独立 Agent：专用提示词 + 同义词 + 工具过滤 + 独立循环"""
    
    async def execute(self, query: str, context: dict) -> str:
        # 1. 同义词预处理（复用 tool_selector.expand_synonyms）
        expanded_keywords = expand_synonyms(query)
        
        # 2. 工具预过滤（复用 tool_selector.select_tools 3 级算法）
        all_erp_tools = build_erp_tools()  # 17 个 ERP 工具
        selected_tools = select_tools("erp", query, expanded_keywords, top_k=8)
        
        # 3. 构建消息（专用 ERP 提示词）
        messages = [
            {"role": "system", "content": ERP_SYSTEM_PROMPT},  # 1500 tokens 专用指引
            {"role": "user", "content": query},
        ]
        
        # 4. 独立工具循环（max 5 轮）
        for turn in range(5):
            response = await call_llm(messages, tools=selected_tools)
            
            if not response.tool_calls:
                return response.text  # 数据采集完毕，返回结论
            
            # 执行工具
            for tc in response.tool_calls:
                result = await tool_executor.execute(tc.name, tc.args)
                messages.append(tool_result(tc, result))
            
            # 自动扩展：AI 需要不在列表中的工具
            needed = {tc.name for tc in response.tool_calls}
            missing = needed - {t["function"]["name"] for t in selected_tools}
            if missing:
                extra = get_tools_by_names(missing)
                selected_tools.extend(extra)
        
        # 5. 超时兜底
        return "ERP 查询已完成，以上是查到的数据。"
```

## 三、与旧架构的对应关系

| 旧架构 | 新架构 | 状态 |
|--------|--------|------|
| Phase1 路由判断 "这是 ERP" | 主 Agent 选择 erp_agent 工具 | 更简单（6 选 1 vs 6 选 1） |
| Phase2 ERP_ROUTING_PROMPT | ERPAgent 专用系统提示词 | **直接复用** |
| Phase2 tool_selector 3 级选择 | ERPAgent 内部工具预过滤 | **直接复用** |
| Phase2 expand_synonyms | ERPAgent 同义词预处理 | **直接复用** |
| Phase2 _try_expand_tools | ERPAgent 自动扩展 | **直接复用** |
| Phase2 _call_brain | ERPAgent 内部 LLM 调用 | 复用 adapter |
| Phase2 AgentResult | ERPAgent 返回纯文本给主 Agent | 简化 |

**核心代码 80% 可以直接复用**，不是重写。

## 四、关键设计决策

### 4.1 ERP Agent 的上下文传递

#### 4.1.1 上下文筛选（主 Agent → ERP Agent）

主 Agent 不传全量 messages，而是**筛选 ERP 相关的对话轮次**传入：

```
主 Agent 的完整 messages：
  Turn1: 用户问库存 → AI 调 erp_agent → 返回库存数据    ← ERP 相关
  Turn2: 用户说"画一张猫" → AI 调 generate_image        ← 无关
  Turn3: 用户说"刚才那个商品退货多少"                    ← ERP 相关

筛选后传给 ERP Agent：
  [Turn1: 库存问题 + 回答]     ← 保留
  [Turn3: 当前问题]            ← 保留
  （Turn2 画猫的内容被过滤掉）
```

**筛选规则**：
- user 消息：全部保留（用户说的每句话都可能有 ERP 上下文）
- assistant 消息：有 erp_agent 工具调用的保留，其他跳过
- tool 结果：跟随 assistant 保留
- system 消息：跳过（ERP Agent 有自己的系统提示词）

```python
def filter_erp_context(messages: list) -> list:
    """从主 Agent 的 messages 中筛选 ERP 相关上下文"""
    result = []
    for msg in messages:
        if msg["role"] == "system":
            continue  # ERP Agent 用自己的系统提示词
        if msg["role"] == "user":
            result.append(msg)  # 用户消息全部保留
        elif msg["role"] == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                result.append(msg)  # 纯文字回复保留
            elif any(tc["function"]["name"] == "erp_agent" for tc in tool_calls):
                result.append(msg)  # ERP 相关的 assistant 保留
        elif msg["role"] == "tool":
            result.append(msg)  # tool 结果保留（简化处理）
    return result
```

#### 4.1.2 ERP Agent 内部 messages 拼接

```python
# ERP Agent 收到筛选后的上下文
erp_messages = [
    {"role": "system", "content": ERP_SYSTEM_PROMPT},  # 专用提示词
    *filtered_context,  # 主 Agent 筛选的历史（ERP 相关轮次）
    {"role": "user", "content": query},  # 当前用户问题
]
```

#### 4.1.3 跨 Agent 数据流转

ERP Agent 返回的结果是纯文本，自动进入主 Agent 的 messages：

```
用户: "查一下YSL01库存"
  → 主 Agent 调 erp_agent → 返回 "可售128件"
  → 结果在 messages 里：{"role": "tool", "content": "可售128件"}

用户: "帮我用这个数据做个图表"
  → 主 Agent 调 code_execute → AI 看到 messages 里有 "可售128件"
  → 自动用这个数据生成图表
  → 不需要额外处理，messages 就是天然的跨 Agent 共享通道
```

### 4.2 ERP Agent 的结果格式

返回给主 Agent 的是纯文本结论（自动进入 messages 供后续工具/Agent 使用）：

```
"库存查询结果：
  商品: YSL01-RED-M (YSL 口红 红色)
  可售库存: 128 件
  锁定: 12 件
  在途: 50 件
  
  库存充足，可支撑约 3 天销量。"
```

### 4.3 WebSocket 进度推送

ERP Agent 内部执行工具时，通过 task_id 发送进度消息：

```python
class ERPAgent:
    def __init__(self, task_id, ...):
        self.task_id = task_id  # 从 ChatHandler 传入
    
    async def _execute_tool(self, tool_name, args):
        # 发进度："正在查询库存..."
        await ws_manager.send_to_task_subscribers(
            self.task_id,
            build_agent_step(tool_name=tool_name, status="running", ...)
        )
        result = await executor.execute(tool_name, args)
        return result
```

用户在 ERP Agent 执行期间会看到"正在查询库存..."等进度提示，不会一片空白。

### 4.4 LLM 调用方式

ERP Agent 使用 `create_chat_adapter()` 创建独立 adapter（和 ChatHandler 同一套），
支持企业 BYOK（自带 API Key）：

```python
from services.adapters.factory import create_chat_adapter

adapter = create_chat_adapter(model_id, org_id=self.org_id, db=self.db)
async for chunk in adapter.stream_chat(messages=erp_messages, tools=selected_tools):
    # ... 流式处理
await adapter.close()
```

### 4.5 积分扣费

ERP Agent **不扣费**。内部 LLM 调用的 token 消耗累计后返回给 ChatHandler：

```python
class ERPAgentResult:
    text: str           # 结论文本
    tokens_used: int    # ERP Agent 内部消耗的总 tokens
    turns_used: int     # 内部轮次数

# ChatHandler 统一扣费：
# 主 Agent tokens + ERP Agent tokens = 总消耗 → _calculate_credits()
```

### 4.6 错误处理

ERP Agent 内部 catch 所有异常，失败时返回错误文本（不抛异常）：

```python
async def execute(self, query, context):
    try:
        # ... 内部工具循环 ...
        return ERPAgentResult(text=result, tokens_used=total)
    except Exception as e:
        logger.error(f"ERPAgent error: {e}")
        return ERPAgentResult(
            text=f"ERP 查询出错：{e}。请稍后重试或换个方式提问。",
            tokens_used=tokens_so_far,
        )
```

主 Agent 的 AI 看到错误文本后自己决定怎么回复用户。

### 4.7 主 Agent 什么时候调 erp_agent

**不需要复杂判断**。主 Agent 只有 6 个工具，erp_agent 的描述里写了：
"用户问任何涉及订单、库存、采购、售后、发货、物流、商品、销量、统计的问题"

6 选 1 的准确率远高于 26 选多。即使用最弱的模型也能判断对。

### 4.8 散客用户

散客没有 ERP 功能 → get_chat_tools(org_id=None) 不返回 erp_agent → 主 Agent 看不到这个工具 → 自然不会调用。

## 五、改动清单

### 5.1 新建文件

| 文件 | 说明 |
|------|------|
| services/erp_agent.py | ERP Agent 核心：execute() + 上下文筛选 + 内部工具循环 + 进度推送 |
| tests/test_erp_agent.py | ERP Agent 单元测试 |

### 5.2 修改文件

| 文件 | 改动 |
|------|------|
| config/chat_tools.py | _CORE_TOOLS 改为 7 个（含 erp_agent）+ erp_agent 工具定义 + 主 Agent 系统提示词简化 |
| services/tool_executor.py | 注册 erp_agent handler |
| services/handlers/chat_handler.py | 调 erp_agent 时传入 messages + task_id |

### 5.3 复用不改的文件

| 文件 | 说明 |
|------|------|
| config/tool_registry.py | BUSINESS_SYNONYMS + expand_synonyms() |
| services/tool_selector.py | select_tools() 3 级选择算法 |
| config/erp_tools.py | ERP_ROUTING_PROMPT + build_erp_tools() |
| config/erp_local_tools.py | 本地工具定义 |
| services/tool_executor.py | 工具执行逻辑（ERP Agent 内部复用） |
| services/kuaimai/* | 整个 ERP 执行层不动 |
| services/adapters/* | LLM 适配器（ERP Agent 复用） |

## 六、预期效果

| 指标 | 当前（通用Agent+26工具） | 预期（ERP Agent） | 原因 |
|------|----------------------|------------------|------|
| ERP 准确率 | 40-50% | **90%+** | 专用提示词+同义词+工具过滤 |
| 响应速度 | 2-4 轮 | **1-2 轮** | 工具预过滤，首轮就选对 |
| token 消耗 | ~3000（26 工具 schema） | **~800（3-5 工具）** | 过滤后只传相关工具 |
| 非 ERP 准确率 | 70% | **95%+** | 主 Agent 只有 6 选 1 |
| 跨 Agent 数据流转 | N/A | **✅** | 结果在 messages 里，其他工具/Agent 自动可见 |
| 用户等待体验 | 无进度 | **有进度** | WebSocket 推送工具执行状态 |

## 七、实现步骤

| 步骤 | 任务 | 文件 |
|------|------|------|
| 1 | 新建 ERPAgent 类（上下文筛选 + 同义词 + 工具过滤 + 独立循环 + 进度推送） | services/erp_agent.py |
| 2 | 注册 erp_agent 工具到 chat_tools + ToolExecutor | config/chat_tools.py + tool_executor.py |
| 3 | 主 Agent 系统提示词简化（ERP 规则移到 Agent 内部） | config/chat_tools.py |
| 4 | ChatHandler 调 erp_agent 时传入 messages + task_id | services/handlers/chat_handler.py |
| 5 | 单元测试 | tests/test_erp_agent.py |
| 6 | benchmark 验证（目标 ≥85%） | scripts/test_tool_loop_benchmark.py |
| 7 | 跑全量测试确认无回归 | - |

## 八、ERP Agent 返回结果的上下文控制

### 8.1 问题

ERP Agent 返回的结果塞进主 Agent 的 messages 后，会随后续每次 LLM 调用一起发送。
长对话中多次 ERP 查询的结果累积，导致 token 浪费。

### 8.2 解决：层1 即时精简（本方案实现）

ERP Agent 返回时区分两路输出：

```
给 messages 的（精简结论，~100 tokens）：
  "YSL01 库存充足：可售128件，锁定12件，在途50件。"

给用户的 WebSocket 推送（完整数据，不限长度）：
  "库存详情：
   ├─ 杭州仓：可售80件，锁定8件
   ├─ 北京仓：可售48件，锁定4件
   └─ 在途：50件（预计4月6日到货）"
```

messages 里只存精简结论，用户在界面上看到完整数据。
后续 LLM 调用只带 100 tokens 的结论，不带完整数据。

### 8.3 后续优化（独立方案，不在本次范围）

完整的上下文压缩管理是 ChatHandler 级别的独立方案，适用于所有入口（Web + 企微）：

| 层 | 策略 | 说明 | 状态 |
|---|------|------|------|
| 层1 | 工具结果即时精简 | 返回时就控制长度 | **本方案实现** |
| 层2 | 旧结果延迟清理 | 超过 3 轮的工具结果替换为"[已归档]" | 后续独立方案 |
| 层3 | 摘要滚动覆盖 | messages 超阈值时触发压缩，摘要固定 200 tokens 上限（覆盖不追加） | 后续独立方案 |

企微场景（同一对话永不结束）尤其需要层2+层3，防止摘要无限增长。
