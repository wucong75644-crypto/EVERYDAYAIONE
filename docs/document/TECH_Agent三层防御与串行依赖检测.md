# TECH: Agent 三层防御与计划模式

> 版本：v2.0 | 日期：2026-04-24 | 状态：方案确认，待实施

## 1. 背景与问题

### 1.1 问题描述

ERP Agent 当前只有一种工作模式——**直接执行**。无论任务简单还是复杂，都直接并行执行所有 step。当任务超出"一次搞定"的能力边界时（如跨域串行依赖、参数不足、查询范围过大），要么超时失败，要么返回无效结果。

核心问题不是"ERP Agent 不够强"，而是**它缺少"先想后做"的判断力**——和人一样，拿到复杂任务时应该先规划、先沟通，而不是盲目执行。

### 1.2 触发场景（已知）

| 场景 | 现象 | 根因 |
|------|------|------|
| **串行依赖** | 查供应商商品→用编码查订单，第二步无过滤→全量超时 | 并行模型无法处理步骤间数据传递 |
| **参数不足** | 用户说"查一下退货"，没指定平台/时间 | ERP Agent 猜测参数后查询结果不准 |
| **查询范围过大** | 导出全年订单明细 | 预估数据量超过处理能力 |
| **多步计算** | 需要先查数据、再沙盒计算、再可视化 | 单次调用无法编排多工具协作 |

### 1.3 设计原则

借鉴 Google A2A 协议三层模型 + 项目核心法律——**"不确定必须问，严禁猜测"**：

- Agent 之间是**协作关系**，不是主从关系
- 不确定能否一次搞定时，**进入计划模式**——先想、先问、先规划，再动手
- 三层防御，每层各管各的阶段，互不依赖

---

## 2. 核心概念：ERP Agent 双模式

### 2.1 两种工作模式

```
┌─────────────────────────────────────────────────┐
│  ERP Agent 接收任务                              │
│                                                  │
│  PlanBuilder 分析 → 能一次搞定吗？               │
│                                                  │
│  ├─ YES → 【直接执行模式】                       │
│  │   条件明确、单域或可并行跨域                   │
│  │   → 正常并行执行 → 返回 status=success        │
│  │                                               │
│  └─ NO  → 【计划模式】                           │
│      有串行依赖 / 参数不足 / 超出处理能力         │
│      → 不执行，返回执行计划 status=plan           │
│      → 告诉主Agent怎么拆、下一步做什么            │
└─────────────────────────────────────────────────┘
```

### 2.2 计划模式的触发条件

```python
# 核心判断：结构性规则 + LLM 标签补充
if len(plan.steps) >= 3 or (plan.dependency == "serial" and len(plan.steps) > 1):
    return self._build_plan_result(plan, query)
```

| steps | 触发 | 原因 |
|-------|------|------|
| 1 step | 直接执行 | 简单查询，无需规划 |
| 2 steps + parallel | 直接并行 | 各域独立，如退货率（订单+售后） |
| 2 steps + serial | **计划模式** | 有数据传递依赖，LLM 标签判断 |
| **≥3 steps** | **必须计划模式** | 复杂任务，不猜，先规划再执行 |

设计原则：
- **≥3 步是结构性规则**：不依赖 LLM 贴标签，永远不会误判
- **2 步 serial 是补充**：LLM 判断依赖关系，偶尔标错由 L3 兜底
- 与主 Agent 提示词一致："复杂多步分析（≥3步）先列计划等用户确认"

计划模式是一个**通用框架**，未来可扩展更多触发条件：

| 未来触发条件 | 检测方式 |
|------------|---------|
| 参数不足 | validate_params 返回缺失字段 |
| 查询范围过大 | 预估数据量 > 阈值 |
| 多步计算编排 | compute_hint 含多工具协作 |

---

## 3. 三层防御架构

```
L1 — 主Agent评估（调用前）
  主Agent自检：这个任务能一次搞定吗？
  ├─ 能 → 直接调用 erp_agent
  ├─ 不能 → 自己拆步骤逐步调用
  └─ 不确定 → 直接调用，交给 L2 判断

L2 — ERP Agent 计划模式（执行前）
  PlanBuilder 分析任务，判断是否进入计划模式
  ├─ 能一次搞定 → 直接执行模式
  └─ 不能 → 计划模式，返回 status="plan"

L3 — ERP Agent 执行诊断（执行后）← 已有，不改
  _diagnose_empty / _diagnose_error
  └─ 空结果/超时/错误 → 返回诊断建议
```

### 3.1 各层职责

| 层 | 阶段 | 谁判断 | 动作 | 对应 A2A |
|----|------|-------|------|---------|
| L1 | 调用前 | 主Agent（LLM推理） | 自己拆步骤 or 直接调用 | Capability Discovery |
| L2 | 执行前 | ERP Agent（PlanBuilder） | 执行 or 返回计划 | Task Validation |
| L3 | 执行后 | ERP Agent（诊断层） | 返回诊断建议 | Execution Feedback |

### 3.2 场景覆盖矩阵

| 场景 | L1 主Agent | L2 ERP Agent | L3 诊断 | 结果 |
|------|-----------|-------------|---------|------|
| 今天订单汇总 | 简单→直接调 | 单域→执行 | - | ✅ 零影响 |
| 退货率（订单+售后） | 独立→直接调 | parallel→并行 | - | ✅ 零影响 |
| 供应商商品→查订单（L1拦截） | 有依赖→自己拆 | 不走到这 | - | ✅ 最优路径 |
| 供应商商品→查订单（L1漏过） | 直接调 | serial→计划模式 | - | ✅ L2兜底 |
| 条件错误导致空结果 | - | - | 诊断建议 | ✅ L3兜底 |

---

## 4. 实现方案（本次：L1 + L2 串行依赖）

### 4.1 L1：主Agent提示词（`backend/config/chat_tools.py`）

#### 4.1.1 TOOL_SYSTEM_PROMPT erp_agent 部分追加

在现有返回值处理规则（`- 错误 → 告知用户并建议替代方案`）之后追加：

```
erp_agent 有两种返回模式：
- 直接执行（大部分查询）：返回 status=success/error/empty，正常呈现
- 能力约束（复杂查询）：返回 status=plan，说明涉及哪些域、每步需要什么参数、
  步骤间的数据依赖关系。你根据这些约束自行规划调用方案：
  · 哪些步骤可以并行
  · 哪些步骤需要前一步的输出作为输入
  · 最终是否需要 code_execute 汇总计算

调用前自检——判断"一次够不够"：
- 各域数据独立（如同时看订单汇总和售后汇总）→ 一次调用
- 后一步的输入依赖前一步的输出（如先查供应商商品→用编码查订单）→ 自己拆成多次顺序调用
- 不确定时直接调用，erp_agent 会自行判断是执行还是返回能力约束

计划模式下的用户沟通（参考 Claude Code 计划模式 + 业界主流实践）：
收到 status=plan 时，展示渠道分两层：
- 主对话区（用户必须看到、可能需要决策的内容）：
  · 执行方案：几步、每步查什么、依赖关系、最终怎么计算
  · 等用户确认后再开始执行（用户可调整条件/范围）
  · 最终汇总：完整结论 + 推理逻辑 + 计算公式
- thinking 折叠区（过程细节，不打扰但可查看）：
  · 每步执行进度（"第1步完成：获取到5个商品编码..."）
  · 中间数据摘要
  · erp_agent 的内部思考过程
```

#### 4.1.2 erp_agent 能力描述更新（`_build_tool_description`）

替换现有的 `跨域并行` 行：

```python
lines.append(
    "- 跨域查询：各域数据独立时一次并行查询；"
    "超出一次执行能力时进入计划模式（status=plan），返回执行计划由调用方逐步执行"
)
```

#### 4.1.3 get_capability_manifest returns 更新

替换现有的 `跨域查询` 行为两行：

```python
"跨域并行：各域数据独立时一次返回多域数据 + 关联计算提示，code_execute 按提示关联",
"计划模式（status=plan）：超出一次执行能力时返回执行计划，调用方按计划逐步调用并传递中间结果",
```

---

### 4.2 L2：PlanBuilder 规划校验（`backend/services/agent/plan_builder.py`）

#### 4.2.1 `build_multi_extract_prompt` — 加规则 6 + 示例 6

规则 6（在规则 5 后追加）：

```
6. 多 step 时补充以下字段：
   a. dependency（必填）：
      - "parallel"（默认）：各 step 过滤条件互相独立，可同时执行
      - "serial"：后续 step 需要前序 step 的查询结果作为过滤条件
      判断标准：后续 step 的某个过滤参数在用户查询中没给明确值，
      需要从前序 step 结果获取 → serial
   b. 每个 step 的 params 中补充（serial 时必填）：
      - _expected_output：该步骤预期产出什么数据给后续步骤
      - _dependencies：依赖哪些前序步骤（步骤序号数组，从1开始）
      - _required_input：需要前序步骤的什么字段（如 {"from_step":1,"field":"product_code"}）
```

现有示例 3、4（parallel 跨域）追加 `"dependency":"parallel"`。

新增示例 6（serial 场景）：

```json
{"steps":[
  {"domain":"purchase","params":{"doc_type":"purchase","mode":"summary",
   "time_range":"2026-04-01 ~ 2026-04-17","supplier_name":"XX","group_by":"product",
   "_expected_output":"商品编码列表（product_code）","_dependencies":[]}},
  {"domain":"trade","params":{"doc_type":"order","mode":"summary",
   "time_range":"2026-04-01 ~ 2026-04-17",
   "_expected_output":"订单数据","_dependencies":[1],
   "_required_input":{"from_step":1,"field":"product_code"}}}
],"compute_hint":"先查供应商采购商品获取编码，再用编码查订单",
 "dependency":"serial"}
```

#### 4.2.2 `parse_multi_extract_response` — 返回值加 dependency

返回类型：`(steps, compute_hint)` → `(steps, compute_hint, dependency)`

```python
dependency = data.get("dependency", "parallel")
if dependency not in ("parallel", "serial"):
    dependency = "parallel"
return (steps, compute_hint, dependency)
```

旧单域格式兼容路径返回 `"parallel"`。

---

### 4.3 L2：ERP Agent 计划模式短路（`backend/services/agent/erp_agent.py`）

#### 4.3.1 ExecutionPlan 新增字段

```python
@dataclass
class ExecutionPlan:
    steps: list[PlanStep]
    compute_hint: str | None = None
    degraded: bool = False
    dependency: str = "parallel"  # "parallel" | "serial"
```

#### 4.3.2 `_llm_extract` 和 `_extract_plan` 对齐 3-tuple

`_extract_plan` L1 路径：

```python
raw_steps, compute_hint, dependency = await self._llm_extract(query)
# ...
return ExecutionPlan(steps=steps, compute_hint=compute_hint,
                     degraded=False, dependency=dependency)
```

L2 关键词降级路径不变（dependency 用默认值 `"parallel"`）。

#### 4.3.3 `_execute` 加计划模式短路

在 `_fill_codes_for_params` 之后、`_execute_plan` 之前插入：

```python
# ── 计划模式：≥3步必须规划，2步serial也规划 ──
if len(plan.steps) >= 3 or (plan.dependency == "serial" and len(plan.steps) > 1):
    reason = "步骤≥3" if len(plan.steps) >= 3 else "串行依赖"
    await self._push_thinking(f"进入计划模式：{reason}")
    result = self._build_plan_result(plan, query)
    if self._thinking_parts:
        result.thinking_text = "\n".join(self._thinking_parts)
    return result
```

#### 4.3.4 计划模式返回结构定义

**设计原则**：ERP Agent 只输出能力约束（涉及哪些域、参数、依赖关系），不指挥主 Agent 怎么执行。主 Agent 拿到约束后自行规划调用方案。

##### 返回结构（AgentResult）

```
AgentResult
├── status: "plan"                    # 标识计划模式
├── summary: str                      # 文本描述（给主Agent LLM 阅读理解）
├── source: "erp_agent"
├── confidence: 1.0
├── tokens_used: int
└── metadata: dict                    # 结构化数据（给主Agent 程序解析）
    ├── objective: str                # 最终目标（来自 compute_hint）
    ├── reason: str                   # 触发计划模式的原因（"步骤≥3" / "串行依赖"）
    └── plan_steps: list[dict]        # 步骤列表
        └── [每个 step]:
            ├── step: int             # 步骤序号（从1开始）
            ├── domain: str           # 查询域（purchase/trade/aftersale/warehouse）
            ├── doc_type: str         # 单据类型
            ├── params: dict          # 已知的查询参数（不含内部字段）
            ├── expected_output: str  # 该步骤预期产出什么数据
            ├── dependencies: list[int]  # 依赖哪些前序步骤（步骤序号）
            └── required_input: dict|null  # 需要前序步骤的什么数据
                ├── from_step: int        # 来自哪个步骤
                └── field: str            # 需要什么字段（如 product_code）
```

##### summary 文本格式（主Agent LLM 阅读）

```
[能力约束 — 需要分步调用]

涉及域：
  ① 采购（purchase）
     条件: supplier_name=纸制品01, group_by=product, time_range=2026-03-25 ~ 2026-04-24
     产出: 商品编码列表（product_code）

  ② 订单（trade）
     条件: time_range=2026-03-25 ~ 2026-04-24
     需要: 步骤1的 product_code
     产出: 订单数据

[关联说明] 先查供应商采购商品获取编码，再用编码查订单

请根据以上约束自行规划调用方案。
```

##### metadata 结构化示例（程序解析）

```json
{
  "objective": "先查供应商采购商品获取编码，再用编码查订单",
  "reason": "串行依赖",
  "plan_steps": [
    {
      "step": 1,
      "domain": "purchase",
      "doc_type": "purchase",
      "params": {
        "mode": "summary",
        "time_range": "2026-03-25 ~ 2026-04-24",
        "supplier_name": "纸制品01",
        "group_by": "product"
      },
      "expected_output": "商品编码列表（product_code）",
      "dependencies": []
    },
    {
      "step": 2,
      "domain": "trade",
      "doc_type": "order",
      "params": {
        "mode": "summary",
        "time_range": "2026-03-25 ~ 2026-04-24"
      },
      "expected_output": "订单数据",
      "dependencies": [1],
      "required_input": {"from_step": 1, "field": "product_code"}
    }
  ]
}
```

##### 字段说明

| 字段 | 来源 | 说明 |
|------|------|------|
| `objective` | PlanBuilder 的 compute_hint | 最终要完成什么 |
| `reason` | `_execute` 判断逻辑 | 为什么进入计划模式 |
| `domain` | PlanBuilder step.domain | 查询哪个业务域 |
| `doc_type` | PlanBuilder step.params | 单据类型 |
| `params` | PlanBuilder step.params | 已知的查询条件（过滤内部 `_` 前缀字段） |
| `expected_output` | PlanBuilder step._expected_output | 该步产出什么，给后续步骤或主Agent用 |
| `dependencies` | PlanBuilder step._dependencies | 依赖关系，`[1]` 表示依赖 step 1 的产出 |
| `required_input` | PlanBuilder step._required_input | 具体需要前步的什么字段（如 `{"from_step":1,"field":"product_code"}`） |

##### 新增 `_build_plan_result` 方法

```python
def _build_plan_result(self, plan: ExecutionPlan, query: str) -> AgentResult:
    """计划模式：不执行，返回能力约束供主 Agent 自行规划。"""
    step_lines = []
    for i, step in enumerate(plan.steps):
        label = _DOMAIN_LABEL.get(step.domain, step.domain)
        # 条件描述
        conditions = "、".join(
            f"{k}={v}" for k, v in step.params.items()
            if k not in ("doc_type", "mode") and not k.startswith("_")
        )
        # 预期产出、依赖、所需输入（从 step.params 中提取）
        expected = step.params.get("_expected_output", "")
        deps = step.params.get("_dependencies", [])
        req_input = step.params.get("_required_input")

        line = f"  ① {label}（{step.params.get('doc_type', '')}）"
        if conditions:
            line += f"\n     条件: {conditions}"
        if req_input:
            line += f"\n     需要: 步骤{req_input['from_step']}的 {req_input['field']}"
        elif deps:
            dep_labels = [f"步骤{d}" for d in deps]
            line += f"\n     需要: {'、'.join(dep_labels)}的产出"
        if expected:
            line += f"\n     产出: {expected}"
        step_lines.append(line)

    summary = (
        "[能力约束 — 需要分步调用]\n\n"
        "涉及域：\n"
        + "\n".join(step_lines)
    )
    if plan.compute_hint:
        summary += f"\n\n[关联说明] {plan.compute_hint}"
    summary += "\n\n请根据以上约束自行规划调用方案。"

    return AgentResult(
        status="plan",
        summary=summary,
        source="erp_agent",
        tokens_used=self._tokens_used,
        confidence=1.0,
        metadata={
            "plan_steps": [
                {
                    "step": i + 1,
                    "domain": s.domain,
                    "params": {k: v for k, v in s.params.items() if not k.startswith("_")},
                    "expected_output": s.params.get("_expected_output", ""),
                    "dependencies": s.params.get("_dependencies", []),
                    "required_input": s.params.get("_required_input"),
                }
                for i, s in enumerate(plan.steps)
            ],
            "objective": plan.compute_hint,
        },
    )
```

---

### 4.4 status 文档更新（`backend/services/agent/agent_result.py`）

status 字段文档字符串新增 `plan`：

```python
"""执行状态：success | error | empty | partial | timeout | ask_user | plan"""
```

无逻辑改动（status 是自由字符串，`__post_init__` 只归一化 `"ok"→"success"`）。

---

## 5. 前端 UI：计划模式交互

### 5.1 模式切换入口

在输入框右下角"按使用量计费"文字位置，替换为模式选择器：

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  发送消息...                                                    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ ✦ 智能 ∨  ⚙  ⊛ 深度思考  📁 工作区          [自动模式 ▾] 📎 🎤│
└─────────────────────────────────────────────────────────────────┘
                                                 ↑
                                          原"按使用量计费"位置
                                          替换为模式选择器

点击 [自动模式 ▾] 弹出选项：
  ┌──────────────┐
  │ ● 自动模式    │  ← 默认，AI 自行判断是否需要规划
  │ ○ 计划模式    │  ← 强制先规划再执行
  └──────────────┘
```

### 5.2 计划模式激活态

切换到计划模式后，输入框边框变为主题色柔和绿（与现有 UI 色系协调）：

```
┌─────────────────────────────────────────────────────────────────┐
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓ │
│ ┃                                                             ┃ │
│ ┃  发送消息...                                  （绿色细边框）┃ │
│ ┃                                                             ┃ │
│ ┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫ │
│ ┃ ✦ 智能 ∨  ⚙  ⊛ 深度思考  📁 工作区        [计划模式 ▾] 📎 🎤┃ │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛ │
└─────────────────────────────────────────────────────────────────┘
```

### 5.3 UI 细节

| 元素 | 自动模式（默认） | 计划模式 |
|------|----------------|---------|
| 输入框边框 | 默认样式（现有） | 主题绿色细边框（`var(--success)` 或 `#10B981/30` 低透明度，不突兀） |
| 右下角文字 | "自动模式 ▾" | "计划模式 ▾"（文字变绿色） |
| 发送行为 | AI 自行判断 | 系统提示词注入 `_plan_mode: true` |
| 模式持续 | - | 单次对话有效，刷新恢复自动模式 |
| 色系原则 | 与现有"深度思考"蓝色胶囊同一设计语言，绿色不抢视觉焦点 |

### 5.4 模式切换的后端联动

用户手动开启计划模式时，前端在 WebSocket 消息中附带标记：

```json
{
  "type": "send_message",
  "content": "查供应商纸制品01的商品，用编码查订单",
  "plan_mode": true
}
```

主 Agent 收到 `plan_mode: true` 时，行为调整：
- 无论查询复不复杂，都先输出执行方案等用户确认
- 相当于用户主动要求"先想后做"

与自动触发的关系：
- **自动模式**：主 Agent 自行判断（L1）+ ERP Agent 兜底（L2）
- **计划模式（手动）**：跳过 L1，所有查询都先输出方案等确认
- 两者互不冲突

### 5.5 计划展示与确认 UI

主 Agent 在主对话区输出计划后，用户可以：

```
┌─────────────────────────────────────────────────────┐
│ 🤖 主Agent:                                        │
│                                                     │
│ 这个查询需要分步执行，我的方案是：                   │
│                                                     │
│ 1. 查供应商「纸制品01」的采购商品（按商品分组）      │
│    → 获取商品编码列表                                │
│ 2. 用这些编码查最近30天的订单数据                    │
│    → 获取订单明细                                    │
│ 3. 计算采购到货与订单发货的时间差                    │
│                                                     │
│ 确认后开始执行。                                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  [输入框：用户可直接回复确认或调整]                  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

用户回复：
- "可以" / "执行" → 主 Agent 按计划逐步执行
- "时间改成最近7天" → 主 Agent 调整后重新展示方案
- "不用分步，直接查" → 主 Agent 跳过计划模式直接调用

---

## 6. 执行流程

### 5.1 L1 拦截场景（最优路径，~13s）

```
用户: "查供应商纸制品01的采购商品，用编码查订单算缺货时间差"

主Agent 自检: "后一步需要前一步的编码 → 有依赖 → 分步调用"
  → erp_agent(task="查供应商纸制品01的采购按商品分组")   # 单域，~5s
  → 拿到编码: 200BCB01, BCBJB02...
  → erp_agent(task="查商品编码200BCB01,BCBJB02最近30天订单")  # 单域，~5s
  → code_execute(计算时间差)  # ~3s
  → 返回最终结果
```

### 5.2 L2 兜底场景（L1 漏过，~16s）

```
主Agent 没识别出依赖 → 直接调用 erp_agent(task=原文)
  PlanBuilder: steps=[purchase, trade], dependency="serial"
  ERP Agent: 进入计划模式 → 返回 status="plan" + 能力约束（~3s）

ERP Agent 返回能力约束（不指挥，只说明）:
  ① 采购：条件 supplier_name=纸制品01 → 产出 product_code
  ② 订单：需要①的 product_code → 产出 订单数据

主Agent 拿到约束，自己规划调用方案:
  → erp_agent(task="查供应商纸制品01的采购按商品分组")
  → 提取编码 → erp_agent(task="查编码XXX的订单")
  → code_execute(计算时间差)
```

### 5.3 对比：无防御（当前，~33s + 失败）

```
ERP Agent 并行执行:
  Step 1 (purchase): 正常查询或空结果
  Step 2 (trade): 无过滤 → 全量导出 → 30s 超时 ❌
```

---

## 7. 改动范围

### 6.1 需要改的文件

| 文件 | 改动类型 | 内容 |
|------|---------|------|
| `backend/config/chat_tools.py` | 提示词 | TOOL_SYSTEM_PROMPT + 能力描述 |
| `backend/services/agent/plan_builder.py` | 代码+提示词 | prompt规则 + parse返回值 + manifest |
| `backend/services/agent/erp_agent.py` | 代码 | ExecutionPlan字段 + 计划模式短路 + plan结果构建 + 能力描述 |
| `backend/services/agent/agent_result.py` | 文档 | status 字段注释 |

### 6.2 不改的文件

- `_execute_plan` 并行逻辑不动
- `_build_multi_result` 不动
- `chat_handler.py` / `chat_tool_mixin.py` 不动（status 是自由字符串）
- L3 诊断（`_diagnose_empty` / `_diagnose_error`）不动
- 单域查询 / parallel 跨域查询完整路径不受影响

---

## 8. 未来扩展

计划模式是通用框架，触发条件可持续扩展：

```python
# 结构性规则（本次实现，不依赖 LLM）
if len(plan.steps) >= 3:
    return self._build_plan_result(plan, query, reason="步骤≥3")

# LLM 标签判断（本次实现，2步时补充）
if plan.dependency == "serial" and len(plan.steps) > 1:
    return self._build_plan_result(plan, query, reason="串行依赖")

# 未来：参数不足
# if self._detect_missing_params(plan):
#     return self._build_plan_result(plan, query, reason="参数不足")

# 未来：查询范围过大
# if self._estimate_data_volume(plan) > THRESHOLD:
#     return self._build_plan_result(plan, query, reason="数据量过大")
```

---

## 9. 验证计划

1. **单元测试**：serial 检测 + plan 返回格式 + parallel 不受影响
2. **线上测试**：服务器执行串联查询，验证返回 plan 而非超时
3. **回归**：单域查询 + parallel 跨域不受影响
4. **端到端**：前端发送"查供应商纸制品01的商品，用编码查订单"，观察主Agent按计划逐步执行

---

## 10. 参考

- Google A2A Protocol: agent-card 能力声明 + `input-required` 状态机 + task lifecycle
- Microsoft AutoGen: ConversableAgent 双向对话协商
- 项目核心法律 V2.2: "不确定必须问，严禁猜测"、"先分析、先调研、先讨论、再动手"
- 现有 L3 诊断: `_diagnose_empty` / `_diagnose_error`（param_converter.py）
