"""
修复订单 outer_id：从 SKU 编码改为主编码

使用预生成的本地映射文件 /tmp/sku_map.json，
按 SKU 编码批量查订单（走索引），逐条更新。

⚠️ 多租户警告：此脚本操作 erp_document_items（租户表），
运行前必须确认目标 org_id，避免跨企业误操作。

用法：
    source backend/venv/bin/activate
    python backend/scripts/fix_order_outer_id.py
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from core.database import get_db

SKU_MAP_PATH = "/tmp/sku_map.json"


def main() -> None:
    db = get_db()

    # Step 1: 从本地文件加载映射
    print("Step 1: 加载本地 SKU→主编码 映射...")
    with open(SKU_MAP_PATH) as f:
        sku_map: dict[str, str] = json.load(f)
    print(f"  映射: {len(sku_map)} 条")

    # Step 2: 按 SKU 编码批量查订单 + 更新
    print("\nStep 2: 按 SKU 编码批量修复...")
    sku_list = list(sku_map.keys())
    batch_size = 10
    total_fixed = 0
    total_skus_done = 0

    for i in range(0, len(sku_list), batch_size):
        batch_skus = sku_list[i:i + batch_size]
        total_skus_done += len(batch_skus)

        try:
            r = (
                db.table("erp_document_items")
                .select("id,outer_id")
                .eq("doc_type", "order")
                .in_("outer_id", batch_skus)
                .limit(5000)
                .execute()
            )
        except Exception as e:
            print(f"  ✗ 查询失败({total_skus_done}/{len(sku_list)}): {e}")
            time.sleep(5)
            continue

        rows = r.data or []
        if not rows:
            if total_skus_done % 500 == 0:
                print(f"  进度: {total_skus_done}/{len(sku_list)} SKU | 累计修复 {total_fixed}")
            continue

        batch_fixed = 0
        for row in rows:
            correct = sku_map.get(row["outer_id"])
            if not correct:
                continue
            try:
                db.table("erp_document_items").update(
                    {"outer_id": correct}
                ).eq("id", row["id"]).execute()
                batch_fixed += 1
            except Exception as e:
                print(f"  ✗ id={row['id']}: {e}")
                time.sleep(3)

        total_fixed += batch_fixed
        print(
            f"  进度: {total_skus_done}/{len(sku_list)} SKU | "
            f"本批 {batch_fixed} 条 | 累计 {total_fixed}"
        )

        time.sleep(0.5)

    print(f"\n完成: 扫描 {len(sku_list)} 个 SKU, 修复 {total_fixed} 条订单")


if __name__ == "__main__":
    main()
