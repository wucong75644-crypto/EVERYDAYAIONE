---
name: pptx
match: .pptx
description: PPT 演示文稿读取与内容提取。用户上传 PPT 时必读。
---

# PPT 处理指南

## 一、探索结构（必须执行，不可跳过）

```python
from pptx import Presentation

prs = Presentation(文件路径)
total = len(prs.slides)
print(f"共 {total} 页幻灯片\n")

for i, slide in enumerate(prs.slides):
    title = ""
    texts = []
    table_count = 0

    for shape in slide.shapes:
        if shape == slide.shapes.title and hasattr(shape, 'text'):
            title = shape.text.strip()
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                t = para.text.strip()
                if t:
                    texts.append(t)
        if shape.has_table:
            table_count += 1

    title_display = title or '(无标题)'
    extra = f" | {table_count}个表格" if table_count else ""
    print(f"  第{i+1}页: {title_display} | {len(texts)}段文字{extra}")
```

## 二、提取内容

### 2.1 提取全部文本

```python
for i, slide in enumerate(prs.slides):
    print(f"\n=== 第{i+1}页 ===")

    # 备注
    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if notes:
            print(f"  [备注] {notes[:100]}")

    # 正文
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if text:
                    print(f"  {text}")
```

### 2.2 提取表格

```python
import pandas as pd

for i, slide in enumerate(prs.slides):
    for shape in slide.shapes:
        if not shape.has_table:
            continue
        table = shape.table
        data = []
        for row in table.rows:
            data.append([cell.text.strip() for cell in row.cells])

        if len(data) > 1:
            df = pd.DataFrame(data[1:], columns=data[0])
            print(f"\n第{i+1}页表格: {df.shape[0]}行 × {df.shape[1]}列")
            print(df.to_string(index=False))
```

## 三、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 遗漏备注内容 | 默认不读备注 | slide.notes_slide.notes_text_frame |
| 图表数据提取不到 | 嵌入图表非表格 | python-pptx 无法提取图表数据 |
| 文字顺序混乱 | shape 无固定顺序 | 按 shape.top 排序 |
| .ppt 旧格式 | python-pptx 不支持 | 告知用户转存为 .pptx |
