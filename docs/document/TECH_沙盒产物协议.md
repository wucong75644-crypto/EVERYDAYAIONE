# 沙盒产物协议 (Phase 2 三引擎全覆盖)

> 状态: 已实施 (2026-06-08)
> 适用: `backend/services/sandbox/`
> 替代: Phase 1 之前的"LLM 必须显式 emit"单引擎(反复踩雷)

---

## 核心原则

**LLM 任意写法都能让产物送达前端,不依赖 LLM 听话调 emit。**

对齐行业 (Anthropic Code Execution / OpenAI Code Interpreter / E2B):
- 行业 0 家要求 LLM 显式 emit
- Anthropic = runtime 写盘 hook 全量回填
- OpenAI / E2B = Jupyter mimebundle + 文件 annotation 双轨

---

## 三引擎架构

```
┌──────────────────────────────────────────────────────────┐
│ Engine A: Jupyter mimebundle 协议 (emit_auto_hooks.py)    │
│   • IPython.display.publish_display_data hook            │
│   • _hooked_display 处理所有 _repr_*_                     │
│     - _repr_mimebundle_ (plotly v4+/altair 富对象)        │
│     - _repr_html_ (pandas DataFrame)                     │
│     - _repr_png_ / _repr_jpeg_ (PIL.Image)                │
│     - _repr_svg_ / _repr_json_ / _repr_markdown_         │
│   • cell 末尾表达式自动 display(对齐 Jupyter InteractiveShell)│
│   • plt.show / fig.show / Chart.show hook                │
│   → 解决: df.head() / fig / im / plt.show / fig.show 等   │
├──────────────────────────────────────────────────────────┤
│ Engine B: Runtime 写盘 diff (executor._auto_emit_missed)  │
│   • 执行前 snapshot output_dir (mtime, size)              │
│   • 执行后 diff 新增/修改文件                              │
│   • 按扩展名分类 image (png/jpg/svg/gif/webp) vs file     │
│   • 自动构造 payload + 上传 OSS + auto_detected=true 标签 │
│   • 去重: 跳过 LLM 已 emit 的文件名                        │
│   → 解决: plt.savefig / df.to_excel / Image.save /        │
│          任何写到 下载/ 的文件                             │
├──────────────────────────────────────────────────────────┤
│ Engine C: LLM 显式 emit (emit_protocol.py)                │
│   • emit_chart(option, title)                            │
│   • emit_file(path, label)                               │
│   • emit_image(path, alt)                                │
│   • emit_table(df, title)                                │
│   → 优势: LLM 可自定义 title/label/alt 优化渲染            │
└──────────────────────────────────────────────────────────┘
```

## 统一 mimetype → kind 配置中心

`emit_auto_hooks._mimebundle_to_payload` 单点配置:

| mimetype | kind | spec_format / 内容 | 前端渲染 |
|----------|------|------------------|---------|
| `application/vnd.plotly.v1+json` | `chart` | `spec_format=plotly` | plotly.js |
| `application/vnd.vegalite.v*+json` | `chart` | `spec_format=vegalite` | vega-embed |
| `image/png` | `image` | 写到 output_dir + OSS | `<img>` 缩略图 |
| `image/jpeg` | `image` | 同上 | 同上 |
| `image/svg+xml` | `image` | 内联 SVG 字符串 | SVG 渲染 |
| `text/html` | `html` | 内联 HTML(DataFrame 用) | `iframe` 或 sanitized html |
| `application/json` | `data` | 结构化 JSON | JSON 卡片 |
| `text/markdown` | `markdown` | Markdown 文本 | Markdown 渲染 |
| 其他扩展名 | `file` | 通用下载 | FileCard |

加新 mimetype 只需在 `_mimebundle_to_payload` 加一行,前端按 kind 自动分发。

## 全场景覆盖矩阵 (13 个)

| # | LLM 写法 | 引擎 | 状态 |
|---|---------|------|------|
| 1 | `emit_image('下载/x.png')` | C | ✅ |
| 2 | `emit_chart(echarts_opt, title='...')` | C | ✅ |
| 3 | `emit_file('下载/x.xlsx', label='...')` | C | ✅ |
| 4 | `emit_table(df, title='...')` | C | ✅ |
| 5 | `plt.show()` | A (matplotlib hook) | ✅ |
| 6 | `fig.show()` (plotly) | A (mimebundle) | ✅ (前端需 Phase 2e plotly.js) |
| 7 | `Chart.show()` (altair) | A (mimebundle) | ✅ (前端需 Phase 2e vega-embed) |
| 8 | `display(obj)` (任意富对象) | A (_hooked_display) | ✅ |
| 9 | `df.head()` cell 末尾 | A (last_expr + _repr_html_) | ✅ |
| 10 | `fig` cell 末尾 | A (last_expr + _repr_mimebundle_) | ✅ |
| 11 | `Image.open(...)` cell 末尾 | A (last_expr + _repr_png_) | ✅ |
| 12 | `plt.savefig('下载/x.png')` | B (写盘 diff) | ✅ |
| 13 | `df.to_excel('下载/x.xlsx')` / 任意写盘 | B | ✅ |

## 去重逻辑

- **同一文件 LLM emit + 自动 detect 都触发** → 以 LLM emit 为准 (保留 title/label)
- 去重 key: `os.path.basename(path)` (LLM emit 用相对路径,自动 detect 用绝对路径)
- 自动 detect 的 payload 加 `auto_detected=true` 标签(开发者调试用)

## 边界

- **只扫 output_dir (`下载/`)** ,staging 中间产物不会被错误上传
- 自动 detect 触发 OSS 上传失败时,payload 仍然加入(不丢)但 url 为空(前端可降级显示)
- cell 最后一行表达式自动 display 仅在值非 None 时触发(对齐 IPython)
- 内联 image bytes (`_repr_png_/_repr_jpeg_`) 写到 `output_dir/display_N.{png|jpg}` 再走 OSS

## 关键文件

| 文件 | 职责 |
|------|------|
| `services/sandbox/executor.py` | Engine B + C 聚合,`_auto_emit_missed` / `_upload_payload_files` / `_parse_emit` |
| `services/sandbox/emit_auto_hooks.py` | Engine A,`_mimebundle_to_payload` 配置中心,`_hooked_publish` / `_hooked_display` / matplotlib hook |
| `services/sandbox/emit_protocol.py` | Engine C,emit_chart/file/image/table 函数定义 |
| `services/sandbox/sandbox_worker.py::_exec_code` | cell 末尾表达式自动 display 入口 |
| `frontend/src/components/chat/message/ChartBlock.tsx` | 按 `spec_format` 分发 ECharts / plotly / vegalite |

## 测试

`backend/tests/test_emit_three_engines.py` 守护 13 个场景。

## 历史教训

| 时间 | bug | 根因 |
|------|-----|------|
| Phase 1 前 | matplotlib 字体被路径白名单拒 | Python 层白名单反模式 → Phase 1 删 |
| Phase 2 前 | LLM 写 `plt.savefig` 不预览 | 只 hook plt.show,没 hook savefig 写盘 |
| Phase 2 前 | LLM 写 `fig.show()` plotly 显示失败 | 前端 ChartBlock 只支持 ECharts |
| Phase 2 前 | LLM 写 `df.head()` cell 末尾不显示表格 | 没用 IPython.display.display |
| **未来** | **任意同类 bug** | **三引擎全覆盖根治,不再发生** |
