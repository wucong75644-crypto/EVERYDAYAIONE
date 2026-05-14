---
name: text
match: .txt, .md, .log, .json, .jsonl, .xml, .html, .yaml, .yml
description: 文本文件读取与内容提取。用户上传文本类文件时必读。
---

# 文本文件处理指南

## 一、探索结构（必须执行，不可跳过）

```python
import os

size = os.path.getsize(文件路径)
print(f"文件大小: {size / 1024:.1f} KB")

# 先检测编码
try:
    with open(文件路径, 'r', encoding='utf-8') as f:
        head = f.readlines()[:10]
    encoding = 'utf-8'
except UnicodeDecodeError:
    with open(文件路径, 'r', encoding='gbk') as f:
        head = f.readlines()[:10]
    encoding = 'gbk'

print(f"编码: {encoding}")
print(f"前 10 行:")
for i, line in enumerate(head):
    print(f"  {i+1}: {line.rstrip()[:120]}")
```

## 二、按格式处理

### JSON 文件
```python
import json

with open(文件路径, encoding=encoding) as f:
    data = json.load(f)

print(f"类型: {type(data).__name__}")
if isinstance(data, list):
    print(f"共 {len(data)} 条记录")
    print(f"第1条: {json.dumps(data[0], ensure_ascii=False, indent=2)[:500]}")
elif isinstance(data, dict):
    print(f"顶层 key: {list(data.keys())}")
```

### JSONL 文件（每行一个 JSON）
```python
import pandas as pd
df = pd.read_json(文件路径, lines=True, encoding=encoding)
print(f"形状: {df.shape}")
print(df.head())
```

大 JSONL 文件用 duckdb：
```python
import duckdb
duckdb.sql(f"SELECT * FROM read_json_auto('{文件路径}') LIMIT 10").show()
```

### 日志文件（.log）
```python
# 大日志只看尾部
with open(文件路径, encoding=encoding) as f:
    lines = f.readlines()
print(f"总行数: {len(lines)}")
print("--- 最后 20 行 ---")
for line in lines[-20:]:
    print(line.rstrip())
```

### CSV 格式的 .txt
```python
import duckdb
duckdb.sql(f"SELECT * FROM read_csv_auto('{文件路径}') LIMIT 10").show()
```

## 三、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 中文乱码 | GBK 编码 | encoding='gbk' |
| JSON 解析失败 | 文件是 JSONL | pd.read_json(lines=True) |
| 文件太大 read() 卡住 | >10MB | 逐行读取或用 duckdb |
| XML/HTML 需要解析 | 纯文本读取不够 | 告知用户需要专用工具 |
