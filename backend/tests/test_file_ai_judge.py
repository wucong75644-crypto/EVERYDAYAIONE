"""file_ai_judge 单元测试。

覆盖：
  - FileAnalyzeError.to_metadata
  - _classify_error 异常分类
  - _decide_final_category 综合判断
  - _parse_and_validate JSON 校验
  - build_prompt + simplified variant
  - adjudicate 失败链（mock LLM）

不含真实 LLM 调用集成测试（避免 CI 调外部 API）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.agent.file_ai_decision import AIDecision, ColumnSemantic
from services.agent.file_ai_judge import (
    ERROR_CATEGORIES,
    AnalyzeAttemptLog,
    FileAnalyzeError,
    _classify_error,
    _decide_final_category,
    _estimate_tokens,
    _parse_and_validate,
    adjudicate,
)
from services.agent.file_evidence import (
    ColumnEvidence,
    EvidencePool,
    SuspiciousRow,
)


# ── 测试 fixture ──

def _mini_pool(path_type: str = "A", rows: int = 100) -> EvidencePool:
    return EvidencePool(
        file_path="/tmp/test.xlsx",
        file_name="test.xlsx",
        file_size_bytes=1024,
        total_rows=rows,
        total_cols=3,
        sheet_names=["Sheet1"],
        target_sheet="Sheet1",
        path_type=path_type,
        header_candidates=[["A", "B", "C"], [1, 2, 3]],
        columns=[
            ColumnEvidence(col_letter="A", raw_header="id"),
            ColumnEvidence(col_letter="B", raw_header="name"),
            ColumnEvidence(col_letter="C", raw_header="amount"),
        ],
    )


_VALID_LLM_OUTPUT = {
    "header_row": 1,
    "data_start_row": 2,
    "header_type": "single",
    "column_semantics": [
        {"letter": "A", "business_name": "ID", "semantic_type": "id", "is_id_column": True},
        {"letter": "B", "business_name": "名字", "semantic_type": "name"},
        {"letter": "C", "business_name": "金额", "semantic_type": "amount", "is_order_level": True},
    ],
    "summary_rows": [],
    "overall_summary": "测试文件",
}


# ── _classify_error ──

class TestClassifyError:
    def test_json_decode(self):
        e = json.JSONDecodeError("msg", "doc", 0)
        assert _classify_error(e) == "llm_output_invalid"

    def test_timeout(self):
        e = asyncio.TimeoutError()
        assert _classify_error(e) == "timeout"

    def test_value_error_schema(self):
        e = ValueError("schema 不符")
        assert _classify_error(e) == "llm_output_invalid"

    def test_unknown_internal(self):
        e = RuntimeError("奇怪错误")
        assert _classify_error(e) == "internal_error"


# ── _decide_final_category ──

class TestDecideFinalCategory:
    def _logs(self, *cats):
        return [
            AnalyzeAttemptLog(
                attempt_number=i + 1, model="qwen-turbo", prompt_variant="default",
                error_category=c,
            )
            for i, c in enumerate(cats)
        ]

    def test_empty_attempts_internal(self):
        assert _decide_final_category([], _mini_pool()) == "internal_error"

    def test_all_auth_failure(self):
        logs = self._logs("auth_failure")
        assert _decide_final_category(logs, _mini_pool()) == "auth_failure"

    def test_repeated_llm_invalid_upgrades_to_too_complex(self):
        """≥2 次 llm_output_invalid → file_too_complex"""
        logs = self._logs("llm_output_invalid", "llm_output_invalid", "llm_output_invalid")
        assert _decide_final_category(logs, _mini_pool()) == "file_too_complex"

    def test_all_timeout_with_large_file(self):
        logs = self._logs("timeout", "timeout", "timeout")
        pool = _mini_pool(rows=200_000)
        assert _decide_final_category(logs, pool) == "file_too_complex"

    def test_all_timeout_small_file_keeps_unavailable(self):
        logs = self._logs("timeout", "timeout", "timeout")
        pool = _mini_pool(rows=100)
        assert _decide_final_category(logs, pool) == "api_unavailable"

    def test_mixed_network_to_api_unavailable(self):
        logs = self._logs("network_failure", "api_unavailable", "timeout")
        assert _decide_final_category(logs, _mini_pool()) == "api_unavailable"


# ── FileAnalyzeError ──

class TestFileAnalyzeError:
    def test_metadata_structure(self):
        err = FileAnalyzeError(
            error_category="file_too_complex",
            error_summary="failed",
            retryable=False,
            suggested_action="ask_user",
            user_message="转告用户的中文",
            file_name="x.xlsx",
            file_size_mb=12.5,
            total_rows=85033,
            path_type="A",
            attempts=[
                AnalyzeAttemptLog(1, "qwen-turbo", "default", elapsed_ms=8200,
                                  error_category="llm_output_invalid",
                                  error_message="JSON 缺字段"),
            ],
        )
        meta = err.to_metadata()
        assert meta["error_category"] == "file_too_complex"
        assert meta["retryable"] is False
        assert meta["suggested_action"] == "ask_user"
        assert meta["file_context"]["name"] == "x.xlsx"
        assert meta["file_context"]["rows"] == 85033
        assert len(meta["attempts_summary"]) == 1
        assert meta["attempts_summary"][0]["category"] == "llm_output_invalid"


# ── ERROR_CATEGORIES 完整性 ──

class TestErrorCategoriesComplete:
    def test_all_categories_have_template(self):
        for cat, cfg in ERROR_CATEGORIES.items():
            assert "retryable" in cfg, cat
            assert "suggested_action" in cfg, cat
            assert "user_template" in cfg, cat

    def test_suggested_actions_valid(self):
        valid = {"retry_immediately", "retry_after_delay", "ask_user", "escalate"}
        for cat, cfg in ERROR_CATEGORIES.items():
            assert cfg["suggested_action"] in valid, cat


# ── _parse_and_validate ──

class TestParseAndValidate:
    def test_valid_minimal(self):
        d = _parse_and_validate(_VALID_LLM_OUTPUT)
        assert d.header_row == 1
        assert d.data_start_row == 2
        assert len(d.column_semantics) == 3
        assert d.column_semantics[0].is_id_column is True
        assert d.overall_summary == "测试文件"

    def test_missing_required_field_raises(self):
        bad = dict(_VALID_LLM_OUTPUT)
        del bad["header_row"]
        with pytest.raises(ValueError, match="缺少必填字段"):
            _parse_and_validate(bad)

    def test_invalid_semantic_type_raises(self):
        bad = dict(_VALID_LLM_OUTPUT)
        bad["column_semantics"] = [
            {"letter": "A", "business_name": "x", "semantic_type": "invalid_xxx"},
        ]
        with pytest.raises(ValueError, match="schema 校验失败"):
            _parse_and_validate(bad)

    def test_full_strategy_fields_parsed(self):
        data = dict(_VALID_LLM_OUTPUT)
        data["merged_cell_actions"] = [
            {"range_str": "A2:H2", "action": "treat_as_header"},
        ]
        data["mixed_type_handling"] = [
            {"col_letter": "F", "action": "extract_unit_number", "unit": "kg"},
        ]
        data["preserve_empty_rows"] = [{"row": 105}]
        d = _parse_and_validate(data)
        assert len(d.merged_cell_actions) == 1
        assert d.merged_cell_actions[0].action == "treat_as_header"
        assert d.mixed_type_handling[0].unit == "kg"
        assert d.preserve_empty_rows[0].row == 105


# ── _estimate_tokens ──

class TestEstimateTokens:
    def test_pure_english(self):
        # 100 字符英文 ≈ 30 tokens
        assert 25 <= _estimate_tokens("a" * 100) <= 35

    def test_pure_chinese(self):
        # 100 字符中文 ≈ 50 tokens
        assert 45 <= _estimate_tokens("文" * 100) <= 55

    def test_mixed(self):
        # 各 50 → ~25 (中) + 15 (英) = 40
        t = _estimate_tokens("文" * 50 + "a" * 50)
        assert 35 <= t <= 45


# ── build_prompt ──

class TestBuildPrompt:
    def test_default_contains_all_sections(self):
        from services.agent.file_ai_prompt import build_prompt
        pool = _mini_pool()
        pool.suspicious_rows.append(SuspiciousRow(row=10, reason="keyword_match", keywords=["合计"]))
        prompt = build_prompt(pool, variant="default")
        assert "# 任务" in prompt
        assert "# 文件信息" in prompt
        assert "# 表头候选" in prompt
        assert "# 列证据" in prompt
        assert "# 可疑行" in prompt
        assert "# 你的输出格式" in prompt
        assert "JSON" in prompt or "json" in prompt

    def test_simplified_shorter_than_default(self):
        from services.agent.file_ai_prompt import build_prompt
        pool = _mini_pool()
        for i in range(30):
            pool.suspicious_rows.append(
                SuspiciousRow(row=i + 1, reason="multi_null", null_ratio=0.8)
            )
        default = build_prompt(pool, variant="default")
        simplified = build_prompt(pool, variant="simplified")
        assert len(simplified) < len(default)


# ── adjudicate 失败链（mock LLM）──

class TestAdjudicateFailureChain:
    @pytest.mark.asyncio
    async def test_first_attempt_succeeds(self):
        pool = _mini_pool()
        with patch(
            "services.agent.file_ai_judge._call_llm",
            new=AsyncMock(return_value=_VALID_LLM_OUTPUT),
        ):
            decision = await adjudicate(pool)
        assert decision.attempt_count == 1
        assert decision.model_used == "qwen-turbo"

    @pytest.mark.asyncio
    async def test_third_attempt_succeeds(self):
        """第 1、2 次失败，第 3 次（qwen-plus）成功。"""
        pool = _mini_pool()
        responses = [
            json.JSONDecodeError("bad", "doc", 0),
            ValueError("schema 不符"),
            _VALID_LLM_OUTPUT,
        ]
        calls = []

        async def mock_call(prompt, model, timeout):
            r = responses[len(calls)]
            calls.append(model)
            if isinstance(r, Exception):
                raise r
            return r

        with patch("services.agent.file_ai_judge._call_llm", new=mock_call):
            decision = await adjudicate(pool)
        assert decision.attempt_count == 3
        assert decision.model_used == "qwen-plus"
        assert calls == ["qwen-turbo", "qwen-turbo", "qwen-plus"]

    @pytest.mark.asyncio
    async def test_three_attempts_all_fail_raises_file_too_complex(self):
        """三次都是 schema 错误 → file_too_complex"""
        pool = _mini_pool()

        async def mock_call(*args, **kwargs):
            raise json.JSONDecodeError("bad", "doc", 0)

        with patch("services.agent.file_ai_judge._call_llm", new=mock_call):
            with pytest.raises(FileAnalyzeError) as exc:
                await adjudicate(pool)
        err = exc.value
        assert err.error_category == "file_too_complex"
        assert err.retryable is False
        assert err.suggested_action == "ask_user"
        assert len(err.attempts) == 3

    @pytest.mark.asyncio
    async def test_auth_failure_short_circuits(self):
        """auth_failure 立即短路，不尝试第 2/3 次。"""
        pool = _mini_pool()
        calls = []

        class FakeAuthError(Exception):
            pass
        FakeAuthError.__name__ = "AuthenticationError"

        async def mock_call(*args, **kwargs):
            calls.append(1)
            raise FakeAuthError("invalid key")

        with patch("services.agent.file_ai_judge._call_llm", new=mock_call):
            with pytest.raises(FileAnalyzeError) as exc:
                await adjudicate(pool)
        assert len(calls) == 1, "auth_failure 应短路只调 1 次"
        assert exc.value.error_category == "auth_failure"
        assert exc.value.suggested_action == "escalate"

    @pytest.mark.asyncio
    async def test_timeout_chain_large_file_to_too_complex(self):
        """三次全超时 + 大文件 → file_too_complex"""
        pool = _mini_pool(rows=200_000)

        async def mock_call(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("services.agent.file_ai_judge._call_llm", new=mock_call):
            with pytest.raises(FileAnalyzeError) as exc:
                await adjudicate(pool)
        assert exc.value.error_category == "file_too_complex"

    @pytest.mark.asyncio
    async def test_user_message_localized(self):
        """user_message 必须包含文件名（中文模板已格式化）。"""
        pool = _mini_pool()

        async def mock_call(*args, **kwargs):
            raise json.JSONDecodeError("bad", "doc", 0)

        with patch("services.agent.file_ai_judge._call_llm", new=mock_call):
            with pytest.raises(FileAnalyzeError) as exc:
                await adjudicate(pool)
        assert "test.xlsx" in exc.value.user_message
        # 中文格式
        assert "文件" in exc.value.user_message
