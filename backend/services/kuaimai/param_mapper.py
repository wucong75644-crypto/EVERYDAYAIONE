"""
参数映射器

将LLM传入的用户友好参数名转换为快麦API实际参数名。
处理日期格式、分页默认值等通用逻辑。
白名单校验：未在 param_map 和通用参数中的参数不传入 API，返回警告。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from services.kuaimai.registry.base import ApiEntry

# 通用参数白名单（下划线风格，大脑友好）
# 大脑传 page/page_size → mapper 转为 API 的 pageNo/pageSize
_COMMON_PARAMS = {"action", "page", "page_size", "pageNo", "pageSize"}


def map_params(
    entry: ApiEntry, user_params: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """将用户参数映射为API参数（带白名单校验）

    Args:
        entry: API注册条目
        user_params: LLM传入的用户参数

    Returns:
        (映射后的API参数字典, 无效参数警告列表)
    """
    mapped: Dict[str, Any] = {}
    warnings: List[str] = []
    valid_keys = set(entry.param_map.keys()) | _COMMON_PARAMS

    # 应用默认值
    for key, default in entry.defaults.items():
        mapped[key] = default

    # 映射用户参数（白名单校验）
    for user_key, value in user_params.items():
        if value is None:
            continue
        if user_key in entry.param_map:
            mapped[entry.param_map[user_key]] = value
        elif user_key in _COMMON_PARAMS:
            mapped[user_key] = value
        else:
            warnings.append(user_key)

    # 统一分页参数（下划线/驼峰 → API 驼峰格式）
    mapped.pop("page", None)
    mapped.pop("page_size", None)
    page_no = (
        user_params.get("pageNo")
        or user_params.get("page")
        or 1
    )
    page_size = (
        user_params.get("page_size")
        or user_params.get("pageSize")
        or entry.page_size
    )
    mapped["pageNo"] = page_no
    mapped["pageSize"] = page_size

    # 处理日期：如果有start_date/end_date且未转换，补全时间部分
    _normalize_dates(mapped)

    return mapped, warnings


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
