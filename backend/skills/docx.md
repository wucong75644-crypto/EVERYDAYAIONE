---
name: docx
match: .docx
description: Word 文档读取与内容提取。用户上传 Word 时必读。
---

# Word 文档处理指南

## 一、探索结构（必须执行，不可跳过）

```python
from docx import Document

doc = Document(文件路径)
print(f"段落数: {len(doc.paragraphs)}")
print(f"表格数: {len(doc.tables)}")
print(f"节数: {len(doc.sections)}")

# 文档结构预览（标题层级 + 正文前 15 段）
print("\n--- 文档结构 ---")
for i, p in enumerate(doc.paragraphs[:15]):
    text = p.text.strip()
    if not text:
        continue
    style = p.style.name if p.style else "Normal"
    if 'Heading' in style:
        level = style.replace('Heading ', '')
        print(f"  {'#' * int(level) if level.isdigit() else '#'} {text}")
    else:
        print(f"    {text[:80]}{'...' if len(text) > 80 else ''}")

# 表格预览
if doc.tables:
    print(f"\n--- 表格预览（共{len(doc.tables)}个）---")
    for i, table in enumerate(doc.tables[:3]):
        rows = len(table.rows)
        cols = len(table.columns)
        print(f"  表格{i+1}: {rows}行 × {cols}列")
        for j, row in enumerate(table.rows[:3]):
            cells = [cell.text.strip()[:20] for cell in row.cells]
            print(f"    Row{j+1}: {cells}")
```

## 二、按需提取

### 2.1 提取全部文本

```python
full_text = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
print(f"总字数: {len(full_text)}")
print(full_text[:2000])
```

### 2.2 提取表格数据

```python
import pandas as pd

for i, table in enumerate(doc.tables):
    # 处理合并单元格去重
    data = []
    for row in table.rows:
        seen_ids = set()
        cells = []
        for cell in row.cells:
            cell_id = id(cell._tc)
            if cell_id in seen_ids:
                continue  # 跳过合并单元格的重复引用
            seen_ids.add(cell_id)
            cells.append(cell.text.strip())
        data.append(cells)

    if len(data) > 1:
        # 列数可能不一致（合并导致），对齐到最大列数
        max_cols = max(len(row) for row in data)
        data = [row + [''] * (max_cols - len(row)) for row in data]

        df = pd.DataFrame(data[1:], columns=data[0])
        print(f"\n--- 表格{i+1} ({df.shape[0]}行 × {df.shape[1]}列) ---")
        print(df.head().to_string(index=False))
```

### 2.3 按标题提取章节

```python
sections = {}
current_heading = "(开头)"
current_text = []

for p in doc.paragraphs:
    style = p.style.name if p.style else ""
    if 'Heading' in style and p.text.strip():
        if current_text:
            sections[current_heading] = '\n'.join(current_text)
        current_heading = p.text.strip()
        current_text = []
    elif p.text.strip():
        current_text.append(p.text.strip())

if current_text:
    sections[current_heading] = '\n'.join(current_text)

print(f"共 {len(sections)} 个章节:")
for title in sections:
    print(f"  {title} ({len(sections[title])}字)")
```

## 三、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 表格列数不一致 | 合并单元格 | id(cell._tc) 去重 |
| 表格中重复文本 | 合并单元格被多次引用 | 用 seen_ids 跳过 |
| 提取不到图片 | python-docx 限制 | 告知用户不支持图片提取 |
| 页眉页脚内容 | 需要通过 sections 访问 | doc.sections[0].header.paragraphs |
| 嵌套表格 | table.rows 只返回外层 | 需要递归访问 cell.tables |
| .doc 旧格式 | python-docx 不支持 | 告知用户转存为 .docx |
