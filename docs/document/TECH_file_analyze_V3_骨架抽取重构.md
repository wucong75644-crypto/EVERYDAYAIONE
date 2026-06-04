# TECH_file_analyze_V3：骨架抽取 + AI 一次裁决

| 元信息 | |
|---|---|
| 版本 | V3.0（继承 V2.2，破坏性变更） |
| 日期 | 2026-06-04 |
| 状态 | 待审 → 待实施 |
| 类型 | A 级架构决策 |
| 影响文件 | 5 个核心 + 7 个测试 |
| 预估周期 | 4.5 天，5 阶段 |

---

## 0. 一句话决策

**扫描器只抓"位置 + 原始值"，AI 看纯净证据一次裁决全部业务语义；空字段全链路不渲染。**

---

## 1. 核心流程（V3 的三步）

```
┌────────────────────────────────────────────────────────────────┐
│ Step 1：扫描表格（代码 / 确定性算法）                            │
│                                                                │
│   - PathA/B/C/D 路由（按文件规模）                              │
│   - 流式读取 + 采样 head/mid/tail                               │
│   - _classify_cell 给每个 cell 打类型标签（统计用途）            │
│   - _looks_like_header 找候选表头位置                           │
│   - _detect_structure 抓合并/隐藏/autofilter                    │
│   - extract_formulas 抓公式 cell                                │
│                                                                │
│   ⚠ 不识别业务关键词，不做"这是不是事实表"的判断                  │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│ Step 2：抓 15 类骨架位置 + 异常位置（装进 EvidencePool）         │
│                                                                │
│  ✅ 抓到 → 写入对应字段                                          │
│  ❌ 没抓到 → 字段缺省，全链路不渲染                              │
│                                                                │
│   骨架（垂直方向，单 sheet 内）:                                 │
│   ① 标题/标语行       ② 单位说明行       ③ 表头行（可能多级）    │
│   ④ 数据正文区        ⑤ 中间分组小计    ⑥ 中间异常行            │
│   ⑦ 末尾汇总行        ⑧ 末尾备注/版权                          │
│                                                                │
│   结构覆盖层（横切）:                                            │
│   ⑨ 合并单元格区     ⑩ 隐藏行 / 隐藏列                          │
│   ⑪ autofilter 区   ⑫ 公式 cell 位置                          │
│                                                                │
│   跨 sheet:                                                    │
│   ⑬ 多 sheet（含空 sheet 标记）                                 │
│   ⑭ 单 sheet 内多区域（PathC，罕见）                            │
│   ⑮ 父子层级缩进树（罕见但常被忽略）                            │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│ Step 3：稀疏 prompt → AI 一次裁决                               │
│                                                                │
│  build_prompt 原则：                                            │
│   - 只渲染有信号的位置（if evidence.X: ...）                    │
│   - 没抓到的位置一行都不出现在 prompt                           │
│   - AI 看原始值 + 类型分布，自己判断业务语义                     │
│                                                                │
│  AI 输出 AIDecision：                                          │
│   - header_row / data_start_row                               │
│   - column_semantics（含业务名、type、is_id、is_order_level） │
│   - summary_rows / note_rows / unit_rows                      │
│   - merged_cell_actions / mixed_type_handling                 │
│   - regions / sheets                                          │
│   - data_quality_notes                                        │
│   - 🆕 table_role（fact/dimension/log/wide/snapshot/unknown）  │
│   - 🆕 table_role_note                                         │
│   - overall_summary                                            │
└────────────────────────────────────────────────────────────────┘
                              ↓
              落 meta.json（稀疏序列化）
              渲染 xml_view（稀疏渲染）给主 Agent
```

### 1.1 分工对照

| Step | 谁做 | 做什么 | 严禁做什么 |
|---|---|---|---|
| 1 | 代码 | 路由 + 流式扫描 | 不判业务 |
| 2 | 代码 | 抓 15 类位置 + 原始采样 + 类型分布 | 不判业务 |
| 3 | AI | 看原始数据 → 输出所有业务语义 | 不预判，证据驱动 |

### 1.2 为什么这样设计

- **Step 1-2 是工程问题**（怎么高效抓位置）→ 用规则、确定性算法
- **Step 3 是业务问题**（这列是不是货币、这行是不是 summary）→ 用 LLM
- 两边解耦：LLM 升级不用改扫描器；扫描器加新位置不用改 LLM
- 对照行业：DAIL-SQL / CodeS skeleton-then-judge + 稀疏 prompt 模式

---

## 2. 决策背景

### 2.1 触发事件

2026-06-04 用户上传店铺映射表（108 行 × 3 列）做 JOIN 分析，AI 写 SQL 时把维度列错挂到事实表别名。深挖发现根因链：

1. file_view 的 `<usage_hints>` 是**空标签**
2. 因为 [_render_usage_hints](backend/services/agent/file_xml_renderer.py#L221) 5 个 if 全 False
3. 因为 `meta.grain = None`
4. 因为 [_detect_grain Step 3](backend/services/agent/file_meta/builders.py#L262) 要求"至少 1 个数值列是订单级"，维度表全字符串列直接 return None
5. 真正根因：[_GROUP_KEY_HINTS 正则](backend/services/agent/file_meta/builders.py#L184) 是**电商关键词硬编码** `(订单|编号|单号|发票号|ID|order|invoice|bill|no)`，非电商场景全失效

### 2.2 系统性反模式审计（同性质问题 5 处）

| # | 位置 | 反模式 | 受影响场景 |
|---|---|---|---|
| 1 | builders.py:184 `_GROUP_KEY_HINTS` | 业务关键词正则 | 非电商业务全 miss |
| 2 | file_scanners.py:55 `SUMMARY_KEYWORDS` | 闭集 6 词 | 财务"累计"、营销"环比"全漏 |
| 3 | file_scanners.py:59 `_RE_CURRENCY_PREFIX` | `^[¥$￥]` | €/£/₩/₹/₽ 跨境漏 |
| 4 | file_scanners.py:60 `_RE_UNIT_NUMBER` | `[A-Za-z一-鿿]` | ℃/㎡/μ 漏 |
| 5 | file_scanners.py:71 `_is_known_id_format` | UUID/ObjectId/ASIN 闭集 | 业务 ID 漏 |

**共性**：扫描器越界做业务识别 → 关键词闭集永远补不完 → 非命中场景下游失明。

### 2.3 行业对照

| 方案 | 上传 Excel 后做法 |
|---|---|
| OpenAI Code Interpreter | 不预提取，AI 多轮 `df.head()` 探查 |
| Snowflake Cortex Analyst | 不处理上传，人工 YAML 配置 fact/dim/metric |
| Databricks AI/BI Genie | 同上 + Unity Catalog 元数据 |
| Tableau Data Interpreter | 识别骨架（标题/表头/合计），规则启发式 |
| DAIL-SQL / CodeS（学术 SOTA）| condensed schema + skeleton → LLM 一次裁决 |

**V3 定位**：Tableau 骨架抽取 + DAIL-SQL 一次裁决的组合。两段都有成熟先例。

---

## 3. 详细改动清单

### 3.1 删除（业务预判层 ~280 行）

| 文件 | 删除内容 |
|---|---|
| file_scanners.py:54 | `SUMMARY_KEYWORDS` 常量 + 引用 |
| file_scanners.py:59-60 | `_RE_CURRENCY_PREFIX` / `_RE_UNIT_NUMBER` + 调用 |
| file_scanners.py:62-71 | `_RE_UUID` / `_RE_OBJECTID` / `_RE_ASIN` / `_RE_HEX_ID` / `_is_known_id_format` |
| file_evidence.py:55-56 | `ColumnEvidence.has_unit_suffix_candidates` / `.has_currency_prefix` 字段 |
| file_evidence.py:30 | `SuspiciousRow.reason="keyword_match"` 来源代码 |
| file_meta/builders.py:182-279 | `_GROUP_KEY_HINTS` 正则 + `_detect_grain` 整个函数 |
| data_query_cache.py:1635,1813 | `meta.grain = _grain` 写入点 |
| file_meta/dataclass.py | `FileMeta.grain` 字段 |
| file_xml_renderer.py | `_render_grain` 函数 |
| file_xml_renderer.py:221 | `_render_usage_hints` 中 grain 依赖分支 |
| file_meta/view.py:73-310 | markdown 渲染中 grain 依赖（旧路径）|

### 3.2 新增（B 类 3 类骨架抽取）

| 位置 | 实现思路 | 工作量 |
|---|---|---|
| ② 单位说明行 | 表头下一行 if `非空 cell ≤ 2 个 且 含"(单位"/"(单元"/"(币种"等模式` → 标 unit_row 候选 | ~30 行 |
| ⑤ 中间分组小计 | 数据区内 if `非数据行模式 + 关键值是数值 + null_ratio 0.3-0.7` → 标 subtotal 候选 | ~50 行 |
| ⑮ 父子层级缩进树 | 检测"层级编号"（1, 1.1, 1.1.1）或"前导空格缩进"模式 | ~40 行 |

**注**：B 类抽取**只标位置 + 候选 reason，不下业务判断**。AI 在 Step 3 看原始值决定到底是不是。

### 3.3 新增（AIDecision 字段）

| 文件 | 新增字段 |
|---|---|
| file_ai_decision.py | `table_role: str = "unknown"` + `table_role_note: str = ""` |

### 3.4 改造（稀疏渲染）

| 文件 | 改造 |
|---|---|
| file_ai_prompt.py | 所有 section 加 `if evidence.X:` 条件渲染（部分已做）|
| file_xml_renderer.py | 13 个 `<section>` 全部条件渲染，**不写空标签** |
| file_meta/builders.py | `asdict` 后递归过滤 `None / [] / {} / ""` |
| file_ai_decision.py | 序列化跳过空 list / 空 dict / unknown 默认值 |

### 3.5 保留（结构骨架层，原样不动）

| 字段/函数 | 用途 |
|---|---|
| `_classify_cell` | 5 类 type 分布统计，不参与业务判断 |
| `_looks_like_header` / `detect_header_row` | 表头位置兜底 |
| PathA/B/C/D `make_scanner` | 算法路由 |
| `_detect_structure`（合并/隐藏/autofilter）| 结构提取 |
| `extract_formulas` | 公式位置 |
| `EvidencePool` 大部分字段 | 仅删 `has_currency_prefix` / `has_unit_suffix_candidates` |
| `AIDecision` 所有现有字段 | 仅新增 `table_role` / `table_role_note` |
| 缓存编排 `data_query_cache.ensure_parquet_cache` | 仅去掉 `meta.grain = ...` |

---

## 4. AI Prompt 改进（[file_ai_prompt.py](backend/services/agent/file_ai_prompt.py)）

### 4.1 删除业务预判提示

**V2.2 现在**：
```
列 H: 原始表头='销售金额', 类型分布={"decimal": 1100, "empty": 71}, null率=6.06% ⚠️ 含货币前缀
```

**V3**：
```
列 H: 原始表头='销售金额', 类型分布={"decimal": 1100, "empty": 71}, null率=6.06%
  样本: ['¥99.50', '¥120.00', '¥45.30', ...]
```

### 4.2 删除可疑行 keyword_match 提示

**V2.2**：
```
Row 1171: reason=keyword_match, null率=83%, 关键词=["合计"]
```

**V3**：
```
Row 1171: null率=83%, 原始值=["合计", "", "", "", "", "", "", "", "", "99999.99"]
```

### 4.3 新增引导

`JSON_SCHEMA_TEMPLATE` 增加：
```json
"table_role": "fact | dimension | log | wide | snapshot | unknown",
"table_role_note": "<一句话理由>"
```

### 4.4 稀疏渲染示例

evidence 没抓到的字段**完全省略**：

```python
# ❌ V2.2 现在
parts.append(f"- 合并单元格: {len(evidence.merged_ranges)} 个\n")  # 即使 0 个也写
parts.append(f"- 隐藏列: {evidence.hidden_cols}\n")               # 即使 [] 也写
parts.append(f"- 公式: {evidence.formula_total_count} 个\n")     # 即使 0 也写

# ✅ V3
if evidence.merged_ranges:
    parts.append(f"- 合并单元格: {len(evidence.merged_ranges)} 个\n")
if evidence.hidden_cols:
    parts.append(f"- 隐藏列: {evidence.hidden_cols}\n")
if evidence.formula_total_count:
    parts.append(f"- 公式: {evidence.formula_total_count} 个\n")
# 全空 → 整个章节都不输出
```

---

## 5. 输出契约

### 5.1 AIDecision（向后兼容 + 新增 2 字段）

```python
@dataclass
class AIDecision:
    # ── V2.2 现有字段全部保留 ──
    header_row: int = 1
    data_start_row: int = 2
    header_type: str = "single"
    column_semantics: list[ColumnSemantic] = field(default_factory=list)
    summary_rows: list[int] = field(default_factory=list)
    unit_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    merged_cell_actions: list[MergedCellAction] = field(default_factory=list)
    mixed_type_handling: list[MixedTypeAction] = field(default_factory=list)
    preserve_empty_rows: list[EmptyRowDecision] = field(default_factory=list)
    regions: list[RegionDecision] = field(default_factory=list)
    sheets: list[SheetDecision] = field(default_factory=list)
    data_quality_notes: list[DataQualityNote] = field(default_factory=list)
    overall_summary: str = ""

    # ── V3 新增 ──
    table_role: str = "unknown"          # fact / dimension / log / wide / snapshot / unknown
    table_role_note: str = ""            # 一句话理由
```

### 5.2 EvidencePool（删 2 字段）

```python
@dataclass
class ColumnEvidence:
    col_letter: str
    raw_header: str
    sample_values: list[Any] = field(default_factory=list)
    classified_dist: dict[str, int] = field(default_factory=dict)
    null_ratio: float = 0.0
    is_long_id_candidate: bool = False
    # ❌ 删 has_unit_suffix_candidates
    # ❌ 删 has_currency_prefix
```

### 5.3 FileMeta（删 1 字段）

```python
@dataclass
class FileMeta:
    # ... 其他字段保留 ...
    # ❌ 删 grain: dict | None
```

---

## 6. 稀疏渲染原则（贯穿全链路）

### 6.1 原则

> **没抓到 → 字段不存在 → prompt/xml/json 全链路都不输出**

不出现：`<merged_ranges></merged_ranges>`、`"summary_rows": []`、`hidden_cols: []` 这种空容器。

### 6.2 实施层

| 层 | 实施位置 | 做法 |
|---|---|---|
| AI prompt | [file_ai_prompt.py:build_prompt](backend/services/agent/file_ai_prompt.py#L81) | 每个 section 加 `if evidence.X:` |
| xml_view | [file_xml_renderer.py:_render_*](backend/services/agent/file_xml_renderer.py) | 13 个 section 全部条件渲染 |
| meta.json | [file_meta/builders.py:build_meta](backend/services/agent/file_meta/builders.py) | `asdict` 后递归过滤 falsy 值 |
| AIDecision 反序列化 | [file_ai_decision.py:asdict_sparse](backend/services/agent/file_ai_decision.py) | 跳过 unknown / 空容器 / "" |

### 6.3 Token 节省估算

| 场景 | V2.2 prompt | V3 sparse | 节省 |
|---|---|---|---|
| 店铺映射表（108×3，无公式无合并）| ~4500 字符 | ~2800 字符 | **~38%** |
| 销售明细表（500k×23，结构密）| ~14000 字符 | ~13200 字符 | ~6% |
| 简单 CSV（3 列 100 行）| ~3000 字符 | ~1500 字符 | **~50%** |

### 6.4 验收指标

- 店铺映射表 xml_view 字节数对比 V2.2 应下降 ≥ 30%
- prompt 中**不出现** `[]` / `{}` / `null` 这种空容器字面量

---

## 7. 测试改造

| 测试文件 | 改动 |
|---|---|
| test_file_evidence.py | 删 `has_currency_prefix` / `has_unit_suffix_candidates` 用例（~5 个）|
| test_file_scanners.py | 删 SUMMARY_KEYWORDS / 货币 / 单位正则用例（~10 个）|
| test_file_meta.py | 删 grain 用例 + 新增"无 grain 文件正常构建 FileMeta" |
| test_file_ai_decision.py | 新增 `table_role` 字段 + `_asdict_sparse` 序列化测试 |
| test_file_ai_judge.py | 新增 prompt 不含 `keyword_match` 快照测试 |
| test_file_ai_prompt.py | 新增稀疏渲染测试（空字段不出现）|
| test_file_xml_renderer.py | 删 `_render_grain` 测试 + 新增 `<usage_hints>` 按 `table_role` 分支测试 |
| test_file_analyze_integration.py | 维度表 e2e（店铺映射表）+ 单位说明行 e2e + 分组小计 e2e |

**预估**：~20 个旧测试删，~15 个新测试加。

---

## 8. 阶段化实施（5 阶段 / 4.5 天）

| 阶段 | 内容 | 文件 | 周期 | 风险 |
|---|---|---|---|---|
| **P1** | 删 `_RE_CURRENCY_PREFIX` / `_RE_UNIT_NUMBER` / `_is_known_id_format` / `has_*` 字段 | file_scanners.py + file_evidence.py + prompt | 半天 | 低 |
| **P2** | 删 `SUMMARY_KEYWORDS` + suspicious_row.reason="keyword_match" + prompt 改原始值 | file_scanners.py + file_ai_prompt.py | 1 天 | 中 |
| **P3** | 删 `_detect_grain` + `_GROUP_KEY_HINTS` + `meta.grain` + 渲染层简化 | file_meta/* + file_xml_renderer.py | 1 天 | 中-高 |
| **P4** | 新增 `table_role` + 渲染按角色分支 + 全链路稀疏渲染 | file_ai_decision.py + file_ai_prompt.py + file_xml_renderer.py | 1 天 | 低 |
| **P5** | 新增 B 类 3 类骨架抓取（单位说明行 / 分组小计 / 父子层级） | file_scanners.py + file_evidence.py | 1 天 | 中 |

每阶段独立 commit + 独立可 revert。

### 8.1 灰度策略

- 不做 feature flag（prompt 重塑无法 A/B）
- 本地完成 P1 → P5 + 全部测试通过 → 一次部署
- 缓存版本 `_CACHE_SCHEMA_VERSION` bump 到 v3.0 → 旧 staging 自动重算
- 回滚 = `git revert` 单个 commit + 手动清 staging

---

## 9. 影响范围

### 9.1 直接消费方

| 模块 | V2.2 用 grain | V3 处理 |
|---|---|---|
| `_render_usage_hints` | order_level/group_key | 改按 `table_role` 分支 |
| `_render_grain` | 整个章节 | 删除 |
| `_render_column_schema` | order_level="true" 标签 | 改读 AIDecision.column_semantics[i].is_order_level |
| `view.py`（旧 markdown）| grain 字段 | 同步简化 |

### 9.2 间接消费方

| 模块 | 影响 |
|---|---|
| `file_cleaning_strategy.py` | 不依赖 grain，无影响 |
| `excel_cleaner/*` | 不依赖 grain，无影响 |
| `data_query_cache.py` 缓存 | bump 缓存 version 强制重算 |

### 9.3 缓存版本

`_CACHE_SCHEMA_VERSION = "v2.2"` → `"v3.0"`。旧 meta.json 失效自动重算。

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| AI 看 sample 漏识别 ¥ → 列被当 string | 低 | 中 | column_semantics 仍有 has_currency 字段，由 AI 输出 |
| AI 漏判 summary_rows → SUM 虚高 | 中 | 高 | prompt 强化"看末尾 N 行原始值判断"提示 |
| 大宽表（>100 列）prompt 爆炸 | 中 | 中 | 已有 simplified variant，V3.1 加列分批裁决 |
| 维度表 AI 误判 table_role | 低 | 低 | 默认 unknown 兜底渲染，不比现状空标签糟 |
| 旧缓存 meta.json 读取异常 | 高 | 低 | bump 缓存版本强制重算 |
| 父子层级抽取误判 | 中 | 低 | 仅作为候选，AI 看采样决定 |

### 10.1 V3 不解决（留 V3.1+）

- 大宽表（>200 列）分批裁决
- Excel 嵌入图片 / 批注 / 条件格式 提取
- 多 Agent 协作探查文件

---

## 11. 验收标准

### 11.1 必须通过

- [ ] 店铺映射表（108×3）→ `<usage_hints>` 非空，AI 输出 `table_role="dimension"` + 含 JOIN 提示
- [ ] 销售明细表（500k×23）→ usage_hints 不丢失"订单级 SUM 必须 DISTINCT"信号
- [ ] 学生成绩表（虚构）→ 不再因 `_GROUP_KEY_HINTS` 不命中而 fail
- [ ] 跨境业务表（含 €/£）→ AI 在 column_semantics 标 has_currency=true
- [ ] 含单位说明行的表 → AI 输出 unit_rows 包含正确行号
- [ ] 含分组小计的表 → AI 输出 summary_rows 包含中间小计行
- [ ] AI prompt 中**不再出现** `keyword_match` / `has_currency_prefix` 字样
- [ ] AI prompt 中**不再出现** 空容器字面量（`[]` / `{}` 单独成段）
- [ ] AIDecision 序列化输出包含 `table_role` 字段
- [ ] 现有 file_analyze 测试（SKIP_LLM_INTEGRATION=1）全部通过

### 11.2 量化指标（生产 1 周后观察）

- 店铺映射表 xml_view 字节数 ≤ V2.2 的 70%
- file_analyze 平均耗时 ≤ V2.2
- file_analyze 失败率 ≤ V2.2
- AI attempts=1 成功率 ≥ V2.2

---

## 12. 与 V2.2 已修复项的关系

V3 建立在 [commit 500f304](commit-500f304) 两个修复之上：

- ✅ schema 校验放宽 `business_name=""`（V2.2 修复）→ V3 保留
- ✅ AsyncOpenAI `max_retries=0`（V2.2 修复）→ V3 保留

V3 不回滚 V2.2 任何修复。

---

## 13. 决策对照表（一页纸）

| 项 | V2.2 现在 | V3 改完 |
|---|---|---|
| 业务关键词识别 | 5 处硬编码正则 | **全删** |
| `_detect_grain` | 电商加权 + 必须有数值订单级 | **删** |
| `<grain>` 章节 | 输出 | 删 |
| 维度表 usage_hints | 空标签 | 按 `table_role` 分支输出 |
| AI prompt 列证据 | 含 `⚠️ 含货币前缀` 等 flag | 仅原始 sample |
| AI prompt 可疑行 | `reason=keyword_match` | 原始值 + 统计 |
| AIDecision 新字段 | — | `table_role` + `table_role_note` |
| 缓存版本 | v2.2 | v3.0（强制重算）|
| 骨架抽取覆盖 | 5 类（隐式）| **15 类（显式）**|
| 稀疏渲染 | prompt 部分做、xml/json 没做 | **全链路** |
| 业务场景覆盖 | 仅电商订单 | 任意业务（学籍/医疗/财务/科研/...）|
| 行业模式参照 | 自创"规则+LLM 混合" | DAIL-SQL skeleton + 一次裁决 |

---

## 14. 待审决策点（请逐项确认）

1. **整体方向**：删业务关键词层、回归"骨架 + 原始样本 + AI 单次裁决" — **✅ / ❌**
2. **加 `table_role` + `table_role_note` 字段** — **✅ / ❌**
3. **B 类 3 个新位置全做（② 单位说明行 / ⑤ 中间分组小计 / ⑮ 父子层级）** — **✅ / ❌**
4. **全链路稀疏渲染原则** — **✅ / ❌**
5. **5 阶段拆分实施，每阶段独立 commit** — **✅ / 一把梭**
6. **缓存版本 v2.2 → v3.0 强制重算** — **✅ / ❌**

---

## 15. 不在本 ADR 范围（明确边界）

- 大宽表（>200 列）分批裁决 → V3.1
- Excel 图片 / 批注 / 条件格式 提取 → V3.2+
- 多 Agent 协作探查文件 → 长期方向无 timeline
- CSV 编码兜底 → 已在 V2.2 完成
