"""Compatibility projections; tool definitions are owned by ToolSpec. Prompt selection stays here."""

from typing import Any, Dict, List, Set
from services.tools.catalog import definition_registry, group_schemas, validation_schemas
from services.tools.definitions.erp_local_schemas import (
    _tool, _str, _int, _bool, _enum
)


ERP_LOCAL_TOOLS: Set[str] = {s.name for s in definition_registry().specs() if 'erp_local_tools' in s.catalog_groups}


def build_local_tools() -> List[Dict[str, Any]]:
    return group_schemas('erp_local_tools')


LOCAL_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = validation_schemas('erp_local_tools')
