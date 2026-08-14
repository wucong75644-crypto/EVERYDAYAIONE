# Agent Runtime 单一运行时收敛方案

状态：已确认并实施 S1 模型链收敛与 S2-ERP-R1 企业 ERP 只读接线。

## 1. 最终边界

系统最终只保留一个执行运行时：

```text
现有产品配置与业务服务
  → 薄 Runtime Adapter
  → ModelLoop / ActionLoop 唯一执行 Owner
  → Runtime Run / ModelStep / Action / Attempt 唯一正确性事实
  → 现有 Web、WeCom、Task、Artifact 用户投影
```

Runtime 负责自主循环、状态推进、授权、成本、幂等、租约、fencing、取消和
`ACCEPTED/UNKNOWN` 恢复。模型、ERP、Media、Scheduler、WeCom 的配置、业务事实、
客户端和发送能力继续使用现有实现，不建立第二套配置或业务平台。

迁移完成后删除旧 ToolLoop、旧执行 Owner、旧 fallback 和无调用方的平行 Runtime
装配。迁移期允许短暂保留代码，但同一任务任何时刻只能有一个副作用和终态 Owner。

## 2. 保留、收敛与删除

### 2.1 保留

- AgentDefinition、Catalog、EffectiveToolset 的 Run-bound 可恢复快照；Catalog 只能由
  当前真实 Executor 接线生成。
- Session、Command、Run、ModelStep、Action、Attempt、RuntimeEvent。
- ModelLoop、ActionLoop、PolicyReceipt、Confirmation、费用、lease、fencing、
  cancel、UNKNOWN/readback/reconcile。
- 现有 `user_subscriptions`、`conversations.model_id`、默认模型配置和
  `services.adapters`。
- 现有企业配置、`AsyncOrgConfigResolver`、`KuaiMaiClient`、`ErpDispatcher`。
- 现有 Media adapter、媒体任务事实、轮询和结果持久化。
- 现有 `scheduled_tasks`、cron、任务管理界面和推送目标。
- 现有 WeCom 配置、AccessToken、Smart Robot/WebSocket 和 App outbound transport。
- Runtime Worker heartbeat、readiness、kill epoch 和必要运维查询。

### 2.2 收敛

- 模型：`对话选择 → 订阅/权限校验 → 当前默认 → Run snapshot`；Provider 构造只使用
  现有 adapter factory。`agent_model_attempts` 是唯一模型执行事实。
- ERP：Runtime Executor 通过 `AsyncOrgConfigResolver → KuaiMaiClient →
  ErpDispatcher` 调用；ActionLoop 是唯一提交、完成和取消 Owner。
- Media：现有图片/视频 adapter 通过薄 Executor 接入；现有后台轮询只能作为
  readback/reconcile transport，不能独立完成 Runtime Action。
- Scheduler：继续使用 `scheduled_tasks`，全部执行绑定切到 Runtime；保留一套 claim、
  CAS、finalization 和 cancel 事实。
- WeCom：发送作为普通 Runtime Action；定时投递复用通用 ActionAttempt/Provider facts，
  不保留专属平行交付平台。
- Ingress：Web、WeCom 和 Scheduler 最终只调用一个正式 Runtime ingress RPC，不探测
  v2/v3/v4/v5，也不回退 Legacy Owner。

### 2.3 删除

- `ChatGenerationExecutor/ToolLoopExecutor/ToolExecutor` 的执行 Owner 路径。
- `ScheduledTaskAgent/ScheduledTaskExecutor` 的 Legacy Owner 路径。
- 旧 ERP/Media ToolLoop 直接执行权和旧 Scheduled WeCom Redis 推送 Owner。
- `ProductionServiceBundle`、`RuntimeProductionAssembly` 和无生产调用方的通用
  production composition。
- `agent_runtime_tenant_provider_bindings`、静态 production binding/readiness 配置源。
- Runtime 重复模型 Provider 工厂；Model Gateway operation 状态机并回
  `agent_model_attempts`。若保留独立进程，只允许做无状态 Secret/transport 隔离。
- 227_04 平行 Provider facts、227_05 平行 Scheduler CAS、227_10/11 无消费者运维队列。
- 旧 ingress RPC、shadow/rollout 临时对象、legacy lifecycle fence。
- Scheduled WeCom 227_38～49、227_51～52 的碎片化请求表；必要幂等操作合并进一套
  通用投递事实。

## 3. 实施批次

### S1 模型链收敛

复用现有模型选择、订阅和 adapter factory；移除重复 Provider factory；让 Runtime
直接通过唯一 ModelAttempt 生命周期完成调用。完成后 Runtime 不再依赖第二套 Gateway
operation 状态机才能启动。

已完成：Runtime 直接使用既有模型选择、配置 Bundle、KEK 解密与
`create_chat_adapter`。`agent_model_attempts` 通过 227_53 原子冻结 dispatch kill epoch，
同一 fenced Attempt 的窄只读 RPC 才可取得 encrypted Bundle。独立 Gateway 进程、UDS、
Python operation owner、systemd unit 和部署环境已删除；227_18～227_27 只作为不可改写的
历史 migration 链保留，不再具有 Python 调用方或运行进程。

### S2 ERP 与 Media 薄适配

接通现有 ERP 配置/client/dispatcher 与 Media adapter。保持 ERP Write、收费 Media
默认关闭，先完成 isolated/disposable 验证；不得新增配置表或 Provider 状态表。

ERP-R1 已完成：六项企业 ERP Read Executor 复用既有 `erp.runtime` Bundle、
`KuaiMaiClient` 与 `ErpDispatcher`。227_54 只增加 ActionAttempt-fenced 配置读取和
Token 版本 CAS 窄 RPC，不增加配置表；租户、worker、execution token、request hash、
attempt version、dispatch intent 与 kill epoch 任一不匹配均失败关闭。Runtime 路径关闭
Redis Token readback、参数知识记录和请求参数日志；刷新 Token 通过既有配置事实 CAS
持久化。227_55 由独立 Python SSOT 确定性冻结 v5 Catalog/Definition/Toolset，保留原
v4 migration 身份；新 release 默认不启用。ERP Write、ERP Sync、淘宝奇门、Media 仍未接入。

### S3 Scheduler 与 WeCom 收敛

统一到现有 `scheduled_tasks` 与 Runtime Run/Action；把 WeCom 发送降为普通 Runtime
Executor，合并专属 delivery/recovery 状态，移除双发送 Owner。

### S4 Ingress 与 Owner 切换

Web、WeCom、Scheduler 只进入最终 Runtime ingress；迁移全部真实调用方后删除 Legacy
fallback、Conversation Actor generation Owner、ToolLoop 和旧 Scheduler execution Owner。

### S5 数据库与部署清理

基于生产当前最高 migration 生成唯一 Runtime 安装清单。先迁移 Python 调用方，再在
disposable PostgreSQL 从生产基线验证最终 schema；确认无引用后移除未部署的临时 227
对象、旧 RPC 权限、Model Gateway 和 Legacy Worker 配置。

### S6 验收与发布

验证 Web/WeCom/定时任务、模型订阅、ERP Read、Media isolated、SAFE/CONFIRM/
DANGEROUS、cancel、UNKNOWN/reconcile、crash recovery、费用与 Projection。必须证明：

- 只有一个 ingress、模型执行、Action、Scheduler 和 WeCom 发送 Owner；
- 只有一套模型、ERP、Media、Scheduler、WeCom 配置事实源；
- 只有一套 ModelAttempt、Provider submission、Scheduler CAS 和 delivery 事实；
- Legacy 进程、RPC、fallback 和直接副作用入口不存在；
- production flags 默认关闭，未授权前不部署。

## 4. 数据库与回滚

224～226 的 Runtime 核心语义原则上保留。227 临时链不能机械删除，因为后续 migration
存在函数重命名和对象依赖。每批先切调用方，再形成最终对象清单和收敛 migration；在
disposable PostgreSQL 验证 apply、权限、RLS、回滚和从生产基线升级。

生产尚未安装这些 227 对象，因此最终发布包不得把已经判定无用的“创建后再删除”临时链
带入生产。发布前应生成经过核验的最终 install manifest；历史开发 migration 仍保留在
Git 审计记录中，但不得成为生产安装事实。

## 5. 停止条件

- 新证据表明现有业务事实无法承载 Runtime；
- 必须改变现有模型订阅、ERP 企业隔离、Media 通用能力或 Scheduler 产品语义；
- 需要生产数据迁移、真实 Provider、副作用或不可逆删除；
- 无法证明旧 Owner 已无真实调用方。

触发停止条件时只暂停受影响批次，其余可隔离批次继续推进。
