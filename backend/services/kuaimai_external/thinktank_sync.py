"""
智库（kmzk）同步主流程

链路：
  1. 拿 org 的 active 凭证
  2. 调智库 /kmzk/profit/report/shop 拉数据
  3. 把 338 字段每行映射到 erp_thinktank_profit_shop 表的 98 个业务列 + raw_payload
  4. UPSERT 入库（按 org_id+companyid+shop_uni_id+date_range 去重）
  5. 跑 field_auditor 检测字段变化
  6. 更新 sync_log + credential 状态

错误处理：
  - Cookie 失效 → 标记 credential expired + 推告警
  - 网络/HTTP 错误 → 记录 sync_log failed，下次重试
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

from services.kuaimai_external import (
    credential_store,
    field_auditor,
    http_base,
    wecom_alert,
)


THINKTANK_URL = "https://erp.superboss.cc/kmzk/profit/report/shop"
THINKTANK_MODULE_PATH = "/think_tank/profit_shop/"
THINKTANK_ORIGIN = "https://erp.superboss.cc"
THINKTANK_REFERER = "https://erp.superboss.cc/index.html"


# 业务表的"我们自己加的辅助/系统列"，不来自响应
_SYSTEM_COLUMNS = frozenset({
    "id",
    "org_id",
    "kuaimai_company_id",
    "shop_uni_id",    # 我们的小写命名，跟响应里的同名 shop_uni_id 重复但 DB 列就叫这个
    "date_range",     # 跟响应同名 date_range，会自动映射
    "raw_payload",
    "sync_batch_id",
    "created_at",
    "updated_at",
})


# 已知的 numeric 列名前缀/字段（响应是 string，DB 是 NUMERIC，需要转）
def _cast_to_db(col_type: str, value: Any) -> Any:
    """根据列的 PG 类型把响应值转成 DB 接受的类型。"""
    if value is None or value == "":
        return None
    if col_type in ("numeric", "double precision", "real"):
        try:
            return float(value) if not isinstance(value, (int, float)) else value
        except (ValueError, TypeError, InvalidOperation):
            return None
    if col_type in ("integer", "bigint", "smallint"):
        try:
            return int(float(value))  # "5" / 5 / 5.0 都接受
        except (ValueError, TypeError):
            return None
    if col_type == "date":
        if isinstance(value, str) and len(value) >= 10:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return value
    # text / character varying / jsonb / boolean 等直接传
    return value


async def _get_table_columns(db: Any, table_name: str) -> dict[str, str]:
    """反射拿表的 {列名: 数据类型}。async DB pool。"""
    cols: dict[str, str] = {}
    async with db.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                """,
                (table_name,),
            )
            for r in await cur.fetchall():
                cols[r["column_name"]] = r["data_type"]
    return cols


def _build_row_payload(
    *,
    response_row: dict,
    table_columns: dict[str, str],
    org_id: str,
    kuaimai_company_id: int,
    sync_batch_id: str,
) -> dict | None:
    """
    把响应一行映射到 DB 的列。

    Returns:
        准备 UPSERT 的 dict；如果缺关键字段（shop_uni_id / date_range）返回 None
    """
    # 关键字段必须存在
    shop_uni_id = response_row.get("shop_uni_id") or response_row.get("shopUniId")
    date_range_str = response_row.get("date_range") or response_row.get("time")
    if not shop_uni_id or not date_range_str:
        return None

    try:
        stat_date = datetime.strptime(str(date_range_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

    payload: dict = {
        "org_id": org_id,
        "kuaimai_company_id": kuaimai_company_id,
        "shop_uni_id": str(shop_uni_id),
        "date_range": stat_date,
        "raw_payload": response_row,
        "sync_batch_id": sync_batch_id,
        "updated_at": datetime.now().isoformat(),
    }

    # 自动匹配同名列：响应字段名 == DB 列名 的字段都自动放进 payload
    for col_name, col_type in table_columns.items():
        if col_name in _SYSTEM_COLUMNS:
            continue
        if col_name in response_row:
            payload[col_name] = _cast_to_db(col_type, response_row[col_name])

    return payload


async def _create_sync_log(
    db: Any,
    *,
    org_id: str,
    source: str,
    sync_type: str,
    start: date,
    end: date,
) -> str:
    """创建同步日志，返回 log id。"""
    resp = await db.table("kuaimai_sync_logs").insert({
        "org_id": org_id,
        "source": source,
        "sync_type": sync_type,
        "status": "running",
        "date_range_start": start.isoformat(),
        "date_range_end": end.isoformat(),
    }).execute()
    return resp.data[0]["id"]


async def _finish_sync_log(
    db: Any,
    *,
    log_id: str,
    status: str,
    rows_synced: int = 0,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> None:
    """更新同步日志为最终状态。"""
    await (
        db.table("kuaimai_sync_logs")
        .update({
            "status": status,
            "finished_at": datetime.now().isoformat(),
            "rows_synced": rows_synced,
            "error_message": error_message,
            "metadata": metadata,
        })
        .eq("id", log_id)
        .execute()
    )


async def _get_org_label(db: Any, org_id: str) -> str:
    """拿企业可读名（用于告警）。"""
    resp = await (
        db.table("organizations")
        .select("name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if resp and resp.data:
        return resp.data.get("name", "未知")
    return "未知"


# ──────────────────────── 主入口 ────────────────────────


@dataclass
class SyncResult:
    success: bool
    log_id: str | None
    rows_synced: int = 0
    error: str | None = None
    cookie_expired: bool = False


async def sync_thinktank(
    db: Any,
    *,
    org_id: str,
    sync_type: str = "manual",
    start_date: date | None = None,
    end_date: date | None = None,
) -> SyncResult:
    """
    智库同步主流程（一个 org，一次）。

    Args:
        db: 同步 DB（注意不是 OrgScopedDB，我们显式传 org_id）
        org_id: 企业 ID
        sync_type: daily / manual / backfill
        start_date: 默认 = end_date - 7d
        end_date: 默认 = 今天

    Returns:
        SyncResult
    """
    # 1. 凭证
    cred = await credential_store.get_active_credential(
        db, org_id=org_id, source="thinktank"
    )
    if not cred:
        logger.warning(
            f"thinktank_sync 跳过 | 无 active 凭证 | org={org_id}"
        )
        return SyncResult(success=False, log_id=None, error="无 active 凭证")

    # 2. 时间范围
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=7)

    # 3. 同步日志
    log_id = await _create_sync_log(
        db,
        org_id=org_id,
        source="thinktank",
        sync_type=sync_type,
        start=start_date,
        end=end_date,
    )
    logger.info(
        f"thinktank_sync 开始 | org={org_id} log={log_id} "
        f"range=[{start_date} ~ {end_date}]"
    )

    # 4. 构造请求 + 调用快麦
    payload = _build_thinktank_payload(
        start_date=start_date,
        end_date=end_date,
    )

    client = http_base.KuaimaiWebClient(
        companyid=cred.kuaimai_company_id,
        cookie=cred.cookie_full or f"_censeid={cred.censeid_cookie}",
    )
    try:
        result = await client.post(
            url=THINKTANK_URL,
            payload=payload,
            module_path=THINKTANK_MODULE_PATH,
            origin=THINKTANK_ORIGIN,
            referer=THINKTANK_REFERER,
        )
    except http_base.CookieExpiredError as e:
        await credential_store.mark_expired(db, credential_id=cred.id, error_msg=str(e))
        await _finish_sync_log(
            db,
            log_id=log_id,
            status="failed",
            error_message=f"cookie_expired: {e}",
        )
        org_label = await _get_org_label(db, org_id)
        await wecom_alert.send_alert(
            org_id,
            f"⚠️ **快麦智库 Cookie 失效** [{org_label}]\n\n"
            f"自动同步任务无法继续，请重新登录快麦后到后台粘贴新的 cURL。\n\n"
            f"错误: `{e}`",
        )
        return SyncResult(
            success=False, log_id=log_id, error=str(e), cookie_expired=True
        )
    except Exception as e:
        await credential_store.record_sync_failure(
            db, credential_id=cred.id, error_msg=str(e)
        )
        await _finish_sync_log(
            db, log_id=log_id, status="failed", error_message=str(e)
        )
        logger.error(f"thinktank_sync 失败 | org={org_id} | err={e}")
        return SyncResult(success=False, log_id=log_id, error=str(e))
    finally:
        await client.close()

    rows = (result.json_body or {}).get("data", {}).get("list", [])
    if not rows:
        await _finish_sync_log(db, log_id=log_id, status="success", rows_synced=0)
        await credential_store.record_sync_success(db, credential_id=cred.id)
        logger.info(f"thinktank_sync 完成（空数据）| org={org_id}")
        return SyncResult(success=True, log_id=log_id, rows_synced=0)

    # 5. UPSERT
    sync_batch_id = str(uuid.uuid4())
    table_columns = await _get_table_columns(db, "erp_thinktank_profit_shop")

    upsert_count = 0
    skipped = 0
    for response_row in rows:
        row_payload = _build_row_payload(
            response_row=response_row,
            table_columns=table_columns,
            org_id=org_id,
            kuaimai_company_id=cred.kuaimai_company_id,
            sync_batch_id=sync_batch_id,
        )
        if row_payload is None:
            skipped += 1
            continue
        try:
            await db.table("erp_thinktank_profit_shop").upsert(
                row_payload,
                on_conflict="org_id,kuaimai_company_id,shop_uni_id,date_range",
            ).execute()
            upsert_count += 1
        except Exception as e:
            logger.error(
                f"thinktank_sync upsert 失败 | "
                f"shop={row_payload.get('shop_uni_id')} "
                f"date={row_payload.get('date_range')} | err={e}"
            )

    logger.info(
        f"thinktank_sync upsert 完成 | "
        f"org={org_id} | upsert={upsert_count} skip={skipped}"
    )

    # 6. 字段审计（用第一行做样本）
    try:
        await field_auditor.audit_response(
            db,
            org_id=org_id,
            source="thinktank",
            company_label=await _get_org_label(db, org_id),
            sample_row=rows[0],
            sync_batch_id=sync_batch_id,
        )
    except Exception as e:
        logger.error(f"thinktank_sync field_auditor 失败 | err={e}")
        # 字段审计失败不影响主同步

    # 7. 收尾
    await _finish_sync_log(
        db,
        log_id=log_id,
        status="success",
        rows_synced=upsert_count,
        metadata={
            "skipped_rows": skipped,
            "sync_batch_id": sync_batch_id,
        },
    )
    await credential_store.record_sync_success(db, credential_id=cred.id)
    return SyncResult(success=True, log_id=log_id, rows_synced=upsert_count)


# ──────────────────────── payload 构造 ────────────────────────


def _build_thinktank_payload(*, start_date: date, end_date: date) -> dict:
    """智库 /kmzk/profit/report/shop 接口 payload。"""
    start_ms = int(time.mktime(start_date.timetuple()) * 1000)
    end_ms = int(time.mktime((end_date + timedelta(days=1)).timetuple()) * 1000) - 1
    return {
        "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
        "groupTypeSum": "",
        "sysStatus": "1",
        "startTime": str(start_ms),
        "endTime": str(end_ms),
        "shopUniIds": "",                     # 留空 = 全部店铺（POC 验证）
        "sortFieldName": "insert_date",
        "sortFieldOrder": "asc",
        "appointReportRecordId": "",
        "formulaId": "658",
        "ruleId": "230290901203812352",
        "showDimension": "0",
        "dateShowType": "0",
        "showSuit": "0",
        "excludeNonConsign": "0",
        "excludeVirtual": "0",
        "excludeClosedRefund": "0",
        "excludeUnSysConsignRefund": "0",
        "refundSumType": "0",
        "consignBeforeRate": "",
        "consignAfterRate": "",
        "consignBeforeCostRate": "",
        "consignAfterCostRate": "",
        "freightCalType": "0",
        "freightEstimateCost": "",
        "costEstimateRuleId": "",
        "costType": "0",
        "isTrusted": "true",
    }
