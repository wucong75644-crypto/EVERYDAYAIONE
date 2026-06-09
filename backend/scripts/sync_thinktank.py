#!/usr/bin/env python3
"""
手动触发智库同步（一个 org）

用法：
    cd backend && venv/bin/python scripts/sync_thinktank.py \\
        --org-id eadc4c11-7e83-4279-a849-cfe0cbf6982b \\
        [--start 2026-05-25 --end 2026-05-31] \\
        [--type manual]

前置：
    该 org 必须先通过 cURL 粘贴方式配置过 thinktank 凭证（status=active）。
"""

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.database import get_db
from services.kuaimai_external import thinktank_sync


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True, help="企业 org_id")
    ap.add_argument("--start", type=parse_date, default=None, help="开始日期 YYYY-MM-DD")
    ap.add_argument("--end", type=parse_date, default=None, help="结束日期 YYYY-MM-DD")
    ap.add_argument(
        "--type",
        choices=["daily", "manual", "backfill"],
        default="manual",
        help="同步类型（写入 sync_logs）",
    )
    args = ap.parse_args()

    db = get_db()
    result = await thinktank_sync.sync_thinktank(
        db,
        org_id=args.org_id,
        sync_type=args.type,
        start_date=args.start,
        end_date=args.end,
    )

    logger.info("─" * 60)
    if result.success:
        logger.info(f"✅ 同步成功 | 落库 {result.rows_synced} 行")
        logger.info(f"   sync_log id: {result.log_id}")
        return 0
    else:
        logger.error(f"❌ 同步失败 | error={result.error}")
        if result.cookie_expired:
            logger.error("   → Cookie 已过期，请重新登录快麦智库后到后台配置")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
