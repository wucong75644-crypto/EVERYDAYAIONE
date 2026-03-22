## 技术设计：定时任务与主动推送

### 1. 现有代码分析

**已阅读文件**：
- `backend/services/intent_router.py` — 路由分发核心，千问 Function Calling 选工具
- `backend/config/erp_tools.py` — ERP 工具定义模板（`_build_query_tool` 模式）
- `backend/services/kuaimai/dispatcher.py` — ERP 工具执行调度器
- `backend/main.py` — lifespan 管理，Worker 启动/停止模式
- `backend/services/background_task_worker.py` — async worker 模板（`while is_running` 循环）
- `backend/services/kuaimai/erp_sync_worker.py` — Redis 分布式锁 + 多频率调度
- `backend/api/routes/wecom.py` — push 接口（WS 长连接推送）
- `backend/wecom_ws_runner.py` — `get_ws_client()` 独立进程
- `frontend/src/pages/Chat.tsx` — 主布局：左 Sidebar + 中 Content（flex）
- `frontend/src/stores/useMessageStore.ts` — Zustand Slice 模式
- `frontend/src/stores/slices/index.ts` — 4 个 Slice 导出
- `frontend/src/services/api.ts` — Axios 实例 + token 拦截器

**可复用模块**：
- `BackgroundTaskWorker` 的 async worker 模式 → 定时任务调度器直接复用
- `WecomPushRequest` + `push_message()` → 任务执行完推送直接调用
- `_build_query_tool()` → 新工具注册照搬模板
- `Sidebar.tsx` 的组件模式 → 右侧栏参照实现
- Zustand Slice 模式 → 新增 `scheduledTaskSlice`

**设计约束**：
- 必须兼容现有 lifespan 启停模式（`start()`/`stop()`）
- 工具定义必须符合 `ROUTER_TOOLS` 的 Function Calling schema
- 前端布局必须在现有 `flex` 结构内扩展，不能破坏响应式
- 推送走现有 WS 通道（`get_ws_client().send_msg()`），不新增通道

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 `schedule_task` 工具 | `smart_model_config.py` | 添加到 `ROUTER_TOOLS` 列表 |
| 路由解析新工具 | `intent_router.py` | `_parse_response()` 新增分支 |
| 新增 GenerationType | `intent_router.py` | `GenerationType` enum 扩展 |
| 新增 Worker | `main.py` | lifespan 中启动/停止 |
| Chat 页面布局扩展 | `Chat.tsx` | 添加右侧栏组件 + 状态 |
| Store 扩展 | `useMessageStore.ts` | 注入新 Slice |
| Slice 索引 | `slices/index.ts` | 导出新 Slice |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| WS 长连接断开时任务触发 | 标记 `last_result.error`，下次连上不补发（避免过期数据） | TaskExecutor |
| 用户删除账号 / 被禁用 | ON DELETE CASCADE 清理任务 | DB 外键 |
| cron 解析失败（自然语言歧义） | AI 解析后返回预览让用户确认，不自动创建 | 路由层 |
| 任务执行超时（ERP 慢查询） | 单任务 30s 超时，标记 failed，不阻塞其他任务 | TaskExecutor |
| 并发：同一任务重复触发 | APScheduler 内置 `max_instances=1` | Scheduler |
| quiet hours 内触发 | 非紧急任务延迟到窗口结束后执行 | TaskExecutor |
| 任务数量极大（单用户 100+） | 前端分页展示，后端限制每用户最多 50 个 active 任务 | API + 前端 |
| 快速连续创建/删除 | 前端防抖 300ms + 后端幂等校验 | 前端交互 + API |
| APScheduler 重启后任务恢复 | 启动时从 DB 加载所有 active 任务重新注册 | SchedulerService.startup() |
| 时区问题 | 统一存 UTC，展示时按用户时区转换，默认 Asia/Shanghai | 全链路 |

---

### 3. 技术栈

- 前端：React + TypeScript + Zustand + TailwindCSS（现有）
- 后端：Python 3.12 + FastAPI（现有）
- 数据库：Supabase PostgreSQL（现有）
- 调度器：APScheduler 3.10.4（新增）
- 缓存/锁：Redis（现有）
- 推送通道：企微 WS 长连接（现有）

---

### 4. 目录结构

#### 新增文件

**后端**：
```
backend/services/scheduler/
├── __init__.py
├── scheduler_service.py    # APScheduler 管理 + 任务 CRUD
├── task_executor.py        # 任务执行器（调 ERP → 格式化 → 推送）
└── task_parser.py          # 自然语言 → cron/interval/once 解析

backend/api/routes/scheduled_tasks.py  # REST API（CRUD + 状态管理）
backend/config/scheduler_tools.py      # schedule_task 工具定义
```

**前端**：
```
frontend/src/components/chat/
├── TaskSidebar.tsx          # 右侧栏主容器（任务列表 + 操作）
├── TaskCard.tsx             # 单个任务卡片（状态 + 操作按钮）
└── TaskDetailModal.tsx      # 统一弹窗（左设置 + 右执行记录）

frontend/src/stores/slices/
└── scheduledTaskSlice.ts    # 定时任务状态管理

frontend/src/services/
└── scheduledTask.ts         # 定时任务 API 调用

frontend/src/types/
└── scheduledTask.ts         # 类型定义
```

#### 修改文件

| 文件 | 修改内容 |
|-----|---------|
| `backend/main.py` | lifespan 中启动/停止 SchedulerService |
| `backend/config/smart_model_config.py` | ROUTER_TOOLS 新增 schedule_task |
| `backend/services/intent_router.py` | GenerationType 扩展 + _parse_response 新分支 |
| `frontend/src/pages/Chat.tsx` | 引入 TaskSidebar + 右侧栏折叠状态 |
| `frontend/src/stores/useMessageStore.ts` | 注入 scheduledTaskSlice |
| `frontend/src/stores/slices/index.ts` | 导出新 Slice |

---

### 5. 数据库设计

#### 表：scheduled_tasks

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | UUID | PK | gen_random_uuid() | 主键 |
| user_id | UUID | FK, NOT NULL | - | 关联用户 |
| name | TEXT | NOT NULL | - | 任务名称（如"每日待发货汇总"） |
| task_type | TEXT | NOT NULL | - | erp_query / ai_generate / reminder |
| task_params | JSONB | NOT NULL | '{}' | 执行参数（action + params） |
| schedule_type | TEXT | NOT NULL | - | cron / interval / once |
| schedule_expr | TEXT | NOT NULL | - | "0 9 * * *" / "7200" / ISO时间 |
| timezone | TEXT | NOT NULL | 'Asia/Shanghai' | 用户时区 |
| push_channel | TEXT | NOT NULL | 'wecom' | 推送渠道 |
| push_chatid | TEXT | | NULL | 指定推送目标（空则自动查找） |
| quiet_start | TEXT | | NULL | 安静时段开始（如 "22:00"） |
| quiet_end | TEXT | | NULL | 安静时段结束（如 "08:00"） |
| status | TEXT | NOT NULL | 'active' | active / paused / completed / failed |
| last_run_at | TIMESTAMPTZ | | NULL | 上次执行时间 |
| last_result | JSONB | | NULL | 上次执行结果 |
| next_run_at | TIMESTAMPTZ | | NULL | 下次执行时间 |
| run_count | INT | NOT NULL | 0 | 累计执行次数 |
| fail_count | INT | NOT NULL | 0 | 连续失败次数 |
| max_retries | INT | NOT NULL | 3 | 最大连续失败次数，超过自动暂停 |
| created_at | TIMESTAMPTZ | NOT NULL | now() | 创建时间 |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | 更新时间 |

**索引**：
- `idx_scheduled_tasks_user_status`：(user_id, status) — 用户任务列表查询
- `idx_scheduled_tasks_next_run`：(next_run_at) WHERE status = 'active' — 调度器扫描

**外键**：
- user_id → users(id) ON DELETE CASCADE

**约束**：
- CHECK (task_type IN ('erp_query', 'ai_generate', 'reminder'))
- CHECK (schedule_type IN ('cron', 'interval', 'once'))
- CHECK (status IN ('active', 'paused', 'completed', 'failed'))

---

### 6. API 设计

#### 6.1 GET /api/scheduled-tasks

- **描述**：获取当前用户的定时任务列表
- **请求参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|-----|------|------|--------|------|
| status | string | 否 | - | 筛选状态（active/paused/failed） |
| page | int | 否 | 1 | 页码 |
| page_size | int | 否 | 20 | 每页条数 |

- **成功响应（200）**：
```json
{
  "success": true,
  "data": {
    "total": 5,
    "items": [
      {
        "id": "uuid",
        "name": "每日待发货汇总",
        "task_type": "erp_query",
        "task_params": {"action": "search_order", "params": {"status": "待发货"}},
        "schedule_type": "cron",
        "schedule_expr": "0 9 * * *",
        "schedule_display": "每天 09:00",
        "timezone": "Asia/Shanghai",
        "status": "active",
        "last_run_at": "2026-03-21T09:00:00+08:00",
        "last_result": {"success": true, "summary": "共12单待发货"},
        "next_run_at": "2026-03-22T09:00:00+08:00",
        "run_count": 15,
        "created_at": "2026-03-15T10:00:00+08:00"
      }
    ]
  }
}
```

#### 6.2 POST /api/scheduled-tasks

- **描述**：创建定时任务
- **请求体**：
```json
{
  "name": "每日待发货汇总",
  "task_type": "erp_query",
  "task_params": {"action": "search_order", "params": {"status": "待发货"}},
  "schedule_type": "cron",
  "schedule_expr": "0 9 * * *",
  "timezone": "Asia/Shanghai",
  "push_chatid": null,
  "quiet_start": "22:00",
  "quiet_end": "08:00"
}
```
- **成功响应（201）**：返回完整任务对象
- **错误响应**：

| 状态码 | 说明 |
|--------|------|
| 400 | 参数校验失败（如 cron 表达式无效） |
| 409 | 超过用户任务上限（50个） |
| 401 | 未登录 |

#### 6.3 PUT /api/scheduled-tasks/{task_id}

- **描述**：编辑定时任务
- **请求体**：同 POST（部分更新）
- **成功响应（200）**：返回更新后的任务对象
- **错误响应**：404（任务不存在）、403（不是自己的任务）

#### 6.4 PATCH /api/scheduled-tasks/{task_id}/status

- **描述**：切换任务状态（暂停/恢复）
- **请求体**：
```json
{ "status": "paused" }
```
- **成功响应（200）**：返回更新后的任务对象

#### 6.5 DELETE /api/scheduled-tasks/{task_id}

- **描述**：删除定时任务
- **成功响应（204）**：无内容
- **错误响应**：404 / 403

---

### 7. 前端状态管理

#### scheduledTaskSlice

```typescript
interface ScheduledTask {
  id: string;
  name: string;
  task_type: 'erp_query' | 'ai_generate' | 'reminder';
  task_params: Record<string, unknown>;
  schedule_type: 'cron' | 'interval' | 'once';
  schedule_expr: string;
  schedule_display: string;
  timezone: string;
  status: 'active' | 'paused' | 'completed' | 'failed';
  last_run_at: string | null;
  last_result: { success: boolean; summary?: string; error?: string } | null;
  next_run_at: string | null;
  run_count: number;
  created_at: string;
}

interface ScheduledTaskSlice {
  // 状态
  scheduledTasks: ScheduledTask[];
  taskSidebarOpen: boolean;
  taskLoading: boolean;

  // 操作
  setScheduledTasks: (tasks: ScheduledTask[]) => void;
  addScheduledTask: (task: ScheduledTask) => void;
  updateScheduledTask: (id: string, updates: Partial<ScheduledTask>) => void;
  removeScheduledTask: (id: string) => void;
  setTaskSidebarOpen: (open: boolean) => void;
  setTaskLoading: (loading: boolean) => void;
}
```

---

### 8. 后端核心模块设计

#### 8.1 SchedulerService（scheduler_service.py）

```
职责：APScheduler 生命周期 + 任务 CRUD + DB 同步

方法：
- startup() → 从 DB 加载 active 任务 → 注册到 APScheduler
- shutdown() → 停止 APScheduler
- create_task(user_id, params) → 入库 + 注册调度
- update_task(task_id, params) → 更库 + 重新注册
- pause_task(task_id) → 更新状态 + 移除调度
- resume_task(task_id) → 更新状态 + 重新注册
- delete_task(task_id) → 删库 + 移除调度
- _register_job(task) → 解析 schedule_expr → APScheduler add_job
- _on_job_executed(task_id) → 更新 last_run_at/run_count/next_run_at
- _on_job_failed(task_id, error) → 更新 fail_count，超过 max_retries 自动暂停
```

#### 8.2 TaskExecutor（task_executor.py）

```
职责：执行具体任务 → 格式化结果 → 推送

方法：
- execute(task) → 分发到对应执行器
- _execute_erp_query(task_params) → 调 ErpDispatcher → 返回数据
- _execute_ai_generate(task_params) → 调 AI 模型 → 返回文本
- _execute_reminder(task_params) → 直接返回提醒文本
- _format_result(raw_data, task) → Markdown 格式化
- _push_result(user_id, message, chatid) → 调 push API 推送
- _check_quiet_hours(task) → 判断是否在安静时段
```

#### 8.3 TaskParser（task_parser.py）

```
职责：自然语言 → 结构化任务定义

方法：
- parse(user_message) → ScheduledTaskCreateParams
  内部调千问 Function Calling，提取:
  - name: 任务名称
  - task_type: 类型
  - task_params: 执行参数
  - schedule_type: 调度类型
  - schedule_expr: cron/interval/时间
```

#### 8.4 schedule_task 工具定义（scheduler_tools.py）

```python
SCHEDULE_TOOL = {
    "type": "function",
    "function": {
        "name": "schedule_task",
        "description": "创建/查看/管理用户的定时任务（如定时查询ERP、定时提醒等）",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "pause", "resume", "delete"],
                    "description": "操作类型"
                },
                "task_name": {
                    "type": "string",
                    "description": "任务名称（create 时必填）"
                },
                "task_type": {
                    "type": "string",
                    "enum": ["erp_query", "reminder"],
                    "description": "任务类型"
                },
                "schedule_description": {
                    "type": "string",
                    "description": "调度描述（如'每天早上9点'、'每2小时'、'明天下午3点'）"
                },
                "query_description": {
                    "type": "string",
                    "description": "查询描述（如'待发货订单汇总'、'库存低于安全库存的商品'）"
                },
                "reminder_text": {
                    "type": "string",
                    "description": "提醒内容（task_type=reminder 时）"
                },
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（pause/resume/delete 时必填）"
                }
            },
            "required": ["action"]
        }
    }
}
```

---

### 9. 开发任务拆分

#### 阶段1：数据库 + 后端基础（P0）

- [ ] 1.1 创建 `scheduled_tasks` 表（Supabase SQL）
- [ ] 1.2 实现 `SchedulerService`（CRUD + APScheduler 集成）
- [ ] 1.3 实现 `TaskExecutor`（ERP 查询 + 推送）
- [ ] 1.4 实现 REST API（`scheduled_tasks.py` 路由）
- [ ] 1.5 `main.py` 集成 SchedulerService 启停

#### 阶段2：AI 路由集成（P1）

- [ ] 2.1 定义 `schedule_task` 工具（`scheduler_tools.py`）
- [ ] 2.2 `smart_model_config.py` 注册工具到 ROUTER_TOOLS
- [ ] 2.3 `intent_router.py` 扩展 GenerationType + 解析逻辑
- [ ] 2.4 实现 `TaskParser`（自然语言 → cron 表达式）
- [ ] 2.5 AI 对话创建任务的端到端流程打通

#### 阶段3：前端侧边栏（P1）

- [ ] 3.1 类型定义（`types/scheduledTask.ts`）
- [ ] 3.2 API 服务（`services/scheduledTask.ts`）
- [ ] 3.3 Zustand Slice（`slices/scheduledTaskSlice.ts`）
- [ ] 3.4 `TaskSidebar` 组件（任务列表 + 状态展示）
- [ ] 3.5 `TaskCard` 组件（单卡片 + 操作按钮）
- [ ] 3.6 `TaskDetailModal` 组件（左右分栏：设置 + 执行记录）
- [ ] 3.7 `Chat.tsx` 集成右侧栏（布局调整 + 折叠状态）
- [ ] 3.8 `useMessageStore.ts` + `slices/index.ts` 注入新 Slice

#### 阶段4：体验优化（P2）

- [ ] 4.1 quiet hours 实现
- [ ] 4.2 失败重试 + 指数退避
- [ ] 4.3 任务执行日志查看（最近 N 次结果）
- [ ] 4.4 WebSocket 实时更新（任务状态变更 → 前端 store 同步）

#### 阶段5：测试 + 文档（P2）

- [ ] 5.1 后端单测（SchedulerService + TaskExecutor + API）
- [ ] 5.2 前端组件测试
- [ ] 5.3 文档更新

---

### 10. 依赖变更

- **新增**：`APScheduler==3.10.4`（理由：轻量级进程内调度器，支持 cron/interval/date 三种触发器，SQLAlchemy 持久化，与 asyncio 兼容。选择 3.x 而非 4.x-alpha，因为 4.x 还不稳定）

---

### 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| APScheduler 重启丢失任务 | 高 | 启动时从 DB 全量加载恢复，不依赖 APScheduler 内置持久化 |
| 企微 WS 断连导致推送失败 | 中 | 失败记录到 last_result，不重试推送（避免过期数据）|
| ERP 查询超时阻塞调度线程 | 中 | 每个任务 asyncio.wait_for(30s)，独立 try-except |
| 自然语言 cron 解析不准 | 中 | 解析后返回预览让用户确认，不直接创建 |
| 前端右侧栏影响聊天区域宽度 | 低 | 可折叠设计，默认折叠，不影响现有体验 |
| 用户创建大量任务拖慢系统 | 低 | 每用户上限 50 个 active 任务 |

---

### 12. 文档更新清单

- [ ] FUNCTION_INDEX.md — 新增 scheduler 模块函数
- [ ] TECH_ARCHITECTURE.md — 补充定时任务架构图
- [ ] PROJECT_OVERVIEW.md — 新增文件说明

---

### 13. UI 设计

#### 13.1 整体布局

```
┌────────────┬──────────────────────────────┬─────────────────┐
│            │  ChatHeader            [📋]  │                 │
│  左侧栏    ├──────────────────────────────┤  📋 定时任务(3) │
│            │                              │─────────────────│
│  (对话列表) │                              │  全部|运行中|暂停│
│            │       MessageArea            │                 │
│            │                              │  ┌─TaskCard─┐   │
│            │                              │  │          │   │
│            │                              │  └──────────┘   │
│            │                              │  ┌─TaskCard─┐   │
│            │                              │  │          │   │
│            ├──────────────────────────────┤  └──────────┘   │
│            │  InputArea                   │ [+ 新建任务]    │
└────────────┴──────────────────────────────┴─────────────────┘
   256px            flex-1                       280px
```

- 右侧栏为常驻面板，默认收起，点击 ChatHeader 📋 图标展开/收起
- 收起时只显示 📋 图标按钮

#### 13.2 TaskSidebar（右侧栏 280px）

```
┌─────────────────────┐
│ 📋 定时任务 (3)   ✕ │  ← 标题 + 数量 + 关闭
│─────────────────────│
│ [全部] [运行中] [暂停]│  ← 筛选 tab
│─────────────────────│
│                     │
│ ┌─ TaskCard ──────┐ │  ← 点击整张卡片 → 打开弹窗
│ │ 🟢 每日待发货汇总│ │
│ │ ⏰ 每天 09:00    │ │
│ │ 下次: 明天 09:00 │ │
│ └─────────────────┘ │
│                     │
│ ┌─ TaskCard ──────┐ │
│ │ 🟡 库存预警提醒  │ │
│ │ ⏰ 每2小时       │ │
│ │ 下次: 14:00     │ │
│ └─────────────────┘ │
│                     │
│ [+ 新建任务]        │  ← 底部固定
└─────────────────────┘
```

**空状态**：
```
┌─────────────────────┐
│       📭            │
│   还没有定时任务     │
│  试试对AI说：        │
│  "每天9点查待发货"   │
│ [+ 新建任务]        │
└─────────────────────┘
```

#### 13.3 TaskCard（任务卡片）

```
┌───────────────────────────┐
│ 🟢 每日待发货汇总          │  ← 状态点 + 名称
│ ⏰ 每天 09:00              │  ← 调度描述
│ 📦 ERP查询 · 已执行15次    │  ← 类型 + 次数
│ 下次: 03-22 09:00          │  ← 下次执行
└───────────────────────────┘

状态点：🟢 active  🟡 paused  🔴 failed
hover: bg-gray-50, cursor pointer，整张卡片可点击
```

#### 13.4 TaskDetailModal（统一弹窗，左右分栏 600px）

新建和编辑用同一个组件 `TaskDetailModal`，通过 `task` prop 区分：
- `task = null` → 新建模式（空表单 + 右侧空状态 + [取消][创建]）
- `task = {...}` → 编辑模式（预填数据 + 右侧历史 + [删除][暂停][保存]）

**新建弹窗**：
```
┌──────────────────────┬─────────────────────┐
│  新建定时任务      ✕  │  执行记录            │
│──────────────────────│                     │
│                      │                     │
│  任务名称            │                     │
│  ┌────────────────┐  │       📭            │
│  │                │  │   暂无执行记录       │
│  └────────────────┘  │                     │
│                      │                     │
│  任务类型            │                     │
│  ┌────────────────┐  │                     │
│  │ ERP查询     ▾  │  │                     │
│  └────────────────┘  │                     │
│                      │                     │
│  执行频率            │                     │
│  ┌────────────────┐  │                     │
│  │ 每天        ▾  │  │                     │
│  └────────────────┘  │                     │
│                      │                     │
│  执行时间            │                     │
│  ┌────────────────┐  │                     │
│  │ 09:00          │  │                     │
│  └────────────────┘  │                     │
│                      │                     │
│  查询内容            │                     │
│  ┌────────────────┐  │                     │
│  │                │  │                     │
│  └────────────────┘  │                     │
│                      │                     │
│  安静时段（选填）     │                     │
│  ┌──────┐至┌──────┐  │                     │
│  │22:00 │  │08:00 │  │                     │
│  └──────┘  └──────┘  │                     │
│──────────────────────│                     │
│    [取消]    [创建]  │                     │
└──────────────────────┴─────────────────────┘
        240px                 340px
```

**编辑弹窗**：
```
┌──────────────────────┬─────────────────────┐
│  每日待发货汇总    ✕  │  执行记录            │
│──────────────────────│  共15次·成功14·失败1  │
│                      │─────────────────────│
│  任务名称            │                     │
│  ┌────────────────┐  │  03-21 09:00  ✅    │
│  │每日待发货汇总   │  │  共12单待发货        │
│  └────────────────┘  │                     │
│                      │  03-20 09:00  ✅    │
│  任务类型            │  共8单待发货         │
│  ┌────────────────┐  │                     │
│  │ ERP查询     ▾  │  │  03-19 09:00  ❌    │
│  └────────────────┘  │  ERP接口超时         │
│                      │                     │
│  执行频率            │  03-18 09:00  ✅    │
│  ┌────────────────┐  │  共15单待发货        │
│  │ 每天        ▾  │  │                     │
│  └────────────────┘  │  03-17 09:00  ✅    │
│                      │  共10单待发货        │
│  执行时间            │                     │
│  ┌────────────────┐  │                     │
│  │ 09:00          │  │                     │
│  └────────────────┘  │                     │
│                      │                     │
│  查询内容            │                     │
│  ┌────────────────┐  │                     │
│  │待发货订单汇总   │  │                     │
│  └────────────────┘  │                     │
│                      │                     │
│  安静时段（选填）     │                     │
│  ┌──────┐至┌──────┐  │  [加载更多...]       │
│  │22:00 │  │08:00 │  │                     │
│  └──────┘  └──────┘  │                     │
│──────────────────────│                     │
│[🗑删除] [⏸暂停] [💾] │                     │
└──────────────────────┴─────────────────────┘
        240px                 340px
```

左右各自独立滚动。

#### 13.5 推送目标

- 任务执行结果**只推送到企微**（WS 长连接）
- Web 端通过弹窗右侧「执行记录」回看历史结果

#### 13.6 交互流程

| 操作 | 触发 | 效果 |
|-----|------|------|
| 展开右侧栏 | 点击 ChatHeader 📋 图标 | 侧边栏 slide in |
| 收起右侧栏 | 点击 ✕ 或再点 📋 | 侧边栏 slide out |
| 新建任务 | 点击 [+ 新建任务] | 弹出弹窗（空表单 + 空历史） |
| 查看/编辑任务 | 点击 TaskCard | 弹出弹窗（预填数据 + 历史列表） |
| 暂停/恢复 | 弹窗底部 [⏸]/[▶️] | 状态变更，侧边栏卡片同步 |
| 删除 | 弹窗底部 [🗑] → 二次确认 | 关闭弹窗，卡片消失 |
| AI 创建 | 对话说"每天9点查待发货" | AI 回复确认，侧边栏新增卡片 |

#### 13.7 响应式

| 屏幕宽度 | 左侧栏 | 聊天区 | 右侧栏 |
|---------|--------|-------|--------|
| ≥1200px | 256px | flex-1 | 280px |
| 900-1200px | 收起 | flex-1 | 280px |
| <900px | 收起 | flex-1 | 收起 |

弹窗所有尺寸居中，<600px 时宽度改为 95vw。

#### 13.8 动画

| 交互 | 动画 | 时长 |
|-----|------|------|
| 右侧栏展开/收起 | width 0→280px + fadeIn | 200ms ease |
| 任务卡片新增 | slideDown + fadeIn | 150ms |
| 任务卡片删除 | fadeOut + slideUp | 150ms |
| 弹窗 | 复用现有 modalEnter/modalExit | 200ms |
| 状态点切换 | 颜色 transition | 300ms |

---

### 14. 前端组件清单（含 UI）

| 组件 | 文件 | 职责 |
|-----|------|------|
| TaskSidebar | `components/chat/TaskSidebar.tsx` | 右侧栏容器：标题 + 筛选 tab + 任务列表 + 新建按钮 |
| TaskCard | `components/chat/TaskCard.tsx` | 单张任务卡片：状态点 + 名称 + 调度 + 下次执行 |
| TaskDetailModal | `components/chat/TaskDetailModal.tsx` | 左右分栏弹窗：左设置表单 + 右执行记录 |

---

### 15. 设计自检

- [x] 连锁修改已全部纳入任务拆分（intent_router / smart_model_config / main.py / Chat.tsx / store）
- [x] 7 类边界场景均有处理策略
- [x] 所有新增文件预估 ≤ 500 行
- [x] 无模糊版本号依赖（APScheduler==3.10.4）
- [x] API 风格与现有 wecom push 一致（success/data/error）
- [x] 前端组件遵循现有 TailwindCSS + Props-driven 模式
- [x] UI 设计已确认（常驻右侧栏 + 左右分栏统一弹窗）
- [x] 推送目标已确认（只推企微，Web 端弹窗回看历史）
