# 技术方案：定时任务心跳系统

> 版本：V2.0 | 日期：2026-04-11
> 状态：方案待实施
> 依赖：[TECH_组织架构与权限模型.md](./TECH_组织架构与权限模型.md)

---

## 一、需求概述

给 AI 加"心跳"——用户创建定时任务后，系统按 cron 表达式自动唤醒 Agent，执行任务（取数据/生成报表），将结果推送到企微群或个人。

### 典型场景

| 场景 | cron | 推送目标 |
|------|------|---------|
| 每日销售日报 | `0 9 * * *` | 企微运营群 |
| 库存低于阈值预警 | `0 8 * * *` | 企微仓管群 |
| 经营周报 | `0 9 * * 1` | 企微老板 |
| 月度采购汇总 | `0 9 1 * *` | 企微采购群 |
| 自定义模板报表 | 用户自定义 | 用户自选 |

### 核心能力

1. **自然语言创建**：用户输入"每天9点推日报到运营群"，Agent 解析后创建
2. **手动管理**：任务栏面板支持新建、编辑、暂停、恢复、删除
3. **模板绑定**：支持绑定 Excel/CSV 模板文件，Agent 按模板格式填数据
4. **企微推送**：通过智能机器人 WS 长连接主动推送（`aibot_send_msg`）
5. **执行日志**：每次执行记录结果、耗时、积分消耗、生成的文件
6. **积分卡控**：执行前检查积分，超额自动暂停
7. **失败自愈**：重试 + 连续失败自动暂停 + 通知用户

---

## 二、架构设计

### 2.1 架构总览

```
┌──────────────────────────────────────────────────┐
│                   前端 (React)                     │
│  任务面板 UI ←→ API Routes ←→ WebSocket 通知       │
└──────────┬───────────────────────────┬────────────┘
           │                           │
┌──────────▼───────────────────────────▼────────────┐
│                   后端 (Python)                     │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  BackgroundTaskWorker（已有，每分钟轮询）      │   │
│  │    ↓                                         │   │
│  │  ScheduledTaskScanner.poll()                 │   │
│  │    ↓ 原子领取到期任务                         │   │
│  │  ScheduledTaskExecutor.execute(task)          │   │
│  │    ├─ 积分预检 + 锁定                        │   │
│  │    ├─ 构建轻量上下文（prompt + 上次摘要）     │   │
│  │    ├─ 唤醒 Agent 循环（参考 ERPAgent 模式）   │   │
│  │    │   ├─ ERP Agent 取数据                   │   │
│  │    │   ├─ 沙盒 code_execute 计算/生成文件     │   │
│  │    │   └─ 返回文本 + 文件                     │   │
│  │    ├─ PushDispatcher 推送                     │   │
│  │    │   ├─ 企微群（aibot_send_msg via WS）     │   │
│  │    │   ├─ 企微个人                            │   │
│  │    │   └─ Web 通知                            │   │
│  │    ├─ 记录执行日志                            │   │
│  │    └─ 更新 next_run_at + 扣积分              │   │
│  └─────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

| 原则 | 实现 |
|------|------|
| **零重构风险** | 照抄 ERPAgent 模式新建 ScheduledTaskAgent，**不动 ChatHandler**，复用 ToolExecutor / ExecutionBudget / context_compressor 等基础设施 |
| **轻量上下文** | 借鉴 OpenClaw lightContext，只注入任务描述 + 上次摘要，不加载历史对话 |
| **调度分离** | 调度器只负责"到点叫人"，执行逻辑全走已有 Agent 链路 |
| **原子领取** | `SELECT FOR UPDATE SKIP LOCKED` 防并发重复执行 |
| **积分约束** | 用积分自然限制任务数量，无需硬编码数量上限 |

---

## 三、数据库设计

### 3.1 scheduled_tasks 表

```sql
CREATE TABLE scheduled_tasks (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID NOT NULL REFERENCES organizations(id),
    user_id              UUID NOT NULL REFERENCES users(id),
    
    -- 任务定义
    name                 VARCHAR(100) NOT NULL,
    prompt               TEXT NOT NULL,
    cron_expr            VARCHAR(50) NOT NULL,
    timezone             VARCHAR(50) DEFAULT 'Asia/Shanghai',
    
    -- 推送目标
    push_target          JSONB NOT NULL,
    
    -- 模板文件（可选）
    template_file        JSONB,
    
    -- 执行控制
    status               VARCHAR(20) DEFAULT 'active' 
        CHECK (status IN ('active','paused','running','error')),
    max_credits          INTEGER DEFAULT 10,
    retry_count          SMALLINT DEFAULT 1,
    timeout_sec          INTEGER DEFAULT 180,
    
    -- 跨次状态
    last_summary         TEXT,
    last_result          JSONB,
    
    -- 调度状态
    next_run_at          TIMESTAMPTZ,
    last_run_at          TIMESTAMPTZ,
    run_count            INTEGER DEFAULT 0,
    consecutive_failures SMALLINT DEFAULT 0,
    
    -- 元数据
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_scheduled_tasks_next_run 
    ON scheduled_tasks(next_run_at) 
    WHERE status = 'active';

CREATE INDEX idx_scheduled_tasks_org 
    ON scheduled_tasks(org_id, user_id);
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `cron_expr` | 标准 5 位 cron 表达式，如 `0 9 * * *`（每天9点） |
| `push_target` | JSONB，支持单目标/多目标，见下方 |
| `template_file` | JSONB，`{"path": "uploads/xxx.xlsx", "name": "模板.xlsx", "url": "..."}` |
| `status` | `active` / `paused` / `error` / `running` |
| `last_summary` | 上次执行的 Agent 生成摘要，注入下次上下文（借鉴 LangGraph stateful cron） |
| `last_result` | `{"tokens": 1500, "duration_ms": 12000, "files": [...]}` |
| `next_run_at` | 带索引，调度器按此字段扫描 |
| `consecutive_failures` | 连续失败计数，达到 3 次自动暂停 |

### 3.2 push_target 结构

```json
// 企微群
{"type": "wecom_group", "chatid": "xxx", "chat_name": "运营群"}

// 企微个人
{"type": "wecom_user", "wecom_userid": "xxx", "name": "张三"}

// Web 通知
{"type": "web", "conversation_id": "xxx"}

// 多目标
{"type": "multi", "targets": [
    {"type": "wecom_group", "chatid": "xxx"},
    {"type": "wecom_user", "wecom_userid": "yyy"}
]}
```

### 3.3 scheduled_task_runs 表（执行日志）

```sql
CREATE TABLE scheduled_task_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    org_id          UUID NOT NULL,
    
    -- 执行信息
    status          VARCHAR(20) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER,
    
    -- 结果
    result_summary  TEXT,
    result_files    JSONB,
    push_status     VARCHAR(20),
    error_message   TEXT,
    
    -- 成本
    credits_used    INTEGER DEFAULT 0,
    tokens_used     INTEGER DEFAULT 0,
    
    -- 重试
    retry_of_run_id UUID REFERENCES scheduled_task_runs(id),
    
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_task_runs_task ON scheduled_task_runs(task_id, started_at DESC);
```

### 3.4 原子领取 RPC

```sql
CREATE FUNCTION claim_due_tasks(p_now TIMESTAMPTZ, p_limit INT)
RETURNS SETOF scheduled_tasks AS $$
    UPDATE scheduled_tasks
    SET status = 'running',
        next_run_at = NULL
    WHERE id IN (
        SELECT id FROM scheduled_tasks
        WHERE status = 'active'
          AND next_run_at <= p_now
        ORDER BY next_run_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *;
$$ LANGUAGE sql;
```

### 3.5 多租户集成

`OrgScopedDB` 的租户表清单是硬编码 frozenset（[org_scoped_db.py:37-66](../../backend/core/org_scoped_db.py#L37-L66)）。需要直接编辑该文件，把两张表加进去：

```python
# backend/core/org_scoped_db.py
TENANT_TABLES = frozenset({
    # ... 现有 36 张表
    "scheduled_tasks",        # 新增
    "scheduled_task_runs",    # 新增
})
```

加进去后，所有 SELECT/UPDATE/DELETE 自动 `.eq("org_id", x)`，INSERT/UPSERT 自动注入 org_id，无需在业务代码里手动加。

---

## 四、后端模块设计

### 4.1 文件结构

```
backend/services/scheduler/
├── __init__.py
├── scanner.py            # 调度扫描（嵌入 BackgroundTaskWorker）
├── task_executor.py      # 任务执行器（唤醒 Agent + 推送）
├── cron_utils.py         # cron 解析 + 下次时间计算
└── push_dispatcher.py    # 推送分发

backend/api/routes/
└── scheduled_tasks.py    # REST API 路由
```

### 4.2 scanner.py — 调度扫描

嵌入已有的 `BackgroundTaskWorker` 轮询循环，不引入 APScheduler。

```python
class ScheduledTaskScanner:
    """每分钟扫描到期任务"""
    
    def __init__(self):
        self._executor = ScheduledTaskExecutor()
        self._semaphore = asyncio.Semaphore(3)  # 最多 3 个并发执行
    
    async def poll(self):
        """在 BackgroundTaskWorker.start() 主循环中调用"""
        now = datetime.now(timezone.utc)
        
        # 原子领取到期任务（SKIP LOCKED 防并发）
        tasks = await db.rpc("claim_due_tasks", {
            "p_now": now.isoformat(),
            "p_limit": 5
        })
        
        if not tasks:
            return
        
        # 并发执行（受 semaphore 控制）
        async with asyncio.TaskGroup() as tg:
            for task in tasks:
                tg.create_task(self._run_with_limit(task))
    
    async def _run_with_limit(self, task: dict):
        async with self._semaphore:
            await self._executor.execute(task)
```

**集成点**（[background_task_worker.py](../../backend/services/background_task_worker.py)）：

实际的 `BackgroundTaskWorker` 没有 `_poll_iteration` 方法，是在 `start()` 主循环里直接调用各个 poll 方法。修改方式：

```python
# backend/services/background_task_worker.py

class BackgroundTaskWorker:
    def __init__(self, db):
        self.db = db
        # ... 现有
        # 新增：定时任务扫描器
        from services.scheduler.scanner import ScheduledTaskScanner
        from services.scheduler.task_executor import ScheduledTaskExecutor
        self._scheduled_scanner = ScheduledTaskScanner(
            executor=ScheduledTaskExecutor(db)
        )
    
    async def start(self):
        """主循环（默认 15s/120s 间隔，对定时任务"分钟级精度"完全够用）"""
        while not self._stopped:
            try:
                await self.poll_pending_tasks()        # 已有
                await self.cleanup_stale_tasks()       # 已有
                await self._scheduled_scanner.poll()   # 新增
                await self.check_data_consistency()    # 已有
                # ... 其他
            except Exception as e:
                logger.exception(f"BackgroundTaskWorker error: {e}")
            
            await asyncio.sleep(self._interval)
```

> **关于精度**：现有 worker 默认 15s 轮询（无 webhook）或 120s（有 webhook），对定时任务"分钟级精度"完全够用。即使 cron 是 `0 9 * * *`（每天9点），实际执行时间会在 9:00-9:02 之间，用户无感。

### 4.3 ScheduledTaskAgent — Agent 执行器（参考 ERPAgent 模式）

> **核心设计决策**：不重构 ChatHandler，**完全照抄 [erp_agent.py](../../backend/services/agent/erp_agent.py) 的模式**。
>
> 理由：ERPAgent 已经是一个成熟的 headless Agent 实现（无 WebSocket、独立循环、结构化返回），它解决的问题（"在主 Agent 之外运行一个独立 Agent 循环"）和定时任务一模一样。Claude Code / OpenAI Agents SDK 的官方建议也是"统一循环 + 不同入口"，ERPAgent 就是 headless 入口的现成实现。

#### 4.3.1 文件结构

```
backend/services/agent/
├── erp_agent.py              # 已有，参考样板
├── erp_agent_types.py        # 已有，类型定义
└── scheduled_task_agent.py   # 新建，照抄 erp_agent 结构
```

#### 4.3.2 ScheduledTaskAgent 类（核心 Agent）

```python
# backend/services/agent/scheduled_task_agent.py
"""
定时任务独立 Agent — 参考 ERPAgent 模式
"""
from __future__ import annotations
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ============================================================
# 结果类型（对应 ERPAgentResult）
# ============================================================

@dataclass
class ScheduledTaskResult:
    """定时任务执行结果"""
    text: str                       # 结论文本（推送给用户）
    summary: str = ""               # ≤500 字摘要（写回 last_summary）
    status: str = "success"         # success | partial | error | timeout
    tokens_used: int = 0
    turns_used: int = 0
    tools_called: List[str] = field(default_factory=list)
    files: List[Dict[str, str]] = field(default_factory=list)  # [{url, name, mime, size}]
    is_truncated: bool = False


# 安全护栏常量（参考 erp_agent_types.py）
TOOL_TIMEOUT = 30.0
MAX_TOTAL_TOKENS = 50000
SCHEDULED_DEADLINE = 180.0       # 比 ERP Agent 略宽（120s），定时任务可能涉及更多步骤
MAX_SCHEDULED_TURNS = 12         # 比 ERP Agent 少（20），任务粒度更明确


# 沙盒输出文件标记正则（来自 sandbox/executor.py 的 [FILE]url|name|mime|size[/FILE]）
_FILE_MARKER_RE = re.compile(
    r"\[FILE\](?P<url>[^|]+)\|(?P<name>[^|]+)\|(?P<mime>[^|]+)\|(?P<size>\d+)\[/FILE\]"
)


class ScheduledTaskAgent:
    """定时任务 Agent — 独立循环，无 WebSocket 依赖"""

    def __init__(self, db: Any, task: Dict[str, Any]) -> None:
        self.db = db
        self.task = task
        self.task_id = task["id"]
        self.user_id = task["user_id"]
        self.org_id = task["org_id"]
        self.conversation_id = f"scheduled_{task['id']}"
        
        # RequestContext（时间事实层，复用 ERPAgent 模式）
        from utils.time_context import RequestContext
        self.request_ctx = RequestContext.build(
            user_id=self.user_id,
            org_id=self.org_id,
            request_id=str(self.task_id),
        )

    async def execute(self) -> ScheduledTaskResult:
        """主入口：执行定时任务，返回结构化结果"""
        total_tokens = 0
        tools_called: List[str] = []
        
        try:
            # 1. 模板文件复制到 staging（如有）
            await self._prepare_template()
            
            # 2. 构建工具列表（复用全部 13 工具）
            from config.phase_tools import build_domain_tools
            all_tools = build_domain_tools("chat")  # 全工具集
            
            # 3. 构建轻量上下文
            messages = self._build_light_context()
            
            # 4. 创建 LLM adapter
            from services.adapters.factory import create_chat_adapter
            from core.config import settings
            adapter = create_chat_adapter(
                settings.agent_loop_model,
                org_id=self.org_id,
                db=self.db,
            )
            
            # 5. 创建 ToolExecutor（共用主 Agent 的）
            from services.agent.tool_executor import ToolExecutor
            executor = ToolExecutor(
                db=self.db,
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                org_id=self.org_id,
                request_ctx=self.request_ctx,
            )
            
            # 6. 全局时间预算
            from services.agent.execution_budget import ExecutionBudget
            budget = ExecutionBudget(
                self.task.get("timeout_sec", SCHEDULED_DEADLINE)
            )
            
            try:
                # 7. 独立工具循环（参考 erp_agent._run_tool_loop）
                text, tokens, turns = await self._run_tool_loop(
                    adapter, executor, messages, all_tools, tools_called, budget
                )
                total_tokens += tokens
            finally:
                await adapter.close()
                # 延迟清理 staging 目录（5 分钟后）
                asyncio.create_task(self._cleanup_staging_delayed())
            
            # 8. 提取沙盒输出的文件
            files = self._extract_files(text)
            
            # 9. 生成摘要（≤500 字，写回 last_summary）
            summary = await self._generate_summary(text, adapter)
            
            return ScheduledTaskResult(
                text=text,
                summary=summary,
                status="success",
                tokens_used=total_tokens,
                turns_used=turns,
                tools_called=tools_called,
                files=files,
                is_truncated="⚠ 输出已截断" in text,
            )
            
        except asyncio.TimeoutError:
            logger.warning(f"ScheduledTask timeout | task={self.task_id}")
            return ScheduledTaskResult(
                text="任务执行超时",
                status="timeout",
                tokens_used=total_tokens,
                tools_called=tools_called,
            )
        except Exception as e:
            logger.error(f"ScheduledTask error | task={self.task_id} | error={e}")
            return ScheduledTaskResult(
                text=f"任务执行出错: {e}",
                status="error",
                tokens_used=total_tokens,
                tools_called=tools_called,
            )
    
    def _build_light_context(self) -> List[Dict[str, Any]]:
        """轻量上下文：任务指令 + 模板提示 + 上次摘要"""
        system_prompt = (
            "你是一个定时任务执行器。执行以下任务并生成结果。\n"
            "要求：\n"
            "1. 完成任务指令中描述的工作\n"
            "2. 如需取数据，调用 erp_agent 工具\n"
            "3. 如需生成报表/计算，调用 code_execute 工具，文件输出到 OUTPUT_DIR\n"
            "4. 最终回复应简洁清晰，适合直接推送到企微群\n"
            "5. 不要使用 ask_user（无人交互场景）"
        )
        
        # 时间事实层
        time_injection = self.request_ctx.for_prompt_injection()
        
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": time_injection},
        ]
        
        # 用户任务消息
        user_msg = f"## 任务\n{self.task['prompt']}"
        
        # 模板文件提示
        if self.task.get("template_file"):
            tpl = self.task["template_file"]
            user_msg += (
                f"\n\n## 模板文件\n"
                f"已放入 staging 目录: staging/{tpl['name']}\n"
                f"使用 pd.read_excel(STAGING_DIR + '/{tpl['name']}') 读取模板结构，"
                f"按模板格式填入数据后输出到 OUTPUT_DIR"
            )
        
        # 上次执行摘要（跨次状态，借鉴 LangGraph stateful cron）
        if self.task.get("last_summary"):
            user_msg += (
                f"\n\n## 上次执行摘要（仅供对比参考）\n"
                f"{self.task['last_summary']}"
            )
        
        messages.append({"role": "user", "content": user_msg})
        return messages
    
    async def _run_tool_loop(
        self,
        adapter: Any,
        executor: Any,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tools_called: List[str],
        budget: Any,
    ) -> tuple:
        """工具循环（参考 erp_agent._run_tool_loop，去掉流式推送）
        
        关键差异：
        - 不调 _notify_progress（无 task_id，无 WebSocket）
        - 不做 L4 TemporalValidator 校验（定时任务不涉及时间敏感事实）
        - 其他逻辑（循环检测、token 预算、上下文压缩）完全一致
        """
        accumulated_text = ""
        total_tokens = 0
        recent_calls: List[str] = []
        context_recovery_used = False
        
        from services.handlers.context_compressor import estimate_tokens, enforce_budget
        from services.agent.erp_agent_types import is_context_length_error
        
        for turn in range(MAX_SCHEDULED_TURNS):
            # 时间预算检查
            if not budget.check_or_log(f"scheduled_turn={turn + 1}"):
                break
            
            # Token 预算检查
            if total_tokens >= MAX_TOTAL_TOKENS:
                logger.warning(f"ScheduledTask token budget exceeded | task={self.task_id}")
                break
            
            # 上下文压缩
            if estimate_tokens(messages) > int(MAX_TOTAL_TOKENS * 0.7):
                enforce_budget(messages, int(MAX_TOTAL_TOKENS * 0.7))
            
            tc_acc: Dict[int, Dict[str, Any]] = {}
            turn_text = ""
            turn_tokens = 0
            
            # 调用 LLM
            try:
                async for chunk in adapter.stream_chat(
                    messages=messages, tools=tools, temperature=0.1
                ):
                    if chunk.content:
                        turn_text += chunk.content
                    if chunk.tool_calls:
                        for tc_delta in chunk.tool_calls:
                            idx = tc_delta.index
                            if idx not in tc_acc:
                                tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            entry = tc_acc[idx]
                            if tc_delta.id:
                                entry["id"] = tc_delta.id
                            if tc_delta.name:
                                entry["name"] = tc_delta.name
                            if tc_delta.arguments_delta:
                                entry["arguments"] += tc_delta.arguments_delta
                    if chunk.prompt_tokens or chunk.completion_tokens:
                        turn_tokens = (chunk.prompt_tokens or 0) + (chunk.completion_tokens or 0)
            except Exception as stream_err:
                if is_context_length_error(stream_err) and not context_recovery_used:
                    context_recovery_used = True
                    enforce_budget(messages, int(MAX_TOTAL_TOKENS * 0.5))
                    messages.append({
                        "role": "user",
                        "content": "上下文过长已自动压缩，请继续完成任务。",
                    })
                    continue
                raise
            
            total_tokens += turn_tokens
            
            # 没有工具调用 → 结束
            if not tc_acc:
                accumulated_text = turn_text or accumulated_text
                break
            
            completed = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))
            
            # 循环检测
            import hashlib
            call_key = "|".join(
                f"{tc['name']}:{hashlib.md5(tc['arguments'].encode()).hexdigest()[:6]}"
                for tc in completed
            )
            recent_calls.append(call_key)
            if len(recent_calls) >= 3 and len(set(recent_calls[-3:])) == 1:
                logger.warning(f"ScheduledTask loop detected | task={self.task_id}")
                break
            
            # 执行工具
            accumulated_text = await self._execute_tools(
                completed, executor, messages, tools_called, turn_text, turn + 1, budget
            )
        
        return accumulated_text, total_tokens, min(turn + 1, MAX_SCHEDULED_TURNS)
    
    async def _execute_tools(
        self,
        completed: List[Dict[str, Any]],
        executor: Any,
        messages: List[Dict[str, Any]],
        tools_called: List[str],
        turn_text: str,
        turn: int,
        budget: Any,
    ) -> str:
        """执行一轮工具调用（简化版 erp_agent._execute_tools，去掉缓存和审计）"""
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
        asst_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in completed
        ]
        messages.append(asst_msg)
        
        accumulated = turn_text
        for tc in completed:
            tool_name = tc["name"]
            tools_called.append(tool_name)
            
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError as e:
                result = f"工具参数 JSON 错误: {e}"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                continue
            
            # 执行工具（带超时）
            tool_timeout = budget.tool_timeout(TOOL_TIMEOUT) if budget else TOOL_TIMEOUT
            try:
                result = await asyncio.wait_for(
                    executor.execute(tool_name, args),
                    timeout=tool_timeout,
                )
            except asyncio.TimeoutError:
                result = f"工具执行超时（{int(tool_timeout)}秒）"
            except Exception as e:
                result = f"工具执行失败: {e}"
            
            # 截断防爆
            from services.agent.tool_result_envelope import wrap_for_erp_agent
            result = wrap_for_erp_agent(tool_name, result)
            
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            accumulated = result
        
        return accumulated
    
    def _extract_files(self, text: str) -> List[Dict[str, str]]:
        """从文本中提取沙盒输出的 [FILE] 标记
        
        沙盒 code_execute 的输出会自动包含 [FILE]url|name|mime|size[/FILE]
        参考 backend/services/sandbox/executor.py 的 _auto_upload_new_files
        """
        files = []
        for match in _FILE_MARKER_RE.finditer(text or ""):
            files.append({
                "url": match.group("url"),
                "name": match.group("name"),
                "mime": match.group("mime"),
                "size": int(match.group("size")),
            })
        return files
    
    async def _generate_summary(self, text: str, adapter: Any) -> str:
        """生成 ≤500 字摘要，写回 last_summary 用于下次执行参考"""
        if len(text) <= 500:
            return text
        try:
            messages = [
                {"role": "system", "content": "用 200 字以内总结以下定时任务执行结果，包含关键数据。"},
                {"role": "user", "content": text[:3000]},  # 截断输入
            ]
            summary = ""
            async for chunk in adapter.stream_chat(messages=messages, temperature=0.3):
                if chunk.content:
                    summary += chunk.content
            return summary[:500]
        except Exception:
            return text[:500]
    
    async def _prepare_template(self) -> None:
        """模板文件复制到 staging 目录"""
        if not self.task.get("template_file"):
            return
        from services.sandbox.functions import get_staging_dir
        from services.file_executor import FileExecutor
        import shutil
        
        tpl = self.task["template_file"]
        staging_dir = get_staging_dir(self.conversation_id)
        staging_dir.mkdir(parents=True, exist_ok=True)
        
        # 从 workspace 复制到 staging
        fe = FileExecutor(self.user_id, org_id=self.org_id)
        src = fe.resolve_safe_path(tpl["path"])
        dst = staging_dir / tpl["name"]
        shutil.copy2(src, dst)
        logger.info(f"Template prepared | task={self.task_id} | dst={dst}")
    
    async def _cleanup_staging_delayed(self) -> None:
        """5 分钟后清理 staging 目录"""
        await asyncio.sleep(300)
        try:
            from services.sandbox.functions import get_staging_dir
            import shutil
            staging_dir = get_staging_dir(self.conversation_id)
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception as e:
            logger.debug(f"Staging cleanup failed | error={e}")
```

#### 4.3.3 任务执行编排器（task_executor.py）

`ScheduledTaskAgent` 只负责"跑 Agent 循环"，外层的"积分锁定 / 推送 / 失败处理 / 状态更新"由编排器处理：

```python
# backend/services/scheduler/task_executor.py
"""定时任务执行编排器：积分 + Agent + 推送 + 状态更新"""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from loguru import logger

from services.credit_service import credit_service
from services.agent.scheduled_task_agent import ScheduledTaskAgent, ScheduledTaskResult
from services.scheduler.push_dispatcher import push_dispatcher
from services.scheduler.cron_utils import calc_next_run


class ScheduledTaskExecutor:
    """定时任务编排器"""
    
    def __init__(self, db):
        self.db = db
    
    async def execute(self, task: dict) -> None:
        """完整的执行流程（被 scanner.poll 调用）"""
        run_id = await self._create_run(task)
        result: ScheduledTaskResult | None = None
        
        try:
            # 使用 credit_lock 上下文管理器（现有 API）
            # 成功 → 自动 confirm；异常 → 自动 refund
            async with credit_service.credit_lock(
                task_id=run_id,
                user_id=task["user_id"],
                amount=task["max_credits"],
                reason=f"定时任务: {task['name']}",
                org_id=task["org_id"],
            ):
                # 1. 跑 Agent
                agent = ScheduledTaskAgent(self.db, task)
                result = await agent.execute()
                
                if result.status in ("error", "timeout"):
                    raise RuntimeError(f"Agent 执行失败: {result.text}")
                
                # 2. 推送
                push_status = await push_dispatcher.dispatch(
                    org_id=task["org_id"],
                    target=task["push_target"],
                    text=result.text,
                    files=result.files,
                )
                
                # 3. 成功收尾（在 credit_lock 内，确认扣费）
                await self._on_success(task, run_id, result, push_status)
        
        except credit_service.InsufficientCreditsError:
            await self._skip(task, run_id, "积分不足，任务自动暂停")
            await self._update_task_status(task["id"], "paused")
        except Exception as e:
            # credit_lock 已自动 refund
            await self._on_failure(task, run_id, e, result)
    
    async def _create_run(self, task: dict) -> str:
        """创建执行记录"""
        run_id = str(uuid4())
        await self.db.table("scheduled_task_runs").insert({
            "id": run_id,
            "task_id": task["id"],
            "org_id": task["org_id"],
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return run_id
    
    async def _on_success(self, task: dict, run_id: str, result, push_status: str) -> None:
        """成功收尾：更新任务 + 记录日志"""
        next_run = calc_next_run(task["cron_expr"], task["timezone"])
        now = datetime.now(timezone.utc)
        
        await self.db.table("scheduled_tasks").update({
            "status": "active",
            "next_run_at": next_run.isoformat(),
            "last_run_at": now.isoformat(),
            "last_summary": result.summary,
            "last_result": {
                "tokens": result.tokens_used,
                "turns": result.turns_used,
                "files": result.files,
            },
            "run_count": task["run_count"] + 1,
            "consecutive_failures": 0,
        }).eq("id", task["id"]).execute()
        
        await self.db.table("scheduled_task_runs").update({
            "status": "success",
            "result_summary": result.summary,
            "result_files": result.files,
            "push_status": push_status,
            "credits_used": task["max_credits"],  # credit_lock 已确认扣费
            "tokens_used": result.tokens_used,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).execute()
    
    async def _on_failure(self, task: dict, run_id: str, error: Exception, result) -> None:
        """失败处理：重试 / 暂停"""
        # credit_lock 已自动 refund，无需手动退还
        
        consecutive = task["consecutive_failures"] + 1
        
        await self.db.table("scheduled_task_runs").update({
            "status": "failed",
            "error_message": str(error)[:500],
            "tokens_used": result.tokens_used if result else 0,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).execute()
        
        if consecutive < task["retry_count"]:
            # 5 分钟后重试
            retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            await self.db.table("scheduled_tasks").update({
                "next_run_at": retry_at.isoformat(),
                "status": "active",
                "consecutive_failures": consecutive,
            }).eq("id", task["id"]).execute()
        elif consecutive >= 3:
            # 连续 3 次失败 → 暂停 + 通知
            await self.db.table("scheduled_tasks").update({
                "status": "error",
                "consecutive_failures": consecutive,
            }).eq("id", task["id"]).execute()
            await self._notify_owner(task, f"⚠️ 任务「{task['name']}」连续失败 {consecutive} 次已暂停")
        else:
            # 算下次正常时间
            next_run = calc_next_run(task["cron_expr"], task["timezone"])
            await self.db.table("scheduled_tasks").update({
                "next_run_at": next_run.isoformat(),
                "status": "active",
                "consecutive_failures": consecutive,
            }).eq("id", task["id"]).execute()
    
    async def _skip(self, task: dict, run_id: str, reason: str) -> None:
        """跳过执行"""
        await self.db.table("scheduled_task_runs").update({
            "status": "skipped",
            "error_message": reason,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).execute()
    
    async def _update_task_status(self, task_id: str, status: str) -> None:
        await self.db.table("scheduled_tasks").update({
            "status": status,
        }).eq("id", task_id).execute()
    
    async def _notify_owner(self, task: dict, message: str) -> None:
        """失败通知任务创建者（通过企微或站内消息）"""
        # 简化实现：写一条系统消息到 user 的 conversation
        # 完整实现见 push_dispatcher
        pass
```

#### 4.3.4 关键复用点

| 现有模块 | 复用方式 |
|---------|---------|
| `ToolExecutor` | 直接实例化，构造参数已匹配 |
| `ExecutionBudget` | 直接用，控制总执行时间和单工具超时 |
| `config.phase_tools.build_domain_tools("chat")` | 取全 13 工具集 |
| `services.adapters.factory.create_chat_adapter` | 创建 LLM adapter |
| `services.handlers.context_compressor` | 上下文压缩，超 70% token 自动触发 |
| `services.agent.tool_result_envelope.wrap_for_erp_agent` | 工具结果截断防爆 |
| `RequestContext` | 时间事实层，注入"今天/昨天" |
| `credit_service.credit_lock` | 积分锁定上下文管理器，自动 confirm/refund |
| 沙盒 `[FILE]` 标记 | 用正则解析，无需新 API |
| `FileExecutor.resolve_safe_path` | 模板文件路径安全检查 |

### 4.4 push_dispatcher.py — 推送分发

```python
# backend/services/scheduler/push_dispatcher.py
"""推送分发：企微群/个人/Web 通知"""
import asyncio
from loguru import logger


class PushDispatcher:
    """根据 push_target 分发推送"""
    
    async def dispatch(
        self, org_id: str, target: dict, 
        text: str, files: list
    ) -> str:
        """返回 push_status: pushed / push_failed"""
        try:
            t = target["type"]
            if t in ("wecom_group", "wecom_user"):
                await self._push_wecom(org_id, target, text, files)
            elif t == "web":
                await self._push_web(target, text, files)
            elif t == "multi":
                await asyncio.gather(*[
                    self.dispatch(org_id, sub, text, files) 
                    for sub in target["targets"]
                ])
            return "pushed"
        except Exception as e:
            logger.error(f"推送失败: {e}")
            return "push_failed"
    
    async def _push_wecom(self, org_id, target, text, files):
        """通过企微 WS 长连接主动推送（aibot_send_msg 协议）"""
        from wecom_ws_runner import wecom_ws_manager
        
        ws_client = wecom_ws_manager.get_client(org_id)
        if not ws_client:
            raise RuntimeError(f"企微 WS 未连接: org_id={org_id}")
        
        chatid = target["chatid"]
        chat_type = 2 if target["type"] == "wecom_group" else 1
        
        # 推送文本（markdown 格式）
        # 文件以 CDN 链接形式追加到 markdown 末尾，避免上传 media_id 的复杂度
        body = text
        if files:
            body += "\n\n📎 **附件：**"
            for f in files:
                body += f"\n- [{f['name']}]({f['url']})"
        
        await ws_client.send_proactive(
            chatid=chatid,
            chat_type=chat_type,
            msgtype="markdown",
            content={"content": body},
        )
    
    async def _push_web(self, target, text, files):
        """通过现有 WebSocketManager 推送到 Web 前端"""
        from services.websocket_manager import websocket_manager
        
        user_id = target.get("user_id")
        if not user_id:
            return
        
        await websocket_manager.send_to_user(user_id, {
            "type": "scheduled_task_result",
            "data": {
                "text": text,
                "files": files,
            },
        })


push_dispatcher = PushDispatcher()
```

> **注意**：定时任务场景**只推 markdown 文本 + CDN 文件链接**，不上传文件到企微（避免 media_id 流程的复杂度）。
> 用户在企微群里点击链接即可下载文件，体验等价。

### 4.5 cron_utils.py — Cron 解析

新增依赖：`croniter==2.0.7`

```python
from croniter import croniter
from zoneinfo import ZoneInfo

def calc_next_run(cron_expr: str, tz: str = "Asia/Shanghai") -> datetime:
    """计算下次执行时间（UTC）"""
    local_tz = ZoneInfo(tz)
    now_local = datetime.now(local_tz)
    cron = croniter(cron_expr, now_local)
    next_local = cron.get_next(datetime)
    return next_local.astimezone(timezone.utc)

def parse_cron_readable(cron_expr: str) -> str:
    """cron 表达式转人类可读描述"""
    # "0 9 * * *" → "每天 09:00"
    # "0 9 * * 1" → "每周一 09:00"
    # "0 9 1 * *" → "每月1日 09:00"
    parts = cron_expr.split()
    minute, hour = parts[0], parts[1]
    dom, month, dow = parts[2], parts[3], parts[4]
    
    time_str = f"{hour.zfill(2)}:{minute.zfill(2)}"
    
    if dom == "*" and dow == "*":
        return f"每天 {time_str}"
    elif dow != "*":
        weekdays = {
            "0": "日", "1": "一", "2": "二", "3": "三",
            "4": "四", "5": "五", "6": "六", "7": "日"
        }
        return f"每周{weekdays.get(dow, dow)} {time_str}"
    elif dom != "*":
        return f"每月{dom}日 {time_str}"
    else:
        return f"cron: {cron_expr}"

def validate_cron(cron_expr: str) -> bool:
    """校验 cron 表达式合法性"""
    try:
        croniter(cron_expr)
        return True
    except (ValueError, KeyError):
        return False
```

> **失败处理与自愈逻辑**已合并到 4.3.3 节 `ScheduledTaskExecutor._on_failure` 中，借助 `credit_service.credit_lock` 上下文管理器自动处理积分退还。

---

## 五、权限集成

> 详细权限模型见 [TECH_组织架构与权限模型.md](./TECH_组织架构与权限模型.md)

### 5.0 权限点

定时任务系统使用以下权限点：

| 权限点 | 名称 | 谁有 |
|--------|------|------|
| `task.view` | 查看定时任务 | 所有职位 |
| `task.create` | 创建定时任务 | 所有职位 |
| `task.edit` | 编辑定时任务 | 所有职位（受数据范围限制） |
| `task.delete` | 删除定时任务 | 所有职位（受数据范围限制） |
| `task.execute` | 立即执行任务 | 主管/副总/老板 |

### 5.1 数据范围矩阵

| 职位 | 看任务 | 编辑/删除任务 | 立即执行 |
|------|-------|--------------|---------|
| **老板** | 全公司 | 全公司 | ✅ |
| **全公司副总** | 全公司 | 全公司 | ✅ |
| **分管副总** | 分管部门成员 | 分管部门成员 | ✅ |
| **主管** | 本部门所有成员 | 本部门所有成员 | ✅ |
| **副主管** | 仅自己 | 仅自己 | ❌ |
| **员工** | 仅自己 | 仅自己 | ❌ |

### 5.2 列表查询：自动数据范围注入

```python
from services.permissions.scope_filter import apply_data_scope

@router.get("/scheduled-tasks")
async def list_tasks(
    current_user: User = Depends(get_current_user),
    view: str = Query("default"),  # default/all/mine
):
    """列表查询：根据用户职位自动过滤"""
    query = db.scheduled_tasks.select().eq('org_id', current_user.org_id)
    
    if view == "mine":
        # 强制只看自己（主管/副总也可以切换到这个视图）
        query = query.eq('user_id', current_user.id)
    else:
        # 默认根据权限自动注入数据范围
        query = await apply_data_scope(
            query, current_user, 'task.view',
            user_id_field='user_id'
        )
    
    return await query.execute()
```

### 5.3 单个操作：权限点检查

```python
from services.permissions.checker import check_permission

@router.post("/scheduled-tasks/{task_id}/pause")
async def pause_task(task_id: str, current_user: User = Depends(get_current_user)):
    task = await get_task(task_id)
    if not task:
        raise HTTPException(404)
    
    # 检查 task.edit 权限 + 数据范围
    if not await check_permission(current_user, 'task.edit', task):
        raise HTTPException(403, "无权操作此任务")
    
    await update_task_status(task_id, 'paused')
    await audit_log('task_paused', current_user, task)


@router.delete("/scheduled-tasks/{task_id}")
async def delete_task(task_id: str, current_user: User = Depends(get_current_user)):
    task = await get_task(task_id)
    if not task:
        raise HTTPException(404)
    
    if not await check_permission(current_user, 'task.delete', task):
        raise HTTPException(403, "无权删除此任务")
    
    await delete_task_record(task_id)


@router.post("/scheduled-tasks/{task_id}/run")
async def run_task_now(task_id: str, current_user: User = Depends(get_current_user)):
    task = await get_task(task_id)
    if not task:
        raise HTTPException(404)
    
    # 立即执行权限：员工/副主管不能强制执行别人的任务
    if not await check_permission(current_user, 'task.execute', task):
        raise HTTPException(403, "无权立即执行此任务")
    
    await scheduled_task_executor.execute_now(task)
```

### 5.4 创建任务：归属当前用户

```python
@router.post("/scheduled-tasks")
async def create_task(
    payload: CreateTaskDto,
    current_user: User = Depends(get_current_user),
):
    # 创建权限：所有职位都有
    if not await check_permission(current_user, 'task.create'):
        raise HTTPException(403)
    
    # 任务自动归属当前用户
    task = await db.scheduled_tasks.insert({
        'org_id': current_user.org_id,
        'user_id': current_user.id,         # 关键：决定了未来谁能看到
        'name': payload.name,
        'prompt': payload.prompt,
        'cron_expr': payload.cron_expr,
        'push_target': payload.push_target,
        'template_file': payload.template_file,
        'next_run_at': calc_next_run(payload.cron_expr),
    })
    return task
```

### 5.5 V1 简化实现说明

V1 阶段权限检查走 [TECH_组织架构与权限模型.md](./TECH_组织架构与权限模型.md) 的硬编码版 `PermissionChecker`：

- 不读 `user_extra_grants` 和 `user_revocations` 表
- 直接根据 `position_code` 走 if-else 分支
- 副主管 `deputy` 等同于 `member`
- 数据范围只支持 `all / dept_subtree / self` 三种

V2 升级时**只改 checker，不改定时任务路由**，因为权限检查全部封装在 `check_permission` 和 `apply_data_scope` 内。

---

## 六、企微主动推送协议

### 6.1 aibot_send_msg 协议

企微智能机器人 WS 协议原生支持主动推送（无需 req_id 回复上下文）：

```json
{
    "cmd": "aibot_send_msg",
    "headers": {
        "req_id": "scheduled_<uuid>"
    },
    "body": {
        "chatid": "CHATID",
        "chat_type": 1,
        "msgtype": "markdown",
        "markdown": {
            "content": "📊 **昨日销售日报**\n\n|店铺|销售额|环比|\n|---|---|---|\n|店铺A|12,345|+5%|"
        }
    }
}
```

### 6.2 限制条件

| 条件 | 说明 |
|------|------|
| 前提 | 目标会话中必须有人先给机器人发过消息 |
| 频率 | 30条/分钟/会话，1000条/小时/会话 |
| 消息类型 | markdown、template_card、file、image、voice、video |

### 6.3 ws_client.py 修改

为定时任务**新增** `send_proactive` 方法，使用 `aibot_send_msg` 协议。

**关键事实**（已通过官方 SDK 源码验证）：
- 参考: [WecomTeam/aibot-node-sdk client.ts](https://github.com/WecomTeam/aibot-node-sdk/blob/main/src/client.ts) `sendMessage()` 方法
- `aibot_send_msg` body 只包含 `chatid` + `msgtype` + 内容字段，**无 chat_type / chattype**
- 企微服务器通过 chatid 自动判断会话类型：
  - **单聊**：chatid 填用户的 userid
  - **群聊**：chatid 填群的 chatid

```python
# 在 backend/services/wecom/ws_client.py 中新增方法

async def send_proactive(
    self,
    chatid: str,
    msgtype: str,
    content: dict,
) -> bool:
    """主动推送消息（aibot_send_msg 协议）

    Args:
        chatid: 单聊填 userid，群聊填群 chatid（服务器自动判断）
        msgtype: markdown / text / file / image / template_card / voice / video
        content: msgtype 对应的内容字典，例如 {"content": "..."}
    """
    if not self._ws or not self._is_connected:
        return False

    req_id = _gen_req_id("scheduled")
    msg = {
        "cmd": WecomCommand.SEND_MSG,
        "headers": {"req_id": req_id},
        "body": {
            "chatid": chatid,
            "msgtype": msgtype,
            msgtype: content,
        },
    }
    await self._safe_send(msg)
    return True
```

> **注意**：现有 ws_client 没有 ACK / pending_acks 机制，无法等待企微的发送确认。如果未来需要确认发送结果，需要先在 ws_client 加 ACK 跟踪机制（V2 优化）。

### 6.4 wecom_chat_targets 联动

创建任务时，推送目标从 `wecom_chat_targets` 表选取（前端下拉选择）。该表被动收集用户和机器人交互过的所有会话，天然满足"先交互后推送"的前提条件。

---

## 七、API 设计

### 7.1 路由

```
POST   /api/scheduled-tasks                  # 创建
GET    /api/scheduled-tasks                  # 列表
GET    /api/scheduled-tasks/:id              # 详情
PATCH  /api/scheduled-tasks/:id              # 修改
DELETE /api/scheduled-tasks/:id              # 删除
POST   /api/scheduled-tasks/:id/run          # 立即执行
POST   /api/scheduled-tasks/:id/pause        # 暂停
POST   /api/scheduled-tasks/:id/resume       # 恢复
GET    /api/scheduled-tasks/:id/runs         # 执行历史
GET    /api/scheduled-tasks/chat-targets     # 可用推送目标列表
```

### 7.2 创建接口

```
POST /api/scheduled-tasks
Content-Type: application/json

{
    "name": "每日销售日报",
    "prompt": "查询昨日各店铺销售数据，按销售额降序生成汇总表格，对比前日标注增降幅",
    "cron_expr": "0 9 * * *",
    "push_target": {
        "type": "wecom_group",
        "chatid": "xxx",
        "chat_name": "运营群"
    },
    "template_file": {
        "path": "uploads/xxxx_销售日报模板.xlsx",
        "name": "销售日报模板.xlsx",
        "url": "https://cdn.xxx/..."
    },
    "max_credits": 10,
    "retry_count": 1,
    "timeout_sec": 180
}
```

**响应**：

```json
{
    "success": true,
    "data": {
        "id": "uuid",
        "name": "每日销售日报",
        "cron_expr": "0 9 * * *",
        "cron_readable": "每天 09:00",
        "status": "active",
        "next_run_at": "2026-04-10T01:00:00Z",
        "push_target": {...},
        "created_at": "..."
    }
}
```

### 7.3 自然语言创建（通过聊天）

用户在任务面板的自然语言输入框提交后，前端调用一个解析接口：

```
POST /api/scheduled-tasks/parse
{
    "text": "每天早上9点把昨日销售日报发到运营群"
}
```

后端用 LLM 解析，返回结构化结果：

```json
{
    "success": true,
    "data": {
        "name": "每日销售日报",
        "prompt": "查询昨日各店铺销售数据，生成汇总表格",
        "cron_expr": "0 9 * * *",
        "cron_readable": "每天 09:00",
        "suggested_target": {
            "type": "wecom_group",
            "chatid": "xxx",
            "chat_name": "运营群"
        }
    }
}
```

前端将解析结果填入表单，用户确认后调用创建接口。

---

## 八、前端集成

> 完整 UI 设计见 [UI_定时任务面板设计.md](./UI_定时任务面板设计.md)

### 8.1 组件结构

```
frontend/src/components/scheduled-tasks/
├── ScheduledTaskPanel.tsx       # 抽屉主面板（右侧 Drawer，跟 SearchPanel 一致）
├── PanelHeader.tsx              # 面板头部
├── ViewSwitcher.tsx             # 视图切换器（按职位显示）
├── TaskList.tsx                 # 任务列表（按状态分组）
├── TaskCard.tsx                 # 任务卡片（含创建者徽标）
├── TaskForm.tsx                 # 创建/编辑表单
├── NaturalLanguageInput.tsx     # 自然语言输入框
├── PushTargetSelector.tsx       # 推送目标选择器
├── TemplateFileUploader.tsx     # 模板文件上传
├── TaskRunHistory.tsx           # 执行历史列表
├── EmptyState.tsx               # 空状态
└── hooks/
    ├── useScheduledTasks.ts     # 任务 CRUD hooks
    ├── useTaskParse.ts          # 自然语言解析
    └── useTaskRuns.ts           # 执行历史 hooks
```

### 8.2 状态管理

使用 Zustand store 模式（与现有 store 一致）：

```typescript
interface ScheduledTask {
  id: string;
  org_id: string;
  user_id: string;          // 创建者
  
  // 创建者展示信息（后端 join 返回）
  creator?: {
    name: string;
    avatar?: string;
    department_id?: string;
    department_name?: string;
    department_type?: string;  // ops/finance/...
    position_code?: string;    // boss/vp/manager/deputy/member
  };
  
  name: string;
  prompt: string;
  cron_expr: string;
  cron_readable: string;
  status: 'active' | 'paused' | 'error' | 'running';
  push_target: PushTarget;
  template_file?: TemplateFile;
  next_run_at: string | null;
  last_run_at: string | null;
  last_summary: string | null;
  run_count: number;
  consecutive_failures: number;
}

interface TaskRun {
  id: string;
  task_id: string;
  status: 'running' | 'success' | 'failed' | 'timeout';
  started_at: string;
  duration_ms: number;
  result_summary: string;
  result_files: FileInfo[];
  credits_used: number;
  error_message?: string;
}
```

### 8.3 WebSocket 实时更新

任务执行状态通过现有 WebSocket 连接推送到前端：

```typescript
// 监听定时任务事件
ws.on('scheduled_task_started', (data) => {
  updateTaskStatus(data.task_id, 'running');
});

ws.on('scheduled_task_completed', (data) => {
  updateTaskStatus(data.task_id, 'active');
  addRunToHistory(data.run);
});

ws.on('scheduled_task_failed', (data) => {
  updateTaskStatus(data.task_id, data.new_status);
  showToast(`任务「${data.name}」执行失败: ${data.error}`);
});
```

---

## 九、文件改动清单

### 后端

| 操作 | 文件 | 改动量 | 说明 |
|-----|------|-------|------|
| **新建** | `backend/services/agent/scheduled_task_agent.py` | ~450行 | **照抄 ERPAgent 模式**的独立 Agent 循环 |
| **新建** | `backend/services/scheduler/__init__.py` | ~5行 | 包初始化 |
| **新建** | `backend/services/scheduler/scanner.py` | ~80行 | 调度扫描，集成到 BackgroundTaskWorker |
| **新建** | `backend/services/scheduler/task_executor.py` | ~180行 | 编排器（积分锁 + Agent + 推送 + 状态更新） |
| **新建** | `backend/services/scheduler/cron_utils.py` | ~50行 | cron 解析（croniter） |
| **新建** | `backend/services/scheduler/push_dispatcher.py` | ~80行 | 推送分发（企微 + Web） |
| **新建** | `backend/api/routes/scheduled_tasks.py` | ~200行 | REST API（含权限集成） |
| **新建** | `backend/migrations/060_scheduled_tasks.sql` | ~80行 | 建表+索引+RPC |
| **修改** | `backend/services/background_task_worker.py` | +6行 | 加 scanner 实例化 + start() 调用 |
| **修改** | `backend/services/wecom/ws_client.py` | +30行 | 新增 `send_proactive` 方法 |
| **修改** | `backend/core/org_scoped_db.py` | +2行 | 加 2 张租户表 |
| **修改** | `backend/api/routes/__init__.py` | +2行 | 注册路由 |
| **修改** | `backend/requirements.txt` | +1行 | `croniter==2.0.7` |

**后端总计**：新增 ~1125 行，修改 ~41 行，新增依赖 1 个

### 前端

| 操作 | 文件 | 改动量 |
|-----|------|-------|
| **新建** | `frontend/src/components/scheduled-tasks/` | ~1360 行（详见 UI 文档）|
| **新建** | `frontend/src/stores/useScheduledTaskStore.ts` | ~150 行 |
| **新建** | `frontend/src/types/scheduledTask.ts` | ~80 行 |
| **新建** | `frontend/src/services/scheduledTaskService.ts` | ~120 行 |
| **新建** | `frontend/src/hooks/usePermission.ts` | ~40 行 |
| **修改** | `frontend/src/pages/Chat.tsx` | +5 行（集成抽屉） |
| **修改** | `frontend/src/components/chat/layout/ChatHeader.tsx` | +5 行（新增按钮） |
| **修改** | `frontend/src/contexts/wsMessageHandlers.ts` | +20 行（WS 事件） |

**前端总计**：新增 ~1750 行，修改 ~30 行

---

## 十、执行计划

| Phase | 内容 | 工作量 | 依赖 |
|-------|------|-------|------|
| **Phase 0** | 权限模块基础（详见 [TECH_组织架构与权限模型.md](./TECH_组织架构与权限模型.md) Phase 1）| 2 天 | 无 |
| **Phase 1** | 扩展 `/api/auth/me` 返回 `current_org.member` + permissions | 0.5 天 | Phase 0 |
| **Phase 2** | migration 060: scheduled_tasks + scheduled_task_runs + claim_due_tasks RPC + croniter 依赖 + cron_utils | 0.5 天 | Phase 1 |
| **Phase 3** | **ScheduledTaskAgent**（照抄 ERPAgent，~450 行） | 1 天 | Phase 2 |
| **Phase 4** | scheduler/scanner + task_executor 编排器 + 集成到 BackgroundTaskWorker.start() | 0.5 天 | Phase 3 |
| **Phase 5** | push_dispatcher + ws_client.send_proactive + **企微 chat_type 实测** | 1 天 | Phase 4 |
| **Phase 6** | REST API 路由（含权限集成 check_permission + apply_data_scope）| 1 天 | Phase 4 |
| **Phase 7** | 前端：useScheduledTaskStore + 类型定义 + service + usePermission hook | 0.5 天 | Phase 6 |
| **Phase 8** | 前端：ScheduledTaskPanel + TaskCard + ViewSwitcher + TaskList | 1.5 天 | Phase 7 |
| **Phase 9** | 前端：TaskForm + NaturalLanguageInput + TemplateFileUploader + 执行历史 | 1 天 | Phase 8 |
| **Phase 10** | 端到端测试（5 个职位场景）+ 三主题视觉验证（Classic/Claude/Linear）+ 企微推送验证 | 0.5 天 | Phase 9 |

**总计**：约 9.5 天（V1 完整版）

---

## 十一、风险与待确认

| 风险 | 影响 | 缓解 |
|------|------|------|
| ✅ 企微 `chat_type` 字段问题 | ~~推送失败~~ | 已通过官方 [aibot-node-sdk](https://github.com/WecomTeam/aibot-node-sdk/blob/main/src/client.ts) 源码确认：**根本不需要 chat_type**，企微通过 chatid 自动判断 |
| Agent 执行超时（ERP 接口慢） | 定时任务卡住 | `ExecutionBudget` 兜底 + 单工具 timeout |
| 大量任务同时到期（如整点） | 并发压力 | `Semaphore(3)` 限制 + `SKIP LOCKED` 排队 |
| 跨次摘要 last_summary 越来越长 | 上下文膨胀 | 限制 500 字，每次重新精简 |
| ltree 扩展未装 | 部门子树查询失败 | migration 050 第一行 `CREATE EXTENSION` 自动安装 |
| ws_client 没有 ACK 机制 | 推送失败无法感知 | V1 接受（fire-and-forget），V2 加 ACK 跟踪 |
| ChatHandler 重构 | ~~影响现有聊天功能~~ | ✅ **完全规避** — 走 ERPAgent 模式，不动 ChatHandler |
| ToolExecutor API 缺失 | ~~执行不了~~ | ✅ **已确认** — 用 `config.phase_tools.build_domain_tools()` 取工具，沙盒输出从 `[FILE]` 标记解析 |
| CreditService API 缺失 | ~~积分扣费失败~~ | ✅ **已确认** — `credit_service.credit_lock()` 上下文管理器现成 |

---

## 十二、参考

| 来源 | 借鉴内容 |
|------|---------|
| OpenClaw Heartbeat | lightContext 省 token、named session 跨次状态 |
| Paperclip | reactive 模式（无任务跳过 LLM）、原子领取 |
| LangGraph Cron | stateful thread 跨次累积、checkpoint |
| Dify Schedule Trigger | 一任务一触发器，简洁直接 |
| Power Automate | 预算/积分卡控 |
| 企微官方文档 | aibot_send_msg 协议 |
