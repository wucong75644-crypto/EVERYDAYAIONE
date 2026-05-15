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
