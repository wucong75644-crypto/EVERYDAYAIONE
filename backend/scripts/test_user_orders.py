"""
用户提供的真实订单号测试脚本

测试 JD(京东)、XHS(小红书)、1688 平台的订单号识别
"""

import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.code_identifier import identify_code, _guess_code_type

# ── 用户提供的真实订单号 ──

JD_ORDERS = [
    # 原始提供
    "345908383885",
    "3421270011372062",
    "3439296013946073",
    "3439294008569275",
    "3441448003013233",
    "3441204014454168",
    # 京东自营店铺
    "3440472001682642",
    "3441266017868626",
    "3441292009265270",
    "3441460002949346",
    "3441463003026166",
    "3441245003254097",
    "3441434003488157",
    "3441232017943358",
    "3441201014668669",
    "3441201014667835",
    "3441407006991110",
    "3441497009300908",
    "3441259018228498",
    "3441490009211872",
    # 京东正常店铺
    "3440284008959156",
    "3440260002643354",
    "3441267003001726",
    "3441402001474105",
    "3441256003199620",
    "3441290014474193",
    "3441260003045599",
    "3441283009194262",
    "3441243017679125",
    "3441218004842963",
    "3441208010765959",
    "3441240017631337",
    "3441268003121261",
    "3441211005673965",
    "3441211005569300",
]

XHS_ORDERS = [
    "P789226955540332051",
    "P789226955540332052",
    "P789231454714324661",
    "P789285800902245132",
    "P789286065994231241",
    "P789287887138349311",
    "P789295393809038461",
    "P789296141052055741",
    "P789296333689303321",
    "P789299403830502571",
    "P789374616203502031",
]

ALI_1688_ORDERS = [
    "5101901892416869041",
    "5101547115720606549",
    "5101910640369092726",
    "5101915176195617019",
    "5102032861365380613",
    "3291175417516407186",
    "5101924680661129322",
    "5101927308358914331",
    "3291295332034919381",
    "5101930224423084132",
    "5101933104364084132",
    "5101936812575783635",
    "5101937388198102532",
    "5102054209309643425",
    "3290705763459669088",
    "3291296952752513282",
    "3291182329192552462",
    "3291511440580543055",
    "5101585635735333433",
    "3291298788118036185",
    "3290706303937936792",
    "3290749538870936792",
    "3291298464302936792",
    "3291182149563936792",
    "5102064901731787709",
    "5101954992948371411",
    "3290714763947899965",
    "3291306096993662492",
    "5101605903210079730",
]


def show_format_analysis():
    """展示格式分析（不调API）"""
    print("=" * 70)
    print("Step 1: 格式预判分析（_guess_code_type）")
    print("=" * 70)

    all_groups = [
        ("京东(JD)", JD_ORDERS),
        ("小红书(XHS)", XHS_ORDERS),
        ("1688", ALI_1688_ORDERS),
    ]

    issues = []
    for platform, orders in all_groups:
        print(f"\n┌─ {platform} ({len(orders)}个) ─────────────────")
        for code in orders:
            guess = _guess_code_type(code)
            flag = ""
            if platform == "京东(JD)" and not guess.startswith("order"):
                flag = " ⚠ 未识别为订单！"
                issues.append((code, platform, guess))
            elif platform == "小红书(XHS)" and guess != "order_xhs":
                flag = " ⚠ 未识别为小红书订单！"
                issues.append((code, platform, guess))
            elif platform == "1688" and guess != "order_19":
                flag = " ⚠ 未识别为1688订单！"
                issues.append((code, platform, guess))
            print(f"│  {code} ({len(code)}位) → {guess}{flag}")
        print(f"└{'─' * 55}")

    if issues:
        print(f"\n⚠ 格式预判问题 ({len(issues)}个):")
        for code, platform, guess in issues:
            print(f"  - {platform} {code} ({len(code)}位) → "
                  f"被判为 {guess}，需要修复 _guess_code_type")
    else:
        print("\n✅ 所有订单号格式预判正确")

    return issues


async def run_identify_test(client: KuaiMaiClient):
    """逐个调用 identify_code 测试"""
    print("\n" + "=" * 70)
    print("Step 2: 调用 identify_code 实测")
    print("=" * 70)

    all_groups = [
        ("京东(JD)", JD_ORDERS),
        ("小红书(XHS)", XHS_ORDERS),
        ("1688", ALI_1688_ORDERS),
    ]

    stats = {"pass": 0, "fail": 0, "not_found": 0}
    failures = []

    for platform, orders in all_groups:
        print(f"\n{'='*60}")
        print(f"  {platform} — {len(orders)} 个订单号")
        print(f"{'='*60}")

        for i, code in enumerate(orders, 1):
            print(f"\n--- [{i}/{len(orders)}] {code} ({len(code)}位) ---")
            try:
                result = await identify_code(client, code)
                for line in result.split("\n"):
                    print(f"  {line}")

                if "✓ 订单存在" in result:
                    stats["pass"] += 1
                    print(f"  → ✅ PASS")
                elif "✓ 商品存在" in result:
                    # 被当成商品了 — 对于订单号来说这是错误的
                    stats["fail"] += 1
                    print(f"  → ❌ FAIL: 订单号被识别为商品")
                    failures.append((platform, code, "被识别为商品"))
                elif "✗ 未识别" in result:
                    stats["not_found"] += 1
                    print(f"  → ⚠ NOT FOUND: 订单可能已不在ERP中")
                else:
                    stats["not_found"] += 1
                    print(f"  → ⚠ UNKNOWN")
            except Exception as e:
                stats["fail"] += 1
                print(f"  → ❌ ERROR: {e}")
                failures.append((platform, code, str(e)))

    # 汇总
    total = sum(stats.values())
    print("\n" + "=" * 70)
    print(f"测试汇总: 共 {total} 个订单号")
    print(f"  ✅ 订单识别成功: {stats['pass']}")
    print(f"  ⚠ 未找到(可能已删除): {stats['not_found']}")
    print(f"  ❌ 识别错误: {stats['fail']}")

    if failures:
        print(f"\n❌ 失败详情:")
        for platform, code, reason in failures:
            print(f"  - [{platform}] {code}: {reason}")

    print("=" * 70)
    print(f"\n订单识别率: {stats['pass']}/{total} "
          f"({stats['pass']/total*100:.1f}%)" if total else "")


async def main():
    # Step 1: 格式分析（不需要API）
    issues = show_format_analysis()

    # Step 2: 实测
    client = KuaiMaiClient()
    if not client.is_configured:
        print("\n❌ ERP 未配置")
        return

    await client.load_cached_token()
    print("\n✓ KuaiMaiClient 已初始化")

    try:
        await run_identify_test(client)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
