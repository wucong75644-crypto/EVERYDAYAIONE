# 消息系统测试计划

> **版本**: v1.0
> **日期**: 2026-02-07
> **范围**: 消息发送、接收、刷新恢复全流程测试

## 测试场景总览

| ID | 场景 | 类型 | 优先级 |
|----|------|------|--------|
| T01 | 聊天消息正常发送返回 | 功能 | P0 |
| T02 | 图片生成正常发送返回 | 功能 | P0 |
| T03 | 视频生成正常发送返回 | 功能 | P0 |
| T04 | 聊天任务刷新后恢复 | 恢复 | P0 |
| T05 | 图片任务刷新后恢复 | 恢复 | P0 |
| T06 | 视频任务刷新后恢复 | 恢复 | P0 |
| T07 | WebSocket 断线重连 | 边界 | P1 |
| T08 | 多任务并发发送 | 并发 | P1 |
| T09 | 发送失败回滚 | 错误 | P1 |
| T10 | 刷新时任务刚好完成 | 竞态 | P2 |

---

## 测试场景详情

### T01: 聊天消息正常发送返回

**前置条件**:
- 用户已登录
- WebSocket 连接正常
- 选择聊天模型

**操作步骤**:
1. 在对话中输入文字消息
2. 点击发送按钮

**验证点**:
- [ ] 用户消息立即显示（乐观更新）
- [ ] 流式占位符出现
- [ ] 内容逐字显示（流式）
- [ ] 完成后占位符被替换为正式消息
- [ ] 无残留的临时消息
- [ ] 积分正确扣除

**技术检查**:
- [ ] RuntimeStore.optimisticMessages 正确添加/清理
- [ ] ChatStore.messages 最终只有正式消息
- [ ] WebSocket 订阅和取消订阅正确

---

### T02: 图片生成正常发送返回

**前置条件**:
- 用户已登录
- WebSocket 连接正常
- 选择图片生成模型

**操作步骤**:
1. 在对话中输入图片描述
2. （可选）上传参考图片
3. 点击发送按钮

**验证点**:
- [ ] 用户消息立即显示
- [ ] 占位符显示"图片生成中..."
- [ ] useMessageStore 注册了任务
- [ ] 生成完成后显示图片
- [ ] 占位符被替换为图片消息
- [ ] Toast 提示"图片生成完成"

**技术检查**:
- [ ] useMessageStore.mediaTasks 正确添加/移除
- [ ] WebSocket message_done 事件正确处理

---

### T03: 视频生成正常发送返回

**前置条件**:
- 用户已登录
- WebSocket 连接正常
- 选择视频生成模型

**操作步骤**:
1. 在对话中输入视频描述
2. （可选）上传参考图片
3. 点击发送按钮

**验证点**:
- [ ] 用户消息立即显示
- [ ] 占位符显示"视频生成中..."
- [ ] useMessageStore 注册了任务
- [ ] 生成完成后显示视频
- [ ] 占位符被替换为视频消息
- [ ] Toast 提示"视频生成完成"

---

### T04: 聊天任务刷新后恢复

**前置条件**:
- 有正在进行的聊天任务（流式输出中）

**操作步骤**:
1. 在聊天流式输出过程中刷新页面
2. 等待页面加载完成

**验证点**:
- [ ] 占位符自动恢复
- [ ] 已生成的内容立即显示
- [ ] 新的流式内容继续追加
- [ ] 完成后占位符被替换
- [ ] 无内容丢失

**技术检查**:
- [ ] fetchPendingTasks 返回正确的聊天任务
- [ ] accumulated_content 正确设置到占位符
- [ ] WebSocket 重新订阅成功

---

### T05: 图片任务刷新后恢复

**前置条件**:
- 有正在进行的图片生成任务

**操作步骤**:
1. 在图片生成过程中刷新页面
2. 等待页面加载完成

**验证点**:
- [ ] 占位符自动恢复（显示"图片生成中..."）
- [ ] useMessageStore 任务重新注册
- [ ] 生成完成后正常显示图片
- [ ] 占位符被正确替换

---

### T06: 视频任务刷新后恢复

**前置条件**:
- 有正在进行的视频生成任务

**操作步骤**:
1. 在视频生成过程中刷新页面
2. 等待页面加载完成

**验证点**:
- [ ] 占位符自动恢复（显示"视频生成中..."）
- [ ] useMessageStore 任务重新注册
- [ ] 生成完成后正常显示视频
- [ ] 占位符被正确替换

---

### T07: WebSocket 断线重连

**前置条件**:
- 有正在进行的任务

**操作步骤**:
1. 断开网络连接（或后端重启）
2. 恢复网络连接

**验证点**:
- [ ] WebSocket 自动重连
- [ ] 订阅状态正确重置
- [ ] 任务恢复流程重新执行
- [ ] 进行中的任务继续正常完成

---

### T08: 多任务并发发送

**前置条件**:
- 用户已登录

**操作步骤**:
1. 快速连续发送多条消息
2. 或同时发起图片和视频生成

**验证点**:
- [ ] 每条消息独立处理
- [ ] 不会出现消息错位
- [ ] 每个任务的占位符正确管理
- [ ] 完成后消息顺序正确

---

### T09: 发送失败回滚

**前置条件**:
- 模拟 API 失败（网络错误/服务端错误）

**操作步骤**:
1. 发送消息时触发 API 错误

**验证点**:
- [ ] 乐观消息被清理（cleanupOptimisticMessages）
- [ ] 显示错误消息
- [ ] 不会残留临时占位符
- [ ] 用户可以重试

---

### T10: 刷新时任务刚好完成

**前置条件**:
- 有正在进行的任务

**操作步骤**:
1. 在任务即将完成时刷新页面
2. 任务在刷新期间完成

**验证点**:
- [ ] 聊天任务：数据库已有消息，不创建占位符
- [ ] 媒体任务：重新加载消息到缓存
- [ ] 无重复消息

---

## 技术验证点

### RuntimeStore 状态检查

```typescript
// 浏览器控制台验证
const state = useConversationRuntimeStore.getState();
const convState = state.getState(conversationId);

console.log('乐观消息:', convState.optimisticMessages);
console.log('流式消息ID:', convState.streamingMessageId);
console.log('流式内容:', convState.streamingContent);
```

### useMessageStore 状态检查

```typescript
const taskState = useuseMessageStore.getState();
console.log('媒体任务:', taskState.mediaTasks);
```

### WebSocket 连接状态

```typescript
// 在 WebSocketContext 中
console.log('已订阅任务:', subscribedTasksRef.current);
console.log('任务-对话映射:', taskConversationMapRef.current);
```

### 任务恢复状态

```typescript
const restoreState = useTaskRestorationStore.getState();
console.log('hydrate完成:', restoreState.hydrateComplete);
console.log('ws连接:', restoreState.wsConnected);
console.log('恢复完成:', restoreState.restorationComplete);
console.log('恢复进行中:', restoreState.restorationInProgress);
```

---

## 测试脚本

测试脚本位于: `frontend/src/__tests__/messageSystem/`

- `chatFlow.test.ts` - 聊天流程测试
- `imageFlow.test.ts` - 图片生成测试
- `videoFlow.test.ts` - 视频生成测试
- `restoration.test.ts` - 刷新恢复测试
- `edge-cases.test.ts` - 边界情况测试

---

## 手动测试检查表

### 聊天消息测试

- [ ] 发送纯文本消息，正常返回
- [ ] 发送长文本消息，流式显示正常
- [ ] 发送消息后立即刷新，恢复正常
- [ ] 流式输出中刷新，恢复正常
- [ ] 快速连续发送多条消息

### 图片生成测试

- [ ] 发送图片生成请求，正常返回
- [ ] 带参考图片的编辑请求
- [ ] 生成过程中刷新，恢复正常
- [ ] 生成完成的 Toast 提示

### 视频生成测试

- [ ] 发送视频生成请求，正常返回
- [ ] 带参考图片的图生视频
- [ ] 生成过程中刷新，恢复正常
- [ ] 生成完成的 Toast 提示

### 错误处理测试

- [ ] 网络断开时发送消息
- [ ] 后端返回错误时的处理
- [ ] 积分不足时的提示
