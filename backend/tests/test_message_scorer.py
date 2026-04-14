"""message_scorer 单元测试 — A 层规则打分 + B 层 Embedding + 融合逻辑

覆盖：
- _rule_score: 废话/高价值/边界/角色权重
- _extract_text: 纯文本/多模态/空值
- score_messages_sync: 批量同步打分
- score_messages: 异步融合 + 超时退化
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from services.handlers.message_scorer import (
    _compute_relevance_scores,
    _extract_text,
    _rule_score,
    score_messages,
    score_messages_sync,
)


# ============================================================
# _extract_text
# ============================================================


class TestExtractText:
    def test_plain_string(self):
        assert _extract_text({"content": "hello"}) == "hello"

    def test_multimodal_list(self):
        msg = {"content": [
            {"type": "text", "text": "图片描述"},
            {"type": "image_url", "url": "https://img.png"},
        ]}
        assert "图片描述" in _extract_text(msg)

    def test_empty_content(self):
        assert _extract_text({"content": ""}) == ""
        assert _extract_text({}) == ""

    def test_none_content(self):
        assert _extract_text({"content": None}) == "None"


# ============================================================
# _rule_score: A 层规则打分
# ============================================================


class TestRuleScore:
    """A 层规则打分"""

    def test_low_value_exact_match(self):
        """废话消息得低分"""
        for text in ["好的", "嗯", "知道了", "ok", "OK", "👍"]:
            msg = {"role": "user", "content": text}
            assert _rule_score(msg) == 0.1, f"'{text}' should be 0.1"

    def test_short_message_low_score(self):
        """极短消息（<8字符）得低分"""
        assert _rule_score({"role": "user", "content": "嗯嗯嗯"}) == 0.2

    def test_system_role_always_high(self):
        """system 消息始终 1.0"""
        assert _rule_score({"role": "system", "content": "好的"}) == 1.0

    def test_tool_role_always_high(self):
        """tool 消息始终 1.0（走工具桶）"""
        assert _rule_score({"role": "tool", "content": "结果"}) == 1.0

    def test_user_role_bonus(self):
        """用户消息比 assistant 基础分高 0.1"""
        user = _rule_score({"role": "user", "content": "这是一条普通消息内容"})
        assistant = _rule_score({"role": "assistant", "content": "这是一条普通消息内容"})
        assert user - assistant == pytest.approx(0.1)

    def test_order_number_high_score(self):
        """含长数字编码（≥10位）加分"""
        msg = {"role": "user", "content": "订单号 1234567890123456"}
        score = _rule_score(msg)
        assert score >= 0.7  # 基础0.5 + 数字0.25

    def test_amount_pattern(self):
        """含金额加分"""
        msg = {"role": "user", "content": "退款金额 ¥199.00"}
        score = _rule_score(msg)
        assert score >= 0.6

    def test_date_pattern(self):
        """含日期加分"""
        msg = {"role": "user", "content": "查一下 2026-04-14 的数据"}
        score = _rule_score(msg)
        assert score >= 0.6

    def test_erp_entity_keywords(self):
        """含 ERP 实体关键词加分"""
        msg = {"role": "user", "content": "查一下这个商品的库存情况"}
        score = _rule_score(msg)
        assert score >= 0.55

    def test_long_message_bonus(self):
        """长消息有微小加分"""
        short = _rule_score({"role": "assistant", "content": "x" * 30})
        long = _rule_score({"role": "assistant", "content": "x" * 250})
        assert long > short

    def test_max_score_capped_at_1(self):
        """分数不超过 1.0"""
        # 包含所有高价值模式
        msg = {"role": "user", "content": "订单号1234567890 SKU123 ¥199.00 2026-04-14 库存 https://example.com " + "x" * 300}
        assert _rule_score(msg) <= 1.0

    def test_multimodal_content(self):
        """多模态消息能正确提取文本打分"""
        msg = {
            "role": "user",
            "content": [{"type": "text", "text": "订单号1234567890123456"}],
        }
        assert _rule_score(msg) >= 0.7


# ============================================================
# score_messages_sync
# ============================================================


class TestScoreMessagesSync:
    def test_returns_list_matching_length(self):
        msgs = [
            {"role": "user", "content": "好的"},
            {"role": "user", "content": "查一下订单1234567890"},
        ]
        scores = score_messages_sync(msgs)
        assert len(scores) == 2

    def test_low_value_first_high_value_second(self):
        msgs = [
            {"role": "user", "content": "嗯"},
            {"role": "user", "content": "帮我查订单号1234567890的退款状态"},
        ]
        scores = score_messages_sync(msgs)
        assert scores[0] < scores[1]

    def test_empty_list(self):
        assert score_messages_sync([]) == []


# ============================================================
# score_messages (async, A+B 融合)
# ============================================================


class TestScoreMessagesAsync:
    @pytest.mark.asyncio
    async def test_few_messages_skip_embedding(self):
        """≤5 条消息跳过 Embedding，只用规则"""
        msgs = [
            {"role": "user", "content": "好的"},
            {"role": "user", "content": "查库存"},
        ]
        scores = await score_messages(msgs, current_query="查库存")
        assert len(scores) == 2
        # 不调 embedding，结果等于 rule_score
        sync_scores = score_messages_sync(msgs)
        assert scores == sync_scores

    @pytest.mark.asyncio
    async def test_no_query_skip_embedding(self):
        """无 query 跳过 Embedding"""
        msgs = [{"role": "user", "content": f"消息{i}"} for i in range(10)]
        scores = await score_messages(msgs, current_query="")
        sync_scores = score_messages_sync(msgs)
        assert scores == sync_scores

    @pytest.mark.asyncio
    async def test_rule_veto_overrides_embedding(self):
        """规则层一票否决：rule_score < 0.2 时忽略 Embedding"""
        msgs = [
            {"role": "user", "content": "好的"},  # rule=0.1（废话）
            *[{"role": "user", "content": f"正常消息内容{i}，足够长"} for i in range(6)],
        ]
        with patch(
            "services.handlers.message_scorer._compute_relevance_scores",
            new_callable=AsyncMock,
            return_value=[0.9] * len(msgs),  # Embedding 给高分
        ):
            scores = await score_messages(msgs, current_query="测试")
        # 第一条即使 Embedding 给 0.9，也应被规则否决为 0.1
        assert scores[0] == 0.1

    @pytest.mark.asyncio
    async def test_timeout_fallback_to_rules(self):
        """Embedding 超时退化为纯规则"""
        msgs = [{"role": "user", "content": f"正常消息{i}"} for i in range(10)]

        async def slow_embedding(*args, **kwargs):
            await asyncio.sleep(10)  # 远超 3s 超时
            return [0.5] * len(msgs)

        with patch(
            "services.handlers.message_scorer._compute_relevance_scores",
            side_effect=slow_embedding,
        ):
            scores = await score_messages(msgs, current_query="查询")

        sync_scores = score_messages_sync(msgs)
        assert scores == sync_scores

    @pytest.mark.asyncio
    async def test_embedding_api_error_returns_default(self):
        """Embedding API 报错时 _compute_relevance_scores 内部捕获返回默认分"""
        from services.handlers.message_scorer import _compute_relevance_scores

        msgs = [{"role": "user", "content": f"正常消息{i}，足够长"} for i in range(8)]

        # mock compute_embedding 报错（在 _compute_relevance_scores 内部被 try/except 捕获）
        with patch(
            "services.knowledge_config.compute_embedding",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API error"),
        ):
            scores = await _compute_relevance_scores(msgs, "查询")

        # 内部 try/except 捕获后返回全 0.5
        assert scores == [0.5] * len(msgs)

    @pytest.mark.asyncio
    async def test_fusion_formula(self):
        """融合公式验证：0.4*rule + 0.6*relevance"""
        msgs = [{"role": "user", "content": f"正常消息内容{i}，足够长度"} for i in range(8)]
        rule_scores = score_messages_sync(msgs)

        mock_relevance = [0.8] * len(msgs)
        with patch(
            "services.handlers.message_scorer._compute_relevance_scores",
            new_callable=AsyncMock,
            return_value=mock_relevance,
        ):
            scores = await score_messages(msgs, current_query="测试查询")

        for i, (rule, final) in enumerate(zip(rule_scores, scores)):
            if rule < 0.2:
                assert final == rule  # 一票否决
            else:
                expected = min(1.0, 0.4 * rule + 0.6 * 0.8)
                assert final == pytest.approx(expected), f"msg[{i}] fusion mismatch"
