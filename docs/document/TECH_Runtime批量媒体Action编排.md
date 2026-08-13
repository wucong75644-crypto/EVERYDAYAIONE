# Runtime 批量媒体 Action 编排技术方案

## 1. 状态与目标

- 已确认产品目标：普通对话中的文生图、图生图和参考图多方案生成统一由 Agent Runtime 执行。
- 首版单批上限为 10 张；每张图片独立执行、结算、重试、取消和恢复。
- 独立 DetailPage 电商分析、`image-ecom` 专业模式和管理员主动推送不进入本次通用批量链路。
- 本方案基于候选分支 `codex/runtime-batch-media-v1`，基线合并提交 `4f59d1ee`。
- 生产开关继续默认关闭；本阶段不推送、不部署、不调用真实 Provider。

## 2. 现有事实

### 2.1 可直接复用

- 一个 ModelStep 已能表达多个 Action，并持久化 `batch_hash`、`action_index`、`wave`、
  `dependency_ids` 和 `blocking`。
- blocking Action 全部终态后，Run 可恢复到 ModelLoop，并把工具结果重新放回模型上下文。
- `accepted/unknown` 已受 claim fence 约束，只允许 readback、reconcile 或 cancel。
- 现有媒体链路已有图片 Task、批次、积分计算、逐项完成、部分成功、单格重试、聚合取消、
  Workspace 持久化和 `image_partial_update`。
- 前端已有多图网格，但当前依赖内容数组下标，不具备稳定混排槽位合同。

### 2.2 必须补齐

1. Runtime ModelLoop 当前只投影文本，输入参考图没有进入生产模型消息。
2. `generate_image` 冻结 schema 只有 `prompt/model`，不能表达参考图片索引、比例和分辨率。
3. raw Action 完成 RPC 没有把数据库计算的 canonical hash 回填给底层原子写入函数。
4. `generate_image` 的 `explicit_intent` 与现有 persisted interaction 恢复合同不一致。
5. 生产 Composition 中 `runtime.media` 仍 disabled；现有 Provider 只是 sidecar 合同，不是实际 KIE 接线。
6. 模型 Action 没有与既有媒体 Task、积分交易和输出槽位原子绑定。
7. Action progress 尚未投影到消息、Asset 和积分；Run 最终文本会覆盖已有媒体内容。
8. 前端乱序完成会错写槽位，第一张完成后其余占位可能消失，图片与最终文本不能稳定混排。

## 3. 关键设计决定

### 3.1 不新建第二套 Batch Runtime

平面批次直接使用现有 Action 数据：

```text
model_step_id + batch_hash = 批次身份
action_index               = 稳定图片槽位序号
wave                       = 0
dependencies               = []
blocking                   = true
```

模型在一次响应中产生 1～10 个 `generate_image` tool calls。不存在模型可见的
`create_batch` 工具，也不为每张图片创建 Child Run。

现有 `wave/dependencies` 只保留为事实，本次不宣称已经支持多阶段成功依赖调度。

### 3.2 多模态输入合同

复用现有消息 `ImagePart` 和 Resource Manifest。模型工具参数不接收任意 URL，只接收当前
输入消息中的稳定索引：

```json
{
  "prompt": "独立完整提示词",
  "reference_image_indexes": [0],
  "aspect_ratio": "1:1",
  "resolution": "2K"
}
```

Runtime 在 Action 执行前根据 Run 固定的输入消息和 manifest 解析真实资产。索引越界、资产
不属于当前租户、manifest 漂移或模型不支持视觉时失败关闭。

### 3.3 工具与产品边界

- 新 Catalog/AgentDefinition revision 中，普通对话只暴露标准 `generate_image`。
- `generate_image` 同时覆盖文生图、图生图和一次多 Action。
- 旧 `image_agent` 不再进入新 Runtime toolset；历史 revision 保持冻结。
- DetailPage 电商需求分析和 `image-ecom` 专业模式保持原路由、模型、计划和重试合同。

### 3.4 精确授权

- 前台 Web/WeCom 用户当前命令直接触发的首个图片 Action batch，可生成绑定
  `command_id + model_step_id + batch_hash + arguments_hashes` 的 explicit-intent receipt。
- receipt 只允许同一工具、同一输入事实和最多 10 个 Action，不形成持久泛化授权。
- 非当前前台命令、模型后续自行扩张、后台或语义不明确的批次只通知一个 confirmation leader；
  批准时仍为每个 Action 生成独立精确 grant 和 PolicyReceipt。
- grouped interaction 由新 RPC 原子解决，旧单 Action RPC 遇到组成员时失败关闭。

## 4. 数据与 RPC

### 4.1 Action canonical hash 修复

新增 additive migration 覆盖 raw Action 完成函数：数据库生成 canonical Action JSON 后，把
`arguments_hash`、`request_hash` 和 canonical `batch_hash` 回填到内部 Action，再调用现有原子
持久化函数。Python 不模拟 PostgreSQL `jsonb::text` 哈希。

### 4.2 媒体关联表

新增 `agent_runtime_media_action_bindings`，只作为 Runtime Action 与既有媒体 Task 的关联：

```text
action_id                 PK/FK agent_actions
task_id                   UNIQUE/FK tasks
run_id
model_step_id
batch_hash
action_index              CHECK 0..9
input_message_id
assistant_message_id
credit_transaction_id
reference_manifest_hash
projection_revision
created_at / updated_at
```

不复制 Action 状态，不复制 Task 生命周期，不开放 Worker 直写。

### 4.3 批量准备 RPC

`prepare_agent_runtime_media_batch_v1` 在一个事务中：

1. 按固定锁序读取租户、Run、ModelStep、完整 Action batch、输入消息和输出消息。
2. 校验 1～10 个同质 `generate_image` Action、连续 `action_index`、参数和参考资产。
3. 复用现有图片参数与积分计算。
4. 全批原子锁定积分，并为每个 Action 创建独立 pending credit transaction。
5. 创建既有媒体 Task、Action binding 和 10 个以内的固定输出槽位。
6. 返回 Executor 所需内部事实，不把 user/org/task/credit id 暴露给模型或 Provider。

余额不足、并发重复、租户不匹配、槽位冲突和输入漂移均整批回滚；此时不得提交 Provider。

### 4.4 投影 RPC

`apply_agent_runtime_media_projection_v1` 按 Action 事件序号执行单调状态转移：

- completed：确定性保存媒体、幂等登记 Asset、确认该项积分、填充对应槽位；
- failed/cancelled：退款该项积分并写入失败或取消状态；
- accepted/unknown：只记录等待 readback/reconcile 的状态；
- 重复或旧 revision 事件不覆盖更新状态。

所有 SQL 函数使用固定 `search_path`、`SECURITY DEFINER`、`session_user/access_kind` 校验、
RLS/FORCE RLS 和最小角色 EXECUTE ACL。

## 5. Provider、恢复与唯一 Owner

- ActionLoop 是 `generate_image` 唯一 Provider submit Owner。
- 复用现有 KIE 图片请求、查询和结果映射，但提交接口禁止普通网络自动重派。
- Provider 请求只包含业务参数和 Provider 幂等事实，不包含 Runtime 的 user/org/credit 内部字段。
- dispatch 前失败可按既有规则处理；dispatch 后失去确定性立即进入 unknown。
- accepted/unknown 只能 readback、reconcile 或 cancel。
- 旧 BackgroundTaskWorker、CompletionService 和 AsyncRetryService 继续跳过 Runtime Task；
  discovery SQL 也直接排除 Runtime binding，避免无效发现和潜在双 Owner。

## 6. 消息与前端槽位合同

Runtime 对话输出保持 chat 消息，增加 `runtime_media_batch` 元数据。每个 ImagePart 增加：

```text
slot_id
slot_index
slot_status = pending|accepted|unknown|completed|failed|cancelled
slot_revision
```

开始执行时一次写入固定数量槽位；局部更新按 `slot_id` 定位，不使用整个 `content` 数组下标。
最终 ModelLoop 文本与槽位合并，图片更新不得覆盖文本，Run 完成也不得覆盖图片。

消息内提供：

- 每格独立状态和失败重试；
- 批次取消入口及成功、取消失败、unknown 数量；
- 已成功图片始终保留；
- 页面刷新、断线重连、乱序和重复事件后的稳定顺序。

普通图片和 `image-ecom` 必须通过 generation metadata/retry context 严格分流。

## 7. 实施顺序

1. 候选基线与方案文档。
2. raw Action hash、授权恢复和批量 confirmation 基础。
3. Runtime 多模态上下文、新 Catalog/Definition 和 1～10 Action 生成。
4. Action/Task binding、批量积分锁定和固定槽位准备。
5. 真实 KIE Runtime Executor、readback/reconcile/cancel 和生产 Composition readiness。
6. Action 终态到 Task、积分、Asset、消息的有序投影。
7. 前端稳定槽位、混合内容、单格重试和批量取消。
8. 旧 Owner discovery 收口和全链路验收。

每个批次独立提交；数据库、授权、Provider 和 Projection 由独立复核验证。

## 8. 验收

- 参考图真实进入模型上下文，模型不能引用当前输入外的资产。
- 一次生成 1、4、10 个 Action 均原子持久化；第 11 个失败关闭。
- 10 个槽位立即出现；以任意顺序完成仍写入原槽位。
- 混合 completed/failed/unknown 时保留成功结果，unknown 不普通重派。
- 单格重试只替换目标槽位；重复活动重试返回冲突。
- 批量取消保留已完成图片，并正确处理 accepted/unknown。
- 余额不足时无 Task、无 binding、无 Provider 副作用。
- 每个 Action 最多提交一次，重启、lease 丢失和 fencing 不产生重复图片。
- 文件和 Asset 登记在重复投影下幂等；积分逐项确认或退款。
- 最终文本与图片各显示一次，刷新和断线恢复后顺序不变。
- 电商独立页面、`image-ecom` 专业模式和专用 retry context 不变。
- 跨租户读取、准备、投影、重试和取消全部失败关闭。
- migration 完成 apply → readback → rollback → reapply。

## 9. 发布与回滚

新增 migration 从 `228_01` 开始，不修改 218～227 和 main 已冻结的 219/220 身份。

发布前所有生产 flags 保持关闭。回滚顺序：关闭新 ingress/catalog revision，停止创建新 binding，
排空未提交 Action，对 accepted/unknown 执行 reconcile/cancel，切回旧 revision；只有不存在非终态
binding、积分挂账和 Provider 不确定状态时，才允许逆序删除新 RPC、索引和空关联表。仍被历史 Run
引用的 Catalog/Definition facts 不删除。

本方案不授权生产部署；生产发布需在 disposable PostgreSQL、CI、回滚演练和用户单独授权后执行。
