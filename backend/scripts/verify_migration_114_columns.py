#!/usr/bin/env python3
"""验证 migration 114 的表列数 + 列名是否符合预期。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_db


EXPECTED = {
    "kuaimai_external_credentials": 13,
    # 系统列 (id, org_id, kuaimai_company_id, sync_batch_id, created_at, updated_at, raw_payload) = 7
    # + 业务列：98（智库）= 105 个左右
    "erp_thinktank_profit_shop": 100,  # 大致
    "erp_viperp_sale_finance": 60,     # 大致
    "kuaimai_sync_logs": 12,
}


def main() -> int:
    db = get_db()
    pool = db.pool

    print("─" * 70)
    print(f"{'表名':<40}{'列数':<10}状态")
    print("─" * 70)

    with pool.connection() as conn:
        with conn.cursor() as cur:
            for table, expected in EXPECTED.items():
                cur.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (table,),
                )
                rows = cur.fetchall()
                count = len(rows)
                mark = "✓" if count >= expected else "❌"
                print(f"{table:<40}{count:<10}{mark} (期望≥{expected})")
                if count == 0:
                    print(f"  ⚠️  表不存在或为空")

    print("─" * 70)
    print("\n▼ erp_thinktank_profit_shop 全部列名:")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'erp_thinktank_profit_shop'
                ORDER BY ordinal_position
                """
            )
            for i, row in enumerate(cur.fetchall(), 1):
                print(f"  {i:3d}. {row['column_name']:<40} {row['data_type']}")

    print("\n▼ erp_viperp_sale_finance 全部列名:")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'erp_viperp_sale_finance'
                ORDER BY ordinal_position
                """
            )
            for i, row in enumerate(cur.fetchall(), 1):
                print(f"  {i:3d}. {row['column_name']:<40} {row['data_type']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
