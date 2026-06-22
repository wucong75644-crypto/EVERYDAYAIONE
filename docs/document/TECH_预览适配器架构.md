# 技术设计：预览适配器架构（Preview Adapter Registry）

> 版本：v1.0 · 创建：2026-06-22 · 任务等级：A 级 · 状态：待开发

## 1. 背景与根因

### 当前问题
用户报告：工作区双击 `*.docx` / `*.pptx` / `*.ppt` 直接下载，不预览。深查后发现这只是症状，根因是「**白名单分散症**」——预览能力的真相散布在 5 处互不通信的位置：

| 位置 | 内容 | 用途 |
|------|------|------|
| `services/workspace.ts:76` | `WORKSPACE_ALLOWED_EXTENSIONS`（19 项含 docx/pptx） | 上传白名单 |
| `utils/fileCategory.ts:9-13` | `IMAGE_EXTS` + `VIDEO_EXTS` | 分类 Tab + 预览路由 |
| `chat/media/FilePreviewModal.tsx:24` | `PREVIEWABLE_EXTS`（**不含 docx/pptx**） | 预览决策 — bug 根源 |
| `workspace/WorkspaceFileItem.tsx:14` | `_IMAGE_EXTS`（含 svg，与 fileCategory 不一致） | 缩略图显示 |
| `workspace/WorkspaceView.handleOpen` | 串联 `canPreviewImage→canPreviewVideo→canPreview→download` | 预览路由 |

加上 `FilePreviewModal` 内部第 6 处分发（`isPdf` / `isExcel` / `isCsv` 三个布尔 + 文本兜底）——412 行的 god component。每次加新类型要改 5-6 个地方，必漏。

补丁式修复（再加白名单 + 加 isDocx 分支）每补一种类型重复一次，技术债持续累积。

### 顺带发现的 PDF bug
线上 PDF 双击有时显示下载行为。根因可能：
1. CDN 故障时 fallback 到后端 `/workspace/preview` 代理
2. 后端用 `FileResponse(path, filename=name)`，starlette 传 `filename` 参数时**默认设 `Content-Disposition: attachment`**
3. 浏览器 iframe 看到 attachment 头 → 触发下载而非 inline 渲染

新架构会一并修：PdfAdapter 强制走后端代理 + 后端 endpoint 显式 `inline` disposition。

## 2. 方案：Preview Adapter Registry

### 设计原则
1. **单一真相源**：所有「文件类型 → 预览方式」决策收敛到一个 registry
2. **决策与渲染分离**：调用方只问 `registry.canPreview(item)` 与 `registry.resolveAdapter(item)`，不关心是哪个组件
3. **加新类型只动一个地方**：写一个 adapter 文件 + registry 注册一行
4. **保留所有现有 UI 交互 100% 不变**：ImagePreviewModal / VideoPreviewModal 作为底层组件保留，新 adapter 薄包装

### 架构图
```
WorkspaceView / FileCard / ImagePreview / MessageItem
        │
        ▼
   usePreview()  ←─── 唯一对外接口（open/close/setIndex）
        │
        ▼
   PreviewHost  ←─── 唯一入口组件
        │
        ▼
  resolveAdapter(item)  ─── registry 按 ext/mime 选 adapter
        │
        ▼
  ┌──────────────────────────────────────────────────┐
  │ ImageAdapter   →  ImagePreviewModal (现有, 不动)  │
  │ VideoAdapter   →  VideoPreviewModal (现有, 不动)  │
  │ PdfAdapter     →  iframe + PreviewFrame          │
  │ SpreadsheetAdapter → xlsx/csv + PreviewFrame    │
  │ TextAdapter    →  pre-wrap + PreviewFrame       │
  │ DocxAdapter    →  mammoth.js + PreviewFrame     │
  │ PptxAdapter    →  后端转 PDF → iframe           │
  │ FallbackAdapter →  「暂不支持，点击下载」        │
  └──────────────────────────────────────────────────┘
```

## 3. 文件结构

### 新建（11 个）
| 路径 | 职责 | 估算行数 |
|------|------|---------|
| `frontend/src/preview/types.ts` | PreviewItem / PreviewCommonProps / PreviewAdapter 接口 | ~60 |
| `frontend/src/preview/registry.ts` | 注册表 + resolveAdapter + canPreview + 扩展名白名单 | ~80 |
| `frontend/src/preview/usePreview.ts` | open/close/setIndex 状态机 Hook | ~50 |
| `frontend/src/preview/PreviewHost.tsx` | 唯一入口组件，按 registry 渲染 | ~60 |
| `frontend/src/preview/PreviewFrame.tsx` | 全屏 shell（遮罩/工具栏/ESC/动画）共享给文档类 adapter | ~150 |
| `frontend/src/preview/adapters/ImageAdapter.tsx` | 薄包装 ImagePreviewModal | ~50 |
| `frontend/src/preview/adapters/VideoAdapter.tsx` | 薄包装 VideoPreviewModal | ~40 |
| `frontend/src/preview/adapters/PdfAdapter.tsx` | iframe + 后端代理 | ~80 |
| `frontend/src/preview/adapters/SpreadsheetAdapter.tsx` | xlsx/xls/csv/tsv 解析 + 表格 | ~200 |
| `frontend/src/preview/adapters/TextAdapter.tsx` | 文本/代码渲染 | ~80 |
| `frontend/src/preview/adapters/DocxAdapter.tsx` | mammoth.js → HTML | ~80 |
| `frontend/src/preview/adapters/PptxAdapter.tsx` | 调后端 /preview/render → iframe | ~80 |
| `frontend/src/preview/adapters/FallbackAdapter.tsx` | 不支持类型 → 弹窗显示「该格式暂不支持预览」+ 大下载按钮（对齐 Google Drive / 飞书 / Dropbox 行为） | ~80 |

### 改动（4 个调用方）
| 文件 | 改动 |
|------|------|
| `components/workspace/WorkspaceView.tsx` | 删除 3 个 state（previewFile/previewImageIndex/previewVideoIndex），替换为 `usePreview` + `<PreviewHost />` |
| `components/chat/media/FileCard.tsx` | 替换 FilePreviewModal 直接调用为 usePreview |
| `components/chat/media/ImagePreview.tsx` | 替换 ImagePreviewModal 直接调用为 usePreview（含 onDelete 透传） |
| `components/chat/message/MessageItem.tsx` | 替换 ImagePreviewModal 直接调用为 usePreview |

### 删除（1 个）
- `components/chat/media/FilePreviewModal.tsx`（412 行）— 能力全部迁移到 PdfAdapter / SpreadsheetAdapter / TextAdapter 后删除

### 保留不动（2 个底层 UI 组件）
- `components/chat/media/ImagePreviewModal.tsx`（471 行）— 缩放/拖拽/上下张/缩略图/删除等所有交互保持现状，仅被 ImageAdapter 调用
- `components/chat/media/VideoPreviewModal.tsx`（170 行）— 同上，仅被 VideoAdapter 调用

### 收敛分散白名单
- 删除：`fileCategory.ts:canPreviewImage` / `canPreviewVideo`（被 registry 取代）
- 保留：`fileCategory.ts:IMAGE_EXTS` / `VIDEO_EXTS` / `categorize` / `matchesFilter`（Tab 筛选仍用）
- 删除：`FilePreviewModal.canPreview`（被 `registry.canPreview()` 取代）
- 改：`WorkspaceFileItem._IMAGE_EXTS` 改用 `IMAGE_EXTS` from fileCategory（消除重复）

## 4. 核心接口（types.ts）

```ts
export interface PreviewItem {
  url?: string;              // CDN URL（首选）
  workspacePath?: string;    // workspace 相对路径（fallback 后端代理）
  filename: string;          // 含扩展名
  mimeType?: string | null;
  size?: number;
}

export interface PreviewCommonProps {
  item: PreviewItem;
  siblings: PreviewItem[];   // 兄弟列表（单文件时 = [item]）
  index: number;
  onClose: () => void;
  onNavigate: (newIndex: number) => void;
  onDelete?: () => void;     // 输入框 ImagePreview 用
}

export interface PreviewAdapter {
  id: string;
  match: (item: PreviewItem) => boolean;
  priority: number;          // 大优先；图片/视频=100，文档=80，文本=50，fallback=0
  Component: React.ComponentType<PreviewCommonProps>;
  supportsNavigation: boolean;
  label: string;
}
```

## 5. Registry 设计

```ts
const IMAGE_EXTS = new Set([...]);   // 与 fileCategory 共享
const VIDEO_EXTS = new Set([...]);

const adapters: PreviewAdapter[] = [
  imageAdapter,        // match: ext in IMAGE_EXTS || mime/^image/
  videoAdapter,        // match: ext in VIDEO_EXTS || mime/^video/
  pdfAdapter,          // match: ext === 'pdf'
  spreadsheetAdapter,  // match: ext in [xlsx,xls,csv,tsv]
  docxAdapter,         // match: ext === 'docx'
  pptxAdapter,         // match: ext === 'pptx'
  textAdapter,         // match: ext in [txt,md,log,json,yaml,yml,xml,py,js,ts,html,css,sql]
  fallbackAdapter,     // match: always; priority=0
];

export function resolveAdapter(item: PreviewItem): PreviewAdapter {
  return adapters
    .filter((a) => a.match(item))
    .sort((a, b) => b.priority - a.priority)[0];
}

export function canPreview(item: PreviewItem): boolean {
  return resolveAdapter(item).id !== 'fallback';
}
```

## 6. usePreview Hook

```ts
type PreviewState =
  | { kind: 'closed' }
  | { kind: 'open'; items: PreviewItem[]; index: number };

export function usePreview() {
  const [state, setState] = useState<PreviewState>({ kind: 'closed' });
  const open = useCallback((items: PreviewItem[] | PreviewItem, index = 0) => {
    const arr = Array.isArray(items) ? items : [items];
    setState({ kind: 'open', items: arr, index });
  }, []);
  const close = useCallback(() => setState({ kind: 'closed' }), []);
  const setIndex = useCallback((i: number) => {
    setState((s) => (s.kind === 'open' ? { ...s, index: i } : s));
  }, []);
  return { state, open, close, setIndex };
}
```

## 7. 100% 功能还原映射表

| 原有功能 | 旧位置 | 新位置 | 还原方式 |
|---------|--------|--------|---------|
| ImagePreviewModal 全部能力（缩放/拖拽/上下张/缩略图/删除/动画/键盘）| `chat/media/ImagePreviewModal.tsx` (471 行) | 不动 | ImageAdapter 薄包装，零改动底层 |
| VideoPreviewModal 全部能力 | `chat/media/VideoPreviewModal.tsx` (170 行) | 不动 | VideoAdapter 薄包装 |
| PDF iframe 渲染 | `FilePreviewModal:301-307` | `PdfAdapter.tsx` | **改用后端代理 URL**（修 OSS attachment / CDN 故障导致下载的 bug）|
| Excel：xlsx.read + sheetRows=201（大文件秒开） | `FilePreviewModal:197-202` | `SpreadsheetAdapter.tsx` | 逐行迁移 |
| Excel：clearMergedCells（合并单元格清非首行）| `FilePreviewModal:42-62` | `SpreadsheetAdapter.tsx` | 逐行迁移 |
| Excel：workbookRef 缓存 + Sheet tab 切换 | `FilePreviewModal:140,225-238` | `SpreadsheetAdapter.tsx` | 逐行迁移 |
| Excel/CSV：MAX_TABLE_ROWS=200 截断 + 提示 | `FilePreviewModal:22,358` | `SpreadsheetAdapter.tsx` | 逐行迁移 |
| Excel/CSV：行号列 sticky + 表头 sticky + 240px truncate | `FilePreviewModal:319-356` | `SpreadsheetAdapter.tsx` | 逐行迁移 |
| CSV/TSV：parseCSV 自定义解析（引号内逗号/换行）| `FilePreviewModal:68-110` | `SpreadsheetAdapter.tsx` | 逐行迁移 |
| 文本：行号 + pre-wrap + break-all + dark bg | `FilePreviewModal:369-390` | `TextAdapter.tsx` | 逐行迁移 |
| 顶部工具栏（emoji icon + 文件名 + 大小 + 下载 + 关闭）| `FilePreviewModal:258-284` | `PreviewFrame.tsx`（共享）| 抽出复用 |
| ESC 关闭 | `FilePreviewModal:148-154` | `PreviewFrame.tsx` | 统一处理 |
| Loader2 spinner + 错误显示 | `FilePreviewModal:288-298` | `PreviewFrame.tsx` | 统一处理 |
| CDN 优先 → CORS 失败 fallback 后端代理 + getAuthHeaders | `FilePreviewModal:172-189` | `preview/utils/fetchPreview.ts` 工具函数 | 抽出复用 |
| `canPreview(name)` 函数 | `FilePreviewModal:122-125` | `registry.canPreview(item)` | 参数从 name → PreviewItem 更明确，调用方都改 |
| `canPreviewImage` / `canPreviewVideo` | `fileCategory.ts:43-50` | 删除 | 由 `resolveAdapter().id === 'image'` 取代 |
| WorkspaceView 三 state 分发 | `WorkspaceView:55-57,99-132` | `usePreview` + `<PreviewHost />` | 一个 state 一个组件 |
| FileCard 单文件预览 | `FileCard:16,24-32` | `usePreview.open(item)` | 等价 |
| ImagePreview 多图轮播 + onDelete | `ImagePreview:20-69` | `usePreview.open(items, index)` + props.onDelete | 等价 |
| MessageItem AI 生成图多图轮播 | `MessageItem:793-806` | `usePreview` | 等价 |

## 8. 新增能力

### docx 预览（DocxAdapter）
- 库：`mammoth@^1.x`（纯前端，~50KB gzip）
- 实现：
  ```tsx
  const { value: html } = await mammoth.convertToHtml({ arrayBuffer });
  setContent(html);  // 渲染到 sanitize 后的 div
  ```
- 限制：复杂样式（如批注、嵌入图表）可能丢失；onError 显示「样式异常，可下载查看完整版」

### pptx 预览（PptxAdapter）
- 前端：调后端 endpoint，得到 PDF URL → iframe 渲染
- 后端新增 endpoint：`POST /files/workspace/preview/render`
  - 入参：`{ workspace_path: string }`
  - 出参：`{ pdf_url: string }` 或直接 stream PDF
  - 实现：调用 `libreoffice --headless --convert-to pdf` 转换
  - 缓存：OSS 上 `workspace/{path}_{mtime}.preview.pdf`，第二次直接命中缓存
  - 生产部署需要：`apt install libreoffice`（约 500MB），第一次冷启动 ~3s
- 同样适用于：`doc`、`ppt` 老格式（LibreOffice 都支持）→ 注册多个 ext 到同一 adapter

### 修复 PDF "iframe 黑屏 + 自动下载" — PDF.js 自渲染

**根本原因**（用户截图证实）：
浏览器内置 PDF 查看器被禁用 / 不支持时（Chrome 设「始终下载」、Safari iOS、
某些隐私扩展），`<iframe src="*.pdf">` 出现 iframe 黑屏 + 浏览器自动触发下载。
依赖浏览器内置查看器**不可靠**。

**方案**：PdfAdapter 用 PDF.js 在前端自渲染——跟 Excel 走 `xlsx.js`、docx
走 `mammoth.js` 同一思路，所有文档类型都用 JS 库自渲染，不依赖浏览器能力。

```tsx
// PdfAdapter.tsx (动态 import 不进首屏)
const { Document, Page, pdfjs } = await import('react-pdf');
<Document file={pdfUrl} onLoadError={onError}>
  <Page pageNumber={currentPage} />
</Document>
+ 翻页 / 缩放 / 下载 工具栏
```

**新依赖**：
- `react-pdf@^9.x`（~10KB） + `pdfjs-dist@^4.x`（~300KB gzip，动态加载不进首屏）
- 与 `xlsx`（~300KB，已用）同等量级

### 架构：CDN 优先 + 后端兜底（沿用历史设计）+ 显式故障感知

OSS bucket CORS 已配置（允许 `*.everydayai.com.cn` + `cdn.*` + `localhost:*`），
CORS 错误本不该发生；但 CDN 本身可能故障（实际遇到过 ERR_HTTP2_PROTOCOL_ERROR），
保留 fallback 作为基础设施异常时的兜底（行业惯例：Google Drive / 飞书 / Dropbox
均有此兜底）：

```
用户文件         →  CDN URL（fetch / img / video）          ← 99% 走这里，零 ECS 流量
                    └─ fetch 失败（CDN 故障 / CORS）
                         └─ 后端代理  /workspace/preview    ← 兜底 + console.warn
                              └─ 仍失败 → 显式错误 + 「点击下载」按钮

pptx 转换输出   →  后端 LibreOffice → inline PDF stream    ← 主路径，不是 fallback
```

**fallback 设计变更点**（修复历史 bug）：
1. **保留** fallback 链路（不动用户原设计的多一层兜底）
2. **新增** `console.warn('[preview] CDN failed, using backend fallback', { url, error })`
   — 让运维能从 Sentry / 前端日志感知 CDN 故障频率，**不再悄悄 fallback**
3. **后端 `/workspace/preview` 加 `content_disposition_type='inline'`** — 修
   FileResponse 默认 attachment 导致的 PDF "iframe 黑屏 + 触发下载" 隐患

### CDN 流量节省策略 100% 保留
所有 adapter（含 PdfAdapter / DocxAdapter / SpreadsheetAdapter）首先 fetch CDN URL，
流量从 CDN 出。仅在 CDN 错误时才走后端代理（消耗 ECS 临时流量，但属于事故应对）。

## 9. 工作量估算

| Phase | 任务 | 工时 |
|-------|------|------|
| 1 | 框架（types/registry/usePreview/PreviewHost/PreviewFrame）| 0.5 天 |
| 2 | ImageAdapter + VideoAdapter（薄包装，零风险）| 0.5 天 |
| 3 | **抽 Pdf / Spreadsheet / Text Adapter（主风险点，逐行迁移）**| 1 天 |
| 4 | FallbackAdapter + 后端 preview endpoint 改 inline | 0.5 天 |
| 5 | 4 个调用方迁移（WorkspaceView/FileCard/ImagePreview/MessageItem）| 0.5 天 |
| 6 | 删 FilePreviewModal + 收敛白名单 | 0.5 天 |
| 7 | adapter 单测 + 集成测 + tsc + vitest 全绿 | 0.5 天 |
| 8 | DocxAdapter（mammoth）| 0.5 天 |
| 9 | 后端 LibreOffice + PptxAdapter + 缓存 | 1.5 天 |
| 10 | 部署 + 端到端手测 | 0.5 天 |
| **合计** | | **5.5 天** |

## 10. 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| 拆 FilePreviewModal 412 行漏细节 | 高 | 行号对照表逐条迁移；最终 diff 检查；adapter 单测覆盖每个分支 |
| 后端 LibreOffice 部署/性能 | 中 | OSS 缓存（mtime key） + 大文件超时控制 + 失败 fallback 到下载 |
| mammoth 不支持复杂样式 | 低 | onError 兜底文案 + 下载按钮 |
| PptxAdapter 调用后端 失败 | 中 | FallbackAdapter 兜底（提示 + 下载）|
| 迁移期间两套并存 | 低 | 不并存，一次性切完；中间 commit 不部署 |

## 11. 部署与回滚

- **数据库迁移**：无
- **API 兼容**：新增 `/files/workspace/preview/render`，纯追加；改动 `/files/workspace/preview` 的 disposition 是响应头级别，前端 iframe 无感
- **前端兼容**：纯重构 + 新增类型预览能力，已有调用方一次性迁移
- **新依赖**：
  - 前端：`mammoth@^1.x`
  - 后端：`apt install libreoffice`
- **回滚**：前后端可独立 revert；LibreOffice 包不卸载不影响

## 12. 文档更新清单

- [ ] `docs/FUNCTION_INDEX.md` — 新增 preview/ 模块的函数索引
- [ ] `docs/PROJECT_OVERVIEW.md` — 加 2026-06-22 更新记录
- [x] `docs/document/TECH_预览适配器架构.md` — 本文档

## 13. 关联功能影响清单（实施前最后核查）

### 容易踩坑的非显性关联点
| 关联点 | 现状 | 新架构需求 |
|--------|------|-----------|
| FileCard「预览」徽标显示条件 | `canPreview(file.name) ? '预览' : null` | docx/pptx 加入后会**多出预览徽标**——这是预期增强 |
| 聊天内 AI 生成的文件附件 | 通过 FileCard 渲染 | 自动继承 docx/pptx 预览能力 |
| ImagePreview 删除最后一张图自动关闭 | previewIndex=-1 时关闭，否则保持索引 | usePreview 提供 setIndex；onDelete 后由调用方计算下一个索引 |
| `workspace_path` 在 PreviewItem 中是 `string?` | 当前用 `!` 断言 | fallback 时 workspacePath 空要显式 throw |
| `getAuthHeaders()` 后端代理认证 | fallback fetch 带 Authorization + X-Org-Id | 抽到 `preview/fetchPreview.ts` 共用 |
| `downloadImage()` vs `downloadFile()` | Image 用 downloadImage，File 用 downloadFile | 各自 adapter 调用对应工具，不混用 |
| 多选模式 + 双击文件 | 仍触发 onOpen 弹预览 | 不变 |
| ImagePreview 的 `image.preview` blob URL | ObjectURL（本地 blob，不是 CDN）| PreviewItem.url 接受 blob URL，下载/预览同 URL |
| 上传中/失败的图片 | 调用方 filter 后才传给 Modal | 调用方仍负责 filter |
| 键盘事件优先级 | WorkspaceView 守卫 previewFile 等 3 个 state | 改为守卫 `preview.state.kind === 'open'` 一个条件 |

### 实施前要解决的细节
1. **PDF.js worker 配置**：`vite.config.ts` 加 pdf.worker.min.js 单独打包路径（否则 PDF 不渲染）
2. **mammoth.js 安全性**：加 DOMPurify 清洗输出 HTML（docx 内若嵌入恶意 HTML）
3. **后端 LibreOffice subprocess**：
   - timeout（防大文件卡死，默认 60s）
   - 转换失败 fallback：返回 4xx + 错误消息，前端 FallbackAdapter 兜底
4. **pptx 转换缓存 key**：`workspace-preview-cache/{md5(file_content)}_{mtime}.pdf` —— 同内容秒开，文件改名/修改重新转

## 14. 实施顺序（按依赖）

1. **后端先**：preview/render endpoint + preview inline 修复 + 部署 libreoffice
2. **前端框架**：types/registry/usePreview/PreviewHost/PreviewFrame
3. **简单 adapter**：Image/Video（包装现有 Modal）+ Fallback
4. **复杂 adapter**：Pdf/Spreadsheet/Text（拆 FilePreviewModal）
5. **新 adapter**：Docx/Pptx
6. **调用方迁移**：WorkspaceView/FileCard/ImagePreview/MessageItem
7. **清理**：删除 FilePreviewModal + 收敛白名单 + 文档更新
8. **回归测试 + 端到端手测 + 部署**

每 Phase 完成提交一次（带「[WIP]」前缀），全部完成 + 测试通过 + 用户验收后才打主 commit + 部署。
