# Agent 架构细节对齐计划（v6 — 评审定稿）

> 日期：2026-04-20 | 四轮审计 + 多角色评审 + 大厂对标
> 参考：Claude Code（8KB+64%容量分层）/ LangChain（三档压缩）/ ydata-profiling（全列类型 profile）/ AWS Bedrock（observability-first）
> **部署策略**：全部 Phase 做完再统一部署测试，不分阶段上线

---

## 当前 Bug（阻塞，最先修） — ✅ 已修复

**现象：** 用户说"导出订单" → erp_agent 选了 mode=export → 返回字段文档而非数据

**根因：** `erp_unified_query.py:405-408`，export 模式有"两步协议"——不传 fields 就返回字段说明。

**修复（已上线）：** `erp_unified_query.py:405-407` 已改为：
```python
if not fields:
    fields = DEFAULT_DETAIL_FIELDS.get(doc_type, ["*"])
```
两步协议已移除，无条件查数据。

---

## Phase 1：基础补齐（FileRef 元数据）

**现状：** FileRef 8 字段（`tool_output.py:61-76`）— `path, filename, format, row_count, size_bytes, columns, preview, created_at`。frozen=True 不可变。

**字段排序约束（Python dataclass 规则）：** 前 6 个字段（path→columns）无默认值（required），后 2 个（preview, created_at）有默认值。新增字段**必须放在 `created_at` 之后**（即文件末尾），否则 Python 报 `TypeError: non-default argument follows default argument`。

| 任务 | 说明 | 文件 | 现状 | 实施细节 |
|------|------|------|------|---------|
| 1.1 | FileRef 加 `id: str`（UUID，全局唯一，跨工具引用） | `tool_output.py` | ❌ 缺 | 默认值 `id: str = ""`，创建时 `id=uuid4().hex`。**联动改动**：① `session_file_registry.py` 的 `to_snapshot()/from_snapshot()` 序列化 id，`from_snapshot` 用 `.get("id", "")` 兼容旧数据 ② registry 索引从 `path` 改为 `id`（path 作 fallback） |
| 1.2 | FileRef 加 `mime_type: str`（显式 MIME） | `tool_output.py` | ❌ 缺 | 默认值 `mime_type: str = ""`。加映射常量：`_FORMAT_MIME = {"parquet": "application/x-parquet", "csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "json": "application/json"}`。两处创建点（`department_agent.py:260`、`erp_unified_query.py:502`）传入 `mime_type=_FORMAT_MIME[fmt]`。**联动**：`erp_agent.py:183` 的 `collected_files` 当前硬编码 `"application/octet-stream"`，改为读 `file_ref.mime_type` |
| 1.3 | FileRef 加 `created_by: str`（记录哪个 agent 创建） | `tool_output.py` | ❌ 缺 | 默认值 `created_by: str = ""`。ToolOutput 已有 `source` 但 FileRef 没有。`department_agent.py` 传 `created_by=self.domain`，`erp_unified_query.py` 传 `created_by="erp_export"` |
| 1.4 | FileRef 加 `ttl_seconds: int = 86400`（显式 TTL） | `tool_output.py` | ❌ 缺 | 改 `is_valid()` 内部读 `self.ttl_seconds` 替代参数默认值。导出文件可设 `ttl_seconds=172800`（48h），普通查询保持 86400（24h） |
| 1.5 | 降级状态从文本前缀改为纯结构化 | `department_agent.py` + `erp_agent.py` | ⚠️ 半完成 | **两处都要改**：① `department_agent.py:609-622` 同时有文本前缀 + `metadata={"_degraded": True}`，需删除文本前缀拼接 ② `erp_agent.py:189-191` 也拼接 `"⚠ 简化查询模式（关键词匹配，非AI分析）"`，同样需删除。改为统一读 metadata._degraded。主 agent system prompt 加一句"metadata._degraded=True 表示降级查询，提醒用户" |

**⚠️ 原方案遗漏**：
1. **frozen 兼容**：FileRef 是 `frozen=True`，新字段必须给默认值，否则破坏所有创建点（2处）
2. **序列化兼容**：`session_file_registry.py:from_snapshot()` 从 JSON 恢复 FileRef，新字段需 `.get(key, default)` 兜底旧数据（当前 from_snapshot 已全部使用 `.get()` 模式，只需补新字段），否则旧会话数据反序列化崩溃
3. **工作量低估**：1.1 不是"一行"而是 3 文件小改（tool_output + session_file_registry + 2处创建点）
4. **1.1 索引纠正**：session_file_registry 当前索引 key 是 `"{domain}:{tool_name}:{timestamp}"` 复合键（非 path），加 id 后可选择：① 保留复合键+增加 `get_by_id(id)` 方法 ② 改用 id 做主键（破坏更大）。建议方案①
5. **1.2 联动 erp_agent.py**：`erp_agent.py:183` 构造 collected_files 时硬编码 `mime_type="application/octet-stream"`，Phase 1.2 后改为 `file_ref.mime_type`
6. **1.5 双处文本前缀**：降级文本前缀不止 department_agent.py 一处，`erp_agent.py:189-191` 也有独立的前缀拼接，两处都要清理
7. **字段排序约束**：FileRef 前 6 字段无默认值，新增字段（id/mime_type/created_by/ttl_seconds）必须放在 `created_at` 之后，否则 Python 报 TypeError。实际字段顺序为：`path, filename, format, row_count, size_bytes, columns, preview="", created_at=0.0, id="", mime_type="", created_by="", ttl_seconds=86400`
8. **FileRef 创建点完整清单**（生产代码 2 处 + 反序列化 1 处 + 测试 9 处）：生产 `department_agent.py:260`、`erp_unified_query.py:505`；反序列化 `session_file_registry.py:109`；测试 `test_tool_output.py`(3处)、`test_tool_loop_tooloutput.py`(1处)、`test_erp_agent.py`(1处)、`test_warehouse_agent.py`(4处)。所有新字段有默认值，**现有创建点不需要修改**（Python dataclass 自动用默认值），但需要确认 9 处测试仍通过

---

## Phase 2：预算系统对齐（inline/staging 动态决策）

**现状：**
- `ExecutionBudget`（`execution_budget.py:27-159`）只有 3 维度（turns/tokens/wall_time），**只被 ScheduledTaskAgent 使用**
- `DepartmentAgent` 完全不用 ExecutionBudget，inline 判断硬编码 `INLINE_THRESHOLD=200` 行
- `tool_result_envelope.wrap()` 有 `budget: Optional[int]` 参数，但它是**字符数**不是 ExecutionBudget 对象
- wrap 的 3 个常量（`MAIN_AGENT_BUDGET=2000` / `ERP_AGENT_BUDGET=3000` / `ERP_AGENT_RESULT_BUDGET=4000`）是**字符预算**，和 ExecutionBudget 的 **token 预算**是不同维度
- **⚠️ 关键发现**：`tool_executor.py:196-199` 已有 `agent._budget = _parent_budget` 将主循环 budget 注入 ERPAgent，但 **ERPAgent 内部 0 处引用 `_budget`**——这是**死代码**。ERPAgent 用 `asyncio.wait_for(timeout=dag_global_timeout)` 独立管超时

**核心问题：** ExecutionBudget 管 token 总量，wrap 管单次字符截断，两者完全独立，staging 决策不看上下文剩余空间。budget 传递链虽然存在但中间断裂（注入后未使用）。

| 任务 | 说明 | 文件 | 现状 | 实施细节 |
|------|------|------|------|---------|
| 2.0 | **前置**：激活 ERPAgent 已有的 budget 注入 + DepartmentAgent 接入 | `erp_agent.py` + `department_agent.py` | ⚠️ 注入存在但未使用 | **传递链已半通**：`tool_executor.py:196-199` 已将主循环 `_budget` 注入 ERPAgent，但 ERPAgent 内部从不读取。需做：① ERPAgent.execute() 读取 `self._budget`，用 `self._budget.remaining` 替代 `dag_global_timeout` 硬编码超时 ② ERPAgent.__init__ 改为显式接受 `budget` 参数（替代属性注入 hack）③ DepartmentAgent 构造函数加 `budget: Optional[ExecutionBudget] = None` ④ `_create_agent()` 传 `budget=self._budget.fork(max_turns=5)` 给子 Agent |
| 2.1 | `ExecutionBudget` 加 `reserved_for_response: int = 4000` | `execution_budget.py` | ❌ 缺 | 从 `tokens_remaining` 扣除，确保最后一轮 LLM 有足够空间生成回复。`tool_loop_executor.py:305` 的 `check_or_log()` 需感知 |
| 2.2 | `ExecutionBudget` 加 `inline_threshold -> int` 属性（两档切换） | `execution_budget.py` | ❌ 缺 | **评审决策：两档切换替代全动态 can_inline**（对标 Claude Code 8KB+容量分层）。实现：`@property inline_threshold(self) -> int: return 50 if self.tokens_remaining < 15000 else 200`。不做字符-token 转换，不改 wrap() 接口 |
| 2.3 | `wrap()` 加两档字符预算 | `tool_result_envelope.py` | ⚠️ 部分 | wrap 现有 `budget: Optional[int]` 保持字符数。新增：`_resolve_budget()` 在有 ExecutionBudget 时根据 `tokens_remaining < 15000` 切换为 tight 预算（MAIN_AGENT_BUDGET 2000→1200，ERP_AGENT_BUDGET 3000→1800）。无 budget 时保持现有逻辑 |
| 2.4 | `_build_output` 用 `budget.inline_threshold` 替代固定 200 | `department_agent.py` | ❌ 缺 | `threshold = self.budget.inline_threshold if self.budget else INLINE_THRESHOLD`，一行替换，无 budget 时 fallback 到 200 |
| 2.5 | `ExecutionBudget` 加 `_per_tool_tokens: dict[str, int]` | `execution_budget.py` | ❌ 缺 | `use_tokens(n, tool_name=None)` 加可选参数，内部 `self._per_tool_tokens[tool_name] += n`，新增 `get_tool_tokens() -> dict[str, int]` 查询方法 |

**⚠️ 原方案遗漏**：
1. **budget 传递链半通**：`tool_executor.py:196-199` 已注入 `agent._budget = _parent_budget`，但 ERPAgent 内部 **0 处引用**——是死代码。2.0 的核心是"激活"而非"新建"传递链
2. **ERPAgent 超时硬编码**：`erp_agent.py:70` 用 `get_settings().dag_global_timeout` 做 `asyncio.wait_for` 超时，不看 budget.remaining。激活 budget 后需用 `min(self._budget.remaining, dag_global_timeout)` 取较小值
3. **属性注入 hack**：当前 `tool_executor.py:199` 用 `agent._budget = _parent_budget` 属性注入，ERPAgent.__init__ 无 budget 参数。建议改为构造函数参数（显式 > 隐式），`tool_executor.py:187` 改为 `ERPAgent(..., budget=_parent_budget)`
4. **两种"budget"混淆**：wrap 的字符预算 vs ExecutionBudget 的 token 预算，原方案没区分。实施时需要明确 wrap 接受两种类型
5. **工作量调整**：传递链已半通（tool_executor→ERPAgent），2.0 从"建链"降为"激活链+延伸到 DepartmentAgent"，复杂度略降

---

## Phase 3：数据摘要增强

**现状：** `build_data_profile()`（`data_profile.py:19-124`）返回纯 `str`，只计算 sum/min/max/mean（数值列），head(3) 预览，无 distinct_count/median/分位数/outlier。

| 任务 | 说明 | 文件 | 现状 | 实施细节 |
|------|------|------|------|---------|
| 3.1 | `build_data_profile` 返回 `(text, stats_dict)` 元组 | `data_profile.py` | ❌ 返回 str | stats_dict 结构：`{col_name: {"sum": float, "min": float, "max": float, "mean": float, "median": float, "null_count": int, "distinct_count": int, "p25": float, "p75": float}}`。**联动改动**：① `department_agent.py:253-258` 的 `_write_to_staging()` 调用 `build_data_profile()` 需拆元组 ② **注意**：`erp_unified_query.py:485` 用的是 `read_parquet_preview()` （来自 `erp_duckdb_helpers.py`），**不是** `build_data_profile()`——需决定：统一切换到 build_data_profile（推荐，获得结构化 stats），或保持两套并行 |
| 3.2 | stats_dict 存入 `ToolOutput.metadata["stats"]` | `department_agent.py` | ❌ 缺 | `_build_output` 里 `business_fields["stats"] = stats_dict`，最终进 metadata |
| 3.3 | 加 `distinct_count`（所有列） | `data_profile.py` | ❌ 缺 | `df[col].nunique()`，文本输出如 "50种"/"3种" |
| 3.4 | 预览改 `head(2) + sample(1)` | `data_profile.py` | ❌ head(3) | **边界保护**：`n_sample = min(1, max(0, len(df) - 2))`，df≤2行时不 sample。`sample(random_state=42)` 保证可复现 |
| 3.5 | 数值列加 `median` + `p25/p75` 分位数 | `data_profile.py` | ❌ 缺 | `df[col].quantile([0.25, 0.5, 0.75])`，文本输出如 "中位数: 150.0 | P25: 80.0 | P75: 220.0" |
| 3.6 | 加 IQR outlier 检测 | `data_profile.py` | ❌ 缺 | IQR = P75 - P25，outlier = `(val < P25 - 1.5*IQR) | (val > P75 + 1.5*IQR)`。输出："异常值: 3个（占1.2%）"。只在 outlier > 0 时输出 |

**⚠️ 原方案遗漏**：
1. **时间列摘要**：日期/时间戳列应报告 min/max（最早/最晚）+ 时间跨度（如"跨 30 天"）。大厂 data catalog（DataHub/OpenMetadata/Great Expectations）标配
2. **文本列摘要**：文本列应报告 avg_length、max_length、top-5 高频值（`value_counts().head(5)`）。对 ERP 数据的 platform/status 等枚举列尤其有用
3. **两条 preview 路径**：`department_agent.py` 调 `build_data_profile()`，`erp_unified_query.py` 调 `read_parquet_preview()`（DuckDB 直读前3行文本），二者产出格式不同。建议统一切 `build_data_profile` 让 export 路径也获得结构化 stats
4. **stats_dict 序列化问题**（四轮审计确认）：`to_message_content()` 的 metadata 遍历用 `f"{key}: {val}"`，Python 对 dict 调 `str()` 输出 `{'count': 100}` 格式（单引号 Python literal），**不是** JSON。LLM 可读但下游工具无法 parse。**实施方案**：在 `to_message_content()` 的 metadata 遍历中加特判：`if isinstance(val, (dict, list)): tag_lines.append(f"{key}: {json.dumps(val, ensure_ascii=False)}")` 让复杂值输出为 JSON

**新增任务**：

| 任务 | 说明 | 文件 | 工作量 |
|------|------|------|--------|
| 3.7 | 时间列加 min/max 日期 + 时间跨度（天数） | `data_profile.py` | 小改 |
| 3.8 | 文本/枚举列加 top-5 高频值 + avg_length | `data_profile.py` | 小改 |
| 3.9 | 加 `max_profile_rows=50000` 采样保护（评审新增） | `data_profile.py` | 一行 |

---

## Phase 4：可靠性增强

**现状：**
- `OutputStatus.PARTIAL` 枚举已定义，`department_agent.py:147-151` 在 `is_truncated` 时设置 PARTIAL，但**超时场景**无 partial 数据
- `ERPAgentResult`（`erp_agent_types.py:17-29`）无 confidence 字段
- FileRef 无 `derived_from`/`access_count`
- FileRef 是 `frozen=True`（不可变）

| 任务 | 说明 | 文件 | 现状 | 实施细节 |
|------|------|------|------|---------|
| 4.1 | `ToolOutput.validate() -> list[str]` | `tool_output.py` | ❌ 缺 | 检查规则：① summary 非空 ② FILE_REF 格式必须有 file_ref ③ TABLE 格式必须有 columns ④ ERROR 状态必须有 error_message ⑤ file_ref 存在时检查 `file_ref.is_valid()`。返回违规项列表，空=有效。**评审补充**：is_valid() 的磁盘 stat 首次调用后缓存结果（会话内文件不会消失），避免每轮重复 IO |
| 4.2 | executor 先 validate 再 to_message_content | `tool_loop_executor.py` | ❌ 缺 | `tool_loop_executor.py:552` 之前插入：`warnings = result.validate(); if warnings: logger.warning(f"ToolOutput validation | {warnings}")`。只 warn 不阻断（渐进式） |
| 4.3 | 超时返回 partial artifact | `erp_agent.py` + `department_agent.py` | ⚠️ 枚举有，逻辑缺 | **架构难点**：timeout 在 ERPAgent 层（`erp_agent.py:73-76` 和 `135-140` 的 `asyncio.wait_for`），DepartmentAgent 内部**无任何超时处理**（`department_agent.py:624` 的 except Exception 捕获所有异常返回 ERROR）。当 wait_for 超时时，DepartmentAgent._dispatch 被**直接 cancel**，partial rows 丢失。**实施方案**：在 DepartmentAgent.execute() 的 try 块外加 `self._partial_rows = []`，分页查询每拿到一页就追加，except asyncio.CancelledError 时返回 `ToolOutput(status=PARTIAL, data=self._partial_rows)`。ERPAgent 层也需区分 TimeoutError 和其他 Error |
| 4.4 | `ERPAgentResult.confidence: float = 1.0` | `erp_agent_types.py` | ❌ 缺 | `erp_agent.py` 降级时设 `confidence=0.6`，正常 `1.0`。注意：当前 `erp_agent.py:203` 的 experience recorder 已用 `confidence=0.6` 但只是日志参数，不是结果字段 |
| 4.5 | FileRef 加 `derived_from: tuple[str, ...] = ()`（血缘追踪） | `tool_output.py` | ❌ 缺 | **前置条件**：需要 1.1（id 字段）先完成。**⚠️ 四轮审计纠正**：原方案用 `list[str]` + `field(default_factory=list)`，但 frozen dataclass 的 list 字段**实际可变**（`append()` 不报错），破坏不变性语义。改为 `tuple[str, ...]` 确保真正不可变。code_execute 产出的文件目前不走 FileRef，血缘只覆盖 Agent 产出。沙盒重构后补 |
| 4.6 | FileRef 访问计数 | ~~`tool_output.py`~~ → `session_file_registry.py` | ❌ 缺 | **⚠️ 原方案有 bug**：FileRef 是 `frozen=True` 不可变，无法原地 +1。改为在 `session_file_registry.py` 维护 `_access_counts: dict[str, int]`（key=file_ref.id），`_extract_field_from_context` 读取时调 `registry.record_access(file_id)` |

**⚠️ 原方案遗漏**：
1. **4.3 超时架构断裂**：timeout 在 ERPAgent 层（asyncio.wait_for），DepartmentAgent 内部无超时处理。wait_for 触发时 _dispatch 被 cancel，partial rows 直接丢失。需要在 DepartmentAgent 层加 partial 数据暂存机制（`self._partial_rows`），并在 CancelledError 时返回 PARTIAL 而非让异常传播
2. **4.5 前置依赖链**：derived_from 依赖 1.1（id），且 code_execute 产出不走 FileRef 协议——需要等沙盒重构或降低范围只覆盖 Agent 产出
3. **4.6 frozen 冲突**：FileRef 是 frozen 不可变，`access_count: int = 0` 原地 +1 会报 `FrozenInstanceError`。必须改到外部计数器
4. **validate() 性能**：4.1 的 `file_ref.is_valid()` 会检查文件是否存在（磁盘 IO），高频调用时需要 LRU cache 或只在首次检查

---

## Phase 5：可观测性

**现状：**
- 无全链路 trace_id（仅 `kuaimai/client.py:268` 有快麦 API 的 trace_id，是对方返回的）
- `ToolAuditEntry` 无 prompt_tokens/completion_tokens/trace_id 字段
- 无 Langfuse/OpenTelemetry 集成

| 任务 | 说明 | 文件 | 现状 | 实施细节 |
|------|------|------|------|---------|
| 5.1 | loguru 注入 trace_id | 全局 middleware + loguru | ❌ 缺 | **传播链**：chat_handler 入口 `trace_id = task_id` → `ContextVar[str]` → loguru `logger.bind(trace_id=trace_id)` → 所有 Agent/工具日志自动带 trace_id。**关键**：`erp_agent.py` 的 `asyncio.create_task(_cleanup_staging_delayed())` 需要 `copy_context().run()` 继承 ContextVar |
| 5.2 | `ToolAuditEntry` 加 `prompt_tokens` + `completion_tokens` | `tool_audit.py` + `loop_hooks.py` + migration | ❌ 缺 | **数据流向**：LLM stream chunk → `tool_loop_executor.py:366-370` 累加 turn_tokens → 需传给 `loop_hooks.py:152-166`（审计条目实际创建位置）。**粒度问题**：token 计数是 per-turn 的（一个 turn 可能有多个 tool call），audit 是 per-tool 的——需要按 tool call 数均分或只记 turn 级。**chat_handler.py:518-522** 也有 token 累加但粒度更粗（全会话级），不适合直接用。需加 DB migration |
| 5.3 | `ToolAuditEntry` 加 `trace_id` | `tool_audit.py` + migration | ❌ 缺 | 从 ContextVar 读取，ALTER TABLE tool_audit_log ADD COLUMN trace_id TEXT DEFAULT '' |
| 5.4 | 接入 Langfuse（Dashboard + 火焰图 + 成本分析） | 新文件 `services/agent/langfuse_integration.py` + 各 Agent 埋点 | ❌ 缺 | **评审决策：本轮做，补齐可观测性到 100%**。Langfuse v3+ 原生基于 OpenTelemetry，初始化自动注册 span processor。实施：① `pip install langfuse` ② 入口初始化 `Langfuse(public_key, secret_key)` ③ chat_handler/tool_loop_executor 关键路径加 `@observe()` 装饰器 ④ LLM 调用用 `langfuse.generation()` 包装自动记录 token/cost ⑤ erp_agent.execute 用 `langfuse.span()` 包装实现父子关系。**异步上报不影响主流程性能** |
| ~~5.5~~ | ~~OpenTelemetry span（与 5.4 二选一）~~ | — | — | **评审决策：不做**。Langfuse v3 已原生基于 OTEL，接入 Langfuse 即等于接入 OTEL，无需单独做 |

**⚠️ 原方案遗漏**：
1. **ContextVar 在 asyncio.create_task 中不自动继承**：`erp_agent.py:187` 的 `_cleanup_staging_delayed()` 和 `scheduled_task_agent.py:198` 都用 create_task，需要 `contextvars.copy_context().run()` 包装
2. **token 粒度不匹配**：token 统计是 per-turn（一轮 LLM 调用），audit 是 per-tool（一轮可能调多个工具）。三种方案：A) turn 级 token 按 tool call 数均分 B) 只记 turn 级（audit 加 turn_prompt_tokens/turn_completion_tokens） C) 改 audit 粒度为 per-turn。推荐 B（最简单且有用）
3. **audit 创建位置**：ToolAuditEntry 在 `loop_hooks.py:152-166` 创建，不是 tool_loop_executor.py。token 数据需要从 executor 传到 hook。**可行路径**：`_stream_one_turn()` 返回 `turn_tokens` → `run()` 存储 → `_execute_tools()` 加参数 `turn_tokens` → `on_tool_end()` 加参数 `turn_prompt_tokens`/`turn_completion_tokens`。约 3 行签名改动，不需要大重构
4. **migration 文件**（四轮审计补充）：5.2 和 5.3 合并为 `088_extend_tool_audit_log.sql`。**关键**：`tool_audit_log` 是**分区表**（`046_tool_audit_log.sql` 创建，按月分区 + 90 天自动清理），ALTER TABLE 需要对主表操作（分区表自动继承）。写入机制是 Supabase ORM `db.table("tool_audit_log").insert(row).execute()`（`tool_audit.py:47-64`），fire-and-forget async 模式
5. **部署步骤**：项目**无自动 migration runner**——部署时需手动 `psql -f 088_extend_tool_audit_log.sql`，并同步更新 `deploy/init-database.sql`（追加新 migration 内容）
6. **Phase 1.5 降级清理完整范围**（四轮审计确认）：共 3 文件 — ① `erp_agent.py:190-191`（文本前缀"关键词匹配，非AI分析"）② `department_agent.py:611-613`（文本前缀"时间范围默认今天"）③ `plan_builder.py:190`（设置 `_degraded=True` 标记，保留不改）

---

## 已有实现参考（审计更新版）

| 模块 | 文件 | 当前状态 | 审计补充 |
|------|------|---------|---------|
| FileRef / ToolOutput | `services/agent/tool_output.py` | 8/8 字段 + 0/6 新增字段 | 8 字段已完整，6 个新增字段（id/mime_type/created_by/ttl_seconds/derived_from/access_count）全缺 |
| 三层存储 | `tool_result_envelope.py` + `tool_result_cache.py` | L1/L2/L3 已有 | wrap() 有 budget 参数但是字符数不是 ExecutionBudget |
| 结构化任务包 | `erp_agent.py` → `department_agent.py` | typed dict + 白名单校验 | ✅ 完善 |
| 返回护栏 | `tool_result_envelope.py` + `data_profile.py` | 截断 + 摘要已有 | 缺 validate() 校验 + 结构化 stats |
| 上下文预算 | `execution_budget.py` | 多维追踪已有 | chat_handler→tool_executor→ERPAgent 注入链已有，但 ERPAgent 内部未使用（死代码），DepartmentAgent 未接入 |
| 计算下推 | `core/duckdb_engine.py` | DuckDB 流式已有 | ✅ 完善 |
| 降级链 | `erp_agent.py` | 三级降级已有 | 降级标记同时有文本前缀+结构化 metadata（冗余） |
| 幂等缓存 | `tool_result_cache.py` | MD5(args) + 5min TTL 已有 | ✅ 完善 |
| 审计日志 | `tool_audit.py` | 结构化写入已有 | 缺 token 计数 + trace_id |
| 数据摘要 | `data_profile.py` | 7 板块文本已有 | 纯 str 返回，缺 distinct_count/median/分位数/outlier/时间列/文本列摘要 |
| 钻取工具集 | 无 | ❌ 完全缺失 | 用 code_execute 替代，待沙盒重构后评估 |
| Staging 清理 | `erp_agent.py` / `scheduled_task_agent.py` | 延迟 15min 清理 | ✅ 已有 `_cleanup_staging_delayed()` |
| 文件 Registry | `session_file_registry.py` | JSON 序列化 + 会话级管理 | 需适配新 FileRef 字段（id/mime_type/等） |

---

## 依赖关系图

```
Phase 1.1 (FileRef.id)
    ├── Phase 1.2 联动 (erp_agent collected_files 读 mime_type)
    ├── Phase 4.5 (derived_from 依赖 id)
    └── Phase 4.6 (access_count 外部计数器 key=id)

Phase 2.0 (激活 ERPAgent._budget + 延伸到 DepartmentAgent) ← 传递链半通，激活+延伸
    ├── Phase 2.2 (can_inline)
    ├── Phase 2.4 (_build_output 用 can_inline)
    └── Phase 4.3 (partial artifact 依赖 budget.remaining 感知剩余时间)

Phase 3.1 (返回元组)
    ├── Phase 3.2 (stats 存入 metadata)
    ├── Phase 3.3-3.8 (各项 stats 指标)
    └── erp_unified_query.py 切换到 build_data_profile（可选，替代 read_parquet_preview）

Phase 5.1 (trace_id ContextVar)
    ├── Phase 5.3 (audit 加 trace_id)
    └── Phase 5.4 (Langfuse 依赖 trace_id 做链路关联)

Phase 5.2 (audit 加 token)
    ├── 前置：token 数据从 tool_loop_executor → loop_hooks.on_tool_end 参数传递
    └── Phase 5.4 (Langfuse generation 包装复用 token 统计)
```

---

## 工作量估算（v6 评审定稿）

| Phase | 任务数 | 预计工时 | 说明 |
|-------|--------|---------|------|
| Bug 修复 | 1 | ✅ 已完成 | — |
| Phase 1 | 5 | 1天 | FileRef 4 字段 + 降级结构化 + 序列化/registry/collected_files 联动 |
| Phase 5.1 轻量版 | 1 | 2小时 | logger.bind(trace_id)，零风险，后续改动可追踪 |
| Phase 3 | **9**（+3.7/3.8/3.9） | 1天 | 结构化 stats + 全列类型摘要 + 采样保护 + erp_unified_query 统一 |
| Phase 4.1-4.2 | 2 | 半天 | validate + executor 校验（含 is_valid 缓存） |
| Phase 2 | **6**（+2.0） | 1.5天 | 激活 budget 链 + 两档 inline 切换（评审简化版）+ per-tool 统计 |
| Phase 4.3-4.6 | 4 | 1天 | partial artifact + confidence + derived_from(tuple) + access_count(外部计数器) |
| Phase 5.2-5.3 | 2 | 半天 | audit token(turn级) + trace_id + 幂等 migration(IF NOT EXISTS) |
| Phase 5.1 完整版 | 1 | 半天 | create_task copy_context 全链路 ContextVar 传播 |
| **Phase 5.4** | **1** | **2天** | **Langfuse 接入**：@observe 埋点 + generation token/cost + 父子 span + Dashboard 验证 |
| 统一测试 | — | 半天 | 全量后端测试 + 核心场景验证 + Langfuse Dashboard 验证 |
| **合计** | **32** | **约 8.5 天** | 可观测性 100% 对齐大厂。Phase 5.5（单独 OTEL）不做，Langfuse v3 已含 |

---

## 实施顺序（全部完成后统一部署）

> 大厂对标结论：AWS Bedrock / LangChain 均推荐 observability-first（先加 tracing 再改功能，方便调试）。
> Phase 5.1 轻量版（logger.bind，2 小时）提前到 Phase 1 之后，后续改动全链路可追踪。

| 步骤 | Phase | 内容 | 预计工时 |
|------|-------|------|---------|
| 1 | Phase 1 | FileRef 元数据补齐（id/mime_type/created_by/ttl_seconds + 降级结构化） | 1天 |
| 2 | Phase 5.1 轻量版 | chat_handler 入口 `logger.bind(trace_id=task_id)`，覆盖主链路 80% 日志 | 2小时 |
| 3 | Phase 3 | 数据摘要增强（结构化 stats + 时间列/文本列 + IQR + 采样预览） | 1天 |
| 4 | Phase 4.1-4.2 | ToolOutput.validate() + executor 校验 | 半天 |
| 5 | Phase 2 | 预算系统打通（激活 budget 链 + 两档 inline 切换 + per-tool 统计） | 1.5天 |
| 6 | Phase 4.3-4.6 | partial artifact + confidence + derived_from + access_count | 1天 |
| 7 | Phase 5.2-5.3 | audit 加 token 计数 + trace_id + migration | 半天 |
| 8 | Phase 5.1 完整版 | create_task 的 copy_context 传播 + ContextVar 全链路覆盖 | 半天 |
| 9 | Phase 5.4 | **Langfuse 接入**（@observe 埋点 + generation 包装 + 父子 span） | 2天 |
| — | 统一测试 | 全量后端测试 + 手动验证核心场景 + Langfuse Dashboard 验证 | 半天 |
| — | 部署 | psql migration + pip install langfuse + deploy.sh + init-database.sql 同步 | — |
| **合计** | | | **约 8.5 天** |

Phase 5.5（单独做 OpenTelemetry）不做——Langfuse v3 原生基于 OTEL，接入 Langfuse 即等于接入 OTEL。

---

## 评审决策记录（多角色辩论 + 大厂对标）

> 2026-04-20 评审，4 角色参与：系统架构师 / 性能工程师 / 运维SRE / 接手者

### 决策 1：Phase 2 inline/staging 动态决策方案

| 选项 | 描述 | 大厂参考 |
|------|------|---------|
| ~~A 固定阈值~~ | 保留 INLINE_THRESHOLD=200 不变 | 无大厂采用 |
| **✅ B 两档切换** | 正常 200 行 / 紧张（tokens_remaining < 15000）降到 50 行 | Claude Code 8KB+64% 容量分层 / LangChain 三档（20K→85%→摘要） |
| ~~C 全动态 can_inline~~ | 原方案，按 token 剩余量实时计算 | 过度设计，导致同一查询不同轮次表现不一致 |

**保留 can_inline 方法但简化实现**：不做复杂的字符-token 转换，只做两档硬切换。wrap() 的字符预算（2000/3000/4000）保持独立不合并。

### 决策 2：Phase 5.1 trace_id 时机

| 选项 | 描述 | 大厂参考 |
|------|------|---------|
| **✅ A 轻量版提前** | Phase 1 之后立即加 logger.bind(trace_id)，覆盖 80% 日志 | AWS Bedrock 出厂预埋 / LangChain "start small" |
| ~~B 放最后~~ | 等全部功能做完再加 | 无大厂推荐 |

轻量版（2 小时）不改 create_task 的 ContextVar，零风险。完整版（copy_context 传播）放最后一步。

### 决策 3：数据摘要范围

| 选项 | 描述 | 大厂参考 |
|------|------|---------|
| ~~A 只做数值列~~ | distinct_count + 分位数 + IQR | 无主流 profiler 只做数值 |
| **✅ B 全列类型** | + 时间列（min/max/跨度）+ 文本列（top-5/avg_length） | ydata-profiling 单次全量 / OpenMetadata 全类型 |

加 `max_profile_rows=50000` 采样保护（性能工程师建议）。

### 决策 4：部署策略

**全部做完再部署**，不分阶段上线。统一测试通过后一次性部署 + 手动执行 migration。

### 决策 5：Langfuse 接入（用户确认加入本轮）

| 选项 | 描述 |
|------|------|
| ~~不做~~ | 可观测性 70%，缺 Dashboard / 火焰图 / 成本分析 |
| **✅ 本轮做** | +2 天，可观测性 100% 对齐大厂（LangSmith / Langfuse 级别） |

选 Langfuse（非 LangSmith）原因：开源、可自托管、v3 原生 OTEL、Python SDK 成熟。Phase 5.5 单独做 OTEL 不需要了——Langfuse v3 已内置。

---

## 审计发现汇总（四轮累计 17 处）

### 二轮审计（v2→v3）— 7 处

| # | 遗漏 | 位置 | 影响 |
|---|------|------|------|
| 1 | session_file_registry 索引是复合键 `domain:tool:ts`，非 path | Phase 1.1 | 1.1 "改索引为 id" 需改为"增加 get_by_id 方法" |
| 2 | `erp_agent.py:183` collected_files 硬编码 mime_type | Phase 1.2 | 新增联动改动点 |
| 3 | `erp_agent.py:189-191` 也有降级文本前缀（原方案只提了 department_agent） | Phase 1.5 | 改动文件 +1 |
| 4 | ERPAgent 本身没有 ExecutionBudget（用 asyncio.wait_for） | Phase 2.0 | 2.0 涉及 ERPAgent+DepartmentAgent |
| 5 | `erp_unified_query.py` 用 `read_parquet_preview()`，非 `build_data_profile()` | Phase 3.1 | 两条 preview 路径需决策统一 |
| 6 | ToolAuditEntry 创建在 `loop_hooks.py`，非 `tool_loop_executor.py` | Phase 5.2 | token 数据传递链多一跳 |
| 7 | token 粒度 per-turn vs audit per-tool 不匹配 | Phase 5.2 | 需设计粒度适配方案 |

### 三轮审计（v3→v4）— 5 处

| # | 遗漏 | 位置 | 影响 |
|---|------|------|------|
| 8 | **ERPAgent._budget 是死代码**：tool_executor.py:199 注入但 ERPAgent 0 处引用 | Phase 2.0 | 2.0 从"建链"降级为"激活链"，复杂度降低 |
| 9 | **FileRef 字段排序约束**：前 6 字段无默认值，新字段必须放在 created_at 之后 | Phase 1 全部 | 字段定义顺序必须 `..., created_at=0.0, id="", mime_type=""...` |
| 10 | **Phase 4.3 超时在 ERPAgent 层**：DepartmentAgent 内部无 timeout，wait_for cancel 时 partial rows 丢失 | Phase 4.3 | 需在 DepartmentAgent 加 _partial_rows 暂存 + CancelledError 处理 |
| 11 | **tool_executor 还传了 _parent_messages**：`tool_executor.py:202` 注入 parent_messages，但 ERPAgent.execute 的 `**_kwargs` 直接吞掉不用 | Phase 2.0 关联 | 清理死代码时一并处理 |
| 12 | **Phase 5.2 可行性验证通过**：turn_tokens 在 `run()` 返回后可通过参数传给 `_execute_tools()` → `on_tool_end()`，约 3 行签名改动 | Phase 5.2 | 确认不需要大重构 |

### 四轮审计（v4→v5）— 5 处

| # | 遗漏 | 位置 | 影响 |
|---|------|------|------|
| 13 | **frozen dataclass list 字段可变**：`field(default_factory=list)` 的 list 可 `.append()`，破坏不变性 | Phase 4.5 | derived_from 改为 `tuple[str, ...]`（真正不可变） |
| 14 | **to_message_content dict 序列化为 Python literal**：`str(dict)` 输出 `{'key': 'val'}` 非 JSON | Phase 3.2 | metadata 遍历需对 dict/list 值特判 `json.dumps()` |
| 15 | **tool_audit_log 是分区表**：按月分区 + 90 天自动清理，ALTER TABLE 需对主表操作 | Phase 5.2/5.3 | migration 语法需兼容分区表 |
| 16 | **无自动 migration runner**：部署需手动 psql + 同步更新 `deploy/init-database.sql` | Phase 5.2/5.3 | 文档化手动步骤 |
| 17 | **Phase 1 测试影响**：9 处 FileRef 构造在测试中，但新字段有默认值不需要改测试代码 | Phase 1 全部 | 确认测试兼容性风险低 |
