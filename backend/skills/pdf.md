# PDF 文件探索指南

处理任何 PDF 前，先判断类型。不同类型处理方式完全不同。

## 探索方法：类型判断 + 内容预览

```python
import pdfplumber

with pdfplumber.open(文件路径) as pdf:
    total_pages = len(pdf.pages)
    print(f"共 {total_pages} 页")

    for i, page in enumerate(pdf.pages[:2]):
        text = page.extract_text() or ""
        tables = page.extract_tables()
        print(f"\n--- 第{i+1}页 ---")
        print(f"  文本: {len(text)}字符 | 表格: {len(tables)}个")

        if len(text.strip()) < 10:
            print("  ⚠ 扫描件（无可提取文本），当前不支持 OCR")
        else:
            print(f"  文本预览:\n{text[:500]}")

        if tables:
            t = tables[0]
            print(f"  表格预览({len(t)}行 × {len(t[0]) if t else 0}列):")
            for row in t[:3]:
                print(f"    {row}")
```

## 探索后要回答的问题

1. 什么类型？纯文字 / 表格型 / 混合型 / 扫描件
2. 表格是否跨页？（跨页需要合并，注意去重复表头）
3. 有无页眉页脚干扰？
