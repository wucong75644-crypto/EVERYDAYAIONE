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
    # 是否自动翻页拉取全量（适用于店铺、仓库等配置列表）
    fetch_all: bool = False
    # 是否为写操作
    is_write: bool = False
    # 写操作确认提示模板
    confirm_template: Optional[str] = None
    # 网关地址覆盖（奇门等走不同网关的接口使用）
    base_url: Optional[str] = None
    # 额外系统参数（如奇门的 target_app_key）
    system_params: Dict[str, Any] = field(default_factory=dict)
    # 每个用户参数的中文描述（key 与 param_map 的 key 对应）
    # 用于两步调用模式：Step 1 返回参数文档给 LLM
    param_docs: Dict[str, str] = field(default_factory=dict)
    # API 专属错误码（code → 描述），用于 Step 1 文档展示
    error_codes: Dict[str, str] = field(default_factory=dict)
    # 参数歧义消解提示（key = 参数名，value = 使用场景说明）
    # 在 param_doc Step 1 中展示，帮助 LLM 选对参数
    param_hints: Dict[str, str] = field(default_factory=dict)
    # 仅支持单值编码的API（如 warehouse_stock 不支持逗号分隔多个编码）
    # True: 不打包宽泛编码，但仍做双参数依次试
    single_code_only: bool = False
    # 零结果替代参数映射（key = 当前参数名，value = 替代参数名）
    # 当用 key 查询返回 0 条时，诊断建议改用 value 重试
    retry_alt_params: Dict[str, str] = field(default_factory=dict)


# 全局通用错误码（所有 API 共享）
GLOBAL_ERROR_CODES = {
    "1": "服务不可用，联系管理员",
    "9": "业务逻辑出错，联系管理员",
    "20": "缺少会话参数，检查签名是否完整",
    "22": "缺少应用键参数",
    "25": "签名无效",
    "50": "非法参数",
    "401": "资源异常或签名参数不完整",
}
