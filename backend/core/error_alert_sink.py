"""全局错误监控 — loguru ERROR sink + 内存队列 + DB 持久化 + 致命告警

架构：
1. loguru sink（同步）：拦截 ERROR/CRITICAL → 写入 asyncio.Queue
2. 后台协程 error_log_consumer()：从 Queue 批量消费 → 写 DB + 致命检测推企微
3. 30 天清理协程 error_log_cleanup_loop()：每天凌晨执行一次

致命级定义（触发企微实时推送）：
- DB 连接失败（connection refused / too many connections / pool）
- Redis 连接失败（Redis 连接获取失败 / Redis 健康检查失败）
- AI 全挂（all models failed / circuit.*breaker.*open / Provider.*熔断中）
- 积分丢失（CREDIT_LOSS_RISK）
- logger.critical() 级别的所有日志
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import traceback as _traceback_mod
from datetime import datetime, timezone
from typing import Any

from loguru import logger


def _format_exception(exc_tuple: tuple | None) -> str | None:
    """将 loguru 的 (type, value, traceback) 元组格式化为完整 stack trace 字符串。"""
    if not exc_tuple:
        return None
    try:
        tp, val, tb = exc_tuple
        return "".join(_traceback_mod.format_exception(tp, val, tb))
    except Exception:
        return str(exc_tuple)

# ── 内存队列（sink 写入，consumer 消费）──────────────────────
# maxsize 防止内存溢出：超出时 sink 丢弃（宁丢日志不阻塞业务）
_error_queue: asyncio.Queue | None = None
_MAX_QUEUE_SIZE = 5000

# ── 致命级正则（编译一次，匹配快）──────────────────────────
_CRITICAL_PATTERNS = [
    # DB 故障
    re.compile(r"connection refused|too many connections|pool.*exhaust", re.I),
    # Redis 故障
    re.compile(r"Redis.{0,20}(?:连接|超时|失败|health.*fail)", re.I),
    # AI 全挂
    re.compile(r"all (?:models|providers) failed", re.I),
    re.compile(r"circuit.*breaker.*open|Provider.*熔断中", re.I),
    # 积分丢失
    re.compile(r"CREDIT_LOSS_RISK"),
]

# ── 去重窗口：同指纹致命告警 30 分钟内只推一次 ───────────────
_CRITICAL_DEDUPE_TTL = 1800  # 秒

# ── 消费批次配置 ─────────────────────────────────────────
_FLUSH_INTERVAL = 5  # 秒，最长等待时间
_FLUSH_BATCH_SIZE = 50  # 最大批次大小

# ── 消息截断 ─────────────────────────────────────────────
_MAX_MESSAGE_LEN = 2000
_MAX_TRACEBACK_LEN = 5000

# 防递归标记：sink 内部的 DB/推送操作产生的 error 不再入队
_SINK_INTERNAL_TAG = "[ErrorSink]"


def _get_queue() -> asyncio.Queue:
    """懒初始化全局队列（必须在 event loop 存在后调用）"""
    global _error_queue
    if _error_queue is None:
        _error_queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
    return _error_queue


def _fingerprint(module: str, function: str, message: str) -> str:
    """生成错误指纹 — 同一位置同类错误聚合为一条"""
    # 去掉消息中的动态部分（数字、UUID、时间戳）用于聚合
    stable_msg = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", message)
    stable_msg = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<ts>", stable_msg)
    stable_msg = re.sub(r"\d{5,}", "<num>", stable_msg)
    raw = f"{module}:{function}:{stable_msg[:200]}"
    return hashlib.md5(raw.encode()).hexdigest()


def _is_critical(level: str, message: str) -> bool:
    """判断是否致命级"""
    if level == "CRITICAL":
        return True
    return any(p.search(message) for p in _CRITICAL_PATTERNS)


def _extract_org_id(message: str) -> str | None:
    """尝试从日志消息中提取 org_id"""
    m = re.search(r"org[_=]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", message, re.I)
    return m.group(1) if m else None


# ── loguru sink 函数（同步，在 logger.error 时被调用）────────


def error_sink(message) -> None:
    """loguru sink：拦截 ERROR+ 日志，写入异步队列。

    设计约束：
    - 必须是同步函数（loguru sink 接口要求）
    - 不能阻塞（queue full 时直接丢弃）
    - 不能产生递归（内部错误不入队）
    """
    record = message.record

    # 防递归：sink 内部操作产生的日志不再入队
    if _SINK_INTERNAL_TAG in record["message"]:
        return

    entry = {
        "level": record["level"].name,
        "module": record["name"] or "",
        "function": record["function"] or "",
        "line": record["line"],
        "message": str(record["message"])[:_MAX_MESSAGE_LEN],
        "traceback": _format_exception(record["exception"]) if record["exception"] else None,
        "timestamp": record["time"].astimezone(timezone.utc),
    }

    if entry["traceback"]:
        entry["traceback"] = entry["traceback"][:_MAX_TRACEBACK_LEN]

    entry["fingerprint"] = _fingerprint(entry["module"], entry["function"], entry["message"])
    entry["is_critical"] = _is_critical(entry["level"], entry["message"])
    entry["org_id"] = _extract_org_id(entry["message"])

    try:
        queue = _get_queue()
        queue.put_nowait(entry)
    except asyncio.QueueFull:
        pass  # 宁丢日志不阻塞业务
    except Exception:
        pass  # sink 绝不抛异常


# ── 后台消费协程 ─────────────────────────────────────────


async def error_log_consumer(db: Any) -> None:
    """后台协程：从队列批量消费错误日志 → 写 DB + 致命推企微。

    Args:
        db: AsyncLocalDBClient 单例（lifespan 传入）
    """
    logger.info(f"{_SINK_INTERNAL_TAG} error_log_consumer started")
    queue = _get_queue()

    while True:
        batch: list[dict] = []
        try:
            # 等第一条（阻塞直到有数据）
            first = await queue.get()
            batch.append(first)

            # 攒批：最多等 _FLUSH_INTERVAL 秒或攒满 _FLUSH_BATCH_SIZE 条
            deadline = asyncio.get_running_loop().time() + _FLUSH_INTERVAL
            while len(batch) < _FLUSH_BATCH_SIZE:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            await _flush_batch(db, batch)

        except asyncio.CancelledError:
            # 关闭前刷完队列残留
            while not queue.empty():
                try:
                    batch.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if batch:
                try:
                    await _flush_batch(db, batch)
                except Exception:
                    pass
            logger.info(f"{_SINK_INTERNAL_TAG} error_log_consumer stopped")
            return
        except Exception as e:
            logger.warning(f"{_SINK_INTERNAL_TAG} consumer loop error | {e}")
            await asyncio.sleep(2)


async def _flush_batch(db: Any, batch: list[dict]) -> None:
    """批量写入 DB + 致命告警推企微"""
    if not batch:
        return

    # 1) 按指纹聚合（同批次内相同指纹只写一条，count 累加）
    merged: dict[str, dict] = {}
    for entry in batch:
        fp = entry["fingerprint"]
        if fp in merged:
            merged[fp]["occurrence_count"] += 1
            merged[fp]["last_seen_at"] = entry["timestamp"]
            # 保留最严重的级别
            if entry["is_critical"]:
                merged[fp]["is_critical"] = True
        else:
            merged[fp] = {
                **entry,
                "occurrence_count": 1,
                "first_seen_at": entry["timestamp"],
                "last_seen_at": entry["timestamp"],
            }

    # 2) 写 DB（逐条 upsert：已有未解决的同指纹 → count+1 + 更新 last_seen）
    for fp, entry in merged.items():
        try:
            await _upsert_error_log(db, entry)
        except Exception as e:
            logger.warning(
                f"{_SINK_INTERNAL_TAG} DB write failed | fp={fp[:8]} | {e}"
            )

    # 3) 致命级推企微
    criticals = [e for e in merged.values() if e["is_critical"]]
    if criticals:
        await _push_critical_alerts(db, criticals)


async def _upsert_error_log(db: Any, entry: dict) -> None:
    """单条 upsert：fingerprint 未解决的已存在 → 累加 count + 更新 last_seen，
    否则插入新行。

    不能用通用 upsert（它生成 SET col = EXCLUDED.col 会覆盖 count），
    必须用 raw SQL 实现 occurrence_count += EXCLUDED.occurrence_count。
    """
    ts_str = entry["last_seen_at"].isoformat() if isinstance(entry["last_seen_at"], datetime) else str(entry["last_seen_at"])
    first_str = entry["first_seen_at"].isoformat() if isinstance(entry["first_seen_at"], datetime) else str(entry["first_seen_at"])

    sql = """
        INSERT INTO error_logs
            (fingerprint, level, module, function, line, message, traceback,
             occurrence_count, first_seen_at, last_seen_at, org_id, is_critical)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (fingerprint) WHERE is_resolved = FALSE
        DO UPDATE SET
            occurrence_count = error_logs.occurrence_count + EXCLUDED.occurrence_count,
            last_seen_at     = GREATEST(error_logs.last_seen_at, EXCLUDED.last_seen_at),
            first_seen_at    = LEAST(error_logs.first_seen_at, EXCLUDED.first_seen_at),
            level            = CASE WHEN EXCLUDED.level = 'CRITICAL' THEN 'CRITICAL'
                                    ELSE error_logs.level END,
            is_critical      = error_logs.is_critical OR EXCLUDED.is_critical,
            message          = EXCLUDED.message,
            traceback        = COALESCE(EXCLUDED.traceback, error_logs.traceback)
    """
    params = (
        entry["fingerprint"],
        entry["level"],
        entry["module"],
        entry["function"],
        entry.get("line"),
        entry["message"],
        entry.get("traceback"),
        entry["occurrence_count"],
        first_str,
        ts_str,
        entry.get("org_id"),
        entry["is_critical"],
    )

    async with db.pool.connection() as conn:
        await conn.set_autocommit(True)
        await conn.execute(sql, params)


async def _push_critical_alerts(db: Any, criticals: list[dict]) -> None:
    """致命级错误推企微（best-effort，去重）"""
    from core.redis import get_redis

    redis = None
    try:
        redis = await get_redis()
    except Exception:
        pass

    for entry in criticals:
        fp = entry["fingerprint"]
        dedupe_key = f"error_monitor:critical:{fp}"

        # Redis 去重
        if redis:
            try:
                if await redis.exists(dedupe_key):
                    continue
            except Exception:
                pass

        # 构造消息
        msg = (
            f"🔴 系统致命错误告警\n"
            f"级别：{entry['level']}\n"
            f"模块：{entry['module']}:{entry['function']}\n"
            f"错误：{entry['message'][:300]}\n"
            f"次数：{entry['occurrence_count']}\n"
            f"时间：{entry['last_seen_at']}"
        )

        # 推企微（遍历所有有自建应用配置的 org 的 owner/admin）
        pushed = False
        try:
            org_id = entry.get("org_id")
            if org_id:
                # 有明确 org → 推给该 org 的 admin
                await _push_to_admins(db, org_id, msg)
                pushed = True
            else:
                # 无 org 上下文 → 推给所有 super_admin
                await _push_to_super_admins(db, msg)
                pushed = True
        except Exception as e:
            logger.warning(
                f"{_SINK_INTERNAL_TAG} critical push failed | fp={fp[:8]} | {e}"
            )

        # 推送成功后设置去重
        if pushed and redis:
            try:
                await redis.set(dedupe_key, "1", ex=_CRITICAL_DEDUPE_TTL)
            except Exception:
                pass


async def _push_to_admins(db: Any, org_id: str, msg: str) -> None:
    """复用 healthcheck 的推送链路"""
    from services.kuaimai.erp_sync_healthcheck import _push_to_org_admins
    await _push_to_org_admins(db, org_id, msg)


async def _push_to_super_admins(db: Any, msg: str) -> None:
    """致命错误无 org 上下文时，推给平台 super_admin"""
    try:
        result = await (
            db.table("users")
            .select("id")
            .eq("role", "super_admin")
            .limit(5)
            .execute()
        )
        admin_ids = [r["id"] for r in (result.data or [])]
        if not admin_ids:
            return

        # 查 super_admin 所属的第一个 org（用于获取企微推送凭证）
        for admin_id in admin_ids:
            member_result = await (
                db.table("org_members")
                .select("org_id")
                .eq("user_id", admin_id)
                .in_("role", ["owner", "admin"])
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            if member_result.data:
                org_id = member_result.data[0]["org_id"]
                await _push_to_admins(db, org_id, msg)
                return  # 推一个 org 就够了
    except Exception as e:
        logger.warning(f"{_SINK_INTERNAL_TAG} super_admin push failed | {e}")


# ── 30 天清理协程 ────────────────────────────────────────


async def error_log_cleanup_loop(db: Any) -> None:
    """每天凌晨 3 点清理 30 天前的已解决错误日志"""
    logger.info(f"{_SINK_INTERNAL_TAG} cleanup loop started | retention=30d")
    while True:
        try:
            await asyncio.sleep(_seconds_until_3am())
            result = await db.rpc("cleanup_old_error_logs", {"retention_days": 30}).execute()
            deleted = result.data if result.data else 0
            if deleted:
                logger.info(
                    f"{_SINK_INTERNAL_TAG} cleanup done | deleted={deleted}"
                )
        except asyncio.CancelledError:
            logger.info(f"{_SINK_INTERNAL_TAG} cleanup loop stopped")
            return
        except Exception as e:
            logger.warning(f"{_SINK_INTERNAL_TAG} cleanup error | {e}")
            await asyncio.sleep(3600)  # 出错等 1 小时再试


def _seconds_until_3am() -> float:
    """计算距离下一个凌晨 3 点的秒数"""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()
