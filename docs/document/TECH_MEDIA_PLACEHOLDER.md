# 技术设计：媒体占位符优化

> **版本**：1.0 | **日期**：2026-01-27 | **状态**：待开发
> **前置文档**：[UI_MEDIA_PLACEHOLDER.md](./UI_MEDIA_PLACEHOLDER.md)

## 1. 技术栈

- 前端：React 19 + TypeScript + Zustand + TailwindCSS 4
- 后端：无改动
- 数据库：无改动

## 2. 目录结构

### 修改文件

| 文件路径 | 改动说明 |
|---------|---------|
| `frontend/src/services/message.ts` | Message 接口添加 `isJustGenerated` 字段 |
| `frontend/src/components/chat/MediaPlaceholder.tsx` | 重构：增加 shimmer 预览框，移除脉冲动画 |
| `frontend/src/components/chat/MessageItem.tsx` | 小改：图片/视频增加条件淡入动画 |
| `frontend/src/stores/useConversationRuntimeStore.ts` | 小改：replaceMediaPlaceholder 增加状态标记 |
| `frontend/src/index.css` | 新增：shimmer 和 fadeIn 动画定义 |

### 新增文件

无

## 3. 数据库设计

无改动（`isJustGenerated` 是前端临时状态，不持久化）

## 4. API设计

无改动

## 5. 前端状态管理

### 5.1 Message 接口扩展

```typescript
// frontend/src/services/message.ts (第9-19行)
export interface Message {
  id: string;
  conversation_id?: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  image_url?: string | null;
  video_url?: string | null;
  is_error?: boolean;
  credits_cost?: number;
  created_at: string;
  isJustGenerated?: boolean;  // ← 新增：淡入动画标记（仅前端状态）
}
```

### 5.2 replaceMediaPlaceholder 改造

```typescript
// frontend/src/stores/useConversationRuntimeStore.ts
replaceMediaPlaceholder: (conversationId: string, placeholderId: string, realMessage: Message) => {
  set((state) => {
    const current = state.states.get(conversationId);
    if (!current) return state;

    // 替换占位符消息，并添加 isJustGenerated 标记
    const updatedMessages = current.optimisticMessages.map(m =>
      m.id === placeholderId
        ? { ...realMessage, isJustGenerated: true }  // ← 添加标记
        : m
    );

    // ... 其余逻辑不变
  });

  // 1秒后清除标记
  setTimeout(() => {
    const state = get();
    const current = state.states.get(conversationId);
    if (!current) return;

    const updatedMessages = current.optimisticMessages.map(m =>
      m.id === realMessage.id
        ? { ...m, isJustGenerated: false }  // ← 清除标记
        : m
    );

    set((state) => {
      const newStates = new Map(state.states);
      newStates.set(conversationId, {
        ...current,
        optimisticMessages: updatedMessages,
      });
      return { states: newStates };
    });
  }, 1000);
},
```

## 6. 组件设计

### 6.1 MediaPlaceholder 重构

**改动前**：图标 + 文字 + 计时 + 脉冲动画（无预览框）

**改动后**：图标 + 文字 + 计时 + **shimmer 预览框**

```typescript
// 组件接口（无变化）
interface MediaPlaceholderProps {
  type: 'image' | 'video';
  startTime: string;
  text?: string;
}
```

**关键实现**：

```tsx
// 预览框结构
<div className="mt-3">
  {/* Shimmer 预览框 */}
  <div
    className={`
      relative overflow-hidden rounded-xl
      ${isImage ? 'w-full max-w-[240px] min-h-[120px]' : 'w-full max-w-[400px] min-h-[200px]'}
      bg-gray-100 dark:bg-gray-700
    `}
  >
    {/* Shimmer 动画层 */}
    <div className="absolute inset-0 shimmer-animation" />

    {/* 居中图标 */}
    <div className="absolute inset-0 flex items-center justify-center">
      {isImage ? (
        <Image className="w-8 h-8 text-gray-400 dark:text-gray-500" />
      ) : (
        <Video className="w-8 h-8 text-gray-400 dark:text-gray-500" />
      )}
    </div>
  </div>
</div>
```

### 6.2 MessageItem 改动

**改动点**：图片/视频渲染时检查 `isJustGenerated`，添加淡入动画类

```tsx
// 图片渲染（约第348-355行）
<img
  src={message.image_url}
  alt={isUser ? '上传的图片' : '生成的图片'}
  className={`
    rounded-xl w-full max-w-[240px] cursor-pointer
    hover:opacity-95 transition-opacity shadow-sm
    ${message.isJustGenerated ? 'animate-fade-in' : ''}
  `}
  onClick={() => setPreviewImageUrl(message.image_url!)}
  onLoad={onMediaLoaded}
  onError={() => {
    // 加载失败时清除标记，避免重试时重复动画
    if (message.isJustGenerated) {
      // 通过 store 清除标记（需要传入 conversationId）
    }
  }}
  loading="lazy"
/>
```

### 6.3 CSS 动画定义

```css
/* frontend/src/index.css */

/* Shimmer 动画 */
.shimmer-animation {
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.6) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

/* 深色模式 shimmer */
.dark .shimmer-animation {
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.15) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
}

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

/* 淡入动画 */
.animate-fade-in {
  animation: fadeIn 500ms ease-out forwards;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
```

## 7. 开发任务拆分

### 阶段1：基础设施（无依赖）

| 任务ID | 任务描述 | 文件 | 预估行数 |
|-------|---------|------|---------|
| 1.1 | Message 接口添加 `isJustGenerated` 字段 | services/message.ts | +1 |
| 1.2 | 添加 shimmer 和 fadeIn CSS 动画 | index.css | +30 |

### 阶段2：核心逻辑（依赖阶段1）

| 任务ID | 任务描述 | 文件 | 预估行数 |
|-------|---------|------|---------|
| 2.1 | replaceMediaPlaceholder 增加状态标记逻辑 | useConversationRuntimeStore.ts | +20 |
| 2.2 | 重构 MediaPlaceholder 组件（shimmer 预览框） | MediaPlaceholder.tsx | +30, -20 |

### 阶段3：集成（依赖阶段2）

| 任务ID | 任务描述 | 文件 | 预估行数 |
|-------|---------|------|---------|
| 3.1 | MessageItem 图片/视频增加条件淡入动画 | MessageItem.tsx | +5 |
| 3.2 | 端到端测试：图片生成 → shimmer → 淡入 | - | - |
| 3.3 | 深色模式测试 | - | - |

### 任务依赖图

```
1.1 ──┬──→ 2.1 ──→ 3.1 ──→ 3.2
      │                    ↓
1.2 ──┴──→ 2.2 ──────────→ 3.3
```

## 8. 依赖变更

无需新增依赖

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| setTimeout 内存泄漏 | 低 | 组件卸载时标记已移除，setTimeout 静默失败 |
| 深色模式 shimmer 不明显 | 低 | 已调整为 0.15 不透明度，测试验证 |
| 淡入动画与懒加载冲突 | 低 | isJustGenerated 仅新生成消息有，历史消息无标记 |
| 快速切换对话导致状态残留 | 低 | 标记基于 conversationId 隔离，互不影响 |

## 10. 文档更新清单

- [ ] FUNCTION_INDEX.md - 更新 MediaPlaceholder 函数说明
- [ ] CURRENT_ISSUES.md - 完成后移除相关 TODO（如有）

## 11. 测试用例

### 11.1 功能测试

| 用例 | 步骤 | 预期结果 |
|-----|------|---------|
| 图片生成 shimmer | 发送图片生成请求 | 显示 shimmer 预览框 + 状态文字 |
| 图片淡入 | 等待图片生成完成 | 图片 500ms 淡入显示 |
| 视频生成 shimmer | 发送视频生成请求 | 显示 shimmer 预览框（400px 宽） |
| 历史消息无动画 | 刷新页面，查看历史图片 | 图片直接显示，无淡入动画 |
| 深色模式 | 切换深色模式 | shimmer 高光变淡（0.15 不透明度） |

### 11.2 边缘测试

| 用例 | 步骤 | 预期结果 |
|-----|------|---------|
| 图片加载失败 | 模拟图片 URL 404 | 显示错误状态，无动画 |
| 快速切换对话 | 生成中切换对话 | 回来后继续显示 shimmer |
| 并发多图生成 | 同时生成3张图 | 各自独立 shimmer，各自淡入 |

---

**确认后保存文档并进入开发（`@4-implementation`）**
