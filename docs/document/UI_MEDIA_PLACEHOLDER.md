# UI设计文档：媒体占位符优化

> **版本**：1.0 | **日期**：2026-01-27 | **状态**：待技术设计

## 1. 需求概述

优化图片/视频生成的占位符体验：
- 在现有占位符下增加预览框（shimmer 动画）
- 图片返回时平滑淡入显示
- 支持深色模式

## 2. 页面结构

### 生成中状态

```
┌─────────────────────────────────────────┐
│  🖼️ 图片生成中...  已运行 0:32          │  ← 状态文字 + 计时
├─────────────────────────────────────────┤
│ ┌─────────────────────────────────────┐ │
│ │                                     │ │
│ │      ░░░░ shimmer 动画 ░░░░         │ │  ← 预览框
│ │           🖼️ (半透明图标)           │ │
│ │                                     │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### 完成状态（淡入）

```
┌─────────────────────────────────────────┐
│ ┌─────────────────────────────────────┐ │
│ │                                     │ │
│ │         实际图片（淡入显示）          │ │
│ │                                     │ │
│ └─────────────────────────────────────┘ │
│ [查看] [下载]                           │  ← 操作按钮
└─────────────────────────────────────────┘
```

## 3. 组件规格

### 3.1 预览框尺寸

| 媒体类型 | 尺寸策略 | 最小高度 | 说明 |
|---------|---------|----------|------|
| 图片 | `max-width: 240px` | `min-height: 120px` | 防止空URL导致0高度 |
| 视频 | `max-width: 400px` | `min-height: 200px` | 防止空URL导致0高度 |

**原因**：图片支持11种比例（1:1, 16:9, 9:16...），视频支持2种（landscape, portrait），占位框无法预知最终比例，采用灵活尺寸。最小高度防止布局抖动（CLS）。

### 3.2 Shimmer 动画

| 属性 | 浅色模式 | 深色模式 |
|-----|---------|---------|
| 背景色 | `#f3f4f6` (gray-100) | `#374151` (gray-700) |
| 渐变高光 | `rgba(255,255,255,0.6)` | `rgba(255,255,255,0.15)` |
| 动画周期 | 1.5s | 1.5s |
| 缓动函数 | ease-in-out | ease-in-out |
| 方向 | 从左到右 (-100% → 100%) | 从左到右 |

### 3.3 淡入动画

| 属性 | 值 |
|-----|-----|
| 时长 | 500ms |
| 缓动 | ease-out |
| 效果 | opacity: 0 → 1 |

## 4. 状态设计

| 状态 | 触发条件 | 显示内容 | `isJustGenerated` | 备注 |
|-----|---------|---------|-------------------|------|
| **生成中** | 任务开始 | shimmer 预览框 + 状态文字 | - | 占位符消息 |
| **淡入中** | 替换为真实消息 | 图片 + 淡入动画 | `true` | 动画播放中 |
| **完成** | 动画结束/1秒超时 | 图片 + 操作按钮 | `false` | 正常状态 |
| **加载失败** | img.onError | 错误图标 + 重试 | `false`（立即清除） | 避免重试时重复动画 |
| **任务失败** | 后端报错 | 错误提示文字 | - | 无图片URL |

## 5. 交互流程

### 5.1 图片生成流程

```
1. 用户发送图片生成请求
   ↓
2. 创建 streaming-xxx 占位符消息
   - 显示 MediaPlaceholder（shimmer 预览框）
   ↓
3. 后端任务完成，返回图片URL
   ↓
4. replaceMediaPlaceholder 替换消息
   - 设置 isJustGenerated: true
   - ID从 streaming-xxx 变为 UUID → React 检测到 key 变化
   - 触发组件卸载并重新挂载（这是预期行为）
   - isJustGenerated 标记用于在新组件挂载时触发淡入
   ↓
5. MessageItem 检测到 isJustGenerated
   - 图片添加 animate-fade-in 类
   - 播放 500ms 淡入动画
   ↓
6. 1秒后自动清除 isJustGenerated 标记
```

**关键说明**：方案D（状态标记法）不阻止组件卸载，而是利用标记在新组件挂载时触发淡入动画。

### 5.2 状态标记生命周期

```typescript
// 标记添加时机
replaceMediaPlaceholder() → isJustGenerated = true

// 标记移除时机（3种情况）
1. 正常：setTimeout(() → isJustGenerated = false, 1000)
2. 加载失败：img.onError → isJustGenerated = false（避免重试时再次动画）
3. 组件卸载：useEffect cleanup 清除定时器（避免内存泄漏）

// 标记用途
MessageItem 渲染时检查 → 决定是否播放淡入动画
```

### 5.3 边缘情况处理

| 场景 | 处理方式 |
|-----|---------|
| 用户在1秒内切换对话 | 定时器继续执行，但不影响其他对话 |
| 用户在1秒内删除消息 | 消息从列表移除，定时器执行但找不到消息（静默失败） |
| 图片加载失败 | onError 立即清除标记，显示错误状态 |
| 网络超时后重试 | 重试时重新触发 replaceMediaPlaceholder → 重新设置标记 |

## 6. 响应式设计

| 设备 | 预览框 max-width | 说明 |
|-----|-----------------|------|
| 移动端 (< 640px) | 100% 容器宽度 | 不超过消息气泡宽度 |
| 平板/桌面 | 240px / 400px | 图片/视频 |

## 7. 深色模式适配

| 元素 | 浅色模式 | 深色模式 |
|-----|---------|---------|
| 预览框背景 | `bg-gray-100` | `dark:bg-gray-700` |
| shimmer 高光 | `rgba(255,255,255,0.6)` | `rgba(255,255,255,0.15)` |
| 图标颜色 | `text-gray-400` | `dark:text-gray-500` |
| 状态文字 | `text-gray-700` | `dark:text-gray-300` |
| 计时文字 | `text-gray-400` | `dark:text-gray-500` |

## 8. 组件清单

| 文件 | 操作 | 改动说明 |
|------|------|---------|
| `services/message.ts` | **扩展接口** | Message 添加 `isJustGenerated?: boolean` 字段 |
| `MediaPlaceholder.tsx` | **改造** | 增加 shimmer 预览框，移除脉冲动画 |
| `MessageItem.tsx` | **小改** | 图片/视频增加条件淡入动画 + onError 清除标记 |
| `useConversationRuntimeStore.ts` | **小改** | replaceMediaPlaceholder 增加状态标记逻辑 |

### 8.1 Message 接口扩展

```typescript
// frontend/src/services/message.ts
export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  image_url: string | null;
  video_url: string | null;
  credits_cost: number;
  created_at: string;
  is_error?: boolean;
  isJustGenerated?: boolean;  // ← 新增：淡入动画标记（仅前端状态）
}
```

**注意**：`isJustGenerated` 是前端临时状态，不持久化到数据库，刷新页面后自动消失。

## 9. 缓存兼容性

### 9.1 isJustGenerated 不影响缓存

```typescript
// useMessageLoader.ts:30 过滤逻辑
.filter((m) => !m.id.startsWith('temp-') && !m.id.startsWith('streaming-'))

// isJustGenerated 是临时状态，不影响过滤
// 刷新页面后：从数据库加载，无 isJustGenerated 标记，无动画（符合预期）
```

### 9.2 历史消息无动画

```
场景：用户刷新页面，加载历史消息
结果：消息无 isJustGenerated 标记 → 无淡入动画 ✅
```

### 9.3 重新生成正常触发

```
场景：用户点击重新生成
流程：replaceMediaPlaceholder 触发 → 设置标记 → 播放动画 ✅
```

## 10. 参考设计

| 平台 | 特点 |
|-----|------|
| Midjourney | 深色占位框 + 渐进式显示 |
| Instagram | 灰色骨架屏 + shimmer 光泽 |
| YouTube | 缩略图占位 + 平滑淡入 |

---

**确认后进入技术设计（`@3-dev-doc`）**
