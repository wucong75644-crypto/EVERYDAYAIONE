# 图片生成消息状态统一与失败交互

> 状态：开发完成，待生产环境冒烟验证（2026-07-11）

## 1. 目标与边界

- 历史成功图片必须继续正常显示，兼容已有 URL 字段。
- 历史失败消息不迁移、不修复、不承诺恢复为新失败 UI。
- 修复后新图片统一使用 `pending / completed / failed` 状态。
- 新任务失败后原图片槽位显示失败占位符、中文原因和重新生成按钮。
- 实时 WebSocket 显示与刷新后的数据库显示必须一致。
- 积分不足不创建模型任务，不改变已有失败占位符，显示后端中文业务错误。

## 2. 架构现状

- 后端以 `tasks` 保存每张图片的执行状态，以 `messages` 保存用户可见结果。
- `TaskCompletionService` 统一接收 Webhook/轮询结果，`BatchCompletionService` 汇总多图结果。
- 前端同时维护持久化消息和乐观消息，通过 WebSocket 增量更新。
- 当前实时错误路径会把媒体错误转换为文字内容，数据库最终路径则保存失败图片块，造成刷新前后不一致。

## 3. 统一规则

### 3.1 消息状态

- `pending`：图片任务仍在执行。
- `completed`：任务结束且至少一张图片成功。
- `failed`：任务结束且全部图片失败。

### 3.2 图片槽位

- 图片槽位数量由 `generation_params.num_images` 决定。
- 有有效图片 URL：成功槽位。
- `failed=true`：失败槽位。
- 无 URL 且未失败：生成中槽位。

### 3.3 事件职责

- `image_partial_update`：仅提前更新单张结果。
- `message_done`：媒体任务唯一最终完整快照，可携带 `failed` 状态。
- `message_error`：仅处理无法形成最终媒体消息的请求/系统错误，不得覆盖已持久化的媒体失败快照。

## 4. 兼容策略

### 历史成功图片

统一识别以下 URL 字段：

- `url`
- `original_url`
- `download_url`
- `preview_url`

已有旧消息只要存在有效 URL，继续按成功图片渲染。

### 历史失败消息

- 不执行数据库迁移。
- 不回填新状态。
- 不保证历史失败消息显示重新生成按钮。

## 5. 用户交互

### 新任务失败

1. 原生成中槽位变为失败占位符。
2. 显示中文失败原因。
3. 显示“重新生成”按钮。
4. 不额外创建错误文字消息。
5. 不显示无意义的英文 Axios 错误。

### 重新生成

- 整条失败消息使用 `retry`。
- 多图单个失败槽位使用 `regenerate_single`。
- 请求发出后目标槽位原位恢复生成中。
- 请求失败时恢复操作前消息快照。
- 再次模型失败时恢复失败占位符。

### 积分不足

- 后端继续返回 `INSUFFICIENT_CREDITS` 和中文 `message`。
- 前端读取 `response.data.error.message`，不使用 Axios 英文 `error.message`。
- 首次生成积分不足：不创建图片任务。
- 重新生成积分不足：保留原失败占位符。

## 6. 计划修改文件

| 文件 | 计划修改 |
|---|---|
| `frontend/src/services/api.ts` | 提供统一业务错误提取能力 |
| `frontend/src/services/messageSender.ts` | 区分发送/媒体重试回滚，保留原消息快照 |
| `frontend/src/hooks/useRegenerateHandlers.ts` | 失败消息使用 `retry`，单图使用 `regenerate_single` |
| `frontend/src/contexts/wsMessageHandlers.ts` | 媒体最终失败使用完整消息快照，不转成文字错误 |
| `frontend/src/components/chat/message/MessageMedia.tsx` | 固定槽位渲染成功、生成中和失败状态 |
| `frontend/src/components/chat/media/AiImageGrid.tsx` | 失败槽位展示中文原因和重新生成按钮 |
| `backend/api/routes/message.py` | 图片消息修改前执行积分预检；捕获提交阶段失败 |
| `backend/api/routes/message_request_preparation.py` | 生成类型、权限、图片积分预检与用户消息创建顺序 |
| `backend/api/routes/message_generation_helpers.py` | 统一预检参数与提交阶段失败图片收尾 |
| `backend/services/handlers/image_handler.py` | 复用同一套提交/计费参数，正式提交时再次校验余额 |
| `backend/services/handlers/image_request_settings.py` | 集中解析图片模型、分辨率、数量和总积分 |
| `backend/services/batch_completion_service.py` | 保证最终媒体快照包含一致状态和失败图片块 |

## 7. 测试范围

- 历史成功图片各 URL 字段正常显示。
- 新单图成功、失败、超时。
- 新多图全部成功、部分失败、全部失败。
- 实时事件与刷新加载结果一致。
- 整批 retry 和单图 regenerate_single。
- 重试 HTTP 失败恢复原消息。
- 积分不足首次生成和重新生成均显示中文。
- 重复点击、401、409、网络错误不破坏原消息。

## 8. 风险与回滚

- 不修改数据库结构，不迁移历史数据。
- 后端响应保持向后兼容。
- 前端按文件逐步上线；回滚代码即可恢复旧行为。
- 不修改积分锁定、确认、退款及智能模型重试流程。

## 9. 文档同步

- 修复完成后更新 `docs/CURRENT_ISSUES.md`。
- 如新增或修改公共函数，同步更新 `docs/FUNCTION_INDEX.md`。
- 新增图片请求参数模块，已同步更新 `docs/PROJECT_OVERVIEW.md`。
