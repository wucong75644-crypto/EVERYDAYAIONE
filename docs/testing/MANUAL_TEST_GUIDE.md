# 消息系统手动测试指南

> 在浏览器中打开 http://localhost:3000 进行测试

## 测试前准备

1. 打开浏览器开发者工具 (F12)
2. 切换到 Console 标签
3. 确认已登录账号

## 测试辅助工具

在控制台输入以下代码启用测试辅助工具：

```javascript
// 获取当前对话 ID（从 URL 或 store 中）
const getConversationId = () => {
  const match = window.location.pathname.match(/\/chat\/([a-f0-9-]+)/);
  return match ? match[1] : null;
};

// 检查 RuntimeStore 状态
const checkRuntimeState = () => {
  const store = window.__zustand?.['conversation-runtime'];
  if (!store) return console.log('Store 不可用');

  const convId = getConversationId();
  if (!convId) return console.log('未找到对话 ID');

  const state = store.getState().getState(convId);
  console.log('RuntimeStore 状态:', {
    optimisticMessages: state.optimisticMessages.length,
    streamingMessageId: state.streamingMessageId,
    streamingContent: state.streamingContent?.length || 0,
  });
};

// 检查 TaskStore 状态
const checkTaskStore = () => {
  const store = window.__zustand?.['task-store'];
  if (!store) return console.log('Store 不可用');

  console.log('媒体任务:', store.getState().mediaTasks);
};
```

---

## 测试场景

### T01: 聊天消息正常发送

**操作步骤**:
1. 选择一个聊天模型（如 Claude）
2. 在输入框输入"你好，请简短回复"
3. 点击发送

**验证点**:
- [ ] 用户消息立即显示
- [ ] 出现流式占位符（光标闪烁）
- [ ] 内容逐字显示
- [ ] 完成后无残留占位符
- [ ] 控制台无报错

**控制台验证**:
```javascript
// 发送后立即执行
checkRuntimeState();
// 应该看到 optimisticMessages > 0, streamingMessageId 非空

// 完成后执行
checkRuntimeState();
// 应该看到 optimisticMessages = 0, streamingMessageId = null
```

---

### T02: 图片生成正常发送

**操作步骤**:
1. 选择图片生成模型
2. 输入"一只可爱的猫咪"
3. 点击发送

**验证点**:
- [ ] 用户消息立即显示
- [ ] 占位符显示"图片生成中..."
- [ ] 生成完成后显示图片
- [ ] Toast 提示"图片生成完成"

**控制台验证**:
```javascript
// 发送后立即执行
checkTaskStore();
// 应该看到有一个 image 类型的任务

// 完成后执行
checkTaskStore();
// 任务应该已清除
```

---

### T03: 视频生成正常发送

**操作步骤**:
1. 选择视频生成模型
2. 输入"海浪拍打沙滩"
3. 点击发送

**验证点**:
- [ ] 用户消息立即显示
- [ ] 占位符显示"视频生成中..."
- [ ] 生成完成后显示视频
- [ ] Toast 提示"视频生成完成"

---

### T04: 聊天刷新后任务恢复

**操作步骤**:
1. 发送一条聊天消息
2. **在流式输出过程中**按 F5 刷新页面
3. 等待页面加载完成

**验证点**:
- [ ] 占位符自动恢复
- [ ] 已生成的内容立即显示
- [ ] 后续内容继续追加
- [ ] 完成后正常显示

**控制台验证**（刷新后）:
```javascript
// 检查恢复状态
const restorationStore = window.__zustand?.['task-restoration'];
console.log('恢复状态:', restorationStore?.getState());
// 应该看到 restorationComplete: true
```

---

### T05: 图片刷新后任务恢复

**操作步骤**:
1. 发送一个图片生成请求
2. **在生成过程中**按 F5 刷新页面
3. 等待页面加载完成

**验证点**:
- [ ] 占位符恢复显示"图片生成中..."
- [ ] 生成完成后正常显示图片
- [ ] TaskStore 任务正确恢复

---

### T06: 视频刷新后任务恢复

**操作步骤**:
1. 发送一个视频生成请求
2. **在生成过程中**按 F5 刷新页面
3. 等待页面加载完成

**验证点**:
- [ ] 占位符恢复显示"视频生成中..."
- [ ] 生成完成后正常显示视频

---

### T07: WebSocket 断线重连

**操作步骤**:
1. 发送一条消息
2. 在流式输出中，进入开发者工具 Network 标签
3. 右键 WebSocket 连接 -> Close
4. 观察重连和恢复

**验证点**:
- [ ] WebSocket 自动重连
- [ ] 任务恢复流程触发
- [ ] 消息继续正常完成

---

### T08: 快速连续发送

**操作步骤**:
1. 快速连续发送 3 条消息

**验证点**:
- [ ] 每条消息独立显示
- [ ] 每条消息有独立的流式响应
- [ ] 无消息错位或丢失

---

## 常见问题排查

### 占位符残留
如果发现占位符没有被清除：
```javascript
// 检查当前状态
checkRuntimeState();

// 强制清理（调试用）
const store = window.__zustand?.['conversation-runtime'];
const convId = getConversationId();
store?.getState().completeStreaming(convId);
```

### 消息顺序错乱
```javascript
// 检查消息时间戳
const chatStore = window.__zustand?.['chat-store'];
const convId = getConversationId();
const messages = chatStore?.getState().messagesByConversation[convId];
messages?.forEach(m => console.log(m.id, m.created_at, m.role));
```

### 任务未恢复
```javascript
// 检查恢复条件
const restorationStore = window.__zustand?.['task-restoration'];
console.log(restorationStore?.getState());
// 需要 hydrateComplete: true 且 wsConnected: true
```

---

## 测试结果记录

| 场景 | 状态 | 备注 |
|------|------|------|
| T01 聊天发送 | ⬜ | |
| T02 图片生成 | ⬜ | |
| T03 视频生成 | ⬜ | |
| T04 聊天恢复 | ⬜ | |
| T05 图片恢复 | ⬜ | |
| T06 视频恢复 | ⬜ | |
| T07 断线重连 | ⬜ | |
| T08 并发发送 | ⬜ | |

状态: ✅ 通过 | ❌ 失败 | ⬜ 未测试
