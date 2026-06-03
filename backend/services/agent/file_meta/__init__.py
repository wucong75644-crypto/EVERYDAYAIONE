"""文件元数据生成模块（.meta.json schema / sample / stats / formulas / issues）。

包目录拆分：
- dataclass.py: FileMeta + 共享常量 + 列号工具
- builders.py:  generate_file_meta + _build_schema / _build_sample / _detect_grain / _scan_issues
- formulas.py:  extract_formulas（Excel 公式提取）
- view.py:      read_file_meta / write_file_meta / format_file_view（IO + 渲染）

公开 API 通过 `from services.agent.file_meta import xxx` 直接访问；
私有名（_detect_grain 等）也在外部被引用，一并重导出。
"""

# 数据类 + 常量
from services.agent.file_meta.dataclass import (
    FileMeta,
    _CATEGORY_THRESHOLD,
    _MAX_ISSUES,
    _SAMPLE_BOUNDARY_MAX,
    _SAMPLE_HEAD,
    _SAMPLE_MIDDLE,
    _SAMPLE_ROWS,
    _SAMPLE_TAIL,
    _col_index_to_letter,
)

# 构建器
from services.agent.file_meta.builders import (
    _build_sample,
    _build_schema,
    _dedup_samples_by_signature,
    _detect_grain,
    _determine_status,
    _infer_dtype,
    _scan_issues,
    _serialize_value,
    generate_file_meta,
)

# 公式提取
from services.agent.file_meta.formulas import (
    _extract_formulas_from_zip,
    _parse_sheet_formulas,
    extract_formulas,
)

# IO + 视图
from services.agent.file_meta.view import (
    _compress_issues,
    _format_single_issue,
    format_file_view,
    read_file_meta,
    write_file_meta,
)

__all__ = [
    "FileMeta",
    "extract_formulas",
    "format_file_view",
    "generate_file_meta",
    "read_file_meta",
    "write_file_meta",
]
