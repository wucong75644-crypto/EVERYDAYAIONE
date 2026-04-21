# 兼容性 re-export — 文件已迁移到 services/agent/erp_agent.py
from services.agent.erp_agent import *  # noqa: F401,F403
from services.agent.erp_agent import ERPAgent  # noqa: F401
from services.agent.agent_result import AgentResult  # noqa: F401 — Phase 6: 替代 ERPAgentResult
from services.agent.erp_agent_types import (  # noqa: F401 — 显式导出常用符号
    MAX_ERP_TURNS,
    filter_erp_context,
)
from services.agent.erp_agent_types import (  # noqa: F401 — 内部常量（测试用）
    TOOL_TIMEOUT as _TOOL_TIMEOUT,
    MAX_TOTAL_TOKENS as _MAX_TOTAL_TOKENS,
)
