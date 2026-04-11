# 技术方案：定时任务心跳系统

> 版本：V1.0 | 日期：2026-04-09
> 状态：方案待确认

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
│  │    ├─ 唤醒 Agent 循环（复用 ChatHandler）     │   │
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
| **最大复用** | 不造新 Agent，复用 ChatHandler + ToolExecutor 全套能力 |
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
    status               VARCHAR(20) DEFAULT 'active',
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

将 `scheduled_tasks` 和 `scheduled_task_runs` 加入 `OrgScopedDB` 租户表列表。

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
        """在 BackgroundTaskWorker._poll_iteration() 中调用"""
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

**集成点**（`background_task_worker.py` 修改约 5 行）：

```python
# 在 _poll_iteration() 中新增一行
async def _poll_iteration(self):
    await self.poll_pending_tasks()        # 已有
    await self.cleanup_stale_tasks()       # 已有
    await self._scheduled_scanner.poll()   # 新增
    await self.check_data_consistency()    # 已有
```

### 4.3 task_executor.py — 任务执行器（核心）

```python
class ScheduledTaskExecutor:
    """唤醒 Agent 执行定时任务"""
    
    async def execute(self, task: dict):
        run_id = await self._create_run(task)
        
        try:
            # 1. 预检积分
            user = await get_user(task["user_id"])
            if user["credits"] < task["max_credits"]:
                return await self._skip(task, run_id, "积分不足，任务已暂停")
            
            # 2. 锁定积分
            await credit_service.lock_credits(
                str(run_id), task["user_id"], 
                task["max_credits"], "scheduled_task"
            )
            
            # 3. 构建轻量上下文
            messages = self._build_light_context(task)
            
            # 4. 唤醒 Agent（复用已有能力）
            result = await self._run_agent(task, messages)
            
            # 5. 推送
            push_status = await push_dispatcher.dispatch(
                task["org_id"], task["push_target"],
                result.text, result.files
            )
            
            # 6. 确认扣费
            actual = calc_credits(result.tokens_used)
            await credit_service.confirm_deduct(str(run_id), actual)
            
            # 7. 成功收尾
            await self._on_success(task, run_id, result, push_status)
            
        except Exception as e:
            await self._on_failure(task, run_id, e)
    
    def _build_light_context(self, task: dict) -> list:
        """轻量上下文：任务指令 + 模板提示 + 上次摘要"""
        system = (
            "你是一个定时任务执行器。执行以下任务并生成结果。\n"
            "要求：\n"
            "1. 完成任务指令中描述的工作\n"
            "2. 生成 ≤200字 的执行摘要（包含关键数据）\n"
            "3. 如有数据文件，输出到 OUTPUT_DIR\n"
            "4. 最终回复应简洁，适合直接推送到企微"
        )
        
        user_msg = f"## 任务\n{task['prompt']}"
        
        # 模板文件注入
        if task.get("template_file"):
            tpl = task["template_file"]
            user_msg += (
                f"\n\n## 模板文件\n"
                f"已放入 staging 目录: staging/{tpl['name']}\n"
                f"请用 pd.read_excel(STAGING_DIR + '/{tpl['name']}') 读取模板结构，"
                f"将查询到的数据按模板格式填入，输出到 OUTPUT_DIR"
            )
        
        # 跨次状态注入
        if task.get("last_summary"):
            user_msg += f"\n\n## 上次执行摘要（可用于对比）\n{task['last_summary']}"
        
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg}
        ]
    
    async def _run_agent(self, task: dict, messages: list) -> AgentResult:
        """复用 Agent 核心循环，无 WebSocket 流式输出"""
        conv_id = f"scheduled_{task['id']}"
        
        tool_executor = ToolExecutor(
            user_id=task["user_id"],
            org_id=task["org_id"],
            conversation_id=conv_id,
        )
        
        # 模板文件复制到 staging
        if task.get("template_file"):
            await copy_template_to_staging(
                task["template_file"]["path"], conv_id
            )
        
        total_tokens = 0
        
        for turn in range(settings.agent_loop_max_turns):
            response = await llm_adapter.chat(
                messages, 
                tools=tool_executor.get_tool_definitions(),
                timeout=task["timeout_sec"]
            )
            total_tokens += response.usage.total_tokens
            
            if not response.tool_calls:
                break
            
            messages.append(response.to_message())
            
            for tc in response.tool_calls:
                result = await tool_executor.execute(tc.name, tc.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
        
        return AgentResult(
            text=response.content or "",
            files=tool_executor.get_output_files(),
            tokens_used=total_tokens
        )
```

### 4.4 push_dispatcher.py — 推送分发

```python
class PushDispatcher:
    """根据 push_target 分发推送"""
    
    async def dispatch(
        self, org_id: str, target: dict, 
        text: str, files: list
    ) -> str:
        """返回 push_status: pushed / push_failed"""
        try:
            match target["type"]:
                case "wecom_group":
                    await self._push_wecom(org_id, target, text, files)
                case "wecom_user":
                    await self._push_wecom(org_id, target, text, files)
                case "web":
                    await self._push_web(target, text, files)
                case "multi":
                    await asyncio.gather(*[
                        self.dispatch(org_id, t, text, files) 
                        for t in target["targets"]
                    ])
            return "pushed"
        except Exception as e:
            logger.error(f"推送失败: {e}")
            return "push_failed"
    
    async def _push_wecom(self, org_id, target, text, files):
        """通过 WS 长连接主动推送"""
        ws_client = wecom_ws_manager.get_client(org_id)
        if not ws_client:
            raise RuntimeError(f"企微 WS 未连接: org_id={org_id}")
        
        chatid = target["chatid"]
        chat_type = 2 if target["type"] == "wecom_group" else 1
        
        # 推送文本（markdown 格式）
        await ws_client.send_proactive(
            chatid=chatid,
            chat_type=chat_type,
            msgtype="markdown",
            content={"content": text}
        )
        
        # 推送文件（如有）
        for f in files:
            media_id = await self._upload_to_wecom(org_id, f)
            await ws_client.send_proactive(
                chatid=chatid,
                chat_type=chat_type,
                msgtype="file",
                content={"media_id": media_id}
            )
    
    async def _push_web(self, target, text, files):
        """通过 WebSocket 推送到 Web 前端"""
        await ws_broadcast(
            user_id=target.get("user_id"),
            event="scheduled_task_result",
            data={"text": text, "files": files}
        )
```

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

### 4.6 失败处理与自愈

```python
async def _on_failure(self, task: dict, run_id: str, error: Exception):
    """失败处理：退积分 → 重试或暂停"""
    # 退还锁定的积分
    try:
        await credit_service.refund_credits(str(run_id))
    except Exception:
        logger.error(f"积分退还失败: run_id={run_id}")
    
    # 记录失败日志
    await self._update_run(run_id, {
        "status": "failed",
        "error_message": str(error)[:500],
        "finished_at": datetime.now(timezone.utc)
    })
    
    consecutive = task["consecutive_failures"] + 1
    
    if consecutive <= task["retry_count"]:
        # 5 分钟后重试
        await self._update_task(task["id"], {
            "next_run_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "status": "active",
            "consecutive_failures": consecutive
        })
        logger.warning(f"任务 {task['name']} 失败，将在5分钟后重试 ({consecutive}/{task['retry_count']})")
        
    elif consecutive >= 3:
        # 连续3次失败 → 自动暂停 + 通知创建者
        await self._update_task(task["id"], {
            "status": "error",
            "consecutive_failures": consecutive
        })
        await self._notify_owner(
            task, 
            f"⚠️ 定时任务「{task['name']}」连续失败{consecutive}次，已自动暂停。\n"
            f"最后错误: {str(error)[:200]}"
        )
        logger.error(f"任务 {task['name']} 连续失败{consecutive}次，已暂停")
        
    else:
        # 恢复到正常调度
        next_run = calc_next_run(task["cron_expr"], task["timezone"])
        await self._update_task(task["id"], {
            "next_run_at": next_run,
            "status": "active",
            "consecutive_failures": consecutive
        })

async def _on_success(self, task, run_id, result, push_status):
    """成功收尾：更新任务 + 记录日志"""
    next_run = calc_next_run(task["cron_expr"], task["timezone"])
    
    await self._update_task(task["id"], {
        "status": "active",
        "next_run_at": next_run,
        "last_run_at": datetime.now(timezone.utc),
        "last_summary": result.text[:500],
        "last_result": {
            "tokens": result.tokens_used,
            "duration_ms": result.duration_ms,
            "files": [{"url": f["url"], "name": f["name"]} for f in result.files]
        },
        "run_count": task["run_count"] + 1,
        "consecutive_failures": 0  # 重置
    })
    
    await self._update_run(run_id, {
        "status": "success",
        "result_summary": result.text[:500],
        "result_files": result.files,
        "push_status": push_status,
        "credits_used": calc_credits(result.tokens_used),
        "tokens_used": result.tokens_used,
        "finished_at": datetime.now(timezone.utc),
        "duration_ms": result.duration_ms
    })
```

---

## 五、企微主动推送协议

### 5.1 aibot_send_msg 协议

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

### 5.2 限制条件

| 条件 | 说明 |
|------|------|
| 前提 | 目标会话中必须有人先给机器人发过消息 |
| 频率 | 30条/分钟/会话，1000条/小时/会话 |
| 消息类型 | markdown、template_card、file、image、voice、video |

### 5.3 ws_client.py 修改

现有 `send_msg` 方法使用 `chattype`（字符串），需要新增 `send_proactive` 方法使用正确的 `chat_type`（整数）：

```python
async def send_proactive(
    self, chatid: str, chat_type: int,
    msgtype: str, content: dict
) -> bool:
    """主动推送消息（非回复，使用 aibot_send_msg 协议）"""
    req_id = f"scheduled_{uuid4().hex[:12]}"
    payload = {
        "cmd": "aibot_send_msg",
        "headers": {"req_id": req_id},
        "body": {
            "chatid": chatid,
            "chat_type": chat_type,  # 1=单聊, 2=群聊（需测试确认）
            "msgtype": msgtype,
            **({msgtype: content})
        }
    }
    await self._ws.send(json.dumps(payload))
    # 等待 ACK（复用现有 _pending_acks 机制）
    return await self._wait_ack(req_id, timeout=10)
```

### 5.4 wecom_chat_targets 联动

创建任务时，推送目标从 `wecom_chat_targets` 表选取（前端下拉选择）。该表被动收集用户和机器人交互过的所有会话，天然满足"先交互后推送"的前提条件。

---

## 六、API 设计

### 6.1 路由

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

### 6.2 创建接口

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

### 6.3 自然语言创建（通过聊天）

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

## 七、前端集成

### 7.1 组件结构

```
frontend/src/components/scheduled-tasks/
├── ScheduledTaskPanel.tsx       # 面板主组件（侧边栏 Tab）
├── TaskList.tsx                 # 任务列表（按状态分组）
├── TaskCard.tsx                 # 单个任务卡片（折叠/展开）
├── TaskForm.tsx                 # 创建/编辑表单
├── NaturalLanguageInput.tsx     # 自然语言输入框
├── PushTargetSelector.tsx       # 推送目标选择器
├── TaskRunHistory.tsx           # 执行历史列表
├── StatusBadge.tsx              # 状态 Badge 组件
├── EmptyState.tsx               # 空状态
└── hooks/
    ├── useScheduledTasks.ts     # 任务 CRUD hooks
    └── useTaskRuns.ts           # 执行历史 hooks
```

### 7.2 状态管理

使用现有的 React 状态模式（与文件面板一致）：

```typescript
interface ScheduledTask {
  id: string;
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

### 7.3 WebSocket 实时更新

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

## 八、文件改动清单

| 操作 | 文件 | 改动量 | 说明 |
|-----|------|-------|------|
| **新建** | `backend/services/scheduler/__init__.py` | ~5行 | 包初始化 |
| **新建** | `backend/services/scheduler/scanner.py` | ~80行 | 调度扫描 |
| **新建** | `backend/services/scheduler/task_executor.py` | ~200行 | 核心执行器 |
| **新建** | `backend/services/scheduler/cron_utils.py` | ~50行 | cron 解析 |
| **新建** | `backend/services/scheduler/push_dispatcher.py` | ~100行 | 推送分发 |
| **新建** | `backend/api/routes/scheduled_tasks.py` | ~180行 | REST API |
| **新建** | `backend/migrations/xxx_scheduled_tasks.sql` | ~70行 | 建表+索引+RPC |
| **修改** | `backend/services/background_task_worker.py` | +5行 | 加扫描调用 |
| **修改** | `backend/services/wecom/ws_client.py` | +25行 | send_proactive 方法 |
| **修改** | `backend/core/org_scoped_db.py` | +2行 | 加租户表 |
| **修改** | `backend/api/routes/__init__.py` | +2行 | 注册路由 |
| **修改** | `backend/requirements.txt` | +1行 | croniter==2.0.7 |
| **新建** | `frontend/src/components/scheduled-tasks/` | ~600行 | 前端面板全部组件 |
| **修改** | `frontend/src/components/` 侧边栏 | ~20行 | 新增任务 Tab |

**后端总计**：新增 ~685行，修改 ~35行，新增依赖 1 个
**前端总计**：新增 ~620行，修改 ~20行

---

## 九、执行计划

| Phase | 内容 | 依赖 |
|-------|------|------|
| **Phase 1** | 数据库迁移（建表+RPC）+ croniter 依赖 + cron_utils | 无 |
| **Phase 2** | scanner + task_executor（Agent 唤醒+执行） | Phase 1 |
| **Phase 3** | push_dispatcher + ws_client.send_proactive | Phase 2 |
| **Phase 4** | REST API 路由 + 自然语言解析接口 | Phase 2 |
| **Phase 5** | 前端任务面板 UI + 交互动画 | Phase 4 |
| **Phase 6** | 失败重试 + 积分卡控 + 日志完善 | Phase 3 |
| **Phase 7** | 端到端测试 + 企微推送验证 | Phase 5+6 |

---

## 十、风险与待确认

| 风险 | 影响 | 缓解 |
|------|------|------|
| 企微 `chat_type` 字段值未确认（1/2 vs single/group） | 推送失败 | Phase 3 实测验证 |
| 企微 WS 主动推送文件需要先上传获取 media_id | 文件推送复杂度增加 | 先只推 markdown 文本，文件推 CDN 链接 |
| Agent 执行超时（ERP 接口慢） | 定时任务卡住 | timeout_sec 兜底 + ExecutionBudget |
| 大量任务同时到期（如整点） | 并发压力 | Semaphore(3) + SKIP LOCKED 排队 |
| 跨次摘要 last_summary 越来越长 | 上下文膨胀 | 限制 500 字，Agent 每次重新精简 |

---

## 十一、参考

| 来源 | 借鉴内容 |
|------|---------|
| OpenClaw Heartbeat | lightContext 省 token、named session 跨次状态 |
| Paperclip | reactive 模式（无任务跳过 LLM）、原子领取 |
| LangGraph Cron | stateful thread 跨次累积、checkpoint |
| Dify Schedule Trigger | 一任务一触发器，简洁直接 |
| Power Automate | 预算/积分卡控 |
| 企微官方文档 | aibot_send_msg 协议 |
