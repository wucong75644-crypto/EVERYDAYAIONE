"""context_compressor 单元测试 — token预算 + system去重

注意：层1 工具结果截断已迁移到 tool_result_envelope.wrap()，
对应测试在 test_tool_result_envelope.py 中。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from services.handlers.context_compressor import (
    deduplicate_system_prompts,
    enforce_budget,
    estimate_tokens,
)


# ============================================================
# 层4: Token 估算
# ============================================================


class TestEstimateTokens:
    """token 估算"""

    def test_simple_text_messages(self):
        msgs = [{"content": "x" * 250}]  # 250 chars / 2.5 = 100 tokens
        assert estimate_tokens(msgs) == 100

    def test_mixed_content(self):
        msgs = [
            {"content": "hello"},
            {"content": [{"text": "world", "type": "text"}]},
        ]
        tokens = estimate_tokens(msgs)
        assert tokens > 0

    def test_tool_calls_counted(self):
        msgs = [{"content": "", "tool_calls": [
            {"function": {"arguments": '{"query": "test" }' * 10}}
        ]}]
        tokens = estimate_tokens(msgs)
        assert tokens > 0

    def test_empty_messages(self):
        assert estimate_tokens([]) == 0


# ============================================================
# 层4: System Prompt 去重
# ============================================================


class TestDeduplicateSystemPrompts:
    """工具循环 system prompt 去重"""

    def test_keeps_latest_only(self):
        messages = [
            {"role": "system", "content": "已识别编码: A=001"},
            {"role": "user", "content": "查库存"},
            {"role": "system", "content": "已识别编码: A=001, B=002 | 已用工具: stock"},
            {"role": "user", "content": "继续"},
        ]
        deduplicate_system_prompts(messages)
        ctx_msgs = [m for m in messages if "已识别编码" in m.get("content", "")]
        assert len(ctx_msgs) == 1
        assert "B=002" in ctx_msgs[0]["content"]

    def test_no_ctx_prompts_unchanged(self):
        messages = [
            {"role": "system", "content": "你是AI助手"},
            {"role": "user", "content": "你好"},
        ]
        original_len = len(messages)
        deduplicate_system_prompts(messages)
        assert len(messages) == original_len

    def test_single_ctx_prompt_kept(self):
        messages = [
            {"role": "system", "content": "已用工具: erp_agent"},
        ]
        deduplicate_system_prompts(messages)
        assert len(messages) == 1


# ============================================================
# 层4: Token 预算兜底
# ============================================================


class TestEnforceBudget:
    """token 预算兜底"""

    def test_under_budget_unchanged(self):
        messages = [{"role": "user", "content": "hi"}]
        enforce_budget(messages, max_tokens=1000)
        assert messages[0]["content"] == "hi"

    def test_over_budget_archives_old(self):
        messages = [
            {"role": "user", "content": "x" * 25000},
            {"role": "assistant", "content": "y" * 25000},
            {"role": "user", "content": "z" * 25000},
            {"role": "assistant", "content": "w" * 25000},
            # 后面6条是 protected_tail
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "current"},
            {"role": "assistant", "content": "response"},
        ]
        enforce_budget(messages, max_tokens=5000)
        archived = [m for m in messages if m["content"] == "[已归档]"]
        assert len(archived) > 0

    def test_protects_tail_messages(self):
        messages = [
            {"role": "user", "content": "x" * 10000},
            {"role": "user", "content": "current question"},
        ]
        enforce_budget(messages, max_tokens=100)
        # 最后几条不应被归档（protected_tail）
        assert messages[-1]["content"] == "current question"
