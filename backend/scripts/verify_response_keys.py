"""
response_key 全量验证脚本

对所有非写操作的 API entry，发 pageSize=1 的请求，验证：
1. 响应中是否包含配置的 response_key 字段
2. response_key 对应的值是否为 list 类型
3. 如果不匹配，显示实际的响应 key 供排查

用法: cd backend && source venv/bin/activate && python scripts/verify_response_keys.py
"""

import asyncio
import sys
import os

# 确保 backend 在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.registry import (
    BASIC_REGISTRY,
    PRODUCT_REGISTRY,
    TRADE_REGISTRY,
    AFTERSALES_REGISTRY,
    WAREHOUSE_REGISTRY,
    PURCHASE_REGISTRY,
    DISTRIBUTION_REGISTRY,
    QIMEN_REGISTRY,
)

# 每个 registry 的名称映射
ALL_REGISTRIES = {
    "PRODUCT": PRODUCT_REGISTRY,
    "TRADE": TRADE_REGISTRY,
    "BASIC": BASIC_REGISTRY,
    "AFTERSALES": AFTERSALES_REGISTRY,
    "WAREHOUSE": WAREHOUSE_REGISTRY,
    "PURCHASE": PURCHASE_REGISTRY,
    "DISTRIBUTION": DISTRIBUTION_REGISTRY,
    "QIMEN": QIMEN_REGISTRY,
}


async def verify_all():
    client = KuaiMaiClient()
    results = {"pass": [], "fail": [], "error": [], "skip": []}

    for reg_name, registry in ALL_REGISTRIES.items():
        for entry_name, entry in registry.items():
            label = f"{reg_name}.{entry_name}"

            # 跳过写操作
            if entry.is_write:
                results["skip"].append((label, "写操作"))
                continue

            # 跳过 response_key=None（Detail API，不返回列表）
            if entry.response_key is None:
                results["skip"].append((label, f"Detail API (response_key=None)"))
                continue

            # 构造最小参数
            params = {"pageNo": 1, "pageSize": 1}

            # 奇门等需要 base_url / system_params
            base_url = entry.base_url
            extra_system = entry.system_params or {}

            try:
                data = await client.request_with_retry(
                    entry.method,
                    params,
                    base_url=base_url,
                    extra_system_params=extra_system if extra_system else None,
                )

                rk = entry.response_key
                actual_keys = [k for k in data.keys() if k not in ("total", "code", "message", "msg")]

                if rk in data:
                    val = data[rk]
                    if isinstance(val, list):
                        results["pass"].append((
                            label, entry.method, rk,
                            f"✅ key={rk}, items={len(val)}, total={data.get('total', '?')}"
                        ))
                    else:
                        results["fail"].append((
                            label, entry.method, rk,
                            f"❌ key存在但不是list, type={type(val).__name__}, "
                            f"actual_keys={actual_keys}"
                        ))
                else:
                    # response_key 不在响应中 → 配置错误！
                    # 尝试找到实际的列表字段
                    list_keys = [k for k, v in data.items() if isinstance(v, list)]
                    results["fail"].append((
                        label, entry.method, rk,
                        f"❌ response_key='{rk}' 不存在! "
                        f"actual_keys={actual_keys}, "
                        f"实际list字段={list_keys or '无'}"
                    ))

            except Exception as e:
                err_msg = str(e)[:120]
                results["error"].append((label, entry.method, entry.response_key, f"⚠ {err_msg}"))

    await client.close()
    return results


def print_report(results):
    total = sum(len(v) for v in results.values())

    print("\n" + "=" * 80)
    print(f"  response_key 全量验证报告")
    print(f"  总计 {total} 个 entry | "
          f"✅ {len(results['pass'])} | "
          f"❌ {len(results['fail'])} | "
          f"⚠ {len(results['error'])} | "
          f"⏭ {len(results['skip'])}")
    print("=" * 80)

    if results["fail"]:
        print(f"\n{'─' * 40}")
        print(f"  ❌ 失败 ({len(results['fail'])} 个) — response_key 配置错误!")
        print(f"{'─' * 40}")
        for label, method, rk, msg in results["fail"]:
            print(f"  {label}")
            print(f"    method: {method}")
            print(f"    configured response_key: {rk}")
            print(f"    {msg}")
            print()

    if results["error"]:
        print(f"\n{'─' * 40}")
        print(f"  ⚠ 请求异常 ({len(results['error'])} 个)")
        print(f"{'─' * 40}")
        for label, method, rk, msg in results["error"]:
            print(f"  {label} | {method} | rk={rk} | {msg}")

    if results["pass"]:
        print(f"\n{'─' * 40}")
        print(f"  ✅ 通过 ({len(results['pass'])} 个)")
        print(f"{'─' * 40}")
        for label, method, rk, msg in results["pass"]:
            print(f"  {label} | {method} | {msg}")

    if results["skip"]:
        print(f"\n{'─' * 40}")
        print(f"  ⏭ 跳过 ({len(results['skip'])} 个)")
        print(f"{'─' * 40}")
        for label, reason in results["skip"]:
            print(f"  {label} — {reason}")

    print("\n" + "=" * 80)
    if results["fail"]:
        print("  ⚠ 有失败项！需要修复 response_key 配置")
    else:
        print("  ✅ 所有 response_key 配置正确")
    print("=" * 80)


if __name__ == "__main__":
    results = asyncio.run(verify_all())
    print_report(results)
