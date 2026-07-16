# 聊天附件统一架构技术设计

> 日期：2026-07-16
> 状态：实施完成，待人工验收
> 任务级别：A 级

## 1. 目标与边界

统一聊天草稿中的图片和文件附件，从添加、引用、工作区插入、`@` 提及、预览、删除、校验、草稿事务到提交转换只暴露一套领域接口。

本次包含：

- 本地文件选择、拖拽和粘贴。
- 消息图片右键引用。
- 工作区右键“插入到聊天”。
- 工作区文件 `@` 提及。
- 图片和普通文件预览、删除与状态展示。
- 模型能力派生、提交前校验、发送快照及失败恢复。
- 聊天、图片、视频、电商图模式使用同一附件快照。

本次不包含：

- 已发送消息的 `ContentPart` 持久化协议。
- 历史消息重新生成。
- 主图详情页的工作区选择器。
- 音频录制和文本引用。
- 后端 API、数据库或模型适配器变更。

## 2. 架构现状

当前附件状态分布在 `useImageUpload`、`useFileUpload` 和 `Chat.pendingWorkspaceFiles`。本地上传、图片引用、工作区插入和 `@` 提及分别调用不同写入方法，预览层使用三个组件，发送时再由 `attachmentNormalization` 临时合并。草稿移出和失败恢复也分成图片、文件、工作区三套事务。

后端 `ContentPart` 已能正确区分 `ImagePart` 与 `FilePart`，用户图片会转换为视觉模型的标准 `image_url` 块，因此统一范围应停留在前端草稿附件域。

## 3. 可复用模块

- `useImageUpload`：本地图片上传、引用图片元数据、ObjectURL 清理。
- `useFileUpload`：普通文件上传、格式和大小校验。
- `imageUrlRules`：缩略图、原图和下载地址选择规则。
- `fileCategory`：MIME 与扩展名分类。
- `PreviewHost`：图片原图预览。
- 原 `attachmentNormalization` 的工作区图片/文件语义已迁入统一附件快照构建器并删除旧入口。
- `messageSender`：最终 `ContentPart` 构建与发送，不改变外部协议。

## 4. 设计约束

1. `ChatAttachment` 是草稿领域模型，`ContentPart` 是消息协议模型，二者不能混用。
2. 所有附件写入必须经过统一命令，组件不得直接修改来源状态。
3. 本地上传的异步生命周期与 ObjectURL 清理由来源适配器负责。
4. 工作区附件继续保留 `workspace_path`，图片提交为 `ImagePart`。
5. 发送失败恢复不能覆盖等待期间新加入的附件。
6. `InputArea.tsx` 当前 500 行，不允许继续增长。
7. 不新增依赖、全局 Store、API 或数据库字段。

## 5. 统一领域模型

```ts
type ChatAttachmentSource = 'upload' | 'quote' | 'workspace';
type ChatAttachmentStatus = 'uploading' | 'ready' | 'error';

type ChatAttachment = ChatImageAttachment | ChatFileAttachment;

interface ChatImageAttachment {
  id: string;
  kind: 'image';
  source: ChatAttachmentSource;
  status: ChatAttachmentStatus;
  name: string;
  previewUrl: string;
  originalUrl: string | null;
  thumbnailUrl?: string;
  workspacePath?: string;
  mimeType?: string;
  size?: number;
  error?: string;
  sourceId: string;
}

interface ChatFileAttachment {
  id: string;
  kind: 'file';
  source: 'upload' | 'workspace';
  status: ChatAttachmentStatus;
  name: string;
  url: string | null;
  workspacePath?: string;
  mimeType: string;
  size: number;
  error?: string;
  sourceId: string;
}
```

`id` 是跨来源稳定标识；`sourceId` 保存上传 Hook ID 或工作区路径，供内部适配器删除和恢复。

## 6. 统一命令门面

`useChatAttachments` 组合现有上传 Hook 与受控工作区状态，对外只提供：

```ts
interface ChatAttachmentController {
  attachments: ChatAttachment[];
  addLocalFiles(files: File[], constraints: AttachmentConstraints): Promise<void>;
  addQuotedImage(input: QuotedImageInput): void;
  addWorkspaceFile(file: WorkspaceFile): void;
  removeAttachment(id: string): void;
  clearImages(): void;
  isUploading: boolean;
  hasImages: boolean;
  hasQuotedImage: boolean;
  hasFiles: boolean;
  readyImageCount: number;
  submissionSnapshot: AttachmentSubmissionSnapshot;
  detachForSubmission(): AttachmentDraftTransaction;
}
```

第一阶段仍复用 `useImageUpload` 和 `useFileUpload` 的上传实现；它们成为门面内部适配器，不再被 `InputArea`、预览或提交层直接消费。

## 7. 数据流

```text
文件选择/拖拽/粘贴 ─┐
消息图片右键引用     ├─> ChatAttachmentController ─> ChatAttachment[]
工作区右键插入       ┤                │
工作区 @ 提及        ┘                ├─> ChatAttachmentPreview
                                      ├─> 模型能力派生
                                      ├─> 统一草稿事务
                                      └─> AttachmentSubmissionSnapshot
                                                     │
                                                     └─> ContentPart[]
```

## 8. 状态位置与通信

工作区右键插入发生在 `WorkspaceView`，图片引用发生在消息树，二者都位于 `InputArea` 外部。统一控制器放在 Chat 页面范围的 `ChatAttachmentProvider`，避免 `window` 全局附件事件和多层回调。

Provider 生命周期与 Chat 页面一致：

- 切换工作区视图不丢草稿附件。
- 不同 Chat 页面实例互不共享附件。
- 卸载时由上传适配器清理 ObjectURL。

图片引用移除 `chat:quote-image` 全局事件，改为调用 Context 命令。文本引用事件不属于本次范围。

## 9. 预览规则

- 所有图片统一为方形缩略图、统一删除按钮和原图预览。
- 引用图片显示“引用”角标。
- 本地上传图片可保留序号、上传中和错误状态。
- 工作区图片只显示缩略图，不显示文件名和序号。
- 普通文件统一显示文件名、大小、状态和删除按钮。
- 预览组件只接受 `ChatAttachment[]` 和 `removeAttachment(id)`。

## 10. 提交快照

```ts
interface AttachmentSubmissionSnapshot {
  attachments: ChatAttachment[];
  imageInputs: ImageInputInfo[];
  imageUrls: string[];
  files: SubmissionFileInput[];
  invalidImages: ChatImageAttachment[];
}
```

`useInputSubmission` 不再接收 `uploadedImageUrls`、`uploadedImages`、`uploadedFileUrls`、`workspaceFiles` 和三个 detach 函数，只接收快照构建和统一事务方法。

`messageSender.createTextWithImages` 与 `createTextWithFiles` 中重复的图片字段映射收口到一个内部函数，外部签名保持兼容。

## 11. 边界场景

| 场景 | 处理策略 |
|---|---|
| 空文件数组 | 命令直接返回，不更新状态 |
| MIME 缺失 | 使用扩展名分类 |
| 相同引用 URL | 按原图 URL 去重 |
| 相同工作区文件 | 按 `workspace_path` 去重 |
| 图片数量超限 | 添加前按现有模型限制校验；提交前再次校验合并数量 |
| 文件过大或格式不支持 | 继续复用上传 Hook 校验和错误提示 |
| 工作区图片无原图 URL | 保留错误态；媒体模式阻止发送 |
| 上传进行中 | 发送按钮禁用，删除遵循现有上传行为 |
| 发送明确拒绝 | 按附件 ID 合并恢复快照 |
| 网络结果未知 | 不恢复，保持现有幂等语义 |
| 等待期间新增附件 | restore 只补回快照中缺失 ID |
| Provider 卸载 | 清理监听器、计时器和 ObjectURL |

## 12. 连锁修改清单

| 改动点 | 影响文件 | 同步内容 |
|---|---|---|
| 统一附件类型与适配器 | `chat/attachments/*` | 新增领域类型、转换和测试 |
| Chat 范围 Provider | `Chat.tsx`、消息树、工作区树 | 删除 `pendingWorkspaceFiles` 和附件全局事件 |
| 图片引用命令 | `ImageContextMenu.tsx` 及调用方 | 从事件改为 Context 命令 |
| 工作区插入命令 | `WorkspaceView.tsx`、`Chat.tsx` | 改为统一添加命令 |
| `@` 提及 | `InputArea.tsx` | 调用统一添加命令并保留文字消费逻辑 |
| 上传入口 | `InputArea.tsx`、`InputControls.tsx` | 调用 `addLocalFiles` |
| 统一预览 | `InputControls.tsx`、预览组件 | 只消费统一附件数组 |
| 模型能力 | `InputArea.tsx` | 使用控制器派生值 |
| 草稿事务 | `useInputDraftTransaction.ts`、`useInputSubmission.ts` | 合并三个附件事务 |
| 提交快照 | `attachmentSubmission.ts`、`useInputSubmission.ts` | 统一输入类型和验证 |
| 电商确认旁路 | `useInputExternalEvents.ts` | 使用快照中的图片 URL |
| ContentPart 构建 | `messageSender.ts` | 收口重复图片映射，保持签名兼容 |

## 13. 文件计划

新增：

- `frontend/src/components/chat/attachments/ChatAttachment.types.ts`
- `frontend/src/components/chat/attachments/attachmentAdapters.ts`
- `frontend/src/components/chat/attachments/ChatAttachmentContext.ts`
- `frontend/src/components/chat/attachments/ChatAttachmentProvider.tsx`
- `frontend/src/components/chat/attachments/attachmentSubmission.ts`
- `frontend/src/components/chat/attachments/useChatAttachments.ts`
- `frontend/src/components/chat/attachments/ChatAttachmentPreview.tsx`
- 对应 `__tests__` 文件。

修改：

- `frontend/src/pages/Chat.tsx`
- `frontend/src/components/chat/input/InputArea.tsx`
- `frontend/src/components/chat/input/InputControls.tsx`
- `frontend/src/components/chat/input/InputControls.types.ts`
- `frontend/src/components/chat/input/useInputSubmission.ts`
- `frontend/src/components/chat/input/useInputDraftTransaction.ts`
- `frontend/src/components/chat/input/useInputExternalEvents.ts`
- `frontend/src/components/chat/media/ImageContextMenu.tsx` 及实际调用方。
- `frontend/src/services/messageSender.ts`

被替代组件仅在全局调用方归零且测试迁移完成后删除；如仍有调用方则保留薄适配器。

实施结果：旧 `ImagePreview`、`FilePreview`、`WorkspaceAttachmentPreview` 和
`attachmentNormalization` 的运行调用方均归零，源码及过期测试已删除；
`ImagePreviewModal` 仍由统一 `PreviewHost` 使用，因此保留。

## 14. 分阶段实施

1. 建立类型、适配器、快照转换与单元测试，不接入 UI。
2. 建立 Chat 范围 Provider 和统一控制器，保持旧 Props 临时兼容。
3. 迁移本地上传、引用、工作区插入和 `@` 提及。
4. 迁移预览、删除、模型能力和草稿事务。
5. 迁移提交与电商确认旁路，收口 `ContentPart` 构建重复逻辑。
6. 删除旧旁路与无调用组件，执行全量验证和文档同步。

## 15. 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增前端聊天附件领域模块 | 中 | 严格区分草稿模型与消息协议 |
| 数据流 | 多入口收口到单一控制器 | 中 | 分阶段迁移并保留临时兼容 |
| 耦合度 | 消息树和工作区树依赖 Chat Context | 中 | Context 仅暴露稳定命令接口 |
| 性能 | 小数组派生，无额外网络请求 | 低 | 使用稳定 ID 和函数式更新 |
| 可观测性 | 上传错误继续走现有错误条 | 低 | 测试覆盖来源和状态转换 |
| 可回滚性 | 纯前端，无数据迁移 | 低 | 按阶段提交，可恢复旧 Props 路径 |

## 16. 验证标准

- 本地图片、引用图片、工作区图片显示一致缩略图。
- 工作区图片不显示文件名。
- 所有入口均通过统一控制器添加。
- 所有删除均通过附件 ID。
- 聊天、图生图、图生视频和电商图使用同一图片快照。
- 工作区图片最终仍构造 `ImagePart`，后端多模态测试保持通过。
- 前端全量测试不得低于基线 `104 files / 1138 tests`。
- 新增核心模块语句、分支、函数覆盖率均不低于 80%。
- TypeScript、ESLint、生产构建、文件与函数阈值全部通过。

## 17. 部署与回滚

无数据库和 API 迁移。部署失败时回滚前端提交并重新执行 `./deploy/deploy.sh -f`。由于消息协议不变，回滚不会影响已保存消息。
