"""
ERP 翻页引擎

从 sandbox/functions.py 迁入，职责归属到工具层。
提供全量翻页查询能力，供 tool_executor._fetch_all_pages 调用。
"""

from typing import Any, Dict, Optional

from loguru import logger


# 已知的 API response_key（按优先级排列，"list" 兜底）
_KNOWN_RESPONSE_KEYS = (
    "list", "items", "stockStatusVoList", "itemSkus",
    "sellerCats", "classifies", "suppliers",
    "itemOuterIdInfos", "trades", "workOrders",
)


def extract_list(data: Dict[str, Any]) -> tuple:
    """从 API 响应中提取列表数据，自动探测 response_key

    Returns:
        (items, key) — 列表数据和命中的 key 名
    """
    for key in _KNOWN_RESPONSE_KEYS:
        items = data.get(key)
        if isinstance(items, list) and items:
            return items, key
    return [], "list"


async def paginate_erp(
    tool_name: str,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    max_pages: int = 200,
    _dispatcher: Any = None,
    _semaphore: Any = None,
) -> Dict[str, Any]:
    """ERP 全量翻页查询（自动翻页拉取全部数据）

    Args:
        tool_name: 工具名
        action: 操作名
        params: 查询参数
        max_pages: 最大翻页数（默认200，约2万条数据上限）
        _dispatcher: 注入的 ErpDispatcher 实例
        _semaphore: 并发控制信号量

    Returns:
        合并后的结果 dict（list 字段包含全部数据）
    """
    import asyncio

    if _dispatcher is None:
        return {"error": "ERP dispatcher 未初始化"}

    params = params or {}
    all_items: list = []
    page = 0
    page_size = int(params.get("page_size", 100))
    api_total = None

    while page < max_pages:
        page += 1
        page_params = {**params, "page": page, "page_size": page_size}

        if _semaphore:
            async with _semaphore:
                data = await _dispatcher.execute_raw(
                    tool_name, action, page_params,
                )
        else:
            data = await _dispatcher.execute_raw(
                tool_name, action, page_params,
            )

        if "error" in data:
            if all_items:
                logger.warning(
                    f"paginate_erp partial | page={page} | error={data['error']}"
                )
                break
            return data

        if page == 1 and api_total is None:
            raw_total = data.get("total")
            if raw_total is None:
                raw_total = data.get("totalCount")
            if raw_total is not None:
                try:
                    api_total = int(raw_total)
                except (ValueError, TypeError):
                    pass

        items, _key = extract_list(data)
        all_items.extend(items)

        if len(items) < page_size:
            break

    logger.info(
        f"paginate_erp | tool={tool_name} action={action} "
        f"pages={page} total={len(all_items)}"
    )

    result: Dict[str, Any] = {"list": all_items, "total": len(all_items)}
    if api_total is not None:
        result["api_total"] = api_total
    if page >= max_pages:
        result["warning"] = f"已达翻页上限({max_pages}页)，数据可能不完整"
    return result
