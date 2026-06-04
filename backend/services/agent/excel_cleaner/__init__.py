"""Excel 三层清洗模块（结构检测 / 智能清洗 / 质量校验）。

包目录拆分（参考 file_meta/ 范式）：
- report.py:           ExcelStructure / CleaningReport / IO
- structure.py:        _detect_structure + XML 解析 + 列字母工具（Layer 1）
- actions.py:          8 个清洗动作（Layer 2，clean_excel 内部使用）
- core.py:             clean_excel 主入口（Layer 3）
- _strategy_helpers:   _strategy_xxx 私有提取辅助

对外契约（被外部模块/测试直接 import 的名字）只重导出在此 __init__。
8 个清洗动作是 clean_excel 的内部实现，不在顶层导出，
仅 _flatten_multi_header 例外（test_excel_cleaner.py 直接调用单测它的行为）。
如需直接调用其他 action，请走子模块路径：
    from services.agent.excel_cleaner.actions import _apply_merge_fill
"""

# 数据结构 + IO
from services.agent.excel_cleaner.report import (
    CleaningReport,
    ExcelStructure,
    read_cleaning_report,
    write_cleaning_report,
)

# 结构检测（XML 解析 + 列字母）
from services.agent.excel_cleaner.structure import (
    _col_letter_to_index,
    _detect_structure,
    _parse_sheet_tags,
    _resolve_sheet_xml_path,
)

# 清洗动作中唯一被外部测试直接 import 的（test_excel_cleaner.py::TestMultiLevelHeader）
from services.agent.excel_cleaner.actions import _flatten_multi_header

# 主入口
from services.agent.excel_cleaner.core import clean_excel

__all__ = [
    # 公开 API
    "CleaningReport",
    "ExcelStructure",
    "clean_excel",
    "read_cleaning_report",
    "write_cleaning_report",
    # 私有 API（仍被外部模块/测试直接引用）
    "_col_letter_to_index",
    "_detect_structure",
    "_parse_sheet_tags",
    "_resolve_sheet_xml_path",
    "_flatten_multi_header",
]
