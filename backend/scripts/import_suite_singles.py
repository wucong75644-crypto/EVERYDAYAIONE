"""
从快麦导出的套件商品明细 CSV 导入 suit_singles 到 erp_products

⚠️ 多租户警告：此脚本操作 erp_products（租户表），
UPDATE 无 org_id 过滤，多企业环境下可能影响其他企业同编码商品。

用途：API 的 singleList 字段暂时拿不到，用导出数据补齐
格式对齐 API 的 singleList，API 修复后会自然覆盖

字段映射（对齐 API suitSingleList 格式）：
  CSV [137] 子商品商家编码  → skuOuterId（子商品的SKU编码）
  DB erp_product_skus 反查  → outerId（子商品的主编码）
  CSV [135] 子商品名称      → title
  CSV [141] 组合比例        → ratio
  CSV [136] 子商品规格信息  → propertiesName

用法：
    source backend/venv/bin/activate
    python backend/scripts/import_suite_singles.py
"""

import csv
import json
import sys
import os
from collections import defaultdict

# 加载 .env + 项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from core.database import get_db

CSV_PATH = os.path.expanduser(
    "~/Downloads/快麦导出_自定义套件商品明细导出表20260323132006_65109_L8Lpcp.csv"
)

# CSV 列索引
COL_OUTER_ID = 0          # 主商家编码（套件本身）
COL_CHILD_NAME = 135      # 子商品名称
COL_CHILD_SPEC = 136      # 子商品规格信息
COL_CHILD_CODE = 137      # 子商品商家编码（实际是子商品的 SKU 编码）
COL_RATIO = 141           # 组合比例


def load_sku_to_outer_map() -> dict[str, str]:
    """从 erp_product_skus 加载 sku_outer_id → outer_id 映射"""
    db = get_db()
    mapping: dict[str, str] = {}

    # 分页加载全部 SKU 映射
    page_size = 1000
    offset = 0
    while True:
        result = (
            db.table("erp_product_skus")
            .select("sku_outer_id,outer_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = result.data or []
        for r in rows:
            mapping[r["sku_outer_id"]] = r["outer_id"]
        if len(rows) < page_size:
            break
        offset += page_size

    print(f"  加载 SKU→主编码 映射: {len(mapping)} 条")
    return mapping


def parse_csv(sku_map: dict[str, str]) -> tuple[dict[str, list[dict]], int]:
    """解析 CSV，按主商家编码分组构建 singleList（对齐 API 格式）

    Returns:
        (products, unmapped_count)
    """
    products: dict[str, list[dict]] = defaultdict(list)
    seen: dict[str, set] = defaultdict(set)
    unmapped = 0

    min_cols = COL_RATIO + 1  # 至少需要142列

    with open(CSV_PATH, "r", encoding="gbk") as f:
        reader = csv.reader(f)
        next(reader)  # 跳过空行
        next(reader)  # 跳过表头

        pending: list[str] | None = None  # 被拆行的前半段
        for row in reader:
            # 处理被换行符拆成两行的情况：前半段列数不足，与后半段合并
            if len(row) < min_cols and row[0].strip():
                pending = row
                continue
            if pending is not None:
                # 字段内含换行导致拆行：前半段末列 + 后半段首列 = 原始完整字段
                row = pending[:-1] + [pending[-1] + row[0]] + row[1:]
                pending = None
            if len(row) < min_cols:
                continue

            suite_outer_id = row[COL_OUTER_ID].strip()
            child_sku_code = row[COL_CHILD_CODE].strip()
            if not suite_outer_id or not child_sku_code:
                continue

            # 同一套件下同一子商品SKU编码去重
            if child_sku_code in seen[suite_outer_id]:
                continue
            seen[suite_outer_id].add(child_sku_code)

            # 反查子商品的主编码
            child_outer_id = sku_map.get(child_sku_code, "")
            if not child_outer_id:
                unmapped += 1

            ratio_str = row[COL_RATIO].strip()
            try:
                ratio = int(float(ratio_str)) if ratio_str else 1
            except ValueError:
                ratio = 1

            # 对齐 API suitSingleList 格式
            entry = {
                "outerId": child_outer_id,
                "skuOuterId": child_sku_code,
                "title": row[COL_CHILD_NAME].strip(),
                "ratio": ratio,
                "propertiesName": row[COL_CHILD_SPEC].strip(),
            }
            products[suite_outer_id].append(entry)

    return dict(products), unmapped


def import_to_db(products: dict[str, list[dict]]) -> None:
    """批量 update suit_singles 到 erp_products（仅更新已有记录）"""
    db = get_db()

    total = len(products)
    updated = 0
    errors = 0

    outer_ids = list(products.keys())
    batch_size = 50

    for i in range(0, total, batch_size):
        batch_ids = outer_ids[i:i + batch_size]

        for oid in batch_ids:
            try:
                db.table("erp_products").update(
                    {"suit_singles": products[oid]},
                ).eq("outer_id", oid).execute()
                updated += 1
            except Exception as e:
                errors += 1
                print(f"  ✗ {oid}: {e}")

        if (i + batch_size) % 500 == 0 or i + batch_size >= total:
            print(f"  进度: {min(i + batch_size, total)}/{total}")

    print(f"\n完成: 更新 {updated} 个套件, 失败 {errors} 个")


def main() -> None:
    if not os.path.exists(CSV_PATH):
        print(f"CSV 文件不存在: {CSV_PATH}")
        sys.exit(1)

    print("Step 1: 加载 SKU→主编码 映射...")
    sku_map = load_sku_to_outer_map()

    print(f"\nStep 2: 解析 CSV: {CSV_PATH}")
    products, unmapped = parse_csv(sku_map)
    print(f"解析完成: {len(products)} 个套件")
    if unmapped:
        print(f"  ⚠ {unmapped} 个子商品SKU编码未在 erp_product_skus 中找到主编码")

    # 预览
    print("\n=== 数据预览（前3个套件）===")
    for i, (oid, singles) in enumerate(products.items()):
        if i >= 3:
            break
        print(f"\n  套件 {oid} → {len(singles)} 个子商品:")
        for s in singles[:3]:
            print(
                f"    outerId={s['outerId']} | skuOuterId={s['skuOuterId']}"
                f" | {s['title']} | {s['propertiesName']} x{s['ratio']}"
            )
        if len(singles) > 3:
            print(f"    ... 共 {len(singles)} 个")

    # 和 API 格式对比
    print("\n=== API 格式对比 ===")
    sample = list(products.values())[0][0]
    print(f"  本次写入格式: {json.dumps(sample, ensure_ascii=False)}")
    print(f"  API 期望格式:  {{\"outerId\": \"XXX\", \"skuOuterId\": \"XXX-01\", "
          f"\"title\": \"名称\", \"ratio\": 1, \"propertiesName\": \"规格\"}}")

    confirm = input(f"\nStep 3: 确认 update {len(products)} 个套件的 suit_singles? [y/N] ")
    if confirm.lower() != "y":
        print("已取消")
        return

    print("\n开始写入数据库...")
    import_to_db(products)

    # 写入后验证
    print("\n=== 写入后验证 ===")
    db = get_db()
    r = (
        db.table("erp_products")
        .select("outer_id,suit_singles")
        .not_.is_("suit_singles", "null")
        .limit(2)
        .execute()
    )
    for row in r.data or []:
        print(f"  {row['outer_id']}: {json.dumps(row['suit_singles'][:2], ensure_ascii=False)}...")


if __name__ == "__main__":
    main()
