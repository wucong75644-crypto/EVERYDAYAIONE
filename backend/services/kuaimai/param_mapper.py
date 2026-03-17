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

# 中文/常见别名 → 标准参数名（跨注册表通用）
# LLM 传入别名时自动转换，无需修改注册表和工具描述
PARAM_ALIASES: Dict[str, str] = {
    # 主商家编码
    "商家编码": "outer_id",
    "商品编码": "outer_id",
    "主商家编码": "outer_id",
    "编码": "outer_id",
    "货号": "outer_id",
    "商品货号": "outer_id",
    # 规格商家编码
    "规格商家编码": "sku_outer_id",
    "SKU商家编码": "sku_outer_id",
    "SKU编码": "sku_outer_id",
    "规格编码": "sku_outer_id",
    # 条码
    "条码": "code",
    "商品条码": "code",
    # 订单号
    "订单号": "order_id",
    "平台订单号": "order_id",
    "单号": "order_id",
    # 系统单号
    "系统单号": "system_id",
    "ERP单号": "system_id",
    "系统订单号": "system_id",
    # 买家
    "买家": "buyer",
    "买家昵称": "buyer",
    "客户": "buyer",
    # 仓库 / 店铺
    "仓库": "warehouse_id",
    "仓库ID": "warehouse_id",
    "店铺": "shop_ids",
    "店铺ID": "shop_ids",
    # 批量
    "多个编码": "outer_ids",
    "批量编码": "outer_ids",
    # 快递
    "快递单号": "express_no",
    "运单号": "express_no",
}


# 同义参数：用户角度相同含义，不同 API 用不同参数名
# 当 A 不在 param_map 但 B 在时，自动用 B 的映射
_PARAM_SYNONYMS: Dict[str, str] = {
    "sku_outer_id": "outer_id",
    "outer_id": "sku_outer_id",
    "sku_outer_ids": "outer_ids",
    "outer_ids": "sku_outer_ids",
}


def _resolve_aliases(
    user_params: Dict[str, Any], valid_keys: set,
) -> Dict[str, Any]:
    """别名解析：仅当 key 不在当前 action 有效参数集时才转换

    优先保留标准参数名，避免别名覆盖已有值。
    """
    resolved: Dict[str, Any] = {}
    for key, value in user_params.items():
        if key in valid_keys or key in _COMMON_PARAMS:
            resolved[key] = value
        elif key in PARAM_ALIASES:
            std_name = PARAM_ALIASES[key]
            if std_name not in resolved:
                resolved[std_name] = value
        else:
            resolved[key] = value
    return resolved


def map_params(
    entry: ApiEntry, user_params: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """将用户参数映射为API参数（带白名单校验 + 同义参数兜底）

    Args:
        entry: API注册条目
        user_params: LLM传入的用户参数

    Returns:
        (映射后的API参数字典, 无效参数警告列表)
    """
    mapped: Dict[str, Any] = {}
    warnings: List[str] = []
    valid_keys = set(entry.param_map.keys()) | _COMMON_PARAMS

    # 别名解析：中文/常见名 → 标准参数名
    user_params = _resolve_aliases(user_params, valid_keys)

    # 应用默认值
    for key, default in entry.defaults.items():
        mapped[key] = default

    # 映射用户参数（白名单校验 + 同义参数兜底）
    for user_key, value in user_params.items():
        if value is None:
            continue
        if user_key in entry.param_map:
            mapped[entry.param_map[user_key]] = value
        elif user_key in _COMMON_PARAMS:
            mapped[user_key] = value
        else:
            # 同义参数兜底：如 sku_outer_id 不在白名单但 outer_id 在
            synonym = _PARAM_SYNONYMS.get(user_key)
            if synonym and synonym in entry.param_map:
                mapped[entry.param_map[synonym]] = value
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
    mapped["pageSize"] = max(int(page_size), 20)

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
        # 波次拣货时间
        "pickStartTime", "pickEndTime",
        # 售后维修单
        "timeStart", "timeEnd",
        # 加工单
        "modifiedStart", "modifiedEnd",
        "productTimeStart", "productTimeEnd",
        "finishedTimeStart", "finishedTimeEnd",
        "createdStart", "createdEnd",
        # 货位进出记录
        "operateStartTime", "operateEndTime",
        # 分销
        "modifiedTimeStart", "modifiedTimeEnd",
        "updateTimeBegin", "updateTimeEnd",
        # 出入库记录
        "operateTimeBegin", "operateTimeEnd",
        # 订单操作日志
        "operateTimeStart",
        # 唯一码
        "receiveTimeStart", "receiveTimeEnd",
        "createStart", "createEnd",
        # 维修单付款
        "receivedTime", "operatorTime",
        # 库存修改时间
        "startStockModified", "endStockModified",
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
