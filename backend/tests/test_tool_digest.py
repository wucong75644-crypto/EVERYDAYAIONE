"""tool_digest 单元测试 — build_tool_digest + format_tool_digest + _extract_archive_meta

覆盖场景：
1. 正常提取（含 staging 路径）
2. 无工具调用 → None
3. 已归档消息中仍可提取 staging 路径
4. 去重逻辑
5. 大小控制裁剪
6. format_tool_digest 格式化
7. _extract_archive_meta 容错降级
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
import pytest
from services.handlers.tool_digest import (
    build_tool_digest,
    format_tool_digest,
    _extract_hint,
    _extract_staging_path,
    _is_error,
    _deduplicate,
)
from services.handlers.context_compressor import (
    _extract_archive_meta,
    compact_stale_tool_results,
)


# ============================================================
# 测试数据工厂
# ============================================================

def _make_assistant_msg(tool_calls):
    """构造 assistant 消息（含 tool_calls）"""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))},
            }
            for tc in tool_calls
        ],
    }


def _make_tool_result(tc_id, content):
    """构造 tool result 消息"""
    return {
        "role": "tool",
        "tool_call_id": tc_id,
        "content": content,
    }


def _make_staged_result(tool_name="erp_agent", hash_str="a1b2c3d4", size=45632):
    """构造已 staging 的大结果"""
    filename = f"tool_result_{tool_name}_{hash_str}.txt"
    return (
        f'<persisted-output>\n'
        f'Output too large ({size} chars). '
        f'Full output saved to: STAGING_DIR + "/{filename}"\n\n'
        f'Preview (first 2000 chars):\n'
        f'商品编码 | 可售库存 | 仓库\n'
        f'A001    | 450     | 上海\n'
        f'B002    | 320     | 北京\n'
        f'...共 500 条记录\n'
        f'</persisted-output>'
    )


# ============================================================
# build_tool_digest 测试
# ============================================================


class TestBuildToolDigest:
    """build_tool_digest 核心逻辑"""

    def test_normal_extraction(self):
        """正常场景：erp_agent + code_execute"""
        messages = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "查订单"},
            _make_assistant_msg([
                {"id": "tc1", "name": "erp_agent", "args": {"query": "查询最近七天付款订单"}},
            ]),
            _make_tool_result("tc1", "订单汇总：有效 48488 笔，金额 213220.15"),
            _make_assistant_msg([
                {"id": "tc2", "name": "code_execute", "args": {"code": "df.describe()"}},
            ]),
            _make_tool_result("tc2", "统计结果：mean=4.39"),
        ]

        digest = build_tool_digest(messages, "conv-123")
        assert digest is not None
        assert len(digest["tools"]) == 2
        assert digest["tools"][0]["name"] == "erp_agent"
        assert "最近七天" in digest["tools"][0]["hint"]
        assert digest["tools"][0]["ok"] is True
        assert digest["staging_dir"] == "staging/conv-123"

    def test_with_staging_path(self):
        """包含 staging 文件路径的大结果"""
        messages = [
            _make_assistant_msg([
                {"id": "tc1", "name": "erp_agent", "args": {"query": "查库存"}},
            ]),
            _make_tool_result("tc1", _make_staged_result()),
        ]

        digest = build_tool_digest(messages, "conv-456")
        assert digest["tools"][0]["staged"] == "tool_result_erp_agent_a1b2c3d4.txt"

    def test_no_tool_calls(self):
        """无工具调用 → None"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        assert build_tool_digest(messages, "conv-789") is None

    def test_empty_messages(self):
        """空消息列表 → None"""
        assert build_tool_digest([], "conv-000") is None

    def test_error_result(self):
        """错误结果标记"""
        messages = [
            _make_assistant_msg([
                {"id": "tc1", "name": "erp_agent", "args": {"query": "查订单"}},
            ]),
            _make_tool_result("tc1", "❌ 执行错误: 连接超时"),
        ]

        digest = build_tool_digest(messages, "conv-err")
        assert digest["tools"][0]["ok"] is False

    def test_archived_message_staging_path(self):
        """归档消息中仍可提取 staging 路径"""
        archived_content = (
            '[已归档] erp_agent 查询结果（原始 45632 字符）\n'
            '数据文件: STAGING_DIR + "/tool_result_erp_agent_a1b2c3d4.txt"\n'
            '字段: 商品编码, 可售库存 | 共 500 条记录'
        )
        messages = [
            _make_assistant_msg([
                {"id": "tc1", "name": "erp_agent", "args": {"query": "查库存"}},
            ]),
            _make_tool_result("tc1", archived_content),
        ]

        digest = build_tool_digest(messages, "conv-arc")
        assert digest["tools"][0]["staged"] == "tool_result_erp_agent_a1b2c3d4.txt"

    def test_deduplication(self):
        """同名工具 + 相同 hint 去重"""
        messages = [
            _make_assistant_msg([
                {"id": "tc1", "name": "erp_agent", "args": {"query": "查订单"}},
                {"id": "tc2", "name": "erp_agent", "args": {"query": "查订单"}},
            ]),
            _make_tool_result("tc1", "结果1"),
            _make_tool_result("tc2", "结果2"),
        ]

        digest = build_tool_digest(messages, "conv-dup")
        assert len(digest["tools"]) == 1

    def test_parallel_tool_calls(self):
        """并行工具调用（同一 assistant 消息多个 tool_calls）"""
        messages = [
            _make_assistant_msg([
                {"id": "tc1", "name": "erp_agent", "args": {"query": "查当前期订单"}},
                {"id": "tc2", "name": "erp_agent", "args": {"query": "查基线期订单"}},
            ]),
            _make_tool_result("tc1", "当前期：48488笔"),
            _make_tool_result("tc2", "基线期：48003笔"),
        ]

        digest = build_tool_digest(messages, "conv-par")
        # 不同 hint → 不去重
        assert len(digest["tools"]) == 2


# ============================================================
# format_tool_digest 测试
# ============================================================


class TestFormatToolDigest:
    """格式化输出"""

    def test_normal_format(self):
        digest = {
            "tools": [
                {"name": "erp_agent", "hint": "查订单", "ok": True, "staged": "tool_result_erp_agent_a1b2.txt"},
                {"name": "code_execute", "hint": "df.groupby", "ok": True},
            ],
            "staging_dir": "staging/conv-123",
        }
        result = format_tool_digest(digest)
        assert "[上轮工具执行记录]" in result
        assert "✓ erp_agent: 查订单" in result
        assert "tool_result_erp_agent_a1b2.txt" in result
        assert "15 分钟内有效" in result

    def test_error_tool_format(self):
        digest = {
            "tools": [{"name": "erp_agent", "hint": "查订单", "ok": False}],
            "staging_dir": "staging/conv-err",
        }
        result = format_tool_digest(digest)
        assert "✗ erp_agent" in result

    def test_empty_digest(self):
        assert format_tool_digest({}) == ""
        assert format_tool_digest(None) == ""


# ============================================================
# _extract_archive_meta 测试
# ============================================================


class TestExtractArchiveMeta:
    """归档元数据提取（容错）"""

    def test_staged_result(self):
        """正常 persisted-output 格式"""
        content = _make_staged_result("erp_agent", "abc12345", 45632)
        result = _extract_archive_meta(content, "erp_agent")
        assert "[已归档]" in result
        assert "erp_agent" in result
        assert "45632" in result
        assert "tool_result_erp_agent_abc12345.txt" in result
        assert "商品编码" in result
        assert "500" in result

    def test_non_staged_large_result(self):
        """非 staging 的大结果 → 前 200 字符"""
        content = "x" * 3000
        result = _extract_archive_meta(content, "web_search")
        assert "[已归档]" in result
        assert "web_search" in result
        assert "..." in result

    def test_no_tool_name_fallback(self):
        """无工具名时从文件名提取"""
        content = _make_staged_result("local_stock_query", "def67890")
        result = _extract_archive_meta(content, "")
        assert "local_stock_query" in result

    def test_extract_fields_with_pipe_separator(self):
        """提取 | 分隔的列名"""
        content = _make_staged_result()
        result = _extract_archive_meta(content)
        assert "商品编码" in result

    def test_malformed_content_graceful(self):
        """格式异常时不 crash，降级为前 200 字符"""
        content = "这是一段没有任何标记的超长文本" * 200
        result = _extract_archive_meta(content, "unknown_tool")
        assert "[已归档]" in result
        assert "unknown_tool" in result


# ============================================================
# compact_stale_tool_results 新行为测试
# ============================================================


class TestCompactStaleSmartArchive:
    """归档策略：短结果不压缩，大结果保留元数据"""

    def test_short_results_not_compressed(self):
        """≤2000 字符的短结果不被压缩"""
        messages = [
            _make_assistant_msg([{"id": "tc1", "name": "erp_agent", "args": {}}]),
            _make_tool_result("tc1", "短结果：488字符" * 20),  # ~300 chars
            _make_assistant_msg([{"id": "tc2", "name": "erp_agent", "args": {}}]),
            _make_tool_result("tc2", "短结果：488字符" * 20),
            _make_assistant_msg([{"id": "tc3", "name": "erp_agent", "args": {}}]),
            _make_tool_result("tc3", "最新结果"),
        ]

        compacted = compact_stale_tool_results(messages, keep_turns=1)
        # 前两轮都是短结果，不应被压缩
        assert compacted == 0
        assert "短结果" in messages[1]["content"]
        assert "短结果" in messages[3]["content"]

    def test_large_result_compressed_with_meta(self):
        """>2000 字符的大结果压缩但保留元数据"""
        # 生成 >2000 字符的 staged content（真实场景 preview 约 2000 字符）
        staged_content = _make_staged_result("erp_agent", "abc12345", 45632)
        # 填充 preview 到 >2000 字符
        padding = "\nC003    | 100     | 杭州" * 100
        staged_content = staged_content.replace("...共 500 条记录", padding + "\n...共 500 条记录")
        assert len(staged_content) > 2000, f"staged_content only {len(staged_content)} chars"

        messages = [
            _make_assistant_msg([{"id": "tc1", "name": "erp_agent", "args": {}}]),
            _make_tool_result("tc1", staged_content),
            _make_assistant_msg([{"id": "tc2", "name": "erp_agent", "args": {}}]),
            _make_tool_result("tc2", "最新短结果"),
        ]

        compacted = compact_stale_tool_results(messages, keep_turns=1)
        assert compacted == 1
        archived = messages[1]["content"]
        assert "[已归档]" in archived
        assert "tool_result_erp_agent_abc12345.txt" in archived
        assert "erp_agent" in archived


# ============================================================
# 辅助函数测试
# ============================================================


class TestHelpers:

    def test_extract_hint_query(self):
        assert "查订单" in _extract_hint("erp_agent", '{"query": "查订单数据"}')

    def test_extract_hint_code(self):
        assert "df.head" in _extract_hint("code_execute", '{"code": "df.head(10)"}')

    def test_extract_hint_invalid_json(self):
        result = _extract_hint("tool", "not json")
        assert result == "not json"[:50]

    def test_extract_staging_path_persisted(self):
        content = 'STAGING_DIR + "/tool_result_erp_agent_abc.txt"'
        assert _extract_staging_path(content) == "tool_result_erp_agent_abc.txt"

    def test_extract_staging_path_archived(self):
        content = '数据文件: STAGING_DIR + "/tool_result_erp_agent_abc.txt"'
        assert _extract_staging_path(content) == "tool_result_erp_agent_abc.txt"

    def test_extract_staging_path_none(self):
        assert _extract_staging_path("普通文本结果") is None
        assert _extract_staging_path("") is None
        assert _extract_staging_path(None) is None

    def test_is_error_markers(self):
        assert _is_error("❌ 执行错误") is True
        assert _is_error("查询超时") is True
        assert _is_error("正常结果") is False
        assert _is_error("[已归档] xxx") is False
        assert _is_error("") is False

    def test_deduplicate(self):
        entries = [
            {"name": "erp_agent", "hint": "查订单", "ok": True},
            {"name": "erp_agent", "hint": "查订单", "ok": True, "staged": "new.txt"},
            {"name": "code_execute", "hint": "df.head", "ok": True},
        ]
        result = _deduplicate(entries)
        assert len(result) == 2
        # 保留最后一条（有 staged）
        assert result[0].get("staged") == "new.txt"
