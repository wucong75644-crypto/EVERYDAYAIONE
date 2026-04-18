# ERPAgent 架构简化——去掉 PlanBuilder/DAGExecutor/ComputeAgent 中间层

**版本 2.0 · 2026-04-18**

---

## 1. 背景与动机

### 1.1 现状

```
主Agent → ERPAgent → PlanBuilder(LLM) → DAGExecutor → DepartmentAgent → QueryEngine
          6 层，2 次 LLM 调用
```

ERPAgent 内部有 3 个中间组件：
- **PlanBuilder**：调 LLM 提取参数 + 生成 ExecutionPlan（DAG 结构）
- **DAGExecutor**：按 Round 调度 Agent，处理依赖和并行
- **ComputeAgent**：LLM 生代码 + 沙盒执行计算

### 1.2 生产数据验证（2026-04-17~18 日志统计）

| 查询类型 | 数量 | 比例 |
|---------|------|------|
| 单域单轮（trade/warehouse 等直查） | 23 | 48.9% |
| data + compute（查数据→沙盒计算） | 21 | 44.7% |
| 真正跨域（warehouse+trade 等不同域并行） | 3 | 6.4% |

**关键发现**：
- 删除 ComputeAgent 后，21 个 data+compute 变成单域查询
- 实际单域比例：(23+21)/47 = **93.6%**
- 真正跨域只有 3 个（缺货+订单超时、七天同期对比），**但这些是最高价值的查询**

### 1.3 问题

| 问题 | 影响 |
|------|------|
| PlanBuilder 是个空壳 | ERPAgent 不做分析，转手给 PlanBuilder |
| DAGExecutor 93.6% 直通 | 绝大多数查询只有 1 Agent，DAG 引擎是过度设计 |
| ComputeAgent 和主 Agent 的 code_execute 职责重叠 | 两者都是 LLM 生代码+沙盒执行 |
| 2 次 LLM 调用 | 主 Agent 一次 + PlanBuilder 一次，额外 2-3s 延迟 |
| 计算/跨域编排放在 ERPAgent 内部 | 违反单一职责——ERPAgent 应该只查数据 |

### 1.4 目标

```
简化后:
  主Agent → ERPAgent(LLM提取参数 + 单域查询) → DepartmentAgent → QueryEngine
            4 层，ERPAgent 每次只查一个域
```

**职责重新划分**：
- **ERPAgent** = 单次数据查询（提取参数 → 校验 → 查一个域 → 返回数据）
- **主 Agent** = 任务编排 + 跨域组合 + 计算汇总（拆任务 → 并行多次调 erp_agent → code_execute）
- **DepartmentAgent** = 域执行层（参数转换 → 安全校验 → 查询引擎）——**完全不动**

### 1.5 跨域查询的正确方式

```
用户: "缺货商品有哪些，订单会不会超时"

主 Agent 拆分（并行 tool_calls，代码层已支持 asyncio.gather）:
  → erp_agent("查所有缺货商品")        → WarehouseAgent → 数据 A
  → erp_agent("查待发货超时的订单")    → TradeAgent     → 数据 B
  → code_execute("合并A+B，标注风险") → 最终报表

每次 erp_agent 调用只查一个域，100% 可靠。
跨域编排和计算由主 Agent 负责——它有完整的对话上下文，拆分更准确。
```

**大厂标准做法**：工具越简单越可靠，编排在上层。OpenAI/Anthropic/Google 的 Agent 框架都是这个模式。

---

## 2. 架构对比

### 2.1 调用链对比

```
现在（6 层）:
  主Agent
    → tool_executor._erp_agent(query)
      → ERPAgent.execute(query)
        → ERPAgent._execute_dag(query)
          → PlanBuilder(adapter).build(query)          ← 删
            → _llm_plan(query) / quick_classify(query) ← 迁入 ERPAgent
            → _fill_platform(plan, query)              ← 保留
            → return ExecutionPlan                      ← 删
          → _fill_codes(plan, query, db, org_id)       ← 保留
          → DAGExecutor(agents).run(plan)               ← 删
            → agent.execute(task, params, dag_mode)
          → DAGResult → ERPAgentResult
        → return ERPAgentResult

简化后（4 层）:
  主Agent
    → tool_executor._erp_agent(query)
      → ERPAgent.execute(query)
        → ERPAgent._execute(query)
          → _extract_params(query)       ← 合并自 PlanBuilder
          → _fill_codes()                ← 准入校验
          → agent.execute(dag_mode=True) ← 直调一个 DepartmentAgent
          → return ERPAgentResult
```

### 2.2 ERPAgent._execute 内部调用顺序（精确）

```python
async def _execute(self, query: str, deadline: float) -> ERPAgentResult:
    # ── Step 1: 参数提取（三级降级链）──
    #   1a. 调 LLM 提取结构化参数
    #       → 输入: query + now_str
    #       → 输出: domain="trade", params={doc_type, time_range, ...}
    #       → LLM 输出格式: {"domain": "trade", "params": {...}}
    #   1b. LLM 失败 → quick_classify 关键词降级
    #       → 输入: query
    #       → 输出: domain + _build_fallback_params (含 _degraded=True)
    #   1c. 降级也失败 → abort，返回错误
    #   内部包含:
    #       → _sanitize_params: mode/doc_type/time_range 格式校验
    #       → _fill_platform: L2 平台补全
    #       → 域路由冲突检测: domain-doc_type 兼容校验
    domain, params = await self._extract_params(query)

    # ── Step 2: 准入校验 ──
    #   DB 验证 product_code/order_no 存在性
    await _fill_codes_for_params(params, query, self.db, self.org_id)

    # ── Step 3: 域白名单校验 ──
    #   确保 domain 在 VALID_DOMAINS 中（不含 compute）
    #   不认识的域 → 返回错误

    # ── Step 4: 实例化 DepartmentAgent ──
    #   按 domain 创建对应的一个 Agent（不是全部创建）

    # ── Step 5: 执行查询 ──
    #   await asyncio.wait_for(
    #       agent.execute(task, dag_mode=True, params=params),
    #       timeout=query_timeout,
    #   )
    #   deadline 检查: 剩余时间不足 → 返回超时错误

    # ── Step 6: 结果处理 ──
    #   6a. 注册 file_ref 到 SessionFileRegistry（detail/export 模式）
    #   6b. 降级标记: params._degraded → 结果前加 "⚠ 简化查询模式"

    # ── Step 7: 后处理 ──
    #   7a. ExperienceRecorder 记录路由经验
    #   7b. 统计 tokens_used（仅 ERPAgent 自己的 LLM 调用）
    #   7c. 收集 collected_files
    #   7d. 启动 staging 延迟清理（如有 file_ref）

    return ERPAgentResult(...)
```

---

## 3. 能力迁移清单

### 3.1 迁入 ERPAgent（17 个，全部保留）

| # | 能力 | 原位置 | 迁入方式 |
|---|------|--------|---------|
| 1 | `_sanitize_params` 参数宽容校验 | plan_builder.py:127 | import 调用 |
| 2 | `_fill_platform` L2 平台补全 | plan_builder.py:161 | import 调用 |
| 3 | `_fill_codes` + `_verify_*` 准入校验 | plan_builder.py:213 | import 调用（已在 ERPAgent） |
| 4 | `VALID_DOMAINS` 域白名单 | plan_builder.py:52 | import（去掉 compute） |
| 5 | `_DOMAIN_DOC_TYPES` 冲突检测矩阵 | plan_builder.py:57 | import |
| 6 | `_DOMAIN_DEFAULT_DOC_TYPE` | plan_builder.py:65 | import |
| 7 | `_VALID_MODES` / `_VALID_DOC_TYPES` / `_TIME_RANGE_RE` | plan_builder.py:117-124 | import |
| 8 | `_DOMAIN_TIME_COL` | plan_builder.py:496 | import |
| 9 | `quick_classify()` 关键词路由 | plan_builder.py:73 | import 调用 |
| 10 | `_build_fallback_params()` | plan_builder.py:502 | import 调用 |
| 11 | `build_plan_prompt()` → 简化为 `build_extract_prompt()` | plan_builder.py:367 | 重写 |
| 12 | `_llm_plan()` LLM 调用 | plan_builder.py:469 | 迁入 `_extract_params` |
| 13 | `parse_llm_plan()` → 简化为 `_parse_llm_response()` | plan_builder.py:316 | 重写，输出 domain+params |
| 14 | 三级降级链 (LLM→关键词→abort) | plan_builder.py:434 | 迁入 `_extract_params` |
| 15 | `_DOMAIN_KEYWORDS` 关键词路由表 | plan_builder.py:27 | import |
| 16 | `_PRODUCT_CODE_RE`/`_ORDER_NO_RE`/`_CODE_STOP_WORDS` | plan_builder.py:203-210 | import |
| 17 | `asyncio.wait_for` 超时控制 | dag_executor.py:203 | 内联 |

### 3.2 删除（25 个）

| # | 能力 | 删除理由 |
|---|------|---------|
| 1-8 | PlanBuilder 类/ExecutionPlan/Round/PlanValidationError/validate()/from_dict()/_COMPUTE_KEYWORDS/needs_compute() | ERPAgent 直接提取参数，不需要 DAG 数据结构 |
| 9-16 | DAGExecutor 类/DAGResult/Round 串行/ERROR 传播/PARTIAL 阈值/context 传递/ComputeAgent 分发/Deadline Round 分配 | 单域查询不需要 DAG 引擎 |
| 17-22 | ComputeAgent 全部 6 个能力 | 主 Agent 的 code_execute 替代 |
| 23 | asyncio.gather 多域并行 | ERPAgent 不做多域，主 Agent 并行调用替代 |
| 24 | steer check Round 间打断 | 单域单次执行，无需 Round 间检查 |
| 25 | SessionFileRegistry file_ref 在 DAGExecutor 自动注册 | ERPAgent 手动注册替代 |

### 3.3 清理

| # | 清理项 | 位置 |
|---|--------|------|
| 1 | 删除 `compute_agent.py` | 整个文件 |
| 2 | 删除 `compute_types.py` | 整个文件 |
| 3 | 删除 `dag_executor.py` | 整个文件 |
| 4 | 删除 `execution_plan.py` | 整个文件 |
| 5 | `plan_builder.py` 删除 PlanBuilder 类，保留工具函数 | 可选重命名 `plan_helpers.py` |
| 6 | 删除 `ToolOutput.to_compute_input()` 死代码 | tool_output.py:176 |
| 7 | config.py 清理 `dag_compute_timeout` | core/config.py:152 |

---

## 4. 风险与缓解

| # | 风险 | 等级 | 缓解方案 |
|---|------|------|---------|
| 1 | **`dag_mode=True` 安全约束** | **高** | ERPAgent 调 DepartmentAgent.execute() 时硬编码 `dag_mode=True` |
| 2 | **跨域查询依赖主 Agent 拆分能力** | **高** | 见 §4.1 |
| 3 | **LLM Prompt 变更准确率回归** | 中 | 见 §4.2 |
| 4 | **`_degraded` 降级标记** | 低 | `_build_fallback_params` 保留此标记 |
| 5 | **`validate_compute_result` 硬校验丢失** | 中 | 上线后观察，必要时提取为独立工具 |
| 6 | **`SessionFileRegistry` 多链路共用** | 低 | 不删除模块，ERPAgent 手动注册 file_ref |
| 7 | **tokens_used 统计来源变更** | 低 | 从 adapter response 直接读取 prompt_tokens + completion_tokens |

### 4.1 跨域查询可靠性保障

**问题**：跨域查询（6.4%）虽然占比小但业务价值最高。简化后完全依赖主 Agent 拆分，拆错了用户体验直接退化。

**保障措施**：

1. **主 Agent 工具描述已优化**（已完成）：明确告诉千问"可同一轮并行调多次 erp_agent"，并给出缺货分析/订单超时/对账核对的拆分示例
2. **ERPAgent 返回结果包含足够上下文**：每次查询结果都有 doc_type/time_range/platform 等 metadata，主 Agent 能准确组合
3. **code_execute 兜底**：即使主 Agent 第一次拆分不完美，用户可追问，主 Agent 能补充调用
4. **后续监控**：上线后通过日志统计跨域查询的成功率，低于 90% 则回退或加强 prompt

### 4.2 LLM Prompt 准确率保障

**风险**：新 prompt 输出格式从 DAG 结构简化为扁平结构，准确率未知。

**分析**：新 prompt 比旧 prompt 更简单——LLM 不需要理解 Round/depends_on/多 Agent 概念，只需要输出 `{"domain": "trade", "params": {...}}`。参数提取规则不变。**准确率预期不降反升。**

**验证方案**：Phase 1 完成后，用 `scripts/test_erp_agent_benchmark.py` 跑对比测试，确认准确率 ≥ 92%。

### 4.3 已知死代码（趁重构清理）

| 项目 | 状态 | 处理 |
|------|------|------|
| `ERPAgentResult.ask_user_question` | 死字段 | 保留字段定义，后续 RBAC 可能用 |
| `_cleanup_staging_delayed` | 未调用 | 趁重构在 `_execute` 结束后启动 |

---

## 5. 不改的文件（完整列表）

| 文件 | 理由 |
|------|------|
| `tool_executor.py` | 只看 ERPAgentResult 接口，不关心内部 |
| `erp_agent_types.py` | ERPAgentResult 接口不变 |
| `department_agent.py` | 全部安全层保留（dag_mode/allowed_doc_types/L1/L3） |
| `departments/trade_agent.py` | 不动 |
| `departments/purchase_agent.py` | 不动 |
| `departments/aftersale_agent.py` | 不动 |
| `departments/warehouse_agent.py` | 不动 |
| `erp_unified_query.py` | 查询引擎不动 |
| `erp_unified_filters.py` | filter 校验不动 |
| `erp_unified_schema.py` | schema 定义不动 |
| `session_file_registry.py` | 保留，多链路共用 |
| `tool_result_envelope.py` | 结果截断不动 |
| `experience_recorder.py` | 经验记录不动（输入格式微调） |
| `tool_loop_executor.py` | 企微链路独立，不受影响 |
| `chat_handler.py` | 只恢复 SessionFileRegistry，不受影响 |

---

## 6. LLM Prompt 变化

### 6.1 ERPAgent 内部 Prompt（替代 build_plan_prompt）

```
现在的 prompt 输出格式（DAG 结构）:
{"rounds": [{"agents": ["trade"], "task": "...", "depends_on": [], "params": {...}}]}

简化后的输出格式（单域扁平结构）:
{"domain": "trade", "params": {"doc_type": "order", "mode": "summary", "time_range": "...", ...}}
```

去掉的规则：
- ~~compute 域~~ → 计算交给主 Agent
- ~~多域数组 agents: ["trade", "warehouse"]~~ → 每次只输出一个域
- ~~depends_on 依赖关系~~ → 无 Round
- ~~最多 5 轮~~ → 单次查询
- ~~多 Round 示例~~ → 只保留单查询示例

保留的规则：
- 4 个域描述（warehouse/purchase/trade/aftersale）
- params 完整定义（doc_type/mode/time_range/time_col/platform/group_by/product_code/order_no/include_invalid）
- 时间标准化指令
- 示例

### 6.2 主 Agent 工具描述（chat_tools.py，已完成）

- erp_agent 每次只查一个域的数据
- 批量查询不需要指定具体商品
- 跨域分析：同一轮并行调多次 erp_agent 分别查不同数据
- 计算/对比/导出用 code_execute
- 示例：缺货分析/订单超时/对账核对

---

## 7. 实施计划

### Phase 1: ERPAgent 重写 `_execute` 方法

**改动文件**：`erp_agent.py`

```
1. 删除 PlanBuilder/DAGExecutor/ComputeAgent import
2. 新增 _extract_params 方法（合并 PlanBuilder 逻辑）：
   - 调 LLM（复用 build_extract_prompt）
   - _sanitize_params
   - _fill_platform
   - 域路由冲突检测
   - 降级链（quick_classify + _build_fallback_params）
   - abort
   → 返回 (domain: str, params: dict)
3. 重写 _execute 主流程：
   - _extract_params → _fill_codes → 实例化单个 Agent → execute → 结果处理
   - 无 asyncio.gather（单域）
   - asyncio.wait_for 超时
   - file_ref 手动注册
   - _degraded 标记处理
   - ExperienceRecorder 记录
   - _cleanup_staging_delayed 启动
```

### Phase 2: plan_builder.py 精简

**改动文件**：`plan_builder.py`

```
1. 删除 PlanBuilder 类
2. 删除 needs_compute / _COMPUTE_KEYWORDS
3. 简化 build_plan_prompt → build_extract_prompt
   - 去掉 compute 域
   - 输出格式从 {"rounds": [...]} 改为 {"domain": "...", "params": {...}}
   - 去掉 DAG 规则
4. 简化 parse_llm_plan → parse_llm_response
   - 输出 (domain, params) 而非 ExecutionPlan
5. 保留所有工具函数供 ERPAgent import
```

### Phase 3: 删除文件 + 清理

```
删除 4 个文件:
  - compute_agent.py
  - compute_types.py
  - dag_executor.py
  - execution_plan.py

清理:
  - tool_output.py: to_compute_input() 死代码
  - config.py: dag_compute_timeout
```

### Phase 4: 测试迁移

```
1. 删除 test_compute_agent.py
2. 删除 test_dag_executor.py
3. 删除 test_execution_plan.py（或保留 DAG 无关的测试）
4. 修改 test_erp_agent.py：适配 _extract_params 单域输出
5. 保留 test_plan_builder.py：工具函数测试
6. 新增测试：单域查询、降级路径、超时、LLM 失败、_fill_codes 准入校验
7. 用 benchmark 脚本验证准确率 ≥ 92%
```

### Phase 5: 主 Agent 工具描述（已完成）

chat_tools.py 的 erp_agent 描述已调整。

---

## 8. 验收标准

| # | 场景 | 预期 |
|---|------|------|
| 1 | "今天淘宝订单统计" | 单域 trade，L1 映射 platform=tb |
| 2 | "查所有缺货商品" | 单域 warehouse，不追问具体商品 |
| 3 | "订单号 126036803257340376" | _fill_codes 补全 order_no |
| 4 | "包含刷单的订单" | include_invalid=True 透传 |
| 5 | LLM 失败降级 | quick_classify + _degraded 标记 |
| 6 | 查询超时 | wait_for 超时，返回提示 |
| 7 | detail/export 模式 | file_ref 注册到 SessionFileRegistry |
| 8 | "缺货商品+订单超时"（跨域） | **主 Agent 拆成两次 erp_agent 调用 + code_execute 合并** |
| 9 | "最近7天对比前7天"（同域不同时段） | **主 Agent 拆成两次 erp_agent 调用 + code_execute 计算涨跌幅** |
| 10 | Benchmark 准确率 | ≥ 92%（与旧架构持平或更高） |

---

## 9. 设计约束

1. **ERPAgent 每次只查一个域** — 输出格式 `{"domain": "trade", "params": {...}}`，不支持多域
2. **ERPAgent 永远只读** — 硬编码 `dag_mode=True`
3. **跨域编排由主 Agent 负责** — 主 Agent 多次调 erp_agent + code_execute
4. **4 个 DepartmentAgent 不动** — 为后续 RBAC 权限隔离保留
5. **安全层全部保留** — L1/L2/L3 + filter 白名单 + RPC 白名单
6. **tool_executor 接口不变** — ERPAgentResult 结构不改
7. **向后兼容** — 企微链路（ToolLoopExecutor）不受影响

---

## 10. 审查修订记录

| 版本 | 日期 | 内容 |
|------|------|------|
| 1.0 | 2026-04-18 | 初版：三遍精读代码，42 个能力点逐一标注去向，5 个风险项 |
| 1.1 | 2026-04-18 | 第四遍审查：补充多域 params 冲突 + ask_user 死代码 + staging 清理 |
| 2.0 | 2026-04-18 | **架构决策变更**：ERPAgent 从"支持多域并行"改为"只做单域查询"。跨域编排完全交给主 Agent。删除多域 params 隔离方案（§4.1），新增跨域可靠性保障（§4.1）和准确率验证（§4.2）。生产数据验证 93.6% 单域（§1.2）。验收标准新增跨域拆分场景（#8/#9）和 Benchmark（#10）。 |
