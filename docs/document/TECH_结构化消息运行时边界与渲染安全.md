# 结构化消息运行时边界与渲染安全

> 版本：v1.0｜日期：2026-07-16｜等级：A级｜状态：已实施

## 1. 问题与根因

结构化输出曾在 Markdown 代码块中显示为 `[object Object]`。直接原因是语法高亮插件先把源码转换成 React 节点，随后组件使用 `String(children)` 提取内容。根本原因是系统把“外部协议数据、可信状态、渲染节点”混在同一条链路中，并依赖 TypeScript 静态类型代替运行时校验。

## 2. 架构原则

1. HTTP、WebSocket、任务恢复数据全部视为 `unknown`。
2. 外部数据必须通过 Zod 协议边界后才能进入 Zustand Store。
3. Store 内部只保存 `ContentPart[]`，不保存渲染节点或高亮 HTML。
4. 原始文本是唯一可信源；高亮、复制、表格展示均为派生结果。
5. 单个非法内容块隔离，不得导致整条消息或页面崩溃。
6. 新增扩展字段通过 `.passthrough()` 保留，避免前后端灰度发布互相阻塞。

## 3. 数据链路

```text
HTTP 历史消息 ── normalizeMessage ─┐
WebSocket block ─ parseContentPart ─┼─> ContentPart[] ─> Zustand Store
任务恢复 ─────── parseContentParts ─┘                       │
                                                            v
                                             MessageContentBlocks
                                              ├─ Markdown raw text
                                              ├─ Table/Form
                                              └─ Media/Chart
```

### 3.1 入口规则

- `message_chunk`、`thinking_chunk`、`accumulated_content` 必须是字符串。
- 内容数组逐块校验；合法块保留，非法块丢弃并记录 `messageId`、`conversationId`、`source`。
- 历史异常 `{ type: "text", text: object }` 兼容恢复为格式化 JSON 文本，避免再次产生隐式对象字符串。
- 未知 `type` 不进入 Store，防止消费者收到不完整协议。

### 3.2 状态规则

- `appendContentBlock`、`restoreStreamingBlocks`、`replaceLastTextBlock` 使用明确的联合类型。
- `updateContentBlock` 只接受 `Partial<ToolStepPart>`。
- WebSocket 和 HTTP 恢复复用同一解析器，不维护两套兼容逻辑。

### 3.3 渲染规则

- Markdown 从 AST 文本节点递归提取源码，不对 React children 做字符串强转。
- 聊天正文遵守共享展示契约：Markdown 加上行内 `<span data-color="red">内容</span>`，颜色目录来自 `backend/config/message_presentation.json`，同一文件供系统提示词、工具说明、后端校验和前端渲染读取。AI 显式选择颜色，前端不从数值、箭头或业务词汇推断。
- `rehypeInlinePresentation` 只在段落、标题、列表项、表格单元格的行内 AST 中识别 span，利用已有 DOMPurify 解析属性，重新构造仅带受控颜色的节点；其余 HTML 继续转义。历史 `style="color:red"` 等目录内 CSS 颜色映射为相同能力，其余 CSS/属性不透传。代码示例、显式转义文字和完整 HTML 块仍原样显示。未闭合 span 不跨当前段落或单元格；KaTeX 在该转换之后渲染。
- 结构化表格的 `rows` 保留数值；可选 `cell_styles` 按行、列名传递 `{color?, bold?}`。沙盒要求样式行数与数据一致、列名存在；截断时同步截断样式。emit → 消息块 → Pydantic → 持久化/WS → Zod → TableBlock 全链路保留字段，颜色与正文共用目录和明暗模式。前端收到非法可选样式时保留原始数据、降级为默认样式。
- `CodeBlock.rawCode` 同时驱动语法高亮与剪贴板；highlight.js 输出只用于派生展示。
- `formatDisplayValue` 负责 Table、Spreadsheet、Chart 数据视图和工具确认参数中的未知值展示；循环引用使用明确占位文本。
- `formatFormValue` 只接受字符串、数字、布尔值；对象和数组不进入标量控件。

## 4. 失败、空值与降级

| 场景 | 行为 |
|------|------|
| 非字符串流式 chunk | 拒绝并记录警告，不污染缓冲区 |
| 内容数组中单块非法 | 丢弃该块，其余合法块继续显示 |
| 全部恢复块非法 | 回退到合法的累计纯文本 |
| 结构化 text | 序列化为可读 JSON 文本 |
| 高亮器失败或未知语言 | 直接渲染原始源码 |
| 表格值循环引用 | 显示“无法显示的结构化数据”，组件不崩溃 |
| 空值 | 渲染为空字符串 |

## 5. 安全与性能

- 不执行外部 HTML；代码中的 `<script>` 作为文本显示。
- highlight.js 返回值已转义后才通过 `dangerouslySetInnerHTML` 渲染，并有 XSS 回归测试。
- 协议解析与展示格式化均为线性处理；表格仍限制最多预览 200 行。
- `cell_styles` 是消息 JSON 的可选扩展字段；无数据库迁移，旧消息无需重写。旧客户端忽略该字段并保留数据，回滚后样式降级但数值不变。前后端应随同一候选部署。

## 6. 验证标准

- JSON 代码块不得出现 `[object Object]`，复制内容必须与原始源码完全一致。
- WebSocket、HTTP 历史消息、任务恢复和图片局部更新均不得绕过协议解析。
- 非法块不得写入 Store；合法扩展字段必须保留。
- Form、Table、Spreadsheet 不得使用对象隐式字符串化。
- TypeScript 构建、相关测试和完整前端回归必须通过。

## 7. 已知后续项

- 2026-07-17 已完成 Markdown、Form、Table、Spreadsheet、Chart 与工具确认弹窗覆盖补强，两组覆盖率均越过全局 80% 门槛。
- 已完成 streaming slice action factories、WS handler、Form 与 Spreadsheet 内部职责拆分；公开协议、Store shape 与组件入口保持兼容。
- 生产构建仍报告项目级大 chunk 警告，属于独立性能治理范围，不影响本次结构化消息正确性。

## 8. 2026-09-22 展示契约修复

首版只移除行内 span，避免标签外露却丢失颜色；随后本地箭头着色方案被用户否定，未发布且已移除。修复目标是 AI 声明的展示能力与实际渲染一致，不预设某个业务指标的颜色。

### 使用约定

- 颜色：`red / green / blue / orange / purple / gray`；精确色值由前端按明暗模式统一呈现。文本同时保留指标、数值、正负号和含义，不能仅依赖颜色。
- 正文：`<span data-color="blue">**需要关注**</span>`；不跨段落或单元格，不输出任意 HTML/CSS。源码放在行内代码或代码块中。
- 表格：`emit_table(rows, cell_styles=[{"涨跌幅": {"color": "green", "bold": True}}, {}])`，样式列表须与原始 rows 一一对应，无样式的行用空字典；旧调用不传字段继续有效。
- 不支持的颜色不进行“就近猜测”；正文保留内容并使用默认颜色，emit 输入错误提供具体校验错误供模型修正。
- JSON 颜色目录由后端部署目录托管，前端构建时直接导入并打包；不另存一份颜色目录或运行时远程拉取配置。

### 验证与发布

回归先复现正文及结构化表格缺失显式颜色（前端 2 项、后端 1 项失败），再验证受控颜色、相同数字不同颜色、历史标签、流式作用域、代码/公式兼容、注入属性过滤、截断对齐和消息持久化。验证结果与发布状态记录在 CURRENT_ISSUES；新契约尚需生产真实会话验收，不把静态提示词检查视作模型遵约保证。


本地最终验证：

- 前端 `npx vitest run --maxWorkers=4`：137 文件、1456 项通过；默认高并发两次运行分别出现 Skill 弹窗退出动画等待和 ECharts 动态导入重试等待失败。Skill 单文件 32 项通过；限制并发后全部通过，未修改相关业务或放宽断言。
- 后端提示词、工具目录、ERP Agent、emit 三入口、消息模型/转换、真实 kernel IPC 共 660 项通过；2 项仅适用于 Linux wqy 字体的测试在 macOS 跳过。
- 定向 ESLint、`tsc -b` 和 `npm run build` 通过；生产构建仍有既存大 chunk 提示。
- 浏览器使用实际后端序列化的消息，经过前端协议校验与真实 MarkdownRenderer/TableBlock，确认明暗模式下同样 +10% 可显式选择红/绿、未指定颜色保持默认、历史 span 与公式正常。
- 一次性预览文件、页面和服务已清理；未部署、未合并主分支、未修改生产消息。没有执行新增真实模型调用，提示词遵约率仍需生产会话复验。
