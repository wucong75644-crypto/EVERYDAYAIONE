# 文件处理系统技术设计 v1.0

> 适用：EVERYDAYAIONE  
> 创建：2026-05-15  
> 状态：方案确认，待实施

---

## 一、定位与目标

### 系统定位

单用户单会话场景下的文件处理工具，服务于 AI 多步骤跨文件数据分析需求。

### 核心目标

把人类可读的文件翻译成 AI 可读的格式，原文件不修改。不同类型文件翻译目标不同：

| 文件类型 | 翻译目标 |
|---------|---------|
| 数据文件（Excel/CSV/TSV） | 结构化 Parquet，供 DuckDB SQL 查询 |
| 文档文件（PDF/DOCX/PPTX） | 提取纯文本 + 表格，供 AI 理解内容 |
| 直读文件（TXT/JSON/YAML/XML/代码/日志） | 天然可读，分页控制即可 |
| 图片（PNG/JPG/GIF/WEBP） | 多模态视觉模型直接分析 |

### 关键术语

- **读取（reading）**：标准化结构化，不改数据
- **修复（fixing）**：AI 辅助处理异常情况
- **确认（confirmation）**：兜底求助用户

---

## 二、文件类型分流策略

### 分流总览

```
                    扩展名判断
                        │
      ┌─────────────────┼─────────────────┬──────────────┐
      ↓                 ↓                 ↓              ↓
  数据文件           文档文件           直读文件         图片
 xlsx/csv/tsv     pdf/docx/pptx    txt/json/yaml    png/jpg
                                   xml/md/py/log    gif/webp
      │                 │                 │              │
      ↓                 ↓                 ↓              ↓
  三层清洗管道       文本提取管道       直接读取        视觉模型
  L1→L2→L3          L1→L2→L3        + 三级防线       CDN URL
      │                 │           + offset/limit    / base64
      ↓                 ↓                │              │
  Parquet           Markdown         带行号文本       FileReadResult
  + .meta.json      + .meta.json    直接进 context   注入多模态消息
      │                 │
      ↓                 ↓
  DuckDB SQL       直接进 context
```

### 统一位置标注规范

所有文件类型的 AI 视图都必须带位置标注，AI 才能精确引用和定位：

| 文件类型 | 位置坐标 | 格式示例 | AI 引用方式 |
|---------|---------|---------|------------|
| Excel/CSV | Row + Col（行号+列号） | Row 4, Col B | "B 列第 4 行的金额是空的" |
| PDF | Page + Table/Para 序号 | Page 3, Table 2 | "第 3 页第 2 个表格显示..." |
| DOCX | Para 序号 + Table 序号 | Para 5, Table 1 | "第 5 段提到了..." |
| PPTX | Slide + 元素序号 | Slide 3, Table 1 | "第 3 张幻灯片的表格中..." |
| TXT/代码/日志 | Line（行号） | Line 42 | "第 42 行有个 bug" |
| JSON/YAML/XML | Path（JSON Path / XPath） | `$.data[0].name` | "data 数组第 1 项的 name 字段..." |

> 原则：每一条数据都有"身份证"（坐标），处理后的内容能反向定位回原始文件。

### 类型一：数据文件（Excel / CSV / TSV）

**处理目标**：完整结构化转换，一行不能少，供后续 SQL 分析。

**处理方式**：完整的 L1→L2→L3 三层架构（本方案核心，详见第三~十二章）。

**位置标注**：schema 带列号（A/B/C），sample 带 Excel 原始行号（`_row`）。

**输出**：Parquet + 完整 .meta.json（schema / sample / stats / formulas / issues）。

**AI 使用方式**：沙盒中 DuckDB SQL 查询。

#### 多 Sheet 模式（已支持）

| 能力 | 实现方式 |
|------|---------|
| 扫描所有 Sheet 结构 | `scan_sheet_structures()` 返回每个 Sheet 的列名+行数 |
| 读指定 Sheet | `sheet="Sheet名"` + `fuzzy_match_sheet()` 模糊匹配 |
| 合并所有 Sheet | `sheet="*"` → `pd.concat` → 单 Parquet，首列 `_sheet` 标识来源 |

合并后的 Parquet 每行带 `_sheet` 列，AI 和用户都能区分数据来自哪个 Sheet：

```
_sheet    | order_id | amount | date
----------+----------+--------+-----------
1月销售   | TB001    | 299.00 | 2024-01-01
2月销售   | TB003    | 420.00 | 2024-02-01
```

#### 单 Sheet 多表格检测（新增）

**问题**：电商报表常见"一个 Sheet 里放多张表"，用空行分隔。当前代码只检测第一个表头，后续表格的表头被当成数据行，空行被删掉，数据全部混在一起。

**检测算法**：复用现有 `detect_header_row` 的判断逻辑（众数法 + 字符串占比 ≥ 70%），但**全扫而非找到第一个就返回**：

```python
@dataclass
class TableRegion:
    """单 Sheet 内一个表格区域"""
    name: str | None        # 表格名（从分隔行上方提取，如"退货表"）
    header_row: int         # 表头在 Excel 中的行号（1-indexed）
    data_start_row: int     # 数据起始行号
    data_end_row: int       # 数据结束行号（含）
    columns: list[str]      # 列名列表
    row_count: int          # 数据行数

def detect_table_regions(rows: list[list]) -> list[TableRegion]:
    """检测单 Sheet 内的多个表格区域。
    
    算法：
    1. 找所有"全空行"作为候选分隔符
    2. 在每个分隔区间内，用 detect_header_row 的逻辑找表头
    3. 表头上方如果有"单值行"（只有1个非空值），视为表格名称
    4. 只有一个区间 → 返回空列表（单表格，走现有逻辑不变）
    """
```

**检测流程示例**：

```
原始 Excel：
  Row 1:  订单表                       ← 单值行 → name="订单表"
  Row 2:  订单号 | 金额 | 日期          ← 字符串≥70% → 表头
  Row 3:  001   | 100  | 2024-01-01   ← 数据行
  Row 4:  002   | 200  | 2024-01-02   ← 数据行
  Row 5:  [全空]                       ← 分隔符
  Row 6:  退货表                       ← 单值行 → name="退货表"
  Row 7:  编号  | 金额  | 原因         ← 字符串≥70% → 表头
  Row 8:  R001  | 50   | 质量问题      ← 数据行
  Row 9:  R002  | 80   | 尺寸不合      ← 数据行

检测结果：
  Region 1: name="订单表", header=2, data=3~4, cols=["订单号","金额","日期"]
  Region 2: name="退货表", header=7, data=8~9, cols=["编号","金额","原因"]
```

**表格名称识别**：分隔空行上方或表头上方，如果有一行只有 1 个非空值，就是表格名称：

```python
def _extract_region_name(rows, region_start, header_row):
    """从表头上方找表格名称（单值行）"""
    for i in range(header_row - 1, region_start - 1, -1):
        non_null = [c for c in rows[i] if c is not None and str(c).strip()]
        if len(non_null) == 1:
            return str(non_null[0]).strip()  # "退货表"
    return None  # 没有名称行，用 Region_1 / Region_2 命名
```

**输出方式**：每个区域独立输出 Parquet + .meta.json：

```
staging/{conv_id}/
├── 销售报表__订单表.parquet        ← Region 1
├── 销售报表__订单表.meta.json
├── 销售报表__退货表.parquet        ← Region 2
├── 销售报表__退货表.meta.json
└── session_files.json             ← 标记来自同一文件
```

**AI 看到的文件视图**：

```
[文件已就绪] 销售报表.xlsx → 检测到 Sheet1 内有 2 个表格区域

── 表格1：订单表（Row 2-4, 2行数据）──
  A | 订单号 | string  | 空值: 0%
  B | 金额   | decimal | 范围: 100 ~ 200
  C | 日期   | date    | 2024-01-01 ~ 2024-01-02
  样本：
    Row 3: {订单号: "001", 金额: 100, 日期: "2024-01-01"}
  位置：staging/{conv_id}/销售报表__订单表.parquet

── 表格2：退货表（Row 7-9, 2行数据）──
  A | 编号 | string  | 空值: 0%
  B | 金额 | decimal | 范围: 50 ~ 80
  C | 原因 | string  | 空值: 0%
  样本：
    Row 8: {编号: "R001", 金额: 50, 原因: "质量问题"}
  位置：staging/{conv_id}/销售报表__退货表.parquet

⚠️ 两个表格来自同一个 Sheet，列"金额"同名但含义可能不同
```

**与现有代码的改动关系**：

| 改动点 | 文件 | 改法 |
|--------|------|------|
| `detect_header_row` | data_query_cache.py | **不改**——仍是单表头检测，被新函数复用 |
| `_remove_empty_rows_cols` | excel_cleaner.py | **不改**——每个 Region 独立清洗，内部没有分隔空行 |
| `clean_excel` | excel_cleaner.py | **不改**——每个 Region 的 DataFrame 独立传入 |
| `ensure_parquet_cache` | data_query_cache.py | **扩展**——读取前先调 `detect_table_regions`，多区域时分别处理 |
| 新增 `detect_table_regions` | data_query_cache.py | 新增函数 |
| 新增 `_extract_region_name` | data_query_cache.py | 新增函数 |

> 核心改动只在入口：`ensure_parquet_cache` 读取前先检测是否多区域，是就分别处理，不是就走现有逻辑。下游清洗、转换、缓存全部不变。

**边界情况处理**：

| 场景 | 处理方式 |
|------|---------|
| 只有一个表格区域 | `detect_table_regions` 返回空列表，走现有逻辑不变 |
| 空行是数据缺失不是分隔符 | 空行后面没有出现新的"表头行"（字符串占比 < 70%）→ 不分割 |
| 表格之间没有空行 | P0 不处理——靠 L2 AI 兜底 |
| 表格名称行不存在 | 用 `Region_1` / `Region_2` 自动命名 |
| 合并单元格跨越分隔区域 | `_detect_structure` 已有合并信息，检测到跨区域合并时不分割 |

### 类型二：文档文件（PDF / DOCX / PPTX）

**处理目标**：提取可读文本 + 表格，保留结构信息。

**现有基础**（`file_read_extensions.py`，已实现）：

| 格式 | 处理方法 | 依赖库 | 特性 |
|------|---------|--------|------|
| PDF | `_read_pdf()` | pdfplumber | 逐页提取文本+表格，≤100页自动全读，>100页需指定页码，自动检测扫描件 |
| DOCX | `_read_docx()` | python-docx | 保留标题层级（Heading 1/2/3），按文档顺序提取段落+表格，>10MB 拒绝 |
| PPTX | `_read_pptx()` | python-pptx | Slide 编号，标题/文本/表格分块，自动提取备注栏，>10MB 拒绝 |

**位置标注**：PDF 按 Page 编号，DOCX 按 Para/Table 序号，PPTX 按 Slide 编号。

**现有输出格式**（纯文本，直接进 context）：
```
文件: 报告.pdf | PDF 50页 | 读取: 1-5 | 表格: 3个
──────────────────────────────────────────────────
── Page 1 ──
[Para 1] 文本内容...
[Para 2] 文本内容...
=== Table 1 (3行 x 4列) ===
  Row 1: ['名称', '类型', '描述']
  Row 2: ['id', 'Integer', '主键']

── Page 2 ──
[Para 3] 文本内容...
```

**DOCX 输出格式**：
```
文件: 方案.docx | DOCX | 2.5MB | 段落: 45 | 表格: 3
──────────────────────────────────────────────────
[Para 1] [Title] 文档标题
[Para 2] [Heading 1] 第一章
[Para 3] [Normal] 正文段落内容...
[Para 4] [Normal] 正文段落内容...
=== Table 1 (5行 x 3列) ===
  Row 1: ['姓名', '部门', '职位']
  Row 2: ['张三', '销售部', '经理']
```

**PPTX 输出格式**：
```
文件: 演示.pptx | PPTX | 5.2MB | 幻灯片: 15 | 表格: 2
──────────────────────────────────────────────────
=== Slide 1 ===
  [Title] 演讲题目
  [Text 1] 主要内容...
  [Notes] 讲演备注...
=== Slide 2 ===
  [Title] 第二页标题
  === Table 1 (3行 x 4列) ===
    Row 1: ['指标', '2023', '2024', '增长率']
```

**当前缺失**：没有质检判定，没有 L2 降级。三层架构补充如下：

```
L1：代码自动提取（现有 _read_pdf/_read_docx/_read_pptx）
  ↓
质检判定（新增）
  ↓
L2：AI 沙盒用其他库重新提取（如 PDF 用 camelot 提表格）
  ↓
L3：告知用户（如"这是扫描件 PDF，无法提取文字"）
```

**文档文件的质检逻辑**：

```python
def assess_extraction_quality(file_size, extracted_text, page_count):
    """文档提取质量判定"""
    text_ratio = len(extracted_text) / file_size
    avg_chars_per_page = len(extracted_text) / max(page_count, 1)

    if text_ratio < 0.01:
        # 文件很大但几乎没提取到文字 → 很可能是扫描件
        return "fail", "scanned_document"
    elif avg_chars_per_page < 50:
        # 每页平均不到50字 → 提取可能不完整
        return "warning", "low_extraction"
    else:
        return "pass", None
```

**文档文件的 .meta.json**（比数据文件简单，但同样带位置标注）：

```json
{
  "version": "1.0",
  "status": "pass | warning | fail",
  "source_file": "报告.pdf",
  "processed_by": "L1",
  "file_type": "pdf",

  "summary": {
    "description": "50页 PDF，包含 12 个表格",
    "page_count": 50,
    "para_count": 120,
    "table_count": 12,
    "char_count": 25000,
    "has_images": true,
    "is_scanned": false
  },

  "structure": [
    {"type": "para",  "id": "Para 1",  "page": 1, "preview": "摘要：本报告分析了..."},
    {"type": "para",  "id": "Para 2",  "page": 1, "preview": "一、市场概况"},
    {"type": "table", "id": "Table 1", "page": 2, "rows": 5, "cols": 4, "header": ["指标","2023","2024","增长率"]},
    {"type": "para",  "id": "Para 15", "page": 3, "preview": "二、竞品分析"},
    {"type": "table", "id": "Table 2", "page": 3, "rows": 10, "cols": 3, "header": ["品牌","市场份额","排名"]}
  ],

  "extraction": {
    "text_ratio": 0.85,
    "tables_extracted": 12,
    "pages_with_text": 48,
    "pages_empty": 2
  },

  "issues": [
    {
      "type": "empty_page",
      "location": {"page": 23},
      "severity": "warning",
      "suggestion": "第23页可能是扫描图片，无法提取文字"
    },
    {
      "type": "table_incomplete",
      "location": {"page": 15, "table": "Table 8"},
      "severity": "warning",
      "suggestion": "Table 8 可能跨页，提取可能不完整"
    }
  ]
}
```

> **structure 字段**：文档的"目录"，AI 拿到后知道整个文档的结构布局（第几页有什么段落/表格），不需要读完全文就能精确引用。

**L2 修复场景**：

| 失败原因 | 降级方式 |
|---------|---------|
| 扫描件 PDF（text_ratio < 0.01） | 直接 L3 告知用户（等 DeepSeek V4 Vision API 开放后接入 OCR） |
| 表格跨页提取不完整 | L2：AI 自主决定用什么库/策略重新提取 |
| DOCX 复杂排版丢失结构 | L2：AI 自主决定修复方式 |
| PPTX 嵌入图表无法提取 | 直接 L3 告知用户"该页包含嵌入图表，建议导出为图片" |

> L2 不指定具体库名（如 camelot/mammoth），AI 根据错误信息自主选择工具和策略。

**AI 使用方式**：提取的文本直接进 LLM context，不走 Parquet/DuckDB。

### 类型三：直读文件（TXT / JSON / YAML / XML / 代码 / 日志）

**处理目标**：天然可读，不需要任何转换。

**位置标注**：`file_read` 现有输出已带行号（cat -n 风格，Line 1/2/3），天然满足。

**现有输出格式**（已带位置标注）：
```
文件: app.py | 120行 | 显示: 1-50
──────────────────────────────────────
    1	import pandas as pd
    2	from pathlib import Path
    3	
    4	def process_data(file_path):
    5	    """处理数据文件"""
   ...
   42	        raise ValueError("无效的日期格式")  ← AI 能说"第42行有问题"
```

**现有机制完整**（`file_executor.py` 的 `file_read()`）：
- 三级防线：256KB 字节限制 / 2000 行硬上限 / 25K tokens 估算
- AI 通过 `offset` + `limit` 按需分页
- UTF-8 失败自动降级 GBK
- BOM 自动剥离

**不需要三层清洗架构**。唯一的边界情况：

| 场景 | 处理方式 |
|------|---------|
| 小 JSON/YAML（< 256KB） | `file_read` 直接读给 AI（带行号） |
| 大 JSON/YAML（> 256KB，用于阅读理解） | AI 用 `offset/limit` 翻页（带行号） |
| 大 JSON/YAML（> 256KB，用于数据分析） | 应视为"数据文件"，沙盒中 `json.load()` → DuckDB 查询 |

### 类型四：图片（PNG / JPG / GIF / WEBP）

**处理目标**：多模态视觉模型直接分析。

**现有机制完整**（`file_read_extensions.py` 的 `_read_image()`）：
- 有 CDN → 返回 CDN URL
- 无 CDN 且 ≤ 2MB → base64 data URL
- 自动获取宽高（PIL）
- 返回 `FileReadResult(type="image", image_url=...)`

**不需要三层清洗架构**，不需要任何改动。

### 各类型处理优先级

| 优先级 | 文件类型 | 方案状态 | 实施阶段 |
|--------|---------|---------|---------|
| P0 | Excel / CSV / TSV | 完整设计（本方案核心） | Week 1-2 |
| P1 | PDF / DOCX / PPTX | 需要补充质检 + L2 降级 | Week 3-4 |
| P2 | 大 JSON 数据分析 | 按需，沙盒处理即可 | 按需 |
| — | TXT/代码/日志/图片 | 现有机制完整，不需要改 | 不做 |

---

## 三、整体架构（数据文件核心链路）

### 为什么要转 Parquet

不是搬运文件，是**翻译格式**。原始 Excel 对 AI 不友好：

| Excel（人类可读） | Parquet（机器可读） |
|------------------|-------------------|
| 合并单元格 | 每个单元格独立有值 |
| 多级表头 | 扁平化单行列名 |
| 隐藏行/列 | 显式标记或过滤 |
| 公式（=SUM(A1:A10)） | 只保留计算结果值 |
| 混合类型（一列既有数字又有文字） | 统一类型 |
| GBK 编码 | UTF-8 |

原文件不动，Parquet 是**副本**，放在 staging 里，24h 过期自动清理。转换一次，后续所有查询走 DuckDB SQL，毫秒级响应。

### 读取触发入口（两种场景）

```
场景A：用户拖拽上传 + 发消息（路径已知）
   前端上传后拿到 {relative_path, cdn_url, size}
   消息体附带文件引用 → AI 直接拿到路径
   ↓ 跳过 file_search
   直接进入 L1 读取管道

场景B：用户口头提到文件（路径未知）
   "帮我看看之前上传的销售报表"
   ↓
   AI 调 file_search 定位路径（纯路径解析，不触发处理）
   ↓
   拿到路径后进入 L1 读取管道

注意：工作区里已有的文件也在 NAS 上，不需要搬运，直接原地读取转换。
```

### 三层处理架构

```
┌──────────────────────────────────────────────────┐
│  入口                                             │
│  A. 消息附带文件引用 → 路径已知，直接进 L1          │
│  B. AI 调 file_search → 拿到路径，再进 L1          │
└──────────────────┬───────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────┐
│  缓存检查                                         │
│  staging/{conv_id}/{filename}.parquet 存在？       │
│  .snapshot 的 mtime+size 匹配？                   │
│  ├── 命中 → 直接返回文件视图，跳过清洗              │
│  └── 未命中 → 进入 L1                             │
└──────────────────┬───────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────┐
│  L1：后端确定性读取管道                             │
│  excel_cleaner.py 等，覆盖 90% 常规电商报表         │
│                                                   │
│  小文件（< 10万行）：                               │
│    全量读 → 全量清洗 → 输出 Parquet + .meta.json   │
│                                                   │
│  大文件（≥ 10万行）：                               │
│    分块读（5万行/块）→ 逐块清洗 → 逐块追加 Parquet  │
│    记录每块的清洗状态                               │
│                                                   │
│  质检 → status = pass / warning / fail             │
└──────────────────┬───────────────────────────────┘
                   │
            pass?  │  有失败（块）？
          ┌────────┤────────┐
          ↓                 ↓
   直接返回           ┌─────────────────────────────┐
   文件视图           │  L2：AI 沙盒静默修复（用户无感）│
                     │  - 后台自动执行，不通知用户     │
                     │  - 看原文件 + L1 失败原因      │
                     │  - 按 SKILL.md 写代码          │
                     │  - 小文件：全量重处理           │
                     │  - 大文件：只处理失败的块       │
                     │  - 输出与 L1 一致的格式        │
                     │                               │
                     │  成功 → 直接返回文件视图        │
                     │  失败（3次）↓                  │
                     └────────────┬──────────────────┘
                                  ↓
                     ┌─────────────────────────────┐
                     │  L3：告知用户                 │
                     │  返回具体问题 + 修复建议       │
                     │  仅此时用户才感知到处理失败    │
                     └─────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────┐
│  标准化数据层                                      │
│  Parquet + .meta.json                             │
│  L1/L2 输出格式完全一致，业务层无感知               │
└──────────────────┬───────────────────────────────┘
                   ↓
┌──────────────────────────────────────────────────┐
│  AI 业务层                                        │
│  沙盒中 DuckDB 查 Parquet                         │
│  跨文件 JOIN、多步骤分析、生成图表报表              │
└──────────────────────────────────────────────────┘
```

### 用户视角

整个 L1 → L2 → L3 对用户来说就是"AI 在思考"：

```
90% 的情况（L1 通过）：
  用户发消息 → "AI 正在分析..." → 拿到结果（不知道后面跑了什么）

9% 的情况（L1 失败，L2 修复成功）：
  用户发消息 → "AI 正在分析..."（稍慢一点）→ 拿到结果（仍然无感）

1% 的情况（L1+L2 都失败）：
  用户发消息 → AI 回复："这个文件前10行是合并单元格，
  我无法自动识别表头位置。您能告诉我数据从第几行开始吗？"
```

### 核心设计原则

- **目标不变，手段可变** —— L1 和 L2 输出格式完全一致
- **架构层 vs 业务层强边界** —— 读取在沙盒外，业务在沙盒内
- **确定性优先** —— 90% 走 L1，10% 走 L2
- **失败静默降级** —— L2 后台执行，成功了直接给结果，失败了才跟用户沟通
- **接口统一** —— 业务层不感知数据来自 L1 还是 L2
- **大文件只修失败块** —— 分块处理时 L2 不重新处理全部数据

---

## 四、读取时机：按需触发

### 触发模式

```
用户上传/工作区已有文件 → 文件已在 NAS 上（不需要搬运）
           ↓
用户发消息（附带文件引用或口头提到文件）
           ↓
AI 拿到文件路径（场景A直接拿到，场景B通过file_search查到）
           ↓
检查缓存 → 命中则跳过 → 未命中则触发 L1 读取（同步）
           ↓
读取完成后 AI 拿到文件视图，继续业务分析
```

### 不做异步预清洗的原因

1. 表格复杂，必须完整读取才能准确回答
2. 用户上传后才提问，时机重合
3. 用户感知是"AI 在思考"，与 Claude 体验一致
4. 大幅简化架构（无状态机、无任务队列、无完成通知）
5. 文件已在 NAS 上，不存在"搬运"开销，只是格式转换

---

## 五、缓存与存储

### 存储路径

NAS 挂载磁盘，两种用户路径：

```
企业用户：
{workspace_root}/org/{org_id}/{user_id}/
  ├── 原始文件（用户上传目录）
  ├── 下载/                        ← 沙盒输出目录 (OUTPUT_DIR)
  └── staging/{conv_id}/
      ├── {filename}.parquet       ← 读取后的结构化数据
      ├── {filename}.meta.json     ← 元数据 + 公式 + 摘要
      └── session_files.json       ← 多文件清单 + 关联提示

个人用户：
{workspace_root}/personal/{md5(user_id)[:8]}/
  ├── 原始文件
  ├── 下载/
  └── staging/{conv_id}/
      ├── {filename}.parquet
      ├── {filename}.meta.json
      └── session_files.json

OSS：
  ├── 原始文件备份
  └── 用户下载链接来源
```

> 路径解析统一由 `core/workspace.py` 的 `resolve_workspace_dir()` 和 `resolve_staging_dir()` 处理，已覆盖企业/个人双路径。

### 缓存 Key（复用现有机制）

文件定位和失效检测分离，与现有 `data_query_cache.py` 保持一致：

```python
# 文件名定位（用于磁盘上定位缓存文件）
path_hash = hashlib.md5(excel_path.encode()).hexdigest()[:8]
cache_name = f"_cache_{path_hash}_{safe_sheet}_{stem}.parquet"

# 失效检测（独立 .snapshot 文件，每次读取时比对）
snapshot_content = f"{st_mtime},{st_size}"
# 比对精度：abs(float(snap_mtime) - src_mtime) < 0.001
```

不用文件内容 SHA256，对单用户场景路径快照足够。

### 失效检测

每次访问缓存时检查：

1. Parquet 文件是否存在
2. `.snapshot` 中的 `mtime` 和 `size` 是否与原文件一致
3. 任一不一致 → 重新读取

---

## 六、缓存清理

### TTL 策略

- **时长**：24 小时（与现有 `staging_cleaner.py` 统一）
- **容量兜底**：目录 > 500MB 时从最旧文件开始删
- **受保护文件**：`.duckdb.db`、`.duckdb_temp/*`、`_bak_*`、`_manifest.json`

### 清理触发（复用现有机制）

```
1. 消息驱动（主）：每次消息处理开始时 fire-and-forget 调用 cleanup_staging()
2. 进程启动（兜底）：cleanup_all_staging() 扫描所有用户目录
```

与 `staging_cleaner.py` 现有逻辑完全一致，不新增触发方式。

### 清理粒度

按会话目录整体清理，不单独删某个文件。

### 清理边界

- **删除**：`staging/{conv_id}/` 下的 Parquet + meta + session_files.json
- **保留**：NAS 原文件、OSS 备份
- **重建**：用户再次访问 → 从原文件重新读取

---

## 七、.meta.json Schema（数据文件）

### 现有基础

当前 `excel_cleaner.py` 的 `CleaningReport` 已有 10 个字段：

```python
@dataclass
class CleaningReport:
    merged_cols_filled: int = 0       # 填充的合并单元格数
    hidden_rows_marked: int = 0       # 隐藏行数
    hidden_cols_names: list[str]      # 隐藏列名
    empty_cols_removed: int = 0       # 删除的空列数
    empty_rows_removed: int = 0       # 删除的空行数
    int_cols_fixed: int = 0           # 整数修复列数
    has_auto_filter: bool = False     # 是否有筛选器
    warnings: list[str]              # 警告列表
    original_shape: tuple[int, int]  # 原始行列数
    final_shape: tuple[int, int]     # 最终行列数
```

### 升级后完整结构

> ⚠️ 这是对 `.meta.json` 的**重写**，不是简单扩展。需要重建生成逻辑。

```json
{
  "version": "1.0",
  "status": "pass | warning | fail",
  "source_file": "原始文件相对路径",
  "processed_at": "2026-05-15T10:30:00",
  "last_accessed_at": "2026-05-15T14:20:00",
  "processed_by": "L1 | L2",

  "summary": {
    "description": "文件整体描述",
    "row_count": 5000,
    "col_count": 12,
    "sheet_count": 3
  },

  "schema": {
    "order_id":  {"col": "A", "col_index": 0, "type": "string",   "null_ratio": 0.0},
    "amount":    {"col": "B", "col_index": 1, "type": "decimal",  "null_ratio": 0.002, "min": 0, "max": 99999},
    "date":      {"col": "C", "col_index": 2, "type": "datetime", "range": ["2024-01-01", "2024-12-31"]},
    "platform":  {"col": "D", "col_index": 3, "type": "string",   "null_ratio": 0.0}
  },

  "sample": {
    "head": [
      {"_row": 2, "order_id": "TB2024010100001", "amount": 299.00, "date": "2024-01-01", "platform": "淘宝"},
      {"_row": 3, "order_id": "TB2024010100002", "amount": 158.50, "date": "2024-01-01", "platform": "京东"},
      {"_row": 4, "order_id": "TB2024010100003", "amount": null,   "date": "2024-01-02", "platform": "淘宝"},
      {"_row": 5, "order_id": "TB2024010100004", "amount": 420.00, "date": "2024-01-02", "platform": "拼多多"},
      {"_row": 6, "order_id": "TB2024010100005", "amount": 67.80,  "date": "2024-01-03", "platform": "京东"}
    ],
    "tail": [
      {"_row": 4998, "order_id": "TB2024123100496", "amount": 312.00, "date": "2024-12-31", "platform": "淘宝"},
      {"_row": 4999, "order_id": "TB2024123100497", "amount": 88.00,  "date": "2024-12-31", "platform": "京东"},
      {"_row": 5000, "order_id": "TB2024123100498", "amount": 189.00, "date": "2024-12-31", "platform": "淘宝"},
      {"_row": 5001, "order_id": "TB2024123100499", "amount": 450.00, "date": "2024-12-31", "platform": "拼多多"}
    ]
  },

  "stats": {
    "missing_values": 23,
    "duplicates": 5
  },

  "formulas": [
    {
      "cell": "Sheet1!B10",
      "formula": "=SUM(B2:B9)",
      "value": 5000
    }
  ],

  "issues": [
    {
      "type": "missing_value",
      "location": {"row": 4, "col": "B", "raw_col_name": "金额"},
      "severity": "warning",
      "count": 12,
      "suggestion": "建议填充均值"
    }
  ],

  "cleaning": {
    "header_row": 0,
    "data_start_row": 2,
    "row_offset": 1,
    "merged_cols_filled": 0,
    "hidden_rows_marked": 0,
    "hidden_cols_names": [],
    "empty_cols_removed": 0,
    "empty_rows_removed": 0,
    "int_cols_fixed": 0,
    "has_auto_filter": false,
    "warnings": [],
    "original_shape": [5200, 15],
    "final_shape": [5000, 12]
  },

  "confidence": 0.95
}
```

### 位置标注设计

**schema 带列位置**：每个字段标注 Excel 列号（A/B/C）和列索引（0/1/2），AI 回答时能精确说"B 列（金额）"。

**sample 带行号**：`_row` 是 Excel 原始行号（不是 Parquet 索引），AI 和用户说的"第几行"坐标系一致。

**行号映射关系**：

```
Excel 原始行号 = Parquet 行索引 + data_start_row

例：表头在第 1 行（header_row=0），数据从第 2 行开始（data_start_row=2）
  Parquet index 0 → Excel Row 2
  Parquet index 1 → Excel Row 3

例：前 10 行是合并单元格，表头在第 11 行（header_row=10），数据从第 12 行开始
  Parquet index 0 → Excel Row 12
  Parquet index 1 → Excel Row 13
```

映射参数存在 `cleaning.header_row` 和 `cleaning.data_start_row` 中，AI 查 DuckDB 时可以换算：

```sql
SELECT (row_number() OVER () + 1) as excel_row, order_id, amount
FROM 'staging/.../销售报表.parquet'
WHERE amount IS NULL
```

### AI context 中注入的文件视图格式

.meta.json 生成后，系统将其格式化为 AI 友好的文本注入 context（每轮对话都带着）：

```
[文件已就绪] 销售报表.xlsx → staging/{conv_id}/销售报表.parquet

字段 schema（12列）：
  A | order_id   | string   | 空值: 0%   | 示例: "TB2024010100001"
  B | amount     | decimal  | 空值: 0.2% | 范围: 0 ~ 99,999
  C | date       | datetime | 空值: 0%   | 2024-01-01 ~ 2024-12-31
  D | platform   | string   | 空值: 0%   | 枚举: 淘宝, 京东, 拼多多
  ...

样本数据（Excel 原始行号）：
  Row 2: {order_id: "TB2024010100001", amount: 299.00, date: "2024-01-01", platform: "淘宝"}
  Row 3: {order_id: "TB2024010100002", amount: 158.50, date: "2024-01-01", platform: "京东"}
  Row 4: {order_id: "TB2024010100003", amount: null,   date: "2024-01-02", platform: "淘宝"}  ← 缺失
  Row 5: {order_id: "TB2024010100004", amount: 420.00, date: "2024-01-02", platform: "拼多多"}
  Row 6: {order_id: "TB2024010100005", amount: 67.80,  date: "2024-01-03", platform: "京东"}
  ...
  Row 5001: {order_id: "TB2024123100499", amount: 450.00, date: "2024-12-31", platform: "拼多多"}

统计：缺失值 23（B列12个, G列11个） | 重复行 5
公式：Sheet1!B10 = SUM(B2:B9) → 5000（汇总行，非原始数据）
问题：[warning] Row 4 B列（金额）缺失，共12处

数据位置：staging/{conv_id}/销售报表.parquet
行号映射：Excel行号 = Parquet索引 + 2（表头占1行）
```

> AI 全程带着这个视图。写 SQL 时直接用列名和类型，回答用户时直接引用行号和列号。用户说"B 列第 500 行"，AI 能精确定位并查询。

### 新增字段与现有字段的关系

| 新增字段 | 来源 | 实现方式 |
|---------|------|---------|
| `version` / `status` / `processed_by` | 新增 | 流程控制字段 |
| `source_file` / `processed_at` / `last_accessed_at` | 新增 | 路径和时间戳 |
| `summary` | 新增 | 从 DataFrame 计算 shape + sheet_count |
| `schema` + `col` / `col_index` | 新增 | 从 DataFrame columns 顺序 + Excel 列号映射 |
| `sample` + `_row` | 新增 | head(5) / tail(5) + data_start_row 换算行号 |
| `stats` | 新增 | DataFrame isnull().sum() + duplicated().sum() |
| `formulas` | 新增 | openpyxl 双模式读取（见下节） |
| `issues` | 新增 | 清洗过程中收集，带 Excel 行列坐标 |
| `cleaning` + `header_row` / `data_start_row` | 扩展 | 新增行号映射参数，现有字段保留 |

### 公式提取

使用 openpyxl 双模式读取：

```python
# 模式1：拿公式字符串
wb_formula = openpyxl.load_workbook(path)
# 模式2：拿计算结果
wb_value = openpyxl.load_workbook(path, data_only=True)
# 两者合并存入 formulas 字段
```

---

## 八、多文件场景

### session_files.json（从零新建）

```json
{
  "files": [
    {
      "path": "staging/{conv_id}/sales.parquet",
      "columns": ["order_id", "amount", "date"],
      "row_count": 10000
    },
    {
      "path": "staging/{conv_id}/products.parquet",
      "columns": ["order_id", "product_name", "qty"],
      "row_count": 50000
    }
  ],
  "potential_relations": [
    {
      "files": ["sales", "products"],
      "common_columns": ["order_id"],
      "confidence": 0.9,
      "hint": "两文件可能通过 order_id 关联"
    }
  ]
}
```

### 关联识别策略

第一阶段：纯列名匹配（启发式提示）

```python
def detect_relations(files):
    relations = []
    for fa, fb in combinations(files, 2):
        common = set(fa.columns) & set(fb.columns)
        if common:
            relations.append({
                "files": [fa.name, fb.name],
                "common_columns": list(common),
                "confidence": len(common) / min(len(fa.columns), len(fb.columns))
            })
    return relations
```

后续可升级到数据采样匹配（看实际需求）。

### 维护机制

新文件加入时增量更新 `session_files.json`，不全量重建。

---

## 九、AI 看到的"文件视图"

### 设计理念：管道模式

升级前 AI 需要多轮工具调用（读文件 → 清洗 → 查结构 → 分析），每步折叠在 ToolStepCard 里。升级后 L1 管道静默执行，文件视图直接注入 AI context，AI 一步到位做业务分析。

```
升级前：5 轮工具调用（读+洗+查+算+画），前 3 轮浪费在"读文件"
升级后：管道静默完成读取，AI 直接拿到认知，1~2 轮工具调用做业务
```

### AI context 中注入的完整文件视图

.meta.json 格式化后注入 AI 每轮对话的 context（用户看不到这段，只有 AI 看到）：

```
[文件已就绪] 销售报表.xlsx → staging/{conv_id}/销售报表.parquet

字段 schema（12列）：
  A | order_id   | string   | 空值: 0%   | 示例: "TB2024010100001"
  B | amount     | decimal  | 空值: 0.2% | 范围: 0 ~ 99,999
  C | date       | datetime | 空值: 0%   | 2024-01-01 ~ 2024-12-31
  D | platform   | string   | 空值: 0%   | 枚举: 淘宝, 京东, 拼多多
  E | product_id | string   | 空值: 0%   | 示例: "SKU-A001"
  ...

样本数据（Excel 原始行号）：
  Row 2: {order_id: "TB2024010100001", amount: 299.00, date: "2024-01-01", platform: "淘宝"}
  Row 3: {order_id: "TB2024010100002", amount: 158.50, date: "2024-01-01", platform: "京东"}
  Row 4: {order_id: "TB2024010100003", amount: null,   date: "2024-01-02", platform: "淘宝"}  ← 缺失
  ...
  Row 5001: {order_id: "TB2024123100499", amount: 450.00, ...}

统计：缺失值 23（B列12个, G列11个） | 重复行 5
公式：Sheet1!B10 = SUM(B2:B9) → 5000（汇总行，非原始数据）
问题：[warning] Row 4 B列（金额）缺失，共12处

数据位置：staging/{conv_id}/销售报表.parquet
行号映射：Excel行号 = Parquet索引 + 2（表头占1行）

关联文件：
  商品清单.parquet — 共同列: product_id — 可通过 product_id JOIN
```

### AI 的认知状态

AI 带着文件视图，在整个对话过程中始终知道：

| AI 知道的 | 来源 | 怎么用 |
|-----------|------|--------|
| 列名 + 类型 + 列号（A/B/C） | schema | 写 SQL 时直接引用，回答时说"B 列（金额）" |
| 每列的值域范围 | schema.min/max/range | 判断异常值、写过滤条件 |
| 样本数据 + Excel 行号 | sample._row | 理解数据格式，回答"第 4 行金额为空" |
| 缺失值位置 | issues.location | 分析时避开或处理缺失 |
| 公式和汇总行 | formulas | 知道哪些行要排除 |
| 关联文件和 JOIN 列 | related_files | 跨表分析时知道怎么 JOIN |
| 行号映射（Parquet↔Excel） | cleaning.data_start_row | DuckDB 查到的结果换算回 Excel 行号 |

### 多轮对话示例

```
第一轮：用户问"帮我分析销售趋势"
  AI 看 context → 知道有 C列(date) 和 B列(amount)
  → 直接写 SQL: SELECT strftime(date,'%Y-%m'), SUM(amount) GROUP BY 1

第二轮：用户问"第4行为什么金额是空的"
  AI 看 context 样本 → Row 4: amount=null, 订单号 TB2024010100003
  → 直接回答，不需要重新查文件

第三轮：用户问"把淘宝的订单单独导出"
  AI 看 context → D列(platform) 有枚举值"淘宝"
  → 写 SQL: WHERE platform = '淘宝' → 导出 Excel 到 OUTPUT_DIR
```

### 信息策略

- **默认**：完整版（带 schema、stats、formulas、sample）
- **特殊**：极简版（极大文件时只给 summary + columns + sample）
- AI 需要细节数据时 → 沙盒中 DuckDB 直接查 Parquet

---

## 十、进度反馈

### 设计原则：静默优先

L1 → L2 的降级对用户完全透明。用户看到的始终是"AI 正在分析..."，不暴露内部处理细节。

### WebSocket 消息格式（对齐现有协议）

复用现有 `websocket_builders.py` 的统一消息外壳：

```json
{
  "type": "file_processing",
  "payload": {
    "stage": "reading | structuring | indexing | ready | failed",
    "file": "sales.xlsx",
    "progress": 0.6,
    "message": "正在结构化第二个 Sheet",
    "details": {}
  },
  "timestamp": 1747296000000,
  "task_id": "xxx",
  "conversation_id": "xxx",
  "message_id": "xxx"
}
```

### 实施要点

1. 在 `websocket_types.py` 的 `WsMessageType` 枚举中新增 `FILE_PROCESSING = "file_processing"`
2. 在 `websocket_builders.py` 中新增 `build_file_processing()` 构建函数
3. `stage` / `progress` / `message` 放在 `payload` 内
4. 必须带 `task_id` 和 `conversation_id`

### 阶段定义

用户可见的阶段（推送给前端）：

| stage | 含义 | 用户看到 |
|-------|------|---------|
| `reading` | 正在读取原文件 | "正在读取文件..." |
| `structuring` | 正在结构化数据 | "正在分析文件结构..." |
| `indexing` | 正在生成索引和摘要 | "正在生成摘要..." |
| `ready` | 处理完成可用 | 无提示，AI 直接开始回答 |
| `failed` | L1+L2 都失败 | AI 用自然语言告知问题 |

内部阶段（仅日志 + 埋点，不推送给前端）：

| 内部状态 | 含义 |
|---------|------|
| `l2_fixing` | L1 失败，L2 静默修复中 |
| `l2_retry` | L2 重试中（第 N 次） |

> L2 修复期间前端继续显示"正在分析..."，用户无感知。

### 前端展示

按 `stage` 展示对应图标/文案，`progress` 用于进度条。L2 修复不改变前端状态。

---

## 十一、错误处理与重试

### 结构化错误返回（内部数据结构，不直接展示给用户）

```json
{
  "status": "failed",
  "stage": "structuring",
  "error_type": "header_detection_failed",
  "details": "前 10 行都是合并单元格，无法识别表头位置",
  "raw_sample": ["前 20 行原始数据"],
  "failed_chunks": [3],
  "suggestions": [
    "尝试手动指定表头行号",
    "尝试跳过前 N 行",
    "请求用户确认表头位置"
  ],
  "retry_count": 1
}
```

### 重试规则

- 同一文件 L1 失败 → 静默触发 L2（用户无感）
- L2 自动调整策略重试
- 重试上限：3 次
- 全部失败 → L3：AI 用自然语言告知用户具体问题和建议
- 用户反馈后（如"表头在第11行"）→ AI 带新信息重新执行 L2

### 大文件分块失败处理

```
大文件分块处理时：

Chunk 1 (行 1-50000)      → L1 清洗 ✅ → 写入 Parquet
Chunk 2 (行 50001-100000)  → L1 清洗 ✅ → 追加 Parquet
Chunk 3 (行 100001-150000) → L1 清洗 ❌ → 记录失败
Chunk 4 (行 150001-200000) → L1 清洗 ✅ → 追加 Parquet
   ↓
L2 只处理 Chunk 3（不重新处理全部数据）：
   AI 看到的输入：
   {
     "failed_chunk": 3,
     "row_range": [100001, 150000],
     "error": "第 100005 行类型混乱",
     "raw_sample": [该块前20行原始数据]
   }
   ↓
   AI 修复 Chunk 3 → 追加到 Parquet
   ↓
最终 Parquet = Chunk1 + Chunk2 + 修复后Chunk3 + Chunk4
```

### AI 重试边界

- 能自动处理 → 自动处理（用户无感）
- 有歧义但能合理猜测 → 假设 + 说明（结果中注明）
- 完全无法判断 → 询问用户（L3）

---

## 十二、L2 AI 沙盒（复用现有沙盒机制）

### 沙盒环境

复用现有 `sandbox_worker.py` 的沙盒执行环境，不新建独立沙盒实例：

- **执行方式**：通过现有 `code_execute` 工具触发，AI 在同一个 Kernel 中执行修复代码
- **预装库**：openpyxl（3.1.5）、pandas（2.2.3）、pyarrow（23.0.1）、duckdb —— 均已在沙盒中预注入
- **权限**：沙盒已有路径白名单（workspace / staging / output / skills / temp）
- **网络**：沙盒已禁止网络访问

### Skills 文件升级（提示词精简）

升级后 `skills_dir` 只保留三个文件，职责从"教 AI 怎么读文件"变为"告诉 AI 数据怎么用、输出什么规范"：

```
backend/skills/
├── data-usage.md    ← 替代 excel.md：数据文件使用指南（始终注入）
├── doc-usage.md     ← 替代 pdf.md + docx.md：文档文件使用指南（始终注入）
└── file-fix.md      ← 新增：L2 修复输出规范（仅 L2 场景注入）
```

**设计原则**：SKILL 只约束"输出长什么样"，不约束"怎么到达那里"。AI 自己决定探索策略和修复方法。

#### data-usage.md（替代 excel.md）

```markdown
# 数据文件使用指南

## 数据格式
所有数据文件已预处理为 Parquet 格式，存储在 staging 目录下。
元数据在同名 .meta.json 中，包含 schema、sample、stats。

## 使用方式
用 DuckDB SQL 直接查询：
  duckdb.sql("SELECT * FROM 'staging/{conv_id}/{filename}.parquet' LIMIT 10")

## 输出规范
- 过渡数据（后续还要计算）→ 保存为 Parquet
- 最终文件（给用户看/下载）→ 导出为 Excel
- 生成的文件必须保存到 OUTPUT_DIR
- 大结果用文件导出，不要 print 全部数据
- 金额保留 2 位小数，日期用 YYYY-MM-DD 格式
- 图表必须有标题、轴标签、中文字体
```

#### doc-usage.md（替代 pdf.md + docx.md）

```markdown
# 文档文件使用指南

## 文档内容
PDF/DOCX/PPTX 已由系统提取为纯文本，直接在消息上下文中可见。
如需精确定位，参考 .meta.json 中的 page_count 和 issues。

## 输出规范
- 引用文档内容时标注页码/段落位置
- 过渡数据（表格需要计算分析）→ 保存为 Parquet 走数据分析流程
- 最终文件（给用户查看下载）→ 导出为 Excel
- 不要尝试自己用 pdfplumber/python-docx 重新读取（系统已提取）
```

#### file-fix.md（仅 L2 修复时注入）

```markdown
# 文件修复规范

## 输入（系统自动提供）
- 原始文件路径
- L1 失败原因 + 错误详情
- 失败区域的原始数据样本

## 输出规范（必须严格遵守）
- Parquet 文件：输出到指定的 staging 路径
- .meta.json：必须包含 version / status / summary / schema / sample / stats / issues / cleaning
- status 必须如实填写 pass / warning / fail
- 不能修改原文件
- 不能跳过数据行（宁可标记异常也不能丢数据）
- 不能联网

## 约束
- 探索策略由你自己判断，系统不指定具体步骤
- 根据错误信息决定需要观察多少原始数据
- 修复后验证输出数据的完整性
```

> **与 Claude Code 的对齐**：Claude Code 的工具定义只写输入输出 schema，不写实现步骤。AI 的能力在于根据错误信息自主判断修复策略，写死步骤反而限制了 AI 的灵活性。

### 启动时机

- 仅当 L1 `status = "fail"` 或 `status = "warning"` 时触发
- 不允许 AI 主动跳过 L1 直接进 L2
- L1 的失败原因和原始数据样本通过 `code_execute` 的输入参数传给 AI
- **系统按需提供原始数据样本**，不固定行数——小文件可能给全部，大文件只给失败块的数据
- L2 修复后的数据追加到已有 Parquet，不重新处理已成功的块

### 监控指标

- L1 vs L2 调用比例
- 目标 L2 占比 < 10%
- 超过阈值 → 告警 + 抽象案例到 L1

---

## 十三、多实例预留

### 当前架构

- **单实例**：`asyncio.Lock` 防并发（复用 `data_query_cache.py` 现有转换锁）
- **锁粒度**：file_path 级
- **LRU 上限**：100 个锁

### 预留接口

- `.meta.json` 增加 `instance_id` 字段（可选）
- 缓存 key 设计兼容分布式锁
- 未来切 Redis SETNX 时不破坏现有结构

---

## 十四、监控埋点

### 关键指标

| 指标 | 说明 |
|------|------|
| 处理成功率 | L1 通过率 / L2 通过率 / 总通过率 |
| 处理时长 | 各阶段耗时分布 |
| 缓存命中率 | 避免重复读取 |
| 失败原因分布 | 指导 L1 扩展 |
| L2 占比 | 超 10% 触发告警 |

### 接入方式（对齐项目现有体系）

| 指标类型 | 接入系统 | 说明 |
|---------|---------|------|
| 处理耗时、成功率、失败原因 | `tool_audit_log` 表（PostgreSQL） | 结构化查询，支持统计报表 |
| 处理链路追踪 | Langfuse span | 调试定位用，关联到对话级 trace |
| L2 占比告警 | 日志 logger.warning | 超阈值时 warning 级别输出 |

### 埋点位置

```python
# L1 完成时
await tool_audit.log(
    tool_name="file_process",
    action="L1_clean",
    status=meta["status"],
    duration_ms=elapsed,
    metadata={"file": filename, "issues_count": len(meta["issues"])}
)

# L2 完成时
await tool_audit.log(
    tool_name="file_process",
    action="L2_fix",
    status=meta["status"],
    duration_ms=elapsed,
    metadata={"file": filename, "retry_count": retry, "l1_error": l1_error_type}
)
```

---

## 十五、实施路径

### P0：核心读取链路（约 2 周）

#### Week 1：.meta.json 重写 + 公式提取

> `file_search` 已经是纯路径解析，不需要重构。

- `.meta.json` 生成逻辑重写（从现有 `CleaningReport` 的 10 字段升级到完整 schema）
  - `summary` 块：从 DataFrame 计算
  - `schema` 块：从 dtypes + describe() 推断
  - `sample` 块：head(5) / tail(5) 序列化
  - `stats` 块：null 统计 + 重复统计
  - `issues` 块：清洗过程中收集带坐标的问题
  - `status` 判定逻辑：pass / warning / fail
  - `cleaning` 块：保留现有 CleaningReport 字段
- 公式提取模块（openpyxl 双模式读取）
- WebSocket `FILE_PROCESSING` 消息类型注册

#### Week 2：多表格检测 + 多文件 + 进度

- 单 Sheet 多表格检测（`detect_table_regions` + `_extract_region_name`）
- `ensure_parquet_cache` 扩展：多区域时分别输出独立 Parquet
- `session_files.json` 多文件清单（从零新建，含同源多表格关联）
- 关联识别（列名匹配 + 同源文件标记）
- 进度推送集成（`build_file_processing()` 构建函数）
- `data-usage.md` + `doc-usage.md` 替代现有 skills 文件
- 缓存失效检测确认（现有 `mtime + size` 快照机制已满足，无需改动）

### P1：数据文件质量增强（约 2 周）

#### Week 3-4

- L2 AI 辅助修复（`file-fix.md` SKILL + 复用现有沙盒）
- L3 用户确认通知
- AI 重试机制（3 次上限）
- 大文件分块失败 → L2 只修失败块
- 监控埋点（`tool_audit_log` + Langfuse span）

### P2：文档文件质检 + L2 降级（约 1 周）

- PDF / DOCX / PPTX 提取质量判定（text_ratio / avg_chars_per_page）
- 文档文件 .meta.json 生成（summary / extraction / issues）
- L2 降级：AI 自主选择工具重新提取
- L3 降级：扫描件 PDF 告知用户（等 OCR/Vision API 接入）
- 重新启用 `staging_cleaner.py`（`session_files.json` 加入保护列表）

### P3：按需推进

- 关联识别升级到数据采样匹配
- 极简版/完整版自动切换
- 多实例分布式锁
- 大 JSON/YAML 数据分析场景支持

---

## 十六、不做项（明确砍掉）

| 砍掉项 | 原因 |
|--------|------|
| ~~上传时异步预清洗~~ | 改为按需读取 |
| ~~文件状态机~~ | 同步读取无需状态 |
| ~~完成通知 WebSocket~~ | 改为进度推送 |
| ~~内容 SHA256 指纹~~ | 路径 + mtime + size 快照足够 |
| ~~processed/ 全局目录~~ | staging/{conv_id}/ 即可 |
| ~~分片存储~~ | 文件 < 100MB 无需 |
| ~~跨用户去重~~ | 单用户场景不需要 |
| ~~跨会话共享~~ | 会话即生命周期 |
| ~~Celery~~ | 复用现有 BackgroundTaskWorker |
| ~~中文件走 Redis~~ | 文件数据走磁盘 |
| ~~file_search 重构~~ | 已经是纯路径解析 |
| ~~mtime 快照升级加 size~~ | 现有快照已包含 size |

---

## 十七、与现有系统的关系

### 复用项

| 现有组件 | 复用方式 |
|---------|---------|
| `excel_cleaner.py`（清洗逻辑） | 保留清洗函数，扩展 `.meta.json` 生成 |
| `ensure_parquet_cache()`（流式分块读取） | 保留大文件分块逻辑 |
| `data_query_cache.py`（缓存 key + 快照校验） | 原样复用，不改缓存机制 |
| DuckDB 沙盒预注入 | AI 业务层直查 Parquet |
| `tool_result_envelope.py`（小数据 in-context） | 小结果直接进 LLM context |
| workspace + OSS 双备份 | 上传链路不变 |
| `asyncio.Lock` 并发防护 | 转换锁机制不变 |
| WebSocket 通道 + 消息外壳 | 新增 `FILE_PROCESSING` 类型 |
| `staging_cleaner.py`（清理机制） | TTL 24h + 500MB 容量兜底不变 |
| `tool_audit_log` 表 | 埋点数据持久化 |
| Langfuse | 链路追踪 |
| 沙盒 `sandbox_worker.py` | L2 修复复用现有执行环境 |
| `skills_dir` 目录挂载机制 | 目录级只读挂载不变，替换内部 .md 文件 |

### 弃用项

| 弃用组件 | 替代 |
|---------|------|
| `skills/excel.md` | `skills/data-usage.md`（不再教 AI 怎么读文件，改为输出规范） |
| `skills/pdf.md` + `skills/docx.md` | `skills/doc-usage.md`（合并精简） |

### 新增项

| 新增组件 | 说明 | 阶段 |
|---------|------|------|
| `.meta.json` 完整生成逻辑（数据文件） | 从 CleaningReport 10 字段升级到完整 schema（含位置标注） | P0 |
| 公式提取模块 | openpyxl 双模式读取 | P0 |
| `detect_table_regions` + `_extract_region_name` | 单 Sheet 多表格检测 + 名称识别 + 分区域输出 | P0 |
| `session_files.json` 管理 | 多文件清单 + 关联识别 + 同源多表格标记 | P0 |
| `WsMessageType.FILE_PROCESSING` | WebSocket 进度消息类型 | P0 |
| `build_file_processing()` | 进度消息构建函数 | P0 |
| `data-usage.md` | 替代 excel.md：数据文件使用+输出规范 | P0 |
| `doc-usage.md` | 替代 pdf.md + docx.md：文档文件使用+输出规范 | P0 |
| `file-fix.md` SKILL 文档 | L2 修复输出规范（只约束输出，不约束步骤） | P1 |
| L3 用户确认通知 | 结构化错误返回 + 修复建议 | P1 |
| `tool_audit_log` 文件处理埋点 | L1/L2 成功率、耗时、失败原因 | P1 |
| 文档提取质检逻辑 | PDF/DOCX/PPTX 的 text_ratio / avg_chars_per_page 判定 | P2 |
| `.meta.json` 生成逻辑（文档文件） | summary / structure / extraction / issues | P2 |
| 文档 L2 降级 | AI 自主选择工具重新提取 | P2 |

---

## 十八、完整运行流程示例

### 场景：用户上传 `销售报表.xlsx`，问"帮我分析这个月的销售趋势"

```
┌─ 阶段一：文件上传（现有链路，不变）──────────────────────────┐
│                                                             │
│  用户拖拽文件到聊天框                                         │
│  ↓                                                          │
│  POST /files/workspace/upload                               │
│  ↓                                                          │
│  后端：校验 → 流式写入 NAS → 备份 OSS → 返回路径+CDN         │
│  ↓                                                          │
│  前端：展示"文件已上传"卡片                                   │
│  ↓                                                          │
│  ⚠️ 到此为止，没有做任何读取或清洗。文件原封不动躺在 NAS 上。  │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─ 阶段二：用户发消息，触发读取 ──────────────────────────────┐
│                                                             │
│  用户发送："帮我分析这个月的销售趋势"                          │
│  消息体附带文件引用 {relative_path, cdn_url, size}           │
│  ↓                                                          │
│  ChatHandler.start() → AI 直接拿到文件路径（场景A，无需查）  │
│  ↓                                                          │
│  缓存检查：staging/{conv_id}/销售报表.parquet 存在吗？       │
│  ├── 存在 + 快照匹配 → 直接返回文件视图，跳到阶段四          │
│  └── 不存在 → 进入 L1 读取                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─ 阶段三：L1 确定性管道（后台执行，用户看到"AI正在分析..."）──┐
│                                                             │
│  Step 1: 读取原文件                                          │
│  ├── detect_file_type() → "excel"                           │
│  ├── openpyxl 双模式：拿公式字符串 + 拿计算结果              │
│  └── WebSocket: {"stage": "reading", "progress": 0.1}      │
│                                                             │
│  Step 2: 结构化清洗（excel_cleaner.py）                      │
│  ├── 自动识别表头 → 多级表头展平 → 列名去重                  │
│  ├── 删空行空列 → 类型强转 → 整数修复                        │
│  ├── 收集 issues（带行列坐标）                               │
│  └── WebSocket: {"stage": "structuring", "progress": 0.5}  │
│                                                             │
│  Step 3: 输出标准化数据                                      │
│  ├── DataFrame → staging/{conv_id}/销售报表.parquet          │
│  ├── 生成 .meta.json（summary/schema/sample/stats/issues）  │
│  ├── 更新 session_files.json                                │
│  └── WebSocket: {"stage": "ready", "progress": 1.0}        │
│                                                             │
│  质检结果：                                                  │
│  ├── status = "pass" → 直接进入阶段四（90%的情况）           │
│  └── status = "fail" → 静默进入 L2（用户无感）               │
│                                                             │
│  L2 静默修复（如果需要）：                                    │
│  ├── AI 在现有沙盒中执行修复代码                              │
│  ├── 小文件：全量重处理                                      │
│  ├── 大文件：只处理失败的块                                  │
│  ├── 成功 → 进入阶段四（用户不知道跑了 L2）                  │
│  └── 3次失败 → L3：AI 用自然语言告知用户问题和建议           │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─ 阶段四：AI 业务层（拿到干净数据后）─────────────────────────┐
│                                                             │
│  AI 收到文件视图：                                           │
│  {                                                          │
│    summary: "销售报表，5000行×12列，2024-01至2024-12",       │
│    schema: {order_id: string, amount: decimal, date: ...},  │
│    sample: {head: [...], tail: [...]},                      │
│    data_location: "staging/{conv_id}/销售报表.parquet"       │
│  }                                                          │
│  ↓                                                          │
│  AI 知道数据结构，不需要自己写清洗代码                        │
│  ↓                                                          │
│  调用 code_execute 在沙盒中执行业务分析：                     │
│                                                             │
│    import duckdb                                            │
│    df = duckdb.sql("""                                      │
│        SELECT strftime(date, '%Y-%m') as month,             │
│               SUM(amount) as total_sales                    │
│        FROM 'staging/{conv_id}/销售报表.parquet'             │
│        WHERE date >= '2024-05-01'                           │
│        GROUP BY month ORDER BY month                        │
│    """).to_df()                                             │
│    plt.plot(df['month'], df['total_sales'])                 │
│    plt.savefig('output/销售趋势.png')                       │
│  ↓                                                          │
│  auto_upload 检测到新文件 → OSS CDN → 前端展示图表           │
│  ↓                                                          │
│  用户看到销售趋势分析结果                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 多文件跨表分析场景

```
用户再上传 商品清单.xlsx → 同样走 L1 读取管道
   ↓
session_files.json 增量更新：
   files: [销售报表.parquet, 商品清单.parquet]
   potential_relations: [{common_columns: ["product_id"], hint: "可通过 product_id JOIN"}]
   ↓
AI 在沙盒中执行跨文件 JOIN：
   SELECT p.product_name, SUM(s.amount)
   FROM '销售报表.parquet' s
   JOIN '商品清单.parquet' p ON s.product_id = p.product_id
   GROUP BY p.product_name ORDER BY 2 DESC
```

### 缓存命中场景

```
同一对话中再次用到 销售报表.xlsx
   ↓
检查 staging/{conv_id}/销售报表.parquet → 存在
   ↓
比对 .snapshot：mtime 和 size 与原文件一致
   ↓
缓存命中 → 跳过整个 L1 管道 → 直接返回文件视图
```

---

## 十九、设计哲学

### 与 ERP 系统的同构

| 文件处理工具 | ERP / 多 Agent 系统 |
|-------------|-------------------|
| 三层 fallback 清洗 | 三层 fallback 参数处理 |
| 统一 meta + issues + location | 统一错误 schema + recovery |
| 三层存储分级 | in-context / NAS / OSS |
| L1 质检前置判断 | capability-probing |
| 按需读取 + 缓存 | ERP Agent 数据访问模式 |

### 核心信念

```
确定性的事交给代码
不确定性的事交给 AI
两者边界不能模糊

目标不变，手段可变
L1 失败有 L2 兜底
L2 失败有 L3 兜底
永远不假装成功

按需读取
按需缓存
按需清理
让用户感受不到系统在工作
```
