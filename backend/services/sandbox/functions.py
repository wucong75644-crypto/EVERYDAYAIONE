"""
沙盒数据源函数

提供沙盒内可调用的外部数据源。作为插件注册到 SandboxExecutor。
每个函数都是 async，沙盒代码通过 await 调用。
"""

import asyncio
import hashlib
import time as _time
from typing import Any, Dict, Optional

from loguru import logger

from services.sandbox.executor import SandboxExecutor


async def erp_query(
    tool_name: str,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    _dispatcher: Any = None,
) -> Dict[str, Any]:
    """ERP 单页查询（返回原始 dict）

    Args:
        tool_name: 工具名（如 erp_trade_query）
        action: 操作名（如 order_list）
        params: 查询参数
        _dispatcher: 注入的 ErpDispatcher 实例

    Returns:
        API 原始响应 dict
    """
    if _dispatcher is None:
        return {"error": "ERP dispatcher 未初始化"}
    params = params or {}
    return await _dispatcher.execute_raw(tool_name, action, params)


# 已知的 API response_key（按优先级排列，"list" 兜底）
_KNOWN_RESPONSE_KEYS = (
    "list", "items", "stockStatusVoList", "itemSkus",
    "sellerCats", "classifies", "suppliers",
    "itemOuterIdInfos", "trades", "workOrders",
)


def _extract_list(data: Dict[str, Any]) -> tuple:
    """从 API 响应中提取列表数据，自动探测 response_key

    Returns:
        (items, key) — 列表数据和命中的 key 名
    """
    for key in _KNOWN_RESPONSE_KEYS:
        items = data.get(key)
        if isinstance(items, list) and items:
            return items, key
    # 全部 key 都没命中或为空列表 → 返回空
    return [], "list"


async def erp_query_all(
    tool_name: str,
    action: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    max_pages: int = 200,
    _dispatcher: Any = None,
    _semaphore: Optional[asyncio.Semaphore] = None,
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
    if _dispatcher is None:
        return {"error": "ERP dispatcher 未初始化"}

    params = params or {}
    all_items = []
    page = 0
    page_size = int(params.get("page_size", 100))
    api_total = None  # API 第一页返回的 total 字段

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
                # 已拉取部分数据，返回已有数据 + 警告
                logger.warning(
                    f"erp_query_all partial | page={page} | error={data['error']}"
                )
                break
            return data

        # 第一页读取 API 返回的 total 字段
        if page == 1 and api_total is None:
            raw_total = data.get("total")
            if raw_total is None:
                raw_total = data.get("totalCount")
            if raw_total is not None:
                try:
                    api_total = int(raw_total)
                except (ValueError, TypeError):
                    pass

        items, key = _extract_list(data)
        all_items.extend(items)

        # 终止条件：返回数 < pageSize → 最后一页
        if len(items) < page_size:
            break

    logger.info(
        f"erp_query_all | tool={tool_name} action={action} "
        f"pages={page} total={len(all_items)}"
    )

    result = {"list": all_items, "total": len(all_items)}
    if api_total is not None:
        result["api_total"] = api_total
    if page >= max_pages:
        result["warning"] = f"已达翻页上限({max_pages}页)，数据可能不完整"
    return result


async def sandbox_web_search(query: str) -> str:
    """沙盒内互联网搜索

    Args:
        query: 搜索关键词

    Returns:
        搜索结果文本
    """
    from services.intent_router import IntentRouter

    if not query:
        return "搜索查询不能为空"

    router = IntentRouter()
    try:
        result = await router.execute_search(
            query=query, user_text=query, system_prompt=None,
        )
        return result or f"搜索「{query}」未找到相关结果"
    finally:
        await router.close()


async def sandbox_search_knowledge(query: str) -> str:
    """沙盒内知识库查询

    Args:
        query: 查询关键词

    Returns:
        知识库匹配结果文本
    """
    from services.knowledge_service import search_relevant

    if not query:
        return "查询关键词不能为空"

    items = await search_relevant(query=query, limit=5)
    if not items:
        return f"知识库中未找到与「{query}」相关的经验"

    lines = []
    for item in items:
        title = item.get("title", "")
        content = item.get("content", "")
        lines.append(f"- {title}: {content}")
    return "\n".join(lines)


def build_sandbox_executor(
    dispatcher: Any = None,
    api_concurrency: int = 10,
    timeout: float = 120.0,
    max_result_chars: int = 8000,
    max_pages: int = 200,
    user_id: str = "",
    org_id: Optional[str] = None,
) -> SandboxExecutor:
    """构建沙盒执行器并注册所有数据源

    Args:
        dispatcher: ErpDispatcher 实例（可选，无则 ERP 函数返回错误）
        api_concurrency: API 并发限制
        timeout: 执行超时（秒）
        max_result_chars: 结果最大字符数
        max_pages: erp_query_all 最大翻页数

    Returns:
        配置好的 SandboxExecutor 实例
    """
    executor = SandboxExecutor(
        timeout=timeout,
        max_result_chars=max_result_chars,
    )

    # API 并发信号量
    semaphore = asyncio.Semaphore(api_concurrency)

    # 注册 ERP 查询函数（闭包绑定 dispatcher）
    async def _erp_query(
        tool_name: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await erp_query(
            tool_name, action, params, _dispatcher=dispatcher,
        )

    async def _erp_query_all(
        tool_name: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages_override: int = max_pages,
    ) -> Dict[str, Any]:
        return await erp_query_all(
            tool_name, action, params,
            max_pages=max_pages_override,
            _dispatcher=dispatcher,
            _semaphore=semaphore,
        )

    executor.register("erp_query", _erp_query)
    executor.register("erp_query_all", _erp_query_all)
    executor.register("web_search", sandbox_web_search)
    executor.register("search_knowledge", sandbox_search_knowledge)

    # 注册文件操作函数（闭包绑定用户信息，沙盒内可 await 调用）
    from core.config import get_settings as _get_settings
    from services.file_executor import FileExecutor as _FileExecutor

    _file_settings = _get_settings()

    def _make_file_executor() -> "_FileExecutor":
        return _FileExecutor(
            workspace_root=_file_settings.file_workspace_root,
            user_id=user_id,
            org_id=org_id,
        )

    async def _read_file(path: str, encoding: str = "utf-8") -> str:
        return await _make_file_executor().file_read(path, encoding=encoding)

    async def _write_file(
        path: str, content: str, mode: str = "overwrite",
    ) -> str:
        return await _make_file_executor().file_write(path, content, mode=mode)

    async def _list_dir(path: str = ".") -> str:
        return await _make_file_executor().file_list(path)

    executor.register("read_file", _read_file)
    executor.register("write_file", _write_file)
    executor.register("list_dir", _list_dir)

    return executor


def compute_code_hash(code: str) -> str:
    """计算代码 MD5 指纹（执行日志去重用）"""
    return hashlib.md5(code.strip().encode()).hexdigest()[:12]
