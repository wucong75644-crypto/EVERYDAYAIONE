---
name: excel
match: .xlsx, .xls, .xlsm
description: Excel 表格读取、分析与生成。用户上传 Excel 时必读。
---

# Excel 处理指南

## 一、探索结构（必须执行，不可跳过）

处理任何 Excel 前，必须完成以下探索。不要凭文件名猜测内容。

### 1.1 三段采样：头部 + 中部 + 尾部

只看前几行不够——业务报表前几行常是标题/日期/汇总，中部可能有合并单元格，尾部可能有汇总行。

```python
import openpyxl

wb = openpyxl.load_workbook(文件路径, read_only=True, data_only=True)
print(f"Sheet 列表: {wb.sheetnames}")

ws = wb.active  # 或 wb['指定Sheet名']
total_rows = ws.max_row or 0
total_cols = ws.max_column or 0
print(f"范围: {total_rows} 行 × {total_cols} 列")

def sample_rows(ws, min_row, max_row, label):
    """采样指定行范围，输出带坐标的内容"""
    print(f"\n--- {label} (Row {min_row}-{max_row}) ---")
    for row in ws.iter_rows(min_row=min_row, max_row=max_row):
        cells = []
        for cell in row:
            if cell.value is not None:
                col_letter = cell.column_letter
                cells.append(f"{col_letter}{cell.row}:{cell.value}")
        if cells:
            print(f"  Row{row[0].row}: {cells}")
        else:
            print(f"  Row{row[0].row}: [空行]")

# 头部（找表头和数据起点）
sample_rows(ws, 1, min(10, total_rows), "头部")

# 中部（发现结构变化、合并单元格）
if total_rows > 30:
    mid = total_rows // 2
    sample_rows(ws, mid, min(mid + 5, total_rows), "中部")

# 尾部（发现汇总行、数据结尾格式）
if total_rows > 20:
    sample_rows(ws, max(1, total_rows - 4), total_rows, "尾部")

wb.close()
```

### 1.2 根据采样结果判断

观察输出后，回答以下问题再写分析代码：
- **表头在第几行？** 前几行是标题/日期/说明的，表头可能在 Row2、Row3 甚至更后面
- **有无合并单元格？** 某列连续出现 None，说明有合并，需要 ffill 处理
- **尾部有无汇总行？** "合计""总计"等行需要在分析前剔除
- **数据类型是否一致？** 同一列中部和头部类型不同（如数字突然变成文本），需要清洗
- **有无分隔空行？** 有些报表用空行分隔分组，ffill 不能跨空行

### 1.3 多 Sheet 处理

多个 Sheet 时，先列出所有 Sheet 结构，询问用户要分析哪个：
```python
for name in wb.sheetnames:
    ws = wb[name]
    print(f"  {name}: {ws.max_row}行 × {ws.max_column}列")
```
不要默认只读第一个 Sheet。

## 二、读取数据

### 2.1 小表（<5000 行）—— pandas

```python
import pandas as pd

# header 参数根据探索结果确定（表头在第几行，从0开始计数）
df = pd.read_excel(文件路径, sheet_name='Sheet名', header=表头行号)
print(f"形状: {df.shape}")
print(f"列名: {list(df.columns)}")
print(f"类型:\n{df.dtypes}")
df.head()
```

### 2.2 大表（≥5000 行）—— calamine 引擎加速

```python
# calamine 比 openpyxl 快 3-5 倍
df = pd.read_excel(文件路径, engine='calamine', header=表头行号)
```

如果内存紧张或超过 5 万行，转 Parquet 后用 duckdb：
```python
# 一次性转换
df = pd.read_excel(文件路径, engine='calamine', header=表头行号)
parquet_path = STAGING_DIR + '/数据.parquet'
df.to_parquet(parquet_path, index=False)

# 后续全用 SQL 查询，不再加载全量
import duckdb
duckdb.sql(f"SELECT count(*) FROM '{parquet_path}'").show()
```

## 三、合并单元格处理

### 3.1 检测合并区域

```python
wb = openpyxl.load_workbook(文件路径, read_only=False)  # 检测合并需要非 read_only
ws = wb.active
merged = list(ws.merged_cells.ranges)
if merged:
    print(f"发现 {len(merged)} 个合并区域:")
    for m in merged[:20]:
        print(f"  {m}")
wb.close()
```

### 3.2 分层填充（关键）

业务报表常见多级合并：大类合并 3 行，小类合并 2 行，明细不合并。
pandas 读入后合并区域只有左上角有值，其余为 None。

**错误做法**：对整个 DataFrame ffill → 非合并列的真实空值也被错误填充
**正确做法**：只对合并列 ffill

```python
# 根据探索阶段发现的合并列，逐列填充
merge_cols = ['大类', '小类']  # 根据实际探索结果确定
for col in merge_cols:
    df[col] = df[col].ffill()

# 如果有分隔空行（整行全空），先标记再填充
mask = df.isnull().all(axis=1)  # 全空行
if mask.any():
    # 给空行一个分组标记，ffill 不跨组
    df['_group'] = mask.cumsum()
    for col in merge_cols:
        df[col] = df.groupby('_group')[col].ffill()
    df = df[~mask].drop(columns='_group')  # 删除空行和临时列
```

### 3.3 验证填充结果

填充后必须抽查，确认没有错位：
```python
# 抽查前 3 个分组
for name, group in df.groupby(merge_cols[0]):
    print(f"\n{merge_cols[0]}={name} | 共{len(group)}行")
    print(group.head(3).to_string(index=False))
    if len(df.groupby(merge_cols[0])) >= 3:
        break
```

## 四、数据分析

### 4.1 分析前清洗

```python
# 1. 删除汇总行（尾部探索时发现的"合计""总计"等）
df = df[~df['第一列名'].astype(str).str.contains('合计|总计|小计', na=False)]

# 2. 数值列转数字（带千分位逗号或中文万/亿的）
def to_number(s):
    if pd.isna(s): return None
    s = str(s).strip().replace(',', '').replace('，', '')
    if s.endswith('万'): return float(s[:-1]) * 10000
    if s.endswith('亿'): return float(s[:-1]) * 100000000
    try: return float(s)
    except: return None

df['金额'] = df['金额'].apply(to_number)

# 3. 日期列转日期
df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
```

### 4.2 分组统计

```python
result = df.groupby('分组列').agg(
    总数量=('数量', 'sum'),
    总金额=('金额', 'sum'),
    笔数=('订单号', 'count'),
).reset_index().sort_values('总金额', ascending=False)
print(result.to_string(index=False))
```

## 五、跨表操作

用户常见需求：用 A 表的编码去 B 表查价格/名称，补充到 A 表。

```python
# A 表：订单明细（有商品编码，缺商品名称和价格）
# B 表：商品主数据（有编码、名称、价格）

df_a = pd.read_excel(WORKSPACE_DIR + '/订单表.xlsx', header=表头行号)
df_b = pd.read_excel(WORKSPACE_DIR + '/商品表.xlsx', header=表头行号)

# 关联补充（类似 VLOOKUP）
result = df_a.merge(
    df_b[['商品编码', '商品名称', '单价']],
    on='商品编码',
    how='left'
)

# 检查未匹配的行
unmatched = result[result['商品名称'].isna()]
if len(unmatched) > 0:
    print(f"⚠ {len(unmatched)} 行未匹配到商品信息:")
    print(unmatched['商品编码'].unique()[:10])

result.to_excel(OUTPUT_DIR + '/补充后的订单表.xlsx', index=False)
```

## 六、按参考格式整理

用户提供参考模板（图片或文件）要求按该格式输出时：

1. 先理解目标格式的结构（列顺序、列名、分组方式）
2. 从源数据中映射对应字段
3. 重排列顺序、重命名列名、调整分组层级
4. 输出前让用户确认列名映射是否正确

```python
# 列名映射（根据参考格式确定）
column_mapping = {
    '原列名A': '目标列名1',
    '原列名B': '目标列名2',
}
result = df.rename(columns=column_mapping)

# 按目标格式的列顺序排列
target_order = ['目标列名1', '目标列名2', '目标列名3']
result = result[target_order]

result.to_excel(OUTPUT_DIR + '/整理后.xlsx', index=False)
```

## 七、验证（分析完成后必须执行）

每次分析完成后，至少做一项验证：

```python
# 验证1：总数对账——原始数据的合计 vs 分析结果的合计
original_total = df['金额'].sum()
result_total = result['总金额'].sum()
print(f"原始合计: {original_total:,.2f}")
print(f"结果合计: {result_total:,.2f}")
print(f"差异: {abs(original_total - result_total):,.2f}")

# 验证2：抽查几行——回到原始数据核对
sample_idx = [0, len(df)//2, len(df)-1]  # 首/中/末各一行
for i in sample_idx:
    row = df.iloc[i]
    print(f"Row{i}: {row.to_dict()}")
```

## 八、生成 Excel 输出

```python
import pandas as pd

with pd.ExcelWriter(OUTPUT_DIR + '/报表.xlsx', engine='xlsxwriter') as writer:
    df.to_excel(writer, sheet_name='数据', index=False)
    workbook = writer.book
    worksheet = writer.sheets['数据']

    # 金额列数字格式
    money_fmt = workbook.add_format({'num_format': '#,##0.00'})
    # 根据实际列位置设置（C列=索引2）
    worksheet.set_column('C:C', 15, money_fmt)

    # 列宽自适应
    for i, col in enumerate(df.columns):
        max_len = max(df[col].astype(str).map(len).max(), len(str(col))) + 2
        worksheet.set_column(i, i, min(max_len, 30))
```

## 九、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 列名是 Unnamed:0 | 表头不在第1行 | header= 参数指定正确行号 |
| 某列全是 None | 合并单元格 | 对该列 ffill |
| 日期显示为 45678 | Excel 序列号 | pd.to_datetime(df['列'], unit='D', origin='1899-12-30') |
| 数字是文本 | 单元格格式为文本 | .astype(float) 或 to_number() |
| 最后几行数据异常 | 汇总行混在数据中 | 删除含"合计/总计"的行 |
| .xls 旧格式报错 | openpyxl 不支持 | engine='xlrd' |
| 读取特别慢 | 文件大 + openpyxl | engine='calamine' |
| ffill 后数据错位 | 空行分隔了分组 | 按空行分组后组内 ffill |
