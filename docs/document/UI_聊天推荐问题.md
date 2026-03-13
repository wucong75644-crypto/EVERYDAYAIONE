# UI 设计：聊天下一步推荐问题

> **状态**：待开发 | **日期**：2026-03-13

## 需求概述

AI 回复完成后，在最新一条 AI 消息下方显示 2-3 个推荐问题，用户点击即可直接发送，降低对话门槛。

**技术方案**：方案 A（Prompt 内嵌），LLM 同时返回正文 + suggestions JSON，零额外调用。

---

## 0. 现有 UI 分析

- **可复用组件**：无需新建复杂组件，推荐 pills 是简单的 button 列表
- **样式约束**：
  - AI 消息气泡：`bg-white border border-gray-200 rounded-2xl`
  - 按钮悬停色：`hover:bg-gray-100`，主题渐变色 `purple-500 → indigo-500`
  - 间距惯例：`gap-2`、`mb-4`、`px-5 py-3`
  - 动画惯例：`transition-colors`、`transition-opacity duration-300`
- **交互惯例**：
  - 工具栏：悬停显示/隐藏 + opacity 过渡
  - 发送：`InputArea.handleSubmit()` → `sendMessage()`
  - 消息状态：streaming → completed 后才显示操作栏
- **消息发送入口**：`InputArea` 组件的 `handleSubmit()`，或直接调用 `useTextMessageHandler.handleChatMessage()`

---

## 1. 组件位置与布局

推荐组件嵌入在 **MessageItem.tsx** 的 AI 消息结构中，位于 **MessageActions（工具栏）下方**：

```
┌──────────────────────────────────────┐
│  AI 消息气泡（Markdown 正文）          │
│  ┌──────────────────────────────────┐ │
│  │ ThinkingBlock（如有）            │ │
│  │ MarkdownRenderer（正文）         │ │
│  └──────────────────────────────────┘ │
│  MessageMedia（图片/视频，如有）        │
│  MessageActions（复制/点赞/重新生成）   │
│                                       │
│  ┌──────────────────────────────┐     │  ← 新增：SuggestionChips（纵向排列）
│  │ 💬 推荐问题 1                  │     │
│  └──────────────────────────────┘     │
│  ┌──────────────────────────────┐     │
│  │ 💬 推荐问题 2                  │     │
│  └──────────────────────────────┘     │
│  ┌──────────────────────────────┐     │
│  │ 💬 推荐问题 3                  │     │
│  └──────────────────────────────┘     │
└──────────────────────────────────────┘
```

---

## 2. 交互流程

### 流程：推荐问题展示与点击

1. AI 回复流式输出完成 → `message_done` 事件触发
2. 后端在 `message_done` payload 的 message 对象中携带 `suggestions: string[]`
3. 前端 `wsMessageHandlers` 将 suggestions 存入 Store（`Map<conversationId, string[]>`）
4. **仅最后一条 AI 消息**的 MessageItem 渲染 `SuggestionChips`
5. pills 以 **300ms 延迟、逐个交错淡入** 出现（每个间隔 100ms）
6. 用户点击某个 pill → 将文本直接**自动发送**
7. 发送后 suggestions 自动清除

### 流程：推荐隐藏

- 用户在输入框**开始输入**时 → suggestions 淡出隐藏
- 用户**发送新消息**后 → suggestions 从 Store 清除
- 对话切换 → 旧推荐自然清除（跟随 conversationId）

---

## 3. 状态设计

| 状态 | 触发条件 | 显示内容 | 行为 |
|------|---------|---------|------|
| 无推荐 | LLM 未返回 / 图片视频类型 / 解析失败 | 不显示任何内容 | 静默 |
| 推荐加载中 | message_done 刚收到 | 不显示（300ms 延迟内） | 等待 |
| 推荐显示 | 300ms 后 & 用户未输入 | 2-3 个纵向排列的 pill 按钮 | 可点击 |
| 推荐隐藏 | 用户开始输入 | 淡出隐藏 | opacity→0 |
| 推荐消失 | 用户发送新消息 | 从 DOM 移除 | Store 清空 |

---

## 4. Pill 按钮样式（纵向排列）

```
单个 pill:
- 背景: bg-gray-50  悬停: hover:bg-purple-50
- 边框: border border-gray-200  悬停: hover:border-purple-300
- 圆角: rounded-xl
- 文字: text-sm text-gray-600  悬停: hover:text-purple-700
- 内边距: px-4 py-2.5
- 宽度: w-full（撑满容器宽度）
- 文本对齐: text-left
- 过渡: transition-all duration-200
- 光标: cursor-pointer

容器:
- 布局: flex flex-col gap-2（纵向排列，间距 8px）
- 上间距: mt-3
- 最大宽度: max-w-[85%]（不超过消息气泡宽度）
- 进入动画: 每个 pill 交错淡入
  - 第 1 个: delay 300ms
  - 第 2 个: delay 400ms
  - 第 3 个: delay 500ms
  - 动画: opacity 0→1 + translateY 8px→0, duration 300ms, ease-out
```

---

## 5. 组件清单

| 组件名 | 功能 | 复用/新建 |
|--------|------|----------|
| **SuggestionChips** | 渲染 2-3 个纵向排列推荐 pill 按钮，支持淡入/淡出动画 | **新建** |
| MessageItem | AI 消息底部（MessageActions 下方）加入 SuggestionChips | **修改** |
| MessageArea | 传入 suggestions 和 isLastAiMessage 标识给 MessageItem | **修改** |
| streamingSlice | 增加 `suggestions: Map<string, string[]>` 和 set/clear 操作 | **修改** |
| wsMessageHandlers | `message_done` 中提取 suggestions 写入 Store | **修改** |
| InputArea | 发送时清除 suggestions；输入时通知隐藏 | **修改** |

---

## 6. 数据流总览

```
后端 LLM 返回（正文 + suggestions JSON）
  ↓ 后端解析分离
message_done WebSocket payload: { message: {...}, suggestions: ["问题1", "问题2", "问题3"] }
  ↓ wsMessageHandlers.handleMessageDone()
Store: suggestions Map<conversationId, string[]>
  ↓ MessageArea 读取
MessageItem(isLastAiMessage=true) → <SuggestionChips suggestions={[...]} />
  ↓ 用户点击
onSuggestionClick(text) → InputArea.handleSubmit(text) → sendMessage()
  ↓ 发送后
Store: suggestions.delete(conversationId)
```

---

## 7. 边界约束

- **不持久化**：刷新页面后推荐消失，不存数据库
- **仅 CHAT 类型**：图片/视频生成完成后不带推荐
- **解析失败静默**：LLM 未返回或 JSON 解析失败时不显示，不影响正常消息
- **暂不做**：设置开关、推荐个数自定义、推荐历史记录

---

## 8. 大厂调研参考

| 产品 | UI 形式 | 数量 | 点击行为 |
|------|---------|------|----------|
| ChatGPT | 椭圆 chips（横向） | 2-4 | 直接发送 |
| Claude Web | 灰色预填文字（输入框内） | 1 | Tab 编辑 / Enter 发送 |
| 豆包/Kimi | 气泡按钮（纵向） | 2-3 | 直接发送 |

我们采用**纵向排列 + 点击直接发送**，与豆包/Kimi 风格一致。
