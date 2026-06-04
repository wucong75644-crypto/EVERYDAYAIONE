# 文件路径全链路调研

> **目的**：评估「沙盒路径协议重构」可行性前的全链路调研。
> **范围**：覆盖 7 层（入口/工具产出/注册/沙盒/上下文/出口/生命周期）。
> **约束**：调研阶段不动任何代码。
> **下一步**：调研 → 用户审 → MVP 设计 → MVP 实现 → 灰度 → 全量。

---

## 0. 全链路总览

### 0.1 物理基础设施

```
NAS（阿里云 NFS，1PB）：/mnt/nas-workspace/
  ├── org/{org_id}/{user_id}/        ← 企业用户工作区
  │     ├── 上传/{YYYY-MM}/...        ← 用户上传文件
  │     ├── 下载/                     ← code_execute 产物（出下载卡片）
  │     ├── staging/{conv_id}/        ← 中间数据缓存（24h TTL）
  │     ├── _bak_*                    ← workspace 备份（restore_file 用）
  │     └── _manifest.json            ← file_path_cache 写入的名字↔路径映射
  └── personal/{md5(user_id)[:8]}/    ← 个人用户（无 org_id）

OSS（阿里云 OSS + CDN）：
  object_key 格式：workspace/org/{org_id}/{user_id}/{rel_path}
  CDN URL：https://{oss_cdn_domain}/workspace/...

沙盒进程（nsjail bind mount，生产已启用）：
  /workspace → bind workspace_dir（虚拟根，AI 看到）
  /staging   → bind staging_dir
  /output    → bind output_dir
  cwd = /workspace
```

### 0.2 完整数据流

```
[1] 用户上传 (api/routes/file.py)
    ↓ 流式写入 + 唯一文件名（{stem}_{6位UUID}{ext}）
[2] 落 NAS：/mnt/nas-workspace/org/.../{user_id}/上传/{YYYY-MM}/x_a1b2c3.xlsx
    ↓ 异步同步 OSS（失败兜底拼 CDN URL）
[3] OSS：workspace/org/.../{user_id}/上传/{YYYY-MM}/x_a1b2c3.xlsx
    ↓ 前端拿 workspace_path + url

[4] AI 调 file_analyze('x_a1b2c3.xlsx')
    ↓ ensure_parquet_cache：mtime/size 快照命中或新转换
[5] 生成 parquet：staging/{conv_id}/_cache_v2_{hash8}_{sheet}_{stem}.parquet
    ↓ file_path_cache.register（三字段：name + workspace + parquet）
    ↓ file_path_cache.set_analyzed(True)（驱动 attachments XML status 切换）

[6] AI 调 code_execute（写代码）
    ↓ sandbox_tool_mixin._code_execute 前：
      cache.write_manifest() → staging/_manifest.json
      （含所有已注册文件的 name → 路径映射）
    ↓ kernel_worker / sandbox_worker_entry
      注入 globals：OUTPUT_DIR, STAGING_DIR, WORKSPACE_DIR, SKILLS_DIR, get_file
      替换 builtins.open = build_scoped_open(...)

[7] AI 代码：
    path = get_file('x_a1b2c3.xlsx')                            ← 读
    df = duckdb.sql(f"FROM read_parquet('{path}')").df()        ← 算
    df.to_excel(OUTPUT_DIR + '/diff.xlsx')                      ← 写（本次 bug 来源）
    ↓ _scoped_open 白名单校验 + 物理落 NAS workspace/下载/

[8] executor._auto_upload_new_files 快照对比
    ↓ 检测到新文件 → 调 auto_upload(filename, size, output_dir)
    ↓ NAS 文件 → OSS sync_workspace_file → CDN URL
    ↓ 输出 [FILE]url|name|mime|size|workspace_path[/FILE]

[9] kernel_worker._hide_paths：result.replace(output_dir, 'OUTPUT_DIR')
    ↓ 真实路径替换为变量名给 LLM（隐藏 UUID）

[10] tool_loop_executor._FILE_RE 提取 [FILE] → collected_files
     ↓ [FILE] 标记替换为占位文本（防 LLM 篡改 URL）

[11] chat_tool_mixin → FilePart → WebSocket → 前端
     ↓ FileCard 渲染下载卡片
```

### 0.3 涉及的代码模块总览

| 层 | 模块 | 总行数 | 调研结果章节 |
|---|---|---|---|
| 入口 | api/routes/file.py + image.py + file_upload.py + workspace.py + file_executor.py | ~1100 | §1 |
| 工具产出 | erp_agent + file_tool_mixin + data_query_cache + sandbox_tool_mixin + tool_result_envelope + media_tool_executor + sandbox/executor.py | ~3500 | §2 |
| 注册 | file_path_cache + loop_types + tool_loop_executor + agent_result + tool_output | ~1500 | §3 |
| 沙盒 | sandbox_worker + kernel_worker + scoped_os + executor + functions + kernel_manager + validators + sandbox_constants | ~2600 | §4 |
| 上下文 | chat_context/attachments + chat_context_mixin + file_meta/view + config/code_tools + config/file_tools + 多处提示词 | ~1500 | §5 |
| 出口 | file_upload + oss_service + tool_loop_executor (FILE 提取) + chat_tool_mixin (FilePart) + frontend FileCard | ~1000 | §6 |
| 生命周期 | staging_cleaner + scheduler/oss_purge_task + file_delete_mixin + migrations/111_deleted_files.sql | ~600 | §7 |

---

## 1. 入口层（用户文件进入系统）

### 1.1 模块清单

| 文件 | 行数 | 一句话职责 |
|------|------|----------|
| api/routes/file.py | 641 | `/files/upload` 和 `/workspace/upload` 双写（NAS+OSS）接口 |
| api/routes/image.py | 155 | `/images/upload` 图片上传（FormData 双写 + base64 OSS-only） |
| core/workspace.py | 87 | 全局唯一真相源：resolve_workspace_dir / resolve_upload_dir / resolve_staging_dir |
| services/file_executor.py | 256 | 安全文件操作：resolve_safe_path / generate_unique_filename / get_cdn_url |
| services/file_upload.py | 93 | auto_upload 公共模块：NAS → OSS 同步 → [FILE] 标记 |

### 1.2 路径生成规则（core/workspace.py:25-86）

```
企业用户：base / "org" / {org_id} / {user_id}
个人用户：base / "personal" / md5(user_id)[:8]

上传子目录：{workspace} / "上传" / {YYYY-MM}/   ← 按月分桶
下载子目录：{workspace} / "下载"/              ← 系统建（产物落点）
staging：  {workspace} / "staging" / {conv_id}/
```

### 1.3 唯一文件名防覆盖（file_executor.py:217-229）

```python
def generate_unique_filename(self, filename: str) -> str:
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    short_id = uuid.uuid4().hex[:6]
    return f"{stem}_{short_id}{suffix}"  # report → report_a1b2c3.xlsx
```

**同名永不覆盖**——加 6 位 UUID 后缀，新旧文件并存。

### 1.4 NAS → OSS → CDN URL 转换公式

```
NAS 绝对路径：/mnt/nas-workspace/org/123/user-xyz/上传/2026-06/report_a1b2c3.xlsx
    ↓ relative_to(workspace_root="/mnt/nas-workspace")
rel_path：    org/123/user-xyz/上传/2026-06/report_a1b2c3.xlsx
    ↓ object_key = f"workspace/{rel_path}"
OSS key：     workspace/org/123/user-xyz/上传/2026-06/report_a1b2c3.xlsx
    ↓ get_url() → https://{cdn_domain}/{object_key}
CDN URL：     https://cdn.example.com/workspace/org/123/user-xyz/上传/2026-06/report_a1b2c3.xlsx
```

### 1.5 多租户隔离（file_executor.py:86-94）

```python
if org_id:
    self._root = base / "org" / org_id / user_id
elif user_id:
    user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]
    self._root = base / "personal" / user_hash
```

### 1.6 resolve_safe_path 七层防御（file_executor.py:161-215）

1. 绝对/相对路径识别
2. 符号链接拒绝（`raise PermissionError`）
3. 禁止文件名白名单（`.env`、`.git`、`credentials.json`）
4. 禁止扩展名（`.pem`、`.key`、`.p12`）
5. staging 目录隔离（API 入口禁止直接访问 staging 路径）
6. 路径必须在 `_root` 内
7. realpath 解析后再次校验

### 1.7 大小 + MIME 白名单（api/routes/file.py:152-163）

```
单文件上限：100MB
扩展名白名单：44 种（不含 SVG，防 XSS）
  txt/csv/json/yaml/xml/md/log/py/js/ts/html/css/sql
  pdf/doc/docx/xls/xlsx/ppt/pptx
  png/jpg/jpeg/gif/webp/bmp/zip
```

### 1.8 auto_upload 返回协议（file_upload.py:20-92）

```
返回格式：[FILE]{url}|{name}|{mime}|{size}|{workspace_path}[/FILE]

兜底链路：
  1. OSS sync 成功 → 返回真实 CDN URL
  2. OSS 失败 + 配置了 oss_cdn_domain → 拼接 CDN URL（NAS 已写入）
  3. 都失败 → 返回 "❌ 文件处理失败"
```

### 1.9 入口层痛点

- **workspace_path 注册责任分散**：上传响应含 workspace_path，但**入口层不负责注册到 file_path_cache**。下游（chat_context_mixin）需自行 register，存在漏注册风险。
- **OSS 同步异步且不报错**：长期失败时用户误以为上传成功 → 实际下载链接失效。
- **CDN URL 硬编码前缀 `workspace/`**：oss_service.py:371，未来若改 OSS 目录结构需改多处。

---

## 2. 工具产出层

### 2.1 产物清单总览

| 工具 | 产物 | 物理位置 | 命名规则 | 给 AI 的形式 |
|------|------|---------|---------|------------|
| file_analyze | Parquet | staging/{conv_id}/ | `_cache_v2_{hash8}_{sheet}_{stem}.parquet` | format_file_view (Markdown) + file_path_cache 注册 |
| erp_agent | Parquet | staging/{conv_id}/ | `{domain}_{timestamp}.parquet` | AgentResult.file_ref（sandbox_ref = `STAGING_DIR + '/...'`） |
| data_query_cache | Parquet 缓存 | staging/{conv_id}/ | `_cache_v2_{hash8}_{sheet}_{stem}.parquet` | 命中后跳过转换（ensure_parquet_cache） |
| code_execute | xlsx/csv/png/json/... | output_dir（workspace/下载/）| AI 自取 | _auto_upload_new_files 扫描 + [FILE] 标记 |
| media_tool | PNG/JPG/MP4 | 外部 CDN（不落本地）| adapter 命名 | AgentResult.collected_files（直接 URL）|
| tool_result_envelope | TXT | staging/{conv_id}/ | `tool_result_{tool}_{digest8}.txt` | `<persisted-output>...STAGING_DIR + '/file'</persisted-output>` |

### 2.2 三个目录的语义分工

| 目录 | 用途 | 谁写 | OSS 同步 | TTL |
|------|------|------|---------|-----|
| **workspace** | 用户长期数据 + 上传文件 | API/file_search/用户 | ✅ 实时 | 不清（用户主动）|
| **staging** | 中间缓存 + 大结果分流 | file_analyze/erp_agent/tool_result_envelope | ❌ 不同步 | 24h |
| **下载/**（output_dir）| 用户最终产出 | code_execute（AI 写） | ✅ auto_upload | 不清 |

### 2.3 关键代码点

#### ensure_parquet_cache 命中机制（data_query_cache.py:177-220）

```python
# 关键点：mtime + size 双键命中
src_mtime, src_size = stat.st_mtime, stat.st_size
if _snapshot_matches(cache_path, snapshot_path, src_mtime, src_size):
    return str(cache_path), None  # ← 秒级返回，不重转
```

- **命中条件**：parquet 存在 + snapshot 存在 + (mtime, size) 完全一致
- **失效**：源文件改了 mtime 或 size → 自动重新转换
- **V1→V2 强制刷**：cache_key 加 `_cache_v2_` 前缀

#### tool_result_envelope 大结果落盘（tool_result_envelope.py:207-235）

- **阈值**：MAIN_AGENT_BUDGET = 2000，CODE_EXECUTE_BUDGET = 30000 字符
- **触发**：`len(result) > budget && tool_name not in _NO_TRUNCATE`
- **返回 LLM 的格式**：
  ```
  <persisted-output>
    数据已落盘到 STAGING_DIR + '/tool_result_erp_a1b2c3.txt'
    Preview: <前 N 字符>
  </persisted-output>
  ```

#### code_execute 自动上传（sandbox/executor.py:312-381）

- **监控目录**：仅 `output_dir`（即 workspace/下载/）
- **快照机制**：执行前 snapshot {mtime, size}，执行后对比新增/覆盖
- **白名单扩展名**：xlsx/xls/csv/tsv/png/jpg/pdf/json/txt/docx/pptx
- **特殊处理**：
  - 图片读 PIL 宽高 → 存 `_image_dims`
  - `.echart.json` 读 ECharts option → 存 `_chart_options`

### 2.4 工具产出层痛点

1. **staging 文件跨会话失效**：staging/{conv_id}/ 按对话隔离，同一用户多会话查同一 Excel 每次都重转 parquet
2. **parquet 缓存失效策略粗糙**：mtime/size 完全相同才命中，理论上"内容变了但大小恰好相同"会用旧缓存（极少但存在）
3. **file_path_cache 注册分散**：file_search / file_analyze / _register_files_from_output / _register_staging_files 各自注册，可能遗漏
4. **路径相对/绝对混用**：erp_agent.file_ref.path 是绝对路径，parquet basename 是相对，manifest 依赖 "/" 区分，边界易错

---

## 3. 注册层（系统记忆有哪些文件）

### 3.1 模块清单

| 文件 | 行数 | 一句话职责 |
|------|------|----------|
| services/agent/file_path_cache.py | 327 | 会话级三字段注册表 + 4 级模糊匹配 + manifest 生成 |
| services/agent/agent_result.py | 340 | AgentResult 类（file_ref 字段 + collected_files 字段）|
| services/agent/loop_types.py | 114 | LoopResult.collected_files 数据结构 |
| services/agent/tool_loop_executor.py | 781 | _FILE_RE 提取 + collected_files 聚合 |
| services/agent/sandbox_tool_mixin.py | 320 | _register_files_from_output / _register_staging_files |
| services/agent/tool_output.py | 143 | FileRef 数据结构（含 sandbox_ref 属性）|

### 3.2 三种核心数据结构

#### FileEntry（file_path_cache 内）

```python
name: str         # 显示名（原始文件名，可含中文/全角）
workspace: str    # 工作区绝对路径（用于 file_analyze / file_delete）
parquet: str      # staging 中的 parquet 路径（已分析后）
analyzed: bool    # 跨轮持久标记（驱动 attachments XML status 切换）
```

#### FileRef（tool_output.py:76-122）

```python
path: str          # 绝对路径（内部用，不暴露 LLM）
filename: str      # 带域标识（如 warehouse_stock_xxx.parquet）
sandbox_ref: str   # 返回 "STAGING_DIR + '/{filename}'" ← LLM 朝向的标准引用
row_count: int     # 写入方负责填准
columns: list      # 完整列元信息（name + dtype + label）
```

#### collected_files（loop_types.py:80-82）

```python
List[Dict[str, Any]]:
  {"url": ..., "name": ..., "mime_type": ..., "size": ..., "workspace_path": "..."}
```

### 3.3 注册时机表

| 触发点 | 谁注册 | 注册什么 | 给谁用 |
|-------|------|--------|------|
| 文件上传 / @ 引用 | chat_context_mixin | name + workspace | file_analyze / file_delete 路径解析 |
| file_analyze 完成 | _file_analyze (file_tool_mixin) | set_parquet + set_analyzed | code_execute 沙盒查询 + attachments XML status 切换 |
| 沙盒代码 os.listdir 发现文件 | _register_files_from_output (sandbox_tool_mixin:236-262) | name + workspace（regex 提取 stdout）| 后续 file_delete |
| ERP/ERPAgent 输出 | _register_staging_files (sandbox_tool_mixin:263+) | filename + path + parquet（两个相同）| 下一轮沙盒查询复用 |
| code_execute 前 | _code_execute (sandbox_tool_mixin:67-75) | 调用 write_manifest() | 沙盒 get_file() 名字→路径映射 |

### 3.4 4 级模糊匹配实战价值（根治 LLM 中文文件名错误）

| 场景 | LLM 输入 | 注册值 | 命中级别 |
|------|---------|--------|---------|
| 加空格 | `"4月 销售分析.xlsx"` | `"4月销售分析.xlsx"` | L2 归一化（NFKC + 删非中文/数字/字母）|
| 全角→半角 | `"利润表（1-4月）.xlsx"` | `"利润表(1-4月).xlsx"` | L2 归一化 |
| 删扩展名 | `"4月销售分析"` | `"4月销售分析.xlsx"` | L3 Stem |
| 截断 ≥6 字符 | `"产品库存"` | `"产品库存表.csv"` | L4 前缀（防短名误匹配）|

**关键保护**（file_path_cache.py:222-228）：前缀匹配仅 `len(input_stem) >= 6` 启用。

### 3.5 write_manifest 协议（file_path_cache.py:246-292）

```python
# manifest 值按是否含 / 分流：
- 不含 / → parquet basename（已 analyze）→ 沙盒拼 staging_dir 前缀
- 含 /   → workspace 相对路径（Word/PDF/数据/文本）→ 沙盒拼 workspace_dir 前缀
- ./    → 强制加 ./ 前缀（workspace 根目录文件，区分 parquet basename）
```

### 3.6 注册层痛点

1. **跨工具协作时数据丢失**：ERPAgent 只填 parquet，没填 workspace → file_delete 找不到
2. **manifest 反序列化失效**：write_manifest 假设 `staging_dir = {workspace_root}/staging/{conv_id}/`，格式不符则反推失败
3. **跨会话失效**：file_path_cache 是会话级 + 7 天 TTL + 1000 上限，超出后路径丢失
4. **AI 拿不到名字**：沙盒 os.listdir 发现新文件但忘 print → regex 不匹配 → 未注册
5. **L4 前缀匹配的不确定性**：两个文件 `产品库.xlsx` 和 `产品库存表.csv`，LLM 写 `产品库` 命中第一个（按字典序）

### 3.7 注册层改动边界

- **必须保留**：三字段分离 + 4 级匹配 + manifest + analyzed 状态
- **可淘汰**：file_registry 已废弃（tool_loop_executor.py:69 注释）
- **可改进**：分散注册点 → 集中到 ChatContextMixin

---

## 4. 沙盒层（最核心）

### 4.1 模块清单

| 文件 | 行数 | 一句话职责 |
|------|------|----------|
| sandbox/sandbox_worker.py | 706 | 子进程入口：chdir + build_scoped_open + 变量注入（无状态）|
| sandbox/kernel_worker.py | 266 | 有状态 REPL：stdin/stdout JSON 协议 + 每次执行前 _reset_security |
| sandbox/scoped_os.py | 297 | os/shutil/pathlib 包装：_check_path 白名单 + 黑名单 |
| sandbox/executor.py | 382 | SandboxExecutor 外壳：AST 验证 + 文件快照 + 路由 kernel/subprocess |
| sandbox/functions.py | 81 | build_sandbox_executor 工厂：workspace/staging/output 目录解析 |
| sandbox/kernel_manager.py | 433 | Kernel 进程池：nsjail 启动 + bind mount 配置 |
| sandbox/validators.py | 119 | AST 验证 + 黑名单模块/函数 |
| sandbox/sandbox_constants.py | 185 | SAFE_BUILTINS + BLOCKED_IMPORT_MODULES |

### 4.2 注入到沙盒 globals 的变量清单（sandbox_worker.py:445-554）

| 变量 | 类型 | 值 | 用途 |
|------|------|-----|------|
| **OUTPUT_DIR** | PathStr | output_dir（绝对路径）| 沙盒内写产物 → 自动上传 |
| **STAGING_DIR** | PathStr | staging_dir（绝对路径）| DuckDB/临时数据 |
| **WORKSPACE_DIR** | PathStr | workspace_dir（绝对路径）| 工作区目录 |
| **SKILLS_DIR** | PathStr | skills_dir（绝对路径）| 文件处理技能（只读）|
| **get_file** | function | _get_file（493-554） | 按名字查 manifest 返回路径 |
| **os** | ScopedOS | 包装版 | 受限 os（路径校验）|
| **shutil** | ScopedShutil | 包装版 | cp/mv 但禁 rmtree |
| **open** | function | _scoped_open（替换 builtins.open）| 所有 open 经此校验 |
| **pd** | PandasProxy | nrows 截断 + 清洗 | pandas 预热 |
| **duckdb** | duckdb | 默认连接 + memory_limit | DuckDB 磁盘模式 |
| **Path** | ScopedPath | 包装版 | 破坏性方法走 scoped_os |

### 4.3 PathStr 设计（sandbox_worker.py:32-46）

```python
class PathStr(str):
    """str 子类 + 支持 / 运算符
    
    用途：让 OUTPUT_DIR / "文件名" 这种代码能正常工作
    """
```

### 4.4 _scoped_open 完整逻辑（sandbox_worker.py:163-216）

```python
# 1. 相对路径解析到 workspace
if not os.path.isabs(path_str):
    path_str = os.path.join(_ws_dir, path_str)
resolved = os.path.realpath(path_str)

# 2. 白名单检查
_allowed_prefixes = [
    realpath(workspace_dir),     # 用户工作区
    realpath(staging_dir),       # staging
    realpath(output_dir),        # output
    realpath(skills_dir),        # skills（只读）
    realpath(tempfile.gettempdir())  # /tmp
]

# 3. 只读系统文件白名单
_readonly_system_files = {
    "/etc/apache2/mime.types", "/private/etc/apache2/mime.types",
    "/etc/mime.types", "/usr/share/misc/mime.types", "/usr/share/zoneinfo"
}

# 4. 文件不存在自动纠错（仅读模式）
#    扫 OUTPUT_DIR / STAGING_DIR 找同名 → 找相似（_find_similar_file_global）
```

### 4.5 scoped_os._check_path 双层校验（scoped_os.py:40-62）

```python
# 黑名单优先：staging 父目录需精确白名单覆盖
_denied = [staging_parent]  # workspace/staging
for d in _denied:
    if resolved == d or resolved.startswith(d + os.sep):
        # 必须有非 workspace-root 的白名单条目覆盖
        if not any(... for a in _allowed if a != _ws):
            raise PermissionError(...)

# 白名单检查
if not any(resolved == p or resolved.startswith(p + os.sep)
           for p in _allowed):
    raise PermissionError(...)
```

**两套校验的差异**：
- `_scoped_open` → 拦截 read/write（builtins.open）
- `scoped_os._check_path` → 拦截 listdir/stat/rename/...（os 模块）

### 4.6 _hide_paths 路径隐藏（kernel_worker.py:90-98 / sandbox_worker.py:690-696）

```python
# 沙盒执行完毕，replace 真实路径为变量名
result = result.replace(output_dir, "OUTPUT_DIR")
result = result.replace(workspace_dir, "WORKSPACE_DIR")
result = result.replace(skills_dir, "SKILLS_DIR")
```

**后果**：LLM 看到 `OUTPUT_DIR/x.xlsx` 字面字符串 → 下一轮字面 copy → **今天的 bug**。

### 4.7 Kernel vs Subprocess 双模式对比

| 维度 | Kernel + nsjail | Subprocess 裸 Python |
|------|----------------|---------------------|
| 启动 | KernelManager 用 nsjail 启动 | multiprocessing.Process(spawn) |
| cwd | `/workspace`（nsjail bind 虚拟根）| workspace_dir（host 真实路径）|
| 路径可见性 | `/workspace /staging /output` 虚拟 | host 绝对路径，无虚拟 |
| C 层库行为 | 读 `/workspace/...` → bind mount 透明解析 | 读 host 绝对路径，直接成功 |
| 变量跨调用 | 进程生命周期内持久 | 每个 subprocess 独立 |
| _scoped_open 白名单 | 检查虚拟路径 | 检查 host 绝对路径 |
| _hide_paths 替换 | replace `/workspace` | replace host 绝对路径 |
| 降级触发 | — | Kernel 池满 / 崩溃重建失败 / KernelManager 不可用 |

### 4.8 设计意图追溯（关键 commits）

| Commit | 关键变更 | 根因/动机 |
|--------|---------|----------|
| **9bb34ac** | 删除虚拟路径别名（`/staging/ /output/`）| PyArrow 等 C 层库绕过 Python `scoped_open`，写 `/staging/x` 找不到 |
| **131c271** | staging 路径协议统一为 STAGING_DIR 变量 | 4 处工具泄露相对路径，沙盒 cwd 是 workspace_dir 不是项目根 → DuckDB 读失败 |
| **9f146d6** | 文件编号系统（根治 LLM 逐 token 生成中文文件名错误）| LLM 拼中文路径常加空格/改破折号/截断 |
| **01fb2c1** | 编号系统改归一化匹配 | 编号 LLM 也容易 copy 错 |
| **6041896** | 三字段注册表 | 分离源文件 vs parquet 缓存 |
| **199a779** | manifest 改存 parquet 文件名 + 相对路径 | nsjail bind 后沙盒能直接读，不暴露宿主机绝对路径 |
| **494f445** | manifest 支持未 analyze 文件 | 用 / 区分两种值（含 / → workspace；不含 → parquet basename）|

### 4.9 沙盒层痛点

1. **OUTPUT_DIR 字符串陷阱**：_hide_paths 替换后 LLM 字面 copy → 写入失败（**今天的 bug**）
2. **跨调用篡改**：用户代码可能覆盖 builtins/open/os → _reset_security 每次执行前重置兜底
3. **资源耗尽**：Kernel MAX_KERNELS=4 + MAX_LIFETIME=1800s；Subprocess RLIMIT_AS=2GB
4. **降级路径不一致**：nsjail 模式看虚拟路径，subprocess 模式看 host 路径，AI 跨模式时困惑

### 4.10 沙盒层改动影响面

如果删除 OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 变量：

| 必改文件 | 改动 |
|---------|------|
| sandbox_worker.py:445-452 | 删除变量注入 |
| sandbox_worker.py:690-696 | 删除 _hide_paths |
| sandbox_worker.py:187-203 | 文件纠错逻辑可能重写 |
| kernel_worker.py:90-98 | 删除 _hide_paths |
| tool_executor.py / tool_result_envelope.py | 返回格式改相对路径 |
| 所有提示词 | 工具描述 OUTPUT_DIR → 相对路径教学 |
| 测试 | E2E 测试改 |

---

## 5. 上下文层（AI 看到什么路径）

### 5.1 模块清单

| 文件 | 行数 | 一句话职责 |
|------|------|----------|
| services/handlers/chat_context/attachments.py | 187 | 渲染 attachments XML（name/type/format/size/status）|
| services/agent/file_tool_mixin.py | 430 | file_search/file_analyze 输出含 `get_file('文件名')` 指引 |
| services/agent/file_meta/view.py | 309 | format_file_view 生成 Markdown（无路径字符串）|
| **config/code_tools.py** | 167 | **code_execute 工具描述 — ERP 版含 `STAGING_DIR + '/文件名'` 字面 3 处** |
| config/file_tools.py | 182 | file_search/file_analyze 描述 — 含 `STAGING_DIR + '/_manifest.json'` |
| sandbox/kernel_worker.py | 90-98 | _hide_paths 路径替换 |
| services/agent/tool_output.py | 109 | FileRef.sandbox_ref = `STAGING_DIR + '/{filename}'` |
| services/handlers/tool_digest.py | 145,151 | 工具摘要含 `STAGING_DIR + '/文件名'` |
| services/agent/scheduled_task_agent.py | 282-283 | 模板路径 `STAGING_DIR + '/{tpl}'` |

### 5.2 attachments XML 完整 schema

```xml
<attachments count="N" hint="status 字段是行动指引；每个文件按 status 决定下一步操作">
  <file>
    <name>文件名（转义）</name>
    <type>图片|数据文件|文档|文本|二进制</type>
    <format>扩展名（不含点）</format>
    <size>大小</size>
    <dimensions>W×H（仅图片）</dimensions>
    <source>本轮上传|工作区引用</source>
    <status>四种文本之一：
      ① 图片：已自动注入视觉，不要调用任何文件读取工具
      ② 未分析数据：未分析。如需查询数据，先调用 file_analyze("文件名")
      ③ 已分析数据：已分析。直接在 code_execute 中用 get_file("文件名") + duckdb 查询
      ④ PDF/Word：在 code_execute 中用 [pdfplumber/python-docx] + get_file("文件名") 读取
    </status>
  </file>
</attachments>
```

**关键**：attachments XML **不含真实路径**，全部用 `get_file('文件名')` 引导。

### 5.3 "教 AI 用 STAGING_DIR/OUTPUT_DIR/WORKSPACE_DIR" 的所有位置（grep 8 处关键）

| 位置 | 代码 |
|------|------|
| code_tools.py:120 | `duckdb.sql("SELECT ... FROM read_parquet(STAGING_DIR + '/文件名')")` |
| code_tools.py:125 | `with open(OUTPUT_DIR + '/图表名.echart.json', 'w')` |
| code_tools.py:130 | `df.to_excel(OUTPUT_DIR + '/报表.xlsx')` |
| code_tools.py:152,156 | CODE_ROUTING_PROMPT 重复教 2 次 |
| file_tools.py:176 | `with open(STAGING_DIR + '/_manifest.json')` |
| tool_output.py:109 | `return f"STAGING_DIR + '/{self.filename}'"` |
| tool_digest.py:145,151 | `" → STAGING_DIR + '/{t['staged']}'"` |
| scheduled_task_agent.py:282-283 | `f"STAGING_DIR + '/{tpl['name']}'"` |

**全项目 grep 结果：193 处提及，关键 8 处在提示词/工具描述里教 AI 字面 copy**。

### 5.4 上下文层关键痛点

1. **字面 copy 陷阱**：工具描述给的示例是**字面字符串**（不是 f-string），LLM 以为直接写就行
2. **字符串 vs 变量混淆**：`STAGING_DIR` 既是 Python 全局变量，又是提示词里的字符串字面值
3. **ERP Agent 过度暴露**：CODE_ROUTING_PROMPT 三处教 `STAGING_DIR + '/文件名'`，比主 Agent 版本更直白
4. **_hide_paths 延迟隐藏**：Agent 中间输出仍含路径，下一轮字面 copy
5. **多处独立的路径格式教学**：code_tools / file_tools / tool_digest / scheduled_task_agent 各自教，容易不一致

### 5.5 上下文层改动建议

| 当前 | 建议 |
|------|------|
| `duckdb.sql("FROM read_parquet(STAGING_DIR + '/文件名')")` 字面 | `path = get_file('文件名'); duckdb.sql(f"FROM read_parquet('{path}')")` |
| `with open(OUTPUT_DIR + '/x.xlsx', 'w')` 字面 | `path = put_file('x.xlsx'); open(path, 'w')` 或相对路径 `open('下载/x.xlsx', 'w')` |
| FileRef.sandbox_ref 硬编码 | 改成 `get_file('{filename}')` 调用形式 |

---

## 6. 出口层（文件交付给用户）

### 6.1 模块清单

| 文件 | 行数 | 职责 |
|------|------|------|
| services/file_upload.py | 20-92 | auto_upload：NAS → OSS → CDN URL + [FILE] 标签 |
| services/oss_service.py | 360-384 | sync_workspace_file：NAS 本地路径上传 OSS |
| services/oss_service.py | 302-319 | get_url：生成 CDN URL |
| services/agent/tool_loop_executor.py | 31-35, 698-716 | _FILE_RE 提取 + collected_files 聚合 + 占位文本替换 |
| services/handlers/chat_tool_mixin.py | 16-18, 473-501 | _FILE_PATTERN 二次提取 + FilePart 生成 |
| services/agent/loop_types.py | 80-81 | LoopResult.collected_files 数据结构 |
| schemas/message.py | 101-109 | FilePart 模型 |
| frontend/src/components/chat/media/FileCard.tsx | 15-80 | 下载卡片 UI |

### 6.2 [FILE] 协议完整规范

**格式**：`[FILE]{url}|{name}|{mime_type}|{size}[|{workspace_path}][/FILE]`

| 字段 | 必填 | 语义 |
|------|------|------|
| url | ✓ | CDN URL，用户直接下载 |
| name | ✓ | 文件名，前端展示 + 下载本地命名 |
| mime_type | ✓ | MIME 类型，决定占位文本 + 前端预览 |
| size | ✓ | 字节数 |
| workspace_path | ✗ | 相对 workspace root 的路径，后端注册 cache + AI 可读 |

**正则定义**（tool_loop_executor.py:33-35）：

```python
_FILE_RE = re.compile(
    r"\[FILE\](?P<url>[^|]+)\|(?P<name>[^|]+)\|(?P<mime>[^|]+)\|"
    r"(?P<size>\d+)(?:\|(?P<wspath>[^[\]]+))?\[/FILE\]"
)
```

### 6.3 [FILE] 标记生成位置（grep）

| 位置 | 谁生成 |
|------|--------|
| services/file_upload.py:71,86 | auto_upload — code_execute 产物 |
| services/agent/tool_loop_executor.py:711 | collected_files 追加 |
| services/media_tool_executor.py:101,119 | image_agent/video_agent 直接构造 |

### 6.4 [FILE] 标记消费位置

| 位置 | 消费 |
|------|------|
| tool_loop_executor.py:701 | _FILE_RE.finditer() 逐个匹配 |
| tool_loop_executor.py:716 | _FILE_RE.sub() 替换占位文本（防 LLM 篡改 URL）|
| chat_tool_mixin.py:247,341 | AgentResult.summary 二次提取 → FilePart |
| frontend FileCard.tsx:20 | 渲染下载卡片 |

### 6.5 完整出口数据流

```
[1] code_execute 沙盒：df.to_excel(OUTPUT_DIR + '/x.xlsx')
[2] executor._auto_upload_new_files 快照对比新增
[3] auto_upload(filename, size, output_dir, user_id):
    - rel_path = file.relative_to(workspace_root)
    - oss.sync_workspace_file(file_path, rel_path)
      object_key = f"workspace/{rel_path}"
      bucket.put_object_from_file(object_key, file_path)
    - 兜底：CDN URL = f"https://{cdn_domain}/workspace/{encoded_key}"
[4] 返回 [FILE]{cdn_url}|{name}|{mime}|{size}|{workspace_path}[/FILE]
[5] tool_loop_executor 提取到 _collected_files
[6] 替换为占位文本 "📎 文件已生成: {name}（下载卡片将自动展示...）"
[7] AgentResult.summary 含占位文本，collected_files 含 [FILE] 元数据
[8] chat_tool_mixin 生成 FilePart → WebSocket → 前端
[9] FileCard 渲染下载卡片，下载点击 → 直接 GET CDN URL
```

### 6.6 出口层痛点

1. **workspace_path 计算失败沉默**：ws_path_suffix 为空 → 后端无法注册 file_path_cache → AI 后续 file_analyze 读不到
2. **OSS sync 与 CDN 兜底不同步**：object_key 格式必须一致（两者都用 `workspace/{rel_path}` 前缀）
3. **[FILE] 正则不支持文件名含 `|`**：边界 case（不过 generate_unique_filename 已净化）
4. **CDN 兜底依赖 NAS 挂载**：未自动检测，需运维保证回源配置

### 6.7 出口层改动影响面

**协议本身无需改动**（workspace_path 仍是相对路径，前端无感）。

**需调整**：
- file_upload.py:54-56,66,81 — rel_path 计算逻辑
- oss_service.py:371 — object_key 前缀

---

## 7. 生命周期（文件什么时候清）

### 7.1 模块清单

| 文件 | 行数 | 职责 |
|------|------|------|
| services/staging_cleaner.py | 205 | staging 24h TTL + 500MB 容量兜底 |
| services/agent/file_path_cache.py | 326 | 对话级路径缓存池，TTL 7 天 + 限额 1000 |
| services/agent/file_delete_mixin.py | 207 | 文件删除 + 恢复 |
| services/scheduler/oss_purge_task.py | 107 | 每日凌晨 3 点清理超 30 天的 deleted_files |
| migrations/111_deleted_files.sql | 21 | deleted_files 表 schema |

### 7.2 完整生命周期表

| 文件类型 | 物理位置 | TTL/清理 | OSS 同步 | 恢复 |
|---------|---------|---------|---------|------|
| **Staging 临时** | `{workspace}/staging/{conv_id}/` | 24h 或 容量>500MB | 不上传 | 不可恢复 |
| **Staging DuckDB** | `.duckdb.db` | 不清（受保护）| 不上传 | N/A |
| **Workspace 源文件** | `{workspace}/org/.../下载/` | 不自动清 | 实时同步 | 用户主动删 → 30 天恢复 |
| **Workspace 备份** | `_bak_*` | 不清（restore 依赖）| 实时同步 | N/A |
| **OSS 已删** | `workspace/.../x.xlsx` | 30 天后清（oss_purge_loop）| 标记 purged | deleted_files.purged=FALSE 可恢复 |
| **路径缓存** | 内存 `_caches[conv_id]` | 7 天无访问或 LRU | 内存无持久化 | 进程重启失效 |

### 7.3 受保护文件清单（不被自动清）

```python
# staging_cleaner.py:27-29
.duckdb.db / .duckdb_temp/  # DuckDB 磁盘文件
_bak_*                       # workspace 备份（restore_file 依赖）
_manifest.json               # file_search 索引清单
session_files.json           # 会话文件注册表
```

`_tmp_*` 前缀文件**无条件删**（写入中断残留）。

### 7.4 删除恢复流程

```
User → file_delete(['报表.xlsx'])
    ↓ _file_delete:50 从 file_path_cache 取路径
    ↓ os.remove(abs_path)  ← 立即删 NAS
    ↓ _record_deleted_files()  ← fire-and-forget 写 deleted_files 表
        relative_path = workspace 相对路径
        oss_object_key = 'workspace/' + relative_path
        purge_after = now() + 30 days
        purged = FALSE

User → restore_file('报表.xlsx')
    ↓ 查 deleted_files: NOT purged AND purge_after > now()
    ↓ 从 OSS 下载回 workspace
    ↓ 标记 purged=TRUE

每日 3:00 oss_purge_loop:
    ↓ SELECT * WHERE purge_after < now() AND NOT purged
    ↓ oss.delete_workspace_object()
    ↓ 标记 purged=TRUE
```

### 7.5 生命周期痛点

1. **容量满竞态**：staging_cleaner 异步 fire-and-forget，多 worker 并发可能都判 <500MB
2. **进程重启丢缓存**：file_path_cache._caches 内存重启清空
3. **OSS 不一致窗口**：oss_purge_task 异常中断 → deleted_files purged=TRUE 但 OSS 仍存在
4. **staging 无用户隔离**：清理是全局路径扫描，容量竞争可能伤害不活跃用户
5. **相对路径与 TTL 耦合**：当前依赖绝对路径前缀（_bak_, .duckdb），改相对路径要重新编码规则

### 7.6 生命周期改动建议

如果统一相对路径：
- **TTL 按目录层级**：staging/* → 24h；workspace/*/下载/* → 不清；workspace/*/_bak_* → 不清
- **deleted_files 加 lifecycle_tag 字段**：TEMP/BACKUP/WORKSPACE
- **保持当前生命周期差异**——staging vs workspace 必须区分

---

## 8. 路径格式横向对照（关键章节）

| 层 | 格式 | 谁看到 | 例子 |
|---|---|---|---|
| NAS 真实绝对 | `/mnt/nas-workspace/org/{uuid}/{uuid}/...` | 系统内部 | `/mnt/nas-workspace/org/eadc.../398.../上传/2026-06/x.xlsx` |
| OSS object_key | `workspace/{rel_path}` | OSS 服务 | `workspace/org/eadc.../398.../上传/2026-06/x.xlsx` |
| CDN URL | `https://{domain}/workspace/{rel_path}` | 用户（下载）| `https://cdn.xxx.com/workspace/...` |
| workspace_path | `{相对 workspace root}` | 用户 + AI（部分场景）| `上传/2026-06/x.xlsx` |
| attachments XML | `name` 字段（不显示路径）| AI | `<name>x.xlsx</name>` |
| 沙盒 nsjail 虚拟 | `/workspace/{rel} /staging/{rel} /output/{rel}` | sandbox 内部 | `/workspace/上传/2026-06/x.xlsx` |
| 沙盒 PathStr 变量 | `OUTPUT_DIR / STAGING_DIR / WORKSPACE_DIR` | AI（沙盒内）| `OUTPUT_DIR + '/x.xlsx'` |
| FileRef.sandbox_ref | `STAGING_DIR + '/{filename}'` 字符串 | AI（工具结果回灌）| `STAGING_DIR + '/erp_xxx.parquet'` |
| _hide_paths 替换 | 真实路径 → "OUTPUT_DIR"/"WORKSPACE_DIR" | AI（沙盒输出回灌）| `OUTPUT_DIR/x.xlsx` |
| [FILE] 标记 | `[FILE]url\|name\|mime\|size\|wspath[/FILE]` | tool_loop_executor | `[FILE]https://.../x.xlsx\|x.xlsx\|...\|1024\|下载/x.xlsx[/FILE]` |
| manifest 值 | parquet basename（无 /）vs workspace 相对路径（含 /）| sandbox get_file | `x.parquet` 或 `./上传/2026-06/x.xlsx` |
| collected_files | `{url, name, mime, size, workspace_path}` dict | 前端下载卡片 | — |

**13 种不同格式分布在系统各层**。

---

## 9. 现状痛点统合

### 9.1 字符串陷阱（本次 bug 根因）

| 痛点 | 触发场景 |
|------|---------|
| 工具描述教 AI 字面 `STAGING_DIR + '/x'` | code_tools.py:120,125,130 + 5 处其他 |
| _hide_paths 显示 `OUTPUT_DIR/x.xlsx` | AI 字面 copy → 字符串字面值 |
| FileRef.sandbox_ref 硬编码字符串 | LLM 看到工具结果含字面字符串 |

**链路**：工具描述/工具结果输出字符串 → LLM 字面 copy → 沙盒 open("OUTPUT_DIR/x.xlsx") → 相对路径解析到 `workspace/OUTPUT_DIR/x.xlsx`（不存在）→ FileNotFoundError 或 PermissionError

### 9.2 注册分散 + 容易断链

- file_search / file_analyze / _register_files_from_output / _register_staging_files 各自注册
- ERPAgent 只填 parquet 不填 workspace → file_delete 找不到
- manifest 反序列化失败（staging_dir 格式不符）→ 沙盒路径错

### 9.3 跨会话/跨进程失效

- file_path_cache._caches 内存级，7 天 TTL，进程重启清空
- staging 24h TTL，超时后 AI 重 file_analyze
- 没有持久化路径协议

### 9.4 双模式不一致

- Kernel + nsjail：虚拟路径 `/workspace`
- Subprocess 降级：host 真实路径 `/mnt/nas-workspace/...`
- AI 跨模式时看到的路径不同

### 9.5 多处独立的路径教学

- code_tools / file_tools / tool_digest / scheduled_task_agent / tool_output 各自教不同格式
- 改一处忘改另一处 → AI 看到矛盾示例

---

## 10. 重构边界

### 10.1 必须保留（动了就崩）

| 模块 | 不能动的原因 |
|------|-------------|
| **file_analyze + file_meta** | 脏 Excel 治理核心：多级表头/合并单元格/汇总行/订单级粒度/公式提取 |
| **file_path_cache 三字段 + 4 级匹配** | LLM 拼中文文件名错误的兜底（删除会导致大量"文件不存在"）|
| **manifest 机制** | 沙盒 get_file 查表的唯一通道 |
| **nsjail bind mount** | C 层库（PyArrow/DuckDB）路径透明化的核心 |
| **OSS 同步 + CDN URL 协议** | 用户下载链路 |
| **deleted_files 30 天恢复期** | 用户误删的兜底 |

### 10.2 可以动（本次 bug 的根因）

| 模块 | 动什么 |
|------|--------|
| **OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 变量注入**（sandbox_worker.py:445-452）| 删除变量层 |
| **_hide_paths 路径替换**（kernel_worker.py:90-98）| 删除（相对路径不需要隐藏）|
| **PathStr 自定义类** | 删除（不再需要）|
| **工具描述里的字面字符串**（code_tools.py 8 处）| 全部改为 get_file 函数调用形式 或 相对路径 |
| **FileRef.sandbox_ref 硬编码格式** | 改为 get_file 调用形式 |
| **_register_files_from_output regex 提取**（sandbox_tool_mixin:236-262）| _auto_upload 快照已覆盖，可淘汰 |

### 10.3 不确定（需 MVP 验证）

| 模块 | 风险点 |
|------|--------|
| **AI 是否真的会用相对路径** | 训练分布友好，但需提示词验证 |
| **C 层库（DuckDB/PyArrow）在 nsjail bind 下行为** | 历史 commit 9bb34ac 说有问题，但当年没 nsjail，现在不知道 |
| **subprocess 降级模式下相对路径** | 没 nsjail bind 时 cwd 是 host workspace_dir，需验证 |
| **跨工具协作的 staging 文件复用** | erp_agent 产 parquet → code_execute 读 → AI 怎么写路径？|

---

## 11. MVP 验证范围建议

### 11.1 MVP 的核心命题

**「AI 在沙盒里用相对路径写产物（"下载/x.xlsx"）能完整走完 → OSS → CDN URL → 下载卡片链路」**

### 11.2 MVP 必须验证的 7 个核心点

| # | 场景 | 验证方法 |
|---|------|---------|
| 1 | nsjail 模式下，AI 写 `to_excel("下载/x.xlsx")` 文件物理落到 NAS | 写测试代码 + 检查文件存在 |
| 2 | subprocess 降级模式下，相对路径行为一致 | 强制走降级路径测试 |
| 3 | C 层库（DuckDB COPY TO）写相对路径 | `duckdb.sql("COPY ... TO '下载/x.parquet'")` |
| 4 | _auto_upload_new_files 检测到新文件 → OSS 同步 | 验证 OSS object_key + CDN URL |
| 5 | [FILE] 标记生成 → collected_files 流转 → 下载卡片 | 端到端 |
| 6 | AI 字面 copy "下载/x.xlsx" 字符串仍工作（无字符串陷阱） | 跟现状对比 |
| 7 | 多 AI 工具协作：erp_agent 产 parquet → code_execute 读 → 写下载 | 跨工具完整链路 |

### 11.3 MVP 范围控制（最小集）

**只改 4 件事**（保持其他不变）：
1. 沙盒提示词新增「优先用相对路径写产物」说明（不删旧的 OUTPUT_DIR 教学，兼容期并存）
2. 在 attachments XML 的 status 字段加上「写产物用 `下载/x.xlsx` 相对路径」提示
3. 把 1 个低风险工具的描述（如 `code_execute` 主 Agent 版本）改为相对路径示例
4. feature flag 控制：5% 用户走新提示词

**不动的**：
- file_path_cache / file_analyze / get_file / manifest（保留兼容）
- OUTPUT_DIR 变量注入（保留兼容）
- _hide_paths（保留兼容）

### 11.4 MVP 灰度策略

```
Phase 0：内部测试（1 天）
  ├─ 10 个真实场景跑通（见 Layer 3 测试方案）
  └─ 监控 PermissionError / FileNotFoundError 频次

Phase 1：5% 灰度（3 天）
  ├─ feature flag 控制
  ├─ 监控关键指标（下载卡片生成率 / 字面 copy 错误率 / AI 用新写法的比例）
  └─ 任何指标退化立即回滚

Phase 2：全量切换（条件：Phase 1 无退化）
  ├─ 提示词主推新写法
  └─ 旧 OUTPUT_DIR 写法保留兼容 2 周

Phase 3：清理（确认无回潮）
  ├─ 删除 OUTPUT_DIR / STAGING_DIR 变量注入
  ├─ 删除 _hide_paths
  ├─ 删除 FileRef.sandbox_ref 硬编码
  └─ 提示词全部统一
```

### 11.5 MVP 监控指标

| 指标 | 阈值 |
|------|------|
| sandbox PermissionError 率 | ≤ baseline 110% |
| sandbox FileNotFoundError 率 | ≤ baseline 110% |
| [FILE] 标记生成成功率 | ≥ 95% sandbox 写入操作 |
| AI 使用新写法（相对路径）的比例 | ≥ 50%（Phase 1 后）|
| AI 字面 copy "OUTPUT_DIR/x" 错误率 | < 1% |
| OSS 同步成功率 | ≥ 99% |
| CDN URL 可访问率 | ≥ 99% |

---

## 12. 风险清单

### 12.1 已知风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| AI 不学新写法，回退到 OUTPUT_DIR | 高 | 中（兼容期保留）| 提示词强化 + 灰度观察 |
| C 层库在 nsjail 下行为异常 | 中 | 高 | MVP 第 3 个点验证 |
| Subprocess 降级模式相对路径解析错 | 中 | 高 | MVP 第 2 个点验证 |
| 跨工具协作时 staging 路径丢失 | 中 | 中 | MVP 第 7 个点验证 |
| _auto_upload_new_files 没监控到新位置 | 低 | 中 | MVP 第 4 个点验证 |
| 灰度时新旧用户混乱 | 低 | 低 | feature flag 严格按 user_id 分桶 |

### 12.2 不确定项（需 MVP 数据回答）

1. AI 看到 `下载/x.xlsx` 提示词后，会主动用相对路径吗？比例？
2. AI 会不会写 `/workspace/下载/x.xlsx` 这种 nsjail 虚拟绝对路径？降级模式会崩吗？
3. multi-sheet Excel / 大文件 (>100MB) / 中文文件名 / 特殊字符路径，相对路径方案都能 cover 吗？
4. file_analyze 后的 parquet 路径，AI 用 `staging/{conv_id}/_cache_v2_xxx.parquet` 这种内部命名还是用 `get_file('x.xlsx')`？
5. 文件名冲突时，put_file 加后缀 vs 覆盖，哪种用户体验更好？

### 12.3 不在本次范围（明确排除）

- 删除 file_analyze / file_meta 数据治理能力（必须保留）
- 改 OSS 协议 / CDN URL 格式
- 改前端下载卡片 UI
- 改 deleted_files 30 天恢复期
- 改 NAS 挂载结构

---

## 13. 调研结论

### 13.1 一句话总结

**当前文件路径协议跨 6 层（入口 → 工具 → 注册 → 沙盒 → 上下文 → 出口）使用 13 种不同格式表达，本次 bug（OUTPUT_DIR 字符串陷阱）只是「沙盒变量层」+「上下文层提示词」的局部问题**。

### 13.2 重构路线（修正之前所有猜测）

**真正可行的最小重构**：
1. **保留** file_analyze / file_meta / file_path_cache / manifest / get_file / nsjail bind mount / OSS 协议 / deleted_files
2. **改造**「上下文层」提示词：教 AI 用相对路径写产物（`下载/x.xlsx`），保留 get_file 读
3. **未来**（验证后）：删除变量注入 + _hide_paths + FileRef.sandbox_ref 字符串

### 13.3 当下立即可做的

**5 行字符串纠错兜底**（_scoped_open 加前缀替换）：
- 不影响任何现有逻辑
- 自动还原 AI 字面 copy 的 OUTPUT_DIR 字符串
- **生产 Excel 写入失败立即修复**
- 给后续大重构留出空间

### 13.4 等用户审完后的下一步

| Step | 内容 |
|------|------|
| Step 1 | 用户审本调研文档，确认/修正调研发现 |
| Step 2 | 出 MVP 设计文档（基于调研发现的具体方案）|
| Step 3 | 用户审 MVP 设计 |
| Step 4 | MVP 实现（feature flag 隔离，新旧并存）|
| Step 5 | MVP 验证（内部 → 5% 灰度 → 全量）|
| Step 6 | 旧代码下线（确认无回潮）|

**总周期预估**：2-3 周。

---

## 附录 A：所有相关文件清单（按层归类）

### 入口层（~1100 行）
- backend/api/routes/file.py (641)
- backend/api/routes/image.py (155)
- backend/core/workspace.py (87)
- backend/services/file_executor.py (256)
- backend/services/file_upload.py (93)

### 工具产出层（~3500 行）
- backend/services/agent/erp_agent.py
- backend/services/agent/file_tool_mixin.py (430)
- backend/services/agent/data_query_cache.py (~1000)
- backend/services/agent/sandbox_tool_mixin.py (320)
- backend/services/agent/tool_result_envelope.py
- backend/services/media_tool_executor.py
- backend/services/sandbox/executor.py (382)

### 注册层（~1500 行）
- backend/services/agent/file_path_cache.py (327)
- backend/services/agent/loop_types.py (114)
- backend/services/agent/tool_loop_executor.py (781)
- backend/services/agent/agent_result.py (340)
- backend/services/agent/tool_output.py (143)

### 沙盒层（~2600 行）
- backend/services/sandbox/sandbox_worker.py (706)
- backend/services/sandbox/kernel_worker.py (266)
- backend/services/sandbox/scoped_os.py (297)
- backend/services/sandbox/functions.py (81)
- backend/services/sandbox/kernel_manager.py (433)
- backend/services/sandbox/validators.py (119)
- backend/services/sandbox/sandbox_constants.py (185)

### 上下文层（~1500 行）
- backend/services/handlers/chat_context/attachments.py (187)
- backend/services/handlers/chat_context_mixin.py
- backend/services/agent/file_meta/view.py (309)
- backend/config/code_tools.py (167)
- backend/config/file_tools.py (182)
- backend/services/handlers/tool_digest.py
- backend/services/agent/erp_tool_description.py

### 出口层（~1000 行）
- backend/services/file_upload.py (与入口层共用)
- backend/services/oss_service.py
- backend/services/agent/tool_loop_executor.py (与注册层共用)
- backend/services/handlers/chat_tool_mixin.py
- backend/schemas/message.py
- frontend/src/components/chat/media/FileCard.tsx

### 生命周期（~600 行）
- backend/services/staging_cleaner.py (205)
- backend/services/agent/file_delete_mixin.py (207)
- backend/services/scheduler/oss_purge_task.py (107)
- backend/migrations/111_deleted_files.sql

---

## 附录 B：关键 git commit 索引

| Commit | 信息 | 调研章节 |
|--------|------|---------|
| 9bb34ac | 删除虚拟路径别名(/staging/ /output/)，根治 C 层库绕过 scoped_open | §4.8 |
| 131c271 | staging 路径协议统一，全链路 STAGING_DIR 变量替代相对路径 | §4.8 |
| 9f146d6 | 文件编号系统——根治 LLM 逐 token 生成中文文件名错误 | §4.8, §3.3 |
| 01fb2c1 | 编号系统改为归一化匹配 | §3.3 |
| 6041896 | 三字段注册表 + get_file 按用途返回 | §3.3, §4.8 |
| 199a779 | manifest 改存 parquet 文件名而非完整路径 | §4.8 |
| 494f445 | manifest 改存相对路径，让沙盒 jail 内能读到 Word/PDF/数据/文本 | §4.8 |
| 6333e2e | confirm_delete 流程简化 + 砍本地备份 + OSS 30 天延迟清理 | §7.5 |
| b8ceb74 | NAS+OSS 双写架构（从 ossfs 改 NAS 实体）| §1.4 |
| 72c6017 | [FILE] 标记补全 workspace_path | §6.2 |

---

**调研完成，等待用户审。**
