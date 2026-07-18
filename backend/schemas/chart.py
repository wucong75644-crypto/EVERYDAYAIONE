"""数据图表消息协议。"""

from typing import Any, Dict, Literal

from pydantic import BaseModel, field_validator


class ChartPart(BaseModel):
    """ECharts正式协议及 Plotly/Vega-Lite历史只读内容块。"""

    type: Literal["chart"] = "chart"
    option: Dict[str, Any]
    title: str = ""
    chart_type: str = ""
    spec_format: str = "echarts"

    @field_validator("spec_format")
    @classmethod
    def normalize_spec_format(cls, value: str) -> str:
        if value in {"echarts", "plotly", "vegalite"}:
            return value
        return "unknown"
