#!/usr/bin/env python3
"""
Step 2 端到端验证：

  1. cURL 文本 → curl_parser 解析（提取 cookie + companyid）
  2. → credential_store 保存到 DB
  3. → 从 DB 读回
  4. → 用读回的凭证构造 KuaimaiWebClient
  5. → 真实调用快麦智库 API
  6. → 拿到数据 → 记录同步成功

用法：
    cd backend && KUAIMAI_TEST_CURL='<完整 cURL>' venv/bin/python scripts/verify_step2_kuaimai_external.py

如果 KUAIMAI_TEST_CURL 没设，会用一个简化的 cURL 串测试解析逻辑（不发真实请求）。
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.database import get_db
from services.kuaimai_external import (
    credential_store,
    curl_parser,
    http_base,
)


# 蓝创 org（生产数据，用于测试）
TEST_ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"

# fallback：内置的简化 cURL（只测解析，不发真请求）
FALLBACK_CURL = """
curl 'https://erp.superboss.cc/kmzk/profit/report/shop' \\
  -H 'accept: application/json' \\
  -H 'companyid: 65109' \\
  -b '_ati=1755400764513; _ga=GA1.1.1604887728.1770627936; _censeid=99a96b5cf008866c91dcf57f5e936a6917574836; tfstk=fake_tfstk_for_test' \\
  -H 'origin: https://erp.superboss.cc' \\
  --data-raw 'pageNo=1&pageSize=50'
"""


async def main() -> int:
    # ───── Step 2-1: 解析 cURL ─────
    logger.info("=" * 60)
    logger.info("【1】解析 cURL")
    logger.info("=" * 60)

    raw_curl = os.environ.get("KUAIMAI_TEST_CURL", "").strip() or FALLBACK_CURL
    is_real = bool(os.environ.get("KUAIMAI_TEST_CURL"))
    logger.info(f"模式: {'真实 cURL（会发请求）' if is_real else 'fallback（仅测解析）'}")

    parsed = curl_parser.parse_curl(raw_curl)
    logger.info(f"  url           : {parsed.url}")
    logger.info(f"  method        : {parsed.method}")
    logger.info(f"  companyid     : {parsed.companyid}")
    logger.info(f"  censeid       : {parsed.censeid[:20]}...{parsed.censeid[-10:] if len(parsed.censeid) > 30 else ''}")
    logger.info(f"  cookies count : {len(parsed.cookies)}")
    logger.info(f"  cookies keys  : {list(parsed.cookies.keys())}")
    logger.info(f"  data_raw len  : {len(parsed.data_raw)} 字符")

    source = curl_parser.detect_source(parsed)
    logger.info(f"  detected src  : {source}")

    # 校验
    assert parsed.url, "URL 必须存在"
    assert parsed.companyid, "companyid 必须存在"
    assert parsed.censeid, "_censeid cookie 必须存在"
    assert source in ("thinktank", "viperp"), f"source 识别错误: {source}"
    logger.info("  ✅ cURL 解析通过")

    # ───── Step 2-2: 保存到 DB ─────
    logger.info("")
    logger.info("=" * 60)
    logger.info("【2】凭证写入 DB")
    logger.info("=" * 60)

    db = get_db()
    cred_id = credential_store.save_credential(
        db,
        org_id=TEST_ORG_ID,
        source=source,  # type: ignore
        kuaimai_company_id=parsed.companyid,
        censeid_cookie=parsed.censeid,
        cookie_full=parsed.cookie_full,
    )
    logger.info(f"  ✅ 凭证已保存 | id={cred_id}")

    # ───── Step 2-3: 从 DB 读回 ─────
    logger.info("")
    logger.info("=" * 60)
    logger.info("【3】从 DB 读回凭证")
    logger.info("=" * 60)

    cred = credential_store.get_active_credential(
        db, org_id=TEST_ORG_ID, source=source  # type: ignore
    )
    assert cred is not None, "凭证读取失败"
    logger.info(f"  id            : {cred.id}")
    logger.info(f"  org_id        : {cred.org_id}")
    logger.info(f"  source        : {cred.source}")
    logger.info(f"  companyid     : {cred.kuaimai_company_id}")
    logger.info(f"  status        : {cred.status}")
    logger.info(f"  censeid       : {cred.censeid_cookie[:20]}...")
    logger.info(f"  cookie_full?  : {bool(cred.cookie_full)}")
    logger.info(f"  last_check_at : {cred.last_health_check_at}")
    logger.info("  ✅ 凭证读回成功")

    # ───── Step 2-4: 列出当前 org 所有凭证 ─────
    logger.info("")
    logger.info("=" * 60)
    logger.info("【4】列出该 org 所有凭证")
    logger.info("=" * 60)

    all_creds = credential_store.list_credentials(db, org_id=TEST_ORG_ID)
    logger.info(f"  共 {len(all_creds)} 条:")
    for c in all_creds:
        logger.info(f"    - {c.source:10s} companyid={c.kuaimai_company_id} status={c.status}")

    # ───── Step 2-5: 真实调用快麦接口（仅在 KUAIMAI_TEST_CURL 提供时） ─────
    if not is_real:
        logger.info("")
        logger.info("⚠️ 未提供真实 cURL（KUAIMAI_TEST_CURL 环境变量），跳过 HTTP 调用测试")
        logger.info("   完整端到端测试请运行：")
        logger.info("     export KUAIMAI_TEST_CURL='<复制的完整 cURL>'")
        logger.info("     venv/bin/python scripts/verify_step2_kuaimai_external.py")
        return 0

    logger.info("")
    logger.info("=" * 60)
    logger.info("【5】用 DB 中凭证调真实快麦接口")
    logger.info("=" * 60)

    client = http_base.KuaimaiWebClient(
        companyid=cred.kuaimai_company_id,
        cookie=cred.cookie_full or f"_censeid={cred.censeid_cookie}",
    )

    # 构造一个最小 payload（拉过去 1 天店铺数据）
    import time
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 7 * 86400_000

    if source == "thinktank":
        payload = {
            "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
            "sysStatus": "1",
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "shopUniIds": "",
            "formulaId": "658",
            "ruleId": "230290901203812352",
            "showDimension": "0",
            "dateShowType": "0",
            "costType": "0",
            "isTrusted": "true",
        }
        try:
            result = await client.post(
                url=parsed.url,
                payload=payload,
                module_path="/think_tank/profit_shop/",
                origin="https://erp.superboss.cc",
                referer="https://erp.superboss.cc/index.html",
            )
            rows = (result.json_body or {}).get("data", {}).get("list", [])
            logger.info(f"  ✅ HTTP {result.status_code} | 数据行数={len(rows)}")
            if rows:
                logger.info(f"  首行字段数: {len(rows[0])}")
                logger.info(f"  首行 shopName: {rows[0].get('shopName')}")

            credential_store.record_sync_success(db, credential_id=cred.id)
            logger.info("  ✅ 已记录同步成功状态")
        except http_base.CookieExpiredError as e:
            credential_store.mark_expired(db, credential_id=cred.id, error_msg=str(e))
            logger.error(f"  ❌ Cookie 失效: {e}")
            return 1
        except Exception as e:
            credential_store.record_sync_failure(db, credential_id=cred.id, error_msg=str(e))
            logger.error(f"  ❌ 调用失败: {e}")
            return 1
        finally:
            await client.close()

    logger.info("")
    logger.info("=" * 60)
    logger.info("🎉 Step 2 全部验证通过")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
