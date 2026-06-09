"""
viperp（销售主题报表）同步主流程

链路：
  1. 拿 org 的 viperp active 凭证
  2. 同时调 /report/sale/dimensions/finance/list（明细）
       和 /report/sale/dimensions/finance/getFinanceAmount（汇总）
  3. 把 62 字段每行映射到 erp_viperp_sale_finance 表（56+ 业务列 + raw_payload）
  4. UPSERT 入库（按 org_id+companyid+user_id+dimension+date_range 去重）
  5. 同时同步店铺-运营映射（erp_shop_operators + erp_operators）
  6. 跑 field_auditor 检测字段变化
  7. 更新 sync_log + credential

错误处理：
  - Cookie 失效 → 标记 expired + 推告警
  - HTTP/网络 → 记 sync_log failed
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import InvalidOperation
from typing import Any

from loguru import logger

from services.kuaimai_external import (
    credential_store,
    field_auditor,
    http_base,
    shop_operator_sync,
    wecom_alert,
)


LIST_URL = "https://erp.superboss.cc/report/sale/dimensions/finance/list"
AMOUNT_URL = "https://erp.superboss.cc/report/sale/dimensions/finance/getFinanceAmount"
VIPERP_MODULE_PATH = "/report/sale_multidimension_finance_next/"
VIPERP_ORIGIN = "https://erp.superboss.cc"
VIPERP_REFERER = "https://erp.superboss.cc/index.html"


# DB 列名中"我们自己加的辅助/系统列"（不从响应自动映射）
_SYSTEM_COLUMNS = frozenset({
    "id", "org_id", "kuaimai_company_id",
    "user_id",                # 来自 viperp.userId
    "date_range_start", "date_range_end",
    "dimension",
    "summary_amount", "summary_total",  # 来自 getFinanceAmount
    "raw_payload", "sync_batch_id",
    "created_at", "updated_at",
})


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
            return int(float(value))
        except (ValueError, TypeError):
            return None
    if col_type == "date":
        if isinstance(value, str) and len(value) >= 10:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return value
    return value


def _get_table_columns(db: Any, table_name: str) -> dict[str, str]:
    cols: dict[str, str] = {}
    with db.pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                """,
                (table_name,),
            )
            for r in cur.fetchall():
                cols[r["column_name"]] = r["data_type"]
    return cols


def _build_row_payload(
    *,
    response_row: dict,
    table_columns: dict[str, str],
    org_id: str,
    kuaimai_company_id: int,
    start: date,
    end: date,
    dimension: str,
    sync_batch_id: str,
    summary_amount: float | None = None,
    summary_total: int | None = None,
) -> dict | None:
    user_id = response_row.get("userId")
    if not user_id:
        return None
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return None

    payload: dict = {
        "org_id": org_id,
        "kuaimai_company_id": kuaimai_company_id,
        "user_id": user_id,
        "date_range_start": start,
        "date_range_end": end,
        "dimension": dimension,
        "raw_payload": response_row,
        "sync_batch_id": sync_batch_id,
        "summary_amount": summary_amount,
        "summary_total": summary_total,
        "updated_at": datetime.now().isoformat(),
    }

    # 自动映射同名列（响应字段名 == DB 列名）
    for col_name, col_type in table_columns.items():
        if col_name in _SYSTEM_COLUMNS:
            continue
        if col_name in response_row:
            payload[col_name] = _cast_to_db(col_type, response_row[col_name])

    return payload


def _create_sync_log(
    db: Any,
    *,
    org_id: str,
    source: str,
    sync_type: str,
    start: date,
    end: date,
) -> str:
    resp = db.table("kuaimai_sync_logs").insert({
        "org_id": org_id,
        "source": source,
        "sync_type": sync_type,
        "status": "running",
        "date_range_start": start.isoformat(),
        "date_range_end": end.isoformat(),
    }).execute()
    return resp.data[0]["id"]


def _finish_sync_log(
    db: Any,
    *,
    log_id: str,
    status: str,
    rows_synced: int = 0,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> None:
    db.table("kuaimai_sync_logs").update({
        "status": status,
        "finished_at": datetime.now().isoformat(),
        "rows_synced": rows_synced,
        "error_message": error_message,
        "metadata": metadata,
    }).eq("id", log_id).execute()


def _get_org_label(db: Any, org_id: str) -> str:
    resp = (
        db.table("organizations")
        .select("name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if resp.data:
        return resp.data.get("name", "未知")
    return "未知"


# ──────────────────────── 主入口 ────────────────────────


@dataclass
class SyncResult:
    success: bool
    log_id: str | None
    rows_synced: int = 0
    summary: dict | None = None
    shop_changes: dict | None = None
    error: str | None = None
    cookie_expired: bool = False


async def sync_viperp(
    db: Any,
    *,
    org_id: str,
    sync_type: str = "manual",
    start_date: date | None = None,
    end_date: date | None = None,
    dimension: str = "shop",
) -> SyncResult:
    """
    viperp 同步主流程（一个 org，一次）。

    viperp 跟 thinktank 不同：list 接口返回的是"时间段汇总"，不是按日。
    所以我们用 (start, end, dimension) 作为唯一键的一部分。
    """
    # 1. 凭证
    cred = credential_store.get_active_credential(
        db, org_id=org_id, source="viperp"
    )
    if not cred:
        logger.warning(f"viperp_sync 跳过 | 无 active 凭证 | org={org_id}")
        return SyncResult(success=False, log_id=None, error="无 active 凭证")

    # 2. 时间范围
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=7)

    log_id = _create_sync_log(
        db,
        org_id=org_id,
        source="viperp",
        sync_type=sync_type,
        start=start_date,
        end=end_date,
    )
    logger.info(
        f"viperp_sync 开始 | org={org_id} log={log_id} "
        f"range=[{start_date} ~ {end_date}] dim={dimension}"
    )

    # 3. 调接口
    client = http_base.KuaimaiWebClient(
        companyid=cred.kuaimai_company_id,
        cookie=cred.cookie_full or f"_censeid={cred.censeid_cookie}",
    )

    payload = _build_viperp_payload(
        start_date=start_date, end_date=end_date, dimension=dimension,
    )

    list_data: dict | None = None
    amount_data: dict | None = None

    try:
        # 主数据 list
        list_result = await client.post(
            url=LIST_URL,
            payload={**payload, "api_name": "report_sale_dimensions_finance_list"},
            module_path=VIPERP_MODULE_PATH,
            origin=VIPERP_ORIGIN,
            referer=VIPERP_REFERER,
        )
        list_data = list_result.json_body or {}

        # 汇总 getFinanceAmount
        amount_result = await client.post(
            url=AMOUNT_URL,
            payload={**payload, "api_name": "report_sale_dimensions_finance_getFinanceAmount"},
            module_path=VIPERP_MODULE_PATH,
            origin=VIPERP_ORIGIN,
            referer=VIPERP_REFERER,
        )
        amount_data = amount_result.json_body or {}

    except http_base.CookieExpiredError as e:
        credential_store.mark_expired(db, credential_id=cred.id, error_msg=str(e))
        _finish_sync_log(
            db, log_id=log_id, status="failed",
            error_message=f"cookie_expired: {e}",
        )
        org_label = _get_org_label(db, org_id)
        await wecom_alert.send_alert(
            org_id,
            f"⚠️ **快麦 viperp Cookie 失效** [{org_label}]\n\n"
            f"自动同步无法继续，请重新登录快麦后到后台粘贴新 cURL。\n\n"
            f"错误: `{e}`",
        )
        return SyncResult(
            success=False, log_id=log_id, error=str(e), cookie_expired=True,
        )
    except Exception as e:
        credential_store.record_sync_failure(db, credential_id=cred.id, error_msg=str(e))
        _finish_sync_log(db, log_id=log_id, status="failed", error_message=str(e))
        logger.error(f"viperp_sync 失败 | org={org_id} | err={e}")
        return SyncResult(success=False, log_id=log_id, error=str(e))
    finally:
        await client.close()

    # 提取数据
    rows = (list_data or {}).get("data", {}).get("list", []) if list_data else []
    summary_amount: float | None = None
    summary_total: int | None = None
    if amount_data:
        summary_obj = amount_data.get("data", {})
        if isinstance(summary_obj, dict):
            try:
                summary_amount = (
                    float(summary_obj["amount"]) if summary_obj.get("amount") else None
                )
            except (ValueError, TypeError):
                summary_amount = None
            try:
                summary_total = (
                    int(summary_obj["total"]) if summary_obj.get("total") else None
                )
            except (ValueError, TypeError):
                summary_total = None

    if not rows:
        _finish_sync_log(
            db, log_id=log_id, status="success", rows_synced=0,
            metadata={"summary_amount": summary_amount, "summary_total": summary_total},
        )
        credential_store.record_sync_success(db, credential_id=cred.id)
        logger.info(f"viperp_sync 完成（空数据）| org={org_id}")
        return SyncResult(success=True, log_id=log_id, rows_synced=0)

    # 4. UPSERT 业务数据
    sync_batch_id = str(uuid.uuid4())
    table_columns = _get_table_columns(db, "erp_viperp_sale_finance")

    upsert_count = 0
    skipped = 0
    for response_row in rows:
        row_payload = _build_row_payload(
            response_row=response_row,
            table_columns=table_columns,
            org_id=org_id,
            kuaimai_company_id=cred.kuaimai_company_id,
            start=start_date,
            end=end_date,
            dimension=dimension,
            sync_batch_id=sync_batch_id,
            summary_amount=summary_amount,
            summary_total=summary_total,
        )
        if row_payload is None:
            skipped += 1
            continue
        try:
            db.table("erp_viperp_sale_finance").upsert(
                row_payload,
                on_conflict=(
                    "org_id,kuaimai_company_id,user_id,dimension,"
                    "date_range_start,date_range_end"
                ),
            ).execute()
            upsert_count += 1
        except Exception as e:
            logger.error(
                f"viperp_sync upsert 失败 | "
                f"user_id={row_payload.get('user_id')} | err={e}"
            )

    logger.info(
        f"viperp_sync upsert 完成 | "
        f"org={org_id} | upsert={upsert_count} skip={skipped}"
    )

    # 5. 同步店铺-运营
    org_label = _get_org_label(db, org_id)
    shop_changes_result = None
    try:
        shop_changes_result = await shop_operator_sync.sync_shop_operators(
            db,
            org_id=org_id,
            company_label=org_label,
            kuaimai_company_id=cred.kuaimai_company_id,
            response_rows=rows,
            sync_batch_id=sync_batch_id,
        )
    except Exception as e:
        logger.error(f"viperp_sync shop_operator_sync 失败 | err={e}")

    # 6. 字段审计
    try:
        await field_auditor.audit_response(
            db,
            org_id=org_id,
            source="viperp",
            company_label=org_label,
            sample_row=rows[0],
            sync_batch_id=sync_batch_id,
        )
    except Exception as e:
        logger.error(f"viperp_sync field_auditor 失败 | err={e}")

    # 7. 收尾
    summary_dict = {
        "summary_amount": summary_amount,
        "summary_total": summary_total,
        "skipped_rows": skipped,
        "sync_batch_id": sync_batch_id,
    }
    if shop_changes_result and shop_changes_result.has_any:
        summary_dict["shop_changes_count"] = {
            "new_shops": len(shop_changes_result.new_shops),
            "operator_changes": len(shop_changes_result.operator_changes),
            "removed_shops": len(shop_changes_result.removed_shops),
            "new_operators_auto_bound": len(shop_changes_result.new_operators_auto_bound),
            "new_operators_unbound": len(shop_changes_result.new_operators_unbound),
            "binding_invalidated": len(shop_changes_result.binding_invalidated),
        }
    _finish_sync_log(
        db, log_id=log_id, status="success", rows_synced=upsert_count,
        metadata=summary_dict,
    )
    credential_store.record_sync_success(db, credential_id=cred.id)

    return SyncResult(
        success=True,
        log_id=log_id,
        rows_synced=upsert_count,
        summary=summary_dict,
        shop_changes=summary_dict.get("shop_changes_count"),
    )


# ──────────────────────── payload 构造 ────────────────────────


def _build_viperp_payload(
    *,
    start_date: date,
    end_date: date,
    dimension: str = "shop",
) -> dict:
    """viperp /report/sale/dimensions/finance/list 接口 payload。"""
    start_ms = int(time.mktime(start_date.timetuple()) * 1000)
    end_ms = int(time.mktime((end_date + timedelta(days=1)).timetuple()) * 1000) - 1
    return {
        "pageNo": "1",
        "pageSize": "500",
        "pageId": "1123",
        "queryFlag": dimension,
        "startTime": str(start_ms),
        "endTime": str(end_ms),
        "vipSign": "false",
        "sysStatus": "sys_consign",
        "sellerFlags": "",
        "tradeTypes": "",
        "excludeTradeTypes": "",
        "containTagIds": "",
        "exceptTagIds": "",
        "containType": "1",
        "exceptType": "1",
        "subTagIdsQueryFlag": "false",
        "userIds": "",
        "shopUkList": "",
        "warehouseIds": "",
        "isAccurate": "",
        "itemFlag": "0",
        "tradeSysStatus": "",
        "scalping": "",
        "sysSkuIds": "",
        "sysItemIds": "",
        "outerIds": "",
        "numIids": "",
        "platformItemNames": "",
        "platformSkuIdFlag": "0",
        "platformSkuIds": "",
        "cids": "",
        "itemBrandIds": "",
        "skuBrandIds": "",
        "containTradeOut": "true",
        "onlyTradeOut": "false",
        "containNonConsign": "true",
        "containCancel": "false",
        "destIds": "",
        "sourceIds": "",
        "taobaoIds": "",
        "supplyIds": "",
        "buyerNicks": "",
        "buyerNickSelectAll": "false",
        "expressIds": "",
        "logisticCompanyIds": "",
        "templateIds": "",
        "showProcessItemDetail": "0",
        "showGroupItemDetail": "0",
        "isOuterIdFuzzy": "0",
        "shipper": "",
        "queryByCake": "",
        "matchFlag": "1",
        "virtualFlag": "1",
        "excludeWorkOrderCloseAndNoneRefundWarehouse": "false",
        "showSuit": "0",
        "asTypes": "",
        "createdStartTime": "",
        "createdEndTime": "",
        "buyerNick": "",
        "classifyIds": "",
        "classifySkuIds": "",
        "itemTagIds": "",
        "itemTagQueryType": "0",
        "afterSaleTimeType": "finish",
        "authorType": "name",
        "authorText": "",
        "sysConsigned": "",
        "definedSearch": "",
        "skuCids": "",
        "categoryFilterType": "0",
        "provinceNames": "",
        "cityNames": "",
        "areaNames": "",
        "provinceCityAreaFilter": "{}",
        "street": "",
        "itemAttribute": "",
        "showSysItem": "0",
        "shouldSort": "false",
        "sortField": "",
        "sortType": "",
    }
