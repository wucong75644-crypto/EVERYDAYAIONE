# 技术方案：单循环 Agent 架构升级

> **状态**：方案讨论完成，待开发  
> **日期**：2026-04-01（审计补充：2026-04-02）  
> **等级**：A级（核心架构改造）

## 一、背景与目标

### 现状问题
1. **两阶段路由浪费调用**：Phase1 路由 + Phase2 执行，每次请求多 1-2 次 LLM 调用
2. **工具串行执行**：多个工具依次执行，不能并行
3. **ERP 三步调用慢**：Phase1选领域 → Step1拿文档 → Step2传参执行，简单查询要 5 次 LLM 调用
4. **上下文断裂**：AgentLoop 和 ChatHandler 是两个独立大脑，中间通过 search_context 传递，信息丢失
5. **ChatHandler 没有工具循环**：只能"念稿"，不能自主调用工具

### 改造目标
- 简单 ERP 查询：5 次 LLM 调用 → 2 次
- 纯聊天：2 次 → 1 次
- 图片/视频生成：2 次 → 1 次
- 搜索+回答：3 次 → 2 次
- 工具并行执行，用户体感快 1-3 秒

## 二、核心设计思路

### 2.1 单循环 messages 架构（参考 Claude Code）

```
用户消息 + 工具描述 → 大脑（一个LLM） → tool_calls?
  → 是：asyncio.gather 并行执行 → 结果塞回 messages → 继续循环
  → 否：流式输出最终回答 → 完成
```

**关键认知**：每次请求本质是"一个用户做一件事"，不需要路由层，大脑直接看工具描述自己选。

### 2.2 轻量工具集（6-8 个顶层工具）

不是把所有工具塞进 messages，而是保持精简的顶层工具：

| 工具 | 描述 |
|------|------|
| erp_query | 查询ERP数据（库存/订单/商品/采购/售后） |
| erp_api_search | 搜索ERP可用API和参数文档（按需发现） |
| web_search | 搜索互联网 |
| social_crawler | 搜索小红书/抖音/B站 |
| code_execute | 执行Python代码 |
| generate_image | 生成图片 |
| generate_video | 生成视频 |
| search_knowledge | 搜索知识库 |

- ERP 内部的 action 选择通过工具交互完成（和现有两步模式一致）
- erp_api_search 负责"不知道用什么API"的场景（已有实现，复用）

### 2.3 ERP 返回优化：参数文档按需附带

**核心改动**：工具返回结果时，自动附带精简的参数提示（不是全量文档）

```python
# 有 params：执行 + 附带精简提示
result + generate_param_hints(action, params)

# 没 params：返回参数文档（和现在一样）
generate_param_doc(action)

# erp_api_search：返回匹配 + 附带相关参数提示
匹配结果 + 相关参数提示
```

`generate_param_hints()` 的逻辑：
- 已传参数：确认用法正确
- 未传但高频的参数：提示可用
- 低频/分页参数：不返回

### 2.4 工具并行执行

**Claude Code 的做法**：不是无脑全并行，而是按安全性分批：
- 只读工具（查库存、搜索）→ 可并行
- 写操作工具（下单、修改）→ 必须串行

```python
# 参考 Claude Code 的 partitionToolCalls + runTools 模式
def partition_tool_calls(tool_calls):
    """按安全性分批：只读并行，写操作串行"""
    batches = []
    current_batch = []
    current_safe = True
    for tc in tool_calls:
        is_safe = is_concurrency_safe(tc.name)  # 查询类=True, 写操作=False
        if is_safe and current_safe:
            current_batch.append(tc)
        else:
            if current_batch:
                batches.append((current_safe, current_batch))
            current_batch = [tc]
            current_safe = is_safe
    if current_batch:
        batches.append((current_safe, current_batch))
    return batches

# 执行时按批次处理
for is_safe, batch in partition_tool_calls(tool_calls):
    if is_safe:
        results = await asyncio.gather(*[execute_tool(tc) for tc in batch])
    else:
        results = [await execute_tool(tc) for tc in batch]  # 串行
    for tc, result in zip(batch, results):
        messages.append(tool_result(tc.id, result))
```

### 2.5 工具错误处理（参考 Claude Code）

Claude Code 的工具错误不会中断循环，而是把错误信息作为 tool_result 传回给 AI：

```python
try:
    result = await execute_tool(tc)
    messages.append(tool_result(tc.id, result, is_error=False))
except Exception as e:
    messages.append(tool_result(tc.id, f"<tool_use_error>{e}</tool_use_error>", is_error=True))
    # 不中断循环！AI 看到错误后自己决定重试还是换方案
```

### 2.6 循环防护（maxTurns）

```python
MAX_TOOL_TURNS = 10  # Claude Code 也有 maxTurns 限制

for turn in range(MAX_TOOL_TURNS):
    response = await llm.chat(messages, tools)
    if not response.tool_calls:
        break  # AI 不调工具了，输出最终回答
    # ... 执行工具 ...
else:
    # 达到上限，强制输出已有内容
    yield accumulated_text or "操作步骤过多，已达上限"
```

### 2.7 工具安全审查（参考 Claude Code Permission Check）

Claude Code 的请求生命周期中，工具检测（Tool Detection）和工具执行（Tool Execute）之间有一层**权限校验（Permission Check）**。我们的 Web SaaS 场景不需要 Claude Code 那么重的 7 层规则引擎 + AI 分类器 + Hook 系统，但需要基于安全级别的执行前检查。

#### 安全级别定义

| 级别 | 含义 | 执行行为 | 适用工具 |
|------|------|---------|---------|
| `safe` | 只读查询，无副作用 | 直接执行，不通知用户 | 所有 ERP 查询（远程+本地）、搜索类、爬虫、代码执行 |
| `confirm` | 消耗资源（积分等） | 通知用户后执行（不阻塞） | generate_image, generate_video |
| `dangerous` | 写操作，不可逆 | **必须用户确认才执行** | erp_execute, trigger_erp_sync |

#### 工具分级清单

```
safe (22个):
  erp_info_query, erp_product_query, erp_trade_query,
  erp_aftersales_query, erp_warehouse_query, erp_purchase_query, erp_taobao_query,
  local_product_identify, local_stock_query, local_order_query,
  local_purchase_query, local_aftersale_query, local_doc_query,
  local_product_stats, local_product_flow, local_global_stats, local_platform_map_query,
  erp_api_search, search_knowledge, web_search,
  social_crawler, code_execute

confirm (2个):
  generate_image, generate_video

dangerous (2个):
  erp_execute, trigger_erp_sync
```

#### 执行前检查流程

```python
for tool_call in tool_calls:
    level = get_safety_level(tool_call.name)

    if level == "dangerous":
        # 发 TOOL_CONFIRM_REQUEST → 前端弹确认框
        await ws_manager.send(build_tool_confirm_request(
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            description="AI 要执行写操作：取消订单 T123",
        ))
        # 等待用户回复（超时 60 秒 → 视为拒绝）
        confirmed = await wait_for_user_confirmation(tool_call.id, timeout=60)
        if not confirmed:
            messages.append(tool_result(tc, "用户拒绝执行此操作", is_error=True))
            continue  # AI 看到拒绝后自己决定下一步

    elif level == "confirm":
        # 通知用户将消耗积分（不阻塞）
        await ws_manager.send(build_tool_notify(...))

    # 执行工具
    result = await execute_tool(tool_call)
```

#### WebSocket 消息类型

| 类型 | 方向 | 说明 |
|------|------|------|
| `TOOL_CONFIRM_REQUEST` | 后端→前端 | 弹确认框："AI 要取消订单 T123，确认？" |
| `TOOL_CONFIRM_RESPONSE` | 前端→后端 | 用户点确认/拒绝 |

#### 多入口场景处理

| 入口 | 确认方式 |
|------|---------|
| Web 前端 | WebSocket 弹确认框，用户点按钮 |
| 企微机器人 | 发文字消息让用户回复"确认"/"取消" |

#### 边界场景

- **确认超时**：60 秒无回复 → 视为拒绝，AI 收到拒绝信息后自行决策
- **批量工具调用**：同一轮多个 tool_calls 中只有 dangerous 的需要确认，safe 的先并行执行不等待
- **确认结果记录**：确认/拒绝结果作为 tool_result 塞进 messages，AI 知道用户态度可以调整策略
- **新增工具默认级别**：未标记的工具默认为 `safe`（查询类工具占绝大多数）

### 2.8 工具注册重构

现有工具注册围绕两阶段路由设计，改造后以下概念消失：

| 消失的概念 | 原文件 | 原因 |
|-----------|--------|------|
| Phase1 路由工具（route_chat等6个） | phase_tools.py | 没有路由阶段了 |
| build_domain_tools("erp") 按领域加载 | phase_tools.py | 不按领域分了 |
| INFO_TOOLS vs ROUTING_TOOLS 分类 | agent_tools.py | 没有路由工具了 |
| PHASE1_TOOL_TO_DOMAIN 映射 | phase_tools.py | 没有领域概念了 |
| route_to_chat / ask_user 退出工具 | agent_tools.py | 大脑直接回答就是退出 |
| Phase1/Phase2 系统提示词 | phase_tools.py | 只需要一个统一提示词 |
| ROUTER_TOOLS（IntentRouter用） | smart_model_config.py | IntentRouter降级为辅助 |

**新结构**：新增 `config/chat_tools.py`，一个文件、一个列表，包含 6-8 个顶层工具 schema。底层工具构建函数（build_erp_tools等）复用。

## 三、全量改动清单（按执行顺序）

### Phase 1：ERP 返回优化

> 不改架构，先优化工具返回内容

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 1.1 | 新增 generate_param_hints() | services/kuaimai/param_doc.py | 新增函数 | 按需返回精简参数提示 |
| 1.2 | _erp_dispatch 返回附带提示 | services/tool_executor.py | 改返回拼接 | 有 params 时附带 hints |
| 1.3 | erp_api_search 返回附带提示 | services/kuaimai/api_search.py | 改返回拼接 | 搜索结果附带参数提示 |
| 1.4 | 测试验证 | tests/ | 更新 | 确保返回格式正确 |

### Phase 2：ChatHandler 加工具循环 + 并行执行

> 核心改动，让 ChatHandler 从"念稿机"变成"自主 Agent"

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 2.1 | 统一工具注册 | config/chat_tools.py | **新建** | 6-8 个顶层工具的 schema 列表 |
| 2.2 | StreamChunk 加 tool_calls 字段 | services/adapters/types.py | 修改 | **当前 StreamChunk 没有 tool_calls 字段，必须新增** |
| 2.3 | KIE Adapter 解析 tool_calls | services/adapters/kie/chat_adapter.py | 修改 | stream_chat 中解析 tool_use blocks |
| 2.4 | DashScope Adapter 解析 tool_calls | services/adapters/dashscope/chat_adapter.py | 修改 | stream_chat 中解析 tool_calls delta |
| 2.5 | OpenRouter Adapter 解析 tool_calls | services/adapters/openrouter/*.py | 修改 | 同上（如需要） |
| 2.6 | 工具安全级别定义 | config/chat_tools.py | 新增 | safe/confirm/dangerous 三级 + get_safety_level() 查询函数 |
| 2.7 | WebSocket 确认消息类型 | schemas/websocket.py | 修改 | 新增 TOOL_CONFIRM_REQUEST / TOOL_CONFIRM_RESPONSE + builder |
| 2.8 | ChatHandler 加工具循环 | services/handlers/chat_handler.py | **重写核心** | _stream_generate 内加 while + maxTurns 循环 |
| 2.9 | 执行前安全检查 | services/handlers/chat_handler.py | 同上 | dangerous→等确认, confirm→通知, safe→直接执行 |
| 2.10 | 工具分批并行执行 | services/handlers/chat_handler.py | 同上 | partition → 只读并行/写操作串行 |
| 2.11 | 工具错误回传AI | services/handlers/chat_handler.py | 同上 | 错误作为 tool_result(is_error=True) 传回，不中断循环 |
| 2.12 | 工具执行 WebSocket 推送 | services/handlers/chat_handler.py | 修改 | 执行过程发 tool_call / tool_result 消息 |
| 2.13 | 记忆预取集成 | services/handlers/chat_handler.py | 修改 | **迁移 chat_routing_mixin 的 asyncio.gather 记忆并行预取模式，必须保留 return_exceptions=True** |
| 2.14 | 前端处理工具消息 | frontend/src/contexts/wsMessageHandlers.ts | 修改 | 处理 tool_call / tool_result / tool_confirm 消息展示 |

### Phase 3：删除两阶段路由 + 清理

> 简化架构，删除多余代码和概念

**3A. 路由入口简化**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3A.1 | 简化路由逻辑 | api/routes/message.py | 修改 | 删除 _resolve_generation_type 复杂分支，不再区分 smart/非smart |
| 3A.2 | 删除路由 Mixin | services/handlers/chat_routing_mixin.py | **删除** | _route_and_stream 逻辑已在 ChatHandler 内 |
| 3A.3 | 简化上下文构建 | services/handlers/chat_context_mixin.py | 修改 | 删除 _router_system_prompt / _router_search_context 参数 |
| 3A.4 | 简化流式支持 | services/handlers/chat_stream_support_mixin.py | 修改 | 重试逻辑集成到工具循环内 |

**3B. AgentLoop 清理**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3B.1 | 删除 Phase1/Phase2 | services/agent_loop_v2.py | **删除** | Phase1/Phase2 不存在了 |
| 3B.2 | 简化 AgentLoop 主类 | services/agent_loop.py | **大幅简化** | 只保留核心循环，删除 Mixin 组合 |
| 3B.3 | 简化工具处理 | services/agent_loop_tools.py | 修改 | 删除 INFO_TOOLS/ROUTING_TOOLS 区分 |
| 3B.4 | 简化基础设施 | services/agent_loop_infra.py | 修改 | _call_brain 保留，删除 Phase 相关 |
| 3B.5 | 简化结果构建 | services/agent_result_builder.py | 修改 | 不再从路由决策构建，直接从循环输出 |
| 3B.6 | 简化类型定义 | services/agent_types.py | 修改 | AgentResult 字段精简 |

**3C. 工具注册清理**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3C.1 | 删除 Phase 工具定义 | config/phase_tools.py | **删除** | Phase1 路由工具 + Phase2 领域加载全部删除 |
| 3C.2 | 清理工具分类 | config/agent_tools.py | 修改 | 删除 INFO_TOOLS/ROUTING_TOOLS 集合 |
| 3C.3 | 清理工具注册表 | config/tool_registry.py | 修改 | 删除 domain 分组逻辑，保留元数据 |
| 3C.4 | 清理模型配置 | config/smart_model_config.py | 修改 | ROUTER_TOOLS 不再需要，保留模型配置 |
| 3C.5 | 删除工具选择器 | services/tool_selector.py | **删除** | Phase2 领域工具选择不需要了 |

**3D. 意图路由降级**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3D.1 | IntentRouter 降为辅助 | services/intent_router.py | 修改 | 不再作为主路由，保留 retry 辅助功能 |
| 3D.2 | 模型选择简化 | services/model_selector.py | 修改 | 从 Phase1 信号选模型 → 规则引擎或前端直选 |
| 3D.3 | 意图学习适配 | services/intent_learning.py | 修改 | 从路由确认学习 → 从工具使用学习 |
| 3D.4 | 重试服务适配 | services/async_retry_service.py | 修改 | 重试逻辑集成到工具循环，_is_smart_mode 简化 |

**3E. Handler 清理**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3E.1 | ImageHandler 清理 | services/handlers/image_handler.py | 小改 | 删除路由结果相关的 params 引用 |
| 3E.2 | VideoHandler 清理 | services/handlers/video_handler.py | 小改 | 同上 |

**3F. 企微适配**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3F.1 | 企微AI调用适配 | services/wecom/wecom_ai_mixin.py | 修改 | 适配新的 agent loop 接口 |
| 3F.2 | 企微消息服务适配 | services/wecom/wecom_message_service.py | 修改 | 适配新的消息生成流程 |

**3G. 技术债清理（Phase 2 遗留）**

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 3G.1 | ToolExecutor 补注册工具 | services/tool_executor.py | 修改 | 补注册 web_search、generate_image、generate_video 到 _handlers（或在工具循环中特殊路由到对应 Handler） |
| 3G.2 | ChatHandler 拆分超标函数 | services/handlers/chat_handler.py | 重构 | _stream_generate 236行→提取 _run_tool_loop()，文件目标 <500 行 |
| 3G.3 | websocket.py 拆分 | schemas/websocket.py | 重构 | 505行→拆为 websocket_types.py（枚举）+ websocket_builders.py（builder函数） |

### Phase 4：测试全量更新

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 4.1 | 删除 Phase1 解析测试 | tests/test_parse_phase1.py | **删除** | Phase1 不存在了 |
| 4.2 | 删除 Phase 工具测试 | tests/test_phase_tools.py | **删除** | phase_tools.py 删了 |
| 4.3 | 删除路由 Mixin 测试 | tests/test_chat_routing_mixin.py | **删除** | mixin 删了 |
| 4.4 | 删除工具选择器测试 | tests/test_tool_selector.py | **删除** | tool_selector.py 删了 |
| 4.5 | 重写 AgentLoop 测试 | tests/test_agent_loop_v2.py | **重写** | 改名 test_agent_loop.py，适配新架构 |
| 4.6 | 重写 ChatHandler 流式测试 | tests/test_chat_handler_stream.py | 修改 | 加工具循环的测试 |
| 4.7 | 重写重试测试 | tests/test_chat_retry.py | 修改 | 重试在循环内而非路由层 |
| 4.8 | 重写 IntentRouter 测试 | tests/test_intent_router.py | 修改 | 降为辅助功能测试 |
| 4.9 | 重写路由重试测试 | tests/test_intent_router_retry.py | 修改 | 适配新接口 |
| 4.10 | 重写结果构建测试 | tests/test_agent_result_builder.py | 修改 | 适配精简后的 AgentResult |
| 4.11 | 重写模型选择测试 | tests/test_model_selector.py | 修改 | 适配新的选择逻辑 |
| 4.12 | 重写 E2E 测试 | tests/test_v2_e2e_simulation.py | 修改 | 适配单循环架构 |
| 4.13 | 重写 ERP 综合测试 | tests/test_v2_erp_comprehensive.py | 修改 | 适配新流程 |
| 4.14 | 重写工作场景测试 | tests/test_v2_workplace_simulation.py | 修改 | 适配新流程 |
| 4.15 | 重写压力测试 | tests/test_v2_stress_simulation.py | 修改 | 适配新流程 |
| 4.16 | 重写消息路由测试 | tests/test_message_routes.py | 修改 | 适配简化后的路由 |
| 4.17 | 重写意图学习测试 | tests/test_intent_learning.py | 修改 | 适配新的学习入口 |
| 4.18 | 重写图片重试测试 | tests/test_image_retry.py | 修改 | 适配新的重试流程 |
| 4.19 | 重写视频重试测试 | tests/test_video_retry.py | 修改 | 适配新的重试流程 |
| 4.20 | 新增统一工具测试 | tests/test_chat_tools.py | **新建** | chat_tools.py 的测试 |
| 4.21 | 新增工具循环测试 | tests/test_tool_loop.py | **新建** | 工具循环+并行执行的测试 |
| 4.22 | 清理测试脚本 | scripts/test_v2_real_llm*.py | 修改 | 适配新架构 |
| 4.23 | 清理基准脚本 | scripts/benchmark_tool_selector.py | **删除** | tool_selector 删了 |

### Phase 5：上下文管理增强

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 5.1 | messages 自动压缩 | services/handlers/chat_context_mixin.py | 修改 | 对话过长时自动摘要（参考 Claude Code autocompact） |
| 5.2 | 记忆注入优化 | services/memory_service.py | 修改 | 记忆作为 messages 的一部分而非 system prompt |

### Phase 6：前端适配

| 序号 | 任务 | 文件 | 动作 | 说明 |
|------|------|------|------|------|
| 6.1 | 处理工具进度消息 | frontend/src/contexts/wsMessageHandlers.ts | 修改 | 新增 tool_progress 消息处理 |
| 6.2 | ROUTING_COMPLETE 处理 | frontend/src/contexts/wsMessageHandlers.ts | 修改 | 该消息可能不再发送或时机变化 |
| 6.3 | 验证 smart model 流程 | frontend/src/constants/smartModel.ts | 验证 | 确认 SMART_MODEL_ID 端到端正常 |
| 6.4 | 更新 messageSender 测试 | frontend/src/services/__tests__/messageSender.test.ts | 修改 | 适配变化 |

## 四、不改动的部分

| 模块 | 文件 | 说明 |
|------|------|------|
| 工具执行入口 | services/tool_executor.py | 只改返回拼接（Phase 1），执行逻辑不变 |
| ERP API 注册 | services/kuaimai/registry/*.py | 8 个领域注册表完全不动 |
| ERP 调度器 | services/kuaimai/dispatcher.py | 调度逻辑不动 |
| 参数映射 | services/kuaimai/param_mapper.py | 映射逻辑不动 |
| 参数守卫 | services/kuaimai/param_guardrails.py | 校验逻辑不动 |
| 格式化器 | services/kuaimai/formatters/*.py | 输出格式不动 |
| HTTP客户端 | services/kuaimai/client.py | 快麦 API 客户端不动 |
| ERP工具定义 | config/erp_tools.py | schema 定义复用，只是加载方式变了 |
| ERP本地工具 | config/erp_local_tools.py | 完全不动 |
| 爬虫工具定义 | config/crawler_tools.py | 完全不动 |
| 文件工具定义 | config/file_tools.py | 完全不动 |
| 代码工具定义 | config/code_tools.py | 完全不动 |
| Handler 工厂 | services/handlers/factory.py | 仍然按 generation_type 分发 |
| Handler 基类 | services/handlers/base.py | 抽象不变 |
| 数据库模型 | schemas/message.py | GenerationType 枚举保留 |
| 适配器工厂 | services/adapters/factory.py | 模型注册不变 |
| WebSocket 管理器 | services/websocket_manager.py | 广播机制不变 |
| 意图蒸馏 | services/intent_distiller.py | 保留（可选预处理） |
| 超时解析 | services/timeout_resolver.py | 保留 |
| 任务完成服务 | services/task_completion_service.py | 保留 |
| 消息服务 | services/message_service.py | 保留 |
| 批量完成服务 | services/batch_completion_service.py | 保留 |

## 五、关键依赖和风险点

### 5.1 代码审计发现的前置问题（必须先解决）

| 问题 | 现状 | 必须做的事 |
|------|------|-----------|
| **StreamChunk 缺少 tool_calls** | `adapters/types.py` 的 StreamChunk 只有 content/thinking_content/finish_reason/tokens，无 tool_calls 字段 | 新增 `tool_calls: Optional[List[ToolCall]]` 字段 |
| **KIE Adapter 不解析 tool_calls** | `kie/chat_adapter.py` stream_chat() 只提取 content/thinking，完全忽略 tool_use blocks | 加 tool_calls 解析逻辑（Gemini API 返回 functionCall） |
| **DashScope Adapter 不解析 tool_calls** | `dashscope/chat_adapter.py` 同样不解析 tool_calls delta | 加 tool_calls 增量拼接逻辑 |
| **ChatHandler 无工具处理分支** | `chat_handler.py` _stream_generate 只处理 chunk.content 和 chunk.thinking_content | 加 chunk.tool_calls 处理 + while 循环 |
| **WebSocket 无工具消息类型** | `schemas/websocket.py` 有 AGENT_STEP 但无 TOOL_CALL/TOOL_RESULT | 新增类型 + builder 函数 |
| **tool_executor 可并行** | execute() 已是 async，无共享可变状态 | ✅ 确认安全，可直接 asyncio.gather |

### 5.2 迁移过程中的关键依赖

| 依赖 | 说明 | 处理方式 |
|------|------|---------|
| 记忆预取模式 | 现在在 _route_and_stream() 里用 `asyncio.gather(agent_task, memory_task, return_exceptions=True)` 并行预取 | **必须原样保留**：迁移到 ChatHandler._stream_generate() 开头，保持 return_exceptions=True 防止记忆失败阻塞主流程 |
| smart_mode 标记 | _is_smart_mode 贯穿路由/重试/参数 | 简化为 task 参数，不再影响路由分支 |
| _batch_prompts | 多图生成的提示词列表 | 保留在 params 中，generate_image 工具处理 |
| _direct_reply | AgentLoop ask_user 的预生成回复 | 不需要了，大脑直接回答 |
| retry_count | 重试计数在 request_params 中 | 移到 task 级别跟踪 |
| ROUTING_COMPLETE WebSocket消息 | 前端用于切换 loading 状态 | 不再发送，改为 TOOL_CALL/TOOL_RESULT |
| generation_type 与消息存储 | DB 中 generation_params.type 是 JSONB 字段，纯信息性 | **确认安全**：改类型不影响存储和展示 |
| 图片/视频 Handler 独立性 | ImageHandler/VideoHandler 使用不同 Adapter（ImageAdapter），需要预锁积分，异步任务模型 | **不适合改成工具**：保留独立 Handler，通过工具循环中的 generate_image 工具触发 handler.start() |

### 5.3 风险和回退策略

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| 模型工具选择准确率下降 | ERP 查询出错 | 保留 erp_api_search 兜底，AI 不确定就先搜 |
| 工具并行执行冲突 | 两个写操作同时执行 | 参考 Claude Code：每个工具标记 is_concurrency_safe，写操作串行 |
| 图片/视频变工具后延迟 | 多一次 LLM 循环 | 保留 ImageHandler/VideoHandler 独立调用，generate_image 工具内部直接调 handler.start() |
| 企微链路中断 | 企微机器人回复异常 | wecom 适配独立做，可临时走旧路径 |
| 工具循环无限执行 | AI 反复调工具不停 | maxTurns=10 强制退出（Claude Code 也有此机制） |
| 工具执行报错中断对话 | 用户看到错误无回复 | 错误作为 tool_result(is_error=True) 传回 AI，让 AI 自己决定重试或换方案（不中断循环） |

## 六、改动统计

| 动作 | 文件数 | 说明 |
|------|--------|------|
| **删除** | 8 | agent_loop_v2.py, phase_tools.py, chat_routing_mixin.py, tool_selector.py, 4个测试 |
| **新建** | 3 | chat_tools.py, test_chat_tools.py, test_tool_loop.py |
| **重写/大改** | 6 | chat_handler.py, agent_loop.py, message.py路由, agent_loop_tools.py, agent_loop_infra.py, adapters/types.py |
| **修改** | 22+ | KIE adapter, DashScope adapter, context_mixin, stream_support, intent_router, model_selector, websocket.py, wecom(2), 15+测试 |
| **不动** | 35+ | tool_executor, registry, dispatcher, formatters, client, factory |
| **总计** | ~74 文件 |  |

## 七、调用次数对比

| 场景 | 现在 | Phase1后 | 全部完成后 |
|------|------|---------|-----------|
| 简单ERP查询 | 5次 | 4次 | 2次 |
| 复杂ERP（多步） | 7-8次 | 6-7次 | 4-5次 |
| 纯聊天 | 2次 | 2次 | 1次 |
| 搜索+回答 | 3次 | 3次 | 2次 |
| 图片生成 | 2次 | 2次 | 1次 |

## 八、需要更新的旧文档

| 文档 | 说明 |
|------|------|
| docs/document/TECH_意图优先动态工具加载架构.md | 完整描述 Phase1/Phase2 架构，改造后标记为 superseded |
| docs/document/TECH_工具系统统一架构方案.md | 引用 v2_enabled、agent_loop_v2.py、phase_tools.py |
| docs/document/TECH_企业级多租户账号系统.md | 引用 agent_loop_v2.py 和路由决策流程 |
| docs/FLOW_DIAGRAMS.md | 包含 Phase1/Phase2 子图定义 |
| backend/core/config.py | 134行注释引用已移除的 v2 flag，需清理 |

## 九、参考架构

Claude Code 调用链（已克隆到 wucong75644-crypto/claude-code-source）：
- 核心循环：`src/query.ts` — async generator 状态机
- 工具分发：`src/tools/` — 注册 + 权限 + 执行
- 并行执行：`src/services/tools/StreamingToolExecutor.ts`
- 上下文压缩：query.ts 内 snip/microcompact/autocompact
- 工具搜索：`src/tools/ToolSearchTool/` — 按需发现工具（类似我们的 erp_api_search）
