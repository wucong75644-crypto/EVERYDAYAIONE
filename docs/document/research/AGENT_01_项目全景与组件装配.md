# AGENT 01：项目全景、组件装配与启动边界

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：启动入口、运行模式、进程/线程边界、核心组件装配、启动关闭参数
> 尚未展开：SessionActor 内部命令和状态机，见后续 Session 专项

## 1. 本板块回答的问题

1. 系统从哪个入口启动。
2. 支持哪些运行模式。
3. Session/任务在哪个进程或线程执行。
4. 核心依赖在哪里组装。
5. 启动和关闭顺序是什么。
6. 进程退出、连接断开和重启时如何处理。
7. 哪些配置参数直接影响运行时容量和可靠性。

## 2. Grok Build 项目全景

### 2.1 仓库职责

| 路径 | 职责 |
|---|---|
| `crates/codegen/xai-grok-pager-bin` | Composition Root，构建最终二进制并选择运行模式 |
| `crates/codegen/xai-grok-pager` | TUI、输入、滚动区、模态框和渲染 |
| `crates/codegen/xai-grok-shell` | Agent Runtime、Session、leader、stdio、headless、server |
| `crates/codegen/xai-grok-agent` | Agent 定义、Prompt Context 和 AgentBuilder |
| `crates/codegen/xai-grok-tools` | ToolBridge、工具注册表和具体工具 |
| `crates/codegen/xai-grok-workspace` | 文件系统、VCS、执行和 Checkpoint |

Grok Build 使用单一 Rust 二进制承载多种模式，模式共享 `xai-grok-shell` Agent Runtime，而不是为每个客户端复制 Agent Loop。

### 2.2 主入口初始化顺序

源码：`crates/codegen/xai-grok-pager-bin/src/main.rs::main`

已核验的顺序：

1. 安装最小运行环境。
2. 可选安装 jemalloc 释放、统计和堆分析钩子。
3. 检查是否以 Mermaid 渲染子进程方式运行；命中则直接退出主流程。
4. 启动内存 Trace，目录为 Grok Home 下的 `memtrace`。
5. 提升 macOS 文件描述符软限制，避免并行目录、MCP stdio、工具子进程和 Socket 耗尽默认额度。
6. 校验受管策略要求；失败以退出码 `2` fail-closed。
7. 初始化 Sentry，版本使用包含 commit 的构建版本。
8. 将用户指南提取到 Grok Home。
9. 安装终端恢复和可选 Crash Handler。
10. 扫描上次崩溃遗留的 Active Sessions。
11. 构建启用全部 Tokio 能力的多线程 Runtime。
12. 执行 `async_main()`。
13. 使用 `RUNTIME_SHUTDOWN_GRACE` 有界关闭 Runtime。
14. Flush 调试日志；错误时恢复 stderr 并以退出码 `1` 退出。

这说明 Grok 的 Composition Root 同时承担运行环境校验、Crash 恢复发现、遥测和模式选择，但 Agent 业务运行下沉到 `xai-grok-shell`。

### 2.3 运行模式

源码：

- `xai-grok-pager-bin/src/main.rs`
- `xai-grok-shell/src/agent/app.rs`
- `xai-grok-shell/src/agent/server.rs`

| 模式 | 入口 | 行为 |
|---|---|---|
| TUI | Pager 默认入口 | 终端交互客户端 |
| stdio | `run_stdio_agent` | ACP JSON-RPC 标准输入输出服务 |
| headless | `run_headless` | 脚本、CI 和无 UI 执行 |
| serve | `run_agent_server` | WebSocket Agent Server |
| leader | `run_leader` | 多客户端共享的常驻 Runtime |

当没有明确 `AgentCmd` 时，Agent 子命令回退到 headless。Serve 模式构造：

```text
ServerConfig {
    bind_addr: CLI 参数 a.bind,
    secret: a.get_secret()
}
```

Leader 模式关键参数：

- `no_exit_on_disconnect`：客户端断开后是否保持 leader。
- `relay_on_demand`：是否按需开启 relay。
- `leader_auto_update`：可选自动更新配置。
- 自动更新检查周期：`60 * 60` 秒。

`apply_headless_args_to_config` 只覆盖 CLI 显式传入的参数，使环境或配置文件默认值继续生效。此模式避免“未传 CLI 参数却覆盖已有配置”的装配错误。

### 2.4 Leader 连接恢复

`xai-grok-pager-bin/src/main.rs` 会缓存从 stdio 客户端观察到的 ACP 状态：

- initialize。
- 已确认的 `session/new`。
- `session/load` 完整请求。
- MCP Server 配置。
- 多 Session 状态。

Leader 断开后重新连接，并按顺序重放初始化和 Session Load。代码明确等待 `session/load` 完成后才宣布 leader reconnected，避免新 leader 尚未恢复 Session 时客户端收到“已连接”并触发 unknown session id。

这是客户端桥接层的恢复缓存，不等同于 Session 持久化；Session 的权威更新日志将在持久化专项中分析。

### 2.5 Session 执行边界

源码：`xai-grok-shell/src/session/acp_session_impl/spawn.rs`

- `spawn_session_actor()` 构造 SessionActor。
- `spawn_session_on_thread()` 为 Session 建立专用 OS Thread。
- SessionActor 包含非 `Send` 状态，在 Session Thread 内构造且不跨线程移动。
- 子 Agent 也通过 Session Spawn 路径建立独立 Session。
- Session 内部异步任务共享该 Session 的运行上下文。

这一设计的核心不是“每个请求启动线程”，而是以 Session 为隔离和所有权边界，避免可变会话状态在多个线程间随意移动。

## 3. EVERYDAYAIONE 项目全景

### 3.1 当前主要运行入口

| 入口 | 职责 |
|---|---|
| `backend/main.py` | FastAPI API、WebSocket Redis Listener、通用后台服务 |
| `backend/conversation_worker_main.py` | Conversation Actor 独立 Worker |
| `backend/services/background_task_worker.py` | 图片/视频等异步任务轮询兜底 |
| 企微相关 Worker 入口 | WebSocket 入站、Outbox 投递和渠道处理 |
| React 应用入口 | Web 客户端、聊天状态和结构化内容渲染 |

与 Grok 的单一二进制多模式不同，本项目采用多进程服务分工。对 SaaS 而言该方向合理，但目前各进程的 Composition Root 分散，尚无统一 Agent Runtime 装配清单。

### 3.2 FastAPI 主进程

源码：`backend/main.py::lifespan`

启动职责已核验包括：

- Redis 初始化，失败时限流相关能力降级。
- WebSocket Redis Pub/Sub Listener。
- Mem0 连接预热。
- 知识库连接预热和 Redis 锁保护的种子导入。
- OrgScopedDB Schema 反射。
- 孤儿任务恢复。
- 中断锚点调和。
- 过期 `pending_interaction` 清理。
- `BackgroundTaskWorker`。
- 全局错误监控等其他后台生命周期。

`backend/main.py` 当前 589 行，已超过项目 500 行阈值。它同时承担 Composition Root、启动自愈和部分业务初始化，属于后续架构设计必须评估的既有结构风险；本阶段不修改。

### 3.3 Conversation Actor Worker 入口

源码：`backend/conversation_worker_main.py`

启动顺序：

1. 读取 Settings。
2. `conversation_actor_worker_enabled=false` 时 fail-closed，拒绝启动。
3. 初始化异步数据库。
4. 构造 `ConversationActorRuntime`。
5. 建立 SIGTERM/SIGINT Shutdown Event。
6. `runtime.start()`。
7. 等待 Shutdown Event。
8. `runtime.stop()`。
9. 关闭数据库和 Redis。

该入口已经具备清晰的独立进程边界和有序关闭机制。

### 3.4 ConversationActorRuntime 装配

源码：`backend/services/conversation_runtime.py`

构造链：

```text
ChatGenerationExecutor
→ ConversationExecutionService
→ ActorTerminalDelivery
→ ConversationWorker
→ RedisConversationWakeup
```

固定参数：

| 参数 | 当前值 | 作用 |
|---|---:|---|
| `renew_interval_seconds` | 5 秒 | Conversation Execution 租约续期周期 |
| Kernel nsjail config | `deploy/sandbox.cfg` 存在时启用 | Sandbox 隔离配置 |

启动顺序：

```text
KernelManager.start
→ 设置全局 KernelManager
→ 创建 conversation_actor_worker asyncio Task
```

关闭顺序：

```text
ConversationWorker.stop
→ 取消并回收 Worker Task
→ KernelManager.shutdown
→ 清空全局 KernelManager
```

Runtime 已经是本项目最接近 Grok Composition Root + Session Runtime 的模块。

### 3.5 ConversationWorker 容量参数

源码：`backend/services/conversation_worker.py::ConversationWorker.__init__`

| 参数 | 默认值 | 校验 |
|---|---:|---|
| `scan_interval_seconds` | 2 秒 | 必须大于 0 |
| `concurrency` | 5 | 必须至少为 1 |
| `scan_batch_size` | 100 | 必须至少为 1 |
| `shutdown_timeout_seconds` | 10 秒 | 必须大于 0 |

Worker 行为：

- PostgreSQL `tasks` 是队列和执行状态事实源。
- Redis 只负责 best-effort 唤醒，不保存队列、上下文或执行权。
- Redis Listener 失败后从 1 秒指数退避到最多 30 秒。
- 数据库扫描失败返回空候选，不使 Worker 崩溃。
- `serial` 模式按 `conversation_id` 排他。
- `branch` 模式按 `task_id` 排他。
- `_active_keys` 防止当前进程重复调度。
- 数据库 Claim 和 execution token 负责跨进程所有权。
- 每个 Claim 完成后再次唤醒同一 Conversation，推动队列继续。
- 关闭时在 10 秒内等待执行任务，超时后的后续行为需在 Session 专项继续核验。

### 3.6 本项目 Session 隔离方式

本项目没有为每个 Conversation 建立永久 OS Thread，而是：

```text
PostgreSQL Tasks
→ Worker 有界扫描/唤醒
→ 按 conversation 或 branch 建立排他调度键
→ Claim 获取数据库执行权
→ asyncio Task 执行
→ Lease + fencing 防止旧执行者写入
→ 原子终态
```

这是面向多用户 SaaS 和多进程部署的持久 Actor，而 Grok 是面向本地客户端的 Session Thread。二者目的相同，运行载体不同。

## 4. 第一轮差距矩阵

| 能力 | Grok Build | EVERYDAYAIONE | 初步判断 |
|---|---|---|---|
| Composition Root | 单二进制集中选择运行模式 | API、Actor、媒体、企微入口分散 | 需要统一装配文档和运行时边界，不应强合成单进程 |
| 多客户端模式 | TUI/headless/stdio/server/leader 共用 Runtime | Web、企微、定时任务存在不同入口 | 需要收口到共享 Agent Runtime 内核 |
| Session 所有权 | 每 Session 专用 Thread | DB Claim + asyncio Task + fencing | 保留本项目方案，更适合 SaaS |
| 状态事实源 | 本地 Session 文件/更新日志 | PostgreSQL | 保留本项目方案 |
| 快速唤醒 | Leader/进程内通道 | Redis Pub/Sub + DB 扫描兜底 | 本项目更适合多进程 |
| 崩溃发现 | Active Session 扫描和客户端重放 | DB running/pending 扫描、租约和恢复 | 后续逐项比较恢复完整性 |
| MCP 生命周期 | 运行模式装配已包含 | 尚未形成统一 Runtime 接口 | ToolBridge 稳定后引入 |
| 启动策略校验 | 受管策略 fail-closed | Actor 开关 fail-closed；其他初始化多为降级 | 需要按能力风险分级 |
| 资源限制 | FD、Runtime、Session Thread | Worker 并发、批量、租约、Sandbox | 融合形成统一资源预算 |

## 5. 当前推荐方向

本板块暂定结论为“融合升级”：

1. 保留 API、Conversation Actor、媒体 Worker 和渠道 Worker 的多进程部署，不复制 Grok 的单机单二进制形态。
2. 保留 PostgreSQL Claim、租约、fencing 和原子终态，它们比本地 Session Thread 更适合本项目。
3. 学习 Grok 的 Composition Root，将 Agent、ToolBridge、Policy、Skills、MCP、Goal 和事件依赖集中装配，避免不同入口自行拼装能力。
4. Web、企微、定时任务未来应调用同一 Agent Runtime 内核，只保留输入和输出 Adapter 差异。
5. 启动时区分“必须 fail-closed 的安全能力”和“允许降级的增强能力”，不能继续依赖分散的 try/except 自行决定。

以上是调研结论，不是已确认实施方案。是否新增统一 Composition Root、放置目录及迁移步骤，必须等待全部板块完成后统一设计。

## 6. 边界场景与后续核验

| 场景 | 当前事实 | 后续专项 |
|---|---|---|
| Redis 不可用 | DB 扫描继续工作，实时唤醒降级 | Session / Observability |
| DB 扫描失败 | 当前轮返回空候选，Worker 继续 | Session / Backoff |
| 同一会话连续任务 | serial key + Claim 顺序执行 | Session |
| branch 并行 | task key 隔离 | Session |
| Worker 收到 SIGTERM | 有序 stop，默认最多等待 10 秒 | Session / Persistence |
| 执行中进程强杀 | 依赖租约和数据库恢复 | Session / Persistence |
| Kernel 启动失败 | Runtime 启动失败 | Tools / Sandbox |
| Leader 断连 | Grok 重连并重放 ACP 状态 | Protocol / Persistence |
| MCP 子进程断开 | Grok 有自动重启相关装配 | MCP 专项 |
| 多客户端共享 Session | Grok leader 支持；本项目通过 DB 和渠道绑定共享 | Session / Protocol |

## 7. 已核验文件与规模

### Grok Build

- `README.md`
- `xai-grok-pager-bin/src/main.rs`
- `xai-grok-shell/src/agent/app.rs`
- `xai-grok-shell/src/agent/server.rs`
- `xai-grok-shell/src/session/acp_session_impl/spawn.rs`

### EVERYDAYAIONE

| 文件 | 行数 |
|---|---:|
| `backend/main.py` | 589 |
| `backend/conversation_worker_main.py` | 54 |
| `backend/services/conversation_runtime.py` | 115 |
| `backend/services/conversation_worker.py` | 325 |
| `backend/services/conversation_execution.py` | 351 |
| `backend/services/handlers/chat/execution_engine.py` | 362 |
| `backend/services/agent/tool_loop_executor.py` | 756 |
| `backend/services/agent/tool_executor.py` | 500 |

## 8. 本板块验收标准

- [x] 已定位双方 Composition Root。
- [x] 已列出主要运行模式和进程/线程边界。
- [x] 已记录本项目 Worker 关键默认参数。
- [x] 已记录启动和关闭顺序。
- [x] 已形成第一轮差距与适用性判断。
- [ ] Session 命令、状态机、租约、取消和恢复逐函数核验。
- [ ] Grok Session Thread 的 Channel、Mailbox 和持久化时序逐函数核验。
- [ ] 双方故障注入和恢复测试逐项对照。

后三项属于下一份 Session Actor 专项，不在本板块伪造完成状态。
