"""
参数映射器

将LLM传入的用户友好参数名转换为快麦API实际参数名。
处理日期格式、分页默认值等通用逻辑。
"""

from datetime import datetime, timedelta
from typing import Any, Dict

from services.kuaimai.registry.base import ApiEntry


def map_params(entry: ApiEntry, user_params: Dict[str, Any]) -> Dict[str, Any]:
    """将用户参数映射为API参数

    Args:
        entry: API注册条目
        user_params: LLM传入的用户参数

    Returns:
        映射后的API参数字典
    """
    mapped: Dict[str, Any] = {}

    # 应用默认值
    for key, default in entry.defaults.items():
        mapped[key] = default

    # 映射用户参数
    for user_key, value in user_params.items():
        if value is None:
            continue
        api_key = entry.param_map.get(user_key, user_key)
        mapped[api_key] = value

    # 处理分页
    if "pageNo" not in mapped and "page" in user_params:
        mapped["pageNo"] = user_params["page"]
    elif "pageNo" not in mapped:
        mapped["pageNo"] = 1

    if "pageSize" not in mapped:
        mapped["pageSize"] = entry.page_size

    # 处理日期：如果有start_date/end_date且未转换，补全时间部分
    _normalize_dates(mapped)

    return mapped


def _normalize_dates(params: Dict[str, Any]) -> None:
    """标准化日期参数格式"""
    date_keys = [
        "startTime", "endTime",
        "startModified", "endModified",
        "startApplyTime", "endApplyTime",
        "startFinished", "endFinished",
        "timeBegin", "timeEnd",
        "startCreated", "endCreated",
    ]
    for key in date_keys:
        val = params.get(key)
        if not val or not isinstance(val, str):
            continue
        # 如果只有日期部分 yyyy-MM-dd，补全时间
        if len(val) == 10:
            if "start" in key.lower() or "begin" in key.lower():
                params[key] = f"{val} 00:00:00"
            else:
                params[key] = f"{val} 23:59:59"


def build_default_date_range(days: int = 7) -> Dict[str, str]:
    """生成默认的日期范围参数"""
    now = datetime.now()
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    return {"start": start, "end": end}
