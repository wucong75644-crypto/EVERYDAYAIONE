"""
通用格式化工具

提供日期处理、分页信息、空结果处理等公共函数。
从原 service.py 迁移 _format_timestamp / _parse_date。
Phase 5B 新增: format_item_with_labels 标签映射表通用格式化。
"""

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 全局跳过字段（图片/系统ID等无业务价值的）
# 注意：code 不在此集合——很多 item 用 code 作为单号字段
# ---------------------------------------------------------------------------
_GLOBAL_SKIP: Set[str] = {
    "picPath", "skuPicPath", "itemPicPath",
    "sysItemId", "sysSkuId",
    "body", "forbiddenField", "solution", "subCode", "subMsg",
    "msg", "traceId",  # 网关级字段
    "companyId",  # 内部公司ID，无业务价值
}


def format_item_with_labels(
    item: Dict[str, Any],
    labels: Dict[str, str],
    skip: Set[str] | None = None,
    transforms: Dict[str, Callable] | None = None,
) -> str:
    """通用字段格式化：按标签表展示 + 未知非空字段兜底

    Args:
        item: API返回的单条数据
        labels: {API字段名: 中文标签} 有序映射
        skip: 额外跳过的字段（与_GLOBAL_SKIP合并）
        transforms: {字段名: 转换函数} 如状态码→中文

    Returns:
        " | " 分隔的格式化字符串，如 "名称: XX | 编码: YY | ..."
    """
    all_skip = _GLOBAL_SKIP | (skip or set())
    transforms = transforms or {}
    parts: list[str] = []

    # 1. 按标签表顺序展示已知字段
    for key, label in labels.items():
        val = item.get(key)
        if val is None or val == "":
            continue
        if key in transforms:
            val = transforms[key](val)
        parts.append(f"{label}: {val}")

    # 2. 未知字段兜底（防止未来API新增字段被遗漏）
    for key, val in item.items():
        if key in labels or key in all_skip:
            continue
        if val is None or val == "" or val == 0:
            continue
        if isinstance(val, (list, dict)):
            continue  # 嵌套数据由各formatter自行处理
        parts.append(f"{key}: {val}")

    return " | ".join(parts)


def format_timestamp(ts: Any) -> str:
    """将毫秒时间戳转换为可读日期

    Args:
        ts: 毫秒时间戳（int/str）或日期字符串

    Returns:
        格式化日期字符串 "yyyy-MM-dd HH:mm" 或原始值
    """
    if not ts:
        return ""
    if isinstance(ts, str):
        if ts.isdigit() and len(ts) >= 13:
            ts = int(ts)
        else:
            return ts
    if isinstance(ts, (int, float)):
        try:
            if ts > 1e12:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (OSError, ValueError):
            return str(ts)
    return str(ts)


def parse_date(date_str: Optional[str], is_end: bool = False) -> str:
    """解析日期字符串为完整时间戳

    Args:
        date_str: 日期字符串（yyyy-MM-dd）或 None
        is_end: 是否为结束日期（True则用23:59:59）

    Returns:
        "yyyy-MM-dd HH:mm:ss" 格式字符串
    """
    if date_str and len(date_str) == 10:
        suffix = " 23:59:59" if is_end else " 00:00:00"
        return date_str + suffix
    if date_str:
        return date_str
    # 默认值
    now = datetime.now()
    if is_end:
        return now.strftime("%Y-%m-%d %H:%M:%S")
    return (now - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")


# 通用格式化器字符预算（紧凑格式下约可容纳 20 条普通记录）
_MAX_GENERIC_CHARS = 4000
_MAX_GENERIC_ITEMS = 20


def format_generic_list(data: Any, entry: ApiEntry) -> str:
    """通用列表格式化器 — 按条目边界截断，保证每条数据完整"""
    if not isinstance(data, dict):
        return str(data)[:_MAX_GENERIC_CHARS]

    items = data.get(entry.response_key) if entry.response_key else None
    total = data.get("total", "")

    if isinstance(items, list):
        if not items:
            return f"{entry.description}：暂无数据"
        count = len(items)
        header = build_list_header(items, total, entry.description)

        # 逐条拼接，超预算则停止（保证每条完整）
        lines: list[str] = [header, ""]
        budget = _MAX_GENERIC_CHARS - len(header) - 20  # 预留尾部提示
        shown = 0
        for item in items[:_MAX_GENERIC_ITEMS]:
            line = "- " + json.dumps(item, ensure_ascii=False)
            if shown > 0 and (sum(len(ln) for ln in lines) + len(line)) > budget:
                break
            lines.append(line)
            shown += 1

        if shown < count:
            lines.append(f"\n（显示前{shown}条，共{total or count}条）")
        return "\n".join(lines)

    # 非列表响应 → 按 key-value 边界截断
    return _format_dict_safe(data, entry.description, _MAX_GENERIC_CHARS)


def format_generic_detail(data: Any, entry: ApiEntry) -> str:
    """通用详情格式化器 — 按 key-value 边界截断"""
    if not isinstance(data, dict):
        return str(data)[:_MAX_GENERIC_CHARS]
    return _format_dict_safe(data, entry.description, _MAX_GENERIC_CHARS)


def _format_dict_safe(data: Dict, desc: str, budget: int) -> str:
    """按 key-value 逐行输出 dict，超预算时停在完整行边界"""
    header = f"{desc}:\n"
    lines: list[str] = [header]
    used = len(header)
    for key, val in data.items():
        if key in _GLOBAL_SKIP:
            continue
        if val is None or val == "":
            continue
        if isinstance(val, (list, dict)):
            line = f"  {key}: {json.dumps(val, ensure_ascii=False)}"
        else:
            line = f"  {key}: {val}"
        if used + len(line) + 1 > budget:
            lines.append("  ...")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def build_list_header(
    items: List,
    total: Any,
    desc: str,
    page: int = 1,
) -> str:
    """构建列表结果的头部信息"""
    total_num = int(total) if total else len(items)
    if total_num == 0:
        return f"{desc}：暂无数据"
    return f"{desc} | 共{total_num}条，当前第{page}页，展示{len(items)}条"
