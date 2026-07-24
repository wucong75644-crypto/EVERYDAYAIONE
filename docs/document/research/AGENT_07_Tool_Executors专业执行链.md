# AGENT 07：Tool Executors 与专业执行链

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：即时查询、Web、文件、沙盒、ERP、媒体及外部副作用的真实执行语义
> 后续专项：Goal、Context、Skills、MCP、Persistence、UI Event 和端到端链路继续核验

## 1. 结论摘要

Executor 不应按“是不是 Tool”使用同一策略，而应按执行语义分类：

| 执行语义 | 示例 | 主要约束 |
|---|---|---|
| 即时只读 | 天气、搜索、本地查询 | timeout、来源、缓存、数据范围 |
| 会话内计算 | Bash、Python、文件分析 | 沙盒、资源预算、流式输出、取消 |
| 资源变更 | 写文件、删除、ERP 写入 | 权限、资源锁、幂等、审计 |
| 异步生成 | 图片、视频、部署 | 持久任务、计费、回调、轮询、恢复 |
| 后台工作 | 子 Agent、调度任务 | 生命周期、所有权、等待和唤醒 |

Grok Build 的工具实现高度类型化，参数、Capability、Progress 和 ToolOutput 清晰，
适合单用户本地 Agent；但图片仍是一次最长 300 秒的 HTTP 调用，视频在 ToolCall
内轮询最多 300 秒，进程退出后的恢复和 SaaS 积分事务不是其目标。

EVERYDAYAIONE 的媒体 Handler 已具备数据库任务、回调优先、轮询兜底、分布式完成
锁、OSS/Workspace 持久化、批次、重试和积分结算，整体强于 Grok 的 SaaS 场景。
但聊天 ToolExecutor 又提供同步等待版 `generate_image/video`，同一种能力存在两套
任务、计费、超时、持久化和取消语义。

推荐“保留专业 Executor，统一 Action 生命周期”：

```text
ToolBridge
→ Executor.submit(ActionContext)
→ immediate ToolRunResult
或
→ persisted ExecutionTask
→ progress / waiting
→ completion reconciler
→ ToolRunResult + Artifacts
```

Agent 不应为了等待媒体而占住一次模型工具调用；异步 Executor 应返回可持久化
TaskRef，由 Session/Goal Orchestrator 决定等待、继续其他步骤或后台恢复。

## 2. Grok Build 执行器事实

### 2.1 Bash 与后台任务

源码：`xai-grok-tools/src/implementations/grok_build/bash/mod.rs`

`BashParams` 关键默认：

- 前台默认 timeout 120 秒。
- 默认前台最大 timeout 5 分钟，生产 Host 可提高。
- 默认输出约 20 KB。
- background 默认启用。
- auto-background 默认关闭；启用时默认前台阻塞预算 15 秒。
- Progress 单帧最大 16 KB。

前台命令可流式产生 `bash_output_chunk`；后台命令返回 task ID，再通过
`get_task_output` 查询或等待。单次等待上限默认 600 秒，可由
`GROK_MAX_WAIT_BLOCK_MS` 覆盖。Terminal 独立控制面支持取消，不要求等待 Registry
锁释放。

可采用的不是 Bash 本身，而是通用模式：

```text
短任务原地完成
长任务 background
→ TaskRef
→ completion ping
→ snapshot / bounded wait / kill
```

### 2.2 文件读取与修改

源码：

- `grok_build/read_file/mod.rs`
- `implementations/read_file/*`
- `implementations/editor_infra/file_operation_lock.rs`

关键限制：

- 默认/最大窗口 1000 行。
- 非 Skill 文本最多约 25,000 token。
- PDF 大于 10 页需显式页码，每次最多 20 页。
- PPTX 最大 50 MB，提取 timeout 60 秒。
- 图片经过像素和 payload 上限治理。
- Read Tool 标记为 read-only，写工具使用独立文件操作锁。
- `behavior_version` 可恢复旧版 read_file 语义。

文件工具不是字符串路径透传：路径解析、gitignore、权限、格式、流式读取和版本均
属于 Executor contract。

### 2.3 Web Search 与 Web Fetch

源码：

- `grok_build/web_search/mod.rs`
- `grok_build/web_fetch/*`

Search 输入为 `query + allowed_domains`，输出保留 query、content、citations 和
domain 限制。Fetch 的关键默认：

- URL 最长 2000 字符。
- 最多 10 次重定向。
- timeout 60 秒，connect timeout 10 秒。
- 最大响应 10 MB。
- inline Markdown 最大 100 KB。
- 默认 Context Window 128K，并限制 Web 内容占比。
- Cache TTL 15 分钟、最多 128 项。
- 默认 domain allowlist。

Fetch 禁止自动重定向，手工验证同 Host 重定向；DNS 解析后阻止 private、
link-local 和云 metadata 地址，避免 SSRF。认证和私有网站明确引导使用专业 MCP。

### 2.4 图片

源码：`grok_build/image_gen/mod.rs`

参数：

- `prompt`
- `aspect_ratio`，默认 `auto`
- 固定 `n=1`、`resolution=1k`、`response_format=b64_json`
- 默认模型 `grok-imagine-image-quality`

HTTP 总 timeout 300 秒、read timeout 240 秒。成功后写入
`<session>/images/<n>.jpg`，返回结构化 `MediaGenOutput`。工具描述明确要求多图时
生成多个、Prompt 不同的 ToolCall。

不足是没有持久 Provider Task、跨进程恢复、成本预授权或重放幂等键。

### 2.5 视频

源码：`grok_build/video_gen/mod.rs`

关键参数：

- 模型：基础版或 `grok-imagine-video-1.5-preview`
- 默认 resolution `480p`，允许 `480p/720p`
- duration 允许 6 或 10 秒，默认 6 秒
- aspect ratio：`1:1/16:9/9:16/3:2/2:3`
- reference images 最多 7 张

时序：

```text
POST start，timeout 60s
→ request_id
→ 每 5s GET
→ 单次 poll timeout 30s
→ 总生成 deadline 300s
→ download timeout 120s
→ <session>/videos/<n>.mp4
```

可选 ZDR S3 输出使用预签名上传/读取 URL，默认有效期 900 秒，且必须覆盖生成、
下载和 60 秒余量。状态处理 `done/failed/expired/other`，但整个过程仍绑定当前
ToolCall 和进程。

### 2.6 Grok 的适用边界

Grok 的 Executor 优势是：

- 类型化参数与输出。
- Capability 明确 read/write。
- 每类工具都有具体预算。
- Progress 和唯一 Terminal。
- 本地 Session 文件统一。

不应直接复制的是：

- 长媒体在 ToolCall 内阻塞轮询。
- 本地 Session 文件代替 SaaS Artifact/OSS。
- 没有本项目需要的积分事务。
- 进程生命周期即会话任务生命周期。

## 3. EVERYDAYAIONE 即时与 Web 执行器

### 3.1 Web Search

源码：`backend/services/agent/web_search_engine.py`

当前链路：

```text
Gemini 3 Flash + Google Grounding
→ 失败降级 DashScope enable_search
→ AgentResult
```

参数：

- Gemini read timeout 30 秒，connect/pool 5 秒，write 10 秒。
- DashScope fallback read timeout 10 秒。
- fallback `temperature=0.3`、`max_tokens=2000`。

优点是有明确降级和来源解析。差距：

- Tool Schema 没有 `allowed_domains`。
- 只有搜索，没有受控通用 Fetch。
- 来源由 Provider 非稳定字段 `reasoning_content` 解析。
- Search 失败最终返回空值，由上层再转 `empty`，缺少标准错误分类。

目标应区分 Search 与 Fetch；Fetch 必须具备 SSRF、DNS rebinding、redirect、
内容长度、MIME 和 domain Policy，不能复用普通 HTTP 客户端直接开放。

### 3.2 ERP 本地和远程查询

源码：`backend/services/agent/erp_tool_executor.py`

本地查询直接读数据库；远程查询采用两步：

```text
只有 action → 返回本地参数文档
action + params → 注入分页 → Dispatcher → KuaiMai API
```

企业凭据由 `OrgConfigResolver` 读取，refresh token 同时写 Redis 热缓存和数据库。
这比把 ERP 当通用 HTTP Tool 更安全，应保留为专业 Executor。

需要在后续端到端核验：

- 每个远程 API 的 timeout/retry/rate limit 是否一致。
- token refresh 并发。
- 分页上限和全量导出。
- Provider 成功但格式化/落盘失败的恢复。

### 3.3 ERP 写入

`erp_execute` 使用参数内容 MD5 形成 Redis 幂等键：

- 执行锁 TTL 120 秒。
- 成功标记 TTL 600 秒。
- Redis 不可用时 fail closed。

优点是已阻止并发重复写。风险：

- 幂等身份由 user + 参数哈希决定，不是持久 Action ID。
- 成功标记写 Redis 失败会放行未来重复执行。
- 10 分钟后同一请求可重放。
- Redis 不是业务结果事实源，无法证明 Provider 已执行但响应丢失。
- 锁 TTL 小于未知远程执行时长时可能失效。

目标必须将 ERP 写入使用持久 `action_id + provider_request_id + result`，并由
数据库保存 unknown/reconciling 状态；参数哈希只用于重复意图检测。

## 4. 文件与沙盒执行器

### 4.1 文件范围

源码：`backend/services/agent/file_tool_mixin.py`

`FileExecutor` 以 workspace root、workspace user 和 org 构造；文件搜索默认优先
当前任务冻结的 `ResourceManifest`，显式 `scope=workspace` 才扫描工作区。路径经过
`resolve_safe_path`，权限异常形成不可重试 AgentResult。

现有专业分工合理：

- `file_search`：定位和多模态描述。
- `file_analyze`：Excel/CSV 治理为 Parquet。
- `file_delete`：删除并记录恢复信息。
- `restore_file`：恢复。
- `code_execute`：在 staging/workspace 上计算。

目标 ToolDescriptor 需要给这些工具声明 resource key；同一路径写操作串行，不同
文件允许并行。文件身份应以 Workspace asset ID/path revision 为主，不能只靠
模型看到的文件名缓存。

### 4.2 Python 沙盒

源码：

- `agent/sandbox_tool_mixin.py`
- `sandbox/executor.py`
- `sandbox/kernel_manager.py`
- `sandbox/emit_protocol.py`

关键参数：

- 默认执行 timeout 120 秒，并取 `min(sandbox_timeout, budget.remaining)`，最低 5 秒。
- Kernel 最大 4 个。
- 空闲 1200 秒回收，最大生命周期 1800 秒。
- 启动 ready timeout 10 秒。
- response 等待为执行 timeout + 10 秒。
- Table Preview 最多 200 行。
- Diagram source 最多 100,000 字符。

Kernel 按 conversation 复用；超过 Kernel 上限当前返回资源紧张，不走不安全
subprocess 降级。生产使用 nsjail，开发环境可使用裸 Python + symlink，安全语义
不等价。

产物有三条统一入口：

1. IPython MIME hook。
2. output 目录 diff 自动发现。
3. `emit_chart/diagram/file/image/table` 显式声明。

产物通过独立 IPC `emit_payloads` 返回，不计入 stdout 截断预算；这是优于仅返回
本地路径的设计，应保留并类型化。

风险：

- 取消是否终止完整进程树仍需端到端验证。
- Conversation Kernel 状态只在进程内，Actor 换 Worker 后无法恢复变量。
- 开发环境裸 Python 不能作为生产安全证明。
- Fire-and-forget 指标/知识写入没有可靠交付保证。

## 5. EVERYDAYAIONE 媒体双链路

### 5.1 Chat Tool 同步链路

源码：`backend/services/media_tool_executor.py`

图片：

```text
prompt 校验
→ 选择文生图/图生图模型
→ 计算并锁积分
→ 随机 task_id
→ wait_for_result=True
→ 最多 90s，每 2s poll
→ 成功 confirm
→ Workspace/OSS
→ AgentResult.emit_payloads
失败 refund
```

视频固定 10 秒，最多等待 300 秒、每 5 秒 poll。成功仅返回 URL，没有和图片同等
的 Workspace Artifact 持久化。

该链路的问题不是不能工作，而是它绕过了异步媒体主链路：

- 不写标准 tasks 状态机。
- 无 Webhook 与进程重启恢复。
- 每次重试生成新的随机 task_id。
- ToolCall timeout 与 Provider unknown outcome 容易被当失败退款。
- 图片和视频 Artifact 行为不一致。

### 5.2 异步 Handler 主链路

源码：

- `handlers/image_handler.py`
- `handlers/video_handler.py`
- `task_completion_service.py`
- `background_task_worker.py`

图片支持 1～4 张统一批次、每张独立 Prompt、每张独立积分事务和任务；提交间隔
300 ms。视频支持文生/图生视频。两者共同流程：

```text
preflight / balance
→ lock credits
→ Provider submit(wait=false)
→ external_task_id
→ tasks 落库
→ Webhook 优先 / Poll fallback
→ completion lock
→ OSS/Workspace
→ Handler terminal
→ confirm/refund
→ WS + message
```

轮询参数：

- 配置 Callback 时默认 120 秒兜底。
- 无 Callback 时默认 15 秒主轮询。
- 任务在 60 秒窗口内抖动，Provider 并发由 `kie_qps_limit` Semaphore 控制。
- 图片任务超时 10 分钟，视频 30 分钟。

完成处理：

- Redis 完成锁 TTL 300 秒，每 60 秒续期。
- 数据库 version 做第二层乐观锁。
- Webhook 与 Poll 共用 `TaskCompletionService`。
- 图片写 Workspace + OSS；视频当前主要写 OSS。
- Smart mode 支持提交失败同步换模型和终态失败异步重试。

### 5.3 已有高风险

1. 积分锁定使用临时随机 task ID，之后 Provider external ID 和 client task ID 分离，
   缺少贯穿全链路的稳定 Action ID。
2. Provider submit 成功、`tasks` 落库失败时会退款，但 Provider 任务仍可能完成，
   形成未归属的付费产物。
3. Completion 抢占只递增 version，没有独立 processing lease/fencing；处理失败后
   version 已改变但状态仍可重试，语义需要专项验证。
4. Redis 完成锁续期失败只记录并退出续期，不取消当前处理，可能与另一 Worker 重叠。
5. 图片与视频 Workspace 持久化不一致。
6. `background_task_worker.py` 仍有三处 `🔥🔥🔥` 生产 `print` 和过期 DEBUG 注释，
   属于既有代码异味，本轮只记录，不修改。
7. Sync/async retry 均可能选择新模型并建立新积分事务，但缺少统一 Attempt lineage。

## 6. 目标 Executor 协议

### 6.1 四种返回

专业 Executor 统一返回以下之一：

- `Completed(output, artifacts, settlement)`
- `Accepted(task_ref, initial_progress)`
- `Rejected(error, retryable)`
- `Unknown(task_ref, reconciliation_required)`

`Unknown` 必须是一等状态：网络 timeout 不代表外部动作未发生。

### 6.2 稳定身份

一次用户授权拆分出的每个 Action 需要：

- `run_id`
- `action_id`
- `attempt_id`
- `tool_call_id`
- `provider_request_id`
- `authorization_grant_id`
- `credit_reservation_id`

模型重试使用新 attempt，但沿用 action；Provider 支持幂等键时传 action ID。所有
Artifact、结算和 UI Event 均关联 action，而不是随机临时 task ID。

### 6.3 通用生命周期与专业状态

统一外层：

```text
proposed → authorized → submitted → running/waiting
→ succeeded | failed | cancelled | unknown
→ settled
```

专业状态继续保留，例如 Provider queued/generating、Sandbox background、ERP
reconciling。不能为了统一而丢弃专业状态。

### 6.4 调度原则

- 即时只读工具可按资源和 Provider 限流并行。
- 有依赖的调用由 Plan DAG 决定先后，不由模型一次乱序并行。
- 文件/ERP 写入按 resource key 串行。
- 多图生成在 Grant 数量和成本内并行，Provider 限流独立控制。
- 超过一秒且可恢复的外部任务优先 `Accepted`，Session 不阻塞 Worker。
- 取消只停止尚未发生的动作；已发生或 unknown 的副作用进入 reconcile。

## 7. 差距与决策

| 模块 | 结论 |
|---|---|
| Bash/background 模式 | 采用 Grok 的 TaskRef、bounded wait、completion ping |
| Web Fetch 安全 | 采用 Grok 的 allowlist、SSRF、redirect、size、cache 边界 |
| 文件/数据 Workspace | 保留现有 ResourceManifest、Parquet 和租户范围 |
| Sandbox Artifact | 保留现有 emit 多通道并类型化 |
| ERP 专业 Dispatcher | 保留，升级持久幂等与 unknown reconcile |
| 图片/视频主链路 | 保留异步 Handler，移除同步语义分叉 |
| Grok 媒体阻塞轮询 | 不采用 |
| Progress/Terminal | 融合成通用 Executor Event |
| 重试 | 统一 Action/Attempt lineage |
| 成本结算 | 保留现有并纳入 Action Settlement |

## 8. 验收场景

1. 用户明确要求按三段 Prompt 生成三张图：一个 Grant、三个 Action、独立 Attempt，
   在数量/成本和 Provider 并发内执行，结果顺序稳定。
2. Worker 在 Provider submit 后退出：重启从数据库恢复，不再次提交或重复扣费。
3. Provider submit timeout：Action 进入 unknown/reconcile，不立即退款并重复提交。
4. Webhook 与 Poll 同时到达：只有一个有 fencing 的 completion owner 产生终态。
5. 图片成功但 OSS 暂时失败：保留 Provider 结果并重试持久化，不重新生成图片。
6. 视频生成期间用户继续聊天：Session 可继续规划，视频完成后事件唤醒。
7. 用户取消视频但 Provider 不支持取消：展示 cancelling/reconciling，迟到结果正确结算。
8. ERP 写入响应丢失：通过 provider request/业务单号核验，不凭 Redis TTL 重放。
9. Web Fetch 访问 `127.0.0.1`、私网 DNS 或跨 Host redirect：执行前拒绝。
10. 两个工具同时写同一文件：按资源串行；读不同文件仍可并行。
11. Sandbox timeout：终止进程树，保留已成功 emit 的 Artifact 并标记 partial/timeout。
12. Actor Worker 切换后继续代码任务：明确变量状态不可恢复并要求重跑，不伪装恢复。

## 9. 对总体重构的输入

1. 所有专业 Executor 通过统一 submit/cancel/status/reconcile 协议接入 ToolBridge。
2. 媒体 Tool 调用收口到异步任务主链路，Chat 不再同步等待 Provider。
3. 引入稳定 Action/Attempt/Provider identity 和 unknown 状态。
4. Task Completion 升级为带 lease/fencing 的持久 Reconciler。
5. 保留媒体批次、积分、OSS/Workspace、ERP Dispatcher、Sandbox emit 和文件治理。
6. 增加安全 Web Fetch 专业 Executor，不直接暴露任意 HTTP。
7. 工具并发由 descriptor、resource key、Provider quota 和 Plan 依赖共同决定。
8. 后续 Persistence 板块确定 Action/Attempt/Artifact/Settlement 的表与原子边界。

下一板块进入 Goal Orchestrator，核验 Planner、Completion Verifier、Stall Strategist
和 Continuation Controller 如何让 Agent 从“一轮工具循环”升级为可持续完成目标。
