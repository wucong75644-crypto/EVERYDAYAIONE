# Runtime 图片任务统一 Owner 技术说明

## 目标

普通图片（`GenerationType.IMAGE`）的唯一执行 Owner 是 Agent Runtime。模型只产生工具意图；Runtime 负责 Action、策略回执、媒体准备、积分锁定、Provider 提交、重试/未知态、投影和消息槽位更新。

电商图（`GenerationType.IMAGE_ECOM`）保持独立，不纳入本次迁移。

## 已闭合的链路

### HTTP 普通图片

`message.py` → `GenerationLifecycle.prepare` → `submit_agent_runtime_media_image_batch_v2` → `ActionLoop` → `RuntimeKieMediaProvider` → `agent_runtime_media_projection_worker` → workspace/asset/message slot。

### Chat `generate_image`

`ChatToolMixin` → `submit_agent_runtime_chat_action_v1` → `agent_session_commands.payload` 保存 `task_id/input_message_id/output_message_id` → `prepare_agent_runtime_media_batch_v1` → `RuntimeKieMediaProvider` → 媒体投影。

聊天 Action 仍使用通用 Chat Action 入口，但媒体执行不再回到 Chat 或旧 Handler；媒体 Runtime 根据 `generate_image` 和已验证的消息锚点进入统一的 batch/provider 绑定流程。

## Owner 门禁

- `ImageHandler.start()` 对普通图片 fail-closed，不能再直接创建 Provider adapter 或提交 Provider。
- `EcomImageHandler` 显式保留旧链路，避免把电商图产品误并入普通图片 Runtime。
- `BackgroundTaskWorker` 和 `TaskCompletionService` 对 `delivery_context.runtime=true` 的任务继续跳过，防止第二 Owner 抢占。
- Runtime/Provider/Projection 的生产开关仍需通过 readiness、provider probe 和 projection heartbeat 后由部署流程开启；本次代码变更不直接开启生产开关。

## 验收证据

必须至少证明：

1. Chat 图片 Action 的 command payload 有输入/输出消息锚点；
2. Runtime 媒体准备能创建稳定 slot、credit binding 和 provider request；
3. Provider 提交与 reconcile 只由 ActionLoop/Runtime Provider 执行；
4. 完成事件先经过 Runtime media projection，再持久化资产并更新消息；
5. 普通 `ImageHandler` 不能执行旧 Provider，电商图仍可独立执行；
6. 同一 `action_id` 重试不会产生第二次 Provider submission。

## 回滚

先停止 Runtime media owner 并保留未完成 Action，再执行 `230_13` rollback 恢复 Chat Action 的旧函数定义。普通图片 Handler 的关闭需要与运行版本一起回滚；不能单独恢复旧 Provider 而继续让 Runtime 任务留在 `runtime=true` 状态。
