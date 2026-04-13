"""ERP 平台映射同步：erp.item.outerid.list.get → erp_product_platform_map

Bug 1+2 修复 2026-04-11：
- limit(10000) 导致 78% SKU 长期未同步（Bug 1）
- except Exception 吞掉 token 失效告警（Bug 2）
- 增量化：按 ORDER BY COALESCE(checked_at, '1970-01-01') ASC 取最旧 1/4
- 异常分类：业务/payload超限/致命/未知 4 类处理
- 未知错误接入 erp_sync_dead_letter 死信队列异步重试

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from math import ceil
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.errors import (
    KuaiMaiBusinessError,
    KuaiMaiSignatureError,
    KuaiMaiTokenExpiredError,
)
from services.kuaimai.erp_sync_utils import _batch_upsert
from utils.time_context import now_cn

if TYPE_CHECKING:
    from services.kuaimai.client import KuaiMaiClient
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 常量 ─────────────────────────────────────────────────
# 每个 API 批次的 SKU 上限。
# 2026-04-12: 400→200。生产 24h 内 42 次 payload too large 降级（400 太大）。
# 实测 200 安全（响应 ~2.5MB，8MB 上限留 3.2 倍余量）。
_PLATFORM_MAP_BATCH_SIZE = 200
# SKU 平台映射过期天数。超过此天数的 checked_at 视为过期需重查。
# 24 小时 = 用户接受的"新上架平台感知延迟"上限。
_PLATFORM_MAP_STALE_DAYS = 1
# 半批降级最大递归深度（200 → 100 → 50 → 25 → 12 → 6 → 3 → 放弃）
_PLATFORM_MAP_SPLIT_MAX_DEPTH = 7
# 半批降级最小批次（小于此值不再分割，直接放弃此批）
_PLATFORM_MAP_SPLIT_MIN_SIZE = 3
# 续锁频率：每 N 个批次调一次 lock_extend_fn
_PLATFORM_MAP_LOCK_EXTEND_EVERY = 10
# 标记 checked_at 时 IN 子句的分块大小
_PLATFORM_MAP_UPDATE_CHUNK = 500


def _parse_platform_map_items(
    items: list[dict], rows: list[dict[str, Any]],
) -> None:
    """从 API 返回的 itemOuterIdInfos 解析出 platform_map 行，追加到 rows。"""
    for item in items:
        outer_id = item.get("outerId")
        if not outer_id:
            continue
        # API 返回 tbItemList 数组，每条是一个平台商品映射
        for tb in item.get("tbItemList") or []:
            num_iid = tb.get("numIid")
            if not num_iid:
                continue
            rows.append({
                "outer_id": outer_id,
                "num_iid": str(num_iid),
                "user_id": str(tb.get("userId", "")),
                "title": tb.get("title"),
                "sku_mappings": (
                    [{"skuOuterId": tb.get("skuOuterId"),
                      "skuNumIid": tb.get("skuId")}]
                    if tb.get("skuOuterId") else None
                ),
            })


async def _process_platform_map_batch(
    svc: "ErpSyncService",
    client: "KuaiMaiClient",
    batch: list[str],
    checked_ids: list[str],
    rows: list[dict[str, Any]],
    *,
    depth: int = 0,
) -> None:
    """处理一批 SKU 的平台映射查询，遇到 payload too large 自动半批降级。

    错误处理（Bug 2 修复）：
    - 成功响应：解析 items，整批标记 checked_at（返回的=有映射，没返回的=确认无）
    - 20150 整批无映射：业务正常，整批标记 checked_at
    - code=1 payload too large：递归半批重试，最深 _PLATFORM_MAP_SPLIT_MAX_DEPTH
    - TokenExpired/Signature：raise 让上层 consecutive_errors 涨 → healthcheck 告警
    - 其他业务错误：写入死信队列由 DLQ 异步重试（不阻塞本轮）
    - 网络/未知错误：跳过此批不标记，下轮 sync 自然重试

    Args:
        depth: 当前递归深度，0 = 顶层调用
    """
    try:
        data = await client.request_with_retry(
            "erp.item.outerid.list.get", {"outerIds": ",".join(batch)},
        )
        items = data.get("itemOuterIdInfos") or []
        _parse_platform_map_items(items, rows)
        # 整批 SKU 都算"已检查"
        checked_ids.extend(batch)

    except KuaiMaiBusinessError as e:
        # 业务正常：整批无映射
        if e.error_code == "20150":
            logger.debug(
                f"platform_map: batch all-empty (20150) | size={len(batch)}"
            )
            checked_ids.extend(batch)
            return

        # 响应超 8MB：自动半批降级
        if e.error_code == "1" and "Data length too large" in str(e):
            if (
                len(batch) <= _PLATFORM_MAP_SPLIT_MIN_SIZE
                or depth >= _PLATFORM_MAP_SPLIT_MAX_DEPTH
            ):
                logger.error(
                    f"platform_map: batch too small to split, giving up | "
                    f"size={len(batch)} | depth={depth} | "
                    f"sku_ids={batch}"
                )
                return
            mid = len(batch) // 2
            logger.warning(
                f"platform_map: payload too large, splitting | "
                f"{len(batch)} → {mid} + {len(batch) - mid} | depth={depth}"
            )
            await _process_platform_map_batch(
                svc, client, batch[:mid], checked_ids, rows, depth=depth + 1,
            )
            await _process_platform_map_batch(
                svc, client, batch[mid:], checked_ids, rows, depth=depth + 1,
            )
            return

        # 其他业务错误：写入死信队列异步重试（不标记 checked_at）
        # 不在主流程重试 → 不阻塞本轮 sync
        logger.warning(
            f"platform_map: business error → DLQ | "
            f"code={e.error_code} | size={len(batch)} | msg={e}"
        )
        await _enqueue_failed_batch_to_dlq(
            svc, batch, error_msg=f"code={e.error_code} msg={e}",
        )

    except (KuaiMaiTokenExpiredError, KuaiMaiSignatureError):
        # 致命错误：必须 raise，让 _update_sync_state_error 涨 consecutive_errors
        # → healthcheck 5 分钟内扫到 → 企微告警
        raise

    except Exception as e:
        # 网络/未知错误：跳过此批，不标记，下轮 sync 自然重试
        logger.error(
            f"platform_map: unexpected error | "
            f"type={type(e).__name__} | size={len(batch)} | error={e}"
        )


async def _enqueue_failed_batch_to_dlq(
    svc: "ErpSyncService", batch: list[str], *, error_msg: str,
) -> None:
    """把失败的 platform_map 批次写入死信队列异步重试。

    每批用 sorted SKU ids 的 hash 做 doc_id，保证相同批次的失败在 DLQ
    内被去重（DLQ 唯一索引 = doc_type + doc_id + org_id）。
    """
    try:
        # 生成稳定的 doc_id（同一批 SKU 始终生成同一 hash）
        sorted_ids = sorted(batch)
        joined = ",".join(sorted_ids)
        batch_hash = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
        doc_id = f"pm_batch_{batch_hash}"

        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db,
            doc_type="platform_map_batch",
            detail_method="erp.item.outerid.list.get",
            failed_docs=[{
                "id": doc_id,
                "sku_ids": sorted_ids,
            }],
            error_msg=error_msg,
            org_id=svc.org_id,
        )
    except Exception as e:
        logger.error(
            f"platform_map: failed to enqueue DLQ | "
            f"size={len(batch)} | error={e}"
        )


async def _select_skus_for_round(
    svc: "ErpSyncService",
) -> list[str]:
    """取本轮要处理的 SKU 列表（仅"未检查"+"过期"的 SKU）。

    2026-04-12 重构：从 ORDER BY COALESCE LIMIT 1/4 改为 WHERE stale/NULL。
    好处：
    - 只查真正需要的 SKU，不再用 ROUND_FRACTION 分轮
    - 如果全部 SKU 都已检查且未过期 → 返回空列表 → sync 直接 return 0
    - Scheduler 多入的队列任务会自然被 return 0 消化，不浪费 API
    """
    stale_cutoff = (
        now_cn() - timedelta(days=_PLATFORM_MAP_STALE_DAYS)
    ).isoformat()

    try:
        # 查询 1：未检查（新增 SKU，checked_at 是 NULL）
        null_res = await (
            svc.db.table("erp_product_skus")
            .select("sku_outer_id")
            .is_("platform_map_checked_at", "null")
            .execute()
        )
        # 查询 2：已过期（checked_at < 24h 前）
        stale_res = await (
            svc.db.table("erp_product_skus")
            .select("sku_outer_id")
            .lt("platform_map_checked_at", stale_cutoff)
            .execute()
        )
        # 合并去重
        seen: set[str] = set()
        sku_ids: list[str] = []
        for row in (null_res.data or []) + (stale_res.data or []):
            oid = row.get("sku_outer_id")
            if oid and oid not in seen:
                seen.add(oid)
                sku_ids.append(oid)
        return sku_ids
    except Exception as e:
        logger.warning(f"platform_map: failed to load SKU list | error={e}")
        return []


def _dedupe_platform_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """API 可能返回重复 (outer_id, num_iid)，去重保留首次出现。"""
    seen: set[tuple[str, str]] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        key = (row["outer_id"], row["num_iid"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)
    return unique_rows


async def _mark_skus_checked(
    svc: "ErpSyncService", checked_ids: list[str],
) -> None:
    """批量更新 platform_map_checked_at = now()，分块避免 IN 子句过长。"""
    if not checked_ids:
        return
    now_iso = now_cn().isoformat()
    for i in range(0, len(checked_ids), _PLATFORM_MAP_UPDATE_CHUNK):
        chunk = checked_ids[i:i + _PLATFORM_MAP_UPDATE_CHUNK]
        try:
            await (
                svc.db.table("erp_product_skus")
                .update({"platform_map_checked_at": now_iso})
                .in_("sku_outer_id", chunk)
                .execute()
            )
        except Exception as e:
            logger.warning(
                f"platform_map: mark checked_at failed | "
                f"chunk_size={len(chunk)} | error={e}"
            )


async def sync_platform_map(
    svc: "ErpSyncService", start: datetime, end: datetime,
) -> int:
    """平台映射增量同步

    设计（2026-04-12 重构）：
    - WHERE stale/NULL 查询：只取未检查 + 过期（> STALE_DAYS 天）的 SKU
    - 如果全部 SKU 都已检查且未过期 → return 0（自然去重，不浪费 API）
    - 异常分类处理（见 _process_platform_map_batch）

    调度行为：
    - Scheduler 每 6 小时触发一次 platform_map
    - 第 1 轮：处理所有 stale/NULL SKU（冷启动 ~10 分钟）
    - Scheduler 若多入了队列任务 → 第 2 轮发现 0 stale → return 0（秒级）
    - 24 小时后：所有 SKU 过期 → 再次全量

    API: erp.item.outerid.list.get
    - 输入参数 outerIds（SKU 编码逗号分隔，单批上限 ≤500，取 200 安全）
    - 返回 itemOuterIdInfos[] → 每条 outerId 的 tbItemList[] 平台商品列表
    - "整批 SKU 全无映射" 触发 20150；混合批静默跳过无效 SKU 不报错
    """
    sku_ids = await _select_skus_for_round(svc)
    if not sku_ids:
        logger.debug("platform_map: no stale/new SKUs, skip")
        return 0

    logger.info(
        f"platform_map: round start | "
        f"to_check={len(sku_ids)} | batch_size={_PLATFORM_MAP_BATCH_SIZE}"
    )

    client = svc._get_client()
    rows: list[dict[str, Any]] = []
    checked_ids: list[str] = []

    total_batches = ceil(len(sku_ids) / _PLATFORM_MAP_BATCH_SIZE)
    for batch_idx, i in enumerate(
        range(0, len(sku_ids), _PLATFORM_MAP_BATCH_SIZE)
    ):
        batch = sku_ids[i:i + _PLATFORM_MAP_BATCH_SIZE]
        await _process_platform_map_batch(svc, client, batch, checked_ids, rows)

        # 每 N 批续锁一次（防止全量 ~10 分钟超过 5 分钟锁 TTL）
        if (
            (batch_idx + 1) % _PLATFORM_MAP_LOCK_EXTEND_EVERY == 0
            and svc._lock_extend_fn
        ):
            try:
                await svc._lock_extend_fn()
            except Exception as e:
                logger.warning(
                    f"platform_map: lock extend failed | "
                    f"batch={batch_idx + 1}/{total_batches} | error={e}"
                )

    # 去重 + upsert
    unique_rows = _dedupe_platform_rows(rows)
    count = 0
    if unique_rows:
        count = await _batch_upsert(
            svc.db, "erp_product_platform_map", unique_rows,
            "outer_id,num_iid,org_id", org_id=svc.org_id,
        )

    # 标记 checked_at
    await _mark_skus_checked(svc, checked_ids)

    skipped = len(sku_ids) - len(checked_ids)
    logger.info(
        f"platform_map: round done | upserted={count} | "
        f"checked={len(checked_ids)} | skipped={skipped} | "
        f"to_check={len(sku_ids)}"
    )
    return count
