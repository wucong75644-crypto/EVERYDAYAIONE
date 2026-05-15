# 文档文件使用指南

## 文档内容
PDF/DOCX/PPTX 已由系统提取为纯文本，直接在消息上下文中可见。
如需精确定位，参考 .meta.json 中的 page_count 和 issues。

## 输出规范
- 引用文档内容时标注页码/段落位置
- 过渡数据（表格需要计算分析）→ 保存为 Parquet 走数据分析流程
- 最终文件（给用户查看下载）→ 导出为 Excel
- 不要尝试自己用 pdfplumber/python-docx 重新读取（系统已提取）
