# 技术设计：停止生成按钮

## 1. 现有代码分析

**已阅读文件**：
- `frontend/src/components/chat/InputControls.tsx` — 发送按钮 UI，根据 `hasContent/isSubmitting` 切换发送/语音/占位
- `frontend/src/components/chat/InputArea.tsx` — 输入区容器，管理 `isSubmitting` 状态
- `frontend/src/stores/slices/streamingSlice.ts` — `streamingMessages` Map 追踪每个会话的流式消息 ID
- `frontend/src/contexts/wsMessageHandlers.ts` — `message_done/message_error` 处理器，有幂等性检查
- `frontend/src/services/message.ts` — `cancelTaskByMessageId()` API 已存在
- `backend/services/handlers/mixins/message_mixin.py` — `_check_idempotency()` 检查任务终态，防重复处理
- `backend/api/routes/task.py` — `cancel-by-message/{message_id}` 端点，仅标记 DB 状态为 failed

**可复用模块**：
- `cancelTaskByMessageId()` — 前端已有取消 API
- `streamingMessages` Map — 已追踪流式状态（conversationId → messageId）
- `completeStreaming()` — 已有清理流式状态的方法
- 后端 `_check_idempotency()` — 确保 cancel 后 `on_complete` 被安全跳过

**设计约束**：
- 按钮位置必须与发送按钮一致（原地替换）
- 图标使用 lucide-react（与现有一致）
- 样式遵循 `p-2.5 rounded-full` 按钮规范

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| InputControls 新增 props | InputArea.tsx | 传入 `isStreaming` + `onStop` |
| 停止时更新消息状态 | streamingSlice.ts (现有 updateMessage) | 无需修改，直接调用 |

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 点击停止但后端已发完 message_done | 前端先设消息 status='completed'，handleMessageDone 幂等检查跳过 | wsMessageHandlers |
| 点击停止后后端 stream 继续运行 | 后端最终调 on_complete → _check_idempotency 发现任务已 failed → 提前返回，不发 WS、不扣积分 | 后端 message_mixin |
| 快速点击停止→重发 | 停止时立即 completeStreaming，新消息走正常流程不冲突 | InputArea |
| 停止后刷新页面 | MVP 已知限制：被中断的回复不会完整保存到 messages 表（后端 on_complete 被跳过）。后续优化可加 stop API 保存 accumulated_content | 后端 |
| conversationId 为 null（新对话中） | isSubmitting=true 期间不显示停止按钮（此时流式尚未开始） | InputControls |
| cancel API 调用失败 | 前端仍然完成本地状态清理（fire-and-forget），log 错误 | InputArea |

## 3. 技术栈

- 前端：React + TypeScript + Zustand + TailwindCSS + lucide-react
- 后端：无改动（利用现有 cancel API + 幂等性机制）

## 4. 目录结构

#### 修改文件
- `frontend/src/components/chat/InputControls.tsx` — 添加停止按钮渲染逻辑
- `frontend/src/components/chat/InputArea.tsx` — 添加 handleStop + 传递 streaming 状态

#### 无新增文件

## 5. 数据库设计

无变更

## 6. API设计

无新增 API，复用现有：
- `POST /tasks/cancel-by-message/{message_id}` — 标记任务失败

## 7. 前端状态管理

**利用现有 Store**（无需新增字段）：
- `streamingMessages.has(conversationId)` → 判断是否正在生成
- `streamingMessages.get(conversationId)` → 获取当前流式消息 ID
- `updateMessage(messageId, { status: 'completed' })` → 标记消息完成（防止后续 WS 事件重复处理）
- `completeStreaming(conversationId)` → 清理流式状态 + isSending=false

## 8. 开发任务拆分

#### 阶段 1：前端实现（共 2 个文件）

- [ ] 任务 1.1：修改 `InputControls.tsx` — 新增 `isStreaming`/`onStop` props，生成中显示红色停止按钮（Square 图标）
- [ ] 任务 1.2：修改 `InputArea.tsx` — 从 store 读取 streaming 状态，实现 handleStop 逻辑，传递 props

#### 阶段 2：验证

- [ ] 任务 2.1：手动测试 — 发送消息→生成中→点击停止→内容保留→可立即发送新消息
- [ ] 任务 2.2：竞态测试 — 快速停止→重发，确认无异常

## 9. 依赖变更

无（lucide-react 已有 Square 图标）

## 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 后端 stream 不中断浪费资源 | 低 | MVP 可接受；Phase 2 可加 asyncio.Task 取消机制 |
| 刷新后丢失已停止的回复 | 低 | 后端每 20 chunk 保存 accumulated_content，可作为恢复依据 |

## 11. 文档更新清单

- [ ] FUNCTION_INDEX.md（如有）

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（InputControls + InputArea）
- [x] 边界场景均有处理策略（6 个场景）
- [x] 所有修改文件预估变更量 < 30 行
- [x] 无新增依赖
