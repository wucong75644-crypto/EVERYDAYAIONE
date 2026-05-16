# 技术设计：Excel 文件自动预处理

> 版本：v1.0  
> 日期：2026-05-16  
> 前置：需求确认——消息发送时自动触发 prescan，同步阻塞，用户可见进度

---

## 1. 项目上下文

### 架构现状
- 当前文件处理是**被动式**：用户上传 → AI 自己调 `file_search` 找文件 → 自己在 `code_execute` 里写 openpyxl 代码读取
- prescan → parquet 管道已完整实现（`file_prescan.py` + `data_query_cache.py` + `excel_cleaner.py`），但**只在特定代码路径中触发**，未接入消息发送入口
- `file_processor.py` 的 L1→L2→L3 三层管道完整但**无调用方**

### 可复用模块
- `ensure_parquet_cache()`：完整的 prescan + 转换 + 缓存链路
- `generate_file_meta()` + `format_file_view()`：结构化元数据生成 + 格式化展示
- `file_processor.process_file()`：L1→L2→L3 完整管道（含 prescan）
- `_build_workspace_prompt()`：已有注入点，只需增强内容

### 设计约束
- 必须**同步阻塞**——AI 必须等预处理完成才开始生成
- 必须**用户可见**——通过 WebSocket 推送预处理进度
- 必须**不影响非文件消息**——没有 Excel/CSV 附件时零开销
- 必须兼容现有的 snapshot 缓存——同文件不重复处理

### 潜在冲突
- 无（prescan off-by-one 已修复，现有测试 6957 全绿）

---

## 2. 核心设计：消息发送时自动预处理

### 数据流

```
用户点击发送（带 Excel 附件）
    │
    ▼
ChatHandler.start()
    │
    ▼
_stream_generate() 开始
    │
    ├─ ① WS 推送 message_start
    │
    ├─ ② 【新增】检测 content 中的 Excel/CSV 附件
    │   │
    │   └─ 有附件 → _auto_preprocess_files()
    │       │
    │       ├─ WS 推送 thinking_chunk「正在解析表格结构...」
    │       │
    │       ├─ 调用 ensure_parquet_cache()（含 AI prescan）
    │       │   ├─ prescan（~2-4s）
    │       │   ├─ Excel → Parquet 转换
    │       │   └─ 生成 file_meta（列名、类型、行数、特殊行）
    │       │
    │       ├─ WS 推送 thinking_chunk「表格解析完成：27列 × 1171行」
    │       │
    │       └─ 返回 prescan_results[]
    │
    ├─ ③ _build_llm_messages()
    │   │
    │   └─ _build_workspace_prompt()【增强】
    │       │
    │       └─ 注入文件元数据（列名、数据类型、行数、表头位置）
    │           而不是只注入「'file.xlsx' (1.2 MB)」
    │
    ├─ ④ LLM 首次调用（AI 已知表格完整结构）
    │   │
    │   └─ AI 直接用 DuckDB SQL 查询 parquet
    │       不需要再调 file_search / 自己 openpyxl 读
    │
    └─ ⑤ 后续工具循环...
```

### 注入点：`chat_handler.py:_stream_generate()`

在 `message_start` 推送后、`_build_llm_messages()` 之前，插入预处理逻辑：

```python
# chat_handler.py:_stream_generate() 中，约 line 165 之后

# ② 【新增】自动预处理 Excel/CSV 附件
preprocess_results = await self._auto_preprocess_files(
    content=content,
    conversation_id=conversation_id,
    user_id=user_id,
    task_id=task_id,
)
```

---

## 3. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|----------|----------|
| 无文件附件 | 跳过预处理，零开销 | `_auto_preprocess_files` |
| 非 Excel/CSV 文件（图片/PDF） | 跳过，只处理表格类 | MIME 类型过滤 |
| 文件已有 parquet 缓存 | snapshot 命中直接返回（<1ms） | `ensure_parquet_cache` |
| prescan AI 超时 | prescan 内部已有降级（qwen-turbo 超时→代码检测），转换继续 | `file_prescan.py:168-170` |
| 大文件（50万行） | 不设超时，持续推送进度，用户等解析完成 | 分块转换 + thinking 进度 |
| 文件损坏/无法读取 | 注入错误信息，AI 告知用户 | ValueError 捕获 |
| 多个文件同时附加 | 并行 prescan，`asyncio.gather` | `_auto_preprocess_files` |
| 空文件 | 捕获已有的 ValueError 提示 | `ensure_parquet_cache` 空文件检测 |
| 50万行大文件 | 分块转换（已有），prescan 采样不受影响 | `_CHUNK_THRESHOLD` |

---

## 4. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|--------|----------|-------------|
| 新增 `_auto_preprocess_files()` | `chat_handler.py` | 在 `_stream_generate` 中调用 |
| 增强 `_build_workspace_prompt()` | `chat_context_mixin.py` | 接收 prescan_results 参数，注入元数据 |
| `_build_llm_messages()` 增加参数 | `chat_context_mixin.py` | 传递 preprocess_results |
| 创建 `skills/excel.md`（可选） | `skills/` | 沙盒内的 Excel 处理指南 |

---

## 5. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|:--------:|----------|
| 模块边界 | 复用已有模块，无新模块 | 低 | 仅在 chat_handler 加编排逻辑 |
| 数据流向 | 与现有 prescan→parquet 流向一致 | 低 | 无新数据流 |
| 扩展性 | prescan 采样固定 50 行，大文件不受影响 | 低 | 分块转换已有 |
| 耦合度 | chat_handler → data_query_cache，已有依赖 | 低 | 通过 process_file 封装 |
| 一致性 | 与现有 thinking chunk 推送模式一致 | 低 | 复用 build_thinking_chunk |
| 可观测性 | prescan 已有 logger.info 日志 | 低 | 增加耗时统计 |
| 可回滚性 | 功能开关 `auto_preprocess_enabled` | 低 | 关闭后回退到 AI 自行读取 |

---

## 6. 方案对比

| 维度 | 方案A：thinking 状态展示 | 方案B：独立工具步骤 |
|------|------------------------|-------------------|
| 实现思路 | 预处理过程通过 thinking_chunk 推送，前端显示在思考区域 | 预处理作为独立 ToolStepPart，前端渲染为工具调用卡片 |
| 前端改动 | 无（thinking_chunk 已支持） | 需新增 preprocess step 渲染组件 |
| 用户体验 | 思考气泡中显示"正在解析表格..."，自然融入 | 独立卡片，更清晰但多一个 UI 元素 |
| 后端改动 | 3 个文件，~80 行 | 5+ 个文件，~150 行 |
| 对现有代码侵入性 | 低（复用 thinking_chunk 通道） | 中（新增 content block 类型） |
| 后续升级 | 可以后续升级到方案B | - |

**推荐：方案A**。理由：零前端改动，复用已有 thinking_chunk WebSocket 通道，3 天内可上线。用户在思考状态中能看到"正在解析表格结构..."进度。

---

## 7. 技术栈

沿用现有：
- 后端：Python 3 + FastAPI + asyncio
- 预处理：fastexcel + openpyxl + pandas + pyarrow
- AI prescan：qwen-turbo（已有）
- WebSocket：现有 ws_manager 推送

无需新增依赖。

---

## 8. 文件结构

### 修改文件

| 文件 | 改动内容 | 预估行数 |
|------|----------|:--------:|
| `services/handlers/chat_handler.py` | 新增 `_auto_preprocess_files()` + 调用 | +40 |
| `services/handlers/chat_context_mixin.py` | 增强 `_build_workspace_prompt()` + `_build_llm_messages()` 参数 | +30 |
| `core/config.py` | 新增 `auto_preprocess_enabled: bool = True` | +1 |

### 新增文件

无。

---

## 9. 数据库设计

无需新增表/字段。元数据复用已有的 `.meta.json` 文件缓存机制。

---

## 10. API 设计

无新增 API。改动在 WebSocket 推送通道（已有格式）。

---

## 11. 核心代码设计

### 11.1 `_auto_preprocess_files()` 方法

```python
# chat_handler.py 新增方法

_PREPROCESS_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.ms-excel",  # xls
    "text/csv",
    "text/tab-separated-values",  # tsv
}
_PREPROCESS_TIMEOUT = 15  # 秒

async def _auto_preprocess_files(
    self,
    content: List[ContentPart],
    conversation_id: str,
    user_id: str,
    task_id: str,
) -> List[Dict[str, Any]]:
    """检测附件中的表格文件，自动触发 prescan → parquet 预处理。
    
    Returns: [{workspace_path, parquet_path, meta, elapsed}]
    """
    # 提取表格类文件
    files_to_process = []
    for part in content:
        if hasattr(part, "mime_type") and hasattr(part, "workspace_path"):
            if part.mime_type in _PREPROCESS_MIME_TYPES and part.workspace_path:
                files_to_process.append(part)
    
    if not files_to_process:
        return []
    
    # 推送思考状态
    names = "、".join(f.name for f in files_to_process)
    await self._push_thinking(task_id, user_id, conversation_id, message_id,
                              f"正在解析表格结构：{names}...")
    
    results = []
    for file_part in files_to_process:
        try:
            result = await asyncio.wait_for(
                self._preprocess_one_file(file_part, conversation_id, user_id),
                timeout=_PREPROCESS_TIMEOUT,
            )
            results.append(result)
        except asyncio.TimeoutError:
            logger.warning(f"File preprocess timeout | file={file_part.name}")
            results.append({"workspace_path": file_part.workspace_path, 
                           "error": "预处理超时，AI 将自行读取"})
        except Exception as e:
            logger.warning(f"File preprocess failed | file={file_part.name} | error={e}")
            results.append({"workspace_path": file_part.workspace_path,
                           "error": str(e)})
    
    # 推送完成状态
    ok_count = sum(1 for r in results if "meta" in r)
    summary_parts = []
    for r in results:
        if "meta" in r:
            m = r["meta"]
            summary_parts.append(f"{r['name']}：{m.get('cols', '?')}列 × {m.get('rows', '?')}行")
        else:
            summary_parts.append(f"{r.get('name', '?')}：{r.get('error', '失败')}")
    await self._push_thinking(task_id, user_id, conversation_id, message_id,
                              f"表格解析完成：{'；'.join(summary_parts)}")
    
    return results
```

### 11.2 `_preprocess_one_file()` 方法

```python
async def _preprocess_one_file(
    self, file_part, conversation_id: str, user_id: str,
) -> Dict[str, Any]:
    """单个文件预处理：resolve 路径 → ensure_parquet_cache → 读 meta。"""
    from services.agent.data_query_cache import ensure_parquet_cache
    from services.agent.file_meta import read_file_meta, format_file_view
    from core.workspace import resolve_workspace_dir
    from core.config import get_settings
    
    settings = get_settings()
    ws_dir = resolve_workspace_dir(settings.file_workspace_root, user_id, self.org_id)
    abs_path = os.path.join(ws_dir, file_part.workspace_path)
    staging_dir = os.path.join(ws_dir, "staging", conversation_id)
    
    start = time.monotonic()
    cache_path, sheet_names = await ensure_parquet_cache(abs_path, None, staging_dir)
    elapsed = round(time.monotonic() - start, 2)
    
    # 读取元数据
    meta = read_file_meta(cache_path)
    meta_dict = {}
    if meta:
        meta_dict = {
            "cols": meta.summary.get("col_count", 0),
            "rows": meta.summary.get("row_count", 0),
            "columns": meta.summary.get("columns", []),
            "column_types": meta.summary.get("column_types", {}),
            "file_view": format_file_view(meta),
        }
        if meta.prescan:
            meta_dict["header_type"] = meta.prescan.get("header_type", "single")
            meta_dict["special_rows"] = meta.prescan.get("special_rows", {})
    
    return {
        "workspace_path": file_part.workspace_path,
        "name": file_part.name,
        "parquet_path": cache_path,
        "sheet_names": sheet_names,
        "meta": meta_dict,
        "elapsed": elapsed,
    }
```

### 11.3 增强 `_build_workspace_prompt()`

```python
@staticmethod
def _build_workspace_prompt(
    workspace_files: List[Dict[str, Any]],
    preprocess_results: List[Dict[str, Any]] | None = None,
) -> str:
    """生成工作区文件提示——含预处理元数据。"""
    if not workspace_files:
        return ""
    
    # 建立预处理结果索引
    meta_map = {}
    if preprocess_results:
        for r in preprocess_results:
            if r.get("meta"):
                meta_map[r["workspace_path"]] = r
    
    lines = ["用户附加了以下文件："]
    for f in workspace_files:
        wp = f.get("workspace_path", "")
        size_str = _fmt_size(f.get("size"))
        
        pre = meta_map.get(wp)
        if pre and pre.get("meta"):
            m = pre["meta"]
            # 有预处理结果：注入完整元数据
            cols = m.get("columns", [])
            col_str = "、".join(cols[:10])
            if len(cols) > 10:
                col_str += f"...共{len(cols)}列"
            lines.append(f"  📊 '{wp}' ({size_str})")
            lines.append(f"     {m.get('rows', '?')}行 × {m.get('cols', '?')}列")
            lines.append(f"     列名：{col_str}")
            lines.append(f"     已转为 Parquet 缓存，可直接用 duckdb.sql() 查询")
            if pre.get("parquet_path"):
                lines.append(f"     Parquet 路径：{pre['parquet_path']}")
        else:
            # 无预处理结果：退回基础信息
            lines.append(f"  '{wp}' ({size_str})")
    
    return "\n".join(lines)
```

---

## 12. 开发任务拆分

### Phase 1：核心管道（后端，~0.5 天）
- [ ] 1.1 `chat_handler.py`：新增 `_auto_preprocess_files()` + `_preprocess_one_file()`
- [ ] 1.2 `chat_handler.py`：在 `_stream_generate()` 中调用，推送 thinking 进度
- [ ] 1.3 `chat_context_mixin.py`：增强 `_build_workspace_prompt()` 接收预处理结果
- [ ] 1.4 `chat_context_mixin.py`：`_build_llm_messages()` 传递 preprocess_results

### Phase 2：降级与配置（~0.5 天）
- [ ] 2.1 `core/config.py`：新增 `auto_preprocess_enabled` 开关
- [ ] 2.2 超时降级逻辑：prescan 超时 → 代码检测，全流程超时 → 跳过注入基础信息
- [ ] 2.3 多文件并行预处理（`asyncio.gather`）

### Phase 3：测试与验证（~0.5 天）
- [ ] 3.1 单元测试：`test_auto_preprocess.py`
- [ ] 3.2 集成测试：上传 Excel → 发送消息 → 验证 AI 首次回复包含结构信息
- [ ] 3.3 性能测试：50 万行大文件预处理耗时 < 15s

---

## 13. 依赖变更

无。全部复用现有依赖。

---

## 14. 部署与回滚策略

- **数据库迁移**：无
- **API 兼容**：完全向后兼容
- **回滚步骤**：`config.py` 设置 `auto_preprocess_enabled = False` 即可关闭，回退到 AI 自行读取
- **灰度**：可按 org_id 控制开关

---

## 15. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|:------:|----------|
| prescan AI 调用超时增加首次响应延迟 | 中 | 15s 超时 + 降级跳过 |
| qwen-turbo 额度不足 | 低 | prescan 失败自动 fallback 代码检测 |
| 大文件转换阻塞消息流 | 低 | 已有分块转换 + snapshot 缓存 |

---

## 16. 文档更新清单

- [ ] `docs/FUNCTION_INDEX.md`：新增 `_auto_preprocess_files`
- [ ] `docs/PROJECT_OVERVIEW.md`：更新文件处理流程描述
- [ ] `docs/document/TECH_文件处理系统.md`：更新为主动预处理架构

---

## 17. 设计自检

- [x] 项目上下文已加载，4 点完整
- [x] 连锁修改已全部纳入任务拆分
- [x] 7 类边界场景均有处理策略
- [x] 架构影响评估无高风险项
- [x] 所有改动文件预估 ≤ 500 行
- [x] 无新依赖
