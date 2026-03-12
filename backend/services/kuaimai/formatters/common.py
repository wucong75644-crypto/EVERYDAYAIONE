"""
通用格式化工具

提供日期处理、分页信息、空结果处理等公共函数。
从原 service.py 迁移 _format_timestamp / _parse_date。
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.kuaimai.registry.base import ApiEntry


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


def format_generic_list(data: Any, entry: ApiEntry) -> str:
    """通用列表格式化器"""
    if not isinstance(data, dict):
        return str(data)[:2000]

    items = data.get(entry.response_key) if entry.response_key else None
    total = data.get("total", "")

    if isinstance(items, list):
        if not items:
            return f"{entry.description}：暂无数据"
        count = len(items)
        text = json.dumps(items[:5], ensure_ascii=False, indent=2)
        if len(text) > 2000:
            text = text[:2000] + "\n..."
        header = f"查询到 {total or count} 条结果"
        if total and int(total) > count:
            header += f"（显示前{count}条）"
        return f"{header}\n\n{text}"

    text = json.dumps(data, ensure_ascii=False, indent=2)
    return text[:2000]


def format_generic_detail(data: Any, entry: ApiEntry) -> str:
    """通用详情格式化器"""
    if not isinstance(data, dict):
        return str(data)[:2000]
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return f"{entry.description}:\n{text[:2000]}"


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
