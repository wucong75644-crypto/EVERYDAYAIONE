---
name: csv
match: .csv, .tsv
description: CSV/TSV 文本表格读取与分析。用户上传 CSV 时必读。
---

# CSV/TSV 处理指南

## 一、探索结构（必须执行，不可跳过）

### 1.1 先解决编码（中文 CSV 最常出错的地方）

ERP 和电商平台导出的 CSV 大部分是 GBK 编码，不是 UTF-8。
duckdb 默认 UTF-8，遇到 GBK 会报错或乱码。**先检测编码再读取。**

```python
import duckdb

file = 文件路径

# 尝试 UTF-8（大部分情况）
try:
    result = duckdb.sql(f"SELECT * FROM read_csv_auto('{file}') LIMIT 3")
    # 检查是否乱码（中文列名出现 ??? 或 ä¸­ 等）
    cols = result.columns
    has_garbled = any('?' in c or '\\x' in repr(c) for c in cols)
    if has_garbled:
        raise ValueError("疑似乱码")
    print("编码: UTF-8")
    result.show()
except:
    # UTF-8 失败，用 pandas + GBK
    import pandas as pd
    df = pd.read_csv(file, encoding='gbk', nrows=5)
    print("编码: GBK")
    print(f"列名: {list(df.columns)}")
    print(df.to_string(index=False))
```

### 1.2 结构探索

```python
# 确定编码后，查看完整结构
import duckdb

# 如果是 GBK，先转 pandas 再查
# 如果是 UTF-8，直接用 duckdb
result = duckdb.sql(f"SELECT * FROM '{file}' LIMIT 5")
df_preview = result.df()
print(f"形状: {df_preview.shape}")
print(f"列名: {list(df_preview.columns)}")
print(f"类型:\n{df_preview.dtypes}")
result.show()

# 总行数（不加载全量数据）
count = duckdb.sql(f"SELECT count(*) FROM '{file}'").fetchone()[0]
print(f"总行数: {count}")

# 尾部采样（检查是否有汇总行）
if count > 20:
    duckdb.sql(f"""
        SELECT * FROM '{file}'
        LIMIT 5 OFFSET {count - 5}
    """).show()
```

### 1.3 判断要点

- **首行是不是列名？** 有些导出文件首行是说明文字
- **分隔符是什么？** .tsv 是 tab，有些 .csv 用分号
- **尾部有汇总行吗？** "合计""总计"需要剔除
- **数值列有没有千分位逗号？** "1,234.56" 会被读成文本

## 二、读取数据

### 2.1 duckdb 查询（推荐，大小文件通用）

```python
# 中文列名用双引号包裹
result = duckdb.sql(f"""
    SELECT "店铺名称", SUM("金额") as 总金额
    FROM '{file}'
    GROUP BY "店铺名称"
    ORDER BY 总金额 DESC
""")
result.show()
```

### 2.2 GBK 编码文件用 pandas

```python
import pandas as pd
df = pd.read_csv(file, encoding='gbk')
# 如果有 BOM 头（Excel 导出的 UTF-8 常带）：
df = pd.read_csv(file, encoding='utf-8-sig')
```

### 2.3 大文件（>100MB）

禁止 pd.read_csv 全量加载。用 duckdb SQL 聚合：
```python
duckdb.sql(f"""
    SELECT "分类", COUNT(*) as 数量, SUM(CAST("金额" AS DOUBLE)) as 总金额
    FROM '{file}'
    GROUP BY "分类"
""").show()
```

需要导出子集时：
```python
duckdb.sql(f"""
    COPY (SELECT * FROM '{file}' WHERE "日期" >= '2026-01-01')
    TO '{OUTPUT_DIR}/筛选结果.csv' (HEADER, DELIMITER ',')
""")
```

## 三、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 中文乱码 | GBK 编码 | encoding='gbk' |
| 列名带 \ufeff | UTF-8 BOM 头 | encoding='utf-8-sig' |
| 数字读成文本 | 千分位逗号 | .str.replace(',','').astype(float) |
| 列数对不上 | 分隔符不是逗号 | sep=';' 或 sep='\t' |
| 首行不是列名 | 文件有说明行 | header=None 或 skiprows=N |
| 日期格式混乱 | 多种格式混用 | pd.to_datetime(errors='coerce') |
| 读取超慢 | 文件 >100MB | duckdb SQL 聚合，不要全量加载 |
