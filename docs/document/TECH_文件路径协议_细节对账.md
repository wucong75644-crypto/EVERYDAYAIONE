# 文件路径协议改造 — 细节对账

> **目的**:Phase 1 实施前的逐行对账。每个删/保/改的代码点必须明确 file:line + 当前行为 + 新行为 + 调用链。
> **依据**:`TECH_文件路径协议统一_MVP方案.md` + 4 组生产实测 + LLM 行为实验
> **状态**:🔵 准备审阅

---

## 全景:改造分类汇总

| 类别 | 数量 | 内容 |
|---|---|---|
| **A 删除** | 5 项 | 沙盒变量层 / 路径隐藏 / get_file+manifest / Subprocess 降级 / .echart.json 错误目录 |
| **B 保留(不动)** | 5 项 | file_path_cache 后端管理 / file_analyze 治理链 / scoped_os 黑名单 / nsjail 基础 / file_search 兜底 |
| **C 修改** | 6 项 | nsjail bind 加覆盖 / attachments XML 加 parquet_path / FileRef 格式 / tool_result_envelope 格式 / 提示词全改 / _upload_scan_dirs 确认 |

---

# §A. 删除清单(5 项)

## A1. 沙盒变量层注入(OUTPUT_DIR / STAGING_DIR / WORKSPACE_DIR / SKILLS_DIR)

### 当前代码

**主进程注入点**:[sandbox_worker.py:445-452](backend/services/sandbox/sandbox_worker.py#L445-L452)
```python
g["WORKSPACE_DIR"] = PathStr(workspace_dir)
g["STAGING_DIR"] = PathStr(staging_dir)
g["OUTPUT_DIR"] = PathStr(output_dir)
g["SKILLS_DIR"] = PathStr(skills_dir)
```

**PathStr 自定义类**:[sandbox_worker.py:32-46](backend/services/sandbox/sandbox_worker.py#L32-L46)
```python
class PathStr(str):
    """str 子类 + 支持 / 运算符
    用途:让 OUTPUT_DIR / "文件名" 这种代码能正常工作"""
```

### 删除范围
- `g["WORKSPACE_DIR"]` 等 4 行注入
- `PathStr` 类整体
- 相关注释文档(line 35, 133)
- 文件不存在自动纠错逻辑里的 `OUTPUT_DIR / STAGING_DIR` fallback(line 186)

### 谁依赖这些变量

| 调用方 | 文件:行 | 用途 | 处理方式 |
|---|---|---|---|
| LLM(提示词诱导)| code_tools.py:80, 138, 143, 148, 170, 174 | 写代码用 | §C5 改提示词同步切 |
| FileRef.sandbox_ref | tool_output.py:109 | 工具返回字符串 | §C3 改格式 |
| tool_result_envelope | tool_result_envelope.py:231 | 大结果落盘提示 | §C4 改格式 |
| tool_digest | tool_digest.py:114, 145, 151 | 工具摘要 | §C5 提示词同步 |
| context_compressor/archive | archive.py:21, 281 | 旧消息归档正则 | **保留正则**(向后兼容历史消息),只是不再产生新格式 |
| scheduled_task_agent | scheduled_task_agent.py:262, 282-284 | 模板路径 | §C5 提示词同步 |
| erp_duckdb_helpers | erp_duckdb_helpers.py:312 | 注释 | §C5 提示词同步 |
| sandbox_tool_mixin _STAGING_RE | sandbox_tool_mixin.py:283 | 正则提取 | **保留正则**(向后兼容)|

### 删除后行为
- LLM 看到的沙盒 globals 不再有这些变量
- 如果旧消息历史里有 `STAGING_DIR + 'x.parquet'` 代码,LLM 字面执行会 NameError
- **缓解**:LLM 看 system 提示词学新写法,不会主动回退;archive/tool_digest 正则**仍保留**用于解析历史

### 风险评估
- ✅ A1-D 实测验证:LLM 在新协议下 0/3 用变量
- ⚠️ 历史 chat 重新加载场景:旧 messages 包含 `STAGING_DIR + 'x'` 字符串,LLM 看到可能困惑
- **缓解**:依靠 LLM 自适应能力(实验组场景 2 已证明)

---

## A2. 路径隐藏 `_hide_paths`

### 当前代码

**两份重复实现**:

[sandbox_worker.py:690-696](backend/services/sandbox/sandbox_worker.py#L690-L696)(subprocess 入口)
```python
# 6. 路径隐藏(替换为变量名,LLM 可直接用 OUTPUT_DIR/WORKSPACE_DIR 引用)
if result and output_dir:
    result = result.replace(output_dir, "OUTPUT_DIR")
if result and workspace_dir:
    result = result.replace(workspace_dir, "WORKSPACE_DIR")
if result and skills_dir:
    result = result.replace(skills_dir, "SKILLS_DIR")
```

[kernel_worker.py:90-98](backend/services/sandbox/kernel_worker.py#L90-L98)(kernel 入口)
```python
def _hide_paths(result: str, output_dir: str, workspace_dir: str, skills_dir: str = "") -> str:
    if result and output_dir:
        result = result.replace(output_dir, "OUTPUT_DIR")
    if result and workspace_dir:
        result = result.replace(workspace_dir, "WORKSPACE_DIR")
    if result and skills_dir:
        result = result.replace(skills_dir, "SKILLS_DIR")
    return result
```

调用点:[kernel_worker.py:219](backend/services/sandbox/kernel_worker.py#L219)
```python
result = _hide_paths(result, output_dir, workspace_dir, skills_dir)
```

### 删除范围
- `_hide_paths` 函数定义(kernel_worker.py:90-98)
- `_hide_paths` 调用(kernel_worker.py:219)
- subprocess 中的 6 行 replace(sandbox_worker.py:690-696)

### 删除后行为
- 沙盒输出里**真实路径**直接暴露给 LLM
- **问题**:真实路径含 org_id/user_id UUID,LLM 看到长字符串
- **解决方案**:新方案下沙盒 cwd=/workspace(nsjail bind 虚拟根),所有相对路径输出**没有 UUID**;C 层库直接写真实路径的情况也很少(它们通过 cwd 解析相对路径)

### 风险
- ⚠️ 如果 LLM 主动 `print(os.path.realpath('staging/x'))`,会看到 host 真实路径
- **缓解**:nsjail 模式下 realpath 看到的是 `/workspace/staging/...`(bind mount 透明);只有 subprocess 降级模式才暴露 host(本次同时删 subprocess,问题不存在)

---

## A3. `get_file` + `manifest`

### 当前代码

**沙盒内 get_file 实现**:[sandbox_worker.py:493-554](backend/services/sandbox/sandbox_worker.py#L493-L554)
```python
def _get_file(name: str) -> str:
    """按文件名获取 parquet 绝对路径(归一化匹配 + 自检)"""
    # 读 staging/_manifest.json
    # 4 级匹配:精确 → 归一化 → stem → 前缀
    # 返回真实路径
```

**manifest 写入**:[file_path_cache.py:246-292](backend/services/agent/file_path_cache.py#L246-L292)
```python
def write_manifest(self) -> None:
    """把所有已注册文件写入 staging/_manifest.json
    沙盒 _get_file 按 '/' 区分两种值(不含 / 走 staging,含 / 走 workspace)"""
```

**调用 manifest 写入的位置**:[sandbox_tool_mixin.py:75](backend/services/agent/sandbox_tool_mixin.py#L75)
```python
_cache.write_manifest()  # code_execute 前写
```

### 删除范围
- `_get_file` 函数定义(sandbox_worker.py:493-554)
- `g["get_file"] = _get_file` 注入(sandbox_worker.py:554)
- `write_manifest` 方法(file_path_cache.py:246-292)
- `_cache.write_manifest()` 调用(sandbox_tool_mixin.py:75)
- 相关注释 & 类似 docstring

### 保留(下文 §B)
- `FilePathCache` 三字段注册表
- 4 级匹配算法(用于 file_search / file_delete 路径解析)
- `set_analyzed` / `is_analyzed` 状态(驱动 attachments XML status)

### 删除后行为
- 沙盒里 LLM 调 `get_file('x.xlsx')` → NameError
- **替代路径**:LLM 在 attachments XML 里看到完整 `parquet_path`(已分析时)或调 `file_search`(长对话兜底)

### 风险
- ⚠️ 实验只用 qwen-plus 验证,gemini/deepseek 行为待 Phase 0 测试
- **缓解**:Phase 0 跨模型测试,确认 LLM 都能从 attachments XML 读 parquet_path

---

## A4. Subprocess 降级路径

### 当前代码

**主调度逻辑**:[executor.py:120-161](backend/services/sandbox/executor.py#L120-L161)
```python
if self._kernel_manager and self._conversation_id:
    for attempt in range(2):
        try:
            kernel_ok = await self._kernel_manager.get_or_create(...)
            if not kernel_ok:
                break  # 池满 → 降级
            status, result = await self._kernel_manager.execute(...)
            if status != "crashed":
                return result
            if attempt == 0:
                await self._kernel_manager.destroy(...)
                continue
            break  # 2 次崩溃 → 降级
        except (KeyError, RuntimeError, OSError) as e:
            break  # 异常 → 降级

# 降级:无状态 subprocess
return await self._run_in_subprocess(code)
```

**subprocess 入口**:[executor.py:163-205](backend/services/sandbox/executor.py#L163)
```python
async def _run_in_subprocess(self, code: str) -> str:
    """在独立子进程中执行代码(spawn 隔离)"""
    from services.sandbox.sandbox_worker import sandbox_worker_entry
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=sandbox_worker_entry, args=(...))
```

**sandbox_worker_entry**:[sandbox_worker.py:628+](backend/services/sandbox/sandbox_worker.py#L628)(整个 subprocess 工作函数体)

### 删除范围
- `_run_in_subprocess` 方法(executor.py:163-205,~50 行)
- `sandbox_worker_entry` 函数(sandbox_worker.py:628 至文件尾,~80 行)
- 降级调用点(executor.py:160-161)
- `mp` / `spawn` import + 相关 helper

### 替代行为(失败时返回明确错误)
```python
# executor.py 改造后:
if not kernel_ok:
    return AgentResult(
        summary="沙盒资源紧张,请稍后重试",
        status="error",
        metadata={"retryable": True},
    )

# 2 次崩溃:
if attempt == 1:  # 已重试一次
    return AgentResult(
        summary="沙盒执行异常,请稍后重试",
        status="error",
        metadata={"retryable": True},
    )
```

### 删除后影响
- Kernel 池满 → 用户看到"请稍后重试"
- Kernel 崩溃 → 重建 1 次,仍崩 → 报错
- nsjail 系统级故障 → 全部 code_execute 失败(原降级也救不了,本就 host 问题)

### 风险
- ⚠️ 7 天 1 次降级数据,但极端故障(nsjail/内核 bug)可能让 Kernel 全挂
- **缓解**:加 Kernel 失败率监控告警;运维介入修复

### 删除后必须保留的(独立)
- `build_scoped_open` 函数(sandbox_worker.py:122-218) — kernel_worker 用
- `_check_node` / validators — AST 验证还要用
- `SAFE_BUILTINS` 等沙盒常量

---

## A5. `.echart.json` 写错目录的特殊处理

### 当前 bug 现象(用户截图证实)
- LLM 写 `OUTPUT_DIR + '/x.echart.json'` → 文件落 `下载/`
- executor 读 chart option → 上传 OSS → 出"下载卡片"
- 用户 `下载/` 堆 N 个 `.echart.json`(无用中转文件)

### 当前代码

**executor 读取**:[executor.py:350-369](backend/services/sandbox/executor.py#L350-L369)
```python
# .echart.json 文件:读取 ECharts option,存到实例供 chart block 使用
if f.name.endswith(".echart.json"):
    try:
        _content = f.read_text(encoding="utf-8")
        if len(_content) <= self._CHART_OPTION_MAX_BYTES:
            _option = _json.loads(_content)
            if not hasattr(self, "_chart_options"):
                self._chart_options = {}
            self._chart_options[f.name] = _option
        # ❌ 读完没删源文件
```

**chat_tool_mixin 跳过 .echart.json 上传**:[chat_tool_mixin.py:495](backend/services/handlers/chat_tool_mixin.py#L495)
```python
if name.endswith(".echart.json"):
    # 跳过(已经作为 chart block 处理)
```

### 删除范围
- executor.py:350-369 这段(把 .echart.json 从 output_dir 扫描里拿走)
- chat_tool_mixin.py:495 的特殊跳过逻辑

### 新增(改到 staging,读完即删)
**在 executor 加 `_scan_chart_options`**:
```python
def _scan_chart_options(self):
    """扫描 staging/ 中的 .echart.json,读取后立即删除(中转数据)"""
    if not self._staging_dir:
        return
    staging_path = Path(self._staging_dir)
    if not staging_path.exists():
        return
    for f in staging_path.iterdir():
        if not f.name.endswith(".echart.json"):
            continue
        try:
            content = f.read_text(encoding="utf-8")
            if len(content) <= self._CHART_OPTION_MAX_BYTES:
                option = json.loads(content)
                if not hasattr(self, "_chart_options"):
                    self._chart_options = {}
                self._chart_options[f.name] = option
            f.unlink()  # ← 关键:读完即删
        except Exception as e:
            logger.warning(f"Chart option scan failed | file={f.name} | error={e}")

# execute 主流程:
file_results = await self._auto_upload_new_files()
self._scan_chart_options()  # ← 新增
```

### 提示词同步改(§C5)
```diff
- with open(OUTPUT_DIR + '/图表名.echart.json', 'w', encoding='utf-8') as f:
+ with open("staging/图表名.echart.json", "w", encoding="utf-8") as f:
```

### 删除后行为
- `.echart.json` 写到 staging/(会话级,24h TTL)
- executor 读完立即删
- 即使删除失败,staging TTL 兜底
- 用户 `下载/` 干净

---

# §B. 保留清单(5 项)— 确认不动

## B1. `FilePathCache` 三字段 + 4 级匹配(后端管理用)

**位置**:`services/agent/file_path_cache.py`

**保留原因**:
- `register` / `_resolve_entry` / `4 级匹配`:被 file_search、file_delete、attachments XML 渲染等 12 处调用
- `set_analyzed` / `is_analyzed`:驱动 attachments XML status 切换(未分析→已分析)
- `register_file_for_workspace` 等:前端 @ 引用文件注册

**调用方清单**(12 处):
```
services/agent/sandbox_tool_mixin.py:68, 245, 267
services/agent/file_tool_mixin.py:141, 189, 232, 436
services/agent/file_delete_mixin.py:32
services/agent/tool_executor.py:507
services/handlers/chat_context_mixin.py:90
services/handlers/chat_tool_mixin.py:549
services/handlers/chat_context/attachments.py:66
```

**仅删除 write_manifest 方法**(§A3),其他全部保留。

## B2. `file_analyze + file_meta` 数据治理链 — **核心保留**

**为什么必须保留**:
- 用户上传的脏 Excel(多级表头/合并单元格/汇总行/中文列名/公式)**必须经治理**才能正确读
- file_analyze 内部:prescan → AI 裁决 → excel_cleaner → 干净 parquet + meta.json
- AI 不能绕过 file_analyze 直接 `pd.read_excel`,会读到混乱数据

**用户明确**:"file_analyze 这个方法不可能跳过的,他必须进去,这是原则问题"

### 设计强约束(写入提示词)

```
未分析的 .xlsx/.csv 必须先 file_analyze('文件名'),不能直接 pd.read_excel
file_analyze 完成后,attachments XML 显示 parquet_path,用 pd.read_parquet 读 parquet
```

### 涉及代码(完全不动)
- `services/agent/file_meta/` 整个包
- `services/agent/data_query_cache.py:ensure_parquet_cache`
- `services/agent/file_tool_mixin.py:_file_analyze`
- `services/agent/file_scanners.py`
- `services/agent/excel_cleaner/`

## B3. `scoped_os` 黑名单(staging 父目录隔离)

**位置**:`services/sandbox/scoped_os.py:35-37, 40-62`

**保留原因**:
- nsjail bind 覆盖让沙盒内看不到其他会话 staging
- 但 `scoped_os` 黑名单是**深度防御独立机制**(防 AI 通过 os.path.realpath 探测出真实 host 路径再写)
- 这是独立安全层,不应跟 bind 方案耦合

**用户明确**:"5 黑名单不删除 正确"

## B4. `nsjail` 基础配置(`deploy/sandbox.cfg`)

**保留**:
- 系统库挂载(`/usr`, `/lib`, `/lib64`, `/etc/alternatives`, `/usr/share/zoneinfo`)
- venv 挂载(`/venv`)
- 后端代码挂载(`/app`)
- tmpfs(`/tmp` 256M)
- cgroup 限制(mem 4G, pids 128, cpu 80%)
- 环境变量(`HOME=/tmp` 等)

**仅 §C1 加 1 行 bind 覆盖**,其他不动。

## B5. `file_search` 工具(LLM 长对话兜底)

**位置**:`services/agent/file_tool_mixin.py:_file_search`

**保留 + 强化提示**:
- 短对话:LLM 看 attachments XML 直接拿路径
- 长对话:attachments XML 被截断 → LLM 调 `file_search(keyword='账单')` → 实时探索文件系统
- 实验场景 1 验证:3/3 次 LLM 自动用 file_search

新提示词需明确这点(§C5 改造)。

---

# §C. 修改清单(6 项)

## C1. `kernel_manager._build_command` 加 nsjail bind 覆盖

### 当前
[kernel_manager.py:308-310](backend/services/sandbox/kernel_manager.py#L308-L310)
```python
"-B", f"{workspace_dir}:/workspace",
"-B", f"{staging_dir}:/staging",
"-B", f"{output_dir}:/output",
```

### 修改后
```python
"-B", f"{workspace_dir}:/workspace",
"-B", f"{staging_dir}:/workspace/staging",  # ← 覆盖 /workspace/staging,实现会话级
# /staging 不再需要(旧的 STAGING_DIR=/staging 引用全删)
# /output 不再需要(旧的 OUTPUT_DIR=/output 引用全删)
```

### B4 实测验证(2026-06-04)
- nsjail 支持后置 bind 覆盖前置
- `/workspace/staging` 透明替换为会话级目录
- 其他会话的 conv_id 完全不可见

### 启动自检
```python
def verify_nsjail_bind_override():
    """启动时验证 nsjail bind 覆盖语义仍工作(防 nsjail 升级行为变化)"""
    # 1. 启动一个临时 kernel
    # 2. exec: print(os.listdir('/workspace/staging'))
    # 3. assert 看到的只是测试会话内的文件
```

启动失败 → 服务不上线 → 告警。

---

## C2. `attachments XML` 加 `parquet_path` 字段

### 当前
[chat_context/attachments.py:76-156](backend/services/handlers/chat_context/attachments.py#L76)
```xml
<file>
  <name>账单.xlsx</name>
  <type>数据文件</type>
  <size>2.1MB</size>
  <source>本轮上传</source>
  <status>已分析。直接在 code_execute 中用 get_file("账单.xlsx") + duckdb 查询。</status>
</file>
```

### 修改后
```xml
<file>
  <name>账单.xlsx</name>
  <type>数据文件</type>
  <size>2.1MB</size>
  <source>本轮上传</source>
  <parquet_path>staging/_cache_v3.0_xxx_账单.parquet</parquet_path>  ← 已分析才有
  <status>已分析 → df = pd.read_parquet("staging/_cache_v3.0_xxx_账单.parquet")</status>
</file>

<!-- 未分析时: -->
<file>
  <name>账单.xlsx</name>
  <type>数据文件</type>
  <status>未分析 → 先调 file_analyze('账单.xlsx')</status>
</file>
```

### 实现要点
- `format_attachments` 函数从 `file_path_cache` 读 `entry.parquet` 字段
- 已分析(entry.analyzed=True 且 entry.parquet 存在)→ 计算 `parquet_path = relpath(entry.parquet, workspace_dir)` → 渲染
- status 字段强制引导:**已分析直接 pd.read_parquet,未分析必须先 file_analyze**

### 长名 parquet 文件名处理
当前 parquet 命名:`_cache_v3.0_{hash8}_{sheet}_{stem}.parquet` — 长但唯一
- LLM 实验显示:60+ 字符长文件名字面 copy 0 错误
- 不改命名规则(避免迁移)

---

## C3. `FileRef.sandbox_ref` 格式

### 当前
[tool_output.py:104-109](backend/services/agent/tool_output.py#L104-L109)
```python
@property
def sandbox_ref(self) -> str:
    return f"STAGING_DIR + '/{self.filename}'"
```

### 修改后
```python
@property
def sandbox_ref(self) -> str:
    return f"staging/{self.filename}"
```

### 影响面
- 工具结果文本里"已存到 staging/xxx.parquet"
- 主 Agent 看到后字面 copy
- 实验场景 2 验证:即使工具结果仍是旧格式,LLM 也会翻译成相对路径

---

## C4. `tool_result_envelope` 返回格式

### 当前
[tool_result_envelope.py:231](backend/services/agent/tool_result_envelope.py#L231)
```python
f'Full output saved to: STAGING_DIR + "/{filename}"\n\n'
```

### 修改后
```python
f'Full output saved to: staging/{filename}\n\n'
```

### 同步改 docstring
[tool_result_envelope.py:240](backend/services/agent/tool_result_envelope.py#L240)
```diff
- 文件写入 staging_dir 目录,调用方通过 STAGING_DIR + '/filename' 引用。
+ 文件写入 staging_dir 目录,调用方通过 staging/filename 相对路径引用。
```

---

## C5. 提示词全改(8 处关键 + 多处零散)

### 改动清单(精确定位)

| 文件:行 | 当前 | 改成 |
|---|---|---|
| code_tools.py:41 | `环境变量: STAGING_DIR, OUTPUT_DIR(自动上传)` | `相对路径:staging/(中间数据) 下载/(用户产物,自动上传)` |
| code_tools.py:51 | `生成文件写到 OUTPUT_DIR` | `生成文件写 "下载/x.xlsx"(用户产物)` |
| code_tools.py:75 | `OUTPUT_DIR 存输出文件,自动上传。` | `产物写 "下载/x.xlsx",自动上传。` |
| code_tools.py:80 | `df.to_excel(OUTPUT_DIR + '/文件.xlsx', ...)` | `df.to_excel("下载/文件.xlsx", ...)` |
| code_tools.py:138 | `duckdb.sql("... read_parquet(STAGING_DIR + '/文件名')")` | `duckdb.sql("... read_parquet('staging/文件名')")` |
| code_tools.py:139 | `生成文件写到 OUTPUT_DIR 目录` | `生成文件写 "下载/文件名"` |
| code_tools.py:143 | `with open(OUTPUT_DIR + '/图表.echart.json', ...)` | `with open("staging/图表.echart.json", ...)` ⬅️ A5 同步 |
| code_tools.py:148 | `df.to_excel(OUTPUT_DIR + '/报表.xlsx')` | `df.to_excel("下载/报表.xlsx")` |
| code_tools.py:170 | `duckdb.sql("... read_parquet(STAGING_DIR + '/文件名')")` | `duckdb.sql("... read_parquet('staging/文件名')")` |
| code_tools.py:174 | `df.to_excel(OUTPUT_DIR + '/报表.xlsx')` | `df.to_excel("下载/报表.xlsx")` |
| file_tools.py:176 | `with open(STAGING_DIR + '/_manifest.json')` | **删除该行**(manifest 已删) |
| chat_tools.py:211 | `OUTPUT_DIR 存输出文件(自动上传)` | `"下载/" 存产物(自动上传)` |
| scheduled_task_agent.py:262 | `文件输出到 OUTPUT_DIR` | `文件输出到 "下载/"` |
| scheduled_task_agent.py:282 | `模板文件路径: STAGING_DIR + '/{tpl[name]}'` | `模板文件路径: staging/{tpl[name]}` |
| scheduled_task_agent.py:283 | `pd.read_excel(STAGING_DIR + '/{tpl[name]}')` | `pd.read_excel("staging/{tpl[name]}")` |
| scheduled_task_agent.py:284 | `按模板格式填入数据后输出到 OUTPUT_DIR` | `按模板格式填入数据后输出到 "下载/"` |
| tool_digest.py:145 | `line += f" → STAGING_DIR + '/{t[staged]}'"` | `line += f" → staging/{t[staged]}"` |
| tool_digest.py:151 | `沙盒变量,用 STAGING_DIR + '/文件名' 访问` | `staging 缓存目录,用 "staging/文件名" 读取` |

### 提示词新增核心规则段(放 code_tools.py 顶部)
```
=== 文件操作规则 ===
所有路径用相对字符串,直接写,禁止变量/拼接/函数包装。

读文件:
  数据文件(xlsx/csv):
    1. 未分析:先调 file_analyze('文件名')
    2. 已分析:看 attachments 的 parquet_path,直接用 pd.read_parquet("staging/...")
  其他文件:直接 open("上传/2026-06/x.txt", "r") 等

写产物(给用户下载):
  df.to_excel("下载/报表.xlsx")
  plt.savefig("下载/图.png")

写缓存(中间数据,会话内,24h 清):
  df.to_parquet("staging/cleaned.parquet")
  with open("staging/图表.echart.json", "w") as f: ...

禁止:
  ❌ OUTPUT_DIR / STAGING_DIR / WORKSPACE_DIR(已删除,会 NameError)
  ❌ get_file() (已删除)
  ❌ STAGING_DIR + '/x' 拼接

长对话忘了文件路径?调 file_search(keyword='账单') 实时查。
```

---

## C6. `_upload_scan_dirs` — 确认不变

### 当前
[executor.py:284-293](backend/services/sandbox/executor.py#L284-L293)
```python
@property
def _upload_scan_dirs(self) -> list[str]:
    """auto_upload 监控的目录列表:仅 OUTPUT_DIR。"""
    dirs = []
    if self._output_dir:
        dirs.append(self._output_dir)
    return dirs
```

### 新方案下行为
- `self._output_dir` 仍是 `$WS/下载/`(物理 host 路径)
- LLM 写 `下载/x.xlsx`(相对) → 沙盒 cwd=/workspace → 解析到 `/workspace/下载/x.xlsx` → bind 透明 → 物理 `$WS/下载/x.xlsx`
- `_auto_upload_new_files` 扫 host `$WS/下载/` → 命中 ✅

### 仅修改文档注释
```diff
- """auto_upload 监控的目录列表:仅 OUTPUT_DIR。
- STAGING_DIR 是中间数据目录(parquet/json 等),不应推送给用户。
- 工具描述已明确要求 LLM 将用户产出写到 OUTPUT_DIR。"""
+ """auto_upload 监控的目录列表:仅 host output_dir(workspace/下载/)。
+ staging 是中间数据目录(parquet/json 等),不上传。
+ LLM 将用户产出写相对路径 '下载/x.xlsx' 自动落到这里。"""
```

---

# §D. `.echart.json` bug 修复细节

### 修改 = §A5(executor 改造)+ §C5(提示词 code_tools.py:143)

### 验证步骤
1. 启动测试 kernel
2. 跑 LLM 代码:`open("staging/test.echart.json", "w") → json.dump(option) → f.close()`
3. 验证:
   - `executor._chart_options['test.echart.json']` 含 option ✅
   - `staging/test.echart.json` 文件**不存在**(已删)✅
   - `下载/` 目录**没有** test.echart.json ✅
4. 前端读 messages content 的 chart block → ECharts 渲染 ✅

### 历史污染清理
- 用户 `下载/` 已有的旧 `.echart.json` 文件:**写一次性清理脚本**
- 不在主路径协议改造范围内,Phase 1 后单独处理

---

# §E. 实施依赖图

```
Phase 0(测试,1-2 天)
  ├─ 跨模型(gemini-3-pro + deepseek-v3.2 + claude-sonnet)
  ├─ 多文件场景(5+ attachments)
  ├─ DuckDB 磁盘模式兼容
  └─ nsjail bind 覆盖压力测试

Phase 1(实施,2-3 天,无双轨,feature flag 全切)
  Step 1: 启动自检(C1 验证 + 物理一致性不变量)
  Step 2: kernel_manager bind 覆盖(C1)
  Step 3: attachments XML 加 parquet_path 字段(C2)
  Step 4: FileRef.sandbox_ref / tool_result_envelope 改格式(C3, C4)
  Step 5: 提示词全改(C5)
  Step 6: 删 OUTPUT_DIR/STAGING_DIR 变量注入(A1)
  Step 7: 删 _hide_paths(A2)
  Step 8: 删 get_file + manifest(A3)
  Step 9: 删 Subprocess 降级(A4)
  Step 10: .echart.json 写到 staging + executor 即删(A5)
  Step 11: 灰度 5% 用户 → 24h 监控 → 全切

Phase 2(清理,Phase 1 全量后立即):
  - 删 feature flag 旧分支
  - 删旧文档/注释
  - 验证生产无回归
```

### 关键依赖
- C1 必须在所有 A* 之前(基础设施先就绪)
- C2 + C5 必须同步(LLM 看到的 path + 提示词教学一致)
- A5(.echart.json)必须 C5 提示词改完才生效

---

# §F. 验证矩阵

## F1. Phase 0 测试矩阵

| 测试 | 工具 | 预期 |
|---|---|---|
| 跨模型(gemini)5 场景 | LLM API 直调 | 都用相对路径 |
| 跨模型(deepseek-v3.2)5 场景 | LLM API 直调 | 都用相对路径 |
| 跨模型(claude-sonnet)5 场景 | LLM API 直调 | 都用相对路径 |
| 多文件(5+ attachments) | qwen-plus + 真实 attachments XML | LLM 选对文件,字面 copy 准确 |
| DuckDB 磁盘模式 | sandbox 启动 + duckdb.duckdb 持久化 | 跨 sandbox 调用读取 OK |
| nsjail bind 覆盖压力(并发 10 用户)| Kernel 池 4 + 10 用户排队 | 各自 /workspace/staging 隔离 |

## F2. Phase 1 灰度监控指标

| 指标 | baseline | 退化阈值 → 自动回滚 |
|---|---|---|
| sandbox PermissionError 率 | 实测取数 | +10% |
| sandbox FileNotFoundError 率 | 实测取数 | +10% |
| [FILE] 标记生成率 | 实测取数 | -5% |
| 跨工具协作(erp → code)成功率 | 实测取数 | -5% |
| AI 字面 copy 错误 | 实测取数 | -5%(应该几乎归零) |
| Kernel 池满频次 | 实测取数 | +50% |
| Kernel 重建失败率 | 实测取数 | +20% |

## F3. Phase 1 启动自检脚本

```python
# 服务启动时跑一次,失败即拒绝启动
def startup_verify_path_protocol():
    """验证新协议关键不变量"""
    # 1. nsjail bind 覆盖语义
    assert verify_nsjail_bind_override()
    
    # 2. 物理一致性:三个引用必须指向同一物理位置
    #    /workspace/staging/test.txt(沙盒内相对路径解析)
    #    /workspace/staging/test.txt(沙盒内绝对路径)
    #    {host_staging}/test.txt(host 真实路径)
    assert verify_physical_consistency()
    
    # 3. _upload_scan_dirs 仍能扫到下载/
    assert verify_auto_upload_scan()
```

---

# §G. 风险清单 + 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| gemini/deepseek 在新协议下行为异常 | 中 | 高 | Phase 0 跨模型测试 |
| 历史 chat 重载时 LLM 看到旧 `OUTPUT_DIR + 'x'` 字符串 | 高 | 低 | LLM 自适应(场景 2 已证),实际不会主动执行 |
| nsjail 后置 bind 覆盖语义跨版本变化 | 低 | 高 | 启动自检 + 锁 nsjail 版本 |
| Kernel 池满 → 无 fallback → 用户报错 | 中 | 中 | 监控 + 自动告警;告知用户重试 |
| 历史 `.echart.json` 文件已堆在 下载/ | 高 | 低 | 一次性清理脚本(Phase 1 后) |
| LLM 偶尔回退到旧写法(变量/拼接) | 低 | 低 | 沙盒 NameError 即时反馈,AI 下一轮自己修正 |

---

# §H. 文档状态

| 章节 | 内容 | 状态 |
|---|---|---|
| §A 删除清单 | 5 项 + file:line + 调用链 + 风险 | ✅ |
| §B 保留清单 | 5 项 + 不动的原因 | ✅ |
| §C 修改清单 | 6 项 + 改前改后 + 影响 | ✅ |
| §D .echart.json bug | 修复细节 | ✅ |
| §E 实施依赖图 | Step by step | ✅ |
| §F 验证矩阵 | Phase 0 测试 + Phase 1 监控 | ✅ |
| §G 风险清单 | 缓解措施 | ✅ |

---

**版本**:V1.0(2026-06-06)
**关联文档**:
- `REQ_文件路径全链路调研.md`(全链路调研基础)
- `TECH_文件路径协议统一_MVP方案.md`(MVP 总览)
- `TECH_messages数组结构净化.md`(并列独立项目,已在另一对话完成)

**审阅人**:_____(用户)
**审阅通过后**:进入 Phase 0 跨模型测试
