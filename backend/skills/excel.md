# Excel / CSV 文件探索指南

处理任何表格文件前，先按以下方法探索结构。探索完成后再写处理代码。

## 探索方法：三段采样 + 带坐标输出

```python
import openpyxl

wb = openpyxl.load_workbook(文件路径, read_only=True, data_only=True)
print(f"Sheet: {wb.sheetnames}")

ws = wb.active
total_rows = ws.max_row or 0
total_cols = ws.max_column or 0
print(f"范围: {total_rows} 行 × {total_cols} 列")

def sample(ws, min_row, max_row, label):
    print(f"\n--- {label} (Row {min_row}-{max_row}) ---")
    for row in ws.iter_rows(min_row=min_row, max_row=max_row):
        cells = []
        for cell in row:
            if cell.value is not None:
                cells.append(f"{cell.column_letter}{cell.row}:{cell.value}")
        if cells:
            print(f"  Row{row[0].row}: {cells}")
        else:
            print(f"  Row{row[0].row}: [空行]")

# 头部：找表头位置和数据起点
sample(ws, 1, min(10, total_rows), "头部")

# 中部：发现合并单元格、结构变化
if total_rows > 30:
    mid = total_rows // 2
    sample(ws, mid, min(mid + 5, total_rows), "中部")

# 尾部：发现汇总行、数据结尾
if total_rows > 20:
    sample(ws, max(1, total_rows - 4), total_rows, "尾部")

wb.close()
```

## 探索后要回答的问题

看完输出后，在回复中说明：
1. 表头在第几行？（前几行可能是标题/日期/说明）
2. 有无合并单元格？（某列连续 None = 合并，需要对该列 ffill）
3. 尾部有无汇总行？（"合计/总计"需要剔除）
4. 有无分隔空行？（空行分隔分组时，ffill 不能跨空行）
5. 数据类型是否一致？（同列中部和头部类型不同需要清洗）

## CSV 补充

CSV 用 duckdb 探索，编码问题用 pandas 兜底：
```python
import duckdb
try:
    duckdb.sql(f"SELECT * FROM '{文件路径}' LIMIT 5").show()
    print(duckdb.sql(f"SELECT count(*) FROM '{文件路径}'").fetchone()[0], "行")
except:
    import pandas as pd
    df = pd.read_csv(文件路径, encoding='gbk', nrows=5)
    print(df)
```

## 多 Sheet

多个 Sheet 时先列出全部，问用户要分析哪个，不要默认只读第一个。
