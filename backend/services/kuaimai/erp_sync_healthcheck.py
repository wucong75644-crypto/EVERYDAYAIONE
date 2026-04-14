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
# 2026-04-11 调整：10 → 3
#   选 10 的原因（旧）：单次网络抖动/限流误差 1~3 次很常见，10 次必然是真问题
#   选 3 的原因（新）：platform_map 等低频同步 6h/轮，10 次=60h 才告警太晚；
#                    client 层已有指数退避兜底瞬时抖动，到 sync 层连续 3 次必然是真问题
ALERT_THRESHOLD = 3

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
    # 用 ALERT_THRESHOLD 做分档：每升一档（再失败 ALERT_THRESHOLD 次）触发一次新告警
    # 例如阈值=3：error_count 3-5 一档，6-8 一档，9-11 一档...
    bucket = max(ALERT_THRESHOLD, 1)
    parts = sorted(
        f"{i['sync_type']}:{i.get('error_count', 0) // bucket}" for i in items
    )
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
    """通过智能机器人 WS 推送告警给企业的 owner/admin。

    链路：org_members(role=owner|admin) → wecom_user_mappings(user_id)
         → push_dispatcher → Redis pub/sub → ws_runner → 智能机器人 WS。
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

    # 3) 通过 MessageGateway 统一存消息 + 推企微（先存后推）
    # MessageGateway 内部 .execute() 是同步调用，必须传同步 db
    from core.database import get_db
    from services.message_gateway import MessageGateway
    gateway = MessageGateway(get_db())
    for m in mappings:
        try:
            await gateway.save_system_message(
                user_id=m["user_id"],
                org_id=org_id,
                text=msg,
                source="error_alert",
            )
            logger.info(
                f"ErpSyncHealthcheck alert saved+pushed | "
                f"org={org_id} | user_id={m['user_id']}"
            )
        except Exception as e:
            logger.error(
                f"ErpSyncHealthcheck push failed | "
                f"user_id={m['user_id']} | error={e}"
            )


# ── Token 刷新失败实时告警（快档） ────────────────────────


async def push_token_refresh_alert(
    org_id: str | None, error_msg: str,
) -> None:
    """Token 刷新失败立即推送告警 — 不等 healthcheck 周期扫描。

    场景：access_token + refresh_token 双重失效（30 天硬到期）。
    此时 client.refresh_token() 会返回 False 并抛 KuaiMaiTokenExpiredError，
    上层 sync 失败 → consecutive_errors 涨 → healthcheck 5 分钟后扫到。
    但每轮 sync 间隔 6 小时，要等 ALERT_THRESHOLD（3）次失败 = 18 小时才告警。

    本函数提供"快档"路径：refresh 失败时直接调用，秒级推送企微，
    不依赖 sync 状态，不依赖 healthcheck 周期。

    设计：
    - 自己拿 async db（get_async_db 单例），调用方无需感知 db 细节
    - 任何步骤失败都 best-effort（不抛异常），保证不影响 client 主流程
    - Redis 状态位 `kuaimai:refresh_alert_fired:{org_id}` 去重，TTL 1 小时

    Args:
        org_id: 触发告警的企业 ID（None=散客模式只打日志）
        error_msg: refresh 失败原因（包含 code/msg）
    """
    if not org_id:
        # 散客模式无 org_members 链路，跳过推送
        logger.error(
            f"KuaiMai token refresh failed (system mode) | error={error_msg}"
        )
        return

    # 1) 兜底日志（任何渠道失败时这是最后保险）
    logger.error(
        f"🔴 KuaiMai token refresh FAILED | org={org_id} | error={error_msg}"
    )

    # 2) Redis 去重检查
    redis = None
    try:
        from core.redis import get_redis
        redis = await get_redis()
        if redis:
            dedupe_key = f"kuaimai:refresh_alert_fired:{org_id}"
            if await redis.exists(dedupe_key):
                logger.info(
                    f"KuaiMai refresh alert deduped | org={org_id}"
                )
                return
    except Exception as e:
        logger.warning(
            f"KuaiMai refresh alert dedupe check failed | org={org_id} | error={e}"
        )

    # 3) 拿 async db 推送（best-effort）
    msg = (
        f"🔴 快麦 ERP Token 刷新失败\n"
        f"org={org_id}\n"
        f"错误：{error_msg[:200]}\n\n"
        f"🛠 处理建议：\n"
        f"  1. 联系快麦客服重新生成 access_token + refresh_token\n"
        f"  2. 在管理面板更新企业 ERP 凭证\n"
        f"  3. 重启 backend 服务（或等待 30 分钟 client 缓存自动失效）"
    )

    push_ok = False
    try:
        from core.database import get_async_db
        db = await get_async_db()
        await _push_to_org_admins(db, org_id, msg)
        push_ok = True
    except Exception as e:
        logger.error(
            f"KuaiMai refresh alert push failed | org={org_id} | error={e}"
        )

    # 4) 推送成功后才设置去重 key（推送失败下次还能再试）
    if push_ok and redis:
        try:
            await redis.set(
                f"kuaimai:refresh_alert_fired:{org_id}",
                error_msg[:200],
                ex=3600,  # 1 小时
            )
        except Exception as e:
            logger.warning(
                f"KuaiMai refresh alert dedupe set failed | "
                f"org={org_id} | error={e}"
            )
