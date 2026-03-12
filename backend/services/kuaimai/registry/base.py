"""
API注册表基础数据结构

每个快麦API用一个ApiEntry描述：
- method: API方法名（如 erp.trade.list.query）
- param_map: 用户友好参数名 → API实际参数名 映射
- formatter: 格式化函数名（在formatters模块中查找）
- response_key: 响应中列表数据的字段名
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ApiEntry:
    """单个API注册条目"""

    # API方法名（如 "erp.trade.list.query"）
    method: str
    # 中文描述
    description: str
    # 用户参数 → API参数 映射
    # 例: {"order_id": "tid", "start_date": "startTime"}
    param_map: Dict[str, str] = field(default_factory=dict)
    # 必填的用户参数名
    required_params: List[str] = field(default_factory=list)
    # 默认参数值
    defaults: Dict[str, Any] = field(default_factory=dict)
    # 格式化函数名（从formatters模块查找）
    formatter: str = "format_generic_list"
    # 响应中列表数据的字段名（如 "list", "items", "stockStatusVoList"）
    response_key: Optional[str] = "list"
    # 默认分页大小
    page_size: int = 20
    # 是否为写操作
    is_write: bool = False
    # 写操作确认提示模板
    confirm_template: Optional[str] = None
    # 网关地址覆盖（奇门等走不同网关的接口使用）
    base_url: Optional[str] = None
    # 额外系统参数（如奇门的 target_app_key）
    system_params: Dict[str, Any] = field(default_factory=dict)
