# file_analyze 工具重构方案

> **版本**：V1.2（PathB 流式重构 + null_ratio bug 修复）
> **任务等级**：A 级（涉及 8+ 文件、核心链路、数据结构升级、AI 调用方式重构）
> **关联文档**：本文档替代 `TECH_文件处理系统.md` 的 §三/§四/§六部分（L1→L2→L3 章节作废）
> **回归目标**：现有 7 个测试文件 60+ 测试类全部通过

## V1.1 → V1.2 修正记录（2026-06-03）

通过实测 53MB 真实订单文件 + 85K 行真实小文件，发现 V1.1 设计仍存在 2 个真实问题，本次修复：

| 项 | V1.1 状态 | V1.2 修正 |
|---|---|---|
| **PathB 大文件读取** | fastexcel 一次性 to_pandas（500K 行 1187 MB / 4.5s）；硬上限 `MAX_TOTAL_ROWS = 800_000` | **改为 calamine + 100K 行 chunk 累加**（500K 行 1474 MB / 5.4s + chunk 释放真生效，不累积）；改为单元格上限 `MAX_TOTAL_CELLS = 25_000_000`（按列数自适应，800K→约 1M~1.5M 行） |
| **`null_ratio` bug（PathA + PathB）** | `BaseScanner._scan_columns / _scan_suspicious_rows` 用 `isna()` 判空 | **加 `df.mask(df.eq(""), np.nan)`**：fastexcel 在 fallback-to-string 列上把空 cell 读成 `""`，原本 `isna()` 漏判，导致 53MB 文件「街道列」null率 0%（实际 83%）；修复后所有列与 openpyxl ground truth 一致 |
| **PathB 流式累加器** | 无 | 新增 `_PathBChunkAccumulator` 类 + `_build_path_b_sample_idx / _build_path_b_key_sample_idx`：跨 chunk 累加列证据 / 可疑行 / key_samples，行号自动映射到 Excel 1-indexed |
| **代码重复** | 3 处独立定义 `_col_index_to_letter`（file_meta / excel_cleaner / file_scanners）| `file_scanners.col_letter` 改为 `file_meta._col_index_to_letter` 的 alias（消除 1 份重复）；`excel_cleaner.structure._col_index_to_letter_local` 因循环依赖保留本地副本 |
| **类型识别精度** | fastexcel 整列 fallback-to-string 时长 ID / 数值 / 日期都被读为 string | calamine 天然保留原生类型：长 ID → str（不丢精度，免兜底逻辑）/ 日期 → datetime / 数值 → int/float |

**V1.2 第一批实测验证（PathB 重构 + null_ratio bug）**：
- 全量回归 312 个相关测试通过（test_file_scanners / test_excel_cleaner_package / test_file_meta / test_file_ai_* / test_file_xml_renderer 等）
- 23 项端到端审计全过（含 AI prompt 核心指纹一致 / XML renderer 单向修正 / 5 个 PathB 单测 / 极端边界 / 列名边界 / header_row 边界 / classified_dist 完全等价 / 第二真实文件 68MB）
- PathA 真实 85K 文件：街道 0.00%→0.7754✅ / 商品规格备注 3.10%→0.6782✅ / 订单商品备注 96.30%→1.0000✅
- PathB 真实 53MB 文件：街道 0.00%→0.8300✅ / 商品规格备注 3.79%→0.8407✅ / 订单商品备注 96.87%→1.0000✅

---

## V1.2 第二批修正记录（2026-06-04）

第一批修复后做整体流程审核，实测发现 **5 个真实存在的问题**（每个都用代码 + 端到端测试证明），全部修复。

| # | 问题 | V1.2 第一批状态 | V1.2 第二批修正 | 用户感受 |
|---|------|--------|--------|---------|
| **1** | **路径 D 完全不消费 AI 决策** | `_convert_all_sheets_to_parquet` 签名只接收 `(excel_path, cache_path, src_mtime, src_size, snapshot_path)` —— 所有 sheet 无条件合并，AI 的 `sheets[i].role={data/meta/aggregated/skip}` 决策完全丢弃 | 加 `decision, strategy` 参数：按 `decision.sheets[i].role ∈ {meta/aggregated/skip}` 过滤；clean_excel 调用同时传 strategy | AI 说 "Sheet2 是说明可跳过" → Parquet 真的不含 Sheet2，金额不再被汇总行重复计算 |
| **2** | **路径 C 完全不消费 AI 决策** | `convert_multi_region` 签名只接收 `column_mapping`，不接收 strategy/decision —— 所有区域无条件合并 | 加 `decision, strategy` 参数：按 `decision.regions[i].role == "skip"` 过滤；clean_excel 调用同时传 strategy；`_convert_excel_to_parquet` 调用点改为从 `_AIDecisionAdapter._d` 取真实 decision | 单 sheet 多区域文件：下半段汇总区不再被并入 Parquet 算错数 |
| **3** | **`""` 全空列没被识别为空列** | `_remove_empty_rows_cols` 列方向用 `df.iloc[:,i].isna().all()`，fastexcel fallback-to-string 列里空 cell 是 `""` → 漏判 | 列检测前加 `df.mask(df.eq(""), np.nan)`：让 `""` 也被识别为空。注意行方向已用 `isna() \| str.strip().eq("") \| eq("nan")` 三重判空，所以原本正确，只补列方向 | Parquet schema 不再多一列被误以为有数据的全空列 |
| **4** | **缓存 key 不含 AI 决策版本** | `_cache_v2_{path_hash}_{sheet}_{stem}.parquet` —— 升级 AI 模型 / 修改 scanner / 改 prompt 后旧缓存仍命中（仅看 mtime + size） | 新增模块常量 `_CACHE_SCHEMA_VERSION = "v2.1"`，cache_name 改为 `_cache_{_CACHE_SCHEMA_VERSION}_{path_hash}_..._{stem}.parquet`；升级时改这个数字即可全局失效 | 升级 AI/扫描器后旧缓存自动失效重算，新 AI 能力立即生效 |
| **5** | **大文件分块用 fastexcel `skip_rows` —— 不是真流式** | `_process_one_chunk` 用 fastexcel `load_sheet(skip_rows=skip)` 并行 + 每 chunk 新建 reader；实测 5 块每块都重复全量解压（500K 行 15s / RSS 994→2530MB 线性累积 → 1M 行 OOM） | 大文件分支完全重写：`python_calamine.iter_rows()` + 100K 行 chunk 顺序处理 + `mask("", NaN)` + 同源 clean_excel/_apply_column_mapping/_cast_to_schema 链路 | 80MB 大文件不再 OOM；50万行 6.8s/1095MB peak（之前 15s/2530MB） |

### V1.2 第二批的关键代码路径

**路径 D 过滤逻辑**（`_convert_all_sheets_to_parquet`）：
```python
sheet_role_map: dict[str, str] = {}
if decision is not None:
    for s in getattr(decision, "sheets", []):
        sheet_role_map[s.name] = s.role

for name in sheet_names:
    role = sheet_role_map.get(name, "data")
    if role in ("meta", "aggregated", "skip"):
        skipped_sheets.append((name, role))
        logger.info(f"Sheet skipped per AI decision | sheet={name} | role={role}")
        continue
    # ... 读取 + clean_excel(..., strategy=strategy)
```

**路径 C 过滤逻辑**（`convert_multi_region`）：
```python
region_role_map: dict[int, str] = {}
if decision is not None:
    for r in getattr(decision, "regions", []):
        region_role_map[r.region_id] = r.role

for i, region in enumerate(regions):
    region_id = i + 1
    role = region_role_map.get(region_id, "primary")
    if role == "skip":
        skipped_regions.append((region_id, role))
        continue
    # ... 读取 + clean_excel(..., strategy=strategy)
```

**`""` 列识别修复**（`_remove_empty_rows_cols`）：
```python
# 修 fastexcel fallback-to-string 列把空 cell 读成 "" 而非 NaN
df_for_empty_check = df.mask(df.eq(""), np.nan)
for i, col in enumerate(df.columns):
    if df_for_empty_check.iloc[:, i].isna().all():
        empty_col_names.append(col_str)
```

**大文件 calamine 重写**（`_convert_excel_to_parquet` 大文件分支）：
```python
wb = python_calamine.CalamineWorkbook.from_path(excel_path)
ws = wb.get_sheet_by_index(target_sheet) if isinstance(target_sheet, int) \
     else wb.get_sheet_by_name(target_sheet)

chunk_buf, chunk_idx, rows_seen = [], 0, 0
for raw_row in ws.iter_rows():
    if rows_seen <= actual_start:
        if rows_seen == actual_start:
            col_names = [str(v) for v in raw_row]
        rows_seen += 1; continue
    chunk_buf.append(list(raw_row)); rows_seen += 1
    if len(chunk_buf) >= _CHUNK_SIZE:
        df_chunk = pd.DataFrame(chunk_buf).mask(... eq("")... )
        df_chunk.columns = col_names
        df_chunk, chunk_report = clean_excel(df_chunk, ..., chunk_row_offset=_CHUNK_SIZE * chunk_idx, strategy=strategy)
        if _col_mapping: df_chunk = _apply_column_mapping(df_chunk, _col_mapping)
        table = _cast_to_schema(df_chunk, target_schema)
        writer.write_table(table)
        chunk_buf = []; chunk_idx += 1
        del df_chunk, table; gc.collect()
# 收尾 chunk 同样处理
```

### V1.2 第二批实测验证

**修复前（实测铁证）**：
- 路径 D：构造 3-sheet 文件（Data + Metadata + Aggregated）→ 3 个 sheet 全部进 Parquet（含 Metadata 标题文本 + Aggregated 汇总数字）
- 路径 C：构造 2 区域文件 → Region_2（汇总区）1 行强制并入 Parquet
- 修 3：全 `""` 列 `all_empty_str` 不在 `empty_cols` 列表
- 修 4：cache_name 无版本字符串
- 修 5：fastexcel skip_rows 5 块 = 每块 2.92~3.11s / RSS 994→2530MB 线性累积

**修复后（端到端测试）**：
- 路径 D：传 decision (Metadata=meta, Aggregated=aggregated) → Parquet 仅含 Data 的 2 行 ✅
- 路径 C：传 decision (Region_2=skip) → Parquet 仅含 Region_1 ✅
- 修 3：全 `""` 列正确进入 `empty_cols` 列表；partial_empty / normal 不误判 ✅
- 修 4：`_CACHE_SCHEMA_VERSION` 不同 → cache_name 不同 → 旧缓存自动失效 ✅
- 修 5：50 万行 6.8s / RSS peak 1095MB（vs fastexcel 15s / 2530MB） ✅

**全量回归**：file_analyze 相关 364 个测试全部通过（含 6 个新增测试覆盖 5 项修复 + table_region_detector 25 个测试）；无任何破坏。

**向后兼容性**：
- `_convert_all_sheets_to_parquet(decision=None, strategy=None)` 时所有 sheet 都进（旧行为）
- `convert_multi_region(decision=None, strategy=None)` 时所有 region 都进（旧行为）
- `_remove_empty_rows_cols` 加 mask 只让"原本漏识别的 `""` 列"被正确标注，不破坏现有 NaN 列识别
- `_CACHE_SCHEMA_VERSION = "v2.1"`：第一次升级会让所有旧缓存失效一次性重算，之后稳定

### V1.2 改动文件汇总

| 文件 | 第一批改动（PathB 重构）| 第二批改动（5 项修复）|
|------|------|------|
| `services/agent/file_scanners.py` | BaseScanner._scan_columns/_scan_suspicious_rows 加 `mask("", NaN)`；`col_letter` 改为 file_meta alias | — |
| `services/agent/file_scanners_paths.py` | PathBScanner 完全重写为 calamine + chunk 累加器；新增 `_PathBChunkAccumulator` | — |
| `services/agent/data_query_cache.py` | — | 新增 `_CACHE_SCHEMA_VERSION` 常量；`_convert_all_sheets_to_parquet` 加 decision/strategy 参数；`_convert_excel_to_parquet` 大文件分支整段重写为 calamine + chunk |
| `services/agent/excel_cleaner/actions.py` | — | `_remove_empty_rows_cols` 列检测前加 `mask("", NaN)` |
| `services/agent/table_region_detector.py` | — | `convert_multi_region` 加 decision/strategy 参数 + skip 逻辑 |
| `tests/test_file_scanners.py` | `test_file_too_large_raises` 适配新 probe 路径 | + 6 个新测试覆盖 5 项修复 |

---

## V1.0 → V1.1 修正记录

通过对照代码事实的全面审查，发现以下修正点：

| 项 | V1.0 描述 | V1.1 修正 |
|---|---|---|
| Bug-5 | `convert_multi_region` off-by-one | **删除**：实际代码正确（A→col_idx=1 恰好命中 columns[1]，_region 在 columns[0]）。真实问题是路径 A/B 与路径 C 的 col_idx 计算**风格不一致**（A/B 有 `-=1`，C 没有），Phase 1 统一风格 |
| Bug-7 | `_dedup_issues` 跨块吞行号 | **删除**：实际只处理 chunked 路径 B 的 `clean_excel` 结构性 issue（如"全空列保留"），跨 chunk 重复是合理去重 |
| file_processor.py | "L2 沙盒修复体系废弃" | **明确为 dead code**：grep 全代码库无外部调用方，整个文件直接删除。Phase 6 简化 -1 天 |
| 路径 D 触发 | `sheet == "*"` 参数 | **改造**：file_analyze 工具不暴露 sheet 参数。路径 D 触发条件改为"代码 probe 文件 sheet 数 ≥ 2 时自动走"，由 AI 一次裁决决定 sheet 角色（data/meta/skip）和合并 group |
| _file_analyze 副作用 | 未提及 | **新增**：cache.register / set_parquet / set_analyzed 必须保留（含 AI 失败时） |
| _file_dispatch 兜底冲突 | 未提及 | **新增**：必须在 `_file_analyze` 内部捕获 FileAnalyzeError，不能冒泡到 `_file_dispatch` 顶层 `except Exception`（会吞结构化 metadata） |
| 空文件检测 | 未映射到结构化错误 | **新增**：`ensure_parquet_cache` 的 ValueError → `error_category="file_corrupted"` |

修正后：
- Bug 列表 9 → **7**（Bug-1/2/3/4/6/8/9）
- 工作量 14 → **13 天**（file_processor 删除 -1 天）
- 总交付 18 → **17 天**

---

## 0. 改造背景与触发点

### 0.1 触发本次重构的直接 bug

用户上传 1,171 行发票订单 Excel（`104960729691_fd1952.xlsx`），`file_analyze` 输出里给出错误提示：

```
- Row 2 多列缺失（D, E, F, G, H...），大概率是 _is_summary 汇总行，
  查询时加 `WHERE _is_summary = false` 排除
- Row 904 多列缺失（J, M, N, O），大概率是 _is_summary 汇总行，
  查询时加 `WHERE _is_summary = false` 排除
```

事实：
- 该文件根本**没有汇总行**（AI prescan 也正确判断 `special_rows.summary = []`）
- Parquet 里**根本没有 `_is_summary` 列**（`_mark_summary_rows` 因 summary 列表空而 return）
- LLM 按 hint 执行 `WHERE _is_summary = false` 触发 `BinderException: column not found`

### 0.2 根因（3 个断裂点）

| 断裂 | 位置 | 性质 |
|---|---|---|
| ① | `_scan_issues` 列方向产出 ↔ `_compress_issues` 行方向合并 | 语义错位 |
| ② | `_compress_issues` 不读 prescan 结论，自行启发式造谣 | 越权决策 |
| ③ | prescan 只看 50 行采样，中段盲区无回流通道 | 视野不对称 |

详见 §4.1。

### 0.3 用户核心思维（本次重构的设计哲学）

> "代码扫描时发现问题，把问题这些点筛选出来让 AI 判断然后做提示。然后清洗整个表格给出完整的数据表"
>
> "每个文件只过一次 AI。代码确定的部分直接做出总结，代码发现的异常抓取数据存储好，最后给到 AI 做整体判断和总结放到 schema"
>
> "拿着这些具体的情况进入 AI 数据清洗，清洗结束。拿到干净的表格地址，加到 XML 里面去 给到主 Agent 一个详细的文档了解方案，还有一个干净的表格地址"
>
> "用我们的提示词格式做一次最终的总结这样出来的摘要是可控的"

翻译成架构：

- **每文件一次 AI 裁决**（不再开头盲采样）
- **代码扫描在前**（按 4 条路径分别筛证据）
- **AI 决策包含清洗策略**（合并语义/ID 列/空行政策/混合类型）
- **代码按策略执行清洗**（保留 deterministic 安全性）
- **输出结构化 XML**（包含干净表格地址 + AI 总结 + 数据全貌 + 跨文件关联）

### 0.4 改造目标（按用户三句话浓缩）

1. 主 Agent 拿到 XML 就能干活——零探查
2. 摘要格式可控——固定 prompt 模板 + 强 JSON schema
3. 文档自洽——XML 包含数据访问地址、AI 总结、清洗结果、查询规则、样本

---

## 1. 现有架构完整图谱

### 1.1 工具入口（实际只有 1 处运行入口）

| 调用方 | 函数 | 行号 | 运行状态 |
|---|---|---|---|
| **主 Agent 工具调用** | `FileToolMixin._file_analyze` | `file_tool_mixin.py:219-336` | ✅ **唯一真实入口** |
| ~~用户上传后自动分析~~ | `file_processor.process_file` | `file_processor.py:91-165` | ❌ **dead code**（grep 无外部调用方） |
| ~~L2 修复后复查~~ | `file_processor.check_l2_result` | `file_processor.py:168-188` | ❌ **dead code** |

**重要事实**：`file_analyze` 工具描述（`config/file_tools.py:96-118`）只暴露 `path` 参数，不暴露 `sheet`，所以 `_file_analyze` 调 `ensure_parquet_cache(abs_path, None, ...)` 硬编码 sheet=None。

### 1.1.1 `_file_analyze` 必须保留的副作用

新方案改造 `_file_analyze` 时必须保留以下副作用（即使 AI 失败也要保留原文件注册）：

```python
# 文件名 → workspace 路径注册
cache.register(name, workspace=abs_path)
cache.register(rel_path, workspace=abs_path)

# 仅成功时：文件名 → parquet 路径（后续 get_file usage="code" 用）
cache.set_parquet(name, cache_path)
cache.set_analyzed(name, True)   # 跨轮持久标记
```

### 1.1.2 `_file_dispatch` 顶层兜底约束

`_file_dispatch` 在 `file_tool_mixin.py:72-77` 有顶层 `except Exception` 把所有异常转成简单错误字符串：

```python
except Exception as e:
    return AgentResult(
        summary=f"文件操作失败: {e}",   # ← 会吞结构化 metadata
        ...
    )
```

**新方案约束**：`FileAnalyzeError` 必须在 `_file_analyze` 内部捕获并转为带 metadata 的 AgentResult，**不能让它冒泡**到 `_file_dispatch` 顶层（详见 §5.5）。

### 1.2 编排层（ensure_parquet_cache）

`data_query_cache.py:177-255`

```
1. snapshot 缓存命中检查（mtime + size，1ms 误差）
2. asyncio.Lock 防并发（LRU 上限 100）
3. ★ run_prescan（唯一 AI 调用，仅 sheet != "*" 时走）
   ↓ 产出 PrescanResult
4. 线程池调:
   ├ sheet == "*" → _convert_all_sheets_to_parquet（路径 D）
   └ sheet != "*" → _convert_excel_to_parquet（路径 A/B/C 分流）
5. 文件不存在校验
```

### 1.3 4 条转换路径（V1.2：路径 B 后端改 calamine + chunk）

| 路径 | 触发条件 | V1.2 后端 | 入口函数 |
|---|---|---|---|
| **A 小文件** | `total_rows < 100,000` 且 `header_depth <= 1` | fastexcel 全表（含 `mask("", np.nan)` null_ratio 修复）| `_convert_excel_to_parquet` 小分支 |
| **B 大文件** | `total_rows >= 100,000` 且单元格 ≤ 25M | **python-calamine iter_rows + 100K 行 chunk 累加**（V1.2 改造）；超 25M cells raise `file_too_large` | `_convert_excel_to_parquet` 大分支（chunk 累加器）|
| **C 多区域** | `detect_table_regions ≥ 2`（仅 A/B 中触发） | fastexcel（同 V1.1）| `convert_multi_region` |
| **D 多 sheet** | 代码 probe 文件后 `len(sheet_names) >= 2` 自动走 | fastexcel（同 V1.1）| `_convert_all_sheets_to_parquet` 改造版 |

**V1.1 路径 D 新设计**：
- 主 Agent 调 `file_analyze(path)` 时，工具内部 probe 文件
- 单 sheet → 直接 A/B/C 分流
- 多 sheet → 走 D scanner 扫所有 sheet 元信息 → AI 一次裁决决定每 sheet 角色：
  - `role="data" + merge_group="X"` → 合并同组
  - `role="meta"` / `"aggregated"` / `"skip"` → 跳过
- 主 Agent 看 XML `<sheets>` 节点知道工具做了什么决策（完全透明）

### 1.4 唯一 AI 介入点

`file_prescan.run_prescan` (`file_prescan.py:148-173`)

- 模型：qwen-turbo
- 温度：0.1
- 超时：10s
- max_tokens：1500（仅限输出）
- 采样：head 20 + middle 10 + tail 20 ≈ 50 行
- 输出：`PrescanResult` 含 8 个字段（见 §1.7）

### 1.5 所有代码扫描动作（18 个）

| # | 函数 | 文件:行 | 决策性质 |
|---|---|---|---|
| 1 | `_detect_structure` | `excel_cleaner.py:301-356` | XML 解析（合并/隐藏行列/autofilter） |
| 2 | `detect_header_row` | `data_query_cache.py:327-346` | 表头位置（兜底） |
| 3 | `_classify_cell` | `data_query_cache.py:284-298` | 单元格类型（long_id/date/numeric/text） |
| 4 | `_prescan_schema` | `data_query_cache.py:386-449` | 大文件 schema 三段采样 800 行 |
| 5 | `_infer_segment_type` | `data_query_cache.py:452-484` | 99% 阈值类型推断 |
| 6 | `_unify_column_types` | `data_query_cache.py:487-504` | 跨段最保守类型 |
| 7 | `_flatten_multi_header` | `excel_cleaner.py:359-379` | 多级表头 `_` 连接 |
| 8 | `_apply_merge_fill` | `excel_cleaner.py:382-448` | 合并范围精确填充 |
| 9 | `_deduplicate_columns` | `excel_cleaner.py:631-656` | 重名加 `_1/_2` |
| 10 | `_mark_summary_rows` | `excel_cleaner.py:451-491` | 消费 prescan |
| 11 | `_mark_hidden_rows` | `excel_cleaner.py:494-512` | 标 `_is_hidden`（未挂入主流程） |
| 12 | `_mark_hidden_cols` | `excel_cleaner.py:515-537` | 加 issue |
| 13 | `_remove_empty_rows_cols` | `excel_cleaner.py:540-598` | 删全空行 / 标空列 |
| 14 | `_coerce_object_columns` | `excel_cleaner.py:658-681` | 混合类型→str |
| 15 | `_fix_int_columns` | `excel_cleaner.py:601-628` | float→Int64 |
| 16 | `_apply_column_mapping` | `data_query_cache.py:538-577` | 消费 prescan column_mapping |
| 17 | `_build_schema` | `file_meta.py:176-221` | 列类型/null率/范围 |
| 18 | `_detect_grain` | `file_meta.py:231-321` | 订单级 vs 明细级 |
| 19 | `_build_sample` | `file_meta.py:324-392` | head 4 + mid 2 + tail 4 + boundary |
| 20 | `_dedup_samples_by_signature` | `file_meta.py:395-452` | 跨段签名去重 |
| 21 | `_scan_issues` | `file_meta.py:455-499` | 列方向首个 null + 重复行 |
| 22 | `_compress_issues` | `file_meta.py:885-918` | ⚠️ **旁路造谣 `_is_summary`** |
| 23 | `extract_formulas` | `file_meta.py:544-603` | lxml 流式公式提取上限 200 |
| 24 | `detect_table_regions` | `table_region_detector.py:35-87` | 多区域空行切分 |

### 1.6 所有 issue 类型来源映射

| issue type | 来源函数 | 触发条件 |
|---|---|---|
| `header_flattened` | `_flatten_multi_header` | MultiIndex 列名 |
| `merge_filled` | `_apply_merge_fill` | 实际填充了单元格 |
| `summary_rows_marked` | `_mark_summary_rows` | prescan summary 非空且匹配到 |
| `hidden_cols` | `_mark_hidden_cols` | hidden_cols 非空 |
| `empty_cols` | `_remove_empty_rows_cols` | 存在全空列 |
| `empty_rows_removed` | `_remove_empty_rows_cols` | 删了空行 |
| `int_cols_fixed` | `_fix_int_columns` | float→Int64 |
| `column_deduplicated` | `_deduplicate_columns` | 列名重复 |
| `mixed_type_coerced` | `_coerce_object_columns` | infer_dtype 是 mixed |
| `column_renamed` | `_convert_excel_to_parquet`/`convert_multi_region` | AI mapping 非空 |
| `formula_skipped` | `generate_file_meta` | 公式提取失败 |
| `merged_cells` | `generate_file_meta` | merged_ranges 非空 |
| `anomaly_*` | `_convert_excel_to_parquet` | prescan.anomalies 非空 |
| `missing_value` | `_scan_issues` | 列 null > 0 |
| `duplicate_row` | `_scan_issues` | df.duplicated > 0 |

### 1.7 数据结构（v1）

#### `PrescanResult` (`file_prescan.py:23-35`)

| 字段 | 类型 | 来源 |
|---|---|---|
| `header_type` | str | AI |
| `header_rows` | list[int] | AI |
| `data_start_row` | int | AI |
| `column_mapping` | dict[str, str] | AI（列字母→业务名） |
| `special_rows` | dict[str, list[int]] | AI（summary/unit/note） |
| `regions` | list[dict] | AI |
| `anomalies` | list[dict] | AI |
| `confidence` | str | AI（high/medium/low） |
| `reasoning` | str | AI |
| `raw_response` | str | 调试用 |

#### `CleaningReport` (`excel_cleaner.py:53-72`)

| 字段 | 类型 | 说明 |
|---|---|---|
| `merged_cols_filled` | int | |
| `summary_rows_marked` | int | |
| `hidden_rows_marked` | int | |
| `hidden_cols_names` | list[str] | |
| `empty_cols_removed` | int | 始终 0（只标注） |
| `empty_rows_removed` | int | |
| `int_cols_fixed` | int | |
| `has_auto_filter` | bool | |
| `warnings` | list[str] | 旧字段保留 |
| `issues` | list[dict] | 主要结构化输出 |
| `original_shape` | tuple | |
| `final_shape` | tuple | |
| `header_row` | int | 行号映射 |
| `data_start_row` | int | |
| `row_offset` | int | |

#### `FileMeta` (`file_meta.py:43-67`)

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | str | "1.0" |
| `status` | str | pass/warning/fail |
| `source_file` | str | |
| `processed_at` | str | ISO datetime |
| `last_accessed_at` | str | |
| `processed_by` | str | L1/L2 |
| `summary` | dict | row_count/col_count/sheet_count/description |
| `schema` | dict[str, dict] | _build_schema 产出 |
| `sample` | dict[str, list[dict]] | head/middle/tail/boundary |
| `stats` | dict[str, int] | missing_values/duplicates |
| `formulas` | list[dict] | extract_formulas 产出 |
| `issues` | list[dict] | 多来源合并 |
| `merged_cells` | list[dict] | XML 区域信息 |
| `raw_preserved` | bool | |
| `grain` | dict | _detect_grain 产出 |
| `prescan` | dict | asdict(PrescanResult) |
| `cleaning` | dict | asdict(CleaningReport) |
| `confidence` | float | 1.0/0.8/0.5 |

### 1.8 缓存策略

| 项 | 说明 |
|---|---|
| 缓存路径 | `staging/_cache_{path_md5_8}_{safe_sheet}_{stem}.parquet` |
| 缓存元数据 | `_cache_xxx.snapshot` 存 `mtime,size` |
| 缓存命中判断 | `_snapshot_matches`：mtime 误差 < 0.001 且 size 完全相等 |
| 并发控制 | `_convert_locks: dict[str, asyncio.Lock]`，LRU 上限 100 |
| 锁 key | `{excel_path}:{sheet_label}` |
| 写入流程 | tmp 文件 → `os.rename` 原子切换 |

### 1.9 file_processor 三层降级（V1.1：已确认 dead code，直接删除）

`file_processor.py:91-271` 整个文件 271 行

```
L1 = ensure_parquet_cache（含 prescan 调用）
  ↓
  meta.status == "fail" OR (meta.status == "warning" AND confidence < 0.7)
  ↓
L2 = 构造 L2FixRequest 返回给 tool_loop_executor
       上层触发沙盒 code_execute 让 AI 写代码修复
       重试 3 次
  ↓
L3 = build_l3_message 告知用户
```

**V1.1 重新评估**：

通过 grep 全代码库验证：

```bash
grep -rn "process_file|FileProcessResult|check_l2_result|build_l3_message" backend
# 结果：除 file_processor.py 自身和 test_file_processor.py 外，无任何业务代码引用
```

**结论**：`file_processor.py` 是 **dead code** —— 设计了 L1→L2→L3 但**从未接入主流程**。

**本次动作**：
- Phase 6 直接删除整个 `file_processor.py`（271 行）
- 删除 `tests/test_file_processor.py`（157 行）
- 删除 skill `backend/skills/file-fix.md`（如果存在且仅 file_processor 引用）
- 工作量 -1 天（不再需要"改造 process_file"）

### 1.10 session_files 跨文件关联（本次内部不动，输出整合）

`session_files.py:32-71`

```
每次新文件入库 → update_session_files:
  ├ 写入文件清单
  └ _detect_relations: 与历史文件做关联分析
       L1: 列名集合交集
       L2: 同名列值采样重叠（≥ 5%）→ JOIN
       L3: 同名列模式分布相似（cosine ≥ 0.7）→ UNION
```

输出 `session_files.json`，**当前不在 file_view**。

新架构整合：渲染 XML 时读这个 json，输出 `<related_files>` 顶级节点。

---

## 2. 通读发现的问题清单

### 2.1 核心 bug（本次重构必须解决）

#### Bug-1：`_compress_issues` 旁路造谣

`file_meta.py:907-912`

```python
if len(group) >= 3:
    out.append(
        f"- Row {row} 多列缺失（...），"
        f"大概率是 _is_summary 汇总行，查询时加 `WHERE _is_summary = false` 排除"
    )
```

**问题**：
- 不读 `meta.prescan` 任何字段
- 不读 `meta.cleaning.summary_rows_marked`
- 不检查 df 列里是否真有 `_is_summary` 列
- 凭"行号在 ≥3 个 missing_value issue 中出现"启发式造谣

**修复**：完全删除这条造谣，改为按事实输出（详见 §6.5 渲染层）

#### Bug-2：`_scan_issues` 列方向产出 ↔ `_compress_issues` 行方向合并语义错位

`file_meta.py:455-499` + `file_meta.py:895-901`

```python
# _scan_issues：列方向
for col in df.columns:
    first_null_idx = col_data.isnull().idxmax()
    issues.append({"location": {"row": first_null_idx, "col": ...}})

# _compress_issues：行方向合并
by_row[row].append(issue)
if len(group) >= 3: 启发式断言"多列缺失"
```

**问题**：
- "D 列首个 null 在 Row 2" ≠ "Row 2 多列缺失"
- 列方向的偶然汇集被误读为行方向的多列缺失
- 即使真的"行多列缺失"，也未必是汇总行（可能是个人订单天然缺单位字段）

**修复**：
- `_scan_issues` 改为**双方向输出**：
  - 列级（保留现状）：每列 missing 总数 → 列汇总
  - 行级（新增）：扫描"整行 ≥ N 列缺失"的可疑行号 → 进 evidence_pool 给 AI
- `_compress_issues` 删除（被 AI 决策替代）

#### Bug-3：prescan 视野盲区（路径 B 大文件）

`file_prescan.py:18-20` 三段采样固定 50 行；大文件中段 99% 行 AI 看不到。

**修复**：
- prescan AI 调用整体废弃，改为**代码扫描完整 evidence pool → AI 一次裁决**
- 大文件路径 B 扫描器全表分桶（10 万行 1 桶取 2 代表 + 关键词行 + 类型突变行）

### 2.2 隐藏 bug（顺手修）

#### Bug-4：`_fix_int_columns` 路径 A 长 ID 保护缺失

`excel_cleaner.py:601-628`

```python
if (non_null == non_null.astype("int64")).all():
    df[col] = df[col].astype("Int64")  # ← 19 位订单号被转 Int64 精度丢失
```

**事实**：
- 路径 B 有 `_infer_segment_type` 的 > 15 位 → string 兜底（`data_query_cache.py:461`）
- 路径 A `_fix_int_columns` **没有任何长度判断**
- 销售明细的 19 位 `平台订单号` 在路径 A 下会被转 Int64

**修复**：`_fix_int_columns` 加 `non_null.astype(str).str.len().max() > 15 → 跳过` 兜底。

#### ~~Bug-5：`convert_multi_region` column_mapping off-by-one 嫌疑~~ ❌ V1.1 删除（误判）

**V1.1 修正**：审查时验证代码事实，发现 `table_region_detector.py:244-258` 实际**正确**：
- col_letter A → col_idx = 1（计算后不减 1）
- `merged.columns[0] = "_region"`，`columns[1]` 恰好对应 A 列
- 数学上正确

**真实问题不是 bug，是代码风格不一致**：
- 路径 A/B 的 `data_query_cache.py:_apply_column_mapping` (line 550) 有 `col_idx -= 1`
- 路径 C 的 `table_region_detector.py:convert_multi_region` (line 247-251) 没有 `-= 1`

两种风格数学等价，但维护时易踩坑。**Phase 1 顺手统一**：路径 C 也改为先 `col_idx -= 1`，然后 `parquet_idx = col_idx + 1`（_region 偏移显式表达）。

#### Bug-6：`_dedup_samples_by_signature` 签名过粗

`file_meta.py:418-431`

```python
def _sig(row):
    for col, t in col_types.items():
        if t == "number":
            sig.append("+" if float(val) > 0 else "0")  # 只看正负零
        else:
            sig.append(hash(str(val)[:8]))  # 只看前 8 字符
```

**问题**：50 万行销售订单里，订单号前 8 字符相同（如 "50068273..." vs "50068274..."）+ 数值正负相同 → 签名相同 → 被误去重

**修复**：
- 数值列：保留前 4 位有效数字
- 字符串列：取前 16 字符 hash + 长度

#### ~~Bug-7：`_dedup_issues` 跨块吞行号~~ ❌ V1.1 删除（误判）

**V1.1 修正**：审查时验证 `_scan_issues` 不参与 chunk 处理（仅在 `generate_file_meta` 末尾对完整 df 调一次）。`_dedup_issues` 在 chunked 路径 B 只处理 `clean_excel` 的结构性 issue（如 `empty_cols`、`int_cols_fixed`、`merge_filled`），这些跨 chunk 重复是合理去重（同一列在多 chunk 都识别"全空"应该合并为一条）。

**结论**：`_dedup_issues` 设计正确，无 bug。

#### Bug-8：多级表头 + 大文件 OOM 风险

`data_query_cache.py:678`

```python
if total_rows < _CHUNK_THRESHOLD or header_depth > 1:
    # 走小文件全量路径
```

**事实**：500 万行 + 2 级表头 → `pd.read_excel(header=[0,1])` 全量加载 → OOM

**修复**：
- `header_depth > 1` 且 `total_rows ≥ 100k` 时，**降级为单级表头读取**（用 prescan/AI 给出的展平列名）
- 加 warning 提示降级

#### Bug-9：`_MAX_XML_SIZE = 500MB` 无 fallback

`excel_cleaner.py:14`

```python
_MAX_XML_SIZE = 500 * 1024 * 1024
# 超过 → return None → 隐藏行列/合并单元格/autofilter 全丢
```

**修复**：
- 超过时 fallback：用 openpyxl read_only 流式读 mergedCells（避免一次性加载完整 XML）
- 如仍失败，记 warning 到 cleaning_report.issues

### 2.3 通读补充的关键事实

| # | 事实 | 影响新方案 |
|---|---|---|
| 1 | 路径 D（多 sheet）`ensure_parquet_cache:217` 跳过 prescan | 新方案路径 D 必须独立扫描器 |
| 2 | `_apply_column_mapping` 重命名后会自动加 `_1`/`_2` 后缀 | XML 必须告知主 Agent 哪些列被加了后缀 |
| 3 | `_prescan_schema`（路径 B）与 prescan AI 完全独立的二次采样系统 | 新方案合并为统一 evidence pool |
| 4 | `_build_sample` 的 boundary 段依赖 prescan.anomalies，路径 C/D 无此字段 | 新方案 boundary 来源统一 |
| 5 | 测试覆盖 2,432 行（7 个 test 文件，60+ 测试类） | 新方案回归必须全过 |
| 6 | `_mark_hidden_rows` 没被 `clean_excel` 主流程调用（孤儿函数） | 新方案视为 dead code 移除 |
| 7 | `fuzzy_match_sheet` 模糊匹配（精确→归一化→包含 ≥4 字符） | 保留 |
| 8 | `scan_sheet_structures` 多 sheet 采样上限 200，只扫前 10 + 最后 1 | 路径 D 扫描器复用 |
| 9 | `detect_header_row` 用 `_classify_cell` 值内容分类 | 保留作为代码扫描兜底 |
| 10 | `confidence` 字段（FileMeta.confidence: float） | 新架构废弃 |

---

## 3. 新架构总览

### 3.1 流程图

```
┌────────────────────────────────────────────────────────────────────┐
│ STEP 0: 入口（V1.1：只有一个真实入口）                                  │
│   FileToolMixin._file_analyze（唯一）                                  │
│   → 路径解析 + 扩展名校验 + 缓存命中检查                                │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
┌────────────────────────────────────────────────────────────────────┐
│ STEP 1: 路径分流（V1.1：纯代码 probe 自动判断）                         │
│   probe 文件后:                                                       │
│   • len(sheet_names) >= 2                → 路径 D（多 sheet）         │
│   • detect_table_regions ≥ 2            → 路径 C（多区域）           │
│   • total_rows >= 100k                   → 路径 B（大文件分块）       │
│   • total_rows < 100k & header_depth ≤ 1 → 路径 A（小文件）          │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
┌────────────────────────────────────────────────────────────────────┐
│ STEP 2: 4 条路径独立 evidence 扫描器（纯代码）                          │
│   PathAScanner / PathBScanner / PathCScanner / PathDScanner         │
│   产出统一格式的 EvidencePool                                          │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
┌────────────────────────────────────────────────────────────────────┐
│ STEP 3: AI 一次裁决（三层失败链）                                       │
│   qwen-turbo (尝试 1) → 失败                                          │
│   qwen-turbo (尝试 2) → 失败                                          │
│   qwen-plus  (尝试 1) → 失败                                          │
│   ↓                                                                  │
│   全部成功 → AIDecision + CleaningStrategy                            │
│   全部失败 → raise FileAnalyzeError（主 Agent 处理）                   │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
┌────────────────────────────────────────────────────────────────────┐
│ STEP 4: 代码按 CleaningStrategy 执行清洗                              │
│   clean_excel(df, strategy)                                          │
│   ├ 合并填充策略     ← strategy.merged_cell_actions                  │
│   ├ 空行处理策略     ← strategy.empty_row_policy                     │
│   ├ 混合类型处理策略  ← strategy.mixed_type_handling                  │
│   ├ ID 列保护       ← strategy.id_columns                            │
│   ├ 汇总行标记      ← strategy.summary_rows                          │
│   └ 列重命名        ← strategy.column_mapping                         │
│   AI 没说的 → 用硬规则兜底（向后兼容）                                    │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
┌────────────────────────────────────────────────────────────────────┐
│ STEP 5: 写干净 Parquet → 拿到 cache_path                              │
│   .parquet + .meta.json (FileMeta v2) + .snapshot                   │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
┌────────────────────────────────────────────────────────────────────┐
│ STEP 6: 拼装 XML（含 related_files 关联）                              │
│   读 session_files.json → 拼 <related_files>                         │
│   渲染 FileMeta v2 → XML                                              │
└──────────────────────────┬─────────────────────────────────────────┘
                           ↓
              主 Agent 拿到完整 XML（零探查可执行）
```

### 3.2 关键变化

| 维度 | 旧 | 新 |
|---|---|---|
| AI 调用时机 | 流程开头（看 50 行盲采样） | 流程中部（看代码扫描完整 evidence） |
| AI 调用次数 | 1 次（成功即止） | 最多 3 次（失败链）|
| AI 失败后果 | 原本设计 L1→L2→L3（但 L2/L3 是 dead code，从未运行） | 直接 raise 结构化 FileAnalyzeError |
| 路径 D 触发 | sheet="*"（但工具不暴露该参数，实际无入口） | 代码 probe `len(sheet_names) >= 2` 自动走 |
| 清洗策略 | 全部硬编码 | 5 个动作 AI 决策 + 硬规则兜底 |
| 输出格式 | markdown（含旁路造谣） | 结构化 XML（强约束） |
| Schema 完整度 | 散落在 markdown 段落 | 顶级 typed entries |
| 跨文件关联 | 独立 json | 整合进 XML |
| 状态标识 | pass/warning/fail + confidence | success/raise（二元） |

### 3.3 废弃模块清单（V1.1：file_processor 整体删除）

| 模块 | 废弃理由 |
|---|---|
| `file_prescan.py`（整个文件） | AI 调用挪到末尾，prescan 整体废弃，被 file_scanners + file_ai_judge 替代 |
| `file_processor.py`（整个文件） | **V1.1 确认 dead code**，grep 无外部调用方，整体删除 |
| `backend/skills/file-fix.md` | 仅 file_processor 引用，随之删除 |
| `FileMeta.confidence` | 新架构二元状态（success/raise） |
| `FileMeta.prescan` | 被 ai_decision 替代 |
| `FileMeta.processed_by` | L1/L2 概念废弃 |
| `FileMeta.status` 三值 | 改为二值（success/raise） |
| `file_meta._compress_issues` | 旁路造谣删除（Bug-1 修复） |
| `file_meta.format_file_view` markdown 版本 | 被 XML renderer 替代 |
| `excel_cleaner._mark_hidden_rows` | 未挂入主流程的孤儿函数 |
| `data_query_cache._prescan_schema` | 与新 evidence pool 合并 |
| `data_query_cache._infer_segment_type` | 与新 evidence pool 合并 |
| `data_query_cache._unify_column_types` | 与新 evidence pool 合并 |

---

## 4. 数据结构定义

### 4.1 `EvidencePool`（新增）

新文件：`backend/services/agent/file_evidence.py`

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class CellSample:
    """单元格采样（含坐标）。"""
    row: int           # Excel 1-indexed 行号
    col: str           # Excel 列字母 (A/B/.../AA)
    raw_value: Any     # 原始值（未清洗）
    classified: str    # _classify_cell 结果（long_id/date/numeric/text/empty）


@dataclass
class SuspiciousRow:
    """代码扫描出的可疑行（待 AI 裁决）。"""
    row: int                       # Excel 行号
    reason: str                    # 触发可疑的原因（"keyword_match"/"multi_null"/"type_outlier"）
    keywords: list[str] = field(default_factory=list)
    null_ratio: float = 0.0        # 该行 null 比例
    raw_values: list[Any] = field(default_factory=list)
    surrounding: dict[str, Any] = field(default_factory=dict)  # 上下文（前一行/后一行片段）


@dataclass
class ColumnEvidence:
    """列级证据。"""
    col_letter: str
    raw_header: str                # 原始表头单元格
    sample_values: list[Any]       # 头 5 + 中 3 + 尾 5 共 13 个值
    classified_dist: dict[str, int]  # {long_id: 9, numeric: 3, empty: 1}
    null_ratio: float
    is_long_id_candidate: bool     # 长度 > 10 且全数字的占比 > 70%
    has_unit_suffix_candidates: bool  # 形如 "1.5kg" 的值
    has_currency_prefix: bool      # ¥/$ 前缀


@dataclass
class RegionEvidence:
    """单 sheet 多区域候选（路径 C）。"""
    region_id: int
    range_str: str                 # "A1:H100"
    header_row: int                # 0-indexed
    header_cells: list[str]
    head_sample: list[list[Any]]
    tail_sample: list[list[Any]]
    row_count: int
    suspected_type: str            # "primary"/"summary"/"meta"/"unknown"


@dataclass
class SheetEvidence:
    """单 sheet 元信息（路径 D 多 sheet 用）。"""
    name: str
    rows: int
    cols: int
    header_candidates: list[list[Any]]  # 前 3 行作为表头候选
    head_sample: list[list[Any]]
    tail_sample: list[list[Any]]
    column_names: list[str]            # detect_header_row 检出的列名


@dataclass
class FormulaEvidence:
    """公式证据。"""
    cell: str
    expression: str
    value: Any
    col_name: str


@dataclass
class EvidencePool:
    """代码扫描完整产出，作为 AI 一次裁决的输入。"""

    # 文件元信息
    file_path: str
    file_name: str
    file_size_bytes: int
    total_rows: int
    total_cols: int
    sheet_names: list[str]
    target_sheet: str
    path_type: str                 # "A" | "B" | "C" | "D"

    # 表头候选（所有路径都有）
    header_candidates: list[list[Any]]  # 前 5 行原始单元格
    detected_header_row_code: int       # 代码 detect_header_row 兜底结果

    # 结构元信息
    merged_ranges: list[tuple[int, int, int, int]] = field(default_factory=list)
    hidden_rows: list[int] = field(default_factory=list)
    hidden_cols: list[int] = field(default_factory=list)
    has_auto_filter: bool = False

    # 列证据（所有路径都有）
    columns: list[ColumnEvidence] = field(default_factory=list)

    # 关键样本（头/中/尾 + 桶代表，路径 A 13 行，路径 B 30+ 行）
    key_samples: list[dict[str, Any]] = field(default_factory=list)

    # 可疑行（待 AI 裁决，所有路径都有）
    suspicious_rows: list[SuspiciousRow] = field(default_factory=list)

    # 路径 C 独有
    regions: list[RegionEvidence] = field(default_factory=list)

    # 路径 D 独有
    sheets: list[SheetEvidence] = field(default_factory=list)

    # 公式（所有路径都有，仅 xlsx）
    formulas: list[FormulaEvidence] = field(default_factory=list)
    formula_total_count: int = 0

    # 代码已确定的清洗事实（不需要 AI 决策的部分）
    confirmed_facts: list[dict[str, Any]] = field(default_factory=list)
```

### 4.2 `AIDecision`（新增）

新文件：`backend/services/agent/file_ai_decision.py`

```python
@dataclass
class ColumnSemantic:
    letter: str
    business_name: str
    semantic_type: str             # "id"/"name"/"datetime"/"amount"/"quantity"/"address"/"note"/"category"/"other"
    is_order_level: bool = False   # AI 判断的订单级标签
    is_id_column: bool = False     # ID 类列（不该转 Int）
    notes: str = ""


@dataclass
class MergedCellAction:
    range_str: str                 # "A2:H2"
    action: str                    # "treat_as_header" | "fill_down" | "preserve_as_group" | "skip"
    reason: str = ""


@dataclass
class MixedTypeAction:
    col_letter: str
    action: str                    # "force_str" | "extract_unit_number" | "extract_currency_amount" | "to_datetime"
    unit: str = ""                 # 当 action=extract_unit_number 时填
    reason: str = ""


@dataclass
class EmptyRowDecision:
    row: int                       # Excel 行号
    preserve: bool
    reason: str


@dataclass
class RegionDecision:
    region_id: int
    range_str: str
    role: str                      # "primary" | "secondary" | "metadata" | "skip"
    relation_to_primary: str = ""
    skip_reason: str = ""


@dataclass
class SheetDecision:
    name: str
    role: str                      # "data" | "meta" | "aggregated" | "skip"
    merge_group: str = ""          # "monthly_data" / "skip" / etc
    skip_reason: str = ""


@dataclass
class DataQualityNote:
    severity: str                  # "info" | "warning" | "error"
    note: str
    affected_rows: list[int] = field(default_factory=list)
    affected_cols: list[str] = field(default_factory=list)


@dataclass
class AIDecision:
    """AI 一次裁决的完整输出（强 JSON schema 约束）。"""

    # 基础结构
    header_row: int                # Excel 1-indexed
    data_start_row: int
    header_type: str               # "single" | "multi_level"
    header_note: str = ""          # 如 "Row 1 是大标题行，Row 2 才是表头"

    # 列业务语义
    column_semantics: list[ColumnSemantic] = field(default_factory=list)

    # 汇总/特殊行
    summary_rows: list[int] = field(default_factory=list)  # 空 = 确认无汇总行
    unit_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)

    # 多区域决策（路径 C）
    regions: list[RegionDecision] = field(default_factory=list)

    # 多 sheet 决策（路径 D）
    sheets: list[SheetDecision] = field(default_factory=list)

    # 数据质量
    data_quality_notes: list[DataQualityNote] = field(default_factory=list)

    # 整体总结
    overall_summary: str = ""      # 一段 100-300 字总结

    # 内部元信息（不在 prompt 约束中）
    model_used: str = ""           # "qwen-turbo" | "qwen-plus"
    attempt_count: int = 1         # 第几次尝试成功
    elapsed_ms: int = 0
```

### 4.3 `CleaningStrategy`（新增）

```python
@dataclass
class CleaningStrategy:
    """AI 决策驱动的清洗策略。代码按此执行。"""

    # 合并单元格语义（每个 range 单独决策）
    merged_cell_actions: list[MergedCellAction] = field(default_factory=list)

    # 空行处理（默认 strict，AI 可指定保留特定行）
    empty_row_policy: str = "strict_all_empty"  # "strict_all_empty" | "preserve_section_separators"
    preserve_empty_rows: list[EmptyRowDecision] = field(default_factory=list)

    # 混合类型列
    mixed_type_handling: list[MixedTypeAction] = field(default_factory=list)

    # ID 列（不转 Int64，保 string）
    id_columns: list[str] = field(default_factory=list)  # 业务列名

    # 汇总行（从 AIDecision.summary_rows 派生）
    summary_rows: list[int] = field(default_factory=list)

    # 列重命名（letter → business_name）
    column_mapping: dict[str, str] = field(default_factory=dict)

    # 跳过列建议（全空且无业务意义）
    skip_columns: list[str] = field(default_factory=list)

    @classmethod
    def from_decision(cls, decision: AIDecision) -> "CleaningStrategy":
        """从 AIDecision 派生 CleaningStrategy。"""
        id_cols = [c.business_name for c in decision.column_semantics if c.is_id_column]
        mapping = {c.letter: c.business_name for c in decision.column_semantics}
        return cls(
            merged_cell_actions=[],   # 由 AIDecision.cleaning_decisions 填，本字段后续扩展
            empty_row_policy="strict_all_empty",
            preserve_empty_rows=[],
            mixed_type_handling=[],
            id_columns=id_cols,
            summary_rows=decision.summary_rows,
            column_mapping=mapping,
            skip_columns=[],
        )
```

注：MergedCellAction / MixedTypeAction 等清洗决策**也归到 AIDecision 中**（避免拆两层），具体见 §5 的 prompt 设计。

### 4.4 `FileMeta v2`（升级）

| 字段 | v1 | v2 |
|---|---|---|
| `version` | "1.0" | **"2.0"** |
| `status` | pass/warning/fail | **success/raise**（仅 success 才写入 .meta.json） |
| `source_file` | ✓ | ✓ |
| `processed_at` | ✓ | ✓ |
| `last_accessed_at` | ✓ | ✓ |
| `processed_by` | L1/L2 | **删除** |
| `confidence` | 1.0/0.8/0.5 | **删除** |
| `summary` | ✓ | ✓（不变） |
| `schema` | _build_schema 产出 | ✓（保留，结构不变） |
| `sample` | head/middle/tail/boundary | ✓（boundary 来源改为 evidence_pool） |
| `stats` | ✓ | ✓ |
| `formulas` | ✓ | ✓ |
| `issues` | 多来源混合 | **只含 cleaning 确定事实**（删除 _scan_issues 产出的 missing_value 误导项；missing 统计移至 schema 列字段 null_ratio） |
| `merged_cells` | ✓ | ✓ |
| `raw_preserved` | ✓ | ✓ |
| `grain` | _detect_grain | ✓（不变） |
| `prescan` | asdict(PrescanResult) | **删除** |
| `cleaning` | asdict(CleaningReport) | ✓（CleaningReport.issues 不再含 missing_value） |
| **`ai_decision`** | — | **新增** asdict(AIDecision) |
| **`cleaning_strategy`** | — | **新增** asdict(CleaningStrategy) |
| **`evidence_summary`** | — | **新增** 关键证据摘要（不存完整 pool 节省空间） |
| **`related_files`** | — | **新增** 从 session_files.json 派生 |
| **`xml_view`** | — | **新增** 持久化的 XML 字符串（缓存命中时直接返回） |

### 4.5 字段映射表（旧 issue type → 新位置）

| 旧 issue type | 新位置 |
|---|---|
| `header_flattened` | `cleaning.issues` 保留 |
| `merge_filled` | `cleaning.issues` 保留 |
| `summary_rows_marked` | `cleaning.issues` 保留，**来源改为 AI 决策** |
| `hidden_cols` | `cleaning.issues` 保留 |
| `empty_cols` | `cleaning.issues` 保留 |
| `empty_rows_removed` | `cleaning.issues` 保留 |
| `int_cols_fixed` | `cleaning.issues` 保留 |
| `column_deduplicated` | `cleaning.issues` 保留 |
| `mixed_type_coerced` | `cleaning.issues` 保留，**来源改为 AI 策略** |
| `column_renamed` | `cleaning.issues` 保留，**来源改为 AI 决策** |
| `formula_skipped` | `cleaning.issues` 保留 |
| `merged_cells` | `cleaning.issues` 保留 |
| `anomaly_*` | **删除**，并入 `ai_decision.data_quality_notes` |
| `missing_value` | **从 issues 删除**，由 `schema[col].null_ratio` 替代 |
| `duplicate_row` | **从 issues 删除**，由 `stats.duplicates` 替代 |

---

## 5. AI 调用层

### 5.1 结构化错误（核心：主 Agent 能精确决策）

**设计原则**：失败不是简单字符串。主 Agent 需要 4 类信息才能做出精确动作：

1. **错误分类**（决定动作类型）
2. **是否可重试 + 建议延迟**（指导自动化处理）
3. **文件上下文**（给用户解释是哪个文件出问题）
4. **可读的中文消息**（直接转述给用户）

新文件：`backend/services/agent/file_ai_judge.py`

```python
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from loguru import logger


@dataclass
class AnalyzeAttemptLog:
    """单次 AI 调用尝试的日志。"""
    attempt_number: int          # 第几次（1/2/3）
    model: str                   # qwen-turbo / qwen-plus
    prompt_variant: str          # default / simplified
    prompt_tokens: int = 0       # 输入 token 数
    elapsed_ms: int = 0          # 耗时
    error_category: str = ""     # 错误分类（见 §5.4）
    error_message: str = ""      # 原始错误消息
    error_traceback: str = ""    # 堆栈（debug 用，主 Agent 不展示）


class FileAnalyzeError(Exception):
    """文件分析失败的结构化异常。

    主 Agent 据此 4 类信息精确决策：
      • error_category    → 知道是哪类问题
      • retryable + action → 知道该不该重试、怎么重试
      • file_context      → 知道是哪个文件、规模多大
      • user_message      → 直接转述给用户的中文（已本地化）

    示例 - 主 Agent 收到后的判断：
      retryable=True + action="retry_immediately" → 直接重新调用 file_analyze
      retryable=True + action="retry_after_delay" → 等 delay_seconds 再调
      retryable=False + action="ask_user"         → 转告用户 user_message，请用户介入
      retryable=False + action="escalate"         → 系统/配置问题，建议联系管理员
    """

    def __init__(
        self,
        error_category: str,
        error_summary: str,
        retryable: bool,
        suggested_action: str,
        retry_delay_seconds: int = 0,
        user_message: str = "",
        file_path: str = "",
        file_name: str = "",
        file_size_mb: float = 0.0,
        total_rows: int = 0,
        path_type: str = "",
        attempts: list[AnalyzeAttemptLog] | None = None,
        debug_details: dict | None = None,
    ):
        super().__init__(error_summary)

        # 错误分类（必填）
        self.error_category = error_category
        self.error_summary = error_summary

        # 主 Agent 决策指引
        self.retryable = retryable
        self.suggested_action = suggested_action  # retry_immediately|retry_after_delay|ask_user|escalate
        self.retry_delay_seconds = retry_delay_seconds
        self.user_message = user_message

        # 文件上下文（让主 Agent 给用户精确解释）
        self.file_path = file_path
        self.file_name = file_name
        self.file_size_mb = file_size_mb
        self.total_rows = total_rows
        self.path_type = path_type

        # 完整尝试日志（debug + 主 Agent 可选展示）
        self.attempts = attempts or []

        # 调试细节（仅运维/开发看）
        self.debug_details = debug_details or {}

    def to_metadata(self) -> dict:
        """转为 AgentResult.metadata，供主 Agent 消费。"""
        return {
            "error_category": self.error_category,
            "retryable": self.retryable,
            "suggested_action": self.suggested_action,
            "retry_delay_seconds": self.retry_delay_seconds,
            "file_context": {
                "name": self.file_name,
                "size_mb": self.file_size_mb,
                "rows": self.total_rows,
                "path_type": self.path_type,
            },
            "attempts_summary": [
                {
                    "n": a.attempt_number,
                    "model": a.model,
                    "elapsed_ms": a.elapsed_ms,
                    "category": a.error_category,
                    "error": a.error_message,
                }
                for a in self.attempts
            ],
        }


_ATTEMPTS = [
    {"model": "qwen-turbo", "timeout": 15, "prompt_variant": "default"},
    {"model": "qwen-turbo", "timeout": 20, "prompt_variant": "simplified"},
    {"model": "qwen-plus",  "timeout": 30, "prompt_variant": "default"},
]


async def adjudicate(evidence: EvidencePool) -> AIDecision:
    """AI 一次裁决（含失败链）。

    Raises:
        FileAnalyzeError: 三次尝试全部失败时（含结构化错误分类 + 主 Agent 决策指引）
    """
    import time
    import traceback
    attempts_log: list[AnalyzeAttemptLog] = []

    for i, cfg in enumerate(_ATTEMPTS, start=1):
        start = time.monotonic()
        attempt_log = AnalyzeAttemptLog(
            attempt_number=i,
            model=cfg["model"],
            prompt_variant=cfg["prompt_variant"],
        )
        try:
            prompt = build_prompt(evidence, variant=cfg["prompt_variant"])
            attempt_log.prompt_tokens = _estimate_tokens(prompt)
            response_json = await _call_llm(
                prompt=prompt,
                model=cfg["model"],
                timeout=cfg["timeout"],
            )
            decision = _parse_and_validate(response_json)
            decision.model_used = cfg["model"]
            decision.attempt_count = i
            decision.elapsed_ms = int((time.monotonic() - start) * 1000)
            return decision
        except Exception as e:
            attempt_log.elapsed_ms = int((time.monotonic() - start) * 1000)
            attempt_log.error_category = _classify_error(e)
            attempt_log.error_message = str(e)[:500]
            attempt_log.error_traceback = traceback.format_exc()[:2000]
            attempts_log.append(attempt_log)
            logger.warning(
                f"AI adjudicate attempt {i}/{len(_ATTEMPTS)} failed "
                f"| model={cfg['model']} | category={attempt_log.error_category} "
                f"| error={type(e).__name__}: {str(e)[:200]}"
            )

    # ── 三次全挂：根据最终错误类别 + 文件特征，构造结构化错误 ──
    final_category = _decide_final_category(attempts_log, evidence)
    template = ERROR_CATEGORIES[final_category]

    user_msg = template["user_template"].format(
        file_name=evidence.file_name,
        size_mb=round(evidence.file_size_bytes / 1024 / 1024, 1),
        rows=evidence.total_rows,
    )

    raise FileAnalyzeError(
        error_category=final_category,
        error_summary=f"文件 {evidence.file_name} AI 分析失败（{final_category}）",
        retryable=template["retryable"],
        suggested_action=template["suggested_action"],
        retry_delay_seconds=template.get("retry_delay_seconds", 0),
        user_message=user_msg,
        file_path=evidence.file_path,
        file_name=evidence.file_name,
        file_size_mb=round(evidence.file_size_bytes / 1024 / 1024, 1),
        total_rows=evidence.total_rows,
        path_type=evidence.path_type,
        attempts=attempts_log,
        debug_details={
            "final_error": attempts_log[-1].error_message,
            "all_categories": [a.error_category for a in attempts_log],
        },
    )


def _estimate_tokens(prompt: str) -> int:
    """中文 1 字符 ≈ 0.5 token，英文 ≈ 1 token 的粗略估算。"""
    chinese = sum(1 for c in prompt if '一' <= c <= '鿿')
    other = len(prompt) - chinese
    return int(chinese * 0.5 + other * 0.3)


def _classify_error(e: Exception) -> str:
    """根据异常类型分类（详见 §5.4 错误分类表）。"""
    from openai import (
        AuthenticationError, RateLimitError, APITimeoutError,
        APIConnectionError, APIError,
    )
    import json as _json

    if isinstance(e, AuthenticationError):
        return "auth_failure"
    if isinstance(e, RateLimitError):
        return "rate_limit"
    if isinstance(e, (APITimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(e, APIConnectionError):
        return "network_failure"
    if isinstance(e, APIError):
        return "api_unavailable"
    if isinstance(e, _json.JSONDecodeError):
        return "llm_output_invalid"
    if isinstance(e, (ValueError, KeyError)):
        # schema 校验失败
        return "llm_output_invalid"
    return "internal_error"


def _decide_final_category(
    attempts: list[AnalyzeAttemptLog],
    evidence: EvidencePool,
) -> str:
    """综合 3 次尝试的错误，判断最终类别。

    决策规则：
      • 全是 auth_failure / internal_error → 同分类（系统问题）
      • 全是 llm_output_invalid → file_too_complex（AI 反复无法理解）
      • 全是 timeout 且文件 > 100K 行 → 倾向 file_too_complex
      • 多种网络问题 → api_unavailable
      • 其他 → 用最后一次的分类
    """
    categories = [a.error_category for a in attempts]

    if all(c == "auth_failure" for c in categories):
        return "auth_failure"
    if all(c == "internal_error" for c in categories):
        return "internal_error"

    # AI 三次都无法理解 → 文件太复杂
    if categories.count("llm_output_invalid") >= 2:
        return "file_too_complex"

    # 全超时 + 大文件 → 可能文件太复杂
    if all(c == "timeout" for c in categories) and evidence.total_rows > 100_000:
        return "file_too_complex"

    if all(c in ("network_failure", "api_unavailable", "timeout") for c in categories):
        return "api_unavailable"

    return categories[-1]


async def _call_llm(prompt: str, model: str, timeout: float) -> dict:
    """调用 DashScope LLM 并强制 JSON 输出。"""
    from openai import AsyncOpenAI
    from core.config import get_settings

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是结构化数据分析专家，必须严格按用户给定的 JSON schema 输出。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    text = resp.choices[0].message.content.strip()
    return json.loads(text)


def _parse_and_validate(data: dict) -> AIDecision:
    """JSON → AIDecision，强 schema 校验。"""
    # 必填字段校验
    required_fields = ["header_row", "data_start_row", "column_semantics", "overall_summary"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"AI 输出缺少必填字段: {missing}")

    # 列语义合法性
    for cs in data.get("column_semantics", []):
        if "letter" not in cs or "business_name" not in cs:
            raise ValueError(f"column_semantics 缺少 letter/business_name: {cs}")
        if cs.get("semantic_type") not in {
            "id", "name", "datetime", "amount", "quantity",
            "address", "note", "category", "other"
        }:
            raise ValueError(f"semantic_type 非法: {cs.get('semantic_type')}")

    # 反序列化
    return AIDecision(
        header_row=int(data["header_row"]),
        data_start_row=int(data["data_start_row"]),
        header_type=data.get("header_type", "single"),
        header_note=data.get("header_note", ""),
        column_semantics=[ColumnSemantic(**cs) for cs in data["column_semantics"]],
        summary_rows=data.get("summary_rows", []),
        unit_rows=data.get("unit_rows", []),
        note_rows=data.get("note_rows", []),
        regions=[RegionDecision(**r) for r in data.get("regions", [])],
        sheets=[SheetDecision(**s) for s in data.get("sheets", [])],
        data_quality_notes=[DataQualityNote(**n) for n in data.get("data_quality_notes", [])],
        overall_summary=data["overall_summary"],
    )
```

### 5.2 prompt 模板

```python
def build_prompt(evidence: EvidencePool, variant: str = "default") -> str:
    """构造 AI 裁决 prompt。

    variant:
        "default"     - 完整 prompt
        "simplified"  - 减少证据细节，仅保留头中尾样本 + 表头候选（用于 retry 时减少 token）
    """
    parts = []
    parts.append("# 任务\n")
    parts.append(
        "你将看到一份 Excel/CSV 文件的代码扫描结果。请基于这些证据做出一次性裁决，"
        "包括表头位置、列业务语义、是否有汇总行、清洗策略等。"
        "你的输出会被代码直接执行，所以必须精确。\n\n"
    )

    parts.append("# 文件信息\n")
    parts.append(f"- 文件名: {evidence.file_name}\n")
    parts.append(f"- 总行数: {evidence.total_rows:,}\n")
    parts.append(f"- 总列数: {evidence.total_cols}\n")
    parts.append(f"- Sheet: {evidence.target_sheet}\n")
    parts.append(f"- 处理路径: {evidence.path_type}\n\n")

    parts.append("# 表头候选（前 5 行）\n")
    for i, row in enumerate(evidence.header_candidates, start=1):
        parts.append(f"Row {i}: {row}\n")
    parts.append(f"\n代码兜底检测表头行: Row {evidence.detected_header_row_code + 1}\n\n")

    parts.append("# 列证据\n")
    for col_ev in evidence.columns:
        parts.append(
            f"列 {col_ev.col_letter}: 原始表头='{col_ev.raw_header}', "
            f"类型分布={col_ev.classified_dist}, null率={col_ev.null_ratio:.2%}\n"
            f"  样本值: {col_ev.sample_values[:8]}\n"
        )
        if col_ev.is_long_id_candidate:
            parts.append(f"  ⚠️ 长度>10 数字占比高，疑似 ID 列\n")
        if col_ev.has_unit_suffix_candidates:
            parts.append(f"  ⚠️ 含单位后缀（如 'kg', 'cm'）\n")
    parts.append("\n")

    if evidence.suspicious_rows:
        parts.append("# 可疑行（请裁决是否汇总/特殊行）\n")
        for sr in evidence.suspicious_rows[:50]:
            parts.append(
                f"Row {sr.row}: reason={sr.reason}, null率={sr.null_ratio:.0%}, "
                f"关键词={sr.keywords}\n"
                f"  原始值: {sr.raw_values[:10]}\n"
            )
        parts.append("\n")

    if evidence.key_samples:
        parts.append("# 关键样本数据\n")
        for sample in evidence.key_samples[:30]:
            parts.append(f"Row {sample['row']}: {sample['cells']}\n")
        parts.append("\n")

    if evidence.path_type == "C" and evidence.regions:
        parts.append("# 候选数据区域（路径 C）\n")
        for r in evidence.regions:
            parts.append(
                f"Region {r.region_id} ({r.range_str}): {r.row_count} 行\n"
                f"  表头: {r.header_cells}\n"
                f"  Head: {r.head_sample[:3]}\n"
            )
        parts.append("\n")

    if evidence.path_type == "D" and evidence.sheets:
        parts.append("# 所有 Sheet 元信息（路径 D）\n")
        for s in evidence.sheets:
            parts.append(
                f"Sheet '{s.name}': {s.rows}行 × {s.cols}列\n"
                f"  列名: {s.column_names}\n"
                f"  Head 2 行: {s.head_sample[:2]}\n"
            )
        parts.append("\n")

    if evidence.formulas:
        parts.append(f"# 公式（共 {evidence.formula_total_count} 个，展示前 10）\n")
        for f in evidence.formulas[:10]:
            parts.append(f"- {f.cell}: {f.expression} = {f.value}\n")
        parts.append("\n")

    parts.append("# 你的输出格式（严格 JSON，不要 markdown）\n")
    parts.append(JSON_SCHEMA_TEMPLATE)

    return "".join(parts)


JSON_SCHEMA_TEMPLATE = """
{
  "header_row": <int, Excel 1-indexed>,
  "data_start_row": <int, Excel 1-indexed>,
  "header_type": "single" | "multi_level",
  "header_note": "<可选，特殊情况说明，如 'Row 1 是标题行'>",

  "column_semantics": [
    {
      "letter": "A",
      "business_name": "<推断的业务列名>",
      "semantic_type": "id" | "name" | "datetime" | "amount" | "quantity" | "address" | "note" | "category" | "other",
      "is_order_level": <bool>,
      "is_id_column": <bool, 是否 ID/订单号类（保护不转 Int）>,
      "notes": "<可选>"
    }
  ],

  "summary_rows": [<Excel 1-indexed 行号>],
  "unit_rows": [],
  "note_rows": [],

  "regions": [
    {
      "region_id": 1,
      "range_str": "A1:H100",
      "role": "primary" | "secondary" | "metadata" | "skip",
      "relation_to_primary": "<可选>",
      "skip_reason": "<可选>"
    }
  ],

  "sheets": [
    {
      "name": "<sheet 名>",
      "role": "data" | "meta" | "aggregated" | "skip",
      "merge_group": "<同组的 sheet 应合并>",
      "skip_reason": "<可选>"
    }
  ],

  "merged_cell_actions": [
    {
      "range_str": "A2:H2",
      "action": "treat_as_header" | "fill_down" | "preserve_as_group" | "skip",
      "reason": "<可选>"
    }
  ],

  "mixed_type_handling": [
    {
      "col_letter": "F",
      "action": "force_str" | "extract_unit_number" | "extract_currency_amount" | "to_datetime",
      "unit": "<当 extract_unit_number 时>",
      "reason": "<可选>"
    }
  ],

  "preserve_empty_rows": [
    {"row": <int>, "reason": "<可选>"}
  ],

  "data_quality_notes": [
    {
      "severity": "info" | "warning" | "error",
      "note": "<给主 Agent 看的提示>",
      "affected_rows": [<int>],
      "affected_cols": ["<列字母>"]
    }
  ],

  "overall_summary": "<100-300 字的整体总结，给主 Agent 快速理解>"
}
""".strip()
```

### 5.3 simplified variant（重试时使用）

第 2 次 retry 时减少 prompt 大小：
- 不传 `key_samples`（只看头/尾各 3 行）
- 不传 `suspicious_rows`（只看 top 10）
- 列证据不传 `sample_values`

减少约 50% token，提高 retry 成功率。

### 5.4 错误分类表（完整结构化）

所有错误分类配置统一定义为 `ERROR_CATEGORIES` 字典。每类错误对应一组主 Agent 决策指引。

```python
ERROR_CATEGORIES = {
    # ════════════════════════════════════════════════════════
    # 网络/服务类 - 立即重试有效
    # ════════════════════════════════════════════════════════
    "network_failure": {
        "retryable": True,
        "suggested_action": "retry_immediately",
        "retry_delay_seconds": 0,
        "user_template": "AI 服务网络不稳定，正在重新分析「{file_name}」",
    },
    "timeout": {
        "retryable": True,
        "suggested_action": "retry_immediately",
        "retry_delay_seconds": 0,
        "user_template": "AI 响应超时（文件 {size_mb}MB / {rows} 行），重新分析中",
    },

    # ════════════════════════════════════════════════════════
    # 限流/服务繁忙 - 延迟重试
    # ════════════════════════════════════════════════════════
    "rate_limit": {
        "retryable": True,
        "suggested_action": "retry_after_delay",
        "retry_delay_seconds": 5,
        "user_template": "AI 服务繁忙，5 秒后自动重试",
    },
    "api_unavailable": {
        "retryable": True,
        "suggested_action": "retry_after_delay",
        "retry_delay_seconds": 10,
        "user_template": "AI 服务暂时不可用，10 秒后自动重试。如果持续失败请告知",
    },

    # ════════════════════════════════════════════════════════
    # AI 理解能力问题 - 不再机械重试，让用户介入
    # ════════════════════════════════════════════════════════
    "llm_output_invalid": {
        # 单次发生时仍可重试（adjudicate 内部已处理）
        # 但走到三次全挂时升级为 file_too_complex
        "retryable": True,
        "suggested_action": "retry_immediately",
        "retry_delay_seconds": 0,
        "user_template": "AI 输出格式异常，重新分析中",
    },
    "file_too_complex": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": (
            "文件「{file_name}」结构过于复杂，AI 三次尝试都无法准确理解。\n"
            "请检查文件是否：\n"
            "  1. 有清晰的表头行（避免合并单元格表头嵌套过深）\n"
            "  2. 单个 Sheet 内不要有多个不规则的子表格\n"
            "  3. 数据格式相对规范（避免一列混合多种格式）\n"
            "或者尝试手动整理后重新上传。"
        ),
    },

    # ════════════════════════════════════════════════════════
    # 文件本身问题 - 用户介入
    # ════════════════════════════════════════════════════════
    "file_corrupted": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": (
            "文件「{file_name}」可能已损坏或格式异常。\n"
            "建议用 Excel 打开后另存为新 xlsx 文件再上传。"
        ),
    },
    "file_too_large": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": (
            "文件「{file_name}」过大（{size_mb}MB / {rows} 行）超出处理上限。\n"
            "建议按日期/区域拆分后分别上传。"
        ),
    },
    "unsupported_format": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": "文件「{file_name}」格式不支持，仅支持 .xlsx/.xls/.csv/.tsv",
    },

    # ════════════════════════════════════════════════════════
    # 系统/配置问题 - 升级运维
    # ════════════════════════════════════════════════════════
    "auth_failure": {
        "retryable": False,
        "suggested_action": "escalate",
        "retry_delay_seconds": 0,
        "user_template": (
            "AI 服务配置异常（鉴权失败）。这不是你的问题，"
            "请联系管理员检查 DashScope API key 配置。"
        ),
    },
    "internal_error": {
        "retryable": False,
        "suggested_action": "escalate",
        "retry_delay_seconds": 0,
        "user_template": (
            "系统内部错误。这不是你的问题，"
            "请把这条消息截图发给管理员，附文件名「{file_name}」。"
        ),
    },
}
```

### 5.5 主 Agent 消费错误的标准流程

```python
# file_tool_mixin._file_analyze 改造
async def _file_analyze(self, executor, args, settings):
    from services.agent.file_ai_judge import FileAnalyzeError
    from services.agent.agent_result import AgentResult

    try:
        cache_path, sheet_names = await ensure_parquet_cache(abs_path, None, staging_dir)
    except FileAnalyzeError as e:
        return AgentResult(
            summary=e.user_message,        # ← 主 Agent 直接转给用户的中文
            status="error",
            error_message=e.error_summary, # ← 主 Agent 自己理解用
            metadata=e.to_metadata(),      # ← 结构化字段，主 Agent 据此决策
        )

    # ... 成功路径
```

### 5.6 主 Agent 收到错误后的决策矩阵

主 Agent 应按以下流程消费 `metadata`：

```
读取 metadata.error_category
  ↓
读取 metadata.suggested_action
  ↓
┌─────────────────────────────────────────────────────────┐
│ "retry_immediately"     → 直接重新调用 file_analyze     │
│                           （工具循环自动处理）            │
├─────────────────────────────────────────────────────────┤
│ "retry_after_delay"     → 等 metadata.retry_delay_      │
│                           seconds 秒后重新调用           │
│                           可向用户说"AI 繁忙等 5 秒"     │
├─────────────────────────────────────────────────────────┤
│ "ask_user"              → 不要自动重试                   │
│                           转告 summary 给用户            │
│                           等用户决定（换文件/手动整理）  │
├─────────────────────────────────────────────────────────┤
│ "escalate"              → 不要自动重试                   │
│                           告知用户是系统问题             │
│                           建议联系管理员                 │
└─────────────────────────────────────────────────────────┘
```

### 5.7 错误分类完整映射示例（三个真实场景）

**场景 1：网络抖动 → 自动恢复**

```python
attempts = [
    AnalyzeAttemptLog(1, "qwen-turbo", "default",
        error_category="network_failure", error_message="Connection reset"),
    AnalyzeAttemptLog(2, "qwen-turbo", "simplified",
        error_category="network_failure", error_message="Connection reset"),
    AnalyzeAttemptLog(3, "qwen-plus", "default",
        error_category="network_failure", error_message="Connection reset"),
]
→ final_category = "api_unavailable"
→ retryable=True, action="retry_after_delay", delay=10s
→ 主 Agent: 10 秒后自动重新调用 file_analyze
```

**场景 2：文件结构复杂 → 让用户介入**

```python
attempts = [
    AnalyzeAttemptLog(1, "qwen-turbo", "default",
        error_category="llm_output_invalid", error_message="missing field column_semantics"),
    AnalyzeAttemptLog(2, "qwen-turbo", "simplified",
        error_category="llm_output_invalid", error_message="semantic_type 非法: amount_total"),
    AnalyzeAttemptLog(3, "qwen-plus", "default",
        error_category="llm_output_invalid", error_message="JSONDecodeError"),
]
→ final_category = "file_too_complex"
→ retryable=False, action="ask_user"
→ 主 Agent 转告用户："文件结构过于复杂...建议手动整理后重新上传"
→ 主 Agent 等待用户决定，不再自动重试
```

**场景 3：API key 失效 → 升级管理员**

```python
attempts = [
    AnalyzeAttemptLog(1, "qwen-turbo", "default",
        error_category="auth_failure", error_message="Invalid API key"),
    # 后续不再尝试（auth_failure 直接 raise）
]
→ final_category = "auth_failure"
→ retryable=False, action="escalate"
→ 主 Agent 告知用户："AI 服务配置异常，请联系管理员"
```

### 5.8 retryable 但发生在 adjudicate 之前的优化

注意一种优化：若第 1 次出现 `auth_failure` 这种不可重试错误，**短路退出**不浪费后续 2 次调用：

```python
async def adjudicate(evidence: EvidencePool) -> AIDecision:
    attempts_log = []
    for i, cfg in enumerate(_ATTEMPTS, start=1):
        try:
            ...
        except Exception as e:
            category = _classify_error(e)
            attempt_log.error_category = category
            attempts_log.append(attempt_log)

            # 不可重试错误 → 立即短路
            if category in ("auth_failure",):
                break

            # rate_limit → 加 sleep
            if category == "rate_limit":
                await asyncio.sleep(2)

    # 构造最终 FileAnalyzeError ...
```

### 5.9 异常 → error_category 完整映射表

#### AI 调用阶段（_classify_error 中）

| Python 异常 | 映射 error_category |
|---|---|
| `json.JSONDecodeError` | `llm_output_invalid` |
| `ValueError` (schema 不符) | `llm_output_invalid` |
| `KeyError` (字段缺失) | `llm_output_invalid` |
| `asyncio.TimeoutError` | `timeout` |
| `openai.APITimeoutError` | `timeout` |
| `openai.RateLimitError` | `rate_limit` |
| `openai.AuthenticationError` | `auth_failure`（短路） |
| `openai.APIConnectionError` | `network_failure` |
| `openai.APIError` | `api_unavailable` |
| 其他 `Exception` | `internal_error` |

#### 文件阶段（AI 调用之前）

需要在 `ensure_parquet_cache` / Scanner 阶段把文件级错误也转为 `FileAnalyzeError`：

| 触发场景 | 映射 error_category |
|---|---|
| 文件大小 = 0 字节 | `file_corrupted` |
| `cache_path` 转换后不存在（当前 `ensure_parquet_cache:250` 的 ValueError） | `file_corrupted` |
| `fastexcel.read_excel` raise（zip 损坏） | `file_corrupted` |
| 文件扩展名不在 `_ANALYZE_EXTENSIONS` 集合 | `unsupported_format` |
| 路径 D 合并后 total_rows > `_MAX_MERGE_ROWS=1M` | `file_too_large` |
| 单文件 > 1GB | `file_too_large` |
| `PermissionError`（沙盒路径权限） | `internal_error`（保留原 PermissionError 处理） |

---

## 6. 4 条路径独立扫描器

### 6.1 共享扫描接口

新文件：`backend/services/agent/file_scanners.py`

```python
from abc import ABC, abstractmethod

class BaseScanner(ABC):
    """4 条路径的扫描器共享基类。"""

    def __init__(self, excel_path: str, sheet: str | None):
        self.excel_path = excel_path
        self.sheet = sheet
        import fastexcel
        self.reader = fastexcel.read_excel(excel_path)
        self.path_type = self.PATH_TYPE  # 子类指定

    @abstractmethod
    def scan(self) -> EvidencePool:
        """执行扫描，返回完整 evidence_pool。"""
        ...

    # 共享辅助函数
    def _scan_columns(self, df) -> list[ColumnEvidence]: ...
    def _scan_suspicious_rows(self, df) -> list[SuspiciousRow]: ...
    def _scan_formulas(self) -> list[FormulaEvidence]: ...
    def _scan_structure(self) -> ExcelStructure: ...

# 工厂（V1.1：路径 D 触发改为自动检测多 sheet）
def make_scanner(excel_path: str) -> BaseScanner:
    """根据代码 probe 结果自动分流到 4 条扫描路径。

    V1.1 变更：file_analyze 工具不暴露 sheet 参数，所以 sheet 永远为 None。
    路径 D 不再依赖 "sheet=='*'"，改为代码 probe 文件 sheet 数 ≥ 2 时自动走。
    """
    import fastexcel
    reader = fastexcel.read_excel(excel_path)
    sheet_names = reader.sheet_names

    # ── 路径 D 触发：多 sheet 自动走 ──
    if len(sheet_names) >= 2:
        return PathDScanner(excel_path, reader)

    # ── 单 sheet → probe 进一步分流 ──
    probe_result = _probe_single_sheet(reader)

    if probe_result.region_count >= 2:
        return PathCScanner(excel_path, reader, probe_result)
    if probe_result.total_rows >= 100_000:
        return PathBScanner(excel_path, reader, probe_result)
    return PathAScanner(excel_path, reader, probe_result)


def _probe_single_sheet(reader) -> dict:
    """单 sheet 场景：探测行数 + 是否多区域。"""
    # 读 20 行检测表头
    probe = reader.load_sheet(0, header_row=None, n_rows=_HEADER_MAX_SCAN).to_pandas()
    header_row = detect_header_row(probe.values.tolist())

    # 拿总行数
    probe_all = reader.load_sheet(0, header_row=header_row)
    total_rows = probe_all.total_height

    # 扫多区域
    scan_raw = reader.load_sheet(0, header_row=None, n_rows=5000).to_pandas()
    regions = detect_table_regions(scan_raw.values.tolist())

    return {
        "header_row": header_row,
        "total_rows": total_rows,
        "region_count": len(regions),
        "regions": regions,
    }
```

### 6.2 路径 A 小文件扫描器

```python
class PathAScanner(BaseScanner):
    PATH_TYPE = "A"

    def scan(self) -> EvidencePool:
        df = self.reader.load_sheet(self.sheet or 0, header_row=None).to_pandas()

        structure = self._scan_structure()
        formulas = self._scan_formulas()

        # 表头候选（前 5 行）
        header_candidates = df.head(5).values.tolist()
        detected_header = detect_header_row(header_candidates)

        # 列证据（全表扫描，因为是小文件）
        df_with_header = self.reader.load_sheet(
            self.sheet or 0, header_row=detected_header
        ).to_pandas()
        columns_ev = self._scan_columns(df_with_header)

        # 可疑行（全表扫描）
        suspicious = self._scan_suspicious_rows(df_with_header)

        # 关键样本（13 行）
        key_samples = self._build_key_samples(df_with_header, n_head=5, n_mid=3, n_tail=5)

        return EvidencePool(
            file_path=self.excel_path,
            file_name=Path(self.excel_path).name,
            total_rows=len(df),
            total_cols=len(df.columns),
            sheet_names=self.reader.sheet_names,
            target_sheet=self._resolve_sheet_name(),
            path_type="A",
            header_candidates=header_candidates,
            detected_header_row_code=detected_header,
            merged_ranges=structure.merged_ranges if structure else [],
            hidden_rows=list(structure.hidden_rows) if structure else [],
            hidden_cols=list(structure.hidden_cols) if structure else [],
            has_auto_filter=structure.has_auto_filter if structure else False,
            columns=columns_ev,
            key_samples=key_samples,
            suspicious_rows=suspicious,
            formulas=formulas[:30],
            formula_total_count=len(formulas),
        )
```

### 6.3 路径 B 大文件扫描器（V1.2：calamine + chunk 累加）

**V1.2 核心变化**：
- **V1.1**：fastexcel 一次性 `to_pandas()`（500K 行 1187 MB），向量化扫整张 DataFrame；行数硬上限 `MAX_TOTAL_ROWS = 800_000`
- **V1.2**：python-calamine `iter_rows()` 流式 → 每 100K 行打包成 DataFrame chunk → `_PathBChunkAccumulator` 跨 chunk 累加 → 释放（chunk DataFrame 不累积）；单元格上限 `MAX_TOTAL_CELLS = 25_000_000`（按列数自适应：800K → 约 1M~1.5M 行）

**为什么 calamine + chunk 不是 openpyxl SAX**（三方实测，500K 真实文件）：
| 后端 | 耗时 | 峰值 RSS | 决策 |
|------|------|---------|------|
| fastexcel 全表（V1.1）| 27.7s | 2156 MB | ❌ 内存高、上限低 |
| **calamine + chunk（V1.2）** | **29.6s** | **1474 MB** | ✅ **平衡：慢 8% / 省 32%** |
| openpyxl SAX | 56.6s | 1215 MB | ❌ 慢 2 倍但内存只省 200 MB |

```python
PATH_B_CHUNK_SIZE = 100_000
PATH_B_MAX_TOTAL_CELLS = 25_000_000  # 25M 单元格 — ECS 2.75GB 可用内存安全


class _PathBChunkAccumulator:
    """跨 chunk 累加列证据 / 可疑行 / key_samples（替代 BaseScanner 全表向量化）。

    - null_ratio: (累计 null) / (累计 total) 全列精确
    - classified_dist: 在预定行号采样
    - sample_values: 跨 chunk 拼接 head/mid/tail
    - is_long_id_candidate: 维护全局 abs().max()
    - suspicious_rows: 全行号映射到 Excel 1-indexed
    """
    def __init__(self, total_rows, n_cols, data_start_excel, col_names,
                 sample_idx_global, key_sample_idx_global, suspicious_limit):
        ...

    def process_chunk(self, chunk_df: pd.DataFrame, chunk_start_local: int):
        # ★ 关键：calamine 把空 cell 给 "" 而非 NaN，转 NaN 让 isna() 正确识别
        # （这也修了 baseline 在 fastexcel fallback-to-string 列上 null_ratio 算错的 bug）
        chunk_df = chunk_df.mask(chunk_df.eq(""), np.nan)
        ...

    def finalize_columns(self) -> list[ColumnEvidence]: ...
    def finalize_key_samples(self) -> list[dict]: ...


class PathBScanner(BaseScanner):
    PATH_TYPE = "B"
    MAX_TOTAL_CELLS = PATH_B_MAX_TOTAL_CELLS

    def scan(self) -> EvidencePool:
        # ① fastexcel probe 拿 (行数, 列数) — 单元格上限保护
        probe = self.reader.load_sheet(target, header_row=self.header_row)
        n_data = probe.total_height
        n_cols_probe = probe.width
        if n_data * n_cols_probe > self.MAX_TOTAL_CELLS:
            self._raise_file_too_large(n_data, n_cols_probe)

        # ② 结构 + 公式（复用 BaseScanner 工具）
        merged, hidden_rows, hidden_cols, has_filter = self._structure_to_lists()
        formulas, total_formulas = self._scan_formulas(target)

        # ③ 采样位置预计算（head 5 / mid 3 / tail 5 列证据 + head/mid/tail key_samples）
        sample_idx_global = _build_path_b_sample_idx(n_data)
        n_head, n_mid, n_tail = sample_segment_sizes(n_data)
        key_sample_idx_global = _build_path_b_key_sample_idx(n_data, n_head, n_mid, n_tail)

        # ④ calamine 流式遍历 + 累加器处理
        wb = python_calamine.CalamineWorkbook.from_path(self.excel_path)
        ws = wb.get_sheet_by_index(0)
        acc = None
        chunk_buf, chunk_start_local, rows_seen = [], 0, 0
        for raw_row in ws.iter_rows():
            if rows_seen < 5: header_candidates_raw.append(list(raw_row))
            if rows_seen <= self.header_row:
                if rows_seen == self.header_row:
                    col_names = [str(v) for v in raw_row]
                    acc = _PathBChunkAccumulator(...)
                rows_seen += 1; continue
            chunk_buf.append(list(raw_row)); rows_seen += 1
            if len(chunk_buf) >= PATH_B_CHUNK_SIZE:
                df = pd.DataFrame(chunk_buf)
                acc.process_chunk(df, chunk_start_local)
                chunk_start_local += len(chunk_buf)
                chunk_buf = []; del df; gc.collect()  # ★ 真释放
        # 收尾 chunk
        if chunk_buf:
            df = pd.DataFrame(chunk_buf)
            acc.process_chunk(df, chunk_start_local)
            del df; gc.collect()

        return EvidencePool(
            ...,
            columns=acc.finalize_columns(),
            key_samples=acc.finalize_key_samples(),
            suspicious_rows=acc.suspicious,
            ...,
        )

    def _raise_file_too_large(self, n_rows: int, n_cols: int):
        from services.agent.file_ai_judge import FileAnalyzeError
        size_mb = round(self.file_size / 1024 / 1024, 2)
        cells = n_rows * n_cols
        raise FileAnalyzeError(
            error_category="file_too_large",
            error_summary=(
                f"文件 {self.file_name} 超过 {self.MAX_TOTAL_CELLS:,} 单元格处理上限"
                f"（{n_rows:,} × {n_cols} = {cells:,}）"
            ),
            retryable=False,
            suggested_action="ask_user",
            user_message=(
                f"文件「{self.file_name}」过大（{size_mb}MB / {n_rows:,} 行 × {n_cols} 列），"
                f"超过 {self.MAX_TOTAL_CELLS:,} 单元格处理上限。建议按日期/区域拆分。"
            ),
            ...,
        )
```

**V1.2 实测数字**（53MB / 500K 行 / 23 列真实订单文件）：
| 指标 | V1.1 baseline | V1.2 calamine |
|------|---------------|---------------|
| 数据读取 | fastexcel.to_pandas 4.5s | calamine iter_rows + chunk 5.4s |
| scan() 端到端 | 24~30s | 26~35s |
| 峰值 RSS | 1187~2156 MB | 1082~1474 MB（-32%）|
| 行数上限 | 800K（硬限）| 25M cells（约 1M~1.5M 行 / 23 列）|
| chunk 期间内存累积 | N/A | **不累积**（10K/20K/30K/40K/50K 行 RSS 稳定）|
| null_ratio 准确性（string 列）| ❌ 严重低估 | ✅ 与 openpyxl ground truth 一致 |
| 长 ID 类型 | float64 推断（需 `is_long_id_candidate` 兜底）| str（天然保留）|
| 日期类型 | float epoch | datetime |

### 6.4 路径 C 多区域扫描器

```python
class PathCScanner(BaseScanner):
    PATH_TYPE = "C"

    def __init__(self, excel_path, sheet, probe_result):
        super().__init__(excel_path, sheet)
        self.regions = probe_result.regions  # 已 probe 的 RegionEvidence 列表

    def scan(self) -> EvidencePool:
        scan_raw = self.reader.load_sheet(
            self.sheet or 0, header_row=None, n_rows=5000,
        ).to_pandas()

        region_evidences = []
        for r in self.regions:
            head_sample = scan_raw.iloc[r.data_start:r.data_start + 5].values.tolist()
            tail_sample = scan_raw.iloc[max(r.data_start, r.data_end - 5):r.data_end].values.tolist()
            region_evidences.append(RegionEvidence(
                region_id=r.region_id,
                range_str=f"A{r.header_row + 1}:{_col_letter(len(r.columns))}{r.data_end}",
                header_row=r.header_row,
                header_cells=r.columns,
                head_sample=head_sample,
                tail_sample=tail_sample,
                row_count=r.row_count,
                suspected_type="unknown",
            ))

        return EvidencePool(
            file_path=self.excel_path,
            file_name=Path(self.excel_path).name,
            total_rows=len(scan_raw),
            total_cols=scan_raw.shape[1],
            sheet_names=self.reader.sheet_names,
            target_sheet=self._resolve_sheet_name(),
            path_type="C",
            header_candidates=scan_raw.head(5).values.tolist(),
            detected_header_row_code=0,
            regions=region_evidences,
            formulas=self._scan_formulas()[:30],
            formula_total_count=0,
        )
```

### 6.5 路径 D 多 sheet 扫描器（V1.1：触发条件变更）

**V1.1 触发条件**：代码 probe 时检测到 `len(sheet_names) >= 2` 自动走。不再依赖 `sheet=="*"` 参数。

**职责**：扫描所有 sheet 的元信息进入 evidence，**AI 一次裁决决定**：
- 每个 sheet 的 `role`：`data` / `meta` / `aggregated` / `skip`
- 每个 sheet 的 `merge_group`（同组的合并为一个 Parquet 加 `_sheet` 列）
- 单组 → 走 `_convert_all_sheets_to_parquet` 改造版（只合并 AI 指定的 sheet）
- 多组 → 类似多区域处理（每组一个独立 Parquet 段）
- 全 skip → raise `FileAnalyzeError("file_corrupted")`

```python
class PathDScanner(BaseScanner):
    PATH_TYPE = "D"
    MAX_SHEETS_SAMPLED = 20

    def scan(self) -> EvidencePool:
        all_names = self.reader.sheet_names[:200]
        sheet_evidences = []

        for name in all_names[:self.MAX_SHEETS_SAMPLED]:
            try:
                probe = self.reader.load_sheet(name, header_row=None, n_rows=20).to_pandas()
                detected_header = detect_header_row(probe.values.tolist())
                df = self.reader.load_sheet(name, header_row=detected_header).to_pandas()

                sheet_evidences.append(SheetEvidence(
                    name=name,
                    rows=len(df),
                    cols=len(df.columns),
                    header_candidates=probe.head(5).values.tolist(),
                    head_sample=df.head(3).values.tolist(),
                    tail_sample=df.tail(3).values.tolist(),
                    column_names=[str(c) for c in df.columns],
                ))
            except Exception as e:
                logger.warning(f"Path D scan sheet failed | sheet={name} | err={e}")

        # 未采样的 sheet 用第一个推断
        if len(all_names) > self.MAX_SHEETS_SAMPLED and sheet_evidences:
            base = sheet_evidences[0]
            for name in all_names[self.MAX_SHEETS_SAMPLED:]:
                sheet_evidences.append(SheetEvidence(
                    name=name,
                    rows=-1,  # 未知
                    cols=base.cols,
                    header_candidates=[],
                    head_sample=[],
                    tail_sample=[],
                    column_names=base.column_names,
                ))

        return EvidencePool(
            file_path=self.excel_path,
            file_name=Path(self.excel_path).name,
            total_rows=sum(s.rows for s in sheet_evidences if s.rows > 0),
            total_cols=sheet_evidences[0].cols if sheet_evidences else 0,
            sheet_names=all_names,
            target_sheet="*",
            path_type="D",
            header_candidates=[],
            detected_header_row_code=0,
            sheets=sheet_evidences,
        )
```

### 6.6 自适应上限规则

| 项 | 上限规则 | 50 万行文件实际 |
|---|---|---|
| `suspicious_rows` | `min(total_rows × 0.1%, 500)` | 500 |
| `key_samples` 路径 A | head 5 + mid 3 + tail 5 = 13 行 | — |
| `key_samples` 路径 B | head 10 + mid 10 + tail 10 = 30 行 | 30 |
| 桶代表（路径 B） | `min(total_rows / 100k × 2, 100)` | 10 |
| `formulas` | 内部上限 200，prompt 中 30 | 30 |
| `regions`（路径 C） | 实际数量（通常 < 10） | — |
| `sheets`（路径 D） | 采样 20，剩余只列名 | — |
| `column_semantics` | 等于总列数 | 23 |
| 整体 prompt token | 软上限 60K（qwen-turbo 1M 完全装得下） | ~50K |

---

## 7. CleaningStrategy 接入清洗

### 7.1 改造后的 `clean_excel` 签名

```python
def clean_excel(
    df: pd.DataFrame,
    excel_path: str,
    sheet_name: str | int,
    header_row: int = 0,
    structure: ExcelStructure | None = None,
    strategy: CleaningStrategy | None = None,  # ★ 新增
    chunk_row_offset: int = 0,
) -> tuple[pd.DataFrame, CleaningReport]:
    """清洗入口。strategy=None 时全部走硬规则（向后兼容）。"""

    report = CleaningReport(original_shape=(len(df), len(df.columns)))

    # Step 1: 多级表头展平（不变）
    _flatten_multi_header(df, report)

    # Step 2: 合并填充（按 strategy 决策）
    if structure and structure.merged_ranges:
        _apply_merge_fill_with_strategy(df, structure, header_row, report,
                                        chunk_row_offset, strategy)

    # Step 3: 列名去重（不变）
    _deduplicate_columns(df, report)

    # Step 4: 汇总行标记（按 strategy）
    if strategy and strategy.summary_rows:
        _mark_summary_rows_from_strategy(df, strategy.summary_rows, header_row,
                                         report, chunk_row_offset)

    # Step 5: 空行处理（按 strategy）
    _remove_empty_rows_cols_with_strategy(df, report, structure, strategy)

    # Step 6: 混合类型处理（按 strategy）
    _coerce_object_columns_with_strategy(df, report, strategy)

    # Step 7: 整数修复（按 strategy 保护 ID 列）
    _fix_int_columns_with_strategy(df, report, strategy)

    report.final_shape = (len(df), len(df.columns))
    return df, report
```

### 7.2 每个动作的策略化改造

#### `_apply_merge_fill_with_strategy`

```python
def _apply_merge_fill_with_strategy(df, structure, header_row, report,
                                     chunk_row_offset, strategy):
    """按 strategy.merged_cell_actions 执行。AI 没说的范围走 fill_down 默认。"""
    actions_map = {}
    if strategy:
        for a in (strategy.merged_cell_actions or []):
            actions_map[a.range_str] = a

    for min_row, max_row, min_col, max_col in structure.merged_ranges:
        range_str = f"{_col_letter(min_col - 1)}{min_row}:{_col_letter(max_col - 1)}{max_row}"
        action = actions_map.get(range_str)

        if action is None:
            # AI 没说 → 默认 fill_down
            action_type = "fill_down"
        else:
            action_type = action.action

        if action_type == "skip":
            continue
        elif action_type == "fill_down":
            _do_fill_down(df, min_row, max_row, min_col, max_col, header_row,
                          chunk_row_offset, report)
        elif action_type == "treat_as_header":
            pass  # 表头已由 _flatten_multi_header 处理
        elif action_type == "preserve_as_group":
            # 不填充，标记到 issues
            report.issues.append({
                "type": "merge_preserved_as_group",
                "severity": "info",
                "location": {"range": range_str},
                "preserved": True,
                "action": f"AI 判断为分组结构，保留 NaN：{range_str}",
            })
```

#### `_remove_empty_rows_cols_with_strategy`

```python
def _remove_empty_rows_cols_with_strategy(df, report, structure, strategy):
    """空行处理。strategy.preserve_empty_rows 中的行保留。"""
    preserve_set = set()
    if strategy:
        preserve_set = {p.row for p in (strategy.preserve_empty_rows or [])}

    # 空列：保持现状（只标注）
    _scan_and_mark_empty_cols(df, report, structure)

    # 空行：找到所有空行，排除 preserve_set
    data_cols = [c for c in df.columns if not str(c).startswith("_is_")]
    blank_mask = df[data_cols].apply(
        lambda col: col.isna() | col.astype(str).str.strip().eq("") | col.astype(str).eq("nan")
    ).all(axis=1)

    excel_row_offset = report.header_row + 2
    rows_to_drop = []
    for idx in df.index[blank_mask]:
        excel_row = idx + excel_row_offset
        if excel_row not in preserve_set:
            rows_to_drop.append(idx)

    if rows_to_drop:
        df.drop(rows_to_drop, inplace=True)
        df.reset_index(drop=True, inplace=True)
        report.empty_rows_removed = len(rows_to_drop)
```

#### `_coerce_object_columns_with_strategy`

```python
def _coerce_object_columns_with_strategy(df, report, strategy):
    """混合类型处理。strategy.mixed_type_handling 指定每列动作。"""
    handling_map = {}
    if strategy:
        for h in (strategy.mixed_type_handling or []):
            handling_map[h.col_letter] = h

    for i, col in enumerate(df.columns):
        if str(col).startswith("_is_"):
            continue

        col_letter = _col_letter(i)
        handling = handling_map.get(col_letter)

        if handling is None:
            # AI 没说 → 现行硬规则（infer_dtype + 强转 str）
            _do_default_coerce(df, col, report)
            continue

        if handling.action == "force_str":
            df[col] = df[col].astype(str).replace({"nan": None})
        elif handling.action == "extract_unit_number":
            # "1.5kg" → 1.5
            pattern = re.compile(r'(-?\d+\.?\d*)\s*' + re.escape(handling.unit))
            df[col] = df[col].astype(str).str.extract(pattern, expand=False).astype(float)
            report.issues.append({
                "type": "mixed_type_extracted",
                "severity": "info",
                "location": {"col": col_letter},
                "preserved": False,
                "action": f"AI 决策：列 {col} 提取单位 {handling.unit} 的数值",
            })
        elif handling.action == "extract_currency_amount":
            # "¥99.5" → 99.5
            df[col] = (df[col].astype(str)
                       .str.replace(r'[¥$￥,]', '', regex=True)
                       .astype(float))
        elif handling.action == "to_datetime":
            df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
```

#### `_fix_int_columns_with_strategy`（含 Bug-4 修复）

```python
def _fix_int_columns_with_strategy(df, report, strategy):
    """整数修复。strategy.id_columns 中的列跳过转换。"""
    id_cols = set()
    if strategy:
        id_cols = set(strategy.id_columns or [])

    fixed_cols = []
    for col in df.columns:
        col_str = str(col)
        if col_str.startswith("_is_") or col_str in id_cols:
            continue
        if df[col].dtype != "float64":
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue

        # ★ Bug-4 修复：长度 > 15 不转（兜底）
        try:
            max_len = non_null.astype(str).str.len().max()
            if max_len > 15:
                continue
            if (non_null == non_null.astype("int64")).all():
                df[col] = df[col].astype("Int64")
                fixed_cols.append(col_str)
        except (OverflowError, ValueError):
            pass

    report.int_cols_fixed = len(fixed_cols)
    if fixed_cols:
        report.issues.append({
            "type": "int_cols_fixed",
            "severity": "info",
            "location": {"cols": fixed_cols},
            "preserved": False,
            "action": f"整数修复（{len(fixed_cols)}列）：{', '.join(fixed_cols)}",
        })
```

### 7.3 兜底降级矩阵

| AI 决策字段 | AI 缺失/失败时降级 |
|---|---|
| `merged_cell_actions` 缺范围 | 该范围走 fill_down（现行行为） |
| `mixed_type_handling` 缺列 | 走 `_coerce_object_columns` 现行 force_str |
| `id_columns` | 兜底 `length > 15 → 跳过`（Bug-4 修复） |
| `summary_rows` | 不加 `_is_summary` 列 |
| `column_mapping` | 不重命名（保留原列名/Excel 自动列名） |
| `preserve_empty_rows` 缺 | 全空行删除（现行行为） |
| 整个 strategy=None | 全部走硬规则（向后兼容） |

### 7.4 兼容性保证

- `clean_excel(df, ..., strategy=None)` 行为与现版本完全等同（向后兼容）
- 现有测试 `test_excel_cleaner.py` 13 个测试类**不需要改动**
- 新增 `test_cleaning_strategy.py` 测试 strategy 接入

---

## 8. XML 渲染器

### 8.1 顶级节点结构

新文件：`backend/services/agent/file_xml_renderer.py`

```xml
<file_analysis>

  <data_access priority="critical" ready="true">
    <parquet_path>...</parquet_path>
    <original_path>...</original_path>
    <quick_start><![CDATA[
duckdb.sql("SELECT * FROM read_parquet('...') LIMIT 5").df()
    ]]></quick_start>
  </data_access>

  <file_meta priority="high">
    <path>...</path>
    <filename>...</filename>
    <size_mb>...</size_mb>
    <rows>...</rows>
    <cols>...</cols>
    <sheet_name>...</sheet_name>
    <sheet_count>...</sheet_count>
    <processed_at>...</processed_at>
    <path_type>A|B|C|D</path_type>
  </file_meta>

  <ai_decision priority="critical" model="qwen-turbo" attempt="1" elapsed_ms="1240">
    <header_row>...</header_row>
    <data_start_row>...</data_start_row>
    <header_note>...</header_note>
    <column_semantics>
      <col letter="A" name="..." type="..." order_level="true|false" is_id="true|false"/>
    </column_semantics>
    <summary_rows>... 或 <summary_rows/> （空 = 确认无）</summary_rows>
    <regions> <!-- 路径 C --> </regions>
    <sheets>  <!-- 路径 D --> </sheets>
    <data_quality_notes>
      <note severity="...">...</note>
    </data_quality_notes>
    <overall_summary>...</overall_summary>
  </ai_decision>

  <usage_hints priority="critical">
    <hint severity="must">...</hint>
    <code_example title="..."><![CDATA[...]]></code_example>
  </usage_hints>

  <column_schema priority="high">
    <column letter="..." name="..." type="..." null_ratio="..." min="..." max="..." 
            categories="..." unique="..." order_level="..."/>
  </column_schema>

  <grain priority="high">
    <group_key>...</group_key>
    <unique_count>...</unique_count>
    <avg_group_size>...</avg_group_size>
    <order_level_fields><field>...</field></order_level_fields>
    <line_level_fields><field>...</field></line_level_fields>
  </grain>

  <sample_data priority="medium">
    <column_index>A=... | B=... | ...</column_index>
    <segment name="head"><row n="...">{...}</row></segment>
    <segment name="middle">...</segment>
    <segment name="tail">...</segment>
  </sample_data>

  <related_files priority="high">  <!-- 新增 -->
    <relation type="join" confidence="0.85">
      <file>orders.xlsx</file>
      <common_columns>商品编码,日期</common_columns>
      <hint>...</hint>
    </relation>
  </related_files>

  <cleaning_result priority="medium">
    <strategy_summary>
      <ai_decided>
        <action type="..." value="..." reason="..."/>
      </ai_decided>
      <code_executed>
        <action type="..." count="..."/>
      </code_executed>
    </strategy_summary>
  </cleaning_result>

  <formulas priority="medium" total_count="...">  <!-- 仅有公式时 -->
    <formula cell="..." expression="..." value="..."/>
  </formulas>

</file_analysis>
```

### 8.2 动态 sample 行数规则

```python
def _sample_segment_size(total_rows: int) -> tuple[int, int, int]:
    """返回 (head_n, mid_n, tail_n)。"""
    if total_rows <= 10_000:
        return 3, 0, 3       # 极简：6 行
    elif total_rows <= 100_000:
        return 4, 2, 4       # 标准：10 行
    elif total_rows <= 1_000_000:
        return 5, 3, 5       # 推荐：13 行
    else:
        return 6, 6, 6       # 大文件：18 行
```

### 8.3 column_index 顶部映射

```python
def _build_column_index_attr(decision: AIDecision) -> str:
    """生成 sample_data 顶部的 column_index 字符串。"""
    parts = []
    for cs in decision.column_semantics:
        parts.append(f"{cs.letter}={cs.business_name}")
    return " | ".join(parts)
```

### 8.4 CDATA 代码示例生成

```python
def _build_code_examples(decision: AIDecision, parquet_path: str) -> list[dict]:
    """根据 grain 自动生成 SQL 范式。"""
    examples = []

    # 找订单级数值列
    order_level_numeric = [
        cs.business_name for cs in decision.column_semantics
        if cs.is_order_level and cs.semantic_type in ("amount", "quantity")
    ]

    if order_level_numeric:
        first_field = order_level_numeric[0]
        group_key = next(
            (cs.business_name for cs in decision.column_semantics
             if cs.semantic_type == "id" and cs.is_id_column),
            "id_column",
        )
        examples.append({
            "title": "订单级金额聚合（必用）",
            "code": (
                f'SELECT SUM("{first_field}") AS 总额\n'
                f'FROM (\n'
                f'    SELECT DISTINCT "{group_key}", "{first_field}"\n'
                f"    FROM read_parquet('{parquet_path}')\n"
                f')'
            ),
        })

    return examples
```

### 8.5 related_files 节点（跨文件关联整合）

```python
def _build_related_files_node(parquet_path: str, staging_dir: str) -> list[dict]:
    """从 session_files.json 读取与当前文件相关的关联。"""
    from services.agent.session_files import read_session_files

    data = read_session_files(staging_dir)
    relations = data.get("potential_relations", [])

    current_name = Path(parquet_path).stem
    related = []
    for rel in relations:
        if current_name in rel.get("files", []):
            other = [f for f in rel["files"] if f != current_name][0]
            related.append({
                "type": rel["relation_type"],  # join | union | column_match
                "confidence": rel["confidence"],
                "other_file": other,
                "common_columns": rel["common_columns"],
                "hint": rel["hint"],
            })

    return related
```

### 8.6 AI 失败时直接抛 FileAnalyzeError

XML 渲染**仅在 AI 成功时**执行。失败时不输出任何 XML 节点，由 `FileAnalyzeError`（含完整结构化字段）承载错误信息。

```python
# 在 ensure_parquet_cache 中
async def ensure_parquet_cache(excel_path, sheet, staging_dir):
    # ... 缓存检查 ...
    scanner = make_scanner(excel_path, sheet)
    evidence = scanner.scan()

    # AI 一次裁决（含失败链）
    decision = await adjudicate(evidence)
    # ↑ 失败时已经在 adjudicate 内部 raise FileAnalyzeError（含完整字段）
    #   不需要再 wrap

    # ... 执行清洗 + 写 Parquet + 渲染 XML ...
```

主 Agent 工具调用流程（详见 §5.5）：

```python
# file_tool_mixin._file_analyze
from services.agent.file_ai_judge import FileAnalyzeError

try:
    cache_path, sheet_names = await ensure_parquet_cache(abs_path, None, staging_dir)
except FileAnalyzeError as e:
    return AgentResult(
        summary=e.user_message,       # ← 直接转给用户的中文（已格式化）
        status="error",
        error_message=e.error_summary,
        metadata=e.to_metadata(),     # ← 含 error_category / suggested_action /
                                      #    retry_delay_seconds / file_context /
                                      #    attempts_summary
    )
```

**主 Agent 看到的最终 AgentResult 示例**（文件太复杂场景）：

```json
{
  "summary": "文件「sales_data.xlsx」结构过于复杂，AI 三次尝试都无法准确理解。\n请检查文件是否：\n  1. 有清晰的表头行...",
  "status": "error",
  "error_message": "文件 sales_data.xlsx AI 分析失败（file_too_complex）",
  "metadata": {
    "error_category": "file_too_complex",
    "retryable": false,
    "suggested_action": "ask_user",
    "retry_delay_seconds": 0,
    "file_context": {
      "name": "sales_data.xlsx",
      "size_mb": 12.5,
      "rows": 85033,
      "path_type": "A"
    },
    "attempts_summary": [
      {"n": 1, "model": "qwen-turbo", "elapsed_ms": 8200, "category": "llm_output_invalid",
       "error": "JSON 缺字段 column_semantics"},
      {"n": 2, "model": "qwen-turbo", "elapsed_ms": 7100, "category": "llm_output_invalid",
       "error": "semantic_type 非法值"},
      {"n": 3, "model": "qwen-plus", "elapsed_ms": 12300, "category": "llm_output_invalid",
       "error": "JSONDecodeError at line 23"}
    ]
  }
}
```

主 Agent 据此**精确决策**：
- `suggested_action="ask_user"` → 不自动重试
- 把 `summary` 直接展示给用户（已是可读中文）
- 用户决定换文件 / 手动整理 / 放弃

而**网络抖动场景**主 Agent 会自动重试不打扰用户：

```json
{
  "summary": "AI 服务网络不稳定，正在重新分析「sales_data.xlsx」",
  "metadata": {
    "error_category": "api_unavailable",
    "retryable": true,
    "suggested_action": "retry_after_delay",
    "retry_delay_seconds": 10
  }
}
```

主 Agent 看到 `retryable=true + retry_after_delay` → 等 10 秒后自动重新调 `file_analyze`，用户基本无感。

---

## 9. Phase 0-6 详细分解

### Phase 0：数据结构 + 接口定义（1.5 天）

**目标**：所有新数据结构、接口定义、空实现就位，单元测试通过。

| 文件 | 改动 |
|---|---|
| `services/agent/file_evidence.py` | **新增**：EvidencePool / CellSample / SuspiciousRow / ColumnEvidence / RegionEvidence / SheetEvidence / FormulaEvidence |
| `services/agent/file_ai_decision.py` | **新增**：AIDecision / ColumnSemantic / MergedCellAction / MixedTypeAction / EmptyRowDecision / RegionDecision / SheetDecision / DataQualityNote |
| `services/agent/file_cleaning_strategy.py` | **新增**：CleaningStrategy + from_decision |
| `services/agent/file_meta.py` | **改 FileMeta**：删除 prescan/confidence/processed_by；新增 ai_decision/cleaning_strategy/evidence_summary/related_files/xml_view |
| `tests/test_file_evidence.py` | **新增**：dataclass 序列化/反序列化测试 |
| `tests/test_file_ai_decision.py` | **新增** |
| `tests/test_file_cleaning_strategy.py` | **新增** |

**验证**：
- `pytest tests/test_file_evidence.py tests/test_file_ai_decision.py tests/test_file_cleaning_strategy.py`
- `pytest tests/test_file_meta.py::TestFileMetaToDict::test_serialize_v2`

### Phase 1：底层共享栈修复（V1.1：1.5 天）

**目标**：删除 `_compress_issues` 旁路造谣；修复 `_scan_issues` 方向错位；修复 4 个隐藏 bug（Bug-4/6/8/9）。

| 文件 | 改动 | 对应 bug |
|---|---|---|
| `services/agent/file_meta.py` | **删除 `_compress_issues`**；改造 `_scan_issues` 为双方向（列级保留 + 行级新增到 evidence_pool）；`format_file_view` markdown 版本暂保留作 fallback（Phase 5 删） | Bug-1, Bug-2 |
| `services/agent/file_meta.py` | `_dedup_samples_by_signature` 签名规则增强（数值列前 4 位有效数字，字符串前 16 字符 hash + 长度） | Bug-6 |
| `services/agent/excel_cleaner.py` | `_fix_int_columns` 加长度 > 15 跳过兜底 | Bug-4 |
| `services/agent/excel_cleaner.py` | `_MAX_XML_SIZE` 超阈值时用 openpyxl read_only 流式 fallback | Bug-9 |
| `services/agent/excel_cleaner.py` | **删除 `_mark_hidden_rows`**（孤儿函数，无主流程调用） | dead code |
| `services/agent/data_query_cache.py` | `header_depth > 1 且 total_rows >= 100k` 降级为单级表头读取 | Bug-8 |
| `tests/test_file_meta.py` | 删除 `TestCompressIssues`；新增 `TestScanIssuesRowLevel` | |
| `tests/test_excel_cleaner.py` | 新增 `TestFixIntColumnsLongId` / `TestXmlFallback` | |

**注意**：
- ~~Bug-5 column_mapping off-by-one~~（V1.1 已确认非 bug，"统一风格"工作放 Phase 6 一起做）
- ~~Bug-7 _dedup_issues 跨块吞行号~~（V1.1 已确认非 bug，无改动）

**验证**：
- 全部现有测试通过（已确认 60+ 测试类回归）
- 用 `104960729691_fd1952.xlsx` 跑 `_compress_issues` 删除后输出不再有"_is_summary"造谣

### Phase 2：4 路径扫描器（3 天）

| 文件 | 改动 |
|---|---|
| `services/agent/file_scanners.py` | **新增**：BaseScanner / PathAScanner / PathBScanner / PathCScanner / PathDScanner / make_scanner |
| `services/agent/file_scanners.py` | 各 scanner 内部实现自适应上限 |
| `tests/test_file_scanners.py` | **新增**：单元测试 4 个 scanner |
| `tests/test_file_scanners.py` | 用真实文件回归（11.6 MB 小文件 / 67.7 MB 大文件） |

**关键实现细节**：
- 路径 B 流式关键词扫描必须用 chunked load（不能全量）
- 路径 D 多 sheet 采样上限 20（其余只列名）
- 所有路径产出统一 EvidencePool

**验证**：
- 50 万行文件扫描时长 < 5 秒
- evidence_pool 内存占用 < 100 MB

### Phase 3：AI 调用层（2 天）

| 文件 | 改动 |
|---|---|
| `services/agent/file_ai_judge.py` | **新增**：adjudicate / build_prompt / _call_llm / _parse_and_validate |
| `services/agent/file_ai_judge.py` | 失败链实现（turbo×2 → plus×1） |
| `services/agent/file_ai_judge.py` | prompt simplified variant |
| `tests/test_file_ai_judge.py` | **新增**：mock LLM 的失败链测试 |
| `tests/test_file_ai_judge.py` | JSON schema 校验测试 |
| `tests/test_file_ai_judge.py` | 真实 LLM 调用集成测试（标 `@pytest.mark.integration`） |

**验证**：
- mock 3 次失败 → raise FileAnalyzeError
- mock 第 2 次成功 → 返回 AIDecision，attempt_count=2
- 真实 50 万行文件 prompt 大小 < 60K tokens（实测）

### Phase 4：CleaningStrategy 接入清洗（2 天）

| 文件 | 改动 |
|---|---|
| `services/agent/excel_cleaner.py` | `clean_excel` 增加 `strategy` 参数 |
| `services/agent/excel_cleaner.py` | 5 个动作策略化（见 §7.2） |
| `services/agent/excel_cleaner.py` | 兜底降级矩阵（见 §7.3） |
| `tests/test_excel_cleaner.py` | 新增 `TestCleanExcelWithStrategy` |
| `tests/test_excel_cleaner.py` | 现有 13 个测试类回归 |

**验证**：
- `clean_excel(df, strategy=None)` 行为与旧版完全等同
- `clean_excel(df, strategy=valid_strategy)` 按策略执行

### Phase 5：XML 渲染器 + 1 处调用方迁移（V1.1：2 天）

| 文件 | 改动 |
|---|---|
| `services/agent/file_xml_renderer.py` | **新增**：render_xml(meta) → str |
| `services/agent/file_xml_renderer.py` | 包含 §8.1-8.6 全部节点 |
| `services/agent/file_meta.py` | **删除 `format_file_view`**（已被 render_xml 替代） |
| `services/agent/file_tool_mixin.py:306` | `format_file_view` → `render_xml` |
| `services/agent/file_tool_mixin.py:_file_analyze` | 加 `except FileAnalyzeError` 内部捕获（不能冒泡到 _file_dispatch） |
| `services/agent/file_tool_mixin.py:_file_analyze` | 保留所有副作用：cache.register / set_parquet / set_analyzed |
| `tests/test_file_xml_renderer.py` | **新增** |
| `tests/test_file_tools.py` | 改 file_analyze 返回检查（XML 校验）+ 失败链 metadata 测试 |

**V1.1 注意**：
- `file_processor.py:141/160/177` 不再修改（Phase 6 整体删除文件）
- `tests/test_file_processor.py` Phase 6 整体删除

**验证**：
- 用 50 万行文件回归，输出 XML 通过 lxml 解析校验
- 主 Agent prompt 注入测试（XML 不破坏整体 prompt 结构）
- 错误场景测试：mock AI 三次失败 → 返回带 metadata 的 AgentResult，不被 _file_dispatch 吞

### Phase 6：废弃模块清理 + 编排重做（V1.1：1 天，简化 -0.5 天）

| 文件 | 改动 |
|---|---|
| `services/agent/file_prescan.py` | **删除整个文件**（被 file_scanners.py + file_ai_judge.py 替代） |
| `services/agent/file_processor.py` | **删除整个文件**（V1.1 确认 dead code，无外部调用方） |
| `backend/skills/file-fix.md` | 删除（仅 file_processor 引用） |
| `services/agent/data_query_cache.py` | **重做** `ensure_parquet_cache`：调 make_scanner → adjudicate → CleaningStrategy.from_decision → 走 clean_excel/convert_multi_region/_convert_all_sheets_to_parquet |
| `services/agent/data_query_cache.py` | **删除** `_prescan_schema` / `_infer_segment_type` / `_unify_column_types` / 大文件分支中的相关代码 |
| `services/agent/data_query_cache.py` | 大文件 schema 推断改用 `EvidencePool.columns` 的 classified_dist |
| `services/agent/data_query_cache.py` | 缓存键加版本号 `_cache_v2_xxx` 让旧缓存失效 |
| `services/agent/data_query_cache.py` | **统一 column_mapping 风格**（Bug-5 修正）：路径 C `convert_multi_region` 与路径 A/B 的 `_apply_column_mapping` 都用同一种 col_idx 计算 |
| `tests/test_file_prescan.py` | **删除**（被 test_file_ai_judge.py + test_file_scanners.py 替代） |
| `tests/test_prescan_integration.py` | 改造为 test_file_analyze_integration.py（端到端） |
| `tests/test_file_processor.py` | **删除**（dead code 测试） |

**验证**：
- 全部测试通过
- 真实回归：两个真实文件（11.6 MB / 67.7 MB）完整跑通
- 性能基准：50 万行文件端到端 < 15 秒

---

## 10. 边界场景清单（必须验证）

| # | 场景 | 期望行为 |
|---|---|---|
| 1 | 文件大小 = 0 字节 | raise "文件为空" |
| 2 | 文件不是 Excel 但扩展名是 .xlsx | raise "文件格式错误" |
| 3 | 单 sheet 有 1 行（仅表头） | AI 裁决 + 输出空数据 |
| 4 | 单 sheet 1000 列 | 列证据数量不超过 1000，prompt 不爆 |
| 5 | 多 sheet 全部空 sheet | 走路径 D，AI 全 role=skip → raise FileAnalyzeError("file_corrupted") |
| 6 | 多区域只有 1 个区域 | 退回路径 A 处理 |
| 7 | 多级表头 3 层 | depth 限制为 3（现有上限） |
| 8 | 多级表头 + 大文件（≥ 10万行） | 走 Bug-8 修复后的降级路径 |
| 9 | 含 100,000 个公式 | 提取上限 200，prompt 中只展示 30 |
| 10 | 表头 ID 是 19 位数字 | Bug-4 修复 + AI is_id_column=true 双重保护 |
| 11 | 列名全部为"销售主题分析-按订单商品明细" | AI 识别 Row 1 是标题行，data_start_row 跳到 3 |
| 12 | 隐藏列含敏感数据 | 不删除，标 hidden_cols issue + ai_decision 提示 |
| 13 | autofilter 开启 | has_auto_filter=true 进 evidence + AI 提示 |
| 14 | xlsx XML > 500 MB | Bug-9 fallback：openpyxl 流式读 mergedCells |
| 15 | 文件含负数（如退款） | AI 识别为业务退款，不进 anomalies |
| 16 | 列含混合类型 "1.5kg"/"2kg" | AI 决策 extract_unit_number action=kg |
| 17 | 列含 "¥99.5"/"¥120" | AI 决策 extract_currency_amount |
| 18 | sheet 名含空格/特殊字符 | fuzzy_match_sheet 兜底（保留现有） |
| 19 | 多 sheet 但全部同结构 | AI 决策全 role=data + 同 merge_group → 合并 |
| 20 | 多 sheet 含"说明"/"汇总"sheet | AI 决策非数据 sheet 的 role=meta/aggregated → 跳过，只合并 data 类 |
| 21 | 网络故障 → AI 调用超时 | 重试链触发 |
| 22 | qwen-turbo 输出非 JSON | 重试链触发，第 2 次用 simplified prompt |
| 23 | qwen-plus 也失败 | raise FileAnalyzeError（结构化），主 Agent 收到结构化 metadata |
| 24 | 缓存命中 | 直接返回 cache_path + 读 meta.xml_view 字段 |
| 25 | 同时上传 2 个相同 hash 文件 | LRU lock 保证只跑一次扫描 |
| 26 | chunked 处理时单 chunk 处理失败 | 跳过该 chunk，merged_report 记 warning |
| 27 | session_files.json 不存在 | related_files 节点为空，不影响主流程 |
| 28 | xlsx 含日文/俄文表头 | AI 应能识别（qwen 多语言） |
| 29 | 文件含 100 万行 | sample 取 18 行；suspicious 上限 500；评估 prompt < 60K tokens |
| 30 | clean_excel 处理过程中 OOM | 上抛 raise → `_file_analyze` 捕获 → 转为 FileAnalyzeError("internal_error") |
| 31 | _file_analyze 内部任何步骤 raise | 必须捕获 FileAnalyzeError 转 AgentResult，不能冒泡到 _file_dispatch（会吞 metadata） |
| 32 | AI 决策后某列被改名导致重复 → 自动加 _1/_2 后缀 | XML 输出告知主 Agent 实际列名（保留现有 _apply_column_mapping 去重逻辑） |

---

## 11. 测试覆盖计划

### 11.1 新增测试文件

| 测试文件 | 覆盖内容 | 测试类数 |
|---|---|---|
| `tests/test_file_evidence.py` | EvidencePool 等 dataclass 序列化 | 3 |
| `tests/test_file_ai_decision.py` | AIDecision schema 校验 | 4 |
| `tests/test_file_cleaning_strategy.py` | from_decision 映射 | 2 |
| `tests/test_file_scanners.py` | 4 个 scanner 单元测试 + 真实文件回归 | 8 |
| `tests/test_file_ai_judge.py` | adjudicate 失败链 + JSON schema 校验 | 6 |
| `tests/test_file_xml_renderer.py` | XML 节点完整性 + 真实文件渲染 | 5 |
| `tests/test_cleaning_strategy_integration.py` | clean_excel(strategy) 端到端 | 4 |
| `tests/test_file_analyze_integration.py` | 端到端（替代 test_prescan_integration） | 7 |

### 11.2 改造的现有测试

| 测试文件 | 改动 |
|---|---|
| `test_file_meta.py` | 删除 TestCompressIssues；新增 TestScanIssuesRowLevel；TestGenerateFileMeta 改造 v2 字段 |
| `test_excel_cleaner.py` | 新增 TestCleanExcelWithStrategy / TestFixIntColumnsLongId / TestXmlFallback |
| `test_table_region_detector.py` | convert_multi_region 测试覆盖 column_mapping 风格统一 |
| `test_file_tools.py` | file_analyze 返回 XML 而非 markdown + 失败链 metadata 测试 |

### 11.3 完全删除的测试

| 测试文件 | 删除理由 |
|---|---|
| `test_file_prescan.py` | prescan 模块整体废弃 |
| `test_prescan_integration.py` | 被 test_file_analyze_integration.py 替代 |
| `test_file_processor.py` | **V1.1：file_processor.py 整体删除（dead code）** |

### 11.4 真实数据回归

| 文件 | 大小 | 测试目标 |
|---|---|---|
| `104960729691_fd1952.xlsx` | 100 KB / 1,171 行 | 路径 A：无误报 _is_summary，无 BinderException |
| `4月 销售主题分析-按订单商品明细 -1.xlsx` | 11.6 MB / 85K 行 | 路径 A 大文件边界 |
| `4月销售主题分析-按订单商品明细.xlsx` | 67.7 MB / 500K 行 | 路径 B：19 位订单号保 string；prompt < 60K tokens |
| `2026公摊明细表4月-按订单数.xlsx` | 27 MB | 中等文件 |

---

## 12. 风险与回滚

### 12.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| AI 调用成本上升（qwen-plus 更贵） | 中 | 中 | 失败链顺序保证 80% 命中 qwen-turbo |
| AI 决策错误导致数据损坏 | 低 | 高 | CleaningStrategy 所有"破坏性动作"（删列/转换）有兜底 + audit log |
| XML 输出 token 超主 Agent 上限 | 低 | 中 | 动态 sample 行数；实测最坏 < 15K tokens |
| 旧缓存读取错误 | 中 | 低 | 缓存键加版本号 `_cache_v2_xxx`，旧缓存自然失效 |
| session_files.json 已有跨文件关联不兼容 | 低 | 低 | 整合时增加字段，不删除 |
| AI 输出 JSON 解析失败率高 | 中 | 中 | response_format=json_object 强约束 + simplified prompt fallback |
| 大文件扫描超时 | 低 | 中 | 流式扫描，不全量加载；timeout 兜底 |
| 测试覆盖不全引入回归 | 中 | 高 | 强制全部 60+ 测试类通过 + 4 个真实文件回归 |

### 12.2 回滚方案

| 阶段 | 回滚策略 |
|---|---|
| Phase 0-1 | 单测覆盖，无副作用，git revert 即可 |
| Phase 2-4 | 新代码与老代码并存（强 feature flag）；问题时切回老路径 |
| Phase 5-6 | 缓存键已 bump 到 v2，回滚后老文件按 v2 重新生成；切回 markdown 输出格式 |

### 12.3 灰度发布建议

- 加 `settings.file_analyze_v2_enabled` 开关
- 默认开启 v2，可以紧急切回 v1
- 14 天观察期后删除 v1 路径

---

## 13. 验证清单（V3.3 最终交付）

按 V3.3 §五要求，每一项必须用工具证据：

- [ ] `wc -l` 检查所有新文件 ≤ 500 行（超过拆分）
- [ ] `grep` 确认 `_compress_issues` / `format_file_view` / `run_prescan` 已无引用
- [ ] `pytest tests/` 全部通过（含新增 39 个测试类）
- [ ] 4 个真实文件回归（命令 + 输出截图）
- [ ] `test_file_analyze_integration.py` 端到端通过
- [ ] 50 万行文件 prompt token 实测 ≤ 60K
- [ ] 50 万行文件端到端时长 ≤ 15 秒
- [ ] XML 输出 lxml 解析校验通过
- [ ] 主 Agent 真实调用流程（模拟用户上传）

---

## 14. 关联文档更新

| 文档 | 改动 |
|---|---|
| `TECH_文件处理系统.md` | §三/§四（L1→L2→L3） 标记为"已废弃，见 TECH_file_analyze_重构.md" |
| `TECH_文件处理系统_坐标预探测方案.md` | 整体标记 DEPRECATED |
| `docs/PROJECT_OVERVIEW.md` | 更新 file_analyze 模块说明 |
| `docs/FUNCTION_INDEX.md` | 删除已废弃函数；新增 11 个新函数索引 |

---

## 15. Phase 总时间预算（V1.1 修正）

| Phase | 工作内容 | V1.0 预估 | V1.1 预估 | 备注 |
|---|---|---|---|---|
| Phase 0 | 数据结构 + 接口 | 1.5 天 | 1.5 天 | |
| Phase 1 | 底层共享栈修复 + bug | 2 天 | 1.5 天 | bug 从 6 个减为 5 个（Bug-5/7 删除） |
| Phase 2 | 4 路径扫描器 | 3 天 | 3 天 | |
| Phase 3 | AI 调用层 + prompt | 2 天 | 2 天 | |
| Phase 4 | CleaningStrategy 接入 | 2 天 | 2 天 | |
| Phase 5 | XML 渲染器 + 调用方迁移 | 2 天 | 2 天 | |
| Phase 6 | 废弃模块清理 + 编排重做 | 1.5 天 | 1 天 | file_processor 直接删 -0.5 天 |
| **总计代码** | | **14 天** | **13 天** | |
| 测试覆盖 | 与各 Phase 并行 | +2 天 | +2 天 | |
| 真实回归 + 灰度发布 | | +2 天 | +2 天 | |
| **总交付** | | **18 天** | **17 天** | -1 天 |

---

## 16. 收尾确认

本方案覆盖范围：

✅ 现有 4 条路径（A/B/C/D）的完整重做
✅ 9 个 bug 的修复（核心 3 个 + 隐藏 6 个）
✅ 数据结构升级（FileMeta v1 → v2）
✅ AI 调用方式重构（开头盲采样 → 末尾全证据裁决 + 三层失败链）
✅ 清洗策略 AI 化（5 个动作 + 硬规则兜底）
✅ XML 结构化输出（含 related_files 跨文件关联）
✅ L2/L3 沙盒修复体系废弃
✅ 全部测试覆盖与回归

**开工前最后确认**：以上方案完整覆盖你的设计意图，无遗漏关键细节，可以开始 Phase 0 实施。

