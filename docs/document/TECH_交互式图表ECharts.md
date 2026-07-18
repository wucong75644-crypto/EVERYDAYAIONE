# ECharts 数据图表与 Mermaid 关系图渲染架构

> **版本**：v2.0 | **状态**：已实现 | **更新日期**：2026-07-18

> v2.0 说明：现行链路已改为沙盒 `emit_*` 结构化协议。本文第 2～13
> 节保留 v1.0 的 `.echart.json` 方案作为历史背景，不再代表生产实现。

## v2.0 现行架构

### 职责与模型选择

- 数值、趋势、统计、分类比较和占比使用 `emit_chart(ECharts option)`。
- 流程、状态、时序、类图、调用关系和甘特图使用
  `emit_diagram(Mermaid source)`。
- 普通文字足够清楚时不生成图形，同一内容不得同时生成两种图形。
- Plotly/Vega-Lite 仅保留历史只读兼容，模型提示词不再引导新消息生成。

### 结构化协议

```text
LLM → code_execute → emit_chart / emit_diagram
  → sandbox emit_payloads
  → build_block_from_payload
  → WebSocket 流式块 + GenerationOutcome
  → ContentPart 持久化与任务恢复
  → 前端 Zod 运行时校验
  → MessageContentBlocks 分发
```

数据图表使用 `ChartPart`：

```json
{"type":"chart","spec_format":"echarts","title":"销售趋势","option":{}}
```

关系图使用 `DiagramPart`，原始 `source` 是唯一可信数据：

```json
{"type":"diagram","format":"mermaid","title":"订单流程","source":"flowchart TD\nA-->B"}
```

### 前端组件与状态

```text
MessageContentBlocks
├── ChartBlock
│   ├── EChartsRenderer
│   └── PlotlyBlock / VegaLiteBlock（历史只读）
└── DiagramBlock
    └── MermaidRenderer
```

两种正式渲染器统一遵循
`idle → loading → ready / error → fallback`。ECharts 失败展示格式化
JSON/数据视图，Mermaid 失败展示原始 DSL；动态 Chunk 加载失败允许重试。
Effect cleanup 和请求序列隔离保证卸载或快速切换后旧结果不会覆盖新状态。

### 按需加载与安全

- ECharts 和 Mermaid 分别由独立动态 import 入口加载，普通文本聊天不加载二者。
- `echartsThemes.ts` 只提供主题数据和注册回调，不再自行导入 ECharts。
- Mermaid 使用 `securityLevel: strict`、关闭 HTML label，并经 DOMPurify SVG
  profile 清理；脚本、外部资源、链接、事件属性和 `foreignObject` 不进入 DOM。
- ECharts option 来自 JSON 协议，不接收可执行函数；字符串 formatter 仅使用
  ECharts 模板语法。
- 错误日志只记录 message ID、内容类型、渲染器、错误类型和源码长度，
  不记录 option、Mermaid DSL 或解析异常正文。

### 历史与渠道兼容

- Markdown `mermaid` 代码块继续经 `MarkdownRenderer → MermaidBlock` 读取，
  但不再作为新消息正式协议。
- 未知 chart 格式恢复为可读 JSON，不因收紧枚举导致历史消息丢失。
- 企业微信不运行浏览器渲染器：chart 降级为格式化 JSON，diagram 降级为
  原始 Mermaid 源码。
- 无数据库迁移，不批量改写生产历史消息。

## 概述

LLM 在沙盒 `code_execute` 中生成 ECharts JSON 配置（`.echart.json`），前端用 ECharts 渲染交互式图表，完全替代 matplotlib 静态 PNG。

**核心改动**：不改沙盒架构，只改"LLM 输出什么"和"前端怎么渲染"。

---

## 1. 需求确认

| # | 决策 | 结论 |
|---|---|---|
| 1 | 生成方式 | 沙盒 code_execute 输出 `.echart.json`，复用现有链路 |
| 2 | 图表类型 | 不限制，提示词提供选型参考知识 + 反模式护栏 |
| 3 | 数据来源 | ERP Agent 查询结果 + 工作区文件 |
| 4 | 交互深度 | ECharts toolbox 全开（tooltip/图例/缩放/导出/类型切换/全屏） |
| 5 | matplotlib | 完全替代，不再引导使用 |
| 6 | 主题适配 | 6 套主题跟随（classic/claude/linear × light/dark） |

## 2. 现有链路

```
LLM → code_execute(code="plt.savefig(OUTPUT_DIR + '/图.png')")
  → SandboxExecutor(spawn 子进程) → 生成 PNG
  → _auto_upload_new_files() → CDN 上传 + PIL 读宽高
  → [FILE]url|name|image/png|size[/FILE]
  → _extract_file_parts() → FilePart 暂存
  → chat_handler L792: {"type":"image","url":...,"width":...,"height":...}
  → content_block_add WS 推送 → 前端 InlineChartImage 渲染静态图
```

## 3. 新链路

```
LLM → code_execute(code="json.dump(echarts_config, open(OUTPUT_DIR+'/图.echart.json','w'))")
  → SandboxExecutor(spawn 子进程) → 生成 .echart.json
  → _auto_upload_new_files() → 检测 .echart.json → 读取 JSON 内容 → CDN 上传
  → [CHART]json_content|title|chart_type[/CHART]
  → _extract_file_parts() → ChartPart 暂存
  → chat_handler: {"type":"chart","option":{...},"title":"..."}
  → content_block_add WS 推送 → 前端 ChartBlock ECharts 渲染交互式图表
```

**关键差异**：
- `.echart.json` 后缀触发 chart 链路（非 file 链路）
- 后端读取 JSON 内容嵌入 block（方案 A），前端零延迟渲染，不依赖 CDN
- 历史消息从 DB JSONB 直接获取 option，无需额外网络请求

## 4. 方案选择

| 维度 | 方案 A：嵌入 option（✅ 选定） | 方案 B：URL 引用 |
|---|---|---|
| 首次渲染 | 零延迟 | 需等 CDN fetch 100-300ms |
| 历史加载 | DB 直读，零请求 | 每次需 fetch CDN |
| DB 存储 | +5-50KB/图表 | +100B/图表 |
| CDN 依赖 | 不依赖 | CDN 故障则图表丢失 |
| 超大 JSON | 500KB 上限，超限降级 file block | 无此问题 |

## 5. 类型定义

### 后端 ChartPart（schemas/message.py）

```python
class ChartPart(BaseModel):
    type: Literal["chart"] = "chart"
    option: Dict[str, Any]           # ECharts option 配置
    title: str = ""                  # 图表标题
    chart_type: str = ""             # 类型标识（line/bar/pie，日志用）
```

### 前端 ChartPart（types/message.ts）

```typescript
export interface ChartPart {
  type: 'chart';
  option: Record<string, unknown>;
  title?: string;
  chart_type?: string;
}
```

## 6. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|---|---|---|
| ECharts JSON 格式无效 | try-catch init，显示错误卡片 + 原始 JSON | ChartBlock |
| JSON 体积 >500KB | 后端降级为 file block（下载 JSON 文件） | executor.py |
| ECharts 库加载失败 | 动态 import catch，显示降级卡片 | ChartBlock |
| 多个图表并发 | 独立 block + 独立实例，互不干扰 | 无需额外处理 |
| 窗口 resize | ResizeObserver → echarts.resize() | ChartBlock |
| 主题切换 | dispose() + init(dom, newTheme) | ChartBlock |
| LLM 仍生成 matplotlib PNG | 现有 image block 链路不受影响（向后兼容） | 无需处理 |
| 全屏模式 | position:fixed 铺满视口，ESC 退出 | ChartBlock |

## 7. 连锁修改清单

| 改动点 | 影响文件 | 同步修改 |
|---|---|---|
| 新增 ChartPart schema | schemas/message.py | 加入 ContentPart 联合 |
| 新增 ChartPart interface | types/message.ts | 加入 ContentPart 联合 |
| .echart.json 检测 | executor.py | _auto_upload_new_files() 读内容 |
| chart 占位文本 | chat_tool_mixin.py | _extract_file_parts() |
| chart block 构造 | chat_handler.py L792 | 新增分支 |
| chart 渲染分支 | MessageItem.tsx L500 | 新增 type=chart |
| 提示词替换 | code_tools.py, chat_tools.py | ⚠️ 用户单独审核 |
| ECharts 主题 | constants/echartsThemes.ts | 新建 |
| ECharts 依赖 | package.json | echarts==5.6.0 |

## 8. 文件结构

### 新增文件

| 文件 | 职责 | 预估行数 |
|---|---|---|
| `frontend/src/components/chat/message/ChartBlock.tsx` | ECharts 渲染组件 | ~200 |
| `frontend/src/constants/echartsThemes.ts` | 6 套主题配置 | ~300 |

### 修改文件

| 文件 | 修改内容 | 预估改动 |
|---|---|---|
| `backend/schemas/message.py` | +ChartPart | +15 行 |
| `frontend/src/types/message.ts` | +ChartPart | +10 行 |
| `backend/services/sandbox/executor.py` | .echart.json 检测+读内容 | +20 行 |
| `backend/services/handlers/chat_tool_mixin.py` | chart 占位文本 | +5 行 |
| `backend/services/handlers/chat_handler.py` | chart block 构造 | +15 行 |
| `frontend/src/components/chat/message/MessageItem.tsx` | chart 渲染分支 | +5 行 |
| `backend/config/code_tools.py` | 提示词（⚠️ 用户审核） | ~20 行改 |
| `backend/config/chat_tools.py` | 提示词（⚠️ 用户审核） | ~10 行改 |
| `frontend/package.json` | +echarts | +1 行 |

## 9. 提示词改造（⚠️ 待用户审核）

### 图表选择参考知识

```
## 图表选择参考（自动选择，不需要用户指定）

根据数据特征自动选择最合适的图表：
- 时间 + 数值 → line
- 时间 + 多组数值 → multi-line（按类别分色）
- 分类 + 数值 → bar（长标签用横向 bar）
- 比例数据（≤6类）→ pie/donut
- 两个数值变量 → scatter
- 分布分析 → histogram / boxplot
- 两个分类 + 数值 → heatmap / grouped bar
- 层级分类 → treemap
- 转化漏斗 → funnel
- 多维评分 → radar

禁止项：
- 饼图不超过 6 个分类，超过改用 bar 并按值排序
- 不用 3D 图表
- 不用双 Y 轴，改用两个独立图表
- 散点图 >5000 点改用 heatmap
- 柱状图 Y 轴必须从 0 开始
- 分类无自然顺序时按值降序排列
```

### code_execute 图表输出指引

```
图表输出用 ECharts JSON 配置：
  import json
  option = {"title":{"text":"标题"}, "xAxis":{...}, "series":[...]}
  with open(OUTPUT_DIR + '/图表名.echart.json', 'w') as f:
      json.dump(option, f, ensure_ascii=False)
ECharts option 规范参考: https://echarts.apache.org/en/option.html
不要用 plt / matplotlib，平台已替换为前端交互式图表。
```

## 10. 开发任务拆分

### Phase 1：后端链路
- [ ] 1.1 schemas/message.py — ChartPart + ContentPart
- [ ] 1.2 executor.py — .echart.json 检测 + 读内容 + [CHART] 标记
- [ ] 1.3 chat_tool_mixin.py — chart 提取逻辑
- [ ] 1.4 chat_handler.py — chart block 构造 + WS 推送
- [ ] 1.5 后端单元测试

### Phase 2：提示词改造（⚠️ 用户单独审核）
- [ ] 2.1 code_tools.py — CODE_ROUTING_PROMPT
- [ ] 2.2 chat_tools.py — TOOL_SYSTEM_PROMPT
- [ ] 2.3 图表选择参考 + 反模式护栏

### Phase 3：前端渲染
- [ ] 3.1 安装 echarts
- [ ] 3.2 types/message.ts — ChartPart
- [ ] 3.3 constants/echartsThemes.ts — 6 套主题
- [ ] 3.4 ChartBlock.tsx — 渲染组件
- [ ] 3.5 MessageItem.tsx — chart 分支

### Phase 4：集成测试 + 文档
- [ ] 4.1 端到端测试
- [ ] 4.2 主题切换测试
- [ ] 4.3 更新 PROJECT_OVERVIEW.md / FUNCTION_INDEX.md

## 11. 依赖变更

| 包 | 版本 | 理由 |
|---|---|---|
| echarts | 5.6.0 | 前端图表引擎，按需引入控制体积 |

不使用 echarts-for-react，直接用 echarts API + React ref/useEffect。

## 12. 部署与回滚

- **数据库迁移**：无
- **API 兼容**：完全向后兼容
- **回滚**：git revert + 提示词恢复 matplotlib

## 13. 风险评估

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| ECharts option 嵌入 JS 函数（XSS） | 中 | json.load() / JSON.parse() 自动拒绝函数 |
| ECharts 包体积 ~800KB | 中 | 按需引入 + 动态 import，首次图表时才加载 |
| DeepSeek V4 生成无效 JSON | 中 | try-catch + 错误卡片 + 提示词护栏 |
| 超大 option JSON 膨胀 DB | 低 | 500KB 上限，超限降级 file block |
