# file_read 统一文件读取工具

> 版本：v1.0 | 日期：2026-05-11 | 状态：方案确认，进入实施
> 前身：`TECH_data_query工具设计.md`（data_query 合并入 file_read）

## 一、背景

### 当前问题

1. **工具分裂**：`data_query`（Excel/CSV/Parquet）和 `file_read`（PDF/DOCX/PPTX/图片/文本）两个工具，AI 需要判断该调哪个
2. **Excel 公式丢失**：无条件扁平化为 Parquet，公式、单元格位置、跨 Sheet 引用全丢
3. **DOCX 结构丢失**：只提取纯文本，丢失标题层级和表格结构
4. **ffill 制造假数据**：合并单元格向下填充，AI 分不清原始值和填充值

### 改动

1. 合并 `data_query` 到 `file_read`——一个工具读所有文件
2. Excel 结构化读取——保留公式+编号+空值跳过，不做 ffill
3. DOCX 结构化标注——[Heading]/[Normal] + 表格行号
4. SQL 查询保留——传 sql 走 DuckDB，和之前一样

## 二、工具定义

```python
{
    "name": "file_read",
    "description": (
        "读取 workspace 内的任何文件。\n\n"
        "支持所有格式：\n"
        "- Excel/CSV/Parquet：返回结构化内容（含公式、单元格位置），"
        "传 sql 可执行 DuckDB SQL 查询\n"
        "- PDF：提取文本，用 pages 指定页范围\n"
        "- DOCX/PPTX：结构化读取（标题、段落、表格带行号）\n"
        "- 图片（png/jpg/gif/webp）：返回图片供视觉分析\n"
        "- 纯文本（txt/md/json/py 等）：返回内容\n\n"
        "不适用：\n"
        "- 计算/生成文件 → code_execute\n"
        "- 查 ERP 业务数据 → erp_agent"
    ),
    "parameters": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "文件名或相对路径"},
            "sql": {"type": "string", "description": "SQL 查询（仅 Excel/CSV/Parquet），表名用 FROM data，中文列名用双引号"},
            "sheet": {"type": "string", "description": "Excel Sheet 名称（可选），传 '*' 合并所有同结构 Sheet"},
            "pages": {"type": "string", "description": "PDF 页码范围（如 '1-5'），仅 PDF 文件"},
            "offset": {"type": "integer", "description": "起始行号，仅文本文件过大时使用"},
            "limit": {"type": "integer", "description": "读取行数上限，仅文本文件过大时使用"},
        },
    },
}
```

## 三、内部路由

```
file_read(path, sql, sheet, pages, offset, limit)
  │
  ├─ Excel
  │   ├─ 无 sql → openpyxl 结构化读取（新增 excel_reader.py）
  │   └─ 有 sql → Parquet + DuckDB（DataQueryExecutor）
  │
  ├─ CSV / Parquet
  │   ├─ 无 sql → DuckDB profile
  │   └─ 有 sql → DuckDB 查询
  │
  ├─ PDF → pdfplumber（现有）
  ├─ DOCX → python-docx 结构化标注（改造）
  ├─ PPTX → python-pptx 结构化标注（改造）
  ├─ 图片 → PIL + CDN/base64（现有）
  └─ 文本 → raw text（现有）
```

`file_tool_mixin._file_dispatch` 中的数据文件分支：

```python
# 检测文件类型（用 DataQueryExecutor 的检测，不用 FileExecutor 的）
from services.agent.data_query_cache import detect_file_type
file_type = detect_file_type(abs_path)

if file_type == "excel" and not args.get("sql"):
    # Excel 结构化读取（公式+编号）
    from services.agent.excel_reader import read_excel_structured
    return await read_excel_structured(abs_path, args.get("sheet"), self._staging_dir)

if file_type in ("excel", "csv", "parquet"):
    # SQL 查询 或 CSV/Parquet profile — 路径解析走 DataQueryExecutor（它支持 staging）
    executor = DataQueryExecutor(
        user_id=self.user_id, org_id=self.org_id,
        conversation_id=self.conversation_id,
        workspace_root=settings.file_workspace_root,
    )
    result = await executor.execute(file=args["path"], sql=args.get("sql"), sheet=args.get("sheet"))
    if executor.last_file_meta:
        self._pending_schemas.append(executor.last_file_meta)
    self._register_staging_files(result)
    return result
```

注意：数据文件分支的路径解析和安全校验走 DataQueryExecutor 内部（支持 staging 目录），不走 FileExecutor.resolve_safe_path（它禁止 staging 访问）。

## 四、输出格式

### Excel 结构化读取

```
=== Sheet: 义乌部门公摊 ===
行数: 33, 列数: 12

['D1:公摊费用', 'E1:订单数', 'F1:公摊值']
['A2:义乌部门', 'B2:义乌租金', 'C2:76800', 'D2:[公式]=C2/12']
['B3:基本工资', 'D3:68609']
['D10:[公式]=SUM(D2:D9)', 'E10:45338', 'F10:[公式]=SUM(D2:D9)/E10']
['A28:淘宝', 'B28:[公式]=G28/$E$10', 'D28:[公式]=$C$28*B28', 'G28:26818']

公式统计: 28个公式单元格
跨Sheet引用: D9 → 金华、义乌公摊值!E38

[Sheet 概览] 共 3 个 Sheet
后续查询: file_read(path="公摊表.xlsx", sql="SELECT ... FROM data")
```

规则：
- 每个值带编号（`A2:义乌部门`）
- 空单元格跳过
- 公式标 `[公式]`
- 不做 ffill

### 大数据 Excel（>1万行）

前5行 + 最后5行 + 列名 + 公式（如果有）。后续用 sql 查。

### DOCX 结构化读取

```
[Heading 1] 快麦奇门自定义接口
[Normal] 自定义奇门官方接入指南

=== 表格 1 (2行 x 2列) ===
  Row1: ['method', 'name']
  Row2: ['kuaimai.order.list.query', '查询订单列表']
```

### staging 带编号 Parquet

| cell | row | col | value | formula |
|------|-----|-----|-------|---------|
| A2 | 2 | A | 义乌部门 | |
| D2 | 2 | D | 6400 | =C2/12 |
| D10 | 10 | D | 161769.47 | =SUM(D2:D9) |

## 五、改动清单

### 新增
- `backend/services/agent/excel_reader.py` — openpyxl 结构化读取，~200行

### 改动
| 文件 | 改什么 |
|------|--------|
| `file_tools.py` | file_read 加 sql/sheet 参数 + 更新 description + schema + routing prompt |
| `file_tool_mixin.py` | _file_dispatch 加数据文件分支 |
| `file_read_extensions.py` | _read_docx 改结构化标注 |
| `file_executor.py:360-362` | 删掉数据文件拦截（"请用 data_query 读取"），file_read 自己处理 |
| `data_query_executor.py` | 6处输出文本：238/496/497/507/511/512行的 data_query(...) → file_read(path=...) |
| `data_profile.py` | 2处输出文本：215/370行的 data_query(file=...) → file_read(path=...) |
| `common_tools.py` | 删 data_query 工具定义 |
| `tool_executor.py` | 删 _data_query 方法和 _handlers 注册 |
| `chat_tools.py` | TOOL_SYSTEM_PROMPT + _CONCURRENT_SAFE + _CORE_TOOLS，data_query → file_read |
| `tool_domains.py` | 删 data_query，file_read GENERAL → SHARED |
| `phase_tools.py` | _get_data_query_tool → 获取 file_read + 提示词 |
| `chat_tool_mixin.py:222` | tool_name "data_query" → "file_read" |
| `file_metadata_extractor.py` | 读取命令 data_query → file_read |
| sandbox_worker.py | 提示文本 data_query → file_read |
| 文档 | TECH 文档 + FUNCTION_INDEX + PROJECT_OVERVIEW |
| 测试 | 21 后端 + 2 前端测试文件 |

## 六、任务拆分

### Phase 1：excel_reader.py（新建）
- [ ] 1.1 openpyxl 两次读取（data_only=False + True）
- [ ] 1.2 Claude 风格输出（编号+公式+空值跳过）
- [ ] 1.3 大文件截断（前5+后5行 + 公式全提取）
- [ ] 1.4 多 sheet 扫描
- [ ] 1.5 staging 带编号 Parquet

### Phase 2：DOCX 结构化标注
- [ ] 2.1 _read_docx 改造（[Heading]/[Normal] + 表格行号维度）

### Phase 3：工具合并
- [ ] 3.1 file_tools.py 工具定义更新
- [ ] 3.2 file_tool_mixin.py 数据文件分支
- [ ] 3.3 删 data_query（common_tools + tool_executor + tool_domains + chat_tools + phase_tools + chat_tool_mixin）
- [ ] 3.4 file_metadata_extractor.py 读取命令更新

### Phase 4：清理 + 验证
- [ ] 4.1 全局 grep data_query，更新所有剩余引用（sandbox_worker / 文档 / 测试）
- [ ] 4.2 全量测试无回归
