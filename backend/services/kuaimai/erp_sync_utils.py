"""
ERP 同步共享工具函数

从 erp_sync_handlers / erp_sync_master_handlers 提取的公共工具函数，
消除跨文件重复定义。

包含：时间格式化、数据转换、批量写入、API 限流、Detail 并发拉取。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any

from loguru import logger


# ── 时间格式化 ──────────────────────────────────────────


def _fmt_dt(dt: datetime) -> str:
    """yyyy-MM-dd HH:mm:ss（快麦API统一要求）"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_d(dt: datetime) -> str:
    """YYYY-MM-DD（收货/上架/售后/订单时间参数格式）"""
    return dt.strftime("%Y-%m-%d")


def _safe_ts(val: Any) -> str | None:
    """安全转换时间值（毫秒时间戳或字符串）→ ISO 字符串

    快麦API部分字段返回毫秒时间戳（如 1767457525000），
    另一些返回 ISO 字符串（如 '2026-01-03 15:25:25'），
    还有些返回字符串形式的毫秒时间戳（如 "946656000000"）。
    PostgreSQL TIMESTAMP 列无法接受裸毫秒数字。
    """
    if val is None:
        return None
    if isinstance(val, str):
        # 纯数字字符串 → 当作毫秒时间戳处理（如 "946656000000"）
        if val.isdigit() and len(val) >= 10:
            return _safe_ts(int(val))
        return val  # 已经是日期字符串，直接写入
    try:
        ts = int(val)
        # 超过 year-3000 的秒值一定是毫秒时间戳
        if ts > 32503680000:  # 3000-01-01 00:00:00 UTC in seconds
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(val)


def _ms_to_iso(val: Any) -> str | None:
    """毫秒时间戳 → ISO 8601 字符串（API返回stockModifiedTime为毫秒数）"""
    if val is None:
        return None
    try:
        ts = int(val) / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


# ── 数据转换 ──────────────────────────────────────────


def _pick(src: dict, *keys: str) -> dict:
    """从 dict 中提取存在且非 None 的键值对（用于 extra_json）"""
    return {k: src[k] for k in keys if k in src and src[k] is not None}


def _to_float(val: Any) -> float:
    """安全转 float（用于折扣分摊计算）"""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str | None:
    """清洗 HTML 标签（商品备注可能含 HTML）"""
    if not text:
        return text
    return _HTML_TAG_RE.sub("", text).strip()


# ── 批量写入 ──────────────────────────────────────────


async def _batch_upsert(
    db: Any, table: str, rows: list[dict], on_conflict: str,
    batch_size: int = 100,
) -> int:
    """通用批量 upsert（与 ErpSyncService.upsert_document_items 类似）"""
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            await db.table(table).upsert(batch, on_conflict=on_conflict).execute()
            total += len(batch)
        except Exception as e:
            logger.error(
                f"Upsert {table} failed | batch={i // batch_size} | "
                f"rows={len(batch)} | error={e}"
            )
    return total


# ── API 限流 ──────────────────────────────────────────


class _ApiRateLimiter:
    """全局 API 速率限制器（Leaky Bucket）

    Semaphore 只限并发数，无法控制 QPS（云服务器内网延迟 ~30ms 时 Sem(4)=133 req/s）。
    此限制器确保请求启动间隔 ≥ 1/max_qps 秒，多请求可并发执行但启动节奏受控。
    """

    def __init__(self, max_qps: float = 12.0) -> None:
        self._min_interval = 1.0 / max_qps
        self._lock: asyncio.Lock | None = None
        self._lock_loop_id: int | None = None
        self._last_request_time = 0.0

    def _get_lock(self) -> asyncio.Lock:
        """延迟创建 Lock，事件循环变化时自动重建"""
        loop_id = id(asyncio.get_event_loop())
        if self._lock is None or self._lock_loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop_id = loop_id
        return self._lock

    async def __aenter__(self):
        async with self._get_lock():
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_time)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()
        return self

    async def __aexit__(self, *args):
        pass


_API_SEM = _ApiRateLimiter(max_qps=12)
"""全局 API 限流器：≤12 req/s（低于 API 15 req/s 限额，留安全余量）。
所有 API 调用（detail / list 翻页）共享此限流器。"""


# ── Detail 并发拉取 ──────────────────────────────────


class _DetailResult:
    """_fetch_details 返回结果，包含成功列表和失败列表"""
    __slots__ = ("succeeded", "failed")

    def __init__(self) -> None:
        self.succeeded: list[tuple[dict, dict]] = []
        self.failed: list[dict] = []

    def __iter__(self):
        """向后兼容：for doc, detail in await _fetch_details(...)"""
        return iter(self.succeeded)


async def _fetch_details(
    client, method: str, docs: list[dict],
) -> _DetailResult:
    """并发获取单据详情（通过全局 _API_SEM 限流）

    Returns:
        _DetailResult: .succeeded 为成功的 (doc, detail) 列表，
                       .failed 为 detail 调用失败的 doc 列表。
    """

    async def _one(doc: dict):
        async with _API_SEM:
            try:
                detail = await client.request_with_retry(method, {"id": doc["id"]})
                return ("ok", doc, detail)
            except Exception as e:
                logger.warning(
                    f"Detail failed | method={method} | id={doc.get('id')} | error={e}"
                )
                return ("fail", doc, str(e))

    raw_results = await asyncio.gather(*[_one(d) for d in docs])

    result = _DetailResult()
    for r in raw_results:
        if r[0] == "ok":
            result.succeeded.append((r[1], r[2]))
        else:
            result.failed.append(r[1])
    return result
