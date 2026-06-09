#!/usr/bin/env python3
"""手动触发 viperp（销售主题报表）同步。

用法：
    cd backend && venv/bin/python scripts/sync_viperp.py \\
        --org-id eadc4c11-7e83-4279-a849-cfe0cbf6982b \\
        [--start 2026-06-02 --end 2026-06-09] \\
        [--type manual]
"""

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.database import get_db
from services.kuaimai_external import viperp_sync


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--start", type=parse_date, default=None)
    ap.add_argument("--end", type=parse_date, default=None)
    ap.add_argument(
        "--type",
        choices=["daily", "manual", "backfill"],
        default="manual",
    )
    ap.add_argument(
        "--dimension",
        choices=["shop", "sku", "item", "day", "brand", "distributor"],
        default="shop",
    )
    args = ap.parse_args()

    db = get_db()
    result = await viperp_sync.sync_viperp(
        db,
        org_id=args.org_id,
        sync_type=args.type,
        start_date=args.start,
        end_date=args.end,
        dimension=args.dimension,
    )

    logger.info("─" * 60)
    if result.success:
        logger.info(f"✅ 同步成功 | 落库 {result.rows_synced} 行")
        if result.summary:
            sa = result.summary.get("summary_amount")
            st = result.summary.get("summary_total")
            logger.info(f"   汇总: amount={sa} total={st}")
        if result.shop_changes:
            logger.info(f"   店铺变化: {result.shop_changes}")
        logger.info(f"   sync_log id: {result.log_id}")
        return 0
    else:
        logger.error(f"❌ 同步失败 | error={result.error}")
        if result.cookie_expired:
            logger.error("   → Cookie 已过期，请重新登录快麦后到后台配置")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
