"""context_compressor 单元测试 — 工具结果压缩 + token预算 + system去重"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from services.handlers.context_compressor import (
    compress_tool_result,
    deduplicate_system_prompts,
    enforce_budget,
    estimate_tokens,
)


# ============================================================
# 层1: compress_tool_result
# ============================================================


class TestCompressToolResult:
    """工具结果即时压缩"""

    def test_short_result_unchanged(self):
        """短结果不压缩"""
        result = "库存: 128件"
        assert compress_tool_result("local_stock_query", result) == result

    def test_erp_result_keeps_summary_line(self):
        """ERP 结果保留汇总行"""
        result = (
            "📦 订单列表（共12笔）\n"
            "| 订单号 | 金额 | 状态 |\n"
            "| T001 | ¥299 | 已发货 |\n"
            "| T002 | ¥158 | 待发货 |\n"
            + "| T003 | ¥100 | 已完成 |\n" * 20
            + "汇总：总金额 ¥3,847，已发货8笔"
        )
        compressed = compress_tool_result("erp_trade_query", result)
        assert "汇总" in compressed
        assert "¥3,847" in compressed
        assert len(compressed) < len(result)

    def test_erp_result_without_summary_keeps_first_lines(self):
        """ERP 结果无汇总行 → 首行 + 前3行"""
        result = (
            "商品列表\n"
            + "商品A 库存100 价格39.9 供应商XX 仓库YY\n" * 50  # 足够长触发压缩
        )
        compressed = compress_tool_result("local_stock_query", result)
        assert "商品列表" in compressed
        assert "共" in compressed  # 行数提示
        assert len(compressed) < len(result)

    def test_code_result_keeps_tail(self):
        """代码结果保留最后10行"""
        lines = [f"line {i}: " + "x" * 20 for i in range(50)]  # 足够长
        result = "\n".join(lines)
        compressed = compress_tool_result("code_execute", result)
        assert "line 49" in compressed
        assert "line 40" in compressed
        assert "已省略" in compressed

    def test_code_error_preserved(self):
        """代码错误完整保留"""
        result = "❌ NameError: name 'foo' is not defined\n" + "x" * 1000
        compressed = compress_tool_result("code_execute", result)
        assert "❌" in compressed
        assert "NameError" in compressed

    def test_search_result_keeps_first_3(self):
        """搜索结果保留前3条"""
        result = "\n".join([f"- 结果{i}: 内容内容内容" * 20 for i in range(10)])
        compressed = compress_tool_result("web_search", result)
        assert "结果0" in compressed
        assert "结果2" in compressed
        assert "已省略" in compressed

    def test_erp_agent_not_compressed(self):
        """erp_agent 返回不压缩（结果已是 LLM 合成的结论）"""
        result = "x" * 2000
        assert compress_tool_result("erp_agent", result) == result

    def test_generate_image_not_compressed(self):
        """媒体生成不压缩"""
        result = "图片已生成：https://example.com/img.png"
        assert compress_tool_result("generate_image", result) == result

    def test_file_result_truncated(self):
        """文件操作结果截断"""
        result = "文件内容\n" + "x" * 2000
        compressed = compress_tool_result("file_read", result)
        assert len(compressed) <= 600
        assert "已省略" in compressed

    def test_empty_result_unchanged(self):
        assert compress_tool_result("any_tool", "") == ""

    def test_none_result_unchanged(self):
        assert compress_tool_result("any_tool", None) is None


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
