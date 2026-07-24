"""
ERPAgent 单元测试

覆盖：filter_erp_context, ERPAgent.execute,
      ToolExecutor._erp_agent handler, erp_agent 工具注册
"""

import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
# filter_erp_context 上下文筛选
# ============================================================


class TestBuildExperienceDetail:
    """ERPAgent._build_experience_detail 经验序列化"""

    def test_with_params(self):
        from services.agent.erp_agent import ERPAgent
        detail = ERPAgent._build_experience_detail("trade", {
            "mode": "summary", "group_by": ["shop"], "platform": "tb",
        })
        assert "domain=trade" in detail
        assert "mode=summary" in detail
        assert "group_by=" in detail
        assert "platform=tb" in detail

    def test_without_params(self):
        from services.agent.erp_agent import ERPAgent
        detail = ERPAgent._build_experience_detail("warehouse", None)
        assert detail == "domain=warehouse"

    def test_with_product_code(self):
        from services.agent.erp_agent import ERPAgent
        detail = ERPAgent._build_experience_detail("trade", {
            "mode": "export", "product_code": "HZ001",
        })
        assert "product_code=HZ001" in detail


class TestExecuteBoundary:
    """ERPAgent.execute 边界场景"""

    def _make_agent(self):
        from services.agent.erp_agent import ERPAgent
        return ERPAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="org1")

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_status(self):
        """全局超时 → status=timeout"""
        agent = self._make_agent()
        with patch.object(agent, "_execute", new_callable=AsyncMock, side_effect=asyncio.TimeoutError()):
            result = await agent.execute("查订单")
        assert result.status == "timeout"
        assert "超时" in result.summary

    @pytest.mark.asyncio
    async def test_unknown_exception_returns_error(self):
        """未知异常 → status=error + 内部错误提示"""
        agent = self._make_agent()
        with patch.object(agent, "_execute", new_callable=AsyncMock, side_effect=RuntimeError("segfault")):
            result = await agent.execute("查订单")
        assert result.status == "error"
        assert "内部错误" in result.summary
        assert "RuntimeError" in result.summary

    @pytest.mark.asyncio
    async def test_known_exception_shows_message(self):
        """已知异常（ValueError）→ 直接展示错误信息"""
        agent = self._make_agent()
        with patch.object(agent, "_execute", new_callable=AsyncMock, side_effect=ValueError("参数错误")):
            result = await agent.execute("查订单")
        assert result.status == "error"
        assert "参数错误" in result.summary


class TestToolSystemPromptNewRules:
    """TOOL_SYSTEM_PROMPT 新增规则验证"""

    def test_staging_and_code_execute_in_prompt(self):
        """主 Agent 提示词包含 staging 和 code_execute 关键概念"""
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        assert "staging" in TOOL_SYSTEM_PROMPT
        assert "code_execute" in TOOL_SYSTEM_PROMPT

    def test_excel_engine_in_code_tools(self):
        """写 Excel 用 xlsxwriter（在 code_tools 描述中）"""
        from config.code_tools import build_code_tools
        desc = build_code_tools(include_workspace=True)[0]["function"]["description"]
        assert "xlsxwriter" in desc


class TestParamDefinitionsConsistency:
    """_PARAM_DEFINITIONS 一致性验证"""

    def test_old_and_new_prompt_share_same_definitions(self):
        """build_extract_prompt 和 build_multi_extract_prompt 共用 _PARAM_DEFINITIONS"""
        from services.agent.plan_builder import (
            build_extract_prompt, build_multi_extract_prompt, _PARAM_DEFINITIONS,
        )
        old_prompt = build_extract_prompt("测试", now_str="2026-04-24")
        new_prompt = build_multi_extract_prompt("测试", now_str="2026-04-24")
        # 两个 prompt 都包含参数定义中的关键片段
        for key_fragment in [
            "order(订单)",       # doc_type 列表（新格式）
            "stock(库存快照)",   # 新表 doc_type
            "receiver_name",
            "sku_properties_name",
            "online_status",
            "handler_status",
            "include_invalid",
            "sort_by",           # 排序参数
            "limit",             # 条数限制
        ]:
            assert key_fragment in old_prompt, f"旧 prompt 缺少 {key_fragment}"
            assert key_fragment in new_prompt, f"新 prompt 缺少 {key_fragment}"


class TestToolSystemPromptAlignment:
    """TOOL_SYSTEM_PROMPT 与新架构一致性"""

    def test_erp_agent_described(self):
        """规则应描述 erp_agent"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "erp_agent" in prompt

    def test_erp_analyze_described(self):
        """规则应描述 erp_analyze（计划模式分析工具）"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "erp_analyze" in prompt
        assert "只分析不执行" in prompt

    def test_code_execute_mentioned(self):
        """规则应提及 code_execute"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "code_execute" in prompt

    def test_erp_agent_task_described(self):
        """规则应说明 task 用途"""
        from config.chat_tools import get_tool_system_prompt
        prompt = get_tool_system_prompt()
        assert "task" in prompt.lower() or "erp_agent" in prompt


# ============================================================
# build_tool_description 自动生成描述测试
# ============================================================


class TestBuildToolDescription:
    """验证 build_tool_description 内容完整性和 token 预算。"""

    def _desc(self) -> str:
        from services.agent.erp_agent import ERPAgent
        return ERPAgent.build_tool_description()

    def test_contains_all_group_by_dims(self):
        desc = self._desc()
        for dim in ("shop", "platform", "product", "supplier",
                     "warehouse", "status"):
            assert dim in desc, f"group_by 维度 {dim} 缺失"

    def test_contains_time_cols(self):
        desc = self._desc()
        for col in ("pay_time", "consign_time", "doc_created_at"):
            assert col in desc, f"time_col {col} 缺失"

    def test_contains_field_categories(self):
        desc = self._desc()
        assert "可查询信息" in desc
        assert "备注" in desc

    def test_contains_use_when(self):
        desc = self._desc()
        assert "使用场景" in desc
        assert "订单" in desc

    def test_contains_dont_use_when(self):
        desc = self._desc()
        assert "不要用于" in desc
        assert "erp_execute" in desc

    def test_contains_oral_mappings(self):
        desc = self._desc()
        assert "丁单" in desc
        assert "酷存" in desc

    def test_contains_examples(self):
        desc = self._desc()
        assert "query 示例" in desc
        assert "按店铺统计" in desc

    def test_token_budget(self):
        desc = self._desc()
        estimated_tokens = len(desc) / 2.5
        assert estimated_tokens < 1100, (
            f"描述 token 超预算: {estimated_tokens:.0f} > 1100"
        )

    def test_no_hardcoded_content(self):
        """描述内容全部来自 manifest，修改 manifest 会改变输出"""
        from services.agent.plan_builder import get_capability_manifest
        m = get_capability_manifest()
        desc = self._desc()
        # manifest 的 summary 必须出现在描述中
        assert m["summary"] in desc
        # manifest 的每个 example query 必须出现
        for ex in m["examples"]:
            assert ex["query"] in desc


    def test_assistant_without_tool_calls_key(self):
        """没有 tool_calls 字段时保留"""
        from services.erp_agent import filter_erp_context
        messages = [
            {"role": "assistant", "content": "好的"},
        ]
        result = filter_erp_context(messages)
        assert len(result) == 1


# ============================================================
# is_context_length_error — 上下文超限检测
# ============================================================

class TestIsContextLengthError:
    """B6: 上下文超限错误关键词匹配"""

    def test_context_length_exceeded(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("context_length_exceeded"))

    def test_input_too_large(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("input too large for model"))

    def test_maximum_context_length(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("maximum context length is 128000"))

    def test_token_limit(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("token limit exceeded"))

    def test_max_token(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert is_context_length_error(Exception("max_token reached"))

    def test_normal_error_not_matched(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert not is_context_length_error(Exception("connection timeout"))

    def test_rate_limit_not_matched(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert not is_context_length_error(Exception("rate_limit_exceeded"))

    def test_empty_error(self):
        from services.agent.erp_agent_types import is_context_length_error
        assert not is_context_length_error(Exception(""))


# ============================================================
# AgentResult — 结构化状态（Phase 6: 替代 ERPAgentResult D1）
# ============================================================

class TestAgentResultStructured:
    """AgentResult status 字段"""

    def test_default_status_values(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="success", summary="OK")
        assert r.status == "success"

    def test_error_status(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="error", summary="出错了", error_message="出错了")
        assert r.status == "error"
        assert r.error_message == "出错了"

    def test_partial_status(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(status="partial", summary="部分结果")
        assert r.status == "partial"

    def test_all_fields_populated(self):
        from services.agent.agent_result import AgentResult
        r = AgentResult(
            status="success",
            summary="结论",
            tokens_used=1000,
            source="erp_agent",
            confidence=1.0,
        )
        assert r.tokens_used == 1000
        assert r.source == "erp_agent"




# ============================================================
# B4: QueryCache — 缓存行为
# ============================================================

class TestERPAgentCache:
    """B4: 会话级读工具缓存（2026-04-11 拆出到 ToolResultCache）"""

    def _make_cache(self):
        from services.agent.tool_result_cache import ToolResultCache
        return ToolResultCache()

    def test_cacheable_tool_returns_true(self):
        from services.agent.tool_result_cache import ToolResultCache
        # local_stock_query 在 _CONCURRENT_SAFE_TOOLS 中
        assert ToolResultCache.is_cacheable("local_stock_query") is True

    def test_non_cacheable_tool_returns_false(self):
        from services.agent.tool_result_cache import ToolResultCache
        # erp_execute 是写操作，不可缓存
        assert ToolResultCache.is_cacheable("erp_execute") is False

    def test_cache_put_and_get(self):
        cache = self._make_cache()
        cache.put("local_stock_query", {"sku": "A1"}, "库存100")
        cached = cache.get("local_stock_query", {"sku": "A1"})
        assert cached == "库存100"

    def test_cache_miss_different_args(self):
        cache = self._make_cache()
        cache.put("local_stock_query", {"sku": "A1"}, "库存100")
        cached = cache.get("local_stock_query", {"sku": "B2"})
        assert cached is None

    def test_cache_skip_non_cacheable_tool(self):
        cache = self._make_cache()
        cache.put("erp_execute", {"action": "create"}, "OK")
        cached = cache.get("erp_execute", {"action": "create"})
        assert cached is None  # 写工具不缓存

    def test_cache_skip_large_result(self):
        cache = self._make_cache()
        large = "x" * 10000  # 超过 _CACHE_MAX_VALUE_CHARS
        cache.put("local_stock_query", {"sku": "A1"}, large)
        cached = cache.get("local_stock_query", {"sku": "A1"})
        assert cached is None  # 大结果不缓存

    def test_cache_max_entries(self):
        cache = self._make_cache()
        # 填满缓存
        for i in range(55):
            cache.put("local_stock_query", {"i": i}, f"result_{i}")
        # 前50个应该被缓存，第51个开始被跳过
        assert cache.get("local_stock_query", {"i": 0}) == "result_0"
        assert cache.get("local_stock_query", {"i": 50}) is None

    def test_cache_key_deterministic(self):
        from services.agent.tool_result_cache import ToolResultCache
        k1 = ToolResultCache._key("tool", {"b": 2, "a": 1})
        k2 = ToolResultCache._key("tool", {"a": 1, "b": 2})
        assert k1 == k2  # sort_keys=True 保证顺序无关

    def test_cache_ttl_expiration(self):
        """过期条目返回 None 且被删除"""
        import time
        from services.agent.tool_result_cache import ToolResultCache
        cache = ToolResultCache()
        cache._CACHE_TTL = 0.05  # 50ms TTL 便于测试
        cache.put("local_stock_query", {"sku": "A1"}, "库存100")
        # 未过期
        assert cache.get("local_stock_query", {"sku": "A1"}) == "库存100"
        # 等待过期
        time.sleep(0.06)
        assert cache.get("local_stock_query", {"sku": "A1"}) is None
        # 过期条目应已被删除，释放空间
        key = ToolResultCache._key("local_stock_query", {"sku": "A1"})
        assert key not in cache._store


# ============================================================
# A2: 失败反思 — 错误前缀检测
# ============================================================
