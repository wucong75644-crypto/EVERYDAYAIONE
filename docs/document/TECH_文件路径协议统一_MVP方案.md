# 文件路径协议统一 — MVP 方案

> **目的**:基于全链路调研 + 生产实测,定义统一文件路径协议,逐模块用真实数据验证后才进入实施。
> **前置**:`REQ_文件路径全链路调研.md`(已完成,7 层 / 40+ 文件 / 13 种格式)
> **核心改造**:删 OUTPUT_DIR/STAGING_DIR 变量层 → 相对路径 + nsjail bind 覆盖 staging
> **依据**:A-D 四组生产实测全部通过(20+ 子用例)
> **当前阶段**:**模块级真实数据测试**(本文档逐个填入测试证据)

---

## 0. 新协议规范

### 0.1 物理结构(NAS 不动)

```
/mnt/nas-workspace/org/{org_id}/{user_id}/   ← workspace 根
  ├── 上传/{YYYY-MM}/                         ← 用户上传文件(永久)
  ├── 下载/                                    ← AI 产物(永久 + OSS 同步 + 出下载卡片)
  └── staging/{conv_id}/                       ← AI 缓存(24h TTL,会话级)
```

### 0.2 沙盒 nsjail bind(关键改造)

```
nsjail --config sandbox.cfg \
  -B $WORKSPACE:/workspace \
  -B $WORKSPACE/staging/$CONV_ID:/workspace/staging   ← 新增,覆盖父目录
  -B $WORKSPACE/下载:/workspace/下载                   ← 可选,显式
```

**后置 bind 覆盖前置** — 已在 B4 实测验证 nsjail 支持。

### 0.3 AI 视角(沙盒里 cwd=/workspace)

```python
pd.read_excel("上传/2026-06/账单.xlsx")           # 读用户上传
df.to_excel("下载/差异.xlsx")                       # 写产物 → 出下载卡片
df.to_parquet("staging/cleaned.parquet")          # 写缓存(自动会话级,24h 清)
df = pd.read_parquet("staging/cleaned.parquet")   # 跨调用复读缓存
```

**统一规则**:
- 永远相对路径
- 永远 cwd = `/workspace`(kernel + nsjail 模式) 或 host workspace_dir(降级模式,**新方案删除**)
- 无字符串变量(OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 全部删除)

---

## 1. 执行模型变更:删 Subprocess 降级

### 现状(双路径)

| 模式 | 触发条件 | 路径行为 |
|---|---|---|
| Kernel + nsjail | 默认 | cwd=/workspace 虚拟路径 |
| Subprocess 降级 | Kernel 池满/崩溃/异常 | cwd=host_workspace 真实路径 |

### 新方案(单路径)

| 模式 | 替代行为 |
|---|---|
| Kernel + nsjail | 唯一路径 |
| Subprocess 降级 | **删除** |

**依据**:
- 7 天生产日志:**降级仅触发 1 次**(Jun 02 21:02,错误信息为空)
- 行业标杆(ChatGPT/Anthropic/Cursor/Jupyter)**均无降级**
- 降级带来双路径不一致 + 维护成本

### 删除范围

| 文件 | 删什么 | 行数 |
|---|---|---|
| `services/sandbox/executor.py` | `_run_in_subprocess` 方法 + 相关调用 | ~100 |
| `services/sandbox/sandbox_worker.py` | `sandbox_worker_entry` 函数 + 全部 subprocess 入口 | ~200 |
| `services/sandbox/sandbox_worker.py` | `build_scoped_open` 仍保留(kernel_worker 用) | 0 |

### 替代行为

```python
# Kernel 池满时(原本降级)→ 排队 + 超时
if not kernel_ok:
    return AgentResult(
        summary="沙盒资源紧张,请稍后重试",
        status="error",
        metadata={"retryable": True}
    )

# Kernel 崩溃(原本降级)→ 重建 1 次(已有),仍崩 → 报错
# 不再 fallback 到 subprocess
```

### 监控加固(替代降级的稳定性兜底)

- Kernel 失败率指标(Sentry / Loguru)
- 池满频次报警
- 重建失败告警(自动通知运维)

---

## 2. 全模块清单(37 个文件,按层分组)

### 2.1 入口层 — 5 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `api/routes/file.py` | 写 workspace + 同步 OSS,返回 workspace_path 相对路径 | **不动**(已经是相对路径协议) | ✅ 已验证 |
| `api/routes/image.py` | 同上 | **不动** | ✅ 已验证 |
| `core/workspace.py` | resolve_workspace_dir/upload_dir/staging_dir | **不动**(物理路径生成不变) | ✅ |
| `services/file_executor.py` | resolve_safe_path 安全校验 | **不动** | ✅ |
| `services/file_upload.py` | auto_upload → OSS → CDN URL → [FILE] 标记 | **不动**(协议层无变化) | 🔵 待测(模块 1) |

### 2.2 工具产出层 — 7 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/agent/erp_agent.py` | staging parquet 文件名生成 | **不动**(物理路径无变化) | 🔵 待测(模块 2) |
| `services/agent/file_tool_mixin.py` | file_analyze 写 staging parquet | **不动** | 🔵 待测(模块 3) |
| `services/agent/data_query_cache.py` | ensure_parquet_cache 命中机制 | **不动**(基于 mtime/size) | 🔵 待测(模块 4) |
| `services/agent/sandbox_tool_mixin.py` | code_execute 写 output_dir + auto_upload | **改 _upload_scan_dirs** + AI 提示词 | 🔵 待测(模块 5) |
| `services/agent/tool_result_envelope.py` | 大结果落盘 staging,返回 `STAGING_DIR + '/x.txt'` | **改字符串格式**(改为相对路径) | 🔵 待测(模块 6) |
| `services/media_tool_executor.py` | 图片/视频生成,collected_files 直接构造 | **不动**(URL 协议无变化) | 🔵 待测(模块 7) |
| `services/agent/scheduled_task_agent.py` | 模板路径 `STAGING_DIR + '/{tpl}'` | **改字符串格式** | 🔵 待测(模块 8) |

### 2.3 注册层 — 5 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/agent/file_path_cache.py` | 三字段注册 + 4 级模糊匹配 + write_manifest | **不动**(纠错能力保留) | 🔵 待测(模块 9) |
| `services/agent/agent_result.py` | file_ref + collected_files 字段 | **不动** | ✅ |
| `services/agent/tool_loop_executor.py` | _FILE_RE 提取 + collected_files 聚合 | **不动**(协议无变化) | ✅ |
| `services/agent/tool_output.py` | FileRef.sandbox_ref 硬编码 `STAGING_DIR + '/x'` | **改字符串格式** | 🔵 待测(模块 10) |
| `services/agent/loop_types.py` | LoopResult.collected_files 数据结构 | **不动** | ✅ |

### 2.4 沙盒层 — 6 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/sandbox/sandbox_worker.py` | OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 注入 + _hide_paths + subprocess 入口 | **删变量注入 + 删 _hide_paths + 删 subprocess 入口** | ✅ A 组验证 |
| `services/sandbox/kernel_worker.py` | _hide_paths | **删 _hide_paths** | ✅ A 组验证 |
| `services/sandbox/kernel_manager.py` | _build_command nsjail bind | **加 1 行 bind 覆盖** | 🔵 待测(模块 11) |
| `services/sandbox/scoped_os.py` | _check_path 白名单+黑名单 | **不动** | ✅ A 组验证 |
| `services/sandbox/executor.py` | _run_in_subprocess 降级路径 + _upload_scan_dirs | **删 subprocess + 改 scan_dirs** | 🔵 待测(模块 12) |
| `services/sandbox/functions.py` | build_sandbox_executor | **不动** | ✅ |

### 2.5 上下文层 — 5 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/handlers/chat_context/attachments.py` | attachments XML(无路径,用 name)| **不动**(已用名字+get_file)| ✅ |
| `services/agent/file_meta/view.py` | format_file_view(无字符串路径)| **不动** | ✅ |
| `config/code_tools.py` | 工具描述教 AI `STAGING_DIR + '/x'` 8 处 | **改全部为相对路径示例** | 🔵 待测(模块 13) |
| `config/file_tools.py` | 工具描述 `STAGING_DIR + '/_manifest.json'` | **改字符串格式** | 🔵 待测(模块 14) |
| `services/handlers/tool_digest.py` | 工具摘要 `STAGING_DIR + '/x'` | **改字符串格式** | 🔵 待测(模块 15) |

### 2.6 出口层 — 4 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/oss_service.py` | sync_workspace_file object_key=`workspace/{rel}` | **不动** | ✅ |
| `services/file_upload.py`(出口侧)| [FILE] 标记生成 | **不动** | ✅ |
| `services/agent/tool_loop_executor.py` | [FILE] 标记提取 | **不动** | ✅ |
| `services/handlers/chat_tool_mixin.py` | FilePart 构造 | **不动** | ✅ |

### 2.7 生命周期层 — 3 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/staging_cleaner.py` | TTL 扫描 + 受保护列表 | **不动** | 🔵 待测(模块 16) |
| `services/agent/file_delete_mixin.py` | file_delete 路径解析 | **不动** | ✅ |
| `services/scheduler/oss_purge_task.py` | 30 天 OSS 清理 | **不动** | ✅ |

### 2.8 ERP / 业务工具 — 2 文件

| 文件 | 当前路径处理 | 改动类型 | 实测状态 |
|---|---|---|---|
| `services/kuaimai/erp_duckdb_helpers.py` | DuckDB COPY TO `STAGING_DIR + '/x'` | **改字符串格式** | 🔵 待测(模块 17) |
| `services/agent/erp_tool_description.py` | ERP 工具描述(可能含路径示例) | **检查,如有则改** | 🔵 待测(模块 18) |

---

## 3. A-D 组实测结论(已完成,综合证据)

### A 组(基础设施 4/4 通过)

| # | 测试 | 证据 |
|---|---|---|
| A1 | nsjail cwd | `/workspace`,内容是真实工作区,中文目录 OK |
| A2 | DuckDB C 层透明写虚拟路径 | `COPY ... TO '/workspace/下载/x.parquet'` 物理落 NAS |
| A3 | 多用户隔离 | 用户 A 沙盒看不到用户 B 的 host 路径 |
| A4 | Subprocess 降级 | 虚拟路径不支持 → **删降级** |

### B 组(核心架构 4/4 通过)

| # | 测试 | 证据 |
|---|---|---|
| B1 | 相对路径写 `下载/x.xlsx` | 物理落 `$WS/下载/x.xlsx` |
| B2 | **staging 跨会话污染** | 发现 + 通过 bind 覆盖解决 |
| B3 | 多 sheet + 中文 + `(V2)` 特殊字符 | 完全 OK |
| B4 | **nsjail bind 覆盖** | `$WS/staging/{conv_id}` 覆盖 `/workspace/staging`,其他会话不可见 |

### C 组(产出场景 4/4 通过)

| # | 测试 | 文件大小 |
|---|---|---|
| C2 matplotlib PNG | ✅ | 14962 B |
| C3 ECharts JSON | ✅ | 214 B |
| C4 reportlab PDF | ✅ | 1360 B |
| C5 python-docx | ✅ | 36652 B |

### D 组(边界场景 4/5 通过,D2 性能跳过)

| # | 测试 | 证据 |
|---|---|---|
| D1 中文 + 空格 + 特殊字符 | ✅ B3 验证 |
| D3 同名冲突 | 默认覆盖(对标 Excel 另存) |
| D4 跨 sandbox 复用 staging | 第 2 次进程读到第 1 次写的 parquet |
| D5 file_analyze 风格 parquet | DuckDB 读取 OK |

---

## 4. 模块级真实数据测试(逐个填入)

### 模块 1:`services/file_upload.py` auto_upload 链路

**现状代码**:
```python
# file_upload.py:42-92
async def auto_upload(filename, size, output_dir, user_id, org_id=None):
    file_path = Path(output_dir) / safe_name
    # workspace_path 后缀(给前端代理预览)
    ws_path = FileExecutor.extract_user_relative_path(file_path, ws_base, user_id, org_id)
    # OSS 同步
    url = await oss.sync_workspace_file(file_path, rel_path)
    return f"[FILE]{url}|{name}|{mime}|{size}|{ws_path}[/FILE]"
```

**新方案下行为**:
- 不变。auto_upload 接收的是物理路径,不是 AI 视角
- _upload_scan_dirs 仍监控 output_dir(即 `$WS/下载/`)

**待测**:用真实文件触发 → 验证 `[FILE]` 标记完整 + CDN URL 可访问。

**测试结果**:🔵 待跑

---

### 模块 2:`services/agent/erp_agent.py` staging parquet 产出

**现状**:erp_agent 子 Agent 产出查询结果,写到 `STAGING_DIR + '/{domain}_{timestamp}.parquet'`,通过 file_ref 暴露给主 Agent。

**新方案下行为**:
- 物理路径不变(还是写 `$WS/staging/{conv_id}/x.parquet`)
- FileRef.sandbox_ref 改成 `staging/{filename}`(模块 10 改)
- 主 Agent 看到结果文本里说"已存到 staging/xxx.parquet"

**待测**:
- 跑一次真实 erp_agent 查询
- 验证 file_ref.path / file_ref.sandbox_ref 都正确
- 主 Agent code_execute 用相对路径 `staging/xxx.parquet` 能读到

**测试结果**:🔵 待跑

---

### 模块 3:`services/agent/file_tool_mixin.py` file_analyze

**现状**:file_analyze 写 `$WS/staging/{conv_id}/_cache_v2_{hash8}_{sheet}_{stem}.parquet`,注册到 file_path_cache。

**新方案下行为**:
- 物理路径不变
- AI 沙盒内 `get_file('账单.xlsx')` 仍返回真实 parquet 路径
- AI 也可以直接 `pd.read_parquet("staging/_cache_v2_xxx.parquet")` 自己拼(不推荐但允许)

**待测**:
- 跑 file_analyze
- 沙盒内 get_file 返回路径仍正确
- 直接相对路径读 parquet 也行

**测试结果**:🔵 待跑

---

### 模块 4:`services/agent/data_query_cache.py` parquet 缓存命中

**现状**:`ensure_parquet_cache` 用 mtime/size 双键命中。

**新方案下行为**:**完全不变**(物理路径协议不动,只是 AI 看到的形式变)。

**待测**:跑两次 file_analyze 同一文件,验证第 2 次命中缓存(秒级返回)。

**测试结果**:🔵 待跑

---

### 模块 5:`services/agent/sandbox_tool_mixin.py` code_execute + auto_upload

**现状**:
```python
# _code_execute 中:
_cache.write_manifest()                             # 写 manifest
# 之后 SandboxExecutor.execute()
# 执行后 _auto_upload_new_files 扫 output_dir
```

**新方案下行为**:
- manifest 不变(让 get_file 仍工作)
- `_upload_scan_dirs` 仍是 `[output_dir]`(= `$WS/下载/`)
- AI 写 `下载/x.xlsx` → 物理落 output_dir → 被监控

**待测**:
- 跑 code_execute,AI 代码用相对路径 `下载/x.xlsx`
- 验证 auto_upload 触发 + [FILE] 标记生成

**测试结果**:🔵 待跑

---

### 模块 6:`services/agent/tool_result_envelope.py` 大结果落盘

**现状**:超 budget 时写 `$WS/staging/{conv_id}/tool_result_{tool}_{hash}.txt`,返回:
```
<persisted-output>
  数据已落盘到 STAGING_DIR + '/tool_result_xxx.txt'
  Preview: ...
</persisted-output>
```

**新方案下行为**:返回改为:
```
<persisted-output>
  数据已落盘到 staging/tool_result_xxx.txt
  Preview: ...
</persisted-output>
```

**待测**:
- 构造一个大结果触发落盘
- 验证返回的相对路径 AI 能读

**测试结果**:🔵 待跑

---

### 模块 7:`services/media_tool_executor.py` 媒体生成

**现状**:图片/视频生成 → 直接构造 collected_files(URL 是外部 CDN,不落本地)。

**新方案下行为**:**完全不变**(不涉及本地路径)。

**待测**:跑一次图片生成 → 验证下载卡片渲染。

**测试结果**:🔵 待跑

---

### 模块 8:`services/agent/scheduled_task_agent.py` 模板路径

**现状**:`STAGING_DIR + '/{tpl}'` 字符串拼接。

**新方案下行为**:改为 `staging/{tpl}`。

**待测**:跑一次定时任务,验证模板能被沙盒读取。

**测试结果**:🔵 待跑

---

### 模块 9:`services/agent/file_path_cache.py` 4 级模糊匹配

**现状**:三字段注册表 + 归一化匹配。

**新方案下行为**:**完全不变**(LLM 中文文件名纠错能力保留)。

**待测**:用各种典型错误输入测试 4 级匹配:
- 加空格:`"4月 销售-分析.xlsx"`
- 全角:`"利润表(1-4月).xlsx"`
- 删扩展名:`"4月销售分析"`
- 截断 ≥6:`"产品库存"`

**测试结果**:🔵 待跑

---

### 模块 10:`services/agent/tool_output.py` FileRef.sandbox_ref

**现状**:
```python
@property
def sandbox_ref(self) -> str:
    return f"STAGING_DIR + '/{self.filename}'"
```

**新方案下行为**:
```python
@property
def sandbox_ref(self) -> str:
    return f"staging/{self.filename}"
```

**待测**:
- 改完代码后,跑 erp_agent → 主 Agent 链路
- 验证主 Agent 看到的 sandbox_ref 是相对路径
- AI 用这个字符串能正确读

**测试结果**:🔵 待跑

---

### 模块 11:`services/sandbox/kernel_manager.py` _build_command 加 bind

**现状**:
```python
"-B", f"{workspace_dir}:/workspace",
"-B", f"{staging_dir}:/staging",      # 现在 staging 单独 bind 到 /staging
"-B", f"{output_dir}:/output",        # 同上
```

**新方案下行为**:
```python
"-B", f"{workspace_dir}:/workspace",
"-B", f"{staging_dir}:/workspace/staging",  # ← 改为覆盖,而非单独路径
# /output 删除(因为 output_dir = workspace_dir/下载,通过 /workspace bind 自动看到)
```

**关键**:
- 旧 `STAGING_DIR=/staging` 不存在了
- AI 看到的 staging 就是 `/workspace/staging`,自动会话级

**待测**:
- 改 sandbox.cfg(或 _build_command)后启动 Kernel
- 沙盒里 `os.listdir('/workspace/staging')` 只看到本会话
- 现有 file_analyze parquet 路径仍能被 get_file 正确解析

**测试结果**:🔵 待跑

---

### 模块 12:`services/sandbox/executor.py` _upload_scan_dirs

**现状**:监控 `[output_dir]`(即 `$WS/下载/`)。

**新方案下行为**:**不变**(仍监控 `$WS/下载/`,因为它在 NAS host 物理路径,跟沙盒虚拟路径无关)。

**待测**:
- AI 写 `下载/x.xlsx` 后,_auto_upload_new_files 仍扫到
- [FILE] 标记仍正常生成

**测试结果**:🔵 待跑

---

### 模块 13-15:`config/code_tools.py` / `config/file_tools.py` / `tool_digest.py` 提示词

**现状**(grep 8 处关键 + 多处零散):
```python
# code_tools.py:120
duckdb.sql("SELECT ... FROM read_parquet(STAGING_DIR + '/文件名')")
# code_tools.py:125
with open(OUTPUT_DIR + '/图表名.echart.json', 'w')
# code_tools.py:130
df.to_excel(OUTPUT_DIR + '/报表.xlsx')
# file_tools.py:176
with open(STAGING_DIR + '/_manifest.json')
# tool_digest.py:145,151
" → STAGING_DIR + '/{t['staged']}'"
```

**新方案下行为**:全部改为相对路径示例:
```python
df = duckdb.sql("SELECT ... FROM read_parquet('staging/xxx.parquet')")
with open("下载/图表.echart.json", "w") as f: ...
df.to_excel("下载/报表.xlsx")
with open("staging/_manifest.json") as f: ...
" → staging/{filename}"
```

**待测**:改完后跑真实 AI 对话,验证 AI 学到了新写法。

**测试结果**:🔵 待跑

---

### 模块 16:`services/staging_cleaner.py` TTL 扫描

**现状**:扫描 `$WS/staging/{conv_id}/`,排除受保护文件名(`.duckdb.db` / `_manifest.json` / `_bak_*` / `session_files.json`)。

**新方案下行为**:**完全不变**(物理路径不变,清理规则不变)。

**待测**:验证清理任务仍正确识别 24h 过期文件 + 不误清受保护文件。

**测试结果**:🔵 待跑

---

### 模块 17-18:`erp_duckdb_helpers.py` / `erp_tool_description.py`

**现状**:可能含 `STAGING_DIR + '/x'` 字符串提示。

**待测**:grep 出所有具体行,改为相对路径。

**测试结果**:🔵 待跑

---

## 5. 实施步骤(按依赖顺序)

### Phase 1:基础设施改造(沙盒)
1. `kernel_manager.py:_build_command` 改 bind:`-B {staging_dir}:/workspace/staging`(模块 11)
2. `executor.py` 删 _run_in_subprocess 方法 + 相关调用
3. `sandbox_worker.py` 删 sandbox_worker_entry + 全部 subprocess 入口代码
4. `sandbox_worker.py` 删 OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 注入(line 445-452)
5. `sandbox_worker.py` 删 _hide_paths(line 690-696)
6. `kernel_worker.py` 删 _hide_paths(line 90-98)

### Phase 2:工具结果协议改造
7. `tool_output.py` 改 FileRef.sandbox_ref 返回格式
8. `tool_result_envelope.py` 改 persisted-output 文本格式
9. `scheduled_task_agent.py` 改模板路径字符串
10. `erp_duckdb_helpers.py` 改 SQL COPY 字符串

### Phase 3:提示词改造
11. `code_tools.py` 改 8 处提示词示例
12. `file_tools.py` 改 manifest 路径示例
13. `tool_digest.py` 改 staged 路径格式
14. `erp_tool_description.py` 检查并改

### Phase 4:测试 + 灰度
15. 所有单元测试通过
16. 内部账号端到端测试(每个场景至少 3 个真实数据)
17. 5% 灰度(feature flag)
18. 监控指标(下载卡片生成率 / staging 隔离 / OSS 同步成功率)
19. 全量切换
20. 1-2 周观察期后,清理旧兼容代码

---

## 6. 灰度策略

### Feature Flag 设计

```python
# settings.py 新增
sandbox_new_path_protocol: bool = False  # 默认 false,逐步开启

# kernel_manager._build_command 中:
if settings.sandbox_new_path_protocol:
    bind = ["-B", f"{staging_dir}:/workspace/staging"]
else:
    bind = ["-B", f"{staging_dir}:/staging", "-B", f"{output_dir}:/output"]
```

### 灰度阶段

| Phase | 范围 | 时长 | 退出标准 |
|---|---|---|---|
| 0 | 内部测试账号 | 1 天 | 10 个真实场景全通 |
| 1 | 5% 用户 | 3 天 | 关键指标无退化 |
| 2 | 20% | 2 天 | 无新增问题 |
| 3 | 50% | 2 天 | 无新增问题 |
| 4 | 100% | — | 观察 1 周 |
| 5 | 清理兼容代码 | — | — |

### 关键指标(每个 Phase 都监控)

| 指标 | 阈值 |
|---|---|
| sandbox PermissionError 率 | ≤ baseline 110% |
| sandbox FileNotFoundError 率 | ≤ baseline 110% |
| [FILE] 标记生成率(产物场景)| ≥ 95% |
| AI 写 `下载/...` 的比例(新协议)| Phase 1 后 ≥ 50% |
| AI 字面 copy `OUTPUT_DIR/...` 错误 | < 1% |
| OSS 同步成功率 | ≥ 99% |
| CDN URL 可访问率 | ≥ 99% |
| Kernel 池满频次 | < 1/天 |

### 回滚预案

- **Phase 1 阶段任何指标退化**:feature flag 一键回滚
- **代码层面**:保留旧 OUTPUT_DIR/STAGING_DIR 变量注入逻辑作为兜底(if 分支),不删除直到 Phase 4 结束

---

## 7. 风险清单 + 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| AI 不学新写法,继续 OUTPUT_DIR | 中 | 中 | 提示词强化 + feature flag 期保留兼容前缀替换 |
| nsjail 系统级故障(无降级兜底)| 极低 | 高 | 加 Kernel 失败率监控 + 告警 |
| Kernel 池满频次上升 | 中 | 中 | 排队 30s + 用户友好报错;池大小动态调整 |
| 提示词改后 AI 困惑 | 中 | 中 | 一次性切完,不要新旧混发 |
| 跨工具协作(erp → code_execute)在新协议下 staging 路径错 | 低 | 高 | 模块 2 + 模块 10 充分测试 |
| 字符串 `OUTPUT_DIR/x` 字面 copy 仍偶发 | 低 | 低 | _scoped_open 加 5 行兼容前缀替换(过渡期 2 周后删) |

---

## 8. 文档状态

| 章节 | 状态 |
|---|---|
| §0 协议规范 | ✅ 完成 |
| §1 删降级方案 | ✅ 完成(7 天 1 次数据 + 行业对照) |
| §2 全模块清单 | ✅ 完成(37 文件分 7 组) |
| §3 A-D 实测结论 | ✅ 完成(全部通过) |
| §4 模块级真实数据测试 | 🔵 18 个模块,5 个已通过 A-D 覆盖,13 个待跑 |
| §5 实施步骤 | ✅ 完成 |
| §6 灰度策略 | ✅ 完成 |
| §7 风险清单 | ✅ 完成 |

---

## 9. 下一步

1. **用户审本文档**(规范 + 模块清单 + 灰度)
2. **跑剩余 13 个模块的真实数据测试**(填入 §4)
3. 所有测试通过 → 开始 Phase 1 实施
4. Phase 1 完成 → Phase 4 灰度 + 监控
5. 1-2 周观察期 → 清理兼容代码

---

**版本**:V1.0(2026-06-04)
**作者**:基于全链路调研 + 4 组生产实测
**审阅人**:_____(用户)
