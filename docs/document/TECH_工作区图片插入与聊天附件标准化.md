# 工作区图片插入与聊天附件标准化技术设计

> 日期：2026-07-16
> 状态：开发完成，待人工验收
> 任务级别：A 级
> 范围：前端聊天附件提交边界；不修改后端消息协议、数据库或主图详情项目图片关联链路

## 1. 背景与问题

工作区图片通过右键“插入到聊天”或输入框 `@`提及加入草稿后，会被统一放入 `workspaceFiles`。`useInputSubmission.handleSubmit()` 将全部 `workspaceFiles` 构造为 `FilePart`，没有按资源类型分流。

直接影响：

1. 前端历史消息使用文件卡片而非图片媒体渲染。
2. 聊天后端将它识别为工作区文件，但不会稳定地把图片像素作为多模态 `image_url` 注入模型。
3. 图片生成、视频生成和电商图模式只消费 `uploadedImageUrls`，工作区图片可能在提交后被清空但未发送。
4. 输入区把所有工作区附件显示为通用文件条目，与本地上传图片的缩略图交互不一致。

## 2. 项目上下文

### 2.1 架构现状

- 前端使用 React + TypeScript + Zustand，所有消息最终通过 `sendMessage()` 提交 `ContentPart[]`。
- 前后端已共享 `TextPart` / `ImagePart` / `FilePart` 等类型化内容块语义，后端 `BaseHandler` 能正确提取携带 `workspace_path` 的 `ImagePart`。
- 聊天输入草稿由 `useImageUpload`、`useFileUpload`、`pendingWorkspaceFiles` 分别管理，在 `useInputSubmission` 才汇合。
- 工作区右键插入与 `@`提及共用 `Chat.handleSendFromWorkspace()` 及 `pendingWorkspaceFiles`，两者必须保持一致。
- 主图详情页通过 `DetailProjectService.attach_image()` 关联工作区图片，属于项目资产链路，不应与聊天草稿状态强行合并。

### 2.2 可复用模块

- `frontend/src/utils/fileCategory.ts#categorize`：扩展名优先、MIME 兜底的图片/视频/文档分类。
- `frontend/src/utils/imageUrlRules.ts`：原图、缩略图和候选 URL 选择规则。
- `frontend/src/services/messageSender.ts#ImageInputInfo`：已能表达原图 URL、文件名、`workspace_path`、MIME 和尺寸。
- `createTextWithImages()` / `createTextWithFiles()`：已能把图片与文件组合为现有 `ContentPart[]`。
- `useInputDraftTransaction`：已实现草稿立即移出、明确拒绝后合并恢复，不改变其事务语义。
- 后端 `BaseHandler._extract_image_urls()` 和 `_extract_workspace_files()`：已支持 `ImagePart + workspace_path`。

### 2.3 设计约束

- 不更改 `ContentPart` API 协议，不新增数据库字段。
- 原图、缩略图语义必须继续遵守 `imageUrlRules.ts`，缩略图不得作为模型输入。
- 工作区文件不重复上传，只转换已有 CDN URL 与元数据。
- 按 `workspace_path` 的去重和草稿恢复行为保持不变。
- `InputControls.tsx` 现有 600 行，已超过项目 500 行限制；禁止继续向该文件追加分类逻辑。
- 新增标准化逻辑必须是无副作用纯函数，便于单元测试。

### 2.4 潜在冲突

- `docs/document/TECH_工作区文件面板.md` 原设计明确将所有工作区资源转换为 `FilePart`，与当前多模态语义冲突。实施后需在该文档标注新设计已取代 5.1–5.4 的附件分类描述。
- `CURRENT_ISSUES` 中已存在草稿事务与幂等协议改造；本次不能破坏“明确拒绝恢复、结果未知不恢复”的语义。
- `InputControls.tsx` 的超长问题是既有代码异味；本任务只拆出本次需要触及的附件预览子组件，不顺手重构其他控件。

## 3. 现有数据流与根因

### 3.1 工作区右键与 `@`提及

```text
WorkspaceView.handleSendToChat / InputArea.handleMentionSelect
  -> Chat.handleSendFromWorkspace
  -> pendingWorkspaceFiles: WorkspaceFile[]
  -> InputArea.workspaceFiles
  -> useInputSubmission.handleSubmit
  -> workspaceFiles.map(...)
  -> mergedFiles
  -> createTextWithFiles
  -> FilePart
```

根因是“来源”被当成“媒体类型”：只要来自工作区，就被当作普通文件。

### 3.2 本地上传与 AI 图片引用

```text
useImageUpload.handleImageFiles / addQuotedImage
  -> uploadedImages: ImageInputInfo[]
  -> createTextWithImages / createTextWithFiles
  -> ImagePart
```

这是应被复用的正确语义。

### 3.3 后端连锁影响

```text
错误 FilePart(image/*, workspace_path)
  -> _extract_file_urls 暂时提取 URL
  -> _extract_workspace_files 识别工作区附件
  -> ChatContextMixin 从 file_urls 排除 workspace URL
  -> 图片不保证进入多模态 image_urls

正确 ImagePart(workspace_path)
  -> _extract_image_urls
  -> PromptBuilder.image_urls
  -> 模型多模态 image_url
  + _extract_workspace_files
  -> file_path_cache / attachments XML
```

正确类型同时保留“模型看到图片”与“Agent 知道工作区路径”两种能力。

## 4. 方案对比

| 维度 | 方案 A：提交处局部条件分流 | 方案 B：输入附件标准化边界（推荐） | 方案 C：统一 Zustand 附件 Store |
|---|---|---|---|
| 实现 | 在 `handleSubmit` 内判断 MIME | 独立纯函数将各来源转为标准图片/文件输入 | 重写上传、引用和工作区草稿状态 |
| 侵入性 | 低 | 中低 | 高 |
| 短期速度 | 最快 | 适中 | 慢 |
| 可维护性 | 低，继续堆积分支 | 高，单一分类边界 | 高，但超出当前需求 |
| 回归风险 | 中 | 低–中 | 高 |
| 后端变更 | 无 | 无 | 无 |

采用方案 B，但分两个实施提交：先完成类型修复与模式贯通，再收口输入区附件预览。不选择方案 C，因为它会与已稳定的草稿事务、幂等和上传恢复逻辑大面积交叉。

## 5. 详细设计

### 5.1 标准化输出

新增纯函数模块 `frontend/src/components/chat/input/attachmentNormalization.ts`，不管理 React 状态，不发起请求。

概念接口（非最终实现代码）：

```ts
interface NormalizedSubmissionAttachments {
  imageInputs: ImageInputInfo[];
  imageUrls: string[];
  files: SubmissionFileInput[];
  rejected: AttachmentRejection[];
}

normalizeSubmissionAttachments(input): NormalizedSubmissionAttachments
```

约束：

- `imageInputs` 供聊天模式构建完整 `ImagePart`。
- `imageUrls` 供图片/视频生成处理器，只包含有效原图 URL。
- `files` 只包含非图片附件以及明确的降级文件。
- `rejected` 承载不可静默忽略的原因，由提交层显示用户可理解错误。

### 5.2 工作区分类规则

1. 复用 `categorize({name, mime_type})`，扩展名白名单优先，MIME 兜底。
2. 分类为 `image` 且 `toOriginalImageUrl(cdn_url)` 非空时，转为 `ImageInputInfo`。
3. 分类为 `image` 但只有 `/workspace-thumbnails/` 缩略图时，禁止发送并返回明确错误。
4. 分类为 `image` 但 `cdn_url` 缺失时：
   - 聊天模式可保留为 `FilePart(workspace_path)`，使 Agent 仍能通过工作区工具处理。
   - 图生图/图生视频/电商图模式必须拒绝并提示“图片缺少可用原图地址”，不得静默丢弃。
5. 非图片按现有 `FilePart` 处理。
6. 去重继续以 `workspace_path` 为主；不对本地上传与工作区同 URL 进行跨来源自动删除，避免改变用户显式选择。

### 5.3 生成模式贯通

`useInputSubmission` 在草稿移出前获取附件快照并完成标准化：

- `chat`：向 `handleChatMessage` 传入合并后的 `imageInputs` 和 `files`。
- `image`：向 `handleImageGeneration` 传入合并后的 `imageUrls`。
- `video`：向 `handleVideoGeneration` 传入合并后的 `imageUrls`。
- `image_ecom`：与图片模式相同，传入合并图片 URL，保留现有 `generation_type_override`。

标准化拒绝必须发生在 `clearPromptForSubmission()` 和各 `detach*ForSubmission()` 之前，保证本地校验失败不会隐藏草稿。

### 5.4 输入区预览

新建 `WorkspaceAttachmentPreview.tsx` 承担工作区附件展示：

- 有效工作区图片：显示缩略图，移除按钮使用 `workspace_path`。
- 普通文件：保持现有图标、文件名和移除交互。
- 无缩略图时，图片可使用原图作为小图兜底，但不修改发送 URL。
- `InputControls.tsx` 只引用子组件，不承载分类规则。

## 6. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|---|---|---|
| 空附件列表 | 返回空数组，保持纯文本发送 | 标准化模块 |
| MIME 缺失 | 使用扩展名白名单分类 | `fileCategory` |
| MIME 与扩展名冲突 | 沿用现有扩展名优先规则，用测试锁定 | `fileCategory` |
| 图片缺少 CDN URL | 聊天可降级为文件；媒体生成模式拒绝 | `useInputSubmission` |
| 只有缩略图 URL | 拒绝，不把缩略图作为模型输入 | `imageUrlRules` |
| 图片 + PDF 混合 | 分别生成 `ImagePart` 与 `FilePart`，顺序保持类型内稳定 | 标准化模块 |
| 本地图片 + 工作区图片 | 合并为同一图片输入集 | `useInputSubmission` |
| 快速重复右键/提及 | 沿用 `workspace_path` 去重 | `Chat.tsx` |
| 快速切换生成模式 | 提交时以当前 `effectiveModelType` 及快照决定 | `useInputSubmission` |
| 请求明确拒绝 | 使用现有 restore 函数合并恢复所有来源附件 | 草稿事务 |
| 网络结果未知 | 不恢复、不重复发送，保持现有幂等策略 | 草稿事务/发送器 |
| token 过期/权限失败 | 由现有 API 错误分类为明确拒绝并恢复 | API/草稿事务 |
| 大量附件 | 沿用模型和现有上传数量限制；标准化为 O(n) | 提交层 |
| 历史错误 `FilePart(image/*)` | 不进行数据迁移，新消息使用正确类型 | 历史消息 |

## 7. 连锁修改清单

| 改动点 | 影响文件 | 必须同步内容 |
|---|---|---|
| 新增附件标准化纯函数 | `attachmentNormalization.ts` | 定义结果、分类、原图校验和拒绝原因 |
| 提交前标准化 | `useInputSubmission.ts` | 聊天/图片/视频/电商图均消费合并图片 |
| 媒体处理器元数据 | `useMediaMessageHandler.ts` 及相关类型 | 如只需 URL 则保持签名；若需历史消息保留工作区元数据，改为接受 `ImageInputInfo[]` 并更新所有调用方 |
| 输入区图片预览 | `WorkspaceAttachmentPreview.tsx`, `InputControls.tsx` | 从超长组件拆出工作区附件区 |
| 输入 Props 类型重复 | `InputArea.tsx`, `InputControls.tsx`, `useInputDraftTransaction.ts` | 复用 `WorkspaceFile` 类型，不改变状态结构 |
| 工作区原设计过期 | `TECH_工作区文件面板.md` | 标记附件类型设计已被本文档取代 |
| 新增函数与文件 | `FUNCTION_INDEX.md`, `PROJECT_OVERVIEW.md` | 更新索引与目录 |
| 问题状态 | `CURRENT_ISSUES.md` | 从待修复更新为已完成并附验证证据 |

## 8. 架构影响评估

| 维度 | 评估 | 风险 | 应对措施 |
|---|---|---|---|
| 模块边界 | 在聊天输入子目录增加纯函数边界，不跨后端 | 低 | 不建立全局 Store |
| 数据流 | 所有来源在提交前汇合，最终仍是 `ContentPart[]` | 低 | 标准化函数无副作用 |
| 扩展性 | O(n) 分类，附件规模 10x 不会引入网络或存储开销 | 低 | 不重复上传 |
| 耦合度 | 复用 `fileCategory`、`imageUrlRules`、`ImageInputInfo` | 中 | 通过输入/输出类型控制依赖方向 |
| 一致性 | 统一工作区、上传、引用图片的消息语义 | 低 | 以 `ImagePart` 为唯一图片消息语义 |
| 可观测性 | 本地拒绝原因可通过现有 logger/toast 暴露 | 低 | 日志只记录类型、模式和 `workspace_path`，不记录 token |
| 可回滚性 | 无 API/数据库变更，可通过回滚前端提交恢复 | 低 | 分两个提交，避免与无关改动混合 |

无高风险架构问题，可进入实施。

## 9. 文件结构

### 9.1 计划新增

- `frontend/src/components/chat/input/attachmentNormalization.ts`：聊天提交前附件标准化纯函数。放在 `input/` 是因为它只服务输入草稿到消息的转换边界。
- `frontend/src/components/chat/input/WorkspaceAttachmentPreview.tsx`：工作区图片/文件预览子组件，用于缩减 `InputControls.tsx`。
- 对应单元测试，放入 `frontend/src/components/chat/input/__tests__/`。

### 9.2 计划修改

- `useInputSubmission.ts`：调用标准化函数，贯通四种生成模式。
- `InputControls.tsx`：仅替换工作区附件内联 JSX 为子组件。
- `InputArea.tsx` / `useInputDraftTransaction.ts`：只在必要时收口重复类型引用，不改行为。
- 相关测试与文档。

## 10. API、数据库、依赖

- API：无变更，继续提交 `ContentPart[]`。
- 数据库：无变更，无迁移。
- 后端：无计划代码变更。
- 依赖：无新增依赖。
- 技术栈：沿用 React + TypeScript + Zustand + TailwindCSS。

## 11. 开发任务拆分

### 阶段 1：类型修复与发送贯通

1. 先补充失败测试，证明工作区图片当前被传为文件且媒体模式未收到它。
2. 实现附件标准化纯函数及边界测试。
3. 接入 `useInputSubmission`，保持草稿事务时序不变。
4. 验证聊天、图生图、图生视频、电商图调用参数。

### 阶段 2：输入预览收口

1. 新增 `WorkspaceAttachmentPreview`。
2. 将 `InputControls.tsx` 内的工作区附件 JSX 精准移入子组件。
3. 补充图片缩略图、文件卡片、移除行为测试。

### 阶段 3：回归与文档

1. 执行 `everydayai-test-coverage` 扫描变更覆盖。
2. 运行相关单测、前端全量测试、TypeScript 检查和生产构建。
3. 检查文件/函数长度、复杂度、嵌套和重复代码。
4. 用 `rg` 确认所有 `workspaceFiles` 和处理器签名调用方一致。
5. 更新 `FUNCTION_INDEX`、`PROJECT_OVERVIEW`、`CURRENT_ISSUES` 及过期工作区设计说明。

## 12. 验收标准

1. 工作区右键插入图片后，输入区显示图片预览，发送后消息渲染为图片。
2. `@`提及同一图片与右键插入行为完全一致。
3. 发送的图片内容块为 `type: "image"`，且保留 `name`、`workspace_path`、`mime_type`、`size` 与原图 URL。
4. 后端既能把图片注入多模态模型，又能在会话文件缓存中注册 `workspace_path`。
5. 图片、视频和电商图模式均收到工作区原图 URL，不得静默丢弃。
6. 图片与非图片附件混合发送后类型正确。
7. 明确拒绝后文本和附件可恢复；结果未知时不会重复恢复或发送。
8. 相关测试、前端全量测试、类型检查与生产构建全部通过。
9. 新增/修改文件符合 500/120/15/4 质量阈值；`InputControls.tsx` 行数下降到 500 以内。

## 13. 部署与回滚

- 部署：仅前端资源变更，无数据库前置步骤。
- API 兼容：完全向后兼容，后端早已支持 `ImagePart + workspace_path`。
- 回滚：回滚本次前端提交即可；无数迁移或清理。
- 历史数据：不回填旧 `FilePart(image/*)`，避免修改旧消息语义。

## 14. 风险

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| 引入新标准化后破坏草稿恢复时序 | 高 | 标准化先于 detach；保留现有 restore 函数和专项测试 |
| 图片模式数量限制未计入工作区图片 | 中 | 提交前使用合并后图片数执行模型限制校验 |
| HEIC/SVG 等工作区可预览格式未必被模型支持 | 中 | 提交类型白名单不盲目等同工作区预览白名单；实施前对齐模型能力 |
| 输入区拆分引入样式回归 | 中 | 保留原 JSX 结构与 className，添加组件测试和视觉检查 |
| 只验证乐观消息，未验证刷新后持久化 | 中 | 同时验证 API 请求 content 和历史消息 normalize/render |

## 15. 设计自检

- [x] 需求范围已确认。
- [x] A 级方案评审已完成，采用“渐进式标准化”共识。
- [x] 架构现状、可复用模块、设计约束、潜在冲突已完整记录。
- [x] 连锁修改已纳入任务拆分。
- [x] 无 API、数据库和外部依赖变更。
- [x] 边界、回滚和验收标准已明确。
- [x] 实施前已由用户再次确认本技术设计。
