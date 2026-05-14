---
name: parquet
match: .parquet
description: Parquet 数据文件查询。ERP 导出和 staging 缓存统一用此方法。
---

# Parquet 数据处理指南

Parquet 文件来源：ERP 导出、file_search 自动转换、code_execute 生成。
统一用 duckdb SQL 查询，不要用 pandas 全量加载。

## 一、探索结构（必须执行，不可跳过）

```python
import duckdb

file = 文件路径

# schema（列名 + 类型）
duckdb.sql(f"DESCRIBE SELECT * FROM '{file}'").show()

# 行数
count = duckdb.sql(f"SELECT count(*) FROM '{file}'").fetchone()[0]
print(f"总行数: {count}")

# 头部预览
duckdb.sql(f"SELECT * FROM '{file}' LIMIT 5").show()

# 尾部预览（检查数据完整性）
if count > 20:
    duckdb.sql(f"SELECT * FROM '{file}' LIMIT 5 OFFSET {count - 5}").show()
```

## 二、查询

中文列名必须用双引号包裹：
```python
duckdb.sql(f"""
    SELECT "店铺名称", SUM("金额") as 总金额
    FROM '{file}'
    WHERE "创建时间" >= '2026-01-01'
    GROUP BY "店铺名称"
    ORDER BY 总金额 DESC
""").show()
```

## 三、导出

```python
# 导出 Excel
result = duckdb.sql(f"SELECT * FROM '{file}' WHERE ...")
result.df().to_excel(OUTPUT_DIR + '/导出结果.xlsx', index=False)

# 导出 CSV
duckdb.sql(f"""
    COPY (SELECT * FROM '{file}' WHERE ...)
    TO '{OUTPUT_DIR}/导出结果.csv' (HEADER, DELIMITER ',')
""")
```

## 四、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 时间列查询无结果 | 类型是 string 不是 timestamp | 先 DESCRIBE 看类型，用 CAST 转换 |
| 列名报错 | 列名含空格或括号 | 双引号包裹: "销售额(元)" |
| 查询结果为空 | 筛选条件不匹配 | 先 SELECT DISTINCT 看实际值 |
| 内存不足 | SELECT * 全量取出 | 用 SQL 聚合，不要全量加载到 pandas |
