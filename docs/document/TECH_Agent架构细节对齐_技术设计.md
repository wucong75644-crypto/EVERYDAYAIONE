# 技术设计：Agent 架构细节对齐

> 前置：`TECH_Agent架构细节对齐计划.md` v6 评审定稿（32 任务 / 8.5 天）
> 评审决策：两档 inline 切换 / trace_id 轻量版提前 / 全列类型摘要 / Langfuse 接入 / 全做完再部署
> 等级：A 级（跨 15+ 文件、核心架构改动）

---

## 1. 项目上下文

**架构现状**：FastAPI + React 单体，Supabase PostgreSQL + Redis + Aliyun ECS（2核4G）。Agent 子系统采用 Subagent-as-Tool 模式：chat_handler → tool_loop_executor → tool_executor → ERPAgent → DepartmentAgent（4 子域）。ExecutionBudget 只被 ScheduledTaskAgent 使用，ERPAgent 虽被注入 budget 但内部不读取（死代码）。

**可复用模块**：
- `tool_result_envelope.py`：三层存储（inline/staging/cache）已完善，wrap() 有 budget 参数
- `tool_result_cache.py`：MD5+5min TTL 幂等缓存
- `context_compressor.py`：三层上下文压缩
- `session_file_registry.py`：会话级文件注册 + JSON 序列化（已用 `.get()` 兼容模式）
- `loop_hooks.py`：ToolAuditHook 已有 on_tool_end 钩子链

**设计约束**：
- FileRef 是 `frozen=True`，新字段必须有默认值且放在 `created_at` 之后
- ToolAuditEntry 用 `dataclasses.asdict()` → Supabase `.insert(row).execute()` 写入分区表
- 部署无自动 migration runner，需手动 psql + 同步 `deploy/init-database.sql`
- `erp_unified_query.py` 的 export 路径用 `read_parquet_preview()` 而非 `build_data_profile()`

**潜在冲突**：
- `CURRENT_ISSUES.md` 无活跃 issue，无直接冲突
- `tool_executor.py:199` 的 `agent._budget = _parent_budget` 属性注入 hack 需正式化
- `erp_agent.py:57` 的 `**_kwargs` 吞掉 parent_messages 参数（死代码），需一并清理

---

## 2. 代码分析

**已阅读文件（17 个）**：
- `tool_output.py`(175行)：FileRef 8 字段 frozen + ToolOutput 9 字段 + to_message_content
- `execution_budget.py`(160行)：3 维预算 + fork + tool_timeout + check_or_log
- `data_profile.py`(124行)：7 板块纯文本 profile，只有 sum/min/max/mean
- `tool_audit.py`(65行)：ToolAuditEntry 13 字段 + fire-and-forget 写入
- `loop_hooks.py`(311行)：ToolAuditHook.on_tool_end 9 参数
- `session_file_registry.py`(121行)：to_snapshot/from_snapshot + 复合键索引
- `tool_result_envelope.py`：wrap() + 3 个字符预算常量 + _resolve_budget
- `tool_loop_executor.py`(632行)：run() 主循环 + _stream_one_turn + _execute_tools
- `department_agent.py`(654行)：_build_output(INLINE_THRESHOLD=200) + execute + _dispatch
- `erp_agent.py`(474行)：execute(wait_for) + _create_agent + _build_result
- `erp_agent_types.py`(91行)：ERPAgentResult 无 confidence
- `tool_executor.py`(571行)：_erp_agent handler + budget 注入
- `chat_handler.py`：ExecutionBudget 创建 + _execute_tool_calls
- `chat_tool_mixin.py`：executor._budget = budget 传递
- `plan_builder.py`：_degraded=True 设置点
- `erp_unified_query.py`：_export + read_parquet_preview
- `erp_duckdb_helpers.py`：read_parquet_preview 实现

**数据流向**：
```
chat_handler(创建 budget) → chat_tool_mixin(注入 executor._budget)
  → tool_executor._erp_agent(注入 agent._budget ← 死代码)
    → ERPAgent.execute(asyncio.wait_for, 不读 _budget)
      → _create_agent(不传 budget) → DepartmentAgent(无 budget)
        → _build_output(硬编码 INLINE_THRESHOLD=200)
        → build_data_profile(返回 str)
```

**可复用逻辑**：
- `wrap()` 的 `_resolve_budget()` 模式可扩展为两档切换
- `ToolAuditHook.on_tool_end()` 的参数链可直接追加 token 字段
- `session_file_registry.from_snapshot()` 的 `.get()` 模式天然兼容新字段

---

## 3. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| FileRef 旧数据反序列化（无 id/mime_type） | `from_snapshot()` 用 `.get("id", "")` 兜底 | session_file_registry |
| build_data_profile 接收空 DataFrame | `if rows == 0: return ("无数据", {})` 提前返回 | data_profile |
| build_data_profile 数据超 50K 行 | `max_profile_rows` 参数，采样后计算 | data_profile |
| value_counts() 在高基数列（如 order_no）上性能 | 只对 nunique < 100 的列做 top-5，高基数跳过 | data_profile |
| sample(1) 在 df 只有 1-2 行时报错 | `n_sample = min(1, max(0, len(df) - 2))`，不够则不 sample | data_profile |
| budget.tokens_remaining 为 0 时 inline_threshold | `inline_threshold` 返回 50（紧张档），不返回 0 | execution_budget |
| ERPAgent 无 budget（旧调用方未传） | `self._budget` 默认 None，`execute()` 内用 `min(budget.remaining if budget else inf, timeout)` | erp_agent |
| DepartmentAgent 无 budget（直接实例化） | `_build_output` 检查 `self._budget is not None`，否则 fallback 200 | department_agent |
| asyncio.CancelledError 时 partial_rows 为空 | 返回 `ToolOutput(status=PARTIAL, summary="查询超时，无部分数据")` | department_agent |
| validate() 对已删除文件调 is_valid() | 首次结果缓存到 `_valid_cache: dict[str, bool]`（ToolOutput 实例级） | tool_output |
| to_message_content 的 metadata 含嵌套 dict | `isinstance(val, (dict, list))` 时用 `json.dumps()` | tool_output |
| Langfuse 服务不可达 | SDK 异步上报，失败只 warning 不阻塞主流程 | langfuse_integration |
| migration 重复执行 | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 幂等 | migration SQL |
| ContextVar 在 create_task 中丢失 | `copy_context().run()` 包装 | erp_agent / scheduled_task_agent |
| tool_audit_log 分区表 ALTER TABLE | PostgreSQL 15+ fast default（只改 catalog），对主表操作自动传播 | migration |

---

## 4. 连锁修改清单

### Phase 1：FileRef 元数据

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| FileRef 加 4 字段 (id/mime_type/created_by/ttl_seconds) | `tool_output.py` | 字段放 `created_at` 之后；`is_valid()` 改读 `self.ttl_seconds` |
| FileRef.id 序列化 | `session_file_registry.py` | `to_snapshot()` 加 id/mime_type/created_by/ttl_seconds；`from_snapshot()` 加 `.get()` |
| FileRef.id 查询方法 | `session_file_registry.py` | 新增 `get_by_id(id) -> FileRef | None` |
| 创建点传 mime_type | `department_agent.py:260` | `mime_type=_FORMAT_MIME.get(fmt, "")` |
| 创建点传 mime_type | `erp_unified_query.py:505` | `mime_type=_FORMAT_MIME.get("parquet", "")` |
| 创建点传 created_by | `department_agent.py:260` | `created_by=self.domain` |
| 创建点传 created_by | `erp_unified_query.py:505` | `created_by="erp_export"` |
| collected_files 读 mime_type | `tool_executor.py:218-221` | `mime_type=f["mime_type"]` → `file_ref.mime_type` |
| erp_agent collected_files 构建 | `erp_agent.py:183` | `"mime_type": result.file_ref.mime_type or "application/octet-stream"` |
| 降级：删文本前缀 | `department_agent.py:611-613` | 删除 `"⚠ 简化查询模式（时间范围默认今天）\n\n"` 拼接 |
| 降级：删文本前缀 | `erp_agent.py:190-191` | 删除 `"⚠ 简化查询模式（关键词匹配，非AI分析）\n\n"` 拼接 |
| 降级：system prompt | 主 agent system prompt 文件 | 加 "metadata._degraded=True 表示降级，请提醒用户" |

### Phase 2：预算系统

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| ExecutionBudget 加 reserved_for_response | `execution_budget.py` | `tokens_remaining` 属性扣除 reserved |
| ExecutionBudget 加 inline_threshold 属性 | `execution_budget.py` | 两档：< 15000 返回 50，否则 200 |
| ExecutionBudget 加 _per_tool_tokens | `execution_budget.py` | `use_tokens(n, tool_name=None)` 加参数 |
| ERPAgent.__init__ 加 budget 参数 | `erp_agent.py` | 替代 `tool_executor.py:199` 的属性注入 |
| ERPAgent.execute() 用 budget.remaining | `erp_agent.py:70-76` | `timeout = min(budget.remaining, dag_global_timeout) if budget else dag_global_timeout` |
| tool_executor 改为构造函数传 budget | `tool_executor.py:187-199` | `ERPAgent(..., budget=_parent_budget)` |
| _create_agent 传 budget.fork() | `erp_agent.py:448` | `cls(..., budget=self._budget.fork(max_turns=5) if self._budget else None)` |
| DepartmentAgent.__init__ 加 budget | `department_agent.py:56-66` | `budget: Optional[ExecutionBudget] = None` |
| _build_output 用 budget.inline_threshold | `department_agent.py:199` | `threshold = self._budget.inline_threshold if self._budget else INLINE_THRESHOLD` |
| wrap() 两档字符预算 | `tool_result_envelope.py:160-161` | `_resolve_budget()` 感知 ExecutionBudget 做 normal/tight 切换 |

### Phase 3：数据摘要

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| build_data_profile 返回元组 | `data_profile.py` | 返回 `(text, stats_dict)` |
| 调用方拆元组 | `department_agent.py:253` | `profile_text, stats = build_data_profile(...)` |
| erp_unified_query 切换到 build_data_profile | `erp_unified_query.py:485` | 替换 `read_parquet_preview` |
| stats 存入 metadata | `department_agent.py:_build_output` | `business_fields["stats"] = stats` |
| to_message_content dict 特判 | `tool_output.py:153-155` | `if isinstance(val, (dict, list)): json.dumps(val)` |
| 测试 build_data_profile | `test_data_profile.py` | 所有断言改为接收元组 `result, stats = ...` |

### Phase 4：可靠性

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| ToolOutput.validate() | `tool_output.py` | 新增方法 |
| executor 调 validate | `tool_loop_executor.py:552` | 在 `to_message_content()` 前调 |
| DepartmentAgent._partial_rows | `department_agent.py` | execute() 加 CancelledError 处理 |
| ERPAgent 区分 TimeoutError | `erp_agent.py:77-81` | 超时时检查子 Agent 的 _partial_rows |
| ERPAgentResult.confidence | `erp_agent_types.py` | 新增字段 `confidence: float = 1.0` |
| FileRef.derived_from | `tool_output.py` | `derived_from: tuple[str, ...] = ()` |
| access_count 外部计数器 | `session_file_registry.py` | `_access_counts: dict[str, int]` + `record_access()` |

### Phase 5：可观测性 + Langfuse

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| trace_id ContextVar | 新文件 `services/agent/observability/__init__.py` | `get_trace_id()` / `set_trace_id()` |
| chat_handler logger.bind | `chat_handler.py` | 入口 `set_trace_id(task_id)` + `logger.bind(trace_id=...)` |
| ToolAuditEntry 加 3 字段 | `tool_audit.py` | `prompt_tokens: int = 0` / `completion_tokens: int = 0` / `trace_id: str = ""` |
| on_tool_end 加参数 | `loop_hooks.py:136-147` | 签名加 `turn_prompt_tokens=0, turn_completion_tokens=0` |
| _execute_tools 传 turn_tokens | `tool_loop_executor.py:153` | 加参数 `turn_prompt_tokens, turn_completion_tokens` |
| run() 拆分 turn_tokens | `tool_loop_executor.py:121` | `_stream_one_turn` 返回 `(prompt_tokens, completion_tokens)` |
| create_task copy_context | `erp_agent.py:187` | `ctx = copy_context(); ctx.run(asyncio.create_task, ...)` |
| create_task copy_context | `scheduled_task_agent.py:198` | 同上 |
| migration SQL | `migrations/088_extend_tool_audit_log.sql` | 3 列 + IF NOT EXISTS |
| Langfuse 初始化 | 新文件 `services/agent/observability/langfuse_integration.py` | `init_langfuse()` + `@observe` 装饰器 |
| Langfuse 入口埋点 | `chat_handler.py` | `@observe(name="chat_handler")` |
| Langfuse generation 包装 | `tool_loop_executor.py:_stream_one_turn` | `langfuse.generation()` 记录 LLM 调用 |
| Langfuse span 包装 | `erp_agent.py:execute` | `langfuse.span(name="erp_agent")` |
| deploy/init-database.sql | `deploy/init-database.sql` | 追加 088 migration |
| requirements.txt | `backend/requirements.txt` | 加 `langfuse==2.51.3` |

---

## 5. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| **模块边界** | 新增 `observability/` 子目录（3 文件），符合现有 `departments/`、`guardrails/` 模式 | 低 | 遵循现有子目录组织模式 |
| **数据流向** | budget 传递链从半通→全通（chat_handler→ERPAgent→DepartmentAgent），无环形依赖 | 低 | 链路激活，不新建 |
| **扩展性** | build_data_profile 加 max_profile_rows=50000 采样，10x 数据量无性能问题 | 低 | 采样保护已内置 |
| **耦合度** | Langfuse 通过装饰器接入（`@observe`），不侵入业务逻辑；`init_langfuse()` 失败时静默降级 | 低 | 装饰器模式，可选 |
| **一致性** | 所有新模式对齐现有：dataclass 字段 / fire-and-forget audit / loguru 日志 / ContextVar 传播 | 低 | 严格复用现有模式 |
| **可观测性** | 本次改动本身就是加可观测性——trace_id + Langfuse + audit token | 低 | 自我验证 |
| **可回滚性** | migration 用 IF NOT EXISTS 幂等；Langfuse 装饰器可注释掉即回退；FileRef 新字段有默认值不影响旧代码 | 低 | 所有改动向后兼容 |

**无高风险项，继续设计。**

---

## 6. 技术栈

沿用现有：
- 后端：Python 3.11 + FastAPI + loguru + Supabase PostgreSQL
- 缓存：Redis（本地）
- 新增依赖：`langfuse==2.51.3`（Langfuse Python SDK）

---

## 7. 目录结构

### 新增文件

| 文件 | 职责 | 预估行数 |
|------|------|---------|
| `services/agent/observability/__init__.py` | trace_id ContextVar + get/set 工具函数 | ~30 行 |
| `services/agent/observability/langfuse_integration.py` | Langfuse 初始化 + @observe 装饰器封装 + 静默降级 | ~80 行 |
| `migrations/088_extend_tool_audit_log.sql` | tool_audit_log 加 3 列（幂等） | ~15 行 |

### 修改文件

| 文件 | 改动内容 | 预估改动量 |
|------|---------|----------|
| `tool_output.py` | FileRef +4 字段 + ToolOutput.validate() + to_message_content dict 特判 + derived_from | ~60 行 |
| `execution_budget.py` | reserved_for_response + inline_threshold + _per_tool_tokens | ~40 行 |
| `data_profile.py` | 返回元组 + distinct_count + median/p25/p75 + IQR + 时间列 + 文本列 + 采样 | ~120 行（重写统计部分）|
| `department_agent.py` | _build_output 用 budget.inline_threshold + _partial_rows + CancelledError | ~30 行 |
| `erp_agent.py` | __init__ 加 budget + execute 用 budget.remaining + _create_agent 传 fork + 清理降级前缀 | ~25 行 |
| `erp_agent_types.py` | +confidence 字段 | ~2 行 |
| `session_file_registry.py` | to_snapshot/from_snapshot 加新字段 + get_by_id + _access_counts | ~40 行 |
| `tool_result_envelope.py` | _resolve_budget 两档切换 | ~15 行 |
| `tool_audit.py` | +3 字段 | ~5 行 |
| `loop_hooks.py` | on_tool_end +2 参数 + 传给 ToolAuditEntry | ~10 行 |
| `tool_loop_executor.py` | _execute_tools 传 turn_tokens + _stream_one_turn 返回拆分 | ~15 行 |
| `tool_executor.py` | ERPAgent 构造改为传 budget 参数 | ~5 行 |
| `erp_unified_query.py` | 切换到 build_data_profile | ~10 行 |
| `chat_handler.py` | set_trace_id + logger.bind + Langfuse @observe | ~10 行 |
| `scheduled_task_agent.py` | create_task copy_context | ~5 行 |
| `requirements.txt` | +langfuse==2.51.3 | ~1 行 |
| `deploy/init-database.sql` | 追加 088 migration | ~15 行 |
| 测试文件（~6 个） | 适配新返回类型 / 新字段 | ~100 行 |

---

## 8. 数据库设计

### Migration: `088_extend_tool_audit_log.sql`

```sql
-- 088: tool_audit_log 扩展（token 统计 + trace_id）
-- 幂等：IF NOT EXISTS，可安全重复执行
-- 分区表：ALTER TABLE 主表自动传播到所有分区

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER DEFAULT 0;

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER DEFAULT 0;

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS trace_id TEXT DEFAULT '';

-- 索引：按 trace_id 查全链路
CREATE INDEX IF NOT EXISTS idx_tool_audit_trace_id
    ON tool_audit_log (trace_id)
    WHERE trace_id != '';
```

**回滚 SQL**：
```sql
ALTER TABLE tool_audit_log DROP COLUMN IF EXISTS prompt_tokens;
ALTER TABLE tool_audit_log DROP COLUMN IF EXISTS completion_tokens;
ALTER TABLE tool_audit_log DROP COLUMN IF EXISTS trace_id;
DROP INDEX IF EXISTS idx_tool_audit_trace_id;
```

---

## 9. API 设计

本次改动无新增 API 端点。所有变更为内部模块改动。

Langfuse 需要配置环境变量（加入 `.env` 和 `.env.example`）：
```
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=https://cloud.langfuse.com  # 或自托管地址
```

---

## 10. 开发任务拆分

### 阶段 1：Phase 1 — FileRef 元数据（1天）

- [ ] 1.1 `tool_output.py`：FileRef 加 id/mime_type/created_by/ttl_seconds 4 字段 + `_FORMAT_MIME` 常量 + `is_valid()` 改读 ttl_seconds
- [ ] 1.2 `session_file_registry.py`：to_snapshot/from_snapshot 适配新字段 + `get_by_id()` 方法 + `_access_counts` 字典 + `record_access()` 方法
- [ ] 1.3 `department_agent.py:260`：FileRef 创建传 id/mime_type/created_by
- [ ] 1.4 `erp_unified_query.py:505`：FileRef 创建传 id/mime_type/created_by
- [ ] 1.5 `erp_agent.py:183` + `tool_executor.py:218`：collected_files 读 file_ref.mime_type
- [ ] 1.6 降级结构化：删除 `department_agent.py:611-613` 和 `erp_agent.py:190-191` 的文本前缀
- [ ] 1.7 运行测试，确认 9 处 FileRef 测试全绿

### 阶段 2：Phase 5.1 轻量版 — trace_id（2小时）

- [ ] 2.1 新建 `services/agent/observability/__init__.py`：trace_id ContextVar + get_trace_id/set_trace_id
- [ ] 2.2 `chat_handler.py`：请求入口调 set_trace_id(task_id) + logger.bind(trace_id=...)

### 阶段 3：Phase 3 — 数据摘要增强（1天）

- [ ] 3.1 `data_profile.py`：改返回 `(str, dict)` 元组 + stats_dict 结构
- [ ] 3.2 `data_profile.py`：加 distinct_count（所有列）+ median/p25/p75（数值列）+ IQR outlier
- [ ] 3.3 `data_profile.py`：加时间列摘要（min/max date + 跨度天数）
- [ ] 3.4 `data_profile.py`：加文本列摘要（top-5 高频值 + avg_length，nunique<100 才做）
- [ ] 3.5 `data_profile.py`：加 max_profile_rows=50000 采样保护 + 预览改 head(2)+sample(1)
- [ ] 3.6 `department_agent.py:253`：拆元组 `profile_text, stats = build_data_profile(...)`
- [ ] 3.7 `erp_unified_query.py:485`：替换 read_parquet_preview 为 build_data_profile
- [ ] 3.8 `department_agent.py:_build_output`：stats 存入 metadata
- [ ] 3.9 `tool_output.py:153-155`：to_message_content dict/list 值用 json.dumps
- [ ] 3.10 `test_data_profile.py`：所有测试适配新返回类型

### 阶段 4：Phase 4.1-4.2 — validate 校验（半天）

- [ ] 4.1 `tool_output.py`：新增 `validate() -> list[str]` + is_valid 缓存
- [ ] 4.2 `tool_loop_executor.py:552`：调 validate()，warning 不阻断

### 阶段 5：Phase 2 — 预算系统（1.5天）

- [ ] 5.1 `execution_budget.py`：加 reserved_for_response + inline_threshold 属性 + _per_tool_tokens
- [ ] 5.2 `erp_agent.py`：__init__ 加 budget 参数 + execute 用 min(budget.remaining, timeout)
- [ ] 5.3 `tool_executor.py:187-199`：改为 ERPAgent(..., budget=_parent_budget)
- [ ] 5.4 `erp_agent.py:_create_agent`：传 budget=self._budget.fork(max_turns=5)
- [ ] 5.5 `department_agent.py`：__init__ 加 budget + _build_output 用 budget.inline_threshold
- [ ] 5.6 `tool_result_envelope.py`：_resolve_budget 两档字符预算切换
- [ ] 5.7 清理死代码：erp_agent.py 的 `**_kwargs` + tool_executor.py 的属性注入

### 阶段 6：Phase 4.3-4.6 — 可靠性（1天）

- [ ] 6.1 `department_agent.py`：_partial_rows 暂存 + CancelledError 返回 PARTIAL
- [ ] 6.2 `erp_agent.py`：超时时检查子 Agent _partial_rows
- [ ] 6.3 `erp_agent_types.py`：加 confidence 字段
- [ ] 6.4 `erp_agent.py:_build_result`：降级时设 confidence=0.6
- [ ] 6.5 `tool_output.py`：FileRef 加 derived_from: tuple[str, ...] = ()
- [ ] 6.6 `session_file_registry.py`：to_snapshot/from_snapshot 适配 derived_from

### 阶段 7：Phase 5.2-5.3 — audit 扩展（半天）

- [ ] 7.1 `tool_audit.py`：ToolAuditEntry 加 prompt_tokens/completion_tokens/trace_id
- [ ] 7.2 `loop_hooks.py`：on_tool_end 加 turn_prompt_tokens/turn_completion_tokens 参数
- [ ] 7.3 `tool_loop_executor.py`：_stream_one_turn 返回拆分 prompt/completion + _execute_tools 传递
- [ ] 7.4 新建 `migrations/088_extend_tool_audit_log.sql`
- [ ] 7.5 `deploy/init-database.sql`：追加 088

### 阶段 8：Phase 5.1 完整版 — ContextVar 全链路（半天）

- [ ] 8.1 `erp_agent.py:187`：_cleanup_staging_delayed 改用 copy_context().run()
- [ ] 8.2 `scheduled_task_agent.py:198`：同上
- [ ] 8.3 `loop_hooks.py:167`：record_tool_audit 的 create_task 也改用 copy_context

### 阶段 9：Phase 5.4 — Langfuse 接入（2天）

- [ ] 9.1 `requirements.txt`：加 langfuse==2.51.3
- [ ] 9.2 新建 `services/agent/observability/langfuse_integration.py`：init_langfuse + @observe 封装 + 静默降级
- [ ] 9.3 `chat_handler.py`：入口 @observe(name="chat_request")
- [ ] 9.4 `tool_loop_executor.py:_stream_one_turn`：Langfuse generation 包装 LLM 调用
- [ ] 9.5 `erp_agent.py:execute`：Langfuse span 包装
- [ ] 9.6 `department_agent.py:execute`：Langfuse span 包装
- [ ] 9.7 `.env.example` 加 LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST
- [ ] 9.8 Langfuse Dashboard 验证：确认 trace/span/generation 链路完整

### 阶段 10：统一测试（半天）

- [ ] 10.1 全量后端测试 `python -m pytest backend/tests/ -q --tb=short`
- [ ] 10.2 手动验证：查询订单 → 检查 inline/staging 分流 + data profile 摘要
- [ ] 10.3 手动验证：降级场景 → 检查 metadata._degraded 无文本前缀
- [ ] 10.4 Langfuse Dashboard：确认 trace_id 串联完整链路
- [ ] 10.5 更新文档：FUNCTION_INDEX.md / PROJECT_OVERVIEW.md

---

## 11. 依赖变更

| 依赖 | 版本 | 理由 |
|------|------|------|
| `langfuse` | `==2.51.3` | Langfuse Python SDK，原生 OTEL，异步上报不阻塞主流程 |

无其他新依赖。build_data_profile 的 pandas/numpy 已在现有依赖中。

---

## 12. 部署与回滚策略

**部署步骤**（全部完成后一次性执行）：
1. `pip install langfuse==2.51.3`（服务器上）
2. `psql -h 127.0.0.1 -U everydayai -d everydayai -f backend/migrations/088_extend_tool_audit_log.sql`
3. 更新 `.env` 加 Langfuse 配置
4. `bash deploy/deploy.sh`（rsync + restart systemd）

**回滚步骤**：
1. 回退代码：`git revert` 到部署前 commit
2. 回滚 migration：执行回滚 SQL（见第 8 节）
3. `bash deploy/deploy.sh` 重新部署
4. Langfuse 停止上报（环境变量删除即可，SDK 静默降级）

**数据库兼容性**：
- 新增列全部有 DEFAULT 值，旧代码写入不会报错
- `asdict(entry)` 自动包含新字段（Python dataclass 行为），Supabase insert 接受额外字段

---

## 13. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| build_data_profile 重写后 stats 格式不兼容 | 中 | 所有现有调用方都改为接收元组，测试覆盖 |
| ERPAgent budget 激活后超时行为变化 | 中 | `min(budget.remaining, dag_global_timeout)` 取较小值，不会更宽松 |
| Langfuse SDK 版本不兼容 | 低 | 锁定 2.51.3，init 失败静默降级不影响主流程 |
| CancelledError 处理改变 DepartmentAgent 异常行为 | 中 | 只在 execute() 层捕获，_dispatch 内部不改 |
| 分区表 ALTER TABLE 锁表 | 低 | PostgreSQL 15+ fast default 不锁表；IF NOT EXISTS 幂等 |

---

## 14. 文档更新清单

- [ ] `docs/FUNCTION_INDEX.md`：新增 validate / inline_threshold / build_data_profile 返回类型 / Langfuse 函数
- [ ] `docs/PROJECT_OVERVIEW.md`：新增 observability/ 子目录说明
- [ ] `docs/document/TECH_ARCHITECTURE.md`：Agent 可观测性章节（trace_id + Langfuse）
- [ ] `.env.example`：LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST

---

## 15. 设计自检

- [x] 项目上下文已加载，4 点完整（架构现状/可复用模块/设计约束/潜在冲突）
- [x] 连锁修改已全部纳入任务拆分（Phase 1: 12 项 / Phase 2: 10 项 / Phase 3: 6 项 / Phase 4: 7 项 / Phase 5: 14 项）
- [x] 15 类边界场景均有处理策略（第 3 节）
- [x] 架构影响评估 7 维度全低风险
- [x] 所有新增文件预估 ≤ 500 行（最大 langfuse_integration.py ~80 行）
- [x] 无模糊版本号依赖（langfuse==2.51.3）
- [x] 评审决策已全部融入设计（两档切换 / trace_id 提前 / 全列 profile / Langfuse）
