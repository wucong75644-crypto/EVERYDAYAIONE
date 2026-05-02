# TECH: 工具返回结构化统一 — 全量 AgentResult 对齐

> 版本：v2.0 | 日期：2026-05-02 | 状态：方案设计
>
> v1.0 → v2.0：全量审计补全 80+ 错误返回点（原 28 处严重低估），新增语义分类（error/timeout/empty/validation）

## 1. 项目上下文

### 架构现状
单循环 Agent 架构下，ChatHandler 主循环直接调用 13+ 工具。工具返回类型不统一：
- `erp_agent`/`erp_analyze` 已返回 `AgentResult`（结构化，含 status/error_message/file_ref）
- `data_query`/`code_execute`/`file_tools`/`media_tools`/`crawler`/`search` 返回纯字符串（"❌ ..." 前缀标识错误）

### 可复用模块
- `AgentResult`（`agent_result.py`）：已有完整的结构化字段 + 序列化方法 `to_tool_content()`
- `ToolOutput` 已废弃，通过 `__getattr__` 别名指向 `AgentResult`，150+ 调用方无感兼容
- 停止策略 `classify_tool_result()` 已支持 AgentResult 分类路径

### 设计约束
- `ToolLoopExecutor` Phase 3 已有 `isinstance(result, ToolOutput)` 分支处理 AgentResult
- ChatHandler `chat_tool_mixin.py` 已有 `isinstance(result, AgentResult)` 分支
- 所有工具通过 `tool_executor.py` 的 `execute()` 分发，返回值直接传递给上层

### 潜在冲突
- `data_query` 成功时返回格式化文本（含 schema/表格），改为 AgentResult 后 `to_tool_content()` 序列化需保持等效
- `code_execute` 成功时返回沙盒 stdout + `[FILE]` 标记，序列化需保留原有格式
- `tool_result_envelope.py` 的 `wrap_for_erp_agent()` 对字符串做截断/staging，需适配 AgentResult

---

## 2. 问题分析

### 2.1 停止策略失效

`data_query` SQL 错误时 `audit_status="success"`（工具本身没崩），返回纯字符串 `"❌ SQL 错误：..."`。
停止策略 `classify_tool_result("❌...", "success")` → `ResultClass.SUCCESS`，连续失败无法升级。

### 2.2 LLM 信息呈现差

纯字符串错误缺少结构：
- 没有明确的"这是错误"语义标记
- DuckDB 的 `Did you mean "X"?` 建议淹没在文本中
- 模型无法区分"工具执行失败"和"查询结果为空"和"参数校验不通过"

### 2.3 行业对标

| 代 | 代表 | 做法 |
|---|------|------|
| Gen 1 | OpenAI 早期 | 纯文本 content，关键词判断 |
| Gen 2 | LangChain | 可选 error 字段 |
| **Gen 3** | **Anthropic Managed Agents** | 独立 ErrorBlock + 结构化 status |

本项目 erp_agent 已在 Gen 3，其他工具停在 Gen 1。

---

## 3. 设计方案

### 3.1 核心原则

**一句话**：所有工具统一返回 `AgentResult`，错误路径必须用正确的 `status` + `error_message` 填充。

### 3.2 语义分类规范

不是所有非成功返回都是 "error"。定义 5 种语义状态：

| status | 语义 | 停止策略分类 | 示例 |
|--------|------|-------------|------|
| `"success"` | 正常完成 | SUCCESS | "查询到5条订单" |
| `"empty"` | 执行成功但无结果 | SUCCESS | "知识库中未找到相关经验" |
| `"error"` | 工具执行失败（可重试/不可重试） | RETRYABLE/FATAL | "SQL 错误：列不存在" |
| `"timeout"` | 执行超时 | RETRYABLE | "代码执行超时（120秒）" |
| `"ask_user"` | 需要用户输入 | NEEDS_INPUT | "请指定查询时间范围" |

### 3.3 AgentResult 错误返回规范

```python
# 可重试错误（SQL 语法错误、网络抖动）
AgentResult(
    summary="SQL 错误：列名不存在 → 修正：使用 \"店铺名\"",
    status="error",
    error_message="Binder Error: Referenced column...",
    metadata={"retryable": True, "suggestion": "店铺名"},
)

# 永久性错误（权限、功能关闭）
AgentResult(
    summary="文件操作功能已关闭，请联系管理员启用",
    status="error",
    error_message="Feature disabled",
    metadata={"retryable": False},
)

# 超时
AgentResult(
    summary="代码执行超时（120秒），请简化逻辑或减少数据量",
    status="timeout",
    error_message="Execution timeout: 120s",
)

# 空结果（不是错误）
AgentResult(
    summary="查询无结果，未找到符合条件的数据",
    status="empty",
)

# 参数校验（可重试，模型需调整参数）
AgentResult(
    summary="搜索关键词不能为空",
    status="error",
    error_message="Validation: query is required",
    metadata={"retryable": True},
)
```

### 3.4 `audit_status` 联动

`tool_loop_helpers.py` 的 `invoke_tool_with_cache()` 需在工具返回 `AgentResult` 失败状态时将 `audit_status` 同步标记：

```python
# tool_loop_helpers.py — invoke_tool_with_cache 改造
r = await executor.execute(tool_name, args)
# 新增：AgentResult 状态 → audit_status 同步
from services.agent.agent_result import AgentResult
if isinstance(r, AgentResult):
    if r.is_failure:  # status in {"error", "timeout"}
        audit_status = "timeout" if r.status == "timeout" else "error"
    else:
        audit_status = "success"
```

### 3.5 data_query SQL 错误精准化

```python
# data_query_format.py — format_sql_error 改造

def format_sql_error(error_msg: str, columns: list[str]) -> AgentResult:
    """SQL 错误 → 结构化 AgentResult，高亮 DuckDB 建议"""
    import re
    
    # 提取 DuckDB 的 "Did you mean" 建议
    match = re.search(r'Did you mean "([^"]+)"', error_msg)
    suggestion = match.group(1) if match else None
    
    if suggestion:
        summary = (
            f"SQL 错误：列名不存在\n"
            f"→ 修正：使用 \"{suggestion}\" 替代\n"
            f"→ 示例：SELECT \"{suggestion}\" FROM data"
        )
    else:
        # 无建议时给前 30 列
        cols_str = ", ".join(f'"{c}"' for c in columns[:30])
        summary = f"SQL 错误：{error_msg}\n可用列名：{cols_str}"
    
    return AgentResult(
        summary=summary,
        status="error",
        error_message=error_msg,
        metadata={"suggestion": suggestion, "retryable": True},
    )
```

### 3.6 tool_result_envelope 适配

`wrap_for_erp_agent()` 当前只处理 `str` 类型。改为：
- `AgentResult` → 不经过 envelope 截断（已有 `to_tool_content()` 控制长度）
- `str` → 保持原有 envelope 逻辑（兼容过渡期）

---

## 4. 全量错误返回审计（80+ 处）

### 4.1 data_query（12 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 1 | `data_query_executor.py:45` | `"❌ SQL 安全限制：不支持多语句查询"` | error | 参数校验 |
| 2 | `data_query_executor.py:47` | `"❌ SQL 安全限制：仅支持 SELECT 查询"` | error | 参数校验 |
| 3 | `data_query_executor.py:91` | `"❌ 参数错误：file 不能为空"` | error | 参数校验 |
| 4 | `data_query_executor.py:96` | `f"❌ {e}"` (FileNotFoundError) | error | 文件不存在 |
| 5 | `data_query_executor.py:98` | `f"❌ 安全限制：{e}"` (PermissionError) | error | 权限(不可重试) |
| 6 | `data_query_executor.py:102` | `f"❌ 不支持的文件格式：{suffix}"` | error | 参数校验 |
| 7 | `data_query_executor.py:420` | `f"❌ {e}"` (TimeoutError) | timeout | 查询超时 |
| 8 | `data_query_executor.py:426` | `format_sql_error(str(e), columns)` | error | SQL 错误(可重试) |
| 9 | `data_query_executor.py:429` | `format_sql_error(str(e), columns)` | error | SQL 错误(可重试) |
| 10 | `data_query_executor.py:505` | `"❌ xlsx 导出需要 DuckDB spatial 扩展"` | error | 环境限制 |
| 11 | `data_query_executor.py:514` | `"❌ 安全限制：输出路径不允许是符号链接"` | error | 安全限制 |
| 12 | `data_query_executor.py:525` | `f"❌ 不支持的导出格式：{ext}"` | error | 参数校验 |
| 13 | `data_query_executor.py:531` | `f"❌ {e}"` (导出超时) | timeout | 导出超时 |
| 14 | `data_query_executor.py:538` | `f"❌ 导出失败：{e}"` | error | 导出失败 |

### 4.2 code_execute — 沙盒层（10 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 15 | `sandbox/executor.py:63` | `f"❌ 代码验证失败:\n{error}"` | error | 代码校验 |
| 16 | `sandbox/executor.py:175` | `f"❌ 沙盒进程异常: {e}"` | error | 系统错误 |
| 17 | `sandbox/sandbox_worker.py:231` | `"❌ 子进程沙盒不支持 async/await"` | error | 语法限制 |
| 18 | `sandbox/sandbox_worker.py:262` | `f"⏱ 代码执行超时（{timeout}秒）"` | timeout | 执行超时 |
| 19 | `sandbox/sandbox_worker.py:317` | `("error", f"❌ 代码验证失败:\n{error}")` | error | 代码校验 |
| 20 | `sandbox/sandbox_worker.py:414` | `("error", f"❌ 执行错误:\n{short_tb}")` | error | 运行时错误 |
| 21 | `sandbox/validators.py:46` | `"代码不能为空"` | error | 参数校验 |
| 22 | `sandbox/validators.py:52` | `f"语法错误: {e.msg}（第{e.lineno}行）"` | error | 语法错误 |
| 23 | `sandbox/validators.py:64` | `"安全检查未通过:\n" + violations` | error | 安全限制 |
| 24 | `sandbox/validators.py:76-103` | 各种 `f"禁止...:{name}()"` | error | 安全限制 |

### 4.3 code_execute — Kernel 层（4 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 25 | `sandbox/kernel_manager.py:299` | `("error", "❌ Kernel 进程已断开")` | error | 系统错误 |
| 26 | `sandbox/kernel_manager.py:308` | `("timeout", f"⏱ Kernel 响应超时")` | timeout | 超时 |
| 27 | `sandbox/kernel_manager.py:311` | `("error", "❌ Kernel 进程已退出")` | error | 系统错误 |
| 28 | `sandbox/kernel_manager.py:316` | `("error", f"❌ Kernel 返回无效 JSON")` | error | 系统错误 |

### 4.4 file_tools（15 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 29 | `tool_executor.py:584` | `"文件操作功能已关闭"` | error | 功能关闭(不可重试) |
| 30 | `tool_executor.py:597` | `f"权限不足: {e}"` (file_list) | error | 权限(不可重试) |
| 31 | `tool_executor.py:600` | `f"文件操作失败: {e}"` (file_list) | error | 系统错误 |
| 32 | `tool_executor.py:607` | `f"权限不足: {e}"` (file_search) | error | 权限(不可重试) |
| 33 | `tool_executor.py:610` | `f"文件操作失败: {e}"` (file_search) | error | 系统错误 |
| 34 | `tool_executor.py:620` | `f"Unknown file tool: {tool_name}"` | error | 系统错误(不可重试) |
| 35 | `tool_executor.py:633` | `f"权限不足: {e}"` (read/write/edit) | error | 权限(不可重试) |
| 36 | `tool_executor.py:636` | `f"文件操作失败: {e}"` (read/write/edit) | error | 系统错误 |
| 37 | `tool_executor.py:687` | `f"目录为空: {path}"` | empty | 空结果 |
| 38 | `file_read_extensions.py:243` | `f"页码格式错误: '{part}'"` | error | 参数校验 |
| 39 | `file_read_extensions.py:245` | `f"页码必须从 1 开始"` | error | 参数校验 |
| 40 | `file_read_extensions.py:247` | `f"页码超出范围"` | error | 参数校验 |
| 41 | `file_read_extensions.py:249` | `f"起始页不能大于结束页"` | error | 参数校验 |
| 42 | `file_read_extensions.py:255-259` | 同上（3 处） | error | 参数校验 |
| 43 | `file_read_extensions.py:263` | `"未指定有效页码"` | error | 参数校验 |

### 4.5 media_tools（6 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 44 | `media_tool_executor.py:45` | `f"积分计算失败：{e}"` (image) | error | 系统错误 |
| 45 | `media_tool_executor.py:76` | `f"图片生成失败：{fail_msg}"` | error | 生成失败 |
| 46 | `media_tool_executor.py:80` | `f"图片生成失败：{e}"` | error | 系统错误 |
| 47 | `media_tool_executor.py:101` | `f"积分计算失败：{e}"` (video) | error | 系统错误 |
| 48 | `media_tool_executor.py:130` | `f"视频生成失败：{fail_msg}"` | error | 生成失败 |
| 49 | `media_tool_executor.py:134` | `f"视频生成失败：{e}"` | error | 系统错误 |

### 4.6 ERP 工具层（4 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 50 | `erp_tool_executor.py:113` | `f"ERP操作失败：{e}"` (write) | error | 远程错误 |
| 51 | `erp_tool_executor.py:151` | `f"ERP操作失败：{e}"` (query) | error | 远程错误 |
| 52 | `erp_tool_executor.py:233` | `f"Unknown local tool: {tool_name}"` | error | 系统错误 |
| 53 | `erp_tool_executor.py:250` | `f"本地查询失败: {e}"` | error | 查询失败 |
| 54 | `erp_tool_executor.py:282` | `f"统一查询失败: {e}"` | error | 查询失败 |

### 4.7 search/knowledge/web_search（3 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 55 | `tool_executor.py:150` | `"查询关键词不能为空"` | error | 参数校验 |
| 56 | `tool_executor.py:154` | `f"知识库中未找到与「{query}」相关的经验"` | empty | 空结果 |
| 57 | `tool_executor.py:174` | `"搜索查询不能为空"` | error | 参数校验 |

### 4.8 social_crawler（3 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 58 | `tool_executor.py:775` | `"社交媒体爬虫功能未启用"` | error | 功能关闭 |
| 59 | `tool_executor.py:790` | `"搜索关键词不能为空"` | error | 参数校验 |
| 60 | `tool_executor.py:808` | `f"爬取失败：{result.error}"` | error | 远程错误 |

### 4.9 manage_scheduled_task（2 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 61 | `tool_executor.py:296` | `"此功能仅企业用户可用"` | error | 权限(不可重试) |
| 62 | `tool_executor.py:300` | `"请指定操作：create / list / ..."` | error | 参数校验 |

### 4.10 fetch_all_pages（4 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 63 | `tool_executor.py:344` | `"❌ 必须指定 tool 和 action 参数"` | error | 参数校验 |
| 64 | `tool_executor.py:349` | dispatcher 字符串错误 | error | 系统错误 |
| 65 | `tool_executor.py:370` | `f"❌ 翻页查询失败: {error}"` | error | 远程错误 |
| 66 | `tool_executor.py:374` | `f"查询结果为空（{tool}:{action}）"` | empty | 空结果 |

### 4.11 invoke_tool_with_cache 异常包装（2 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 67 | `tool_loop_helpers.py:100` | `f"工具执行超时（{timeout}秒）"` | timeout | 超时 |
| 68 | `tool_loop_helpers.py:104` | `f"工具执行失败: {e}"` | error | 系统错误 |

### 4.12 tool_loop_executor 并行异常（1 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 69 | `tool_loop_executor.py:705` | `f"工具执行失败: {e}"` | error | 系统错误 |

### 4.13 file_upload（2 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 70 | `file_upload.py:70` | `f"❌ 文件过大（{size}MB）"` | error | 参数校验(不可重试) |
| 71 | `file_upload.py:84` | `f"❌ 文件处理失败: {name} ({e})"` | error | 系统错误 |

### 4.14 其他（4 处）

| # | 文件:行 | 当前返回 | 改造后 status | 语义 |
|---|---------|---------|-------------|------|
| 72 | `tool_executor.py:113` | `"当前对话暂无历史消息"` | empty | 空结果 |
| 73 | `tool_executor.py:441` | `"代码执行功能已关闭"` | error | 功能关闭 |
| 74 | `tool_executor.py:446` | `"代码不能为空"` | error | 参数校验 |
| 75 | `erp_local_sync_trigger.py:59` | `f"✗ {sync_type} 同步失败: {e}"` | error | 远程错误 |

**总计：75 处明确错误/空结果返回点需要改造为 AgentResult。**

---

## 5. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| data_query 返回 AgentResult | `tool_loop_executor.py:734` | 已有 isinstance 分支，无需改 |
| data_query 返回 AgentResult | `chat_tool_mixin.py:106` | 已有 isinstance 分支，无需改 |
| data_query 返回 AgentResult | `tool_result_envelope.py` | AgentResult 跳过 envelope 截断 |
| audit_status 联动 | `tool_loop_helpers.py:77` | 新增 AgentResult.is_failure/timeout 检查 |
| format_sql_error 返回类型 | `data_query_executor.py:426,429` | 调用方直接 return（不拼接） |
| code_execute 返回 AgentResult | `sandbox/executor.py` | execute() 返回类型变更 |
| sandbox_worker 错误格式 | `sandbox/sandbox_worker.py` | result_queue.put 改为 AgentResult 序列化 |
| file_tools 返回 AgentResult | `tool_executor.py:584-636` | 每个 return 点改造 |
| media 返回 AgentResult | `media_tool_executor.py` | 每个 return 点改造 |
| erp_tool_executor 返回 | `erp_tool_executor.py` | 已在 ERPAgent 内部循环，需确认不影响 |
| FailureReflectionHook | `loop_hooks.py:349` | `str(result)` 已兼容 AgentResult |

### 不需要改动的
- `ToolLoopExecutor` Phase 3 — `isinstance(result, ToolOutput)` 自动兼容
- ChatHandler 主循环工具结果注入 — 走 `to_tool_content()` 序列化
- 停止策略 `classify_tool_result()` — 已有 AgentResult 分类路径
- 150+ 处 `from tool_output import ToolOutput` — 别名机制无感兼容

---

## 6. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| 工具返回 None | 包装为 AgentResult(status="error", summary="工具返回为空") | tool_loop_helpers |
| 工具抛异常（未被内部捕获） | invoke_tool_with_cache 包装为 AgentResult(status="error") | tool_loop_helpers |
| data_query 成功但结果为空 | AgentResult(status="empty") — 不是 error | data_query_executor |
| code_execute stdout 含 [FILE] | AgentResult(summary=stdout) — [FILE] 提取在 Phase 3 检查 content 字符串不受影响 | sandbox/executor |
| 大结果落盘 staging | AgentResult 有 file_ref 字段 + to_tool_content() 生成 [DATA_REF]，不经过 envelope | tool_result_envelope |
| 缓存命中 | 缓存存储原始 AgentResult 对象（pickle 支持） | tool_result_cache |
| sandbox_worker 子进程返回 | 子进程通过 result_queue 传递序列化数据，executor 侧包装为 AgentResult | sandbox/executor |
| erp_tool_executor 在 ERPAgent 内部循环 | ERPAgent 已用 AgentResult，erp_tool_executor 改造后对齐 | erp_tool_executor |

---

## 7. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | 不新增模块，只统一返回类型 | 低 | — |
| 数据流向 | 工具 → AgentResult → to_tool_content() → messages。与 erp_agent 一致 | 低 | — |
| 扩展性 | metadata dict 可扩展，未来新工具直接复用 | 低 | — |
| 耦合度 | 所有工具依赖 AgentResult（公共基础类型，合理耦合） | 低 | — |
| 一致性 | 从"部分结构化"变为"全部结构化"，消除不一致 | 低（正向） | — |
| 可观测性 | audit_status 联动后，停止策略日志正确记录失败 | 低（正向） | — |
| 可回滚性 | 纯代码变更，git revert 即可 | 低 | — |

---

## 8. 任务拆分

### Phase 1：audit_status 联动 + data_query 错误结构化（最紧急，解决循环 bug）

| 步骤 | 内容 | 文件 | 改造点数 |
|------|------|------|---------|
| 1.1 | `tool_loop_helpers.py` — AgentResult.is_failure → audit_status 同步 | 1 文件 | ~5 行 |
| 1.2 | `data_query_format.py` — format_sql_error 返回 AgentResult + DuckDB 建议提取 | 1 文件 | ~30 行 |
| 1.3 | `data_query_executor.py` — 14 处错误/超时返回改为 AgentResult | 1 文件 | ~50 行 |
| 1.4 | `tool_result_envelope.py` — AgentResult 跳过 str envelope 截断 | 1 文件 | ~5 行 |
| 1.5 | `chat_tools.py` — 删除 `SELECT "店铺名称"` 写死示例 | 1 文件 | ~2 行 |
| 1.6 | 测试适配 + 新增 | 2 文件 | ~60 行 |

### Phase 2：code_execute 全量结构化（14 处）

| 步骤 | 内容 | 文件 | 改造点数 |
|------|------|------|---------|
| 2.1 | `sandbox/executor.py` — 错误路径返回 AgentResult | 1 文件 | 2 处 |
| 2.2 | `sandbox/sandbox_worker.py` — 错误/超时返回标准化 | 1 文件 | 4 处 |
| 2.3 | `sandbox/validators.py` — 验证错误返回 AgentResult | 1 文件 | 6 处 |
| 2.4 | `sandbox/kernel_manager.py` — kernel 错误返回 AgentResult | 1 文件 | 4 处 |
| 2.5 | `tool_executor.py:441,446` — code_execute 入口校验 | 1 文件 | 2 处 |
| 2.6 | 测试适配 | 2 文件 | ~40 行 |

### Phase 3：file_tools 全量结构化（15 处）

| 步骤 | 内容 | 文件 | 改造点数 |
|------|------|------|---------|
| 3.1 | `tool_executor.py:584-687` — file_* 错误/空结果返回 AgentResult | 1 文件 | 9 处 |
| 3.2 | `file_read_extensions.py` — PDF 页码校验返回 AgentResult | 1 文件 | 7 处 |
| 3.3 | 测试适配 | 1 文件 | ~30 行 |

### Phase 4：media_tools + social_crawler 结构化（9 处）

| 步骤 | 内容 | 文件 | 改造点数 |
|------|------|------|---------|
| 4.1 | `media_tool_executor.py` — generate_image/video 错误返回 | 1 文件 | 6 处 |
| 4.2 | `tool_executor.py:775-808` — social_crawler 错误返回 | 1 文件 | 3 处 |
| 4.3 | 测试适配 | 1 文件 | ~20 行 |

### Phase 5：ERP 工具层 + search/knowledge + 定时任务（12 处）

| 步骤 | 内容 | 文件 | 改造点数 |
|------|------|------|---------|
| 5.1 | `erp_tool_executor.py` — 远程/本地错误返回 | 1 文件 | 5 处 |
| 5.2 | `tool_executor.py:150,154,174` — search/knowledge 错误/空结果 | 1 文件 | 3 处 |
| 5.3 | `tool_executor.py:296,300` — manage_scheduled_task 校验 | 1 文件 | 2 处 |
| 5.4 | `tool_executor.py:344-374` — fetch_all_pages 错误/空结果 | 1 文件 | 4 处 |
| 5.5 | 测试适配 | 1 文件 | ~30 行 |

### Phase 6：invoke_tool_with_cache + file_upload 兜底（4 处）

| 步骤 | 内容 | 文件 | 改造点数 |
|------|------|------|---------|
| 6.1 | `tool_loop_helpers.py:100,104` — 异常包装为 AgentResult | 1 文件 | 2 处 |
| 6.2 | `tool_loop_executor.py:705` — _invoke_safe 异常返回 AgentResult | 1 文件 | 1 处 |
| 6.3 | `file_upload.py:70,84` — 上传错误返回 AgentResult | 1 文件 | 2 处 |
| 6.4 | `erp_local_sync_trigger.py:59` — 同步失败 | 1 文件 | 1 处 |

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| data_query 成功返回格式变化影响 LLM | 中 | Phase 1 只改错误路径，成功路径暂不动 |
| sandbox_worker 子进程序列化 AgentResult | 中 | 子进程仍返回 (status, text) tuple，executor 侧包装为 AgentResult |
| code_execute [FILE] 标记提取受影响 | 低 | to_tool_content() 仍输出含 [FILE] 的文本，Phase 3 提取逻辑不变 |
| erp_tool_executor 在 ERPAgent 内部循环 | 低 | ERPAgent 已用 AgentResult，内部工具对齐后更一致 |
| 缓存存储 AgentResult | 低 | ToolResultCache 使用 pickle，支持任意类型 |
| 改造点多（75 处）回归风险 | 中 | 分 6 Phase 逐步改造，每 Phase 独立测试+部署 |

---

## 10. 设计自检

- [x] 项目上下文已加载（架构现状/可复用模块/设计约束/潜在冲突）
- [x] 全量审计覆盖 75 处错误返回点（按工具分类 + 行号定位）
- [x] 语义分类规范（error/timeout/empty/ask_user）
- [x] 连锁修改已全部纳入任务拆分
- [x] 边界场景均有处理策略
- [x] 架构影响评估无高风险项
- [x] 所有改动在已有文件中，不新增文件
- [x] 无新增依赖
- [x] 分 6 Phase 逐步实施，每 Phase 可独立部署验证
