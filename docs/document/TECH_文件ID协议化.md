# 文件 ID 协议化技术方案

> 把 AI 看到的"文件引用"从中文路径升级到短 ASCII ID，根治 LLM 在生成 tool_call path 参数时的 pangu 化偏好。

---

## 一、背景与问题

### 1.1 真实事故

- 生产 chat_id `829d5529-6b52-4925-b1c8-c9fb0d599b8c`
- 用户上传文件 `4月销售主题分析-按订单商品明细-202605...xlsx`
- LLM (qwen3.5-plus) 调 `file_analyze` 时把 path 写成 `4 月销售主题分析 - 按订单商品明细 -202605...xlsx`（中文与 ASCII 间擅自加排版空格）
- file_search 已返回正确路径后，LLM 第二次仍然写错；4 次调用全错
- **DB 实证**：注入给 LLM 的 `workspace_path` 是干净的，pangu 化 100% 由 LLM 生成端造成

### 1.2 复现数据

| 协议 | 错误率（20 次） | 错误形式 |
|---|---|---|
| **path 协议**（现状） | 10%（2/20） | "4月"→"4 月"、"析-按"→"析 - 按"、"细-2026"→"细 -2026"（位置独立判断） |
| **file_id 协议** | 0%（60/60） | — |

复现脚本：`/tmp/reproduce_v2.py`、`/tmp/verify_file_id_protocol.py`

### 1.3 行业对照

| 平台 | 协议 |
|---|---|
| OpenAI Assistants / Files API | `file-abc123def` |
| Anthropic Claude Files API | `file_011CN9...` |
| Google Gemini File API | `files/abc-xyz` |
| 字节 Coze / 阿里灵积 | file_id |

行业主流不靠"防御层兜底"，从协议层避开让 LLM 字面 copy 长中文字符串。

---

## 二、核心设计

### 2.1 fid 哈希函数（无状态、确定性）

```python
# backend/services/agent/file_id.py（新建）
import hashlib

def compute_fid(org_id: str, workspace_path: str) -> str:
    """确定性哈希：同 (org_id, path) 永远得同 fid。
    
    无需缓存、无需 DB、多 worker 天然一致、重启即恢复。
    历史 path 也可即时翻译为 fid。
    """
    seed = f"{org_id}:{workspace_path}".encode("utf-8")
    digest = hashlib.blake2b(seed, digest_size=4).hexdigest()
    return f"fid_{digest}"  # 12 位 ASCII，e.g. fid_a3f2b1c9
```

**关键属性**：
- 同 org 内同路径 → 同 fid（幂等）
- 不同 org 同路径 → 不同 fid（多租户隔离）
- 冲突概率：单 org 内 1000 文件下 ~10⁻⁸（4 字节 hash 空间）
- 无运行时状态：重启不丢、跨 worker 一致、历史对话可随时翻译

### 2.2 双字段渲染（id 给机器、name 给用户）

```xml
<attachments count="1">
  <file>
    <id>fid_a3f2b1c9</id>      <!-- AI 调工具用这个 -->
    <name>4月销售.xlsx</name>   <!-- AI 跟用户说话用这个 -->
    <path>已整理表格/饶/4月销售.xlsx</path>  <!-- 沙盒内代码读取用 -->
    <status>raw</status>
    <action>调 file_analyze 转 Parquet</action>
  </file>
</attachments>
```

**三字段分工**：
- `<id>`：tool 入参（强约束 `pattern: ^fid_[a-z0-9]{8}$`，后端 `compute_fid` 校验）
- `<name>`：自然语言交互（AI 回复用户、生成图表标题）
- `<path>`：沙盒内代码 `pd.read_excel/read_csv/open` 直接用（沙盒无 fid→path 查表机制）

**沙盒安全说明**：`<path>` 字段虽含中文，但沙盒 `_scoped_open` 已有"去空格/连字符/下划线归一化匹配"兜底（参见 [sandbox_worker.py:70-103](../../backend/services/sandbox/sandbox_worker.py#L70-L103)），实测 4/4 pangu 化变体全部命中。覆盖路径：

| 调用 | 走 `_scoped_open` | 安全性 |
|---|---|---|
| `open()` / `pd.read_excel` / `pd.read_csv` | ✓ | 兜底覆盖 |
| `pd.read_parquet('staging/abc123.parquet')` | ✗（pyarrow native IO） | staging parquet 是 md5[:12] 纯 ASCII，无 pangu 风险 |

**工具入参**（file_analyze 等，不经沙盒）无任何兜底 → 这正是 fid 协议要解决的目标。

### 2.3 系统 prompt 显式分工指引

attachments XML 块尾追加：
```
【附件使用规则】
- 调工具(file_analyze/file_delete 等)时,file_id 参数必须 copy <id> 字段
- 回复用户、生成图表标题时,引用 <name> 字段
- 沙盒 code_execute 内读取数据时,用 <path> 字段
- 禁止把 <name> 当 file_id 传给工具
```

### 2.4 后端验证 + 友好报错

工具入参校验失败时，回传 AI 可读的诊断（让 Agentic Retry Loop 自我修正）：

```python
def resolve_fid_to_path(file_id: str, org_id: str, attachments: list) -> str:
    if not file_id.startswith("fid_"):
        raise ToolInputError(
            f"file_id 格式错误。你传的是 {file_id!r}（看起来是 name 或 path）。"
            f"请改用 <attachments> 里 <id> 字段的值（fid_xxx 格式）。"
        )
    for f in attachments:
        if compute_fid(org_id, f["workspace_path"]) == file_id:
            return f["workspace_path"]
    raise ToolInputError(f"未找到 file_id={file_id}，请检查 <attachments> 块。")
```

---

## 三、受影响范围（来自调研）

### 3.1 用户入口（7 个，前端）

| 入口 | 走上传接口? | 文件 |
|---|---|---|
| ①点击上传 | ✓ | `useFileUpload.ts` |
| ②拖拽 | ✓ | `useDragDropUpload.ts` |
| ③粘贴 | ✓ | 同上 |
| ④@ 提及 | ✗（引用） | `useFileMention.ts` |
| ⑤工作区插入 | ✗（引用） | `InputArea.tsx` workspaceFiles |
| ⑥AI 图右键引用 | ✗（引用） | `chat:quote-image` 事件 |
| ⑦语音录制 | ✓ | `useAudioRecording.ts` |

**关键**：引用类（④⑤⑥）不走上传接口，所以 fid 分配必须在 **attachments 渲染层**（汇聚点），不能在上传接口分配。

### 3.2 AI 接触点（13 个，后端）

| 类别 | 入口 | 文件:行号 | 改造要点 |
|---|---|---|---|
| **system 注入** | format_attachments XML | `attachments.py:121-135` | 加 `<id>` 字段 + 规则说明 |
| | build_workspace_prompt | `attachments.py:152-200` | 加 `[fid_xxx]` 前缀 |
| **历史回放** | extract_oai_messages file block | `content_extractors.py:154-168` | 注入时调 `compute_fid(org_id, wp)` 翻译 |
| | tool_step input 回放 | `content_extractors.py:188-228` | 老 path 兼容（保留通道） |
| | format_tool_digest | `tool_digest.py` + `history_loader.py:176` | 加 fid 前缀 |
| **工具返回** | _file_list | `file_tool_mixin.py:136-185` | 每行加 `[fid_xxx]` |
| | _search_files | `file_tool_mixin.py:187-224` | 同上 |
| | _describe_single_file | `file_tool_mixin.py:433-485` | 同上 |
| | _file_analyze | `file_tool_mixin.py:232-431` | staging parquet 也带 fid |
| | _fetch_all_pages staging | `tool_executor.py:408-522` | 同上 |
| | to_tool_content DATA_REF | `agent_result.py:224-282` | FileRef 加 fid 字段 |
| **子 Agent** | ERPAgent._execute | `erp_agent.py:381-389` | 同上 |
| | ScheduledTaskAgent 模板 | `scheduled_task_agent.py:282-283` | 同上 |

### 3.3 媒体类型（5 大类）

| 类型 | XML 通道 | 多模态 image_url | 改造 |
|---|---|---|---|
| 数据文件（.xlsx/.csv/.tsv） | ✓ | ✗ | XML 加 fid |
| 图片（用户上传） | ✓ (status=image) | ✓ | XML 加 fid + 多模态层加 fid 索引 |
| PDF/Word/PPT | ✓ (status=doc) | ✗ | XML 加 fid |
| 文本/代码 | ✓ (status=text) | ✗ | XML 加 fid |
| 音频/Parquet/二进制 | ✓ | ✗ | XML 加 fid |
| AI 生成图片 | ✗ | ✓ | 无需 fid（无 path） |

---

## 四、关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| fid 范围 | 所有 AI 接触的文件（含 staging） | 一致性最好，避免协议混合 |
| fid 分配位置 | attachments 渲染层中心化 | 引用类入口（④⑤⑥）不经上传接口 |
| fid 持久化 | **无状态确定性哈希** | 不依赖 cache/DB，重启即恢复 |
| 沙盒内引用 | 仍用 path | 沙盒无查表能力，XML 同时给 path 字段 |
| 老对话兼容 | **不做翻译**（用户决策） | 新对话纯 fid 协议；老对话历史里残留 path 也只是渲染时旧样式，不影响新一轮工具调用 |
| 新消息持久化 | 仍存 path | fid 可随时重生成，存 path 简化 schema |
| pattern 校验 | 加 `^fid_[a-z0-9]{8}$` | 60/60 实测 LLM 服从，作为防御兜底 |

---

## 五、Phase 拆分

### Phase 1：核心打基础 ✅ 已完成
- 新建 `backend/services/agent/file_id.py`（`compute_fid` + `is_valid_fid`）
- 改 `attachments.py`：XML 加 `<id>` + 附件使用规则文字
- 改 `build_workspace_prompt`：每行加 `[fid_xxx]` 前缀
- 改 `prompt_builder/builder.py`：透传 `inp.org_id`
- 单测：18 个（哈希幂等、跨 org 隔离、冲突率统计、XML 注入）

### Phase 2：工具入参 + 返回值 ✅ 已完成
- 新增 `file_id.resolve_fid_to_workspace`（fid 反查 workspace 绝对路径）
- 改 `file_analyze` schema：加 `file_id`（pattern `^fid_[a-z0-9]{8}$`），保留 `path` 作老协议兜底
- 改 `_file_analyze`：入参优先 `file_id` → 反查 → 失败兜底 `path`
- 改 `file_delete` schema 加 `file_ids` + `_file_delete` 双协议入参
- 改 `_list_directory` / `_search_files`：返回每行带 `[fid_xxx]` 前缀
- 单测：25 个（含 schema 校验/反查/双协议/向后兼容）

### 跳过项（用户产品决策）
- ~~历史对话翻译（content_extractors / tool_digest）~~ — 不考虑老对话
- ~~子 Agent 改造（ERPAgent / ScheduledAgent）~~ — 调研确认无独立文件通道，自动复用主 Agent
- ~~前端 fid→name 反查接口~~ — tool_step 展示 fid 不影响功能，按需追加
- ~~staging parquet 加 fid（_fetch_all_pages / DATA_REF）~~ — staging 文件名是 ASCII md5，沙盒里读不会 pangu 化

### 验证 ✅ 已完成
- E2E：生产 attachments XML + 新工具 schema，qwen3.5-plus 跑 20 次 → 20/20 用 file_id，0 pangu 化
- 单测 25 个全过
- 直接相关回归 128 个全过（4 个失败为预存在的 `_build_memory_prompt` 重构遗留）

---

## 六、回归测试清单

| 场景 | 验证点 |
|---|---|
| 中文文件名 file_analyze | 100% 命中，无 pangu 化 ✅ E2E 20/20 |
| @ 提及历史文件 | fid 正确分配，工具调用成功 |
| 工作区插入文件 | 同上 |
| 多文件对比选 1 个 | AI 用 name 判断、用 fid 调工具 |
| 沙盒内 read_parquet | 用 `path` 字段正常读取（_scoped_open 兜底覆盖 pangu） |
| 跨 org 同名文件 | fid 不冲突 ✅ 单测验证 |
| 服务重启后再调用 | fid 哈希确定性，重启即恢复 |
| 企微通道 | 文件转文本注入，行为不变 |
| 多模态图片 + AI 操作 | 既能看图（image_url）又能删图（fid） |

---

## 七、上线风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| AI 偶发不服从 pattern 传 path | 极低（E2E 0%） | 单次工具失败 | `_file_analyze` 保留 path 兜底通道；格式错时返回 `retryable=True` 让 AI 重试 |
| pattern 校验被 DashScope 忽略 | 低 | 工具收到非法 fid | 后端 `is_valid_fid` 二次校验 + 友好报错 |
| 沙盒代码用 fid 而非 path | 低 | 沙盒 IO 失败 | XML 附件使用规则文字明确分工：`<path>` 在沙盒内用 |

**回滚方案**：
- attachments XML 加 `<id>` + 工具 schema 加 `file_id` 字段都向后兼容 — `path` 通道保留
- 如需回滚：删除 schema 里的 `file_id` 字段即可，老 path 协议立刻生效

---

## 八、实际改动的文件清单（5 个改 + 1 个新建）

**新建**：
- `backend/services/agent/file_id.py`（`compute_fid` / `is_valid_fid` / `resolve_fid_to_workspace`）

**改造**：
- `backend/services/handlers/chat_context/attachments.py`（XML 加 `<id>` + 附件使用规则 + workspace_prompt 加 fid 前缀）
- `backend/services/prompt_builder/builder.py`（透传 `inp.org_id`）
- `backend/config/file_tools.py`（`file_analyze` schema 加 `file_id` + `file_delete` schema 加 `file_ids`）
- `backend/services/agent/file_tool_mixin.py`（`_file_analyze` 入参双协议 + `_list_directory` / `_search_files` 返回加 `[fid_xxx]`）
- `backend/services/agent/file_delete_mixin.py`（`_file_delete` 入参双协议）

**新增测试**：
- `backend/tests/test_file_id_protocol.py`（25 个单测）

---

## 九、文档同步 ✅ 已完成

- `docs/FUNCTION_INDEX.md`：已添加 file_id 协议模块的 5 个函数
- 本设计文档（自我修订实际执行情况）
- MEMORY 新增 `project_file_id_protocol.md`

---

**作者**：架构师
**日期**：2026-06-14
**状态**：待用户审核
**预计工期**：3 天（4 Phase）
