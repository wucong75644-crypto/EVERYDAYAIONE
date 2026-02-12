# Message Handlers 使用指南

本目录包含所有消息处理相关的 Hook，按消息类型分类。

## 目录结构

```
handlers/
├── README.md                      # 本文档
├── useTextMessageHandler.ts      # 文本消息处理
└── useMediaMessageHandler.ts     # 图片/视频统一处理（通过 type 参数区分）
```

## 快速开始

### 1. 文本消息处理

处理普通聊天消息和带图片输入的对话。

```typescript
import { useTextMessageHandler } from './handlers/useTextMessageHandler';

function ChatComponent() {
  const { handleChatMessage } = useTextMessageHandler({
    selectedModel: currentChatModel,
    thinkingEffort: 'medium',
    deepThinkMode: false,
    onMessagePending: (message) => {
      console.log('消息待发送:', message);
    },
    onMessageSent: (aiMessage) => {
      console.log('AI 回复:', aiMessage);
    },
    onStreamContent: (text, conversationId) => {
      console.log('流式内容:', text);
    },
    // 注意：onStreamStart 已废弃，广播由内部 tabSync 处理
  });

  // 发送文本消息
  const sendMessage = async () => {
    await handleChatMessage('Hello, AI!', 'conversation-id');
  };

  // 发送带图片的消息
  const sendMessageWithImage = async () => {
    await handleChatMessage(
      'Describe this image',
      'conversation-id',
      'https://example.com/image.jpg'
    );
  };

  return <button onClick={sendMessage}>Send</button>;
}
```

**支持的功能：**
- ✅ 流式响应
- ✅ 图片输入
- ✅ Thinking 模式（minimal/low/medium/high）
- ✅ Deep Think 模式
- ✅ 错误处理

---

### 2. 图片消息处理

处理图片生成和图片编辑。使用统一的 `useMediaMessageHandler`，通过 `type: 'image'` 参数区分。

```typescript
import { useMediaMessageHandler } from './handlers/useMediaMessageHandler';

function ImageGenerationComponent() {
  const { handleMediaGeneration } = useMediaMessageHandler({
    type: 'image',
    selectedModel: currentImageModel,
    aspectRatio: '16:9',
    resolution: '1024x1024',
    outputFormat: 'png',
    conversationTitle: 'AI 图片生成',
    onMessagePending: (message) => {
      console.log('图片生成中:', message);
    },
    onMessageSent: (aiMessage) => {
      console.log('图片生成完成:', aiMessage);
    },
    onMediaTaskSubmitted: () => {
      console.log('任务已提交到后台');
    },
  });

  // 文本生图片
  const generateImage = async () => {
    await handleImageGeneration(
      'A beautiful sunset over mountains',
      'conversation-id'
    );
  };

  // 图片编辑
  const editImage = async () => {
    await handleImageGeneration(
      'Make it more vibrant',
      'conversation-id',
      'https://example.com/original.jpg' // 输入图片 URL
    );
  };

  return (
    <>
      <button onClick={generateImage}>Generate Image</button>
      <button onClick={editImage}>Edit Image</button>
    </>
  );
}
```

**支持的功能：**
- ✅ 文本生图片
- ✅ 图片编辑
- ✅ WebSocket 实时推送（异步处理）
- ✅ 自定义宽高比
- ✅ 分辨率控制
- ✅ 输出格式选择（png/jpg/webp）
- ✅ 积分消耗跟踪

**后台任务流程（POST + WebSocket 模式）：**
1. 用户消息立即显示（乐观更新）
2. 占位符消息显示"生成中..."
3. POST API 请求提交，立即返回 task_id
4. WebSocket 订阅任务状态，接收实时推送
5. 任务完成后替换占位符为真实图片

---

### 3. 视频消息处理

处理文本生视频和图片生视频。使用统一的 `useMediaMessageHandler`，通过 `type: 'video'` 参数区分。

```typescript
import { useMediaMessageHandler } from './handlers/useMediaMessageHandler';

function VideoGenerationComponent() {
  const { handleMediaGeneration } = useMediaMessageHandler({
    type: 'video',
    selectedModel: currentVideoModel,
    videoFrames: '10',
    videoAspectRatio: 'landscape',
    removeWatermark: true,
    conversationTitle: 'AI 视频生成',
    onMessagePending: (message) => {
      console.log('视频生成中:', message);
    },
    onMessageSent: (aiMessage) => {
      console.log('视频生成完成:', aiMessage);
    },
    onMediaTaskSubmitted: () => {
      console.log('任务已提交到后台');
    },
  });

  // 文本生视频
  const generateVideo = async () => {
    await handleVideoGeneration(
      'A cat playing piano',
      'conversation-id'
    );
  };

  // 图片生视频
  const imageToVideo = async () => {
    await handleVideoGeneration(
      'Animate this image',
      'conversation-id',
      'https://example.com/image.jpg' // 输入图片 URL
    );
  };

  return (
    <>
      <button onClick={generateVideo}>Generate Video</button>
      <button onClick={imageToVideo}>Image to Video</button>
    </>
  );
}
```

**支持的功能：**
- ✅ 文本生视频
- ✅ 图片生视频
- ✅ WebSocket 实时推送（异步处理）
- ✅ 帧数控制（5/10/15 帧）
- ✅ 宽高比选择（landscape/portrait/square）
- ✅ 水印去除选项
- ✅ 积分消耗跟踪

**后台任务流程（POST + WebSocket 模式）：**
1. 用户消息立即显示（乐观更新）
2. 占位符消息显示"生成中..."
3. POST API 请求提交，立即返回 task_id
4. WebSocket 订阅任务状态，接收实时推送
5. 任务完成后替换占位符为真实视频

---

## 工具函数

错误提取和消息创建逻辑已统一移至 `services/messageSender.ts`。

---

## 错误处理

所有 Handler 都提供完整的错误处理：

### 1. 网络错误

```typescript
try {
  await handleChatMessage('Hello', 'conv-id');
} catch (error) {
  // 自动显示错误消息给用户
  // 错误会通过 onMessageSent 回调传递
}
```

### 2. API 错误

后端返回的错误会自动提取并显示友好消息：

```json
{
  "error": {
    "message": "积分不足，请充值"
  }
}
```

会显示为："图片生成失败: 积分不足，请充值"

### 3. 超时错误

媒体生成任务由后端统一管理超时：
- 图片：最长 10 分钟
- 视频：最长 30 分钟

超时后后端通过 WebSocket 推送失败状态，前端显示错误消息。

---

## 性能优化

### 1. 并行处理

图片和视频 Handler 使用 `Promise.all` 并行处理：

```typescript
const [, response] = await Promise.all([
  saveUserMessage(...),  // 保存用户消息
  generateImage(...)      // 请求生成
]);
```

### 2. WebSocket 实时推送

媒体生成使用 WebSocket 订阅，实时接收任务状态：

```typescript
// 通过 sendMessage 自动处理 WebSocket 订阅
await sendMessage({
  conversationId: 'xxx',
  content: [{ type: 'text', text: prompt }],
  generationType: 'image',
  subscribeTask: (taskId, conversationId) => ws.subscribe(taskId),
});
```

### 3. 占位符优化

使用乐观更新，立即显示占位符，提升用户体验：

```typescript
// 立即显示
onMessagePending(userMessage);
onMessagePending(placeholder);

// 后台处理
await generateImage(...);
```

---

## 集成示例

完整的消息处理器组合：

```typescript
import { useMessageHandlers } from '../useMessageHandlers';

function MessageComposer() {
  const {
    handleChatMessage,
    handleImageGeneration,
    handleVideoGeneration,
  } = useMessageHandlers({
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
    videoFrames,
    videoAspectRatio,
    removeWatermark,
    conversationTitle,
    onMessagePending,
    onMessageSent,
    onStreamContent,
    onMediaTaskSubmitted,
  });

  const handleSubmit = async (content: string, imageUrl?: string) => {
    switch (selectedModel.type) {
      case 'chat':
        await handleChatMessage(content, conversationId, imageUrl);
        break;
      case 'image':
        await handleImageGeneration(content, conversationId, imageUrl);
        break;
      case 'video':
        await handleVideoGeneration(content, conversationId, imageUrl);
        break;
    }
  };

  return <form onSubmit={() => handleSubmit(input, uploadedImage)}>...</form>;
}
```

---

## 测试

> **待完善**：测试用例待补充。

---

## 常见问题

### Q: 如何取消正在进行的媒体生成任务？

A: 使用 WebSocket 上下文的取消订阅方法：

```typescript
import { useWebSocketContext } from '../../contexts/WebSocketContext';

const { unsubscribeTask } = useWebSocketContext();
unsubscribeTask(taskId);
```

### Q: 如何监控任务进度？

A: 通过 `useMessageStore` 获取任务状态：

```typescript
import { useMessageStore } from '../../stores/useMessageStore';

const task = useMessageStore.getState().getTask(taskId);

console.log(task?.status); // 'pending' | 'processing' | 'completed' | 'failed'
```

### Q: WebSocket 连接断开怎么办？

A: WebSocket 上下文会自动重连，任务订阅会在重连后自动恢复：

```typescript
// WebSocketContext 内部处理重连逻辑
// 用户无需手动干预
```

### Q: 如何获取生成消耗的积分？

A: 从响应中获取 `credits_consumed`：

```typescript
onMessageSent: (aiMessage) => {
  console.log('消耗积分:', aiMessage?.credits_cost);
};
```

---

## 最佳实践

1. **始终提供错误回调**
   ```typescript
   onMessageSent: (aiMessage) => {
     if (aiMessage?.is_error) {
       showErrorNotification(aiMessage.content);
     }
   };
   ```

2. **使用后台任务提示**
   ```typescript
   onMediaTaskSubmitted: () => {
     toast.success('任务已提交，将在后台处理');
   };
   ```

3. **清理未完成的任务**
   ```typescript
   useEffect(() => {
     return () => {
       // WebSocket 订阅会在组件卸载时自动清理
       unsubscribeTask(taskId);
     };
   }, [taskId]);
   ```

4. **WebSocket 订阅自动管理**
   - 使用 `sendMessage` 自动处理订阅/取消订阅
   - 页面刷新后通过 `taskRestoration` 恢复未完成任务

---

## 相关文档

- [测试指南](../../TESTING.md)
- [API 文档](../../../backend/docs/API.md)
- [状态管理](../../stores/README.md)

---

## 贡献

如需添加新的消息类型处理器，请遵循以下步骤：

1. 创建新的 Handler 文件（如 `useAudioMessageHandler.ts`）
2. 实现核心逻辑
3. 添加单元测试
4. 更新本文档
5. 在 `useMessageHandlers.ts` 中集成

示例模板：

```typescript
export function useNewMessageHandler({
  // 参数
}: UseNewMessageHandlerParams) {
  const handleNewMessage = async (
    content: string,
    conversationId: string
  ) => {
    // 实现逻辑
  };

  return { handleNewMessage };
}
```
