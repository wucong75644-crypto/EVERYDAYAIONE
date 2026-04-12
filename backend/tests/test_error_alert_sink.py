"""error_alert_sink 单元测试

覆盖：
- _fingerprint: 稳定性、动态部分替换（UUID/时间戳/长数字）
- _is_critical: CRITICAL 级别 + 各模式匹配 + 非致命判断
- _extract_org_id: 提取 org_id / 无匹配返回 None
- error_sink: 入队、防递归、队满丢弃
- _flush_batch: 同指纹聚合、count 累加、致命级检测
- _seconds_until_3am: 时间计算
- _serialize_row (error_monitor): 序列化
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.error_alert_sink import (
    _SINK_INTERNAL_TAG,
    _extract_org_id,
    _fingerprint,
    _flush_batch,
    _is_critical,
    _seconds_until_3am,
    error_sink,
)


# ── _fingerprint ─────────────────────────────────────────


class TestFingerprint:
    def test_same_input_same_output(self):
        fp1 = _fingerprint("mod", "func", "some error happened")
        fp2 = _fingerprint("mod", "func", "some error happened")
        assert fp1 == fp2

    def test_different_module_different_fp(self):
        fp1 = _fingerprint("mod_a", "func", "error")
        fp2 = _fingerprint("mod_b", "func", "error")
        assert fp1 != fp2

    def test_uuid_replaced(self):
        msg1 = "org=eadc4c11-7e83-4279-a849-cfe0cbf6982b failed"
        msg2 = "org=11111111-2222-3333-4444-555555555555 failed"
        assert _fingerprint("m", "f", msg1) == _fingerprint("m", "f", msg2)

    def test_timestamp_replaced(self):
        msg1 = "error at 2026-04-12T10:20:30"
        msg2 = "error at 2026-01-01 00:00:00"
        assert _fingerprint("m", "f", msg1) == _fingerprint("m", "f", msg2)

    def test_long_numbers_replaced(self):
        msg1 = "request 12345678 failed"
        msg2 = "request 99999999 failed"
        assert _fingerprint("m", "f", msg1) == _fingerprint("m", "f", msg2)

    def test_short_numbers_not_replaced(self):
        """4 位以下数字保留（可能是端口、行号等有意义的值）"""
        msg1 = "port 8000 error"
        msg2 = "port 9000 error"
        fp1 = _fingerprint("m", "f", msg1)
        fp2 = _fingerprint("m", "f", msg2)
        assert fp1 != fp2

    def test_returns_32_char_hex(self):
        fp = _fingerprint("m", "f", "error")
        assert len(fp) == 32
        assert all(c in "0123456789abcdef" for c in fp)


# ── _is_critical ─────────────────────────────────────────


class TestIsCritical:
    def test_critical_level_always_true(self):
        assert _is_critical("CRITICAL", "any message") is True

    def test_db_connection_refused(self):
        assert _is_critical("ERROR", "connection refused to postgres") is True

    def test_db_too_many_connections(self):
        assert _is_critical("ERROR", "too many connections") is True

    def test_db_pool_exhausted(self):
        assert _is_critical("ERROR", "pool exhausted, no connections") is True

    def test_redis_failure_chinese(self):
        assert _is_critical("ERROR", "Redis 连接获取失败") is True

    def test_redis_health_fail(self):
        assert _is_critical("ERROR", "Redis health check failed") is True

    def test_all_models_failed(self):
        assert _is_critical("ERROR", "all models failed, using raw results") is True

    def test_all_providers_failed(self):
        assert _is_critical("ERROR", "all providers failed") is True

    def test_circuit_breaker_open(self):
        assert _is_critical("ERROR", "Circuit breaker | provider=kie | closed → open") is True

    def test_provider_fused_chinese(self):
        assert _is_critical("ERROR", "Provider openrouter 熔断中") is True

    def test_credit_loss_risk(self):
        assert _is_critical("ERROR", "CREDIT_LOSS_RISK: refund failed for user 123") is True

    def test_normal_error_not_critical(self):
        assert _is_critical("ERROR", "File not found: /tmp/test.txt") is False

    def test_timeout_not_critical(self):
        """普通超时不算致命（ReadTimeout 等属于 TRANSIENT）"""
        assert _is_critical("ERROR", "ReadTimeout connecting to kuaimai API") is False

    def test_partial_redis_match_not_critical(self):
        """Redis 关键词必须搭配失败相关词"""
        assert _is_critical("ERROR", "Redis cache set OK") is False


# ── _extract_org_id ──────────────────────────────────────


class TestExtractOrgId:
    def test_org_equals_format(self):
        msg = "error | org=eadc4c11-7e83-4279-a849-cfe0cbf6982b | timeout"
        assert _extract_org_id(msg) == "eadc4c11-7e83-4279-a849-cfe0cbf6982b"

    def test_org_underscore_format(self):
        """org_ 后紧跟 UUID 才匹配（如 org_eadc...），org_id= 不匹配"""
        msg = "sync failed org_eadc4c11-7e83-4279-a849-cfe0cbf6982b"
        result = _extract_org_id(msg)
        assert result == "eadc4c11-7e83-4279-a849-cfe0cbf6982b"

    def test_no_org_id(self):
        assert _extract_org_id("generic error without org context") is None

    def test_invalid_uuid_format(self):
        assert _extract_org_id("org=not-a-uuid") is None


# ── error_sink ───────────────────────────────────────────


class TestErrorSink:
    def _make_message(self, msg: str, level: str = "ERROR"):
        """构造模拟的 loguru message 对象"""
        record = {
            "level": MagicMock(name=level),
            "name": "test.module",
            "function": "test_func",
            "line": 42,
            "message": msg,
            "exception": None,
            "time": datetime.now(timezone.utc),
        }
        record["level"].name = level
        message = MagicMock()
        message.record = record
        return message

    def test_entry_added_to_queue(self):
        """正常错误应入队"""
        from core.error_alert_sink import _get_queue
        queue = _get_queue()
        # 清空队列
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        error_sink(self._make_message("test error"))
        assert not queue.empty()
        entry = queue.get_nowait()
        assert entry["message"] == "test error"
        assert entry["module"] == "test.module"
        assert entry["function"] == "test_func"

    def test_recursive_prevention(self):
        """带 _SINK_INTERNAL_TAG 的消息不入队"""
        from core.error_alert_sink import _get_queue
        queue = _get_queue()
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        error_sink(self._make_message(f"{_SINK_INTERNAL_TAG} DB write failed"))
        assert queue.empty()

    def test_critical_flag_set(self):
        """致命级消息应标记 is_critical=True"""
        from core.error_alert_sink import _get_queue
        queue = _get_queue()
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        error_sink(self._make_message("connection refused"))
        entry = queue.get_nowait()
        assert entry["is_critical"] is True

    def test_queue_full_no_exception(self):
        """队列满时不抛异常"""
        with patch("core.error_alert_sink._get_queue") as mock_q:
            q = MagicMock()
            q.put_nowait.side_effect = asyncio.QueueFull()
            mock_q.return_value = q
            # 不应抛异常
            error_sink(self._make_message("error"))


# ── _flush_batch ─────────────────────────────────────────


class TestFlushBatch:
    @pytest.mark.asyncio
    async def test_empty_batch_noop(self):
        db = MagicMock()
        await _flush_batch(db, [])
        # 不应有任何 DB 调用

    @pytest.mark.asyncio
    async def test_same_fingerprint_merged(self):
        """同指纹条目应聚合，count 累加"""
        now = datetime.now(timezone.utc)
        entries = [
            {
                "fingerprint": "abc123",
                "level": "ERROR",
                "module": "m",
                "function": "f",
                "line": 1,
                "message": "err",
                "traceback": None,
                "is_critical": False,
                "org_id": None,
                "timestamp": now,
            },
            {
                "fingerprint": "abc123",
                "level": "ERROR",
                "module": "m",
                "function": "f",
                "line": 1,
                "message": "err",
                "traceback": None,
                "is_critical": False,
                "org_id": None,
                "timestamp": now,
            },
        ]

        with patch("core.error_alert_sink._upsert_error_log", new_callable=AsyncMock) as mock_upsert:
            with patch("core.error_alert_sink._push_critical_alerts", new_callable=AsyncMock):
                await _flush_batch(MagicMock(), entries)

            # 只调用一次 upsert（两条聚合为一条）
            assert mock_upsert.call_count == 1
            merged_entry = mock_upsert.call_args[0][1]
            assert merged_entry["occurrence_count"] == 2

    @pytest.mark.asyncio
    async def test_different_fingerprints_separate(self):
        """不同指纹应分开写"""
        now = datetime.now(timezone.utc)
        base = {
            "level": "ERROR", "module": "m", "function": "f",
            "line": 1, "message": "err", "traceback": None,
            "is_critical": False, "org_id": None, "timestamp": now,
        }
        entries = [
            {**base, "fingerprint": "aaa"},
            {**base, "fingerprint": "bbb"},
        ]

        with patch("core.error_alert_sink._upsert_error_log", new_callable=AsyncMock) as mock_upsert:
            with patch("core.error_alert_sink._push_critical_alerts", new_callable=AsyncMock):
                await _flush_batch(MagicMock(), entries)
            assert mock_upsert.call_count == 2

    @pytest.mark.asyncio
    async def test_critical_entries_trigger_push(self):
        """含致命级条目应触发推送"""
        now = datetime.now(timezone.utc)
        entries = [
            {
                "fingerprint": "crit1",
                "level": "CRITICAL",
                "module": "m",
                "function": "f",
                "line": 1,
                "message": "CREDIT_LOSS_RISK: refund failed",
                "traceback": None,
                "is_critical": True,
                "org_id": None,
                "timestamp": now,
            },
        ]

        with patch("core.error_alert_sink._upsert_error_log", new_callable=AsyncMock):
            with patch("core.error_alert_sink._push_critical_alerts", new_callable=AsyncMock) as mock_push:
                await _flush_batch(MagicMock(), entries)
            mock_push.assert_called_once()
            pushed = mock_push.call_args[0][1]
            assert len(pushed) == 1
            assert pushed[0]["is_critical"] is True

    @pytest.mark.asyncio
    async def test_upsert_failure_does_not_crash(self):
        """单条 upsert 失败不影响其他条目"""
        now = datetime.now(timezone.utc)
        base = {
            "level": "ERROR", "module": "m", "function": "f",
            "line": 1, "message": "err", "traceback": None,
            "is_critical": False, "org_id": None, "timestamp": now,
        }
        entries = [
            {**base, "fingerprint": "fail1"},
            {**base, "fingerprint": "ok1"},
        ]

        call_count = {"n": 0}

        async def mock_upsert(db, entry):
            call_count["n"] += 1
            if entry["fingerprint"] == "fail1":
                raise Exception("DB error")

        with patch("core.error_alert_sink._upsert_error_log", side_effect=mock_upsert):
            with patch("core.error_alert_sink._push_critical_alerts", new_callable=AsyncMock):
                await _flush_batch(MagicMock(), entries)
        # 两条都尝试了
        assert call_count["n"] == 2


# ── _seconds_until_3am ───────────────────────────────────


class TestSecondsUntil3am:
    def test_returns_positive_float(self):
        result = _seconds_until_3am()
        assert isinstance(result, float)
        assert result > 0

    def test_less_than_24_hours(self):
        result = _seconds_until_3am()
        assert result <= 86400  # 24 * 60 * 60

    def test_at_2am_returns_about_1_hour(self):
        """凌晨 2 点时距离 3 点约 3600 秒"""
        from unittest.mock import patch as _p
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        fake_now = _dt(2026, 4, 12, 2, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with _p("core.error_alert_sink.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: _dt(*a, **kw)
            result = _seconds_until_3am()
        assert 3500 < result < 3700

    def test_at_4am_returns_about_23_hours(self):
        """凌晨 4 点时距离下一个 3 点约 23 小时"""
        from unittest.mock import patch as _p
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        fake_now = _dt(2026, 4, 12, 4, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with _p("core.error_alert_sink.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: _dt(*a, **kw)
            result = _seconds_until_3am()
        assert 82000 < result < 83000
