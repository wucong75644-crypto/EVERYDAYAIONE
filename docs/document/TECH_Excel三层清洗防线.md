# TECH_Excel三层清洗防线

> 版本：v2.0（评审后修订）
> 状态：方案确认，待实施
> 日期：2026-05-07
> 评审日期：2026-05-07

## 一、背景

### 问题

Excel 既是数据格式又是展示格式，用户上传的 Excel 经常包含：
- 合并单元格（跨行合并，pandas 读成 NaN）
- 多级表头（2-3 行表头，只检测到一行）
- 隐藏行/列（被跳过或误读）
- 汇总行混在数据里（"合计"行被当成数据）
- NaN 干扰类型推断（合并单元格产生的 NaN 导致数值列被推断为字符串）

### 现状

当前 `data_query_cache.py` 的 `_convert_excel_to_parquet` 只做了：
- 表头行自动检测（只取一行）
- 删除全空 Unnamed 列
- 类型推断（95% 阈值）

合并单元格、多级表头等问题未处理，错误数据直接写入 Parquet 缓存，后续 SQL 查询和 code_execute 都基于错误数据计算。

### 核心链路

```
用户上传 Excel
    ↓
① file_list 发现文件 → 提取元数据
    ↓
② data_query 探索/查询 → Excel → Parquet 转换 ★ 数据质量防线
    ↓
③ Parquet 缓存写入 staging（schema 锁定）
    ↓
④ data_query SQL / code_execute → 从 staging 读干净数据
    ↓
⑤ 结果返回用户
```

**第②步是唯一的修复点**——Parquet 写入后 schema 锁定，后续无法修正。

## 二、方案：三层清洗防线

三层清洗逻辑**抽到独立文件 `excel_cleaner.py`**（`data_query_cache.py` 已 491 行，内联会超 500 行硬约束），提供统一入口供 `_convert_excel_to_parquet` 和 `_convert_all_sheets_to_parquet` 共用。

**核心原则**：标记优先于删除（ETL 行业标准，Apache Spark Deequ / Great Expectations 通行做法），保留原始数据可追溯性。

```
原始 Excel
    ↓
pd.read_excel (calamine 引擎，快速读取)
    ↓
excel_cleaner.clean_excel(df, excel_path, sheet_name)  ← 独立文件
    ├─ Layer 1: 结构检测（openpyxl 元数据）
    ├─ Layer 2: 智能清洗（基于检测结果）
    └─ Layer 3: 质量校验（写 Parquet 前）
    ↓
返回 (cleaned_df, CleaningReport)
    ↓
_coerce_object_columns (类型推断)
    ↓
写入 Parquet → staging
写入 CleaningReport → .meta.json（与 Parquet 同目录）
```

**文件结构**：
```
services/agent/
├── data_query_cache.py      # 现有文件，调用 clean_excel() 入口
├── data_query_executor.py   # 探索模式读 .meta.json 注入报告
└── excel_cleaner.py         # ★ 新增：三层清洗独立文件
```

### Layer 1: 结构检测

**职责**：用 openpyxl 读取 Excel 元数据（不加载数据），识别结构特征。

**输入**：Excel 文件路径 + sheet 名
**输出**：结构检测结果 `ExcelStructure`

```python
@dataclass
class ExcelStructure:
    merged_columns: set[int]       # 有垂直合并的列索引（0-indexed）
    header_rows: int               # 表头行数（1 = 普通，2-3 = 多级）
    hidden_rows: set[int]          # 隐藏行索引
    hidden_cols: set[int]          # 隐藏列索引
    summary_rows: set[int]         # 疑似汇总行索引（含"合计""小计"等）
```

**实现要点**（Phase 0 Benchmark 结论：**放弃 openpyxl，改用 regex 解析 ZIP 内 XML**）：

openpyxl `read_only=False` 实测 5.8MB Excel 占 360MB 内存（60x 放大），不可接受。
lxml iterparse 内存仅 0.5MB 但耗时 13.6s（遍历全部 row XML 节点），不可接受。
**最终方案**：直接读取 xlsx ZIP 内的 `xl/worksheets/sheetN.xml`，用 regex 提取元数据。

- 实测：5.8MB Excel → 109ms / 86MB（= XML 解压大小，与 calamine DataFrame 量级相当）
- 安全阀：解压后 XML > 500MB 时跳过结构检测，降级到现有逻辑
- 不依赖 openpyxl 读取数据（仅用 `zipfile` + `re` 标准库）

**检测项**：

| 检测项 | 解析方式 | 说明 |
|--------|---------|------|
| 合并区域 | `<mergeCell ref="A2:A5"/>` regex | 只关注垂直合并（max_row > min_row） |
| 隐藏行 | `<row hidden="1" r="20"/>` regex | 扫描 row 标签的 hidden + r 属性 |
| 隐藏列 | `<col hidden="1" min="3" max="3"/>` regex | 扫描 col 标签 |
| 自动筛选 | `<autoFilter` 存在性检测 | 有筛选时在报告里标注"数据包含全部行，非筛选结果" |
| 公式无缓存值 | calamine `data_only` 模式已处理 | 检测失败时 Layer 3 标记为警告 |

**性能**（Phase 0 实测，regex 方案）：
- 5.8MB 文件：109ms / 86MB 内存
- 50MB 文件预估：~500ms / ~400MB 内存（与 calamine DataFrame 量级相当）
- 安全阀：XML > 500MB 时跳过检测

### Layer 2: 智能清洗

**职责**：基于 Layer 1 的检测结果，对 DataFrame 做精确清洗。

**规则**（标记优先于删除）：

| 检测结果 | 清洗动作 | 策略 |
|---------|---------|------|
| 垂直合并列 | 只对这些列做 `ffill()`，其他列不动 | 修复 |
| 多级表头 | 将多行表头展平为单行（用 `_` 连接） | 修复 |
| 隐藏行 | 添加 `_is_hidden` 布尔列标记，**不删除** | 标记 |
| 隐藏列 | 添加 `_is_hidden` 布尔列标记，**不删除** | 标记 |
| 汇总行 | 添加 `_is_summary` 布尔列标记，**不删除** | 标记 |

**标记列说明**：
- `_is_hidden: bool`：隐藏行标记为 `True`，正常行为 `False`；隐藏列不加标记列（会增加列数），改为在 CleaningReport.warnings 中列出隐藏列名
- `_is_summary: bool`：疑似汇总行标记为 `True`
- 无隐藏行且无汇总行时，不添加标记列（零侵入）
- 探索报告提示 LLM："已标记 N 行隐藏行 / N 行汇总行，查询时建议 `WHERE _is_hidden = false AND _is_summary = false`"

**合并单元格 ffill 逻辑**：
```python
# openpyxl 1-indexed → pandas 0-indexed
for rng in ws.merged_cells.ranges:
    if rng.max_row > rng.min_row:  # 垂直合并
        for col in range(rng.min_col, rng.max_col + 1):
            pandas_col = col - 1
            if pandas_col < len(df.columns):
                df.iloc[:, pandas_col] = df.iloc[:, pandas_col].ffill()
```

**汇总行检测逻辑**（检测后标记，不删除）：
```python
# 检测第一列包含"合计""小计""总计"等关键词的行
SUMMARY_KEYWORDS = {"合计", "小计", "总计", "总数", "Total", "Sum", "Subtotal"}
summary_rows: set[int] = set()
for idx, val in df.iloc[:, 0].items():
    if isinstance(val, str) and any(kw in val for kw in SUMMARY_KEYWORDS):
        summary_rows.add(idx)

# 标记而非删除（行业标准：ETL 保留原始数据可追溯性）
if summary_rows:
    df["_is_summary"] = False
    df.loc[list(summary_rows), "_is_summary"] = True
```

**隐藏行标记逻辑**（标记而非删除）：
```python
if structure.hidden_rows:
    df["_is_hidden"] = False
    # hidden_rows 是 openpyxl 1-indexed，需转换为 pandas index
    pandas_hidden = [r - structure.header_rows - 1 for r in structure.hidden_rows
                     if r > structure.header_rows]
    df.loc[df.index.isin(pandas_hidden), "_is_hidden"] = True
```

**边界场景**：
- 合并区域值为空：ffill(NaN) = NaN，不填，正确
- 水平合并（跨列）：calamine 已处理，跳过
- 表头在合并区域内：header_row 之前的行已被 pandas 跳过
- openpyxl 未安装：整个 Layer 1+2 跳过，降级到现有逻辑
- 无隐藏行且无汇总行：不添加 `_is_hidden` / `_is_summary` 列，零侵入
- "合计器"等误匹配：标记模式下不会丢数据，LLM 可自行判断是否排除

### Layer 3: 质量校验

**职责**：在写 Parquet 前校验数据质量，标记问题。

**校验规则**：

| 检查项 | 动作 |
|--------|------|
| 全 NaN 列 | 删除（已有逻辑，移到这里统一处理）|
| 全 NaN 行 | 删除 |
| 列名重复 | 加后缀 `_1` `_2` 去重 |
| 空 DataFrame | 返回错误提示 |
| 整数列因 NaN 变 float64 | 全整数的 float 列转回 nullable Int64（防止订单号 `123` 变 `123.0`） |
| 前导零丢失 | ~~标记到清洗报告~~ → **不可检测**（calamine 读出时已丢失前导零，无法区分"001"和"1"） |
| 自动筛选状态 | 标记到清洗报告（提示"数据包含全部行，非筛选结果"） |
| 隐藏列 | 标记到清洗报告 warnings（列出隐藏列名，提示 LLM 注意） |

**清洗报告**：
```python
@dataclass
class CleaningReport:
    merged_cols_filled: int        # ffill 了多少列
    hidden_rows_marked: int        # 标记了多少隐藏行（_is_hidden=True）
    hidden_cols_names: list[str]   # 隐藏列名列表（不删除，仅报告）
    summary_rows_marked: int       # 标记了多少汇总行（_is_summary=True）
    empty_cols_removed: int        # 删了多少空列
    empty_rows_removed: int        # 删了多少空行
    int_cols_fixed: int            # float→int 修复了多少列
    has_auto_filter: bool          # 是否有自动筛选
    warnings: list[str]           # 无法自动修复的问题（前导零丢失、隐藏列等）
    original_shape: tuple          # 清洗前的行列数
    final_shape: tuple             # 清洗后的行列数
```

**报告持久化**：
清洗报告写入 Parquet 同目录的 `.meta.json` 文件（与 snapshot 模式一致），`data_query_executor.py` 探索模式读取。`ensure_parquet_cache` 接口不变。

```python
# 写入示例
meta_path = cache_path.replace(".parquet", ".meta.json")
Path(meta_path).write_text(json.dumps(asdict(report), ensure_ascii=False))
```

**报告注入 LLM 上下文**：
```
[数据清洗] 合并单元格已填充（3列）| 标记隐藏行（5行）| 标记汇总行（2行）| 整数修复（2列）
清洗前: 150行×12列 → 清洗后: 150行×12列（+2标记列）
⚠ 隐藏列: ["辅助列", "备注"]（数据保留，建议按需排除）
⚠ 建议查询时加: WHERE _is_hidden = false AND _is_summary = false
注意: 数据包含自动筛选，已读取全部行（非筛选结果）
```

## 三、改动范围

### 新增文件

| 文件 | 职责 |
|------|------|
| `excel_cleaner.py` | 三层清洗独立模块：`clean_excel()` 入口 + `ExcelStructure` + `CleaningReport` |

### 修改文件

| 文件 | 改动 |
|------|------|
| `data_query_cache.py` | `_convert_excel_to_parquet` 和 `_convert_all_sheets_to_parquet` 调用 `clean_excel()` + 写入 `.meta.json` |
| `data_query_executor.py` | 探索模式读 `.meta.json` 注入清洗报告到 LLM 上下文 |

### 不改的文件

- `file_executor.py`（file_read 不走 Parquet 转换）
- `file_metadata_extractor.py`（元数据提取独立于数据清洗）
- `tool_executor.py`（消费 Parquet，不关心清洗过程）
- CSV/Parquet 文件链路（不存在合并单元格）

### 函数调用顺序

**`_convert_excel_to_parquet`**（单 Sheet）：
```python
def _convert_excel_to_parquet(excel_path, cache_path, sheet, ...):
    # 1. 快速读取数据（calamine）
    df = pd.read_excel(xl, sheet_name=target_sheet, header=header_row)
    xl.close()

    # 2. ★ 三层清洗（调用独立模块）
    from services.agent.excel_cleaner import clean_excel
    df, report = clean_excel(df, excel_path, sheet_name, header_row)

    # 3. 类型推断（在清洗后做，NaN 已修复，准确率更高）
    _coerce_object_columns(df)

    # 4. 写入 Parquet + 清洗报告
    df.to_parquet(tmp_path, index=False, engine="pyarrow")
    _write_cleaning_report(cache_path, report)
```

**`_convert_all_sheets_to_parquet`**（多 Sheet 合并）：
```python
for name in sheet_names:
    df = pd.read_excel(xl, sheet_name=name, header=header_row)
    # ★ 每个 Sheet 独立清洗（共用同一入口）
    df, report = clean_excel(df, excel_path, name, header_row)
    _coerce_object_columns(df)
    df.insert(0, "_sheet", name)
    frames.append(df)
    reports.append(report)
# 合并后写入 Parquet + 汇总清洗报告
```

**`clean_excel` 入口签名**：
```python
def clean_excel(
    df: pd.DataFrame,
    excel_path: str,
    sheet_name: str,
    header_row: int = 0,
) -> tuple[pd.DataFrame, CleaningReport]:
    """三层清洗入口。openpyxl 不可用时降级（只做 Layer 3）。"""
```

## 四、测试计划

### 测试用例

| 场景 | 输入 | 期望输出 |
|------|------|---------|
| 普通表格（无合并） | 标准 Excel | 不做任何清洗，行为不变 |
| 垂直合并单元格 | 订单号合并 3 行 | 合并列 ffill，其他列不动 |
| 多级表头（2 行） | 大类+小类 表头 | 展平为 "大类_小类" |
| 隐藏行 | 中间隐藏 5 行 | `_is_hidden=True` 标记 5 行，数据保留 |
| 隐藏列 | 中间隐藏 2 列 | 数据保留，warnings 列出隐藏列名 |
| 汇总行 | "合计" 行在末尾 | `_is_summary=True` 标记，数据保留 |
| 无隐藏无汇总 | 普通表格 | 不添加 `_is_hidden` / `_is_summary` 列（零侵入） |
| 整数列含 NaN | 订单号列有空行 | float64 → nullable Int64，不出现 `123.0` |
| 自动筛选状态 | 有 Filter 的 Excel | 读全部行，报告标注"非筛选结果" |
| 前导零列 | 邮编 `001234` | ~~标记警告~~ → 不可检测（calamine 读出即丢失），跳过 |
| 混合场景 | 以上全部 | 全部处理 |
| openpyxl 不可用 | 卸载 openpyxl | 降级到现有逻辑，不报错 |
| 大文件性能 | 50MB Excel | 清洗耗时 < 1s |

### 验证方法

```bash
python -m pytest tests/test_data_query.py -k "merged_cell or multi_header or hidden_row or summary_row"
```

## 五、实施步骤

0. **Phase 0: 前置验证** ✅ 已完成
   - openpyxl `read_only=False` 实测 360MB/5.8MB 文件 → **放弃 openpyxl**
   - 选定 regex 解析 ZIP XML 方案（109ms / 86MB）
   - 创建 `excel_cleaner.py` 文件骨架 + `clean_excel()` 入口
1. **Phase 1**: Layer 1 结构检测 + Layer 2 合并单元格 ffill（最高频问题）
2. **Phase 2**: Layer 2 多级表头展平 + 隐藏行标记 `_is_hidden`
3. **Phase 3**: Layer 2 汇总行标记 `_is_summary` + Layer 3 质量校验 + `.meta.json` 清洗报告
4. **Phase 4**: `data_query_executor.py` 探索模式注入清洗报告 + 测试 + 部署

## 六、风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| ~~openpyxl 内存占用~~ | ~~360MB/5.8MB 文件~~ | ✅ 已放弃 openpyxl，改用 regex 解析 ZIP XML（86MB/109ms） |
| regex 解析大文件内存 | 50MB Excel XML 解压后 ~400MB | 安全阀：XML > 500MB 跳过检测 + Parquet 缓存只清洗一次 |
| ffill 误填正常空值 | 数据错误 | 只对有合并标记的列 ffill，其他列不动 |
| 汇总行误标记 | LLM 可能误排除正常数据行 | 标记而非删除，误标记不丢数据；LLM 可看到原始值自行判断 |
| 多级表头展平后列名过长 | 可读性差 | 截断到 50 字符 |
| _convert_all_sheets_to_parquet 也需要清洗 | 遗漏 | `clean_excel()` 统一入口，两个转换函数共用 |
| 前导零无法恢复 | 数据精度丢失 | calamine 读出时已丢失，代码层面不可检测，已从实现范围移除 |
| 公式单元格无缓存值 | 读到公式文本 | calamine data_only 模式已处理，极端情况标记警告 |
| 文件损坏（BadZipFile） | 解析直接失败 | 已有 try/except 兜底，返回友好错误 |
| 标记列增加 schema 复杂度 | Parquet 多 1-2 列 | 无隐藏/汇总时不添加（零侵入）；LLM 探索报告明确提示过滤条件 |

## 七、不处理的场景（已确认无需处理）

| 场景 | 原因 |
|------|------|
| CSV 文件 | 纯文本格式，不存在合并/隐藏/表头问题 |
| Parquet 文件 | 已是结构化数据 |
| PDF/DOCX/PPTX | 走 file_read 提取文字，不经过 Parquet 转换 |
| 水平合并（跨列） | calamine 引擎已自动处理 |
| VBA 宏 | 不执行宏，只读数据 |
| 嵌入图表/对象 | 不影响数据解析，忽略 |
| 条件格式 | 只影响显示，不影响数据值 |
| 单元格批注 | 不影响数据值，忽略 |
| 1900/1904 日期系统 | calamine 已自动处理 |
| 密码保护 | 只保护编辑，不影响读取 |

## 八、评审记录（2026-05-07）

### 评审角色
系统架构师 / 性能工程师 / 接手者视角 / 产品视角 + 行业标准专家（仲裁）

### v1 → v2 关键变更

| 变更项 | v1（原方案） | v2（评审后） | 依据 |
|--------|-------------|-------------|------|
| 汇总行处理 | 直接删除 | `_is_summary` 标记列 | ETL 行业标准（Deequ/Great Expectations：标记优先于删除） |
| 隐藏行处理 | 直接删除 | `_is_hidden` 标记列 | 同上，数据保留可追溯 |
| 隐藏列处理 | 直接删除 | 保留数据，CleaningReport.warnings 列出列名 | 列标记会增加列数不合适，改为报告 |
| 代码位置 | 内联到 `data_query_cache.py` | 独立 `excel_cleaner.py` | `data_query_cache.py` 已 491 行，内联超 500 行硬约束 |
| 清洗报告传递 | 未设计 | `.meta.json` 文件（与 Parquet 同目录） | 不改 `ensure_parquet_cache` 接口 |
| 多 Sheet 清洗 | 仅风险表提及 | `clean_excel()` 统一入口，两个函数共用 | 消除重复代码 |
| openpyxl 内存 | "~5MB 内存" | 需 Phase 0 实测，不达标切 lxml | openpyxl 官方文档：`read_only=False` 会全量加载 |
| 实施步骤 | 4 Phase | 5 Phase（新增 Phase 0 benchmark） | 内存风险需前置验证 |

### 达成共识
1. 改动点精准：Parquet 写入前是唯一修复点
2. 合并单元格 ffill 只对有合并标记的列做，安全
3. openpyxl 降级策略正确（不可用时跳过）
4. CleaningReport 对调试和 LLM 上下文都有价值
5. 无隐藏/汇总行时零侵入（不添加标记列）

### 风险接受
- openpyxl 二次读取带来 30-200ms 额外延迟 → 缓存机制确保只清洗一次
- 标记列增加 schema 复杂度 → 探索报告明确提示 WHERE 过滤条件
