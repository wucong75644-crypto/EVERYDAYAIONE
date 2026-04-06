# 技术设计：沙盒文件生成与下载

## 1. 现有代码分析

### 已阅读文件及关键理解

- `sandbox/executor.py`：受限 exec 环境，stdout 通过 StringIO 捕获，结果截断 8000 字符，通过 `register()` 注入外部函数
- `sandbox/functions.py`：`create_sandbox_executor()` 注册了 erp_query/erp_query_all/web_search/write_file 等函数
- `sandbox/validators.py`：`truncate_result()` 截断阈值 8000 字符
- `file_executor.py`：workspace 路径隔离（org/{org_id}/{user_id} 或 personal/{hash}），有 `get_cdn_url()` 但沙盒内未暴露
- `oss_service.py`：`upload_bytes()` 支持 xlsx/csv/pdf 等二进制格式，返回 CDN URL
- `media_extractor.py`：从文本中正则提取图片/视频 URL → ImagePart/VideoPart，**不支持 FilePart**
- `chat_handler.py`：`on_complete` 调用 `extract_media_parts()` 转为 ContentPart 数组存入消息
- `schemas/message.py`：FilePart 已定义（url/name/mime_type/size），但从未被使用
- `tool_executor.py`：`_code_execute` 返回纯文本给主 Agent
- `config/erp_tools.py`：ERP_ROUTING_PROMPT 是 ERP Agent 的行为规则

### 可复用模块

- `oss_service.upload_bytes()` — 直接上传二进制到 OSS，返回 CDN URL
- `extract_media_parts()` — 扩展即可支持 FilePart
- `FilePart` 数据模型 — 后端已定义（url/name/mime_type/size），**但前端无渲染分支**
- `create_sandbox_executor()` — 现有注册机制，加一个函数即可

### 设计约束

- 沙盒内无网络、无 os 模块，只能通过注册函数访问外部
- 沙盒结果是纯文本（截断 8000 字符），不能返回结构化数据
- 必须走现有的 `extract_media_parts()` → `on_complete` 链路
- 参考 Claude/ChatGPT 模式：**输出格式选择由 LLM 自主判断，提示词驱动，无硬编码阈值**

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 沙盒新增 upload_file 函数 | sandbox/functions.py | 注册到 executor |
| io 模块加入白名单 | sandbox/executor.py | `_ALLOWED_IMPORT_MODULES` 加 `"io"` **（必须，否则 BytesIO 不可用）** |
| [FILE] 标记在 tool result 返回时提取 | handlers/chat_tool_mixin.py | 提取 FilePart → `ChatHandler._pending_file_parts`，替换为友好文本 |
| ChatHandler on_complete 合并 FilePart | handlers/chat_handler.py | `_pending_file_parts` 追加到 `result_parts` |
| ChatHandler FilePart 转 dict | handlers/chat_handler.py | `_convert_content_parts_to_dicts` 加 FilePart |
| media_extractor 支持 FilePart | handlers/media_extractor.py | 新增 [FILE] 正则（兜底，万一有漏） |
| ERP 提示词加输出格式规则 | config/erp_tools.py | ERP_ROUTING_PROMPT 追加规则 |
| code_execute 工具描述加示例 | config/code_tools.py | 描述中加 upload_file 用法 |
| 前端 messageUtils.ts | frontend/src/utils/messageUtils.ts | 新增 `getFiles()` 提取 FilePart |
| 前端 MessageItem.tsx | frontend/src/components/chat/MessageItem.tsx | 提取文件信息传给 MessageMedia |
| 前端 MessageMedia.tsx | frontend/src/components/chat/MessageMedia.tsx | 新增文件下载卡片渲染分支 |
| 前端下载函数 | frontend/src/utils/downloadFile.ts | 新建通用文件下载函数（支持所有 MIME 类型） |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 生成的文件过大（>50MB） | OSS upload_bytes 有大小校验，超限返回错误文本 | sandbox/functions.py |
| 沙盒执行超时（120s） | 现有超时机制兜底，返回 ⏱ 提示 | executor.py |
| OSS 上传失败（网络/权限） | try-except 返回错误文本，LLM 看到后告知用户 | sandbox/functions.py |
| 文件名含特殊字符/路径穿越 | pathlib 提取 ext，sanitize 文件名 | sandbox/functions.py |
| pandas to_excel 需要 BytesIO | 沙盒白名单确认 io 模块可用 | executor.py |
| 用户连续生成多个文件 | 每次独立上传，返回多个 [FILE] 标记，前端逐个渲染 | media_extractor.py |
| CDN URL 过期 | OSS 公读，无过期问题 | oss_service.py |
| LLM 第一轮就想生成文件不查数据 | 提示词规则约束"先查数据再决定输出格式" | erp_tools.py |
| LLM 不知道怎么用 upload_file | code_execute 工具描述里加完整示例代码 | code_tools.py |
| [FILE] 标记被 LLM 改写/翻译 | **在 code_execute 层直接解析**，不经过 LLM 传递 | tool_executor.py |
| 企微端文件下载 | CDN URL 公开链接，企微内置浏览器可打开（xlsx→WPS，pdf→预览） | 无需特殊处理 |

---

## 3. 技术栈

- 后端：Python 3.x + 现有沙盒框架
- 存储：阿里云 OSS（已配置）
- 前端：React + TypeScript（扩展现有组件）
- 数据处理：pandas + openpyxl（已安装）
- 无新增依赖

---

## 4. 目录结构

### 修改文件

**后端：**
- `backend/services/sandbox/functions.py`：注册 upload_file 函数
- `backend/services/sandbox/executor.py`：确认 io 模块白名单
- `backend/services/handlers/chat_tool_mixin.py`：tool result 中提取 [FILE] 标记 → _pending_file_parts
- `backend/services/handlers/media_extractor.py`：新增 [FILE] 正则兜底解析
- `backend/services/handlers/chat_handler.py`：on_complete 合并 FilePart + FilePart → dict 转换
- `backend/config/erp_tools.py`：ERP_ROUTING_PROMPT 追加输出格式规则
- `backend/config/code_tools.py`：code_execute 描述加 upload_file 示例

**前端：**
- `frontend/src/utils/messageUtils.ts`：新增 `getFiles()` 提取 FilePart
- `frontend/src/components/chat/MessageItem.tsx`：提取文件信息传给 MessageMedia
- `frontend/src/components/chat/MessageMedia.tsx`：新增 FileCard + 预览弹窗入口

### 新增文件
- `frontend/src/utils/downloadFile.ts`：通用文件下载函数
- `frontend/src/components/chat/FilePreviewModal.tsx`：文件在线预览弹窗（Excel表格/CSV/文本/代码/PDF）

---

## 5. 数据流设计

不需要新建数据库表。文件存 OSS，URL 存在消息的 content JSONB 中（复用现有 FilePart 结构）。

### 完整链路

```
用户: "帮我导出这个月订单按店铺统计成Excel"

Step 1: 主 Agent 调 erp_agent
Step 2: ERP Agent 判断"需要导出" → 调 code_execute
Step 3: 沙盒执行代码:
  ┌─────────────────────────────────────────────┐
  │ data = await erp_query_all(                 │
  │     "erp_trade_query", "order_list",        │
  │     {"time_type": "pay_time", ...}          │
  │ )                                           │
  │ df = pd.DataFrame(data["items"])            │
  │ pivot = df.pivot_table(                     │
  │     values="payment", index="shopName",     │
  │     aggfunc=["count", "sum"]                │
  │ )                                           │
  │ buf = io.BytesIO()                          │
  │ pivot.to_excel(buf)                         │
  │ result = await upload_file(                 │
  │     buf.getvalue(), "订单按店铺统计.xlsx"     │
  │ )                                           │
  │ print(result)                               │
  └─────────────────────────────────────────────┘
Step 4: ChatToolMixin 处理 tool result:
  - 从 result 中提取 [FILE] 标记 → FilePart 存入 ChatHandler._pending_file_parts
  - [FILE] 标记替换为 "📎 文件: 订单按店铺统计.xlsx"（给 LLM 看的友好文本）
  - 清理后的 result 塞进 messages
Step 5: ERP Agent LLM 合成结论（文字 + "📎 文件: xxx.xlsx"）
Step 6: 主 Agent LLM 生成最终回复
Step 7: ChatHandler on_complete:
  - extract_media_parts() 正常处理文字/图片/视频
  - 追加 code_execute 暂存的 FilePart
  - message.content = [TextPart, FilePart]
Step 8: 前端渲染：文字 + 文件下载卡片
```

---

## 6. 核心实现规格

### 6.1 沙盒注册函数 upload_file

```python
async def upload_file(
    content: bytes,     # 文件二进制内容
    filename: str,      # 文件名（如 "报表.xlsx"）
) -> str:              # 返回格式化文本（含 [FILE] 标记）
```

**逻辑：**
1. 从 filename 用 pathlib 提取扩展名
2. 用 mimetypes 推断 MIME 类型
3. 调用 `oss_service.upload_bytes(content, ext=ext, category="generated")`
4. 返回 `"✅ 文件已上传: {filename}\n[FILE]{url}|{filename}|{mime_type}|{size}[/FILE]"`

**安全：**
- 文件名 sanitize（去除路径分隔符）
- 扩展名白名单校验（复用 OSS SUPPORTED_FORMATS）
- 大小限制（复用 OSS 现有校验）

### 6.2 [FILE] 标记提取与传递（核心改动）

**问题分析**：
- [FILE] 标记存在于 code_execute 的返回文本中（tool result）
- tool result 作为 `{"role":"tool", "content": result}` 塞进 messages
- LLM 读取 tool result 后输出自己的 assistant message（`accumulated_text`）
- `extract_media_parts(accumulated_text)` 处理的是 LLM 输出，**看不到 tool result 中的 [FILE] 标记**
- 所以必须在 tool result 塞进 messages 之前提取 [FILE] 标记

**解析位置：ChatToolMixin._execute_single_tool()**

```python
# chat_tool_mixin.py 中：
_FILE_PATTERN = re.compile(
    r'\[FILE\](https?://\S+?)\|([^|]+)\|([^|]+)\|(\d+)\[/FILE\]'
)

# 工具执行后、result 塞进 messages 前：
# 1. 从 result 中提取 [FILE] 标记 → FilePart 列表
# 2. 将 [FILE] 标记替换为 "📎 文件: xxx.xlsx"（给 LLM 看的友好文本）
# 3. FilePart 存到 ChatHandler._pending_file_parts 列表
# 4. 清理后的 result 正常塞进 messages
```

**ChatHandler on_complete 合并**：

```python
# chat_handler.py on_complete 中：
result_parts = extract_media_parts(accumulated_text)
# 追加工具执行过程中积累的 FilePart
if hasattr(self, '_pending_file_parts') and self._pending_file_parts:
    result_parts.extend(self._pending_file_parts)
    self._pending_file_parts = []
```

**为什么这个方案最可靠**：
- [FILE] 标记在产生后第一时间被提取，不经过任何 LLM
- 友好文本（"📎 文件: xxx.xlsx"）让 LLM 知道文件已生成，可以在回复中提及
- FilePart 通过 ChatHandler 实例变量传递到 on_complete，无需改动 ToolExecutor

### 6.3 ERP 提示词 — 输出格式自动选择

追加到 `ERP_ROUTING_PROMPT`：

```
## 输出格式选择（自动判断）
- 统计汇总（总数/金额/占比）→ 直接文字回复
- 结果 ≤20 条明细 → 直接文字回复
- 结果 >20 条明细 → 调 code_execute 生成 Excel 文件
- 用户要求"导出/报表/Excel/下载/文件" → 调 code_execute 生成 Excel 文件
- 多维度对比/趋势分析 → 调 code_execute 计算，数据量大时同时生成 Excel
```

### 6.4 code_execute 工具描述 — 加 upload_file 示例

在工具 description 中追加：

```
生成可下载文件示例：
import pandas as pd, io
data = await erp_query_all("erp_trade_query", "order_list", {...})
df = pd.DataFrame(data["items"])
buf = io.BytesIO()
df.to_excel(buf, index=False)
result = await upload_file(buf.getvalue(), "报表.xlsx")
print(result)
```

### 6.5 前端改动（6 个文件）

#### messageUtils.ts — 新增 getFiles()

```typescript
export function getFiles(message: Message): FilePart[] {
  return message.content.filter(
    (p): p is FilePart => p.type === 'file' && !!p.url
  );
}
```

#### MessageItem.tsx — 提取文件并传给 MessageMedia

```typescript
const files = getFiles(message);
const hasFiles = files.length > 0;
// 传给 MessageMedia
<MessageMedia imageUrls={...} videoUrls={...} files={files} />
```

#### MessageMedia.tsx — 新增文件卡片渲染

```typescript
// 文件渲染分支（在图片/视频之后）
{files.length > 0 && files.map(file => (
  <FileCard
    key={file.url}
    file={file}
    onPreview={() => setPreviewFile(file)}
    onDownload={() => downloadFile(file.url, file.name)}
  />
))}

// 预览弹窗
{previewFile && (
  <FilePreviewModal
    file={previewFile}
    onClose={() => setPreviewFile(null)}
    onDownload={() => downloadFile(previewFile.url, previewFile.name)}
  />
)}
```

**FileCard 样式规格**：
- 圆角卡片，浅灰背景，hover 高亮
- 左侧：文件类型图标（xlsx=📊，pdf=📄，csv=📋，其他=📎）
- 中间：文件名 + 文件大小（如 "订单报表.xlsx · 128KB"）
- 右侧：[预览] + [下载↓] 两个按钮
- 可预览的类型显示"预览"按钮，不可预览的只显示"下载"

#### FilePreviewModal.tsx — 新建文件在线预览弹窗

参考 ImagePreviewModal 的全屏弹窗架构：

```
┌─────────────────────────────────────────────┐
│  📊 订单按店铺统计.xlsx        [下载] [✕]   │  ← 顶部工具栏
├─────────────────────────────────────────────┤
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │ 店铺    │ 订单数 │ 销量 │ 金额     │    │  ← 表格内容
│  │ 旗舰店  │ 3200  │ 6400│ ¥12,800  │    │
│  │ 专营店  │ 2100  │ 4200│ ¥8,400   │    │
│  │ ...     │       │     │          │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  Sheet1 │ Sheet2 │                          │  ← 多 sheet 切换
└─────────────────────────────────────────────┘
```

**支持的预览类型**：

| 文件类型 | 预览方式 | 依赖 |
|---------|---------|------|
| .xlsx/.xls | SheetJS 解析 → HTML 表格渲染 | 新增 xlsx 库 |
| .csv/.tsv | 文本解析 → HTML 表格渲染 | 无新增 |
| .json/.yaml/.xml | 代码高亮渲染 | 复用 highlight.js |
| .txt/.md/.log | 文本/Markdown 渲染 | 复用 react-markdown |
| .pdf | iframe 嵌入预览 | 无新增（浏览器原生支持） |
| 其他 | 不可预览，仅下载 | — |

**预览渲染器分离**：

```typescript
// 根据 MIME 类型选择渲染器
function FilePreviewContent({ file, data }: Props) {
  const ext = file.name.split('.').pop()?.toLowerCase();
  
  if (['xlsx', 'xls'].includes(ext))
    return <ExcelRenderer data={data} />;
  if (['csv', 'tsv'].includes(ext))
    return <CsvRenderer data={data} />;
  if (['json', 'yaml', 'xml'].includes(ext))
    return <CodeRenderer data={data} language={ext} />;
  if (['txt', 'md', 'log'].includes(ext))
    return <TextRenderer data={data} />;
  if (ext === 'pdf')
    return <iframe src={file.url} className="w-full h-full" />;
  
  return <div>此文件类型不支持预览，请下载后查看</div>;
}
```

#### downloadFile.ts — 新建通用下载函数

```typescript
export async function downloadFile(url: string, filename: string): Promise<void> {
  const response = await fetch(url, { mode: 'cors', credentials: 'omit' });
  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;  // 直接用原始文件名，不追加扩展名
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
}
```

---

## 7. 开发任务拆分

### 阶段 1：后端核心链路（沙盒 → OSS → 消息）

- [ ] 任务 1.1：sandbox/executor.py — `_ALLOWED_IMPORT_MODULES` 加 `"io"`
- [ ] 任务 1.2：sandbox/functions.py — 注册 `upload_file` 函数
- [ ] 任务 1.3：chat_tool_mixin.py — tool result 返回时提取 [FILE] 标记 → `_pending_file_parts`
- [ ] 任务 1.4：chat_handler.py — on_complete 合并 `_pending_file_parts` + `_convert_content_parts_to_dicts` 加 FilePart
- [ ] 任务 1.5：media_extractor.py — 新增 [FILE] 正则兜底解析

### 阶段 2：提示词（LLM 自动判断输出格式）

- [ ] 任务 2.1：erp_tools.py — ERP_ROUTING_PROMPT 追加输出格式选择规则
- [ ] 任务 2.2：code_tools.py — code_execute 描述加 upload_file 用法示例

### 阶段 3：前端展示（6 个文件）

- [ ] 任务 3.1：messageUtils.ts — 新增 `getFiles()` 函数
- [ ] 任务 3.2：downloadFile.ts — 新建通用文件下载函数
- [ ] 任务 3.3：MessageItem.tsx — 提取文件信息传给 MessageMedia
- [ ] 任务 3.4：MessageMedia.tsx — 新增 FileCard 文件卡片 + 预览弹窗入口
- [ ] 任务 3.5：FilePreviewModal.tsx — 新建文件在线预览弹窗（Excel/CSV/文本/代码/PDF）
- [ ] 任务 3.6：安装 xlsx（SheetJS）依赖用于 Excel 文件读取和表格渲染

### 阶段 4：测试

- [ ] 任务 4.1：后端单测（upload_file + [FILE] 解析 + FilePart 转换）
- [ ] 任务 4.2：前端单测（getFiles + FileCard 渲染）
- [ ] 任务 4.3：端到端测试（企微/Web 发"导出这个月订单Excel"）

---

## 8. 依赖变更

**后端**：无新增依赖
- pandas==2.2.3 ✅ 已安装
- openpyxl==3.1.5 ✅ 已安装
- oss2（阿里云 OSS SDK）✅ 已安装

**前端**：新增 1 个依赖
- `xlsx`（SheetJS）— 用于读取 Excel 文件并渲染为表格（~150KB gzipped）
- PDF 预览用浏览器原生 iframe，无需额外依赖

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| io 模块在沙盒白名单中被拦截 | 高 | 任务 1.1 加入白名单（**必须做，否则 BytesIO 不可用**） |
| [FILE] 标记被 LLM 改写/翻译 | 高 | 在 code_execute 层直接解析，不经过 LLM（任务 1.3） |
| LLM 不主动使用 upload_file | 中 | 提示词规则 + 工具描述示例双重引导 |
| 大文件上传超时（>50MB） | 低 | OSS SDK 内部重试，文档格式不会太大 |
| 前端 FilePart 渲染缺失 | 高 | 需改 4 个前端文件（任务 3.1-3.4） |
| 企微端文件下载 | 低 | CDN URL 公开链接，企微内置浏览器可打开/预览 |

---

## 10. 设计自检

- [x] 连锁修改已全部纳入任务拆分（7 个后端文件 + 6 个前端文件）
- [x] 12 个边界场景均有处理策略
- [x] 新增文件 2 个（downloadFile.ts + FilePreviewModal.tsx），其余修改现有文件
- [x] 后端无新增依赖，前端新增 xlsx（SheetJS）1 个
- [x] 复用现有 OSS / FilePart / media_extractor / sandbox 架构
- [x] 输出格式选择由提示词驱动（Claude/ChatGPT 模式），无硬编码阈值
- [x] LLM 自动判断能力通过提示词规则实现
- [x] [FILE] 标记在 code_execute 层解析，避免 LLM 改写风险
- [x] io 模块白名单问题已识别并纳入任务
