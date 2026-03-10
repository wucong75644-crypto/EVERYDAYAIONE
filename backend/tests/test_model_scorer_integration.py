"""
模型动态评分 — 补充测试

覆盖：边界场景、异常路径、BackgroundTaskWorker 集成、
_format_period_dt、多模型混合状态、write 失败降级。
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============ Fixtures ============


@pytest.fixture(autouse=True)
def reset_kb_globals():
    """每个测试前重置知识库全局状态"""
    import services.knowledge_config as cfg

    cfg._pg_pool = None
    cfg._kb_available = None
    cfg._search_cache.clear()
    yield
    cfg._pg_pool = None
    cfg._kb_available = None
    cfg._search_cache.clear()


@pytest.fixture
def mock_cursor():
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.description = []
    return cursor


@pytest.fixture
def mock_conn(mock_cursor):
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_cursor),
        __aexit__=AsyncMock(return_value=False),
    ))
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_pg_connection(mock_conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_row(
    model_id: str = "gemini-3-pro",
    task_type: str = "chat",
    total: int = 100,
    success_count: int = 95,
    p75_latency: float = 2000.0,
    retry_count: int = 5,
    timeout_count: int = 1,
    hard_error_count: int = 2,
) -> dict:
    return {
        "model_id": model_id,
        "task_type": task_type,
        "total": total,
        "success_count": success_count,
        "p75_latency": p75_latency,
        "retry_count": retry_count,
        "timeout_count": timeout_count,
        "hard_error_count": hard_error_count,
        "period_start": datetime(2026, 3, 3, tzinfo=timezone.utc),
        "period_end": datetime(2026, 3, 10, tzinfo=timezone.utc),
    }


# ============ 边界场景补充 ============


class TestComputeRawScoreEdgeCases:
    """评分计算边界场景"""

    def test_p75_exceeds_max_latency(self):
        """P75 超过 30s 基准时延迟得 0 分"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(p75_latency=60000.0)  # 60s >> 30s max
        score = _compute_raw_score(row)
        # latency_score = max(0, 1 - 60000/30000) = 0.0
        assert 0.0 <= score <= 1.0
        # 与正常延迟对比应更低
        normal = _compute_raw_score(_make_row(p75_latency=2000.0))
        assert score < normal

    def test_hard_errors_double_penalty(self):
        """硬错误双倍惩罚使得 error_score=0"""
        from services.model_scorer import _compute_raw_score

        # 60 个硬错误 → error_score = max(0, 1 - 120/100) = 0
        row_bad = _make_row(hard_error_count=60, success_count=40)
        row_good = _make_row(hard_error_count=0, success_count=40)
        assert _compute_raw_score(row_bad) < _compute_raw_score(row_good)


class TestDetermineStatusEdgeCases:
    """审核规则边界"""

    def test_above_threshold_triggers_review(self):
        """分数变化 >0.1 → pending_review"""
        from services.model_scorer import _determine_status

        # |0.85 - 0.74| = 0.11 → pending_review
        assert _determine_status(0.85, 0.74, 100) == "pending_review"

    def test_just_below_threshold_auto_applied(self):
        """分数变化 <0.1 → auto_applied"""
        from services.model_scorer import _determine_status

        # 浮点注意：|0.85 - 0.75| = 0.0999... < 0.1（浮点精度）
        assert _determine_status(0.85, 0.76, 100) == "auto_applied"

    def test_exact_sample_threshold(self):
        """样本量恰好 20 → auto_applied（>= 20 不触发审核）"""
        from services.model_scorer import _determine_status

        assert _determine_status(0.85, 0.84, 20) == "auto_applied"


# ============ _format_period_dt 测试 ============


class TestFormatPeriodDt:
    """_format_period_dt 测试"""

    def test_datetime_objects(self):
        """datetime 对象直接返回"""
        from services.model_scorer import _format_period_dt

        start = datetime(2026, 3, 3, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, tzinfo=timezone.utc)
        row = {"period_start": start, "period_end": end}
        result_start, result_end = _format_period_dt(row)
        assert result_start == start
        assert result_end == end

    def test_string_timestamps(self):
        """字符串时间戳被解析为 datetime"""
        from services.model_scorer import _format_period_dt

        row = {
            "period_start": "2026-03-03T00:00:00+00:00",
            "period_end": "2026-03-10T00:00:00+00:00",
        }
        result_start, result_end = _format_period_dt(row)
        assert isinstance(result_start, datetime)
        assert result_start.year == 2026

    def test_none_values_fallback_to_now(self):
        """None 值回退到当前时间"""
        from services.model_scorer import _format_period_dt

        row = {"period_start": None, "period_end": None}
        result_start, result_end = _format_period_dt(row)
        assert isinstance(result_start, datetime)
        assert isinstance(result_end, datetime)
        # 应该接近当前时间（±5 秒）
        now = datetime.now(timezone.utc)
        assert abs((result_start - now).total_seconds()) < 5


# ============ 异常路径补充 ============


class TestWriteScoreToKnowledgeEdgeCases:
    """知识节点写入异常路径"""

    @pytest.mark.asyncio
    async def test_add_knowledge_returns_none(self):
        """add_knowledge 返回 None 时结果也为 None"""
        with patch("services.model_scorer.add_knowledge", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = None
            from services.model_scorer import _write_score_to_knowledge

            result = await _write_score_to_knowledge(_make_row(), 0.85, 0.7)
            assert result is None

    @pytest.mark.asyncio
    async def test_total_zero_no_divide_error(self):
        """total=0 时不触发除零错误"""
        with patch("services.model_scorer.add_knowledge", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = "node-ok"
            from services.model_scorer import _write_score_to_knowledge

            row = _make_row(total=0, success_count=0)
            result = await _write_score_to_knowledge(row, 0.5, 0.3)
            assert result == "node-ok"
            # metadata 中 success_rate 和 retry_rate 应为 0
            call_kwargs = mock_add.call_args[1]
            assert "0%" in call_kwargs["content"] or "0.0%" in call_kwargs["content"]


class TestWriteAuditLogEdgeCases:
    """审核日志异常路径"""

    @pytest.mark.asyncio
    async def test_old_score_none_score_change_zero(self, mock_pg_connection, mock_cursor):
        """old_score=None 时 score_change 应为 0"""
        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _write_audit_log

            await _write_audit_log(_make_row(), None, 0.88, "pending_review", None)

            call_args = mock_cursor.execute.call_args[0][1]
            assert call_args["score_change"] == 0.0
            assert call_args["old_score"] is None

    @pytest.mark.asyncio
    async def test_db_exception_swallowed(self, mock_pg_connection, mock_cursor):
        """DB 执行异常被静默吞掉"""
        mock_cursor.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _write_audit_log

            # 不应抛异常
            await _write_audit_log(_make_row(), 0.85, 0.88, "auto_applied", "node-1")


class TestQueryAggregatedMetricsEdgeCases:
    """聚合查询异常路径"""

    @pytest.mark.asyncio
    async def test_sql_exception_returns_empty(self, mock_pg_connection, mock_cursor):
        """SQL 执行异常返回空列表"""
        mock_cursor.execute = AsyncMock(side_effect=RuntimeError("SQL timeout"))

        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _query_aggregated_metrics

            rows = await _query_aggregated_metrics()
            assert rows == []


# ============ 主流程补充 ============


class TestAggregateModelScoresExtra:
    """主函数补充测试"""

    @pytest.mark.asyncio
    async def test_write_knowledge_none_still_writes_audit(self):
        """write_knowledge 返回 None 时 audit_log 仍被调用且 node_id=None"""
        row = _make_row(total=100, success_count=95)

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q, \
             patch("services.model_scorer._get_latest_score", new_callable=AsyncMock) as mock_old, \
             patch("services.model_scorer._write_score_to_knowledge", new_callable=AsyncMock) as mock_write, \
             patch("services.model_scorer._write_audit_log", new_callable=AsyncMock) as mock_log:
            mock_q.return_value = [row]
            mock_old.return_value = 0.90
            mock_write.return_value = None  # 写入知识库失败

            from services.model_scorer import aggregate_model_scores
            await aggregate_model_scores()

            mock_log.assert_called_once()
            assert mock_log.call_args[0][4] is None  # node_id=None

    @pytest.mark.asyncio
    async def test_write_knowledge_exception_row_skipped(self):
        """write_knowledge 异常时该模型被跳过，audit_log 不被调用"""
        row = _make_row()

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q, \
             patch("services.model_scorer._get_latest_score", new_callable=AsyncMock) as mock_old, \
             patch("services.model_scorer._write_score_to_knowledge", new_callable=AsyncMock) as mock_write, \
             patch("services.model_scorer._write_audit_log", new_callable=AsyncMock) as mock_log:
            mock_q.return_value = [row]
            mock_old.return_value = 0.90
            mock_write.side_effect = RuntimeError("KB write failed")

            from services.model_scorer import aggregate_model_scores
            await aggregate_model_scores()

            # 异常在 try-except 中被捕获，audit_log 不会被调用
            mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_model_mixed_statuses(self):
        """多模型：一个 auto_applied + 一个 pending_review"""
        row_good = _make_row(model_id="good-model", total=100, success_count=95)
        row_bad = _make_row(
            model_id="bad-model", total=100, success_count=10,
            p75_latency=28000.0, retry_count=80, hard_error_count=40,
        )

        write_calls = []
        log_calls = []

        async def track_write(*args, **kwargs):
            write_calls.append(args)
            return "node-good"

        async def track_log(*args, **kwargs):
            log_calls.append(args)

        async def get_score(model_id, task_type):
            return 0.90  # 与 bad-model 差距大

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q, \
             patch("services.model_scorer._get_latest_score", side_effect=get_score), \
             patch("services.model_scorer._write_score_to_knowledge", side_effect=track_write), \
             patch("services.model_scorer._write_audit_log", side_effect=track_log):
            mock_q.return_value = [row_good, row_bad]

            from services.model_scorer import aggregate_model_scores
            await aggregate_model_scores()

            # good-model: auto_applied → 写知识库 + 审核日志
            # bad-model: pending_review → 只写审核日志
            assert len(write_calls) == 1  # 只有 good-model 写知识库
            assert len(log_calls) == 2  # 两个都写审核日志

            # 验证 status
            statuses = [call[3] for call in log_calls]
            assert "auto_applied" in statuses
            assert "pending_review" in statuses


# ============ BackgroundTaskWorker 集成测试 ============


class TestRunModelScoring:
    """BackgroundTaskWorker._run_model_scoring 测试"""

    def _make_worker(self):
        """创建 mock worker 实例"""
        from services.background_task_worker import BackgroundTaskWorker

        mock_db = MagicMock()
        with patch("services.background_task_worker.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                callback_base_url=None,
                poll_interval_seconds=0,
            )
            worker = BackgroundTaskWorker(mock_db)
        return worker

    @pytest.mark.asyncio
    async def test_first_run_executes(self):
        """首次运行立即执行聚合"""
        worker = self._make_worker()
        assert worker._last_scoring_aggregation is None

        with patch("services.model_scorer.aggregate_model_scores", new_callable=AsyncMock) as mock_agg:
            await worker._run_model_scoring()
            mock_agg.assert_called_once()
            assert worker._last_scoring_aggregation is not None

    @pytest.mark.asyncio
    async def test_skips_within_one_hour(self):
        """1 小时内再次调用被跳过"""
        worker = self._make_worker()
        worker._last_scoring_aggregation = datetime.now(timezone.utc)

        with patch("services.model_scorer.aggregate_model_scores", new_callable=AsyncMock) as mock_agg:
            await worker._run_model_scoring()
            mock_agg.assert_not_called()

    @pytest.mark.asyncio
    async def test_runs_after_one_hour(self):
        """超过 1 小时后重新执行"""
        worker = self._make_worker()
        worker._last_scoring_aggregation = datetime.now(timezone.utc) - timedelta(hours=2)

        with patch("services.model_scorer.aggregate_model_scores", new_callable=AsyncMock) as mock_agg:
            await worker._run_model_scoring()
            mock_agg.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_caught_and_timestamp_updated(self):
        """异常被捕获，且 _last_scoring_aggregation 仍被更新（finally）"""
        worker = self._make_worker()

        with patch("services.model_scorer.aggregate_model_scores", new_callable=AsyncMock) as mock_agg:
            mock_agg.side_effect = RuntimeError("DB down")
            await worker._run_model_scoring()
            # 不应抛异常
            assert worker._last_scoring_aggregation is not None
