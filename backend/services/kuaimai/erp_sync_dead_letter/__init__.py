"""
ERP 同步死信队列（Dead Letter Queue）

detail API 调用失败时，将单据信息写入 erp_sync_dead_letter 表。
独立消费者协程以指数退避策略异步重试，不阻塞主同步流程。

设计参考：Queue-Based Exponential Backoff + DLQ Pattern

包结构（2026-04-11 拆分）：
- queue.py             — record_dead_letter / _calc_next_retry / 常量
- consumer.py          — consume_dead_letters / _process_batch / _retry_one /
                         _get_or_create_client / _mark_batch_retry_failed
- platform_map_retry.py — _retry_platform_map_batch + 2 helpers (Bug 2)

外部 import 路径不变：
    from services.kuaimai.erp_sync_dead_letter import record_dead_letter
    from services.kuaimai.erp_sync_dead_letter import consume_dead_letters
"""

# ── 公开 API ──────────────────────────────────────────────
from services.kuaimai.erp_sync_dead_letter.queue import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    CONSUMER_BATCH_SIZE,
    CONSUMER_POLL_INTERVAL,
    DEFAULT_MAX_RETRIES,
    _calc_next_retry,
    record_dead_letter,
)
from services.kuaimai.erp_sync_dead_letter.consumer import (
    _CLIENT_CACHE_TTL,
    _get_or_create_client,
    _mark_batch_retry_failed,
    _process_batch,
    _retry_one,
    consume_dead_letters,
)
from services.kuaimai.erp_sync_dead_letter.platform_map_retry import (
    _apply_platform_map_success,
    _bump_platform_map_retry,
    _retry_platform_map_batch,
)


__all__ = [
    # 公开 API
    "record_dead_letter",
    "consume_dead_letters",
    # 常量
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_MAX_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "CONSUMER_POLL_INTERVAL",
    "CONSUMER_BATCH_SIZE",
    "_CLIENT_CACHE_TTL",
    # 内部 helper（测试需要）
    "_calc_next_retry",
    "_process_batch",
    "_get_or_create_client",
    "_mark_batch_retry_failed",
    "_retry_one",
    # platform_map 重试（Bug 2）
    "_retry_platform_map_batch",
    "_bump_platform_map_retry",
    "_apply_platform_map_success",
]
