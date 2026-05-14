---
name: pdf
match: .pdf
description: PDF 文档读取、文本提取与表格解析。用户上传 PDF 时必读。
---

# PDF 处理指南

## 一、探索结构（必须执行，不可跳过）

PDF 有三种类型，处理方式完全不同。必须先判断类型。

```python
import pdfplumber

with pdfplumber.open(文件路径) as pdf:
    total_pages = len(pdf.pages)
    print(f"共 {total_pages} 页")

    # 采样前 2 页判断类型
    for i, page in enumerate(pdf.pages[:2]):
        text = page.extract_text() or ""
        tables = page.extract_tables()
        print(f"\n--- 第{i+1}页 ---")
        print(f"  文本长度: {len(text)} 字符")
        print(f"  表格数: {len(tables)}")

        if len(text.strip()) < 10:
            print("  ⚠ 疑似扫描件（无可提取文本）")
        else:
            print(f"  文本预览: {text[:300]}")

        if tables:
            t = tables[0]
            print(f"  第1个表格: {len(t)}行 × {len(t[0]) if t else 0}列")
            for row in t[:3]:
                print(f"    {row}")
```

### 判断结果 → 对应处理方式

| 类型 | 特征 | 处理方式 |
|------|------|---------|
| 纯文字 | 有文本，无表格 | extract_text() 逐页提取 |
| 表格型 | 有表格 | extract_tables() → DataFrame |
| 混合型 | 文本 + 表格都有 | 分别提取，表格转 DataFrame |
| 扫描件 | 无文本（<10字符） | 告知用户当前不支持 OCR |

## 二、纯文字 PDF

```python
all_text = []
with pdfplumber.open(文件路径) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if text:
            all_text.append(f"=== 第{i+1}页 ===\n{text}")

full_text = '\n\n'.join(all_text)
print(full_text[:3000])  # 预览前3000字
```

大 PDF 不要一次读全部，按需读取指定页：
```python
with pdfplumber.open(文件路径) as pdf:
    # 只读第 3-5 页
    for page in pdf.pages[2:5]:
        print(page.extract_text())
```

### 过滤页眉页脚

```python
# 裁剪掉顶部和底部各 50 像素
with pdfplumber.open(文件路径) as pdf:
    page = pdf.pages[0]
    cropped = page.within_bbox((0, 50, page.width, page.height - 50))
    text = cropped.extract_text()
```

## 三、表格型 PDF

### 3.1 单页表格

```python
import pandas as pd

with pdfplumber.open(文件路径) as pdf:
    page = pdf.pages[0]
    tables = page.extract_tables()
    if tables:
        # 第一行当表头
        df = pd.DataFrame(tables[0][1:], columns=tables[0][0])
        print(df.shape)
        print(df.head())
```

### 3.2 多页连续表格（最常见的难点）

合同、报价单、财务报表的表格经常跨页。pdfplumber 每页独立提取，需要手动合并。

```python
import pandas as pd

all_dfs = []
header = None

with pdfplumber.open(文件路径) as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue

            if header is None:
                # 第一个表格：第一行是表头
                header = table[0]
                data = table[1:]
            else:
                # 后续表格：检查第一行是否是重复的表头
                if table[0] == header:
                    data = table[1:]  # 跳过重复表头
                else:
                    data = table

            df = pd.DataFrame(data, columns=header)
            all_dfs.append(df)
            print(f"  第{i+1}页: 提取 {len(data)} 行")

if all_dfs:
    combined = pd.concat(all_dfs, ignore_index=True)
    # 清理：去掉全空行、去掉列名行混入数据的情况
    combined = combined.dropna(how='all')
    combined = combined[combined.iloc[:, 0] != header[0]]  # 去重复表头
    print(f"\n合并后: {combined.shape}")
    print(combined.head())
    combined.to_excel(OUTPUT_DIR + '/PDF表格数据.xlsx', index=False)
```

### 3.3 表格提取质量差时

有些 PDF 表格线不完整，默认策略可能漏列或错位。调整提取策略：

```python
# 策略1：用文本位置而不是线条检测
table = page.extract_table(table_settings={
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
})

# 策略2：手动指定列分隔位置（需要先观察页面宽度）
# 用 page.chars 查看文字位置
chars = page.chars[:20]
for c in chars:
    print(f"  '{c['text']}' x={c['x0']:.0f}")
```

## 四、混合型 PDF

文字和表格都有（如合同正文中嵌了报价表格）：

```python
with pdfplumber.open(文件路径) as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        if tables:
            print(f"\n第{i+1}页 [表格]:")
            for j, t in enumerate(tables):
                df = pd.DataFrame(t[1:], columns=t[0])
                print(f"  表格{j+1}: {df.shape}")
                print(df.to_string(index=False))
        else:
            text = page.extract_text()
            if text and text.strip():
                print(f"\n第{i+1}页 [文字]:")
                print(text[:500])
```

## 五、常见陷阱速查

| 现象 | 原因 | 解法 |
|------|------|------|
| 表格列错位 | PDF 表格线不完整 | vertical_strategy="text" |
| 跨页表格断裂 | pdfplumber 按页提取 | 合并同列数的相邻表格 |
| 表头每页重复 | PDF 打印设置 | 检测并跳过重复表头行 |
| 提取出空字符串 | 扫描件 PDF | 告知用户不支持 OCR |
| 数字含换行符 | PDF 排版换行 | .str.replace('\n','') |
| 日期格式不一致 | PDF 来源不同 | pd.to_datetime(errors='coerce') |
| 金额带货币符号 | "¥1,234.56" | .str.replace('[¥,$,]','',regex=True) |
