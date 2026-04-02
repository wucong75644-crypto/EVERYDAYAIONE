"""
一次性订单数据补偿脚本

背景：3/25-3/30 期间订单同步使用旧接口 erp.trade.list.query，
该接口比 erp.trade.outstock.simple.query 每天少返回约 1400-1800 单。
3/29 切换到新接口后，增量同步只往前走，不会回补历史。

本脚本：只补缺失订单（跳过已有），批量写入，速度快很多。

用法：
    cd backend
    source venv/bin/activate
    python scripts/backfill_orders.py [--start 2026-03-25] [--end 2026-03-31] [--dry-run]
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("ENV_FILE", ".env")

ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"


async def main(start_date: str, end_date: str, dry_run: bool) -> None:
    from loguru import logger
    from core.config import get_settings
    from core.local_db import AsyncLocalDBClient
    from services.kuaimai.client import KuaiMaiClient
    from services.kuaimai.erp_sync_handlers import _build_order_rows
    from services.kuaimai.erp_sync_service import ErpSyncService
    from services.kuaimai.erp_sync_persistence import upsert_document_items

    settings = get_settings()

    db = AsyncLocalDBClient(settings.database_url)
    await db.open()
    client = KuaiMaiClient()
    svc = ErpSyncService(db, org_id=ORG_ID, client=client)

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    logger.info(
        f"订单补偿开始 | range={start_date}~{end_date} | "
        f"org_id={ORG_ID} | dry_run={dry_run}"
    )

    grand_total = 0
    cursor = start

    while cursor < end:
        shard_end = cursor + timedelta(days=1)
        label = cursor.strftime("%m-%d")

        # 1) 拿 DB 中这一天已有的 sid 集合
        pool = db._pool
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT doc_id FROM erp_document_items "
                "WHERE doc_type = 'order' AND org_id = %s "
                "AND pay_time >= %s AND pay_time < %s",
                (ORG_ID, cursor, shard_end),
            )
            rows = await cur.fetchall()
            db_sids = {r["doc_id"] if isinstance(r, dict) else r[0] for r in rows}

        # 2) 从 ERP 用 pay_time 维度逐页拉取，只处理 DB 中没有的
        missing_rows: list[dict[str, Any]] = []
        erp_total = 0
        skip_count = 0
        page = 0

        while True:
            page += 1
            resp = await client.request(
                "erp.trade.outstock.simple.query",
                {
                    "startTime": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                    "endTime": shard_end.strftime("%Y-%m-%d %H:%M:%S"),
                    "timeType": "pay_time",
                    "pageNo": page,
                    "pageSize": 200,
                },
            )
            if not resp:
                break
            docs = resp.get("list", [])
            if not docs:
                break
            if page == 1:
                erp_total = resp.get("total", 0)

            for doc in docs:
                sid = str(doc.get("sid", ""))
                if not sid:
                    continue

                if sid in db_sids:
                    skip_count += 1
                    continue

                rows = _build_order_rows(doc, svc)
                missing_rows.extend(rows)
                db_sids.add(sid)

            if len(docs) < 200:
                break

        missing_orders = len({r["doc_id"] for r in missing_rows})

        if dry_run:
            logger.info(
                f"[DRY-RUN] {label} | DB已有={len(db_sids) - missing_orders} | "
                f"ERP(pay)={erp_total} | 待补={missing_orders}"
            )
        else:
            if missing_rows:
                count = await upsert_document_items(db, missing_rows, org_id=ORG_ID)
                grand_total += count
                logger.info(
                    f"补偿完成 {label} | 补充={missing_orders}单({count}行) | "
                    f"跳过已有={skip_count}"
                )
            else:
                logger.info(f"补偿完成 {label} | 无缺失")

        cursor = shard_end

    if dry_run:
        logger.info("DRY-RUN 结束，未写入任何数据")
    else:
        logger.info(f"订单补偿全部完成 | total={grand_total}行")

    await client.close()
    await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补偿历史订单数据")
    parser.add_argument(
        "--start", default="2026-03-25",
        help="起始日期（含），默认 2026-03-25",
    )
    parser.add_argument(
        "--end", default="2026-03-31",
        help="截止日期（不含），默认 2026-03-31",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只查询对比，不写库",
    )
    args = parser.parse_args()
    asyncio.run(main(args.start, args.end, args.dry_run))
