# TECH: Agent 计划模式 v3 — 分析/执行分离架构

> 版本：v3.0 | 日期：2026-04-25 | 状态：已实施

## 1. 架构思想

### 1.1 核心原则

**计划模式是主 Agent 的通用能力，不是某个工具的内部功能。**

主 Agent 面对复杂任务时，先探索再执行。探索通过工具的**分析接口**完成（只分析不执行），
执行通过工具的**执行接口**完成。两者分离，由主 Agent 统一编排。

### 1.2 与 v2.0 的关系

| 维度 | v2.0（三层防御） | v3.0（分析/执行分离） |
|------|----------------|---------------------|
| 定位 | erp_agent 内部防御机制 | 主 Agent 通用工作模式 |
| 触发 | erp_agent 被动检测复杂度 | 主 Agent 主动判断 |
| 覆盖 | 仅 ERP 查询 | 所有工具（ERP、爬虫、代码、图表...） |
| 实现 | L2 短路返回 plan | 独立 analyze 接口 |

v2.0 的 L2 短路代码已删除。PlanBuilder 分析能力保留，通过 `analyze()` 方法暴露。

---

## 2. 两种工作模式

### 2.1 模式判断（提示词驱动）

主 Agent 收到请求后自检：
> **"完成这个任务，后续步骤是否需要前面步骤的结果才能确定怎么做？"**

| 判断 | 模式 | 行为 |
|------|------|------|
| 否——参数都清楚 | **直接模式** | 直接调工具执行 |
| 是——有步骤依赖 | **计划模式** | 探索 → 规划 → 确认 → 执行 |

### 2.2 计划模式流程

```
1. 探索：调 erp_analyze 等分析接口，获取结构化任务拆解
2. 规划：基于分析结果制定执行方案
3. 展示：向用户展示方案，停止工具调用，等确认
4. 执行：用户确认后，逐步调执行接口
5. 汇总：输出完整结论
```

### 2.3 场景覆盖

| 场景 | 模式 | 工具链 |
|------|------|--------|
| 昨天订单汇总 | 直接 | erp_agent |
| 退货率（订单+售后） | 直接 | erp_agent（内部并行） |
| 供应商商品→用编码查订单 | 计划 | erp_analyze → 确认 → erp_agent ×2 |
| 导出数据→生成柱状图 | 计划 | erp_analyze → 确认 → erp_agent → code_execute |
| 查库存不足→创建采购单 | 计划 | erp_analyze → 确认 → erp_agent → erp_execute |

---

## 3. 分析/执行分离

### 3.1 架构

```
工具                  分析接口（计划模式）     执行接口（正常）
─────────────────────────────────────────────────────
erp_agent      →     erp_analyze             erp_agent
                      只跑PlanBuilder          查数据库
                      毫秒级                   秒级

（未来扩展）
social_crawler →     crawler_analyze          social_crawler
code_execute   →     code_analyze             code_execute
```

### 3.2 erp_analyze 工具

**接口**：与 erp_agent 相同的 task + conversation_context 参数。

**实现**：`ERPAgent.analyze()` — 调用 `_extract_plan()`（PlanBuilder LLM 提取），
然后 `_build_analyze_result()` 格式化为结构化 plan，不调 `_execute_plan()`。

**返回**：`AgentResult(status="plan")`
```json
{
  "status": "plan",
  "summary": "[能力约束 — 需要分步调用]\n涉及域：\n  ① 采购...\n  ② 订单...",
  "metadata": {
    "plan_steps": [...],
    "objective": "先查供应商采购商品获取编码，再用编码查订单",
    "reason": "串行依赖"
  }
}
```

---

## 4. 改动清单

### 4.1 本次改动

| 文件 | 改动 | 说明 |
|------|------|------|
| `erp_agent.py` | 删 L2 短路 + `_build_plan_result` 改为 `analyze()` + `_build_analyze_result()` | 分析/执行分离 |
| `tool_executor.py` | 新增 `_erp_analyze` handler | 注册分析接口 |
| `chat_tools.py` | 重写 TOOL_SYSTEM_PROMPT + 新增 erp_analyze 工具 schema | 模式判断 + 工具描述 |
| `test_erp_agent.py` | TestPlanMode → TestAnalyze / TestPlanModeE2E → TestAnalyzeE2E | 测试适配 |

### 4.2 保留不变

| 文件 | 内容 | 保留原因 |
|------|------|---------|
| `plan_builder.py` | PlanBuilder / dependency / 自动纠正 | analyze 的分析引擎 |
| `agent_result.py` | `_localize_data` / 表头翻译 / status="plan" | 数据展示 + plan 返回格式 |
| `plan_fill.py` | L2 补全 | 参数补全 |
| `erp_tool_description.py` | 能力描述 | 工具描述 |

---

## 5. 参考

- **Claude Code Plan Mode**：纯提示词驱动，不改循环架构。计划阶段限制只读工具。
- **LangGraph Plan-and-Execute**：Planner 节点 + Executor 节点分离。
- **CrewAI planning=True**：编排层路由到 AgentPlanner 后再执行。
- **Google A2A**：任务状态机（working → input-required → completed）。

共同模式：**分析和执行在架构层分离**，不是在同一个工具内加 flag。
