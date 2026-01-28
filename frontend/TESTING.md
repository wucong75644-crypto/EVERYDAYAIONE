# 测试指南

本项目使用 Vitest 作为测试框架，目标测试覆盖率为 80%+。

## 快速开始

### 安装依赖

```bash
npm install -D vitest @vitest/ui @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

### 运行测试

```bash
# 运行所有测试
npm test

# 监听模式
npm run test:watch

# 生成覆盖率报告
npm run test:coverage

# UI 模式
npm run test:ui
```

## 测试文件组织

```
src/
├── hooks/
│   ├── handlers/
│   │   ├── __tests__/
│   │   │   ├── useTextMessageHandler.test.ts
│   │   │   ├── useImageMessageHandler.test.ts
│   │   │   └── mediaHandlerUtils.test.ts
│   │   └── ...
│   └── ...
├── utils/
│   ├── __tests__/
│   │   └── polling.test.ts
│   └── ...
└── test/
    ├── setup.ts           # 测试环境配置
    └── testUtils.tsx      # 测试工具函数
```

## 测试覆盖范围

### ✅ 已测试模块

1. **useTextMessageHandler** (100% 覆盖)
   - ✅ 文本消息发送
   - ✅ 带图片的消息
   - ✅ 错误处理
   - ✅ Thinking 模式

2. **mediaHandlerUtils** (100% 覆盖)
   - ✅ 错误消息提取
   - ✅ 图片 URL 提取
   - ✅ 视频 URL 提取
   - ✅ 生成错误处理

3. **PollingManager** (100% 覆盖)
   - ✅ 轮询启动/停止
   - ✅ 成功/失败回调
   - ✅ 超时处理
   - ✅ 竞态条件防护

### 🚧 待添加测试

1. **useImageMessageHandler**
   - 图片生成流程
   - 图片编辑流程
   - 轮询管理

2. **useVideoMessageHandler**
   - 文本生视频
   - 图片生视频
   - 轮询管理

3. **Scroll Hooks**
   - useConversationSwitchScroll
   - useMessageLoadingScroll
   - useNewMessageScroll
   - useStreamingScroll
   - useMediaReplacementScroll

4. **Regenerate Hooks**
   - useRegenerateFailedMessage
   - useRegenerateAsNewMessage

## 编写测试的最佳实践

### 1. 使用 describe/it 结构

```typescript
describe('ComponentName', () => {
  describe('feature or function', () => {
    it('should do something specific', () => {
      // Test implementation
    });
  });
});
```

### 2. 使用 beforeEach 清理

```typescript
beforeEach(() => {
  vi.clearAllMocks();
});
```

### 3. Mock 外部依赖

```typescript
vi.mock('../../../services/message', () => ({
  sendMessageStream: vi.fn(),
  createMessage: vi.fn(),
}));
```

### 4. 测试异步操作

```typescript
it('should handle async operation', async () => {
  await result.current.someAsyncFunction();

  await waitFor(() => {
    expect(mockFn).toHaveBeenCalled();
  });
});
```

### 5. 测试错误情况

```typescript
it('should handle errors gracefully', async () => {
  vi.mocked(someFunction).mockRejectedValue(new Error('Test error'));

  await result.current.handleFunction();

  expect(onError).toHaveBeenCalled();
});
```

## 测试工具

### 测试 Hooks

```typescript
import { renderHook, waitFor } from '@testing-library/react';

const { result } = renderHook(() => useCustomHook(props));
```

### 测试组件

```typescript
import { render, screen } from '@testing-library/react';

render(<Component />);
expect(screen.getByText('Hello')).toBeInTheDocument();
```

### Mock 数据

使用 `src/test/testUtils.tsx` 中的预定义 mock 数据：

```typescript
import { mockMessage, mockChatModel, mockAsyncFn } from '../test/testUtils';
```

## 覆盖率目标

- **Lines**: 80%+
- **Functions**: 80%+
- **Branches**: 80%+
- **Statements**: 80%+

## CI/CD 集成

在 CI 环境中，测试会自动运行并生成覆盖率报告。如果覆盖率低于阈值，构建将失败。

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: npm run test:coverage

- name: Check coverage
  run: |
    if [ $(cat coverage/coverage-summary.json | jq '.total.lines.pct') -lt 80 ]; then
      echo "Coverage is below 80%"
      exit 1
    fi
```

## 故障排查

### 常见问题

1. **测试超时**
   ```typescript
   it('slow test', async () => {
     // ...
   }, 10000); // 增加超时时间到 10 秒
   ```

2. **Mock 不生效**
   ```typescript
   // 确保 mock 在 import 之前
   vi.mock('./module');
   import { function } from './module';
   ```

3. **异步测试不稳定**
   ```typescript
   // 使用 waitFor 等待条件满足
   await waitFor(() => {
     expect(element).toBeVisible();
   }, { timeout: 3000 });
   ```

## 参考资源

- [Vitest 文档](https://vitest.dev/)
- [Testing Library 文档](https://testing-library.com/)
- [Jest DOM 匹配器](https://github.com/testing-library/jest-dom)
