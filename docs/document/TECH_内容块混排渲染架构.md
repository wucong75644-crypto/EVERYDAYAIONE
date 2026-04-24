# 技术设计：内容块混排渲染架构

> **版本**：v1.0 | **日期**：2026-04-24 | **等级**：A级 | **状态**：方案确认

## 背景与目标

### 问题

沙盒（code_execute）生成的图表/文件，走 `_pending_file_parts` 追加到消息 content 末尾。LLM 不知道这些文件会被前端自动展示，在文字里重复描述了一遍（数据表格 + 文件链接），导致用户看到**同一内容显示两次**。

### 目标

对齐 Claude / ChatGPT 的 content block 混排架构：图片/文件作为独立块嵌入 content 数组，和文字块交替排列。LLM 只看到占位标记（不含 URL），文字只写结论；前端按 type 逐块顺序渲染。

### 对标

| | Claude | ChatGPT | 我们（改造后） |
|--|--------|---------|--------------|
| 图表存储 | base64 内联 | file_id 引用 | CDN URL |
| content 结构 | text + image 混排 | text + image_file 混排 | text + image + file 混排 |
| LLM 可见 | 知道图片位置 | annotations 引用 | 占位标记（不含 URL） |
| 前端渲染 | 按块顺序 | 按块顺序 | 按块顺序 |

---

## 1. 项目上下文

- **架构现状**：消息 content 是 `ContentPart[]` 数组（TextPart/ImagePart/FilePart 等）。沙盒生成的文件通过 `[FILE]` 标记提取后暂存到 `_pending_file_parts`，在 LLM 完成后追加到 content 末尾。LLM 上下文看到 `📎 文件已生成: name`（不含 URL）。
- **可复用模块**：`_content_blocks` 已按轮次追踪 text 块；`[FILE]` 提取正则已稳定；前端已有多块渲染入口（`content.map()`）
- **设计约束**：LLM 上下文不能暴露真实 URL（防幻觉篡改）；前端按 `type` 字段分流渲染
- **潜在冲突**：无

---

## 2. 核心数据流改造

### 改造前（当前）

```
LLM turn 1: "让我分析数据" + tool_call(code_execute)
  → _content_blocks = [text("让我分析数据")]
  → tool 执行 → 生成 chart.png → [FILE] 标记
  → _extract_file_parts() → _pending_file_parts 暂存
  → LLM 看到 "📎 文件已生成: chart.png"

LLM turn 2: "拼多多占比76%...数据表格...chart.png..."  ← 重复描述
  → 循环结束
  → _pending_file_parts 追加到 content 末尾

content = [TextPart("让我分析...拼多多占比76%..."), FilePart(chart.png)]
                     ↑ 文字重复了图表数据              ↑ 末尾追加
```

### 改造后（目标）

```
LLM turn 1: "让我分析数据" + tool_call(code_execute)
  → _content_blocks = [text("让我分析数据")]
  → tool 执行 → 生成 chart.png → [FILE] 标记
  → _extract_file_parts() → _pending_file_parts
  → 插入 _content_blocks = [text("让我分析数据"), image(chart.png)]
  → LLM 看到 "📊 图表已生成（将自动展示给用户，不要在文字中重复描述图表数据）"

LLM turn 2: "从图表可以看出拼多多占比最大"  ← 只写结论
  → _content_blocks = [text("让我分析数据"), image(chart.png), text("从图表可以看出...")]

content = [TextPart, ImagePart, TextPart]  ← 混排，无重复
```

---

## 3. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| 工具无文件输出 | `_pending_file_parts` 为空，不插入块，行为不变 | chat_handler |
| 一次工具生成多文件 | 按顺序全部插入 `_content_blocks` | chat_handler |
| LLM 仍在文字里引用文件名 | 不致命，只是多几个字；占位文本已明确提示"不要重复" | chat_tool_mixin |
| 单块模式（无工具调用） | 无 `[FILE]` 标记，不走此逻辑，行为不变 | chat_handler |
| SVG 文件（image/svg+xml） | `mime.startswith("image/")` 匹配，走 ImagePart；`<img>` 可渲染 SVG | chat_tool_mixin |
| 流式输出阶段 | content 数组未完成，显示流式文字；完成后切换为块渲染 | MessageItem |
| AI 图片生成（generate_image） | 走 image_handler 独立路径，不经过 `_content_blocks`，不受影响 | image_handler |
| 多轮上下文膨胀 | image/file 块在上下文压缩时可省略 | context_compressor |
| ERPAgent 子循环生成文件 | collected_files 透传到 `_pending_file_parts`，同样走插入逻辑 | chat_tool_mixin |

---

## 4. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 占位文本按 mime 区分 | chat_tool_mixin.py `_extract_file_parts()` | tool_loop_executor.py 同步改占位文本 |
| `_pending_file_parts` 插入 `_content_blocks` | chat_handler.py L581 后 | 删除 L732-753 末尾追加 + markdown 后处理 |
| 结果构建增加 image/file 块处理 | chat_handler.py L718-726 | 新增 elif 分支 |
| AgentResult.collected_files 同样走插入 | chat_tool_mixin.py L116-123 | 已通过 _pending_file_parts 统一处理 |
| 前端统一 content 顺序渲染 | MessageItem.tsx | 删除 hasMultiBlocks/单块分流 |
| MessageMedia 收窄为仅媒体生成消息 | MessageItem.tsx | isMediaMessage 判断 |

---

## 5. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | 不新增模块，改造 chat_handler + chat_tool_mixin + MessageItem | 低 | — |
| 数据流向 | 文件从"末尾追加"变为"嵌入 content 流"，方向一致 | 低 | — |
| 扩展性 | content 数组长度增加（多了 image/file 块），10x 无压力 | 低 | — |
| 耦合度 | 前后端通过 ContentPart type 字段解耦，无新耦合 | 低 | — |
| 一致性 | 与 Claude/ChatGPT 的 content block 混排模式对齐 | 低 | — |
| 可观测性 | 现有日志已覆盖 | 低 | — |
| 可回滚性 | 无数据库变更，git revert 即可 | 低 | — |

---

## 6. 后端改造详细设计

### 6.1 占位文本按 mime 类型区分

**文件**：`chat_tool_mixin.py` `_extract_file_parts()`、`tool_loop_executor.py` L633

```python
# 图片文件
return "📊 图表已生成（将自动展示给用户，不要在文字中重复描述图表数据）"

# 非图片文件
return f"📎 文件已生成: {name}（下载卡片将自动展示，不要重复引用文件名）"
```

### 6.2 文件块插入 `_content_blocks`

**文件**：`chat_handler.py`，在 `_execute_tool_calls()` 返回后（约 L581）、ask_user 检测前（约 L605）

```python
# 工具执行后，将生成的文件插入 content 块流（而非末尾追加）
if self._pending_file_parts:
    for part in self._pending_file_parts:
        if part.mime_type.startswith("image/"):
            _content_blocks.append({
                "type": "image", "url": part.url, "alt": part.name,
            })
        else:
            _content_blocks.append({
                "type": "file", "url": part.url, "name": part.name,
                "mime_type": part.mime_type, "size": part.size,
            })
    self._pending_file_parts.clear()
```

### 6.3 结果构建增加 image/file 块处理

**文件**：`chat_handler.py` L718-726

```python
for block in _content_blocks:
    if block["type"] == "text":
        result_parts.extend(extract_media_parts(block["text"]))
    elif block["type"] == "tool_result":
        result_parts.append(ToolResultPart(
            tool_name=block["tool_name"],
            text=block["text"],
            files=block.get("files", []),
        ))
    elif block["type"] == "image":
        result_parts.append(ImagePart(url=block["url"], alt=block.get("alt")))
    elif block["type"] == "file":
        result_parts.append(FilePart(
            url=block["url"], name=block["name"],
            mime_type=block["mime_type"], size=block.get("size"),
        ))
```

### 6.4 删除末尾追加 + markdown 链接后处理

**文件**：`chat_handler.py` L732-753

删除整段：
- `_file_url_map` 构建
- markdown 链接替换
- `result_parts.extend(self._pending_file_parts)`

文件已在 6.2 步插入 `_content_blocks`，不需要末尾追加。

---

## 7. 前端改造详细设计

### 7.1 统一两通道渲染

**文件**：`MessageItem.tsx`

| 通道 | 触发条件 | 处理内容 |
|------|---------|---------|
| **content 顺序渲染** | 聊天消息（`!isMediaMessage`） | text / tool_result / image / file 按序渲染 |
| **MessageMedia** | 媒体生成消息（`isMediaMessage`） | AI 图片/视频的占位符、grid、重试 |

```tsx
const isMediaMessage = !!message.generation_params?.type;
```

### 7.2 AI 消息渲染逻辑

```tsx
{/* AI 聊天消息：统一 content 顺序渲染 */}
{!isUser && !isMediaMessage && (
  isStreaming ? (
    // 流式阶段：显示累积文字（content 数组未完成）
    <MarkdownRenderer content={textContent} isStreaming />
  ) : Array.isArray(message.content) ? (
    // 完成后：按 content 数组逐块渲染
    message.content.map((part, idx) => {
      if (part.type === 'text')        return <MarkdownRenderer key={idx} content={part.text} />
      if (part.type === 'tool_result') return <ToolResultBlock key={idx} ... />
      if (part.type === 'image')       return <InlineImage key={idx} url={part.url} alt={part.alt} />
      if (part.type === 'file')        return <FileCardItem key={idx} file={part} />
      return null
    })
  ) : (
    <MarkdownRenderer content={textContent} />
  )
)}

{/* AI 媒体生成消息：保留 MessageMedia 全部功能 */}
{!isUser && isMediaMessage && (
  <MessageMedia
    imageUrls={imageUrls}
    videoUrls={videoUrls}
    isGenerating={!!mediaPlaceholderInfo}
    generatingType={mediaPlaceholderInfo?.type}
    imageAspectRatio={actualImageAspectRatio}
    numImages={numImages}
    failedMediaType={failedMediaType}
    onRegenerate={onRegenerate}
    ...
  />
)}

{/* 用户消息：保持现有渲染不变 */}
```

### 7.3 删除冗余逻辑

- 删除 `hasMultiBlocks` 判断和单块/多块分流
- 聊天消息不再调用 `getImageUrls()` / `getFiles()`（仅 isMediaMessage 时用）
- MessageMedia 不再用于聊天消息

---

## 8. 开发任务拆分

### Phase 1：后端 — 文件块嵌入 content 流

- [ ] 1.1 `chat_tool_mixin.py`：`_extract_file_parts()` 占位文本按 mime 区分
- [ ] 1.2 `tool_loop_executor.py`：占位文本同步修改
- [ ] 1.3 `chat_handler.py`：工具执行后 `_pending_file_parts` 插入 `_content_blocks`
- [ ] 1.4 `chat_handler.py`：结果构建增加 image/file 块处理
- [ ] 1.5 `chat_handler.py`：删除末尾追加 + markdown 链接后处理
- [ ] 1.6 更新后端测试

### Phase 2：前端 — 统一 content 顺序渲染

- [ ] 2.1 `MessageItem.tsx`：新增 `isMediaMessage` 判断
- [ ] 2.2 `MessageItem.tsx`：聊天消息统一 content.map 渲染（含 image/file 块）
- [ ] 2.3 `MessageItem.tsx`：删除 hasMultiBlocks 分流，MessageMedia 收窄为 isMediaMessage
- [ ] 2.4 处理流式阶段 vs 完成阶段切换
- [ ] 2.5 内联图片组件样式调整（点击放大、下载按钮）
- [ ] 2.6 更新前端测试

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| LLM 偶尔仍重复描述 | 低 | 占位文本明确提示"不要重复"，不致命 |
| 前端混排样式适配 | 中 | Phase 2.5 专项调整 |
| 旧消息兼容 | 低 | 旧消息 content 末尾的 FilePart 仍能通过 content.map 正常渲染 |
| 流式→完成的视觉跳变 | 低 | 和当前行为一致（文件都是 message_done 后才出现） |

---

## 10. 文档更新清单

- [ ] FUNCTION_INDEX.md（chat_handler 函数变更）
- [ ] TECH_ARCHITECTURE.md（content block 混排说明）

---

## 11. 设计自检

- [x] 项目上下文已加载，4 点完整
- [x] 连锁修改已全部纳入任务拆分
- [x] 边界场景均有处理策略
- [x] 架构影响评估无高风险项
- [x] 无新增文件
- [x] 无新增依赖
- [x] 无数据库变更
- [x] 前端渲染通道从 3 个精简为 2 个
