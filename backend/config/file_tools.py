"""Compatibility projections; tool definitions are owned by ToolSpec. Prompt selection stays here."""

from typing import Any, Dict, List, Set
from services.tools.catalog import definition_registry, group_schemas, validation_schemas
from config.file_call_contract import FILE_ANALYZE_SELECTOR_GUIDANCE


FILE_INFO_TOOLS: Set[str] = {s.name for s in definition_registry().specs() if 'file_tools' in s.catalog_groups}


FILE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = validation_schemas('file_tools')


def build_file_tools() -> List[Dict[str, Any]]:
    return group_schemas('file_tools')


FILE_ROUTING_PROMPT = (
    "## 文件操作规则\n"
    "- file_search 定位文件: 无参数列目录,有 path/keyword 搜索文件\n"
    "- 数据文件(Excel/CSV)用 file_analyze 治理 → 生成 staging/x.parquet\n"
    "- code_execute 直接读 parquet:\n"
    "  df = pd.read_parquet('staging/x.parquet')\n"
    "  或 duckdb.sql(\"SELECT * FROM 'staging/x.parquet'\").df()\n"
    "- 图片: file_search 命中单张图直接返回多模态(视觉模型自动可见,无需额外工具)\n"
    "- 撤销/恢复原文件 → restore_file\n\n"
)
