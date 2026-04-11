"""ERP 同步健康检查 — 异常告警后台协程

每 5 分钟扫描一次 erp_sync_state，对 error_count >= 阈值的同步类型触发告警。

告警渠道（多通道兜底）：
1. **logger.error**：始终输出 — 任何渠道失败都至少日志可见
2. **Redis 状态位**：`erp_sync:alerts:{org_id}` 写入活跃告警列表（TTL 1h），
   供 AdminPanel 前端轮询展示红点
3. **企微推送**：best-effort 推给该 org 的 super_admin，链路任一步失败都
   logger.error 但不抛异常

去重：相同 org 的相同告警指纹 1 小时内只推一次，避免 5 分钟扫描刷屏。

历史背景：2026-04-10 因为多租户改造遗漏 token DB 双写，导致快麦 token
30 天硬到期后所有 ERP worker 雪崩。当时 400 次连续失败 7 小时无人察觉，
这个 healthcheck 是防止此类失声的兜底。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from loguru import logger

# 告警阈值：error_count >= 此值才触发告警
# 选 10 的原因：单次网络抖动/限流误差 1~3 次很常见，10 次必然是真问题
ALERT_THRESHOLD = 10

# 去重窗口：相同告警指纹在此窗口内只推一次（秒）
DEDUPE_TTL = 3600  # 1 小时

# 扫描间隔（秒）
SCAN_INTERVAL = 300  # 5 分钟

# Redis 告警状态位前缀（供前端 AdminPanel 读取展示）
ALERT_STATE_PREFIX = "erp_sync:alerts"
ALERT_STATE_TTL = 3600  # 与去重窗口一致


async def healthcheck_loop(db: Any) -> None:
    """常驻后台协程：周期扫描 erp_sync_state 异常并告警。

    Args:
        db: AsyncLocalDBClient（worker 共享的 DB 实例）
    """
    logger.info(
        f"ErpSyncHealthcheck started | "
        f"threshold={ALERT_THRESHOLD} | interval={SCAN_INTERVAL}s"
    )
    while True:
        try:
            await _scan_and_alert(db)
        except asyncio.CancelledError:
            logger.info("ErpSyncHealthcheck cancelled")
            return
        except Exception as e:
            # 单次扫描失败不影响后续扫描
            logger.error(f"ErpSyncHealthcheck loop error | {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)


async def _scan_and_alert(db: Any) -> None:
    """单次扫描 + 触发告警

    扫描两类异常源：
    1. erp_sync_state.error_count >= 阈值（同步任务连续失败）
    2. Redis kuaimai:persist_failure:* 状态位（token DB 持久化失败）

    第二类是 2026-04-10 雪崩根因的兜底防线 — 同步本身可能正常工作（用 Redis 缓存
    的新 token），但 DB 写入持续失败 → erp_sync_state 看不到任何异常 → 直到 Redis
    失效才暴露。所以必须独立检测 token 持久化失败。
    """
    by_org: dict[str, list[dict]] = {}

    # === 1) 同步任务失败扫描 ===
    rows = await (
        db.table("erp_sync_state")
        .select("org_id, sync_type, error_count, last_error, last_run_at")
        .gte("error_count", ALERT_THRESHOLD)
        .execute()
    )
    for r in (rows.data or []):
        org_id = r.get("org_id") or "system"
        by_org.setdefault(org_id, []).append(r)

    # === 2) Token DB 持久化失败扫描（隐性失败兜底）===
    try:
        from core.redis import get_redis
        redis = await get_redis()
        if redis:
            async for key in redis.scan_iter(match="kuaimai:persist_failure:*", count=100):
                key_str = key.decode() if isinstance(key, bytes) else key
                org_id = key_str.split(":")[-1]
                error_msg = await redis.get(key_str)
                if error_msg:
                    if isinstance(error_msg, bytes):
                        error_msg = error_msg.decode()
                    by_org.setdefault(org_id, []).append({
                        "org_id": org_id,
                        "sync_type": "token_db_persist",
                        "error_count": ALERT_THRESHOLD,  # 触发告警
                        "last_error": f"Token DB 持久化失败: {error_msg}",
                        "last_run_at": None,
                    })
    except Exception as e:
        logger.warning(
            f"ErpSyncHealthcheck persist_failure scan failed | error={e}"
        )

    if not by_org:
        return

    for org_id, org_items in by_org.items():
        try:
            await _maybe_alert_org(db, org_id, org_items)
        except Exception as e:
            logger.error(
                f"ErpSyncHealthcheck alert failed | org={org_id} | error={e}"
            )


def _fingerprint(items: list[dict]) -> str:
    """生成告警指纹用于去重 — 同 org 相同 sync_type 集合视为同一告警"""
    parts = sorted(
        f"{i['sync_type']}:{i.get('error_count', 0) // 10}" for i in items
    )  # error_count 每 10 阶视为一档，避免每次扫描数字+1 都触发新告警
    s = ",".join(parts)
    return hashlib.md5(s.encode()).hexdigest()[:12]


async def _maybe_alert_org(
    db: Any, org_id: str, items: list[dict],
) -> None:
    """对单个 org 的告警做去重 + 触发

    Dedupe 策略：先 check（GET），通道全部尝试完后再 SET。
    这样如果某个通道（如企微）失败，下次扫描还能再试，不会被锁 1h。
    Redis 不可用时不去重 — 宁可重复推也不能漏。
    """
    from core.redis import get_redis

    fingerprint = _fingerprint(items)
    dedupe_key = f"erp_sync_healthcheck:fired:{org_id}:{fingerprint}"

    redis = await get_redis()
    if redis:
        # 先 check：如果已存在，1 小时内已告警过，直接 skip
        if await redis.exists(dedupe_key):
            return

    # 构造可读消息
    lines = [f"🔴 ERP 同步异常告警 | org={org_id}"]
    for i in items:
        err = (i.get("last_error") or "")[:80]
        lines.append(
            f"  • {i['sync_type']}: {i['error_count']} 次失败 — {err}"
        )
    msg = "\n".join(lines)

    # 1) 兜底：高优先级日志（任何告警通道都会失败时这是最后保险）
    logger.error(msg)

    # 2) 写入 Redis 告警状态位（供前端 AdminPanel 读取展示）
    if redis:
        try:
            state_key = f"{ALERT_STATE_PREFIX}:{org_id}"
            payload = json.dumps(
                {
                    "items": items,
                    "fingerprint": fingerprint,
                    "msg": msg,
                },
                default=str,
                ensure_ascii=False,
            )
            await redis.set(state_key, payload, ex=ALERT_STATE_TTL)
        except Exception as e:
            logger.warning(
                f"ErpSyncHealthcheck redis state write failed | "
                f"org={org_id} | error={e}"
            )

    # 3) 企微推送（best-effort，任何依赖缺失都跳过）
    push_ok = True
    if org_id != "system":
        try:
            await _push_to_org_admins(db, org_id, msg)
        except Exception as e:
            push_ok = False
            logger.error(
                f"ErpSyncHealthcheck wecom push failed | "
                f"org={org_id} | error={e}"
            )

    # 4) 所有通道尝试完后再 SET dedupe key（推送失败不锁，下次扫描可重试）
    if redis and push_ok:
        try:
            await redis.set(dedupe_key, "1", ex=DEDUPE_TTL)
        except Exception as e:
            logger.warning(
                f"ErpSyncHealthcheck dedupe write failed | "
                f"org={org_id} | error={e}"
            )


async def _push_to_org_admins(db: Any, org_id: str, msg: str) -> None:
    """通过该企业的自建应用，推送告警给企业的 owner/admin。

    链路：org_members(role=owner|admin) → wecom_user_mappings(user_id) → 自建应用 send_text。
    任一步缺失则跳过（这是 best-effort，不影响主流程）。

    注意: 必须查 org_members 表的 role 字段（多租户成员关系），
    而不是 users.role（那是系统级，只有 super_admin/user）。
    更不能用 users.current_org_id 因为那只代表"当前切换到哪个 org"，
    不代表 ta 是这个 org 的管理员。
    """
    # 1) 找该 org 的 owner / admin（多租户成员关系）
    member_rows = await (
        db.table("org_members")
        .select("user_id, role")
        .eq("org_id", org_id)
        .in_("role", ["owner", "admin"])
        .eq("status", "active")
        .limit(10)
        .execute()
    )
    admin_ids = [m["user_id"] for m in (member_rows.data or [])]
    if not admin_ids:
        logger.info(
            f"ErpSyncHealthcheck no org owner/admin to notify | org={org_id}"
        )
        return

    # 2) 查这些管理员的 wecom_userid 映射
    mapping_rows = await (
        db.table("wecom_user_mappings")
        .select("wecom_userid, user_id")
        .in_("user_id", admin_ids)
        .eq("org_id", org_id)
        .execute()
    )
    mappings = mapping_rows.data or []
    if not mappings:
        logger.info(
            f"ErpSyncHealthcheck no wecom mapping for admins | org={org_id}"
        )
        return

    # 3) 加载企业的自建应用凭证
    from services.org.config_resolver import AsyncOrgConfigResolver
    resolver = AsyncOrgConfigResolver(db)
    agent_id_raw = await resolver.get(org_id, "wecom_agent_id")
    agent_secret = await resolver.get(org_id, "wecom_agent_secret")
    if not agent_id_raw or not agent_secret:
        logger.info(
            f"ErpSyncHealthcheck no wecom agent config | org={org_id}"
        )
        return

    # 4) 取 corp_id
    org_result = await (
        db.table("organizations")
        .select("wecom_corp_id")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    corp_id = (org_result.data or {}).get("wecom_corp_id") if org_result else None
    if not corp_id:
        logger.info(
            f"ErpSyncHealthcheck no corp_id on organization | org={org_id}"
        )
        return

    # 5) 推送
    from services.wecom.app_message_sender import OrgWecomCreds, send_text
    creds = OrgWecomCreds(
        org_id=org_id,
        corp_id=corp_id,
        agent_id=int(agent_id_raw),
        agent_secret=agent_secret,
    )
    for m in mappings:
        try:
            await send_text(m["wecom_userid"], msg, creds)
            logger.info(
                f"ErpSyncHealthcheck alert pushed | "
                f"org={org_id} | userid={m['wecom_userid']}"
            )
        except Exception as e:
            logger.error(
                f"ErpSyncHealthcheck send_text failed | "
                f"userid={m['wecom_userid']} | error={e}"
            )
