# 沙盒 IO 统一协议调研与设计

> **创建日期**：2026-06-06
> **背景**：Phase 1 路径协议重构后 ECharts 渲染断裂事故的根因复盘 — 当前下载卡片（`[FILE]` marker + 后端扫描）和图表渲染（`.echart.json` 文件约定）是两套独立链路，各自缺陷不同。本文调研行业 5 家方案后给出统一协议建议。
> **状态**：调研完成 + 推荐方案待评审，未实施

---

## 1. 现状摸底

### 1.1 下载卡片链路（完整）

```
LLM 写代码 df.to_excel('下载/x.xlsx')
  ↓
沙盒 (kernel_worker)  执行,写入文件到 host workspace_dir/下载/
  ↓
SandboxExecutor._auto_upload_new_files()  (executor.py:208)
  • 扫 output_dir 目录,对比 _snapshot_before {filename: (mtime, size)}
  • 检测新增/修改的文件
  • 后缀过滤:_AUTO_UPLOAD_EXTENSIONS (xlsx/csv/png/pdf/json/...)
  ↓
upload_fn 回调 — services/sandbox/functions.py:71 注入 services/file_upload.py:auto_upload
  • OSS sync 上传 → 拿 CDN URL
  • 拼字符串 marker (file_upload.py:71):
    "✅ 文件已生成: x.xlsx\n[FILE]https://cdn/.../x.xlsx|x.xlsx|application/xlsx|2048|上传/2026-06/x.xlsx[/FILE]"
  ↓
marker 被 append 到 AgentResult.summary 末尾,跟着工具结果走 LLM 消息流
  ↓
ToolLoopExecutor._FILE_RE 正则提取 (tool_loop_executor.py:33)
  • 解析 url/name/mime/size/workspace_path
  • 构造 FilePart 对象塞进 _pending_file_parts
  • 从文本中删除 [FILE]...[/FILE],替换为占位文字"文件已生成"
  ↓
ChatHandler 流式循环遍历 _pending_file_parts (chat_handler.py:703-756)
  • 按 mime/name 分流:image → image block, chart_options 命中 → chart block, 其他 → file block
  ↓
WebSocket 推送 content_block_add → 前端按 type 渲染
```

**关键文件**：
- [backend/services/sandbox/executor.py:208-259](backend/services/sandbox/executor.py#L208) `_auto_upload_new_files`
- [backend/services/sandbox/functions.py:71](backend/services/sandbox/functions.py#L71) `upload_fn` 注入
- [backend/services/file_upload.py:20-92](backend/services/file_upload.py#L20) `auto_upload` 实现
- [backend/services/agent/tool_loop_executor.py:31-35](backend/services/agent/tool_loop_executor.py#L31) marker 正则
- [backend/services/handlers/chat_handler.py:703-756](backend/services/handlers/chat_handler.py#L703) FilePart 处理循环

### 1.2 图表渲染链路（完整）

```
LLM 看 code_tools 提示词 "写图表配置: open('staging/x.echart.json', 'w')"
   (config/code_tools.py:43)
  ↓
LLM 写代码 with open('staging/trend.echart.json', 'w') as f: json.dump(option, f)
  ↓
沙盒 (kernel_worker) 执行,写入文件到 host staging_dir/trend.echart.json
  ↓
SandboxExecutor._scan_chart_options()  (executor.py:261)
  • 扫 staging_dir 目录里 *.echart.json
  • 大小过滤 ≤500KB
  • JSON 解析失败跳过
  • 存到 self._chart_options[filename] = option dict
  • 读完即 unlink (修了之前污染"下载/"目录的 bug)
  ↓
SandboxToolMixin._code_execute 透传到 host (sandbox_tool_mixin.py:94-97)
   executor._chart_options → self._chart_options
   (self 是 ToolExecutor 或 ChatToolMixin 通过 mixin 组合到 ChatHandler)
  ↓
ChatHandler 流式循环 (chat_handler.py:702, 719-730)
  ⚠️ 旧设计 bug:依赖 FilePart 触发
   • 遍历 _pending_file_parts:
     if fp.name in _chart_options:  ← 命中才生成 chart block
   • Phase 1 改 echart.json 走 staging 后,_pending_file_parts 没有对应 FilePart
   • → chart block 永远不会被生成 → 前端拿不到任何 type=chart 数据 → 柱形图不显示
  ↓
今天 hotfix 补丁 (未 commit):
   chart_block_builder.py + build_orphan_chart_blocks
   直接遍历 _chart_options 推 chart block,不依赖 FilePart
  ↓
WebSocket 推送 content_block_add (type=chart, option=ECharts JSON)
  ↓
前端 ChartBlock 组件 (ChartBlock.tsx) 收到 ChartPart 用 ECharts 渲染
```

**关键文件**：
- [backend/config/code_tools.py:43,105](backend/config/code_tools.py#L43) LLM 提示词约定路径
- [backend/services/sandbox/executor.py:261-294](backend/services/sandbox/executor.py#L261) `_scan_chart_options`
- [backend/services/agent/sandbox_tool_mixin.py:94-97](backend/services/agent/sandbox_tool_mixin.py#L94) 透传
- [backend/services/handlers/chat_handler.py:702,719-730](backend/services/handlers/chat_handler.py#L719) 旧 FilePart 触发逻辑
- [backend/services/handlers/chart_block_builder.py](backend/services/handlers/chart_block_builder.py) 今天 hotfix
- [frontend/src/types/message.ts:141](frontend/src/types/message.ts#L141) `ChartPart` schema
- [frontend/src/components/chat/message/ChartBlock.tsx](frontend/src/components/chat/message/ChartBlock.tsx) 前端渲染

### 1.3 两套架构的根本差异

| 维度 | 下载卡片 | 图表渲染 |
|---|---|---|
| 触发器 | `[FILE]...[/FILE]` 字符串 marker（注入 LLM 文本流） | 字典 `_chart_options`（透传到 host 内存） |
| 沙盒侧 | LLM 写到 `下载/`，后端**扫文件**生成 marker | LLM 写到 `staging/`，后端**扫文件**生成字典 |
| 主进程持有 | 无（marker 在 LLM 上下文里流动） | `self._chart_options` 字典 |
| 数据传输 | 字符串 marker → regex → FilePart | 字典 → ChatHandler 透传 |
| 流式协议 | 跟 LLM 文本流走，立刻可被前端解析 | 独立带外通道，在 stream 末尾推送 |
| 类型识别 | mime 字符串 + 扩展名 | dict key `name` + `.echart.json` 后缀 |
| 漏渲染失败模式 | OSS sync 失败 → marker 不生成 → 文本提示"文件处理失败" | FilePart 不存在 → 字典数据没人消费 → 静默丢失 |

**根本问题**：
1. **两套用了不同的"信号载体"**（字符串 marker vs 字典透传）
2. **chart 错误地把 FilePart 当触发器**——耦合了"产物落地通道"和"渲染触发"两个本该独立的概念
3. **沙盒侧 LLM 仍是"被动方"**——后端扫文件猜意图，LLM 不知道渲染发生在何时

---

## 2. 行业方案调研

### 2.1 Jupyter mime bundle（IPython display protocol）

**核心范式**：mime bundle dict `{mime_type: payload}` + `IPython.display.publish_display_data()`。Plotly / Altair / matplotlib_inline / Bokeh **全部建立在这套之上**。

**关键证据**：
- `display()` 实现 [IPython/core/display_functions.py:85](https://github.com/ipython/ipython/blob/main/IPython/core/display_functions.py#L85)
- `_repr_mimebundle_` hook 优先级 > 各 `_repr_*_` hook（[formatters.py:204-251](https://github.com/ipython/ipython/blob/main/IPython/core/formatters.py#L204)）
- 标准 mime 清单：`text/plain` `text/html` `image/png` `image/svg+xml` `application/json` 等
- 自定义 mime 不需要在 Python 侧注册，**前端识别即可**（Plotly 的 `application/vnd.plotly.v1+json` / Altair 的 `application/vnd.vegalite.v6+json`）
- 二进制（PNG）由 publisher base64 编码进 JSON content

**Python 侧 emit 范本**：
```python
# Plotly figure._repr_mimebundle_ 实现 (basedatatypes.py:830)
def _repr_mimebundle_(self, include=None, exclude=None, ...):
    return renderers._build_mime_bundle(fig_dict, renderer_str, ...)
# → {"application/vnd.plotly.v1+json": {...fig spec...}}
```

**matplotlib `post_execute` 自动 hook 范本**（行业里最优雅）：
- [matplotlib_inline/backend_inline.py:148](https://github.com/ipython/matplotlib-inline/blob/main/matplotlib_inline/backend_inline.py#L148) `shell.events.register("post_execute", flush_figures)`
- 每个 cell 执行完，遍历 active figures 自动 `display()`，用户不用写 `plt.show()`

**适配判断**（给我们）：
- 完整复用难度：**高**（需要 ZMQ + ipykernel + InteractiveShell）
- 简化版借鉴：**可行**——只取 mime bundle dict 结构 + `display()` 函数语义，沙盒里搞 20 行的 fake `IPython.display` 模块就能让 plotly/altair 无感工作
- 推荐保留 `_repr_mimebundle_` hook 协议作为对接点（未来扩 plotly 不用改架构）

---

### 2.2 OpenAI Code Interpreter

**核心范式**：沙盒里跑 Jupyter kernel，但**绕过 IPython rich display**，改用 OpenAI 内部库 `ace_tools` 的**显式 callback**。

**关键证据**：
- 系统 prompt 强制模型写 `ace_tools.display_dataframe_to_user(name, df)` / `display_chart_to_user(path, title, chart_type)` / `display_matplotlib_image_to_user(...)`（[asgeirtj/system_prompts_leaks tool-python.md](https://github.com/asgeirtj/system_prompts_leaks/blob/main/OpenAI/tool-python.md)，非官方）
- 沙盒内部署 FastAPI 服务负责文件 ID 双向传输 + `AsyncMultiKernelManager`（[Ryan Govostes 沙盒拆解](https://ryan.govost.es/2025/openai-code-interpreter/)，非官方）
- API 返回结构：
  ```json
  // Assistants API
  {"type": "image_file", "image_file": {"file_id": "file-abc123"}}
  {"type": "text", "text": {"value": "...sandbox:/mnt/data/x.csv...",
                            "annotations": [{"type": "file_path", "file_path": {"file_id": "..."}}]}}
  ```
- file_id 引用模式（不内联 base64，客户端再调 Files API 下载）

**适配判断**：
- `ace_tools` 显式 callback 思路适合我们——比扫文件猜意图更可控
- file_id 引用模式 ≈ 我们的 OSS CDN URL，思路对齐
- gVisor + Jupyter kernel 池在我们规模过度设计

---

### 2.3 Anthropic Claude code_execution + artifacts

**两套独立协议**，Claude.ai 产品 UI 用 artifacts（`<antArtifact type="..." ...>` XML），API 用 `code_execution` tool（`tool_result.content` 强类型）。

**关键证据**：
- 官方文档 [code-execution-tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool)
- file 输出 schema（SDK 类型）：
  ```python
  class BetaCodeExecutionOutputBlock:
      file_id: str
      type: Literal["code_execution_output"]
  ```
- **不内联 base64**，全部走 `file_id` 引用 + `client.beta.files.download(file_id)`
- 流式：`content_block_start` → `content_block_delta` → `content_block_stop`
- artifacts 协议（产品 UI，非 API）：MIME 类型 `application/vnd.ant.code` / `application/vnd.ant.mermaid` / `application/vnd.ant.react` 等，按"vendor + ant"命名规范

**适配判断**：
- `file_id` 引用模式跟我们 OSS URL 一致，方向正确
- artifacts XML 不通用、不稳定（依赖 system prompt 自律），**不建议借鉴**
- `tool_result.content` 强类型 schema 思路可学：与其传字符串 marker 不如直接传结构化 block

---

### 2.4 Google Gemini code_execution

**核心范式**：`Part` 类型联合（`executable_code` / `code_execution_result` / `inline_data` / `file_data`）+ `outcome` 枚举。

**关键证据**：
- [Gemini Code Execution docs](https://ai.google.dev/gemini-api/docs/code-execution)
- Part schema：
  ```json
  {"executable_code": {"id": "a1b2c3d4", "language": "PYTHON", "code": "..."}}
  {"code_execution_result": {"id": "a1b2c3d4", "outcome": "OUTCOME_OK", "output": "stdout text"}}
  {"inline_data": {"mime_type": "image/png", "data": "<base64>"}}
  ```
- matplotlib `plt.show()` 自动捕获 → 序列化 PNG → 直接进 response part（推断：沙盒层 hook）
- **`id` 字段配对**：`executable_code.id` ↔ `code_execution_result.id`，多轮跟踪
- 阈值：text/CSV 推荐 ~1-2MB 以内走 inline，超过走 file_data URI

**适配判断**（强烈推荐借鉴）：
- `id` 配对 + `outcome` 枚举 — 比我们靠 stderr 字符串判断成功失败更结构化
- 小图（<256KB PNG）inline 直返思路可学，省一次 OSS 上传
- 大数据继续走 staging URL，对齐我们 FileRef v7

---

### 2.5 国内代码解释器

| 厂商 | 协议 | 文件输出 |
|---|---|---|
| **阿里通义/Qwen-Agent** | `enable_code_interpreter` + Jupyter kernel | 静态 URL 服务暴露 |
| **百度文心** | 无开放 API | — |
| **智谱 GLM-4** | `tools=[{"type":"code_interpreter","sandbox":"auto"}]` | URL/file_id（推断） |

**共同模式**：沙盒写文件 → 后端扫目录 → 静态 URL/OSS 暴露 → URL 注入响应。**没有任一国内厂商公开使用 emit marker 模式**——我们 `[FILE]` marker 在国内同行里反而独特。

**适配判断**：
- Qwen-Agent 的"静态 URL 服务暴露沙盒文件" = 我们 auto_upload，方向对齐无需改
- 反过来证明我们 `[FILE]` marker **过度灵活**——LLM 漏掉 marker 就会丢文件，扫描兜底反而更稳

---

### 2.6 Plotly / Bokeh / matplotlib_inline / Altair auto-display

四家可视化库都建立在 IPython mime bundle 之上，分两种触发模型：

| 库 | 触发模型 | 关键代码 |
|---|---|---|
| Plotly | `fig.show()` 显式 + `_repr_mimebundle_` 隐式（Jupyter 自动） | [basedatatypes.py:3386](https://github.com/plotly/plotly.py/blob/master/plotly/basedatatypes.py#L3386), [basedatatypes.py:830](https://github.com/plotly/plotly.py/blob/master/plotly/basedatatypes.py#L830) |
| Bokeh | `show()` 显式 → `publish_display_data` push | [notebook.py:501-507](https://github.com/bokeh/bokeh/blob/main/src/bokeh/io/notebook.py#L501) |
| matplotlib_inline | `post_execute` 事件全自动（用户不写 show() 也 emit） | [backend_inline.py:148](https://github.com/ipython/matplotlib-inline/blob/main/matplotlib_inline/backend_inline.py#L148) |
| Altair | 纯 `_repr_mimebundle_`（无 show 接口） | [altair/utils/display.py:155-163](https://github.com/vega/altair/blob/main/altair/utils/display.py#L155) |

**最重要的发现**（Altair 跟我们 ECharts 同款思路）：
- Altair 不渲染图，只 emit `{spec, mime: "application/vnd.vegalite.v6+json"}`
- 前端 Vega-Lite JS 库识别 mime 后做渲染
- **零 PNG 二进制传输，体积小一两个数量级**

**核心 caveat**：所有这些库都依赖 `IPython.display.publish_display_data`。我们沙盒里只要注入一个"fake `IPython.display` 模块"（约 20 行），plotly/altair/bokeh 全部能**无感工作**。

---

### 2.7 横向对比表

| 维度 | Jupyter mime bundle | OpenAI `ace_tools` | Claude code_execution | Gemini Part | 国内 URL 模式 | 我们 `[FILE]`+`_chart_options` |
|---|---|---|---|---|---|---|
| 触发模型 | sandbox 显式 `display()` | sandbox 显式 callback | tool_result block | response Part | sandbox 写文件→后端扫描 | 后端扫描 + 字符串 marker |
| 数据形态 | mime bundle dict | image_file / file_path annotation | code_execution_output | inline_data / file_data | URL 字符串 | `[FILE]` marker + 字典 |
| 自定义类型扩展 | 自定义 mime（vendor+json） | 写新 callback | 新 content block type | 新 Part type | 自由（URL 后缀） | 改正则 + 改字典 |
| 大文件 | base64 inline 或外部 ref | file_id ref | file_id ref | inline_data + 阈值 + file_data ref | URL ref | URL ref |
| 多类型混排 | 多 mime bundle | 多 callback | 多 block | 多 Part | 多 URL marker | 多 marker + 多字典 |
| LLM 主动性 | 强（用户写代码） | 强（system prompt 强约束） | 弱（结果在 tool_result） | 中（plt.show 自动 hook） | 弱（写文件就行） | 弱（写文件就行） |
| 扩展新图表类型 | 加 mime 即可 | 加 callback | 加 block type | 加 part type | 加 URL 后缀 | 改协议 + 改前端 |

---

## 3. 候选方案设计

### 方案 A：完整 Jupyter mime bundle 协议（重）

让沙盒输出走完整 mime bundle 协议，自定义 `application/vnd.everydayai.{file,chart,table,...}+json`。

**架构**：
```
LLM 代码 → display(obj) 或 _repr_mimebundle_ →
  fake IPython.display.publish_display_data 拦截 →
  写 JSON-Line 到 stdout:{"marker":"DISPLAY", "data":{mime:payload}, "metadata":{}}  →
  主进程 stdout 解析器识别 marker →
  按 mime 路由 → 前端
```

**成本**：~3-4 人日
- sandbox 内 fake IPython.display 模块（20 行）
- monkey-patch matplotlib `post_execute` 等价物（30 行）
- 主进程 stdout 解析器扩展（50 行）
- 主进程→前端 block 路由（50 行）
- system prompt 改提示词（10 行）
- 守护测试（100 行）

**收益**：
- 接管 plotly / altair / bokeh / matplotlib 自动 emit（零业务代码）
- 加新图表类型只需扩 mime（前端注册新 renderer）
- 与 Jupyter 生态对齐，未来如果要做 notebook export 平滑

**风险**：
- mime bundle 嵌在 stdout JSON-Line，跟普通 print 输出共用通道——需要可靠 marker 分隔
- 大 PNG base64 占用上下文（缓解：>256KB 自动转 OSS URL）

---

### 方案 B：简化版 emit marker 统一协议（推荐）

**核心思想**：把现在两套（`[FILE]` 字符串 marker + `_chart_options` 字典透传）统一成**一套 marker 协议**，沙盒主动 print。

**协议格式**（保留人类可读 + 机器可解析）：
```
[EMIT]{"kind":"file","url":"...","name":"...","mime":"...","size":N,"workspace_path":"..."}[/EMIT]
[EMIT]{"kind":"chart","spec_format":"echarts","title":"...","option":{...}}[/EMIT]
[EMIT]{"kind":"table","columns":[...],"rows":[...]}[/EMIT]
[EMIT]{"kind":"image","url":"...","width":N,"height":N}[/EMIT]
```

**架构**：
```
LLM 代码 → 沙盒内 emit() 函数(注入 globals) →
  print [EMIT]{"kind":"...", ...}[/EMIT] →
  ToolLoopExecutor 单一正则解析 →
  按 kind 路由到对应 ContentBlock builder →
  ChatHandler 推 WS
```

**沙盒侧 emit 函数**（约 50 行）：
```python
def emit(kind: str, **fields):
    """统一产物 emit。kind ∈ {file, chart, table, image, html, markdown}"""
    payload = {"kind": kind, **fields}
    print(f"[EMIT]{json.dumps(payload, ensure_ascii=False)}[/EMIT]")

def emit_file(path: str, label: str = None):
    """文件下载卡片(沙盒里写完文件后调一次)"""
    name = os.path.basename(path)
    size = os.path.getsize(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # 上传 OSS / 拿 CDN URL(沙盒内有 helper)
    url = _upload_to_oss(path)
    emit("file", url=url, name=label or name, mime=mime, size=size)

def emit_chart(option: dict, title: str = None, format: str = "echarts"):
    """图表卡片(沙盒里构造 option dict 直接调)"""
    emit("chart", spec_format=format, title=title or "", option=option)
```

**对 LLM 的提示词**（提示词中 1 行 + 自动 hook 兜底）：
```python
# code_tools.py 提示词
"产物输出协议:沙盒里直接调 emit_file('下载/x.xlsx') / emit_chart(option, title='标题')\n"
"matplotlib/plotly/altair 调 fig.show() 自动 emit,不需要手动 emit_image。\n"
```

**自动 hook 兜底**（即使 LLM 忘了显式 emit）：
- matplotlib：注入自定义 backend 在 `plt.show()` 时自动 `emit_image(...)`
- plotly：注入 fake `IPython.display.publish_display_data` 把 `application/vnd.plotly.v1+json` 转 `emit_chart(format="plotly")`
- altair：同上 转 `emit_chart(format="vegalite")`

**主进程侧**（最小改动）：
```python
# tool_loop_executor.py 把 _FILE_RE 改成 _EMIT_RE
_EMIT_RE = re.compile(r"\[EMIT\](?P<payload>\{[^\[\]]+\})\[/EMIT\]")
# 解析 → payload = json.loads → 按 kind 分发

# 删除:
#   - executor._auto_upload_new_files 主动扫文件(改成兜底,LLM 漏 emit 时才扫)
#   - executor._scan_chart_options 整套
#   - chat_handler chart_options 透传链路
#   - chart_block_builder.py(今天的 hotfix)
```

**成本**：~2-3 人日
- 沙盒 emit 函数 + 自动 hook（150 行）
- 主进程统一正则 + block 路由（80 行）
- 删旧链路 + 清理过期代码（净 -300 行）
- LLM 提示词更新（20 行）
- 守护测试（200 行）

**收益**：
- 两套合一，一处协议、一种触发器
- LLM 主动声明意图（不再后端猜测）
- 加新产物类型只需扩 kind 枚举（前后端各加一个 renderer）
- 删掉今天的 hotfix（永久去掉技术债）

**风险**：
- LLM 漏调 emit_file → 文件还是被后端兜底扫描上传（用 system prompt 强约束 + 兜底）
- emit() 输出混在 stdout 里，跟 `print()` 共通道——marker 必须可靠分隔

---

### 方案 C：混合方案（务实折中）

**思路**：保留 `[FILE]` marker 现状（已稳定生产），**仅把 chart 从字典透传改成 marker emit**，让两套对齐到同一形态。

**架构**：
```
下载卡片:保留 [FILE]...[/FILE] 现状,不变
图表:沙盒主动 print [CHART]{json option}[/CHART] marker
   ToolLoopExecutor 新加一个正则 _CHART_RE
   解析 → ChartPart 塞 _pending_chart_parts(类似 _pending_file_parts)
   ChatHandler 推 chart block
```

**成本**：~1 人日
- 沙盒 emit_chart 函数（30 行）
- 主进程 _CHART_RE 正则 + ChartPart 处理（50 行）
- 删 _chart_options 字典链路（净 -100 行）
- 守护测试（80 行）

**收益**：
- 风险最小（动的代码少）
- 两套虽然没完全统一，但**信号形态都是 marker**（相比当前一套字符串一套字典已经统一了一层）
- 渐进式升级，下次有空再做方案 B 全统一

**缺点**：
- 没解决根本——`[FILE]` 仍依赖后端扫描兜底，不是 LLM 主动声明
- 协议表面统一了，本质仍是两套（一套靠 OSS URL，一套传 option JSON）

---

## 4. 推荐方案

### 推荐：**方案 B（简化版 emit marker 统一协议）**

**理由**：
1. **真正根治架构分裂**——下载卡片 + chart + 未来产物（table/html/markdown/...）走同一套协议
2. **跟行业模式对齐**——OpenAI `ace_tools` 显式 callback / Claude `tool_result.content` 强类型 / Gemini `Part` 联合类型，都是"沙盒主动声明 + 主进程结构化解析"
3. **保留扩展性**——自动 hook 让 plotly/altair 无感工作（mime bundle 借鉴）
4. **可直接删掉今天的 chart hotfix**（永久去技术债）
5. **工作量适中**（2-3 人日，对比方案 A 的 3-4 人日更务实）

### 实施步骤（11 步，分 3 个 PR）

**PR-1：沙盒侧 emit 协议建立（1 人日）**
1. 新建 `services/sandbox/emit_protocol.py` — emit() / emit_file() / emit_chart() / emit_image() / emit_table()
2. 沙盒 globals 注入 emit 系列函数
3. 注入 fake `IPython.display.publish_display_data`，让 plotly/altair `_repr_mimebundle_` 自动转 emit_chart
4. 注入 matplotlib 自定义 backend，让 `plt.show()` 自动转 emit_image
5. 单元测试：emit 函数 + 自动 hook 覆盖

**PR-2：主进程侧 marker 解析（0.5 人日）**
6. `tool_loop_executor.py` 把 `_FILE_RE` 升级为 `_EMIT_RE`，解析 `[EMIT]{json}[/EMIT]` 通用格式
7. 按 `kind` 字段分发到 `_pending_emit_parts`
8. `chat_handler.py` 流式循环按 `kind` 推对应 content block（file/chart/image/table）
9. 守护测试：每种 kind 的 emit → block 链路

**PR-3：旧链路删除 + 灰度上线（0.5 人日）**
10. 删 `chart_block_builder.py`（今天的 hotfix）
11. 删 `executor._scan_chart_options` 主动扫 staging 的逻辑（保留 `_auto_upload_new_files` 作为 LLM 漏 emit 时兜底，并加日志告警）

### 回滚预案

- 每个 PR 独立 commit
- PR-1 先上线（沙盒侧增强，不影响主进程，兼容旧 `[FILE]` marker）
- PR-2 上线后双轨运行 1-2 天（旧 `[FILE]`/`_chart_options` + 新 `[EMIT]` 并存）
- 监控 7 天确认无回归再走 PR-3 删除旧链路
- 任一 PR 出问题 → 单独回滚（git revert），不影响其他

### 灰度策略

- 不需要 feature flag（marker 协议本身向后兼容：旧 `[FILE]` marker 解析路径保留，只是新增 `[EMIT]` 路径）
- system prompt 改提示词后 LLM 自然走新路径
- 旧 marker 用户也能继续用（兜底扫描仍生效）

### 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM 漏调 emit_file 导致文件丢失 | 中 | 高 | 主进程保留 `_auto_upload_new_files` 兜底扫描 + 监控告警 |
| `[EMIT]{...}[/EMIT]` 跟正常 print 输出混淆 | 低 | 中 | marker 用稀有字符序列 + JSON payload 双验证 |
| 自动 hook（matplotlib/plotly）跟用户代码冲突 | 低 | 中 | 自动 hook 用 monkey-patch，可被用户 override |
| Phase 1 file_analyze 的路径协议跟 emit 协议有冲突 | 低 | 低 | emit 沿用相对路径（'下载/x.xlsx'），跟 Phase 1 一致 |

---

## 5. 未决问题（需要用户拍板）

1. **是否包含 PR-3 的"删除旧链路"？** — 推荐包含，否则技术债会一直在；但保留旧 `[FILE]` 作为兜底跑 1-2 周再删也行
2. **是否一次性把 table/html/markdown 类型都加进 emit 协议？** — 不一定要，按需扩展更稳；本期只加 `file`/`chart`/`image`/`table` 四种
3. **emit_file 在沙盒里上传 OSS 怎么做？** — 沙盒在 nsjail 里默认无网络，建议 emit_file 只产生 marker（含相对路径），主进程拿到 marker 后上传 OSS 拼 CDN URL；保持网络隔离
4. **要不要在沙盒里做 `plt.show()` 自动转 emit_image？** — 推荐做（matplotlib_inline 范本证明可行 30 行代码），但属于 nice-to-have，可放到 PR-1.5

---

## 6. 决策时间线

- 2026-06-06：调研完成（本文）
- 待用户评审：选择方案 A/B/C 或调整
- 评审通过后排进下个 sprint：约 2-3 人日实施

---

## 参考资料汇总

- Jupyter messaging protocol: https://jupyter-client.readthedocs.io/en/stable/messaging.html
- IPython display source: https://github.com/ipython/ipython/blob/main/IPython/core/display_functions.py
- IPython formatters: https://github.com/ipython/ipython/blob/main/IPython/core/formatters.py
- matplotlib_inline: https://github.com/ipython/matplotlib-inline/blob/main/matplotlib_inline/backend_inline.py
- Plotly renderers: https://github.com/plotly/plotly.py/blob/master/plotly/io/_renderers.py
- Plotly base renderers: https://github.com/plotly/plotly.py/blob/master/plotly/io/_base_renderers.py
- Altair display: https://github.com/vega/altair/blob/main/altair/utils/display.py
- Altair mimebundle: https://github.com/vega/altair/blob/main/altair/utils/mimebundle.py
- Bokeh notebook: https://github.com/bokeh/bokeh/blob/main/src/bokeh/io/notebook.py
- OpenAI Code Interpreter (docs): https://platform.openai.com/docs/assistants/tools/code-interpreter
- OpenAI sandbox 逆向（非官方）: https://ryan.govost.es/2025/openai-code-interpreter/
- ChatGPT system prompts leak（非官方）: https://github.com/asgeirtj/system_prompts_leaks/blob/main/OpenAI/tool-python.md
- Claude code_execution: https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool
- Claude messages-streaming: https://platform.claude.com/docs/en/api/messages-streaming
- Anthropic artifacts (非官方逆向): https://github.com/jujumilk3/leaked-system-prompts/blob/main/claude-artifacts_20240620.md
- Gemini code_execution: https://ai.google.dev/gemini-api/docs/code-execution
- Gemini cookbook: https://github.com/google-gemini/cookbook/blob/main/quickstarts/Code_Execution.ipynb
- 阿里百炼 Qwen: https://help.aliyun.com/zh/model-studio/qwen-code-interpreter
- Qwen-Agent code interpreter: https://deepwiki.com/QwenLM/Qwen-Agent/5.1-code-interpreter-tool
- 智谱 BigModel: https://docs.bigmodel.cn/cn/guide/tools/code-interpreter
