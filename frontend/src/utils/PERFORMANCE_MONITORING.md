# 性能监控指南

本项目集成了性能监控工具，用于跟踪关键操作的性能指标。

## 快速开始

### 1. 基本使用

```typescript
import { performanceMonitor, PerfMarkers } from '../utils/performanceMonitor';

// 开始测量
performanceMonitor.start(PerfMarkers.MESSAGE_SEND, {
  conversationId: 'conv-123',
  messageLength: 150,
});

// 执行操作
await sendMessage();

// 结束测量
const duration = performanceMonitor.end(PerfMarkers.MESSAGE_SEND, {
  success: true,
});

console.log(`消息发送耗时: ${duration}ms`);
```

### 2. 自动测量异步操作

```typescript
import { measureAsync } from '../utils/performanceMonitor';

const result = await measureAsync(
  'api:fetch-messages',
  async () => {
    return await fetchMessages(conversationId);
  },
  { conversationId, limit: 50 }
);
```

### 3. 自动测量同步操作

```typescript
import { measureSync } from '../utils/performanceMonitor';

const processedData = measureSync(
  'data:process',
  () => {
    return processLargeDataset(data);
  },
  { dataSize: data.length }
);
```

## 预定义性能标记

使用 `PerfMarkers` 常量确保标记名称一致：

```typescript
import { PerfMarkers } from '../utils/performanceMonitor';

// 消息相关
PerfMarkers.MESSAGE_SEND         // 消息发送
PerfMarkers.MESSAGE_STREAM       // 流式响应
PerfMarkers.MESSAGE_LOAD         // 消息加载

// 图片相关
PerfMarkers.IMAGE_GENERATION     // 图片生成
PerfMarkers.IMAGE_UPLOAD         // 图片上传
PerfMarkers.IMAGE_POLLING        // 图片轮询

// 视频相关
PerfMarkers.VIDEO_GENERATION     // 视频生成
PerfMarkers.VIDEO_POLLING        // 视频轮询

// UI 相关
PerfMarkers.CONVERSATION_SWITCH  // 对话切换
PerfMarkers.SCROLL_POSITION      // 滚动位置
PerfMarkers.RENDER               // 渲染性能

// API 相关
PerfMarkers.API_REQUEST          // API 请求
PerfMarkers.API_RESPONSE         // API 响应
```

## 集成示例

### 在 Message Handler 中使用

```typescript
// useTextMessageHandler.ts
import { measureAsync, PerfMarkers } from '../../utils/performanceMonitor';

export function useTextMessageHandler({ ... }) {
  const handleChatMessage = async (
    messageContent: string,
    conversationId: string
  ) => {
    await measureAsync(
      PerfMarkers.MESSAGE_SEND,
      async () => {
        const optimisticMessage = createOptimisticUserMessage(...);
        onMessagePending(optimisticMessage);

        if (onStreamStart) onStreamStart(conversationId, selectedModel.id);

        try {
          await sendMessageStream(conversationId, { ... });
        } catch (error) {
          onMessageSent(createErrorMessage(...));
        }
      },
      {
        conversationId,
        messageLength: messageContent.length,
        modelId: selectedModel.id,
      }
    );
  };

  return { handleChatMessage };
}
```

### 在 API Service 中使用

```typescript
// message.ts
import { performanceMonitor, PerfMarkers } from '../utils/performanceMonitor';

export async function sendMessageStream(
  conversationId: string,
  request: SendMessageRequest,
  callbacks: StreamCallbacks
): Promise<void> {
  performanceMonitor.start(PerfMarkers.MESSAGE_STREAM, {
    conversationId,
    modelId: request.model_id,
  });

  try {
    const response = await axios.post(`/messages/stream`, request, {
      responseType: 'stream',
    });

    // 处理流式响应
    await handleStreamResponse(response, callbacks);

    performanceMonitor.end(PerfMarkers.MESSAGE_STREAM, {
      success: true,
    });
  } catch (error) {
    performanceMonitor.end(PerfMarkers.MESSAGE_STREAM, {
      success: false,
      error: String(error),
    });
    throw error;
  }
}
```

### 在组件中使用

```typescript
// MessageArea.tsx
import { useEffect } from 'react';
import { performanceMonitor, PerfMarkers } from '../../utils/performanceMonitor';

export default function MessageArea({ conversationId }) {
  useEffect(() => {
    if (!conversationId) return;

    performanceMonitor.start(PerfMarkers.CONVERSATION_SWITCH, {
      conversationId,
    });

    const cleanup = () => {
      performanceMonitor.end(PerfMarkers.CONVERSATION_SWITCH, {
        messagesLoaded: messages.length,
      });
    };

    return cleanup;
  }, [conversationId]);

  // ...
}
```

## 性能阈值

监控工具会根据耗时自动选择日志级别：

| 耗时 | 日志级别 | 说明 |
|------|---------|------|
| < 1s | DEBUG | ✅ 性能良好 |
| 1-3s | INFO | ⏱️ 性能一般 |
| > 3s | WARN | ⚠️ 性能较差 |

## 页面性能指标

自动收集的页面性能指标：

```typescript
import { performanceMonitor } from '../utils/performanceMonitor';

const metrics = performanceMonitor.getPageMetrics();

console.table(metrics);
// {
//   dns: 2.5,              // DNS 解析时间
//   tcp: 15.3,             // TCP 连接时间
//   request: 8.2,          // 请求时间
//   response: 120.5,       // 响应时间
//   domParse: 450.2,       // DOM 解析时间
//   resourceLoad: 320.8,   // 资源加载时间
//   totalLoad: 917.5,      // 总加载时间
//   firstPaint: 380.5,     // 首次渲染
//   firstContentfulPaint: 420.3  // 首次内容渲染
// }
```

## 配置

### 启用/禁用监控

默认情况下：
- **开发环境**: 自动启用
- **生产环境**: 默认禁用

通过环境变量控制：

```bash
# .env
VITE_ENABLE_PERF_MONITOR=true
```

### 集成外部监控服务

在 `performanceMonitor.ts` 中集成 Sentry、DataDog 等：

```typescript
private sendToMonitoringService(metric: PerformanceMetric): void {
  // Sentry 示例
  if (window.Sentry && import.meta.env.PROD) {
    window.Sentry.captureMessage('Performance Metric', {
      level: 'info',
      tags: {
        operation: metric.name,
      },
      extra: {
        duration: metric.duration,
        ...metric.metadata,
      },
    });
  }

  // DataDog 示例
  if (window.DD_RUM && import.meta.env.PROD) {
    window.DD_RUM.addAction(metric.name, {
      duration: metric.duration,
      ...metric.metadata,
    });
  }
}
```

## 最佳实践

### 1. 为关键路径添加监控

关键用户操作都应该被监控：
- ✅ 消息发送和接收
- ✅ 图片/视频生成
- ✅ 对话切换
- ✅ 数据加载

### 2. 添加有意义的元数据

```typescript
performanceMonitor.start('operation', {
  userId: currentUser.id,
  conversationId: conversation.id,
  messageCount: messages.length,
  modelId: selectedModel.id,
});
```

### 3. 错误情况也要记录

```typescript
try {
  await operation();
  performanceMonitor.end('operation', { success: true });
} catch (error) {
  performanceMonitor.end('operation', {
    success: false,
    error: error.message,
  });
  throw error;
}
```

### 4. 避免过度监控

不要监控微小操作（< 10ms），会影响性能：
- ❌ 不监控：简单的数据转换、getter/setter
- ✅ 监控：网络请求、大量计算、UI 渲染

## 性能优化指南

### 识别性能瓶颈

1. 运行应用并执行关键操作
2. 查看控制台的性能日志
3. 找出耗时 > 1s 的操作
4. 分析元数据定位问题

### 常见优化策略

1. **API 请求优化**
   - 使用缓存减少重复请求
   - 并行请求而非串行
   - 实现请求去重

2. **数据加载优化**
   - 实现虚拟滚动
   - 懒加载图片/视频
   - 分页加载消息

3. **UI 渲染优化**
   - 使用 React.memo
   - 避免不必要的重新渲染
   - 优化列表渲染

4. **资源优化**
   - 压缩图片/视频
   - 使用 CDN
   - Code splitting

## 监控报告示例

### 控制台输出

```
✅ [Perf] message:send: 145.23ms | {"conversationId":"conv-123","messageLength":150}
⏱️  [Perf] image:generation: 1820.45ms | {"conversationId":"conv-123","aspectRatio":"16:9"}
⚠️ [Perf] video:polling: 3250.12ms | {"taskId":"task-456","attempts":13}
```

### 性能指标表

```
📊 Page Performance Metrics
┌────────────────────────┬──────────┐
│ Metric                 │ Time(ms) │
├────────────────────────┼──────────┤
│ dns                    │ 2.5      │
│ tcp                    │ 15.3     │
│ request                │ 8.2      │
│ response               │ 120.5    │
│ domParse               │ 450.2    │
│ resourceLoad           │ 320.8    │
│ totalLoad              │ 917.5    │
│ firstPaint             │ 380.5    │
│ firstContentfulPaint   │ 420.3    │
└────────────────────────┴──────────┘
```

## 故障排查

### 监控未生效

1. 检查环境变量配置
2. 确认在开发环境或已启用监控
3. 检查浏览器控制台是否有错误

### 性能数据不准确

1. 确保 start/end 成对调用
2. 检查是否在正确的时机调用
3. 验证元数据是否正确传递

### 内存泄漏

如果发现测量未正常结束导致内存泄漏：

```typescript
// 清理所有未完成的测量
performanceMonitor.clear();
```

## 参考资源

- [Web Performance API](https://developer.mozilla.org/en-US/docs/Web/API/Performance_API)
- [React Performance Optimization](https://react.dev/learn/render-and-commit#optimizing-rendering-performance)
- [Vitejs Performance](https://vitejs.dev/guide/performance.html)
