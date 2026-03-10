# 技术设计：PDF 文档上传

## 1. 现有代码分析

### 已阅读文件
- **前端**：`InputArea.tsx`、`InputControls.tsx`、`useImageUpload.ts`、`UploadMenu.tsx`、`ImagePreview.tsx`、`upload.ts`、`messageSender.ts`、`useTextMessageHandler.ts`、`models.ts`、`types/message.ts`
- **后端**：`image.py`(路由)、`storage_service.py`、`message.py`(schema)、`agent_loop.py`、`chat_handler.py`、`chat_context_mixin.py`、`kie/chat_adapter.py`、`google/chat_adapter.py`、`intent_router.py`

### 架构理解
1. **上传流程**：前端 `useImageUpload` → `uploadImageFile()` → `POST /images/upload` → `StorageService.upload_image()` → OSS → CDN URL
2. **消息流程**：前端 `sendMessage()` → `POST /conversations/{id}/messages/generate` → `IntentRouter`/`AgentLoop` 路由 → `ChatHandler._stream_generate()` → 适配器 `stream_chat()`
3. **多模态传递（两条链路）**：
   - **KIE（gemini-3-pro/flash）**：`_build_llm_messages()` → `image_url` 格式 → KIE 适配器 `format_multimodal_message()` 统一用 URL 传递，Gemini 3 API 根据 MIME 自动识别 PDF，**无需额外转换**
   - **Google 直连（gemini-2.5-flash/pro）**：`_build_llm_messages()` → `image_url` 格式 → Google 适配器 `_convert_to_google_format()` 下载文件 → base64 编码 → `inline_data` 格式。**当前只处理图片，需要扩展支持 PDF**
4. **Google 直连适配器问题**：
   - `_download_image()` 限制 20MB → PDF 需要 50MB
   - `_detect_mime_type()` 只识别 jpg/png/webp/gif → 缺少 `.pdf` 映射
   - 两个函数名暗示只处理图片，语义需要泛化

### 可复用模块
- `StorageService` — 扩展允许类型即可支持 PDF
- `useImageUpload` 模式 — `useFileUpload` 完全照搬其状态管理模式
- `UploadMenu` — 已有"上传文档"按钮（disabled），启用即可
- `FilePart` schema — 前后端都已定义，直接使用
- `UploadErrorBar` — PDF 上传错误复用

### 设计约束
- 必须兼容现有 `ContentPart[]` 消息格式
- KIE 适配器通过 `image_url` 格式传递所有媒体，PDF URL 也走这个通道
- Agent Loop 的 `_extract_text()` 只提取 `TextPart`，不受 PDF 影响
- `ChatContextMixin._build_llm_messages()` 只处理 `ImagePart`，需扩展支持 `FilePart`

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|-------------|
| `StorageService` 新增 PDF 类型 | `storage_service.py` | 添加 MIME 类型白名单 |
| 新增 `/files/upload` 路由 | 新建 `file.py` | 注册到 `__init__.py` |
| `_build_llm_messages()` 支持 `FilePart` | `chat_context_mixin.py` | 提取 file URL 作为 `image_url` 传给模型 |
| `agent_loop.py` 识别 PDF 附件 | `agent_loop.py` | 检测 `FilePart` + 注入 PDF 上下文提示 |
| Google 适配器支持 PDF | `google/chat_adapter.py` | `_detect_mime_type()` 加 .pdf + `_download_image()` 扩大限制 + 重命名 |
| Google 模型配置加 PDF | `google/configs.py` | 添加 `supports_pdf: True` |
| 前端 Google 模型 pdfInput | `models.ts` | gemini-2.5-flash/pro 标记 `pdfInput: true` |
| `InputControls` 新增 PDF 文件输入 | `InputControls.tsx` | 添加隐藏 `<input>` + 传递 PDF 回调 |
| `InputArea` 集成 `useFileUpload` | `InputArea.tsx` | 导入 hook + 传递 props |
| `UploadMenu` 启用文档按钮 | `UploadMenu.tsx` | 条件判断 + 回调绑定 |
| `handleSubmit` 合并 PDF 到 content | `InputArea.tsx` | FilePart 加入发送内容 |
| `useTextMessageHandler` 支持 files | `useTextMessageHandler.ts` | 新增 `createTextWithFiles()` |
| 拖拽提示文案更新 | `InputControls.tsx` | 修改提示文字 |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| PDF 文件过大（>50MB） | 前端校验拦截 + toast 提示 | `useFileUpload` |
| 非 PDF 文件伪装 | 后端校验 MIME + magic bytes | `storage_service.py` |
| 上传中网络断开 | 上传失败状态 + 错误提示 + 可删除 | `useFileUpload` |
| 模型不支持 PDF | UploadMenu 禁用 + 切模型后 ConflictAlert | `UploadMenu` + `useModelSelection` |
| PDF + 图片混合上传 | 正常支持，content 中同时包含 FilePart + ImagePart | `_build_llm_messages` |
| 智能模式（auto）带 PDF | Agent Loop 检测 FilePart → 路由到 Gemini 模型 | `agent_loop.py` |
| OSS 上传超时 | 默认 httpx 超时（60s），失败显示错误 | `storage_service.py` |
| 重复上传同一文件 | 每次生成唯一 object_key，无冲突 | OSS 服务 |

---

## 3. 技术栈

- 前端：React + TypeScript + Zustand + TailwindCSS（不变）
- 后端：Python 3 + FastAPI + Supabase（不变）
- 存储：阿里云 OSS + CDN（不变）
- **无需新增依赖**

---

## 4. 目录结构

### 新增文件
- `backend/api/routes/file.py` — 文件上传路由（`POST /files/upload`）
- `backend/schemas/file.py` — 文件上传请求/响应 schema
- `frontend/src/hooks/useFileUpload.ts` — PDF 文件上传 Hook
- `frontend/src/components/chat/FilePreview.tsx` — PDF 文件预览卡片
- `frontend/src/services/fileUpload.ts` — 文件上传 API 服务

### 修改文件
- `backend/services/storage_service.py` — 新增 `upload_file()` + PDF MIME 白名单
- `backend/api/routes/__init__.py` — 注册 file 路由
- `backend/services/handlers/chat_context_mixin.py` — `_build_llm_messages()` 支持 FilePart
- `backend/services/agent_loop.py` — 检测 PDF 附件 + 注入上下文提示
- `backend/services/adapters/google/chat_adapter.py` — `_detect_mime_type()` + `_download_media()` 支持 PDF
- `backend/services/adapters/google/configs.py` — 添加 `supports_pdf` 配置
- `frontend/src/constants/models.ts` — gemini-2.5-flash/pro 标记 `pdfInput: true`
- `frontend/src/components/chat/UploadMenu.tsx` — 启用"上传文档"按钮
- `frontend/src/components/chat/InputControls.tsx` — 添加 PDF 文件输入 + props
- `frontend/src/components/chat/InputArea.tsx` — 集成 useFileUpload + 合并 content
- `frontend/src/hooks/handlers/useTextMessageHandler.ts` — 支持 files 参数
- `frontend/src/services/messageSender.ts` — 新增 `createTextWithFiles()`

---

## 5. 数据库设计

**无需新增表/字段。** PDF 信息通过 `FilePart` 存储在 messages 表的 `content` JSONB 字段中：

```json
{
  "content": [
    {"type": "text", "text": "请分析这份报告"},
    {"type": "file", "url": "https://cdn.../xxx.pdf", "name": "report.pdf", "mime_type": "application/pdf", "size": 2400000}
  ]
}
```

---

## 6. API 设计

### POST /files/upload

- **描述**：上传文件到 OSS（当前仅支持 PDF）
- **认证**：需要登录（`CurrentUser`）
- **请求**：`multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | UploadFile | 是 | PDF 文件 |

- **成功响应（200）**：
```json
{
  "url": "https://cdn.example.com/uploaded/xxx.pdf",
  "name": "report.pdf",
  "mime_type": "application/pdf",
  "size": 2400000
}
```

- **错误响应**：

| 状态码 | code | 说明 |
|--------|------|------|
| 400 | VALIDATION_ERROR | 文件类型不支持 / 文件过大 |
| 401 | UNAUTHORIZED | 未登录 |
| 500 | UPLOAD_FILE_ERROR | 上传失败 |

---

## 7. 前端状态管理

### useFileUpload Hook

```typescript
interface UploadedFile {
  id: string;
  file: File;
  name: string;
  size: number;
  mime_type: string;
  url: string | null;    // 上传后的 CDN URL
  isUploading: boolean;
  error: string | null;
}

// 返回值
{
  files: UploadedFile[];
  uploadedFileUrls: { url: string; name: string; mime_type: string; size: number }[];
  isUploading: boolean;
  uploadError: string | null;
  hasFiles: boolean;
  handleFileSelect: (e: ChangeEvent<HTMLInputElement>) => void;
  handleFileDrop: (files: FileList) => void;
  handleRemoveFile: (fileId: string) => void;
  handleRemoveAllFiles: () => void;
  clearUploadError: () => void;
}
```

无需 Zustand Store 变更。文件状态通过 Hook 本地管理（同 `useImageUpload`）。

---

## 8. 开发任务拆分

### 阶段 1：后端（API + 存储）

- [ ] **任务 1.1**：`storage_service.py` 新增 `upload_file()` 方法 + PDF MIME 白名单（`ALLOWED_FILE_TYPES`）+ 50MB 大小限制
- [ ] **任务 1.2**：新建 `schemas/file.py`（`UploadFileResponse`）
- [ ] **任务 1.3**：新建 `api/routes/file.py`（`POST /files/upload`），注册到 `__init__.py`

### 阶段 2：后端（多模态链路 — 两条适配器）

- [ ] **任务 2.1**：`chat_context_mixin.py` — `_build_llm_messages()` 扩展，提取 `FilePart` URL 作为 `image_url` 格式传给模型
- [ ] **任务 2.2**：`agent_loop.py` — `run()` 检测 `FilePart`，注入 `[上下文：用户附带了PDF文档]` 提示，影响路由决策
- [ ] **任务 2.3**：`google/chat_adapter.py` — `_download_image()` 重命名为 `_download_media()`，max_size 参数化支持 50MB；`_detect_mime_type()` 增加 `.pdf → application/pdf` 映射
- [ ] **任务 2.4**：`google/configs.py` — 两个模型添加 `supports_pdf: True`
- [ ] **任务 2.5**：`frontend/constants/models.ts` — gemini-2.5-flash 和 gemini-2.5-pro 标记 `pdfInput: true` + `maxPDFSize: 50`

### 阶段 3：前端（上传基础设施）

- [ ] **任务 3.1**：新建 `services/fileUpload.ts` — `uploadFile()` API 调用
- [ ] **任务 3.2**：新建 `hooks/useFileUpload.ts` — PDF 文件状态管理（校验/上传/删除）
- [ ] **任务 3.3**：新建 `components/chat/FilePreview.tsx` — PDF 预览卡片组件

### 阶段 4：前端（UI 集成）

- [ ] **任务 4.1**：修改 `UploadMenu.tsx` — 启用"上传文档"按钮（根据 `pdfInput` 能力）
- [ ] **任务 4.2**：修改 `InputControls.tsx` — 添加 PDF `<input>` + 传递回调 + 预览区展示 + 拖拽提示更新
- [ ] **任务 4.3**：修改 `InputArea.tsx` — 集成 `useFileUpload` + 合并 PDF 到 `content` 发送
- [ ] **任务 4.4**：修改 `useTextMessageHandler.ts` + `messageSender.ts` — 支持 `FilePart` 内容构建

### 阶段 5：验证

- [ ] **任务 5.1**：端到端测试（上传 PDF → 发送 → Gemini 回答）
- [ ] **任务 5.2**：补充单元测试

---

## 9. 依赖变更

**无需新增依赖。** 现有技术栈完全支持：
- 后端：FastAPI `UploadFile` + 阿里云 OSS SDK
- 前端：原生 `FormData` + `fetch`

---

## 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| Gemini API 对 PDF URL 的 MIME 识别 | 中 | OSS 上传时保留原始 `content_type`，确保 URL 响应头正确 |
| 大文件上传慢（50MB） | 低 | 前端 spinner + 用户可取消；OSS 直传性能足够 |
| 智能模式路由到不支持 PDF 的模型 | 中 | Agent Loop 检测 PDF → 强制路由到 Gemini |

---

## 11. 文档更新清单

- [ ] FUNCTION_INDEX.md — 新增 `upload_file()`、`useFileUpload`
- [ ] PROJECT_OVERVIEW.md — 新增 `file.py` 路由说明

---

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（13 个改动点 → 14 个任务）
- [x] 7 类边界场景均有处理策略
- [x] 所有新增文件预估 ≤ 500 行（最大 `useFileUpload.ts` ≈ 120 行）
- [x] 无模糊版本号依赖（无新依赖）
- [x] 只做 Google 系模型（KIE + 直连）PDF 支持，不做千问/Claude/GPT 等模型转接

---

**确认后保存文档并进入开发（`@4-implementation`）**
