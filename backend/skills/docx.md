# Word / PPT 文件探索指南

处理文档前，先看清结构。

## Word 探索方法

```python
from docx import Document

doc = Document(文件路径)
print(f"段落: {len(doc.paragraphs)} | 表格: {len(doc.tables)} | 节: {len(doc.sections)}")

print("\n--- 文档结构 ---")
for i, p in enumerate(doc.paragraphs[:15]):
    text = p.text.strip()
    if not text:
        continue
    style = p.style.name if p.style else "Normal"
    if 'Heading' in style:
        print(f"  {'#' * int(style[-1]) if style[-1].isdigit() else '#'} {text}")
    else:
        print(f"    {text[:80]}")

if doc.tables:
    print(f"\n--- 表格预览 ---")
    for i, table in enumerate(doc.tables[:3]):
        print(f"  表格{i+1}: {len(table.rows)}行 × {len(table.columns)}列")
        for j, row in enumerate(table.rows[:3]):
            cells = [cell.text.strip()[:20] for cell in row.cells]
            print(f"    Row{j+1}: {cells}")
```

## PPT 探索方法

```python
from pptx import Presentation

prs = Presentation(文件路径)
print(f"共 {len(prs.slides)} 页")

for i, slide in enumerate(prs.slides):
    title = ""
    text_count = 0
    table_count = 0
    for shape in slide.shapes:
        if shape.has_text_frame:
            text_count += len(shape.text_frame.text.strip())
        if shape == slide.shapes.title and hasattr(shape, 'text'):
            title = shape.text.strip()
        if shape.has_table:
            table_count += 1
    extra = f" | {table_count}表格" if table_count else ""
    print(f"  第{i+1}页: {title or '(无标题)'} | {text_count}字{extra}")
```

## 探索后要回答的问题

1. 文档主要内容是什么？（正文/表格/混合）
2. 表格有无合并单元格？（Word 合并单元格会重复引用，用 id(cell._tc) 去重）
3. 需要提取哪部分？（全文/指定章节/表格数据）
