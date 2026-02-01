# 方案 A 深度分析：完全统一

## 一、当前 useConversationRuntimeStore 的职责

### 1.1 管理的状态

| 状态 | 说明 | 是否需要持久化 |
|------|------|---------------|
| `optimisticMessages` | 临时消息列表（temp-xxx, streaming-xxx） | ❌ |
| `isGenerating` | 是否正在生成（UI 状态） | ❌ |
| `streamingMessageId` | 当前流式消息 ID | ❌ |

### 1.2 提供的方法（15 个）

| 方法 | 用途 | 调用位置 |
|------|------|---------|
| `addOptimisticUserMessage` | 添加临时用户消息 | useMessageCallbacks |
| `replaceOptimisticMessage` | 替换临时消息为真实消息 | useMessageCallbacks |
| `updateMessageId` | 更新消息 ID | chatSender, useChatStore |
| `addErrorMessage` | 添加错误消息 | useMessageCallbacks |
| `addMediaPlaceholder` | 添加媒体占位符 | imageSender, videoSender, taskRestoration |
| `replaceMediaPlaceholder` | 替换媒体占位符 | imageSender, videoSender, taskRestoration |
| `removeOptimisticMessage` | 移除临时消息 | useMessageCallbacks |
| `startStreaming` | 开始流式生成 | useMessageCallbacks |
| `appendStreamingContent` | 追加流式内容 | useMessageCallbacks |
| `completeStreaming` | 完成流式生成 | useMessageCallbacks |
| `completeStreamingWithMessage` | 完成流式并替换 | useMessageCallbacks |
| `setGenerating` | 设置生成状态 | - |
| `getState` | 获取状态 | Chat.tsx |
| `clearState` | 清空状态 | - |
| `migrateConversation` | 迁移对话状态 | useMessageCallbacks |

### 1.3 使用位置（8 个文件）

| 文件 | 使用方式 |
|------|---------|
| `useMessageCallbacks.tsx` | 核心使用：乐观更新、流式处理 |
| `chatSender.ts` | updateMessageId |
| `imageSender.ts` | replaceMediaPlaceholder |
| `videoSender.ts` | replaceMediaPlaceholder |
| `MessageArea.tsx` | 订阅 runtimeState，合并消息 |
| `Chat.tsx` | LRU 清理时判断 isGenerating |
| `taskRestoration.ts` | 恢复媒体任务占位符 |
| `useStreamingScroll.ts` | 判断 streamingMessageId 自动滚动 |

---

## 二、方案 A 需要的改动

### 2.1 状态迁移到 useChatStore

```typescript
// 新增到 useChatStore
interface ConversationRuntimeState {
  isGenerating: boolean;
  streamingMessageId: string | null;
}

interface ChatState {
  // 现有状态...
  messageCache: Map<string, MessageCacheEntry>;

  // 新增：运行时状态（按对话存储）
  runtimeStates: Map<string, ConversationRuntimeState>;
}
```

### 2.2 临时消息存储策略

**问题**：临时消息（temp-xxx, streaming-xxx）如果存入 messageCache：
1. 会被持久化到 localStorage
2. 刷新后会显示这些无效消息

**解决方案**：
```typescript
// 方案 A-1：在 persist 配置中过滤
partialize: (state) => ({
  messageCache: filterTempMessages(state.messageCache),  // 过滤临时消息
  // runtimeStates 不持久化
})

// 方案 A-2：临时消息单独存储（不持久化）
interface ChatState {
  messageCache: Map<string, MessageCacheEntry>;           // 持久化
  tempMessages: Map<string, Message[]>;                   // 不持久化
}
```

### 2.3 方法整合

| 原方法 | 整合到 | 说明 |
|--------|--------|------|
| `addOptimisticUserMessage` | `appendMessage` | 统一追加 |
| `replaceOptimisticMessage` | `replaceMessage` | 统一替换 |
| `addMediaPlaceholder` | `appendMessage` | 统一追加 |
| `replaceMediaPlaceholder` | `replaceMessage` | 统一替换 |
| `addErrorMessage` | `appendMessage` | 统一追加 |
| `removeOptimisticMessage` | `removeMessage` | 统一删除 |
| `updateMessageId` | `replaceMessage` | 通过替换实现 |
| `startStreaming` | **保留** | 流式专用 |
| `appendStreamingContent` | **保留** | 流式专用 |
| `completeStreaming` | **保留** | 流式专用 |
| `setGenerating` | **保留** | UI 状态 |

**结论**：6 个方法可以整合，5 个方法需要保留（流式 + UI 状态）

### 2.4 需要修改的文件（12 个）

| 文件 | 改动内容 | 复杂度 |
|------|---------|--------|
| `useChatStore.ts` | 新增 runtimeStates + 5 个流式方法 | 高 |
| `useMessageCallbacks.tsx` | 全部改用 useChatStore | 高 |
| `chatSender.ts` | 改用 useChatStore.updateMessageId | 低 |
| `imageSender.ts` | 改用 useChatStore.replaceMessage | 低 |
| `videoSender.ts` | 改用 useChatStore.replaceMessage | 低 |
| `MessageArea.tsx` | 删除 mergeOptimisticMessages | 中 |
| `Chat.tsx` | 改用 useChatStore.runtimeStates | 低 |
| `taskRestoration.ts` | 改用 useChatStore | 中 |
| `useMessageAreaScroll.ts` | 改用 useChatStore | 低 |
| `useStreamingScroll.ts` | 改用 useChatStore | 低 |
| `mergeOptimisticMessages.ts` | **删除** | - |
| `useConversationRuntimeStore.ts` | **删除** | - |

---

## 三、方案 A 的复杂点

### 3.1 高复杂度（必须解决）

| 问题 | 说明 | 解决方案 |
|------|------|---------|
| **持久化过滤** | 临时消息不能存到 localStorage | persist 的 partialize 中过滤 temp-/streaming- 前缀消息 |
| **流式方法整合** | startStreaming/appendStreamingContent 无法用 replaceMessage 替代 | 保留为独立方法，放入 useChatStore |
| **UI 状态分离** | isGenerating 是 UI 状态，与消息数据混合不合理 | 单独存储在 runtimeStates 中 |

### 3.2 中等复杂度

| 问题 | 说明 | 解决方案 |
|------|------|---------|
| **useMessageCallbacks 改造** | 核心回调处理，涉及 15+ 个方法调用 | 逐个替换，充分测试 |
| **mergeOptimisticMessages 删除** | 合并逻辑需要内置到消息获取中 | 统一入口自动处理 |

### 3.3 低复杂度

| 问题 | 说明 | 解决方案 |
|------|------|---------|
| **导入替换** | 7 个文件需要改导入 | 简单查找替换 |
| **方法重命名** | addOptimisticUserMessage → appendMessage | 改调用即可 |

---

## 四、方案 A vs 方案 B 对比

| 维度 | 方案 A（完全统一） | 方案 B（保持分离） |
|------|-------------------|-------------------|
| **数据源** | 1 个（useChatStore） | 2 个（useChatStore + useConversationRuntimeStore） |
| **修改文件数** | 12 个 | 7 个 |
| **删除文件数** | 2 个 | 0 个 |
| **新增方法数** | 5 个（流式相关） | 0 个 |
| **持久化处理** | 需要过滤临时消息 | 天然分离，无需处理 |
| **职责清晰度** | 混合（消息 + 流式 + UI 状态） | 清晰（持久化 vs 临时） |
| **后续维护** | 一个 Store 管所有 | 两个 Store 各司其职 |
| **风险等级** | 高（改动大，易出错） | 低（改动小，可控） |

---

## 五、方案 A 执行步骤（如果选择）

### 步骤 1：扩展 useChatStore（高风险）
- 新增 `runtimeStates: Map<string, ConversationRuntimeState>`
- 新增 5 个流式方法
- 修改 persist 配置，过滤临时消息
- 保持旧方法兼容

### 步骤 2：改造核心回调（高风险）
- 改造 useMessageCallbacks.tsx（最复杂）
- 逐个替换方法调用
- 充分测试流式、乐观更新场景

### 步骤 3：改造发送器
- chatSender.ts
- imageSender.ts
- videoSender.ts

### 步骤 4：改造组件和 Hook
- MessageArea.tsx
- Chat.tsx
- taskRestoration.ts
- useMessageAreaScroll.ts
- useStreamingScroll.ts

### 步骤 5：删除旧代码
- 删除 useConversationRuntimeStore.ts
- 删除 mergeOptimisticMessages.ts
- 清理未使用的导入

---

## 六、风险评估

### 方案 A 的主要风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 持久化过滤逻辑出错 | 中 | 高（刷新后显示无效消息） | 充分测试刷新场景 |
| 流式消息管理混乱 | 中 | 高（消息显示异常） | 保留独立流式方法 |
| useMessageCallbacks 改造出错 | 高 | 高（核心功能失效） | 逐步改造，每步测试 |
| 回滚困难 | 中 | 高 | 提前做好 Git 备份 |

### 方案 B 的主要风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 两个 Store 仍需协调 | 低 | 低 | useUnifiedMessages 封装 |
| 后续需要二次统一 | 可能 | 低 | 稳定后再考虑 |

---

## 七、结论

### 方案 A 适合场景
- 项目稳定期，有充足时间做彻底重构
- 团队熟悉所有模块，能快速定位问题
- 对「单一数据源」有强烈洁癖

### 方案 B 适合场景
- 开发阶段，需要快速迭代
- 优先解决核心问题（缓存不一致）
- 控制风险，避免大范围改动

### 最终建议

**开发阶段建议方案 B**，原因：
1. 方案 A 改动 12 个文件，方案 B 改动 7 个文件
2. 方案 A 需要处理持久化过滤，方案 B 天然分离
3. 方案 A 的 useMessageCallbacks 改造风险最高
4. 方案 B 同样解决了核心问题（组件状态 + 缓存同步）
5. 方案 B 稳定后，可以再评估是否需要进一步统一
