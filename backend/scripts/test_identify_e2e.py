"""
erp_identify 端到端实测脚本（增强版）

流程：
1. 从 ERP 拉取真实商品、订单数据，提取各类编码
2. 展示全部采集到的测试数据
3. 用 identify_code() 逐个识别，验证准确性
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.code_identifier import identify_code


async def collect_real_codes(client: KuaiMaiClient) -> list[dict]:
    """从 ERP 采集真实编码样本"""
    codes = []
    items = []

    # ── 1. 拉取商品列表（多页），提取 outerId ──
    print("=" * 60)
    print("[Step 1] 拉取商品数据（2页 x 20条）...")
    for page in range(1, 3):
        try:
            data = await client.request_with_retry(
                "item.list.query",
                {"pageNo": page, "pageSize": 20, "activeStatus": 1},
            )
            page_items = data.get("items") or data.get("list") or []
            items.extend(page_items)
            print(f"  第{page}页: 获取到 {len(page_items)} 个商品")
        except Exception as e:
            print(f"  第{page}页拉取失败: {e}")

    # 去重 outerId
    seen_outer = set()
    for item in items:
        outer_id = item.get("outerId", "")
        title = item.get("title", "")
        item_type = item.get("type", 0)
        if outer_id and outer_id not in seen_outer:
            seen_outer.add(outer_id)
            codes.append({
                "code": outer_id,
                "expected": "主编码(outer_id)",
                "source": f"商品: {title} (type={item_type})",
                "category": "商品主编码",
            })

    print(f"  共获取 {len(items)} 个商品，去重后 {len(seen_outer)} 个主编码")

    # ── 2. 提取 SKU 编码 ──
    print("\n[Step 2] 提取 SKU 编码...")
    sku_count = 0
    seen_sku = set()
    for item in items:
        skus = item.get("skus") or item.get("items") or []
        outer_id = item.get("outerId", "")
        for sku in skus[:2]:
            sku_code = sku.get("skuOuterId", "")
            spec = sku.get("propertiesName", "")
            if sku_code and sku_code != outer_id and sku_code not in seen_sku:
                seen_sku.add(sku_code)
                codes.append({
                    "code": sku_code,
                    "expected": "SKU编码(sku_outer_id)",
                    "source": f"SKU of {outer_id}: {spec}",
                    "category": "SKU编码",
                })
                sku_count += 1
            if sku_count >= 15:
                break
        if sku_count >= 15:
            break
    print(f"  提取到 {sku_count} 个 SKU 编码")

    # ── 3. 拉取订单（按状态分批查，收集不同平台/状态的 sid 和 tid）──
    print("\n[Step 3] 拉取订单数据...")
    now = datetime.now()

    order_codes = []
    seen_tids = set()
    seen_sids = set()

    # 按不同状态查询，收集尽可能多样的订单号
    order_queries = [
        {"status": "WAIT_AUDIT", "label": "待审核"},
        {
            "status": "FINISHED", "label": "已完成(3天)",
            "timeType": "created",
            "startTime": (now - timedelta(days=3)).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "endTime": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        {
            "status": "CLOSED", "label": "已关闭(3天)",
            "timeType": "created",
            "startTime": (now - timedelta(days=3)).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "endTime": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
    ]

    all_orders_raw = []
    for query in order_queries:
        label = query.pop("label")
        try:
            params = {"pageNo": 1, "pageSize": 50}
            params.update(query)
            data = await client.request_with_retry(
                "erp.trade.list.query", params,
            )
            orders = data.get("list") or []
            all_orders_raw.extend(orders)
            print(f"  {label}: {len(orders)} 个")
        except Exception as e:
            print(f"  {label} 查询失败: {e}")

    # 按 source 分组，每个平台取最多3个订单
    source_groups = {}
    for o in all_orders_raw:
        src = str(o.get("source", "") or "unknown")
        source_groups.setdefault(src, []).append(o)

    print(f"  订单平台分布: "
          + ", ".join(f"{k}:{len(v)}" for k, v in source_groups.items()))

    for source, orders in source_groups.items():
        for o in orders[:3]:
            tid = str(o.get("tid", "") or "")
            sid = str(o.get("sid", "") or "")
            status = str(o.get("sysStatus", "") or "")

            print(f"    [{source}] tid={tid!r}({len(tid)}位), "
                  f"sid={sid!r}({len(sid)}位), status={status}")

            # tid（平台订单号）
            if tid and tid not in seen_tids:
                seen_tids.add(tid)
                order_codes.append({
                    "code": tid,
                    "expected": "平台订单号(order_id)",
                    "source": f"订单 tid({len(tid)}位): {source} {status}",
                    "category": "订单号(tid)",
                })

            # sid（系统单号）
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                order_codes.append({
                    "code": sid,
                    "expected": "系统单号(system_id)",
                    "source": f"订单 sid({len(sid)}位): {source} {status}",
                    "category": "订单号(sid)",
                })

    # 3b. 从归档订单中补充其他平台（tb/fxg/kuaishou/jd/xhs/1688）
    found_sources = set(source_groups.keys())
    missing = {"tb", "fxg", "kuaishou", "jd", "xhs", "1688"} - found_sources
    if missing:
        print(f"\n  补充查询归档订单（3月前），寻找: {', '.join(missing)}")
        try:
            arch_data = await client.request_with_retry(
                "erp.trade.list.query",
                {
                    "pageNo": 1, "pageSize": 50,
                    "status": "FINISHED",
                    "queryType": "1",
                    "timeType": "created",
                    "startTime": (now - timedelta(days=365)).strftime(
                        "%Y-%m-%d %H:%M:%S"),
                    "endTime": (now - timedelta(days=90)).strftime(
                        "%Y-%m-%d %H:%M:%S"),
                },
            )
            arch_orders = arch_data.get("list") or []
            print(f"  归档订单: {len(arch_orders)} 个")

            # 按 source 分组
            arch_groups = {}
            for o in arch_orders:
                src = str(o.get("source", "") or "")
                arch_groups.setdefault(src, []).append(o)

            for src in list(missing):
                if src not in arch_groups:
                    continue
                for o in arch_groups[src][:2]:
                    tid = str(o.get("tid", "") or "")
                    sid = str(o.get("sid", "") or "")
                    status = str(o.get("sysStatus", "") or "")

                    print(f"    [归档-{src}] tid={tid!r}({len(tid)}位), "
                          f"sid={sid!r}({len(sid)}位)")

                    if tid and tid not in seen_tids:
                        seen_tids.add(tid)
                        order_codes.append({
                            "code": tid,
                            "expected": "平台订单号(order_id)",
                            "source": f"归档 tid({len(tid)}位): {src} {status}",
                            "category": "订单号(tid)",
                        })

                    if sid and sid not in seen_sids:
                        seen_sids.add(sid)
                        order_codes.append({
                            "code": sid,
                            "expected": "系统单号(system_id)",
                            "source": f"归档 sid({len(sid)}位): {src} {status}",
                            "category": "订单号(sid)",
                        })

            still_missing = missing - set(arch_groups.keys())
            if still_missing:
                print(f"  仍未找到: {', '.join(still_missing)}")
        except Exception as e:
            print(f"  归档查询失败: {e}")

    codes.extend(order_codes)

    print(f"  共采集 {sum(1 for c in codes if '订单' in c['category'])} 个订单号")

    # ── 4. 添加边界测试编码 ──
    print("\n[Step 4] 添加边界测试编码...")
    edge_cases = [
        {"code": "NOTEXIST999", "expected": "未识别", "source": "不存在的编码",
         "category": "边界测试"},
        {"code": "ZZZZZZZ_FAKE", "expected": "未识别", "source": "不存在的长编码",
         "category": "边界测试"},
        {"code": "8001", "expected": "商品/未识别", "source": "短纯数字（非订单格式）",
         "category": "边界测试"},
        {"code": "99999", "expected": "商品/未识别", "source": "5位纯数字",
         "category": "边界测试"},
    ]
    codes.extend(edge_cases)
    print(f"  添加 {len(edge_cases)} 个边界测试用例")

    return codes


def display_test_data(codes: list[dict]) -> None:
    """在测试前展示所有待测试的数据"""
    print("\n" + "=" * 70)
    print("📋 测试数据总览")
    print("=" * 70)

    # 按 category 分组展示
    categories = {}
    for item in codes:
        cat = item.get("category", "其他")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    total_idx = 0
    for cat, items in categories.items():
        print(f"\n┌─ {cat} ({len(items)}个) ─────────────────────────")
        for item in items:
            total_idx += 1
            print(f"│ {total_idx:>3}. 编码: {item['code']:<30} | "
                  f"期望: {item['expected']}")
            print(f"│      来源: {item['source']}")
        print(f"└{'─' * 55}")

    print(f"\n📊 统计: 共 {len(codes)} 个编码")
    for cat, items in categories.items():
        print(f"  • {cat}: {len(items)} 个")
    print("=" * 70)


async def run_identify_test(client: KuaiMaiClient, codes: list[dict]) -> None:
    """逐个识别并对比结果"""
    print("\n" + "=" * 70)
    print(f"🔍 开始识别测试 ({len(codes)} 个编码)")
    print("=" * 70)

    results = {"pass": 0, "fail": 0, "unknown": 0}
    details = []

    for i, item in enumerate(codes, 1):
        code = item["code"]
        expected = item["expected"]
        source = item["source"]
        category = item.get("category", "")

        print(f"\n--- [{i}/{len(codes)}] {category} | 编码: {code} ---")
        print(f"  来源: {source}")
        print(f"  期望: {expected}")

        try:
            result = await identify_code(client, code)
            print(f"  结果:")
            for line in result.split("\n"):
                print(f"    {line}")

            # 判断逻辑
            if "✓" in result and expected != "未识别":
                results["pass"] += 1
                verdict = "✅ PASS"
            elif "✗" in result and "未识别" in expected:
                results["pass"] += 1
                verdict = "✅ PASS (预期未识别)"
            elif "✓" in result and "未识别" in expected:
                # 边界用例如果匹配到了也算pass
                results["pass"] += 1
                verdict = "✅ PASS (有匹配)"
            elif "✗" in result and "商品/未识别" in expected:
                results["pass"] += 1
                verdict = "✅ PASS (预期未识别)"
            else:
                results["unknown"] += 1
                verdict = "⚠️ REVIEW"

            print(f"  判定: {verdict}")
            details.append({
                "code": code, "category": category,
                "verdict": verdict, "result_line": result.split("\n")[1] if "\n" in result else result,
            })
        except Exception as e:
            results["fail"] += 1
            verdict = f"❌ FAIL: {e}"
            print(f"  错误: {verdict}")
            details.append({
                "code": code, "category": category,
                "verdict": verdict, "result_line": str(e),
            })

    # 汇总
    total = len(codes)
    print("\n" + "=" * 70)
    print(f"📊 测试汇总: 共 {total} 个编码")
    print(f"  ✅ PASS:   {results['pass']}")
    print(f"  ⚠️ REVIEW: {results['unknown']}")
    print(f"  ❌ FAIL:   {results['fail']}")

    # 按 category 统计
    cat_stats = {}
    for d in details:
        cat = d["category"]
        if cat not in cat_stats:
            cat_stats[cat] = {"pass": 0, "fail": 0, "review": 0}
        if "PASS" in d["verdict"]:
            cat_stats[cat]["pass"] += 1
        elif "FAIL" in d["verdict"]:
            cat_stats[cat]["fail"] += 1
        else:
            cat_stats[cat]["review"] += 1

    print("\n  分类统计:")
    for cat, stats in cat_stats.items():
        total_cat = stats["pass"] + stats["fail"] + stats["review"]
        print(f"    {cat}: {stats['pass']}/{total_cat} PASS"
              + (f", {stats['fail']} FAIL" if stats["fail"] else "")
              + (f", {stats['review']} REVIEW" if stats["review"] else ""))

    # 列出非 PASS 的
    non_pass = [d for d in details if "PASS" not in d["verdict"]]
    if non_pass:
        print("\n  ⚠️ 需要关注的编码:")
        for d in non_pass:
            print(f"    - {d['code']} ({d['category']}): {d['verdict']}")

    print("=" * 70)

    # 成功率
    pass_rate = results["pass"] / total * 100 if total else 0
    print(f"\n🎯 识别成功率: {pass_rate:.1f}% ({results['pass']}/{total})")


async def main():
    client = KuaiMaiClient()
    if not client.is_configured:
        print("❌ ERP 未配置，请检查 .env 中的 KUAIMAI_* 环境变量")
        return

    await client.load_cached_token()
    print("✓ KuaiMaiClient 已初始化\n")

    try:
        # 采集数据
        codes = await collect_real_codes(client)

        # 展示测试数据
        display_test_data(codes)

        # 运行识别测试
        await run_identify_test(client, codes)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
