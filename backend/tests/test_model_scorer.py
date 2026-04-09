"""
模型动态评分单元测试

覆盖：评分计算、EMA 平滑、Confidence 分级、审核规则、
聚合查询、知识节点写入、审核日志写入、主流程。
"""

from datetime import datetime, timezone
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
    """Mock psycopg 异步游标"""
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.description = []
    return cursor


@pytest.fixture
def mock_conn(mock_cursor):
    """Mock psycopg 异步连接"""
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_cursor),
        __aexit__=AsyncMock(return_value=False),
    ))
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_pg_connection(mock_conn):
    """Mock get_pg_connection 返回 context manager"""
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
    """构建模拟聚合行"""
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


class TestComputeRawScore:
    """评分计算测试"""

    def test_perfect_model(self):
        """全优模型得高分"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(
            total=100, success_count=100, p75_latency=500.0,
            retry_count=0, hard_error_count=0,
        )
        score = _compute_raw_score(row)
        assert score > 0.9
        assert score <= 1.0

    def test_poor_model(self):
        """差模型得低分"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(
            total=100, success_count=30, p75_latency=25000.0,
            retry_count=50, hard_error_count=20,
        )
        score = _compute_raw_score(row)
        assert score < 0.5

    def test_zero_total(self):
        """零样本返回 0"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(total=0, success_count=0)
        assert _compute_raw_score(row) == 0.0

    def test_null_p75_latency(self):
        """P75 为 None 时延迟得满分"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(p75_latency=None)
        score = _compute_raw_score(row)
        # p75=None 被视为 0 → latency_score=1.0
        assert score > 0.8

    def test_score_bounded(self):
        """评分在 0-1 范围内"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(
            total=10, success_count=10, p75_latency=0,
            retry_count=0, hard_error_count=0,
        )
        score = _compute_raw_score(row)
        assert 0.0 <= score <= 1.0

    def test_typical_model(self):
        """典型模型得中等偏高分"""
        from services.model_scorer import _compute_raw_score

        row = _make_row(
            total=100, success_count=90, p75_latency=5000.0,
            retry_count=10, hard_error_count=3,
        )
        score = _compute_raw_score(row)
        assert 0.6 < score < 0.95


class TestApplyEma:
    """EMA 平滑测试"""

    def test_first_run_no_old(self):
        """首次无历史评分，直接用 raw"""
        from services.model_scorer import _apply_ema

        assert _apply_ema(0.85, None) == 0.85

    def test_ema_smoothing(self):
        """EMA 平滑：新数据权重 20%"""
        from services.model_scorer import _apply_ema, EMA_ALPHA

        old = 0.9
        raw = 0.7
        expected = round(EMA_ALPHA * raw + (1 - EMA_ALPHA) * old, 4)
        assert _apply_ema(raw, old) == expected

    def test_ema_stable_score(self):
        """分数不变时 EMA 保持不变"""
        from services.model_scorer import _apply_ema

        result = _apply_ema(0.85, 0.85)
        assert result == 0.85


class TestGetConfidence:
    """Confidence 分级测试"""

    def test_confidence_levels(self):
        from services.model_scorer import _get_confidence

        assert _get_confidence(5) == 0.3   # <10 → low
        assert _get_confidence(9) == 0.3
        assert _get_confidence(10) == 0.7  # 10-49 → mid
        assert _get_confidence(49) == 0.7
        assert _get_confidence(50) == 0.9  # ≥50 → high
        assert _get_confidence(1000) == 0.9


class TestDetermineStatus:
    """审核规则测试"""

    def test_auto_applied(self):
        """分数变化小 + 样本足够 → auto_applied"""
        from services.model_scorer import _determine_status

        assert _determine_status(0.85, 0.84, 100) == "auto_applied"

    def test_pending_large_change(self):
        """分数变化 ≥0.1 → pending_review"""
        from services.model_scorer import _determine_status

        assert _determine_status(0.85, 0.70, 100) == "pending_review"

    def test_pending_low_samples(self):
        """样本量 <20 → pending_review"""
        from services.model_scorer import _determine_status

        assert _determine_status(0.85, 0.84, 15) == "pending_review"

    def test_first_score_small_samples(self):
        """首次评分 + 小样本 → pending_review"""
        from services.model_scorer import _determine_status

        assert _determine_status(0.85, None, 10) == "pending_review"

    def test_first_score_enough_samples(self):
        """首次评分 + 足够样本 → auto_applied（变化量=0）"""
        from services.model_scorer import _determine_status

        assert _determine_status(0.85, None, 50) == "auto_applied"


class TestFormatPeriod:
    """格式化工具测试"""

    def test_format_period(self):
        from services.model_scorer import _format_period

        # datetime 对象
        row_dt = {
            "period_start": datetime(2026, 3, 3, tzinfo=timezone.utc),
            "period_end": datetime(2026, 3, 10, tzinfo=timezone.utc),
        }
        assert _format_period(row_dt) == ("2026-03-03", "2026-03-10")

        # 字符串
        row_str = {
            "period_start": "2026-03-03T00:00:00+00:00",
            "period_end": "2026-03-10T00:00:00+00:00",
        }
        assert _format_period(row_str) == ("2026-03-03", "2026-03-10")


class TestGetLatestScore:
    """历史评分查询测试"""

    @pytest.mark.asyncio
    async def test_returns_score(self, mock_pg_connection, mock_cursor):
        """有历史评分时返回分数"""
        mock_cursor.fetchone = AsyncMock(return_value=(0.88,))

        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _get_latest_score

            result = await _get_latest_score("gemini-3-pro", "chat")
            assert result == 0.88

    @pytest.mark.asyncio
    async def test_returns_none_no_history(self, mock_pg_connection, mock_cursor):
        """无历史评分时返回 None"""
        mock_cursor.fetchone = AsyncMock(return_value=None)

        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _get_latest_score

            result = await _get_latest_score("new-model", "chat")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_db_unavailable(self):
        """DB 不可用时返回 None"""
        with patch("services.model_scorer.get_pg_connection", return_value=None):
            from services.model_scorer import _get_latest_score

            result = await _get_latest_score("gemini-3-pro", "chat")
            assert result is None


class TestWriteScoreToKnowledge:
    """知识节点写入测试"""

    @pytest.mark.asyncio
    async def test_calls_add_knowledge(self):
        """调用 add_knowledge 写入评分节点"""
        with patch("services.model_scorer.add_knowledge", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = "node-123"
            from services.model_scorer import _write_score_to_knowledge

            row = _make_row()
            result = await _write_score_to_knowledge(row, 0.92, 0.9)

            assert result == "node-123"
            mock_add.assert_called_once()
            call_kwargs = mock_add.call_args[1]
            assert call_kwargs["category"] == "model"
            assert call_kwargs["subcategory"] == "chat"
            assert call_kwargs["node_type"] == "performance"
            assert call_kwargs["source"] == "aggregated"
            assert call_kwargs["confidence"] == 0.9
            assert "gemini-3-pro" in call_kwargs["title"]
            assert "0.92" in call_kwargs["content"]


class TestWriteAuditLog:
    """审核日志写入测试"""

    @pytest.mark.asyncio
    async def test_writes_log(self, mock_pg_connection, mock_conn, mock_cursor):
        """成功写入审核日志"""
        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _write_audit_log

            row = _make_row()
            await _write_audit_log(row, 0.85, 0.88, "auto_applied", "node-123")

            mock_cursor.execute.assert_called_once()
            mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_db_unavailable(self):
        """DB 不可用时跳过"""
        with patch("services.model_scorer.get_pg_connection", return_value=None):
            from services.model_scorer import _write_audit_log

            row = _make_row()
            # 不应抛异常
            await _write_audit_log(row, None, 0.88, "pending_review", None)


class TestQueryAggregatedMetrics:
    """聚合查询测试"""

    @pytest.mark.asyncio
    async def test_returns_rows(self, mock_pg_connection, mock_cursor):
        """返回聚合结果"""
        now = datetime.now(timezone.utc)
        mock_cursor.fetchall = AsyncMock(return_value=[
            ("gemini-3-pro", "chat", 100, 95, 2000.0, 5, 1, 2, now, now),
        ])
        # MagicMock(name=...) 设置的是 mock 内部名，不是 .name 属性
        col_names = [
            "model_id", "task_type", "total", "success_count",
            "p75_latency", "retry_count", "timeout_count",
            "hard_error_count", "period_start", "period_end",
        ]
        desc = []
        for n in col_names:
            col = MagicMock()
            col.name = n
            desc.append(col)
        mock_cursor.description = desc

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _query_aggregated_metrics

            rows = await _query_aggregated_metrics()
            assert len(rows) == 1
            assert rows[0]["model_id"] == "gemini-3-pro"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data(self, mock_pg_connection, mock_cursor):
        """无数据时返回空列表"""
        mock_cursor.fetchall = AsyncMock(return_value=[])

        with patch("services.model_scorer.get_pg_connection", return_value=mock_pg_connection):
            from services.model_scorer import _query_aggregated_metrics

            rows = await _query_aggregated_metrics()
            assert rows == []

    @pytest.mark.asyncio
    async def test_returns_empty_db_unavailable(self):
        """DB 不可用时返回空"""
        with patch("services.model_scorer.get_pg_connection", return_value=None):
            from services.model_scorer import _query_aggregated_metrics

            rows = await _query_aggregated_metrics()
            assert rows == []


class TestAggregateModelScores:
    """主函数集成测试"""

    @pytest.mark.asyncio
    async def test_skips_when_kb_unavailable(self):
        """知识库不可用时跳过"""
        with patch("services.model_scorer.is_kb_available", return_value=False):
            from services.model_scorer import aggregate_model_scores

            # 不应抛异常
            await aggregate_model_scores()

    @pytest.mark.asyncio
    async def test_skips_when_no_metrics(self):
        """无指标数据时跳过"""
        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = []
            from services.model_scorer import aggregate_model_scores

            await aggregate_model_scores()
            mock_q.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_flow_auto_applied(self):
        """完整流程：auto_applied → 写入知识库 + 审核日志"""
        row = _make_row(total=100, success_count=95)

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q, \
             patch("services.model_scorer._get_latest_score", new_callable=AsyncMock) as mock_old, \
             patch("services.model_scorer._write_score_to_knowledge", new_callable=AsyncMock) as mock_write, \
             patch("services.model_scorer._write_audit_log", new_callable=AsyncMock) as mock_log:
            mock_q.return_value = [row]
            mock_old.return_value = 0.90  # 接近新分数，不触发审核
            mock_write.return_value = "node-abc"

            from services.model_scorer import aggregate_model_scores

            await aggregate_model_scores()

            mock_write.assert_called_once()
            mock_log.assert_called_once()
            log_args = mock_log.call_args
            assert log_args[0][3] == "auto_applied"  # status
            assert log_args[0][4] == "node-abc"  # knowledge_node_id

    @pytest.mark.asyncio
    async def test_full_flow_pending_review(self):
        """完整流程：pending_review → 只写审核日志，不写知识库"""
        # 极差模型：成功率 10%、高延迟、大量重试和硬错误
        row = _make_row(
            total=100, success_count=10, p75_latency=28000.0,
            retry_count=80, hard_error_count=40,
        )

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q, \
             patch("services.model_scorer._get_latest_score", new_callable=AsyncMock) as mock_old, \
             patch("services.model_scorer._write_score_to_knowledge", new_callable=AsyncMock) as mock_write, \
             patch("services.model_scorer._write_audit_log", new_callable=AsyncMock) as mock_log:
            mock_q.return_value = [row]
            mock_old.return_value = 0.95  # 与极差 raw_score 差距大 → EMA 变化 ≥0.1

            from services.model_scorer import aggregate_model_scores

            await aggregate_model_scores()

            mock_write.assert_not_called()
            mock_log.assert_called_once()
            log_args = mock_log.call_args
            assert log_args[0][3] == "pending_review"
            assert log_args[0][4] is None  # 无 knowledge_node_id

    @pytest.mark.asyncio
    async def test_single_model_error_does_not_abort(self):
        """单个模型处理失败不影响其他模型"""
        row_ok = _make_row(model_id="model-ok")
        row_bad = _make_row(model_id="model-bad")

        call_count = {"write": 0, "log": 0}

        async def mock_write(*args, **kwargs):
            call_count["write"] += 1
            return "node-ok"

        async def mock_log(*args, **kwargs):
            call_count["log"] += 1

        async def bad_latest(model_id, task_type, org_id=None):
            if model_id == "model-bad":
                raise RuntimeError("DB error")
            return 0.90

        with patch("services.model_scorer.is_kb_available", return_value=True), \
             patch("services.model_scorer._query_aggregated_metrics", new_callable=AsyncMock) as mock_q, \
             patch("services.model_scorer._get_latest_score", side_effect=bad_latest), \
             patch("services.model_scorer._write_score_to_knowledge", side_effect=mock_write), \
             patch("services.model_scorer._write_audit_log", side_effect=mock_log):
            mock_q.return_value = [row_bad, row_ok]

            from services.model_scorer import aggregate_model_scores

            await aggregate_model_scores()

            # model-bad 失败，model-ok 应该正常处理
            assert call_count["log"] == 1
