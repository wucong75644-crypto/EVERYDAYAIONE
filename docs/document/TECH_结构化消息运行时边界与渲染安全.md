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
- 不新增后端接口、数据库字段或持久化格式，部署和回滚不要求迁移。

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
