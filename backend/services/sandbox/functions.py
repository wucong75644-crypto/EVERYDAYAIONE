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


def build_sandbox_executor(
    timeout: float = 120.0,
    max_result_chars: int = 8000,
    user_id: str = "",
    org_id: Optional[str] = None,
    conversation_id: str = "",
) -> SandboxExecutor:
    """构建沙盒执行器（纯计算引擎）

    沙盒只注册计算和输出能力，不注册数据获取函数。
    数据获取必须走 Agent 工具层（local_* > erp_* > fetch_all_pages）。

    文件输出：
    - LLM 代码写 df.to_excel(OUTPUT_DIR + "/报表.xlsx") 到 OUTPUT_DIR（workspace/下载/）
    - ossfs 自动同步到 OSS → 平台生成 CDN 下载链接
    - 用户在工作区"下载/"文件夹直接可见可下载

    已注册能力：
    - read_file: 读取 staging 目录下的预获取数据（仅限 staging/）
    - 标准库: pandas, math, datetime, Decimal, Counter, io, json

    Args:
        timeout: 执行超时（秒）
        max_result_chars: 结果最大字符数
        conversation_id: 会话ID（用于隔离输出目录）

    Returns:
        配置好的 SandboxExecutor 实例
    """
    from core.config import get_settings as _get_settings
    from pathlib import Path

    _file_settings = _get_settings()
    _conv_id = conversation_id or "default"

    # 1. 用户 workspace 目录（对标 OpenAI /mnt/data）
    _ws_base = Path(_file_settings.file_workspace_root).resolve()
    if org_id:
        _workspace_dir = str(_ws_base / "org" / org_id / user_id)
    elif user_id:
        _workspace_dir = str(
            _ws_base / "personal" / hashlib.md5(user_id.encode()).hexdigest()[:8]
        )
    else:
        _workspace_dir = str(_ws_base)

    # 2. 输出目录 → workspace 下的 "下载/" 文件夹（对标电脑的下载文件夹）
    _output_dir = str(Path(_workspace_dir) / "下载")

    # 3. staging 数据目录（local_db_export 写 parquet 到这里）
    _staging_dir = str(
        Path(_file_settings.file_workspace_root) / "staging" / _conv_id
    )

    # 4. 文件检测函数 — 生成 workspace CDN URL（不上传 OSS，文件已通过 ossfs 在 OSS 上）
    async def _auto_upload(filename: str, size: int) -> str:
        """生成文件的 CDN URL（不读文件内容，零内存开销）"""
        import mimetypes
        safe_name = Path(filename).name
        mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"

        # 直接算 workspace CDN URL（文件在 WORKSPACE_DIR/下载/ 下，ossfs 自动同步到 OSS）
        from core.config import get_settings as _cdn_settings
        _cs = _cdn_settings()
        if _cs.oss_cdn_domain:
            _ws_base = Path(_cs.file_workspace_root).resolve()
            _file_path = Path(_output_dir) / safe_name
            try:
                object_key = str(_file_path.relative_to(_ws_base))
                url = f"https://{_cs.oss_cdn_domain}/workspace/{object_key}"
                return (
                    f"✅ 文件已生成: {safe_name}\n"
                    f"[FILE]{url}|{safe_name}|{mime_type}|{size}[/FILE]"
                )
            except ValueError:
                pass

        # 兜底：无 CDN 配置时读文件上传 OSS
        try:
            from services.oss_service import get_oss_service
            _file_path = Path(_output_dir) / safe_name
            content = _file_path.read_bytes()
            ext = Path(safe_name).suffix.lstrip(".")
            oss = get_oss_service()
            result = oss.upload_bytes(
                content=content, user_id=user_id, ext=ext,
                category="generated", content_type=mime_type, org_id=org_id,
            )
            return (
                f"✅ 文件已生成: {safe_name}\n"
                f"[FILE]{result['url']}|{safe_name}|{mime_type}|{result['size']}[/FILE]"
            )
        except Exception as e:
            return f"❌ 文件处理失败: {safe_name} ({e})"

    executor = SandboxExecutor(
        timeout=timeout,
        max_result_chars=max_result_chars,
        output_dir=_output_dir,
        staging_dir=_staging_dir,
        workspace_dir=_workspace_dir,
        upload_fn=_auto_upload,
    )

    # read_file: 仅允许读取 staging 目录（对标 OpenAI Code Interpreter）
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
        if not path.startswith("staging/"):
            return (
                "❌ 沙盒内只能读取 staging 目录下的数据文件。"
                "请先用 local_db_export 或 fetch_all_pages 工具获取数据。"
            )
        if path.endswith(".parquet"):
            return (
                "❌ Parquet 文件不能用 read_file 读取，"
                "请用 pd.read_parquet(STAGING_DIR + '/文件名') 读取。"
            )
        # 直接读原始文件内容（不走 file_read 的行号格式化），
        # 这样 pandas 的 pd.read_json(io.StringIO(raw), lines=True) 能正确解析
        fe = _make_file_executor()
        target = fe.resolve_safe_path(path)
        if not target.exists():
            return f"❌ 文件不存在: {path}"
        return target.read_text(encoding=encoding)

    executor.register("read_file", _read_file)

    # upload_file 已删除 — 所有文件输出走 OUTPUT_DIR（workspace/下载/），ossfs 自动同步到 OSS

    return executor


def compute_code_hash(code: str) -> str:
    """计算代码 MD5 指纹（执行日志去重用）"""
    return hashlib.md5(code.strip().encode()).hexdigest()[:12]
