"""跨 Worker 对话摘要协调测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from services.agent.runtime.context.summary_coordination import (
    SummaryCoordination,
    acquire_summary_coordination,
    finish_summary_coordination,
    summary_prefix_fingerprint,
)


def test_summary_prefix_changes_with_revision() -> None:
    first = summary_prefix_fingerprint("conv-1", 2, 5)
    same = summary_prefix_fingerprint("conv-1", 2, 5)
    advanced = summary_prefix_fingerprint("conv-1", 2, 6)

    assert first == same
    assert first != advanced
    assert "conv-1" not in first


@pytest.mark.asyncio
async def test_failure_suppression_skips_same_prefix() -> None:
    redis = AsyncMock()
    redis.exists.return_value = True

    with patch(
        "core.redis.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        coordination = await acquire_summary_coordination("conv-1", 2, 5)

    assert coordination.outcome == "suppressed"
    assert coordination.should_run is False


@pytest.mark.asyncio
async def test_lock_conflict_skips_concurrent_summary() -> None:
    redis = AsyncMock()
    redis.exists.return_value = False

    with (
        patch("core.redis.get_redis", new=AsyncMock(return_value=redis)),
        patch(
            "core.redis.RedisClient.acquire_lock",
            new=AsyncMock(return_value=None),
        ) as acquire,
    ):
        coordination = await acquire_summary_coordination("conv-1", 2, 5)

    assert coordination.outcome == "in_flight"
    assert coordination.should_run is False
    acquire.assert_awaited_once()
    assert acquire.await_args.kwargs["timeout"] == 60


@pytest.mark.asyncio
async def test_redis_failure_degrades_to_database_cas() -> None:
    redis = AsyncMock()
    redis.exists.side_effect = ConnectionError("redis down")

    with patch(
        "core.redis.get_redis",
        new=AsyncMock(return_value=redis),
    ):
        coordination = await acquire_summary_coordination("conv-1", 2, 5)

    assert coordination.outcome == "degraded"
    assert coordination.should_run is True


@pytest.mark.asyncio
async def test_failed_summary_sets_ttl_before_owned_lock_release() -> None:
    redis = AsyncMock()
    coordination = SummaryCoordination(
        outcome="acquired",
        lock_key="context-summary:hash",
        lock_token="token",
        suppression_key="context:summary:suppressed:hash",
    )

    with (
        patch("core.redis.get_redis", new=AsyncMock(return_value=redis)),
        patch(
            "core.redis.RedisClient.release_lock",
            new=AsyncMock(return_value=True),
        ) as release,
    ):
        await finish_summary_coordination(coordination, failed=True)

    redis.set.assert_awaited_once_with(
        coordination.suppression_key,
        "1",
        ex=300,
    )
    release.assert_awaited_once_with(
        coordination.lock_key,
        coordination.lock_token,
    )


@pytest.mark.asyncio
async def test_success_only_releases_owned_lock() -> None:
    redis_get = AsyncMock()
    coordination = SummaryCoordination(
        outcome="acquired",
        lock_key="context-summary:hash",
        lock_token="token",
        suppression_key="context:summary:suppressed:hash",
    )

    with (
        patch("core.redis.get_redis", new=redis_get),
        patch(
            "core.redis.RedisClient.release_lock",
            new=AsyncMock(return_value=True),
        ) as release,
    ):
        await finish_summary_coordination(coordination, failed=False)

    redis_get.assert_not_awaited()
    release.assert_awaited_once()
