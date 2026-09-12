"""Original common tool factory API, projected from the Spec-owned catalog."""
from typing import Any, Dict, List
from services.tools.catalog import group_schemas
from services.tools.definitions.erp import _build_erp_agent_description


def build_common_tools() -> List[Dict[str, Any]]:
    return group_schemas("common_tools")
