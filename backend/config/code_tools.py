"""Compatibility projections; tool definitions are owned by ToolSpec. Prompt selection stays here."""

from typing import Any, Dict, List, Set
from services.tools.catalog import definition_registry, group_schemas, validation_schemas
from services.tools.definitions.code_schemas import (
    _DESCRIPTION
)


CODE_INFO_TOOLS: Set[str] = {s.name for s in definition_registry().specs() if 'code_tools' in s.catalog_groups}


CODE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = validation_schemas('code_tools')


def build_code_tools(include_workspace: bool = False) -> List[Dict[str, Any]]:
    return group_schemas('code_tools')


CODE_ROUTING_PROMPT = (
    "## 工作流\n"
    "- code_execute 只算数据,不取数据(取数据用 file_search / fetch_all_pages / erp_*)\n"
    "- 典型流程: 取数据 → code_execute 算 → emit_chart/emit_diagram/emit_file/emit_table 给用户看\n"
    "- 完整 API/路径/CAVEATS 见 code_execute 工具 description,不重复约定\n"
    "\n"
    "## 图形选择\n"
    "数值/趋势/统计/比较/占比 → emit_chart(ECharts option)。\n"
    "流程/状态/调用关系/时序/类图/系统关系/甘特图 → emit_diagram(Mermaid source)。\n"
    "普通文字足够清楚时不生成图形；同一内容禁止重复生成两种图形。\n"
    "新消息禁止生成 Plotly/Vega-Lite，它们只保留历史读取兼容。\n"
    "Y 轴必须从 0 开始,无序分类按值降序,不用 3D / 双 Y 轴。\n"
    "\n"
    "## fetch_all_pages\n"
    "- 包装 erp_* 远程查询自动翻页,只用于本地 DB 没有的数据(如物流轨迹)\n"
    "- 结果自动落 staging/erp_xxx.parquet,在 code_execute 里 duckdb 直接读\n"
    "- 用前先按 erp_* 工具的两步协议确认参数格式\n"
)
