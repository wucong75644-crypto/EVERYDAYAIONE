# Word / PPT 处理指南

处理前必须先了解文档结构：

- 段落数、表格数
- 标题层级和正文结构
- 表格有无合并单元格（Word 合并单元格会重复引用同一个 cell，用 id(cell._tc) 去重）

PPT 注意备注栏内容（slide.notes_slide）和嵌入表格。
