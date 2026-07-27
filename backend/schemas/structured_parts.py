"""非媒体类结构化消息内容块。"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class TablePart(BaseModel):
    """沙盒 emit_table 产生的结构化表格内容块。"""

    type: Literal["table"] = "table"
    title: Optional[str] = None
    columns: List[str]
    rows: List[Dict[str, Any]]
    truncated: Optional[bool] = None


class InterruptMarkerPart(BaseModel):
    """消息中断锚点；用于刷新恢复和上下文连续性，不直接渲染。"""

    type: Literal["interrupt_marker"] = "interrupt_marker"
    interrupted_at: str
    reason: Literal["user_cancel", "system_timeout", "network_error"]
