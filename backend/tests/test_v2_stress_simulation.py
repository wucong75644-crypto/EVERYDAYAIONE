"""
v2 压力/恶劣场景模拟 — 最坏情况全覆盖

故障注入维度：
- Phase 1：畸形响应 / 超时 / 未知工具 / JSON 损坏 / 并行获取双异常
- Phase 2：工具超时 / 执行异常 / 幻觉工具 / 循环检测 / Token 爆预算
         / 轮次耗尽 / 空 choices / 多工具并行 / 模型覆盖冲突
- 数据畸形：prompts 非字符串 / 空输入 / signals 缺失键 / knowledge 畸形
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.message import GenerationType, TextPart
from services.agent_loop import AgentLoop
from services.agent_types import AgentResult


# ============================================================
# Helpers
# ============================================================


def _make_loop() -> AgentLoop:
    loop = AgentLoop(db=None, user_id="stress", conversation_id="stress_c")
    loop._settings = MagicMock()
    loop._settings.agent_loop_v2_enabled = True
    loop._settings.agent_loop_max_turns = 3
    loop._settings.agent_loop_max_tokens = 5000
    loop._has_image = False
    loop._thinking_mode = None
    loop._user_location = None
    loop._task_id = None
    loop._phase1_model = ""
    return loop


def _p1(tool_name: str, args: dict, tokens: int = 200) -> dict:
    return {
        "choices": [{"message": {"tool_calls": [{
            "id": "tc_p1", "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            },
        }]}}],
        "usage": {"total_tokens": tokens},
    }


def _p2_tools(calls: list, tokens: int = 500) -> dict:
    tcs = [{
        "id": f"tc_{i}", "type": "function",
        "function": {"name": n, "arguments": json.dumps(a)},
    } for i, (n, a) in enumerate(calls)]
    return {
        "choices": [{"message": {"tool_calls": tcs}}],
        "usage": {"total_tokens": tokens},
    }


def _p2_text(text: str = "", tokens: int = 100) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": tokens},
    }


def _p2_empty() -> dict:
    return {"choices": [], "usage": {"total_tokens": 50}}


def _base_patches(loop, brain, history=None, knowledge=None):
    """返回 Phase 1/2 共用的 mock 上下文列表"""
    return [
        patch.object(loop, "_get_recent_history",
                     new_callable=AsyncMock, return_value=history),
        patch.object(loop, "_fetch_knowledge",
                     new_callable=AsyncMock, return_value=knowledge),
        patch.object(loop, "_call_brain", brain),
        patch.object(loop, "_notify_progress",
                     new_callable=AsyncMock),
        patch.object(loop, "_fire_and_forget_knowledge"),
        patch.object(loop, "_record_ask_user_context"),
    ]


# ============================================================
# A. Phase 1 畸形响应（7 场景）
# ============================================================


class TestPhase1Malformed:

    @pytest.mark.asyncio
    async def test_empty_choices_fallback(self):
        """Phase 1 返回空 choices → 兜底 chat"""
        loop = _make_loop()
        resp = {"choices": [], "usage": {"total_tokens": 100}}
        brain = AsyncMock(return_value=resp)
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        # _parse_phase1_response 返回 ("chat", {})
        assert result.generation_type == GenerationType.CHAT
        assert result.turns_used == 1

    @pytest.mark.asyncio
    async def test_no_tool_calls_fallback(self):
        """Phase 1 有 message 但无 tool_calls → 兜底 chat"""
        loop = _make_loop()
        resp = {
            "choices": [{"message": {"content": "直接回复了"}}],
            "usage": {"total_tokens": 100},
        }
        brain = AsyncMock(return_value=resp)
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_invalid_json_arguments(self):
        """Phase 1 工具参数 JSON 损坏 → 空 signals，兜底 chat"""
        loop = _make_loop()
        resp = {
            "choices": [{"message": {"tool_calls": [{
                "id": "tc_1", "type": "function",
                "function": {
                    "name": "route_chat",
                    "arguments": "{broken json!!!",
                },
            }]}}],
            "usage": {"total_tokens": 100},
        }
        brain = AsyncMock(return_value=resp)
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_unknown_tool_name(self):
        """Phase 1 返回未知工具名 → domain=chat"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("route_unknown_xyz", {}))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_brain_timeout(self):
        """Phase 1 大脑调用超时 → 重试 1 次 → 降级 chat"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=TimeoutError("brain timeout"))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT
        assert brain.await_count == 2
        assert result.model != ""

    @pytest.mark.asyncio
    async def test_brain_connection_error(self):
        """Phase 1 网络连接异常 → 重试 1 次 → 降级 chat"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=ConnectionError("refused"))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT
        assert brain.await_count == 2
        assert result.model != ""

    @pytest.mark.asyncio
    async def test_no_usage_in_response(self):
        """Phase 1 响应缺 usage 字段 → 不崩溃"""
        loop = _make_loop()
        resp = {
            "choices": [{"message": {"tool_calls": [{
                "id": "tc_1", "type": "function",
                "function": {
                    "name": "route_chat",
                    "arguments": json.dumps({}),
                },
            }]}}],
            # 无 usage 键
        }
        brain = AsyncMock(return_value=resp)
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT
        assert result.total_tokens == 0


# ============================================================
# B. 并行获取双异常（3 场景）
# ============================================================


class TestParallelFetchFailures:

    @pytest.mark.asyncio
    async def test_history_exception_swallowed(self):
        """_get_recent_history 抛异常 → 降级为 None，不影响路由"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("route_chat", {}))
        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection lost"),
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(loop, "_call_brain", brain):
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_knowledge_exception_swallowed(self):
        """_fetch_knowledge 抛异常 → 降级为 None"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("route_chat", {}))
        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock,
            side_effect=RuntimeError("vector DB down"),
        ), patch.object(loop, "_call_brain", brain):
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_both_fetch_fail(self):
        """历史 + 知识库同时异常 → 仍能正常路由"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("route_chat", {}))
        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB down"),
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock,
            side_effect=RuntimeError("vector DB down"),
        ), patch.object(loop, "_call_brain", brain):
            result = await loop._execute_loop_v2([TextPart(text="x")])
        assert result.generation_type == GenerationType.CHAT
        assert result.model != ""


# ============================================================
# C. Phase 2 工具执行故障（6 场景）
# ============================================================


class TestPhase2ToolFailures:

    @pytest.mark.asyncio
    async def test_tool_execution_timeout(self):
        """ERP 工具执行超时 → 错误回传大脑 → 大脑用文本回复"""
        import asyncio
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_tools([("erp_trade_query", {
                "action": "order_list", "params": {},
            })]),
            _p2_text("抱歉，查询超时了"),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(
                    loop.executor, "execute",
                    new_callable=AsyncMock,
                    side_effect=asyncio.TimeoutError(),
                ):
            result = await loop._execute_loop_v2([TextPart(text="查订单")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_tool_execution_exception(self):
        """ERP 工具执行报错 → 错误回传大脑"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_tools([("erp_product_query", {
                "action": "stock_status", "params": {},
            })]),
            _p2_text("系统暂时无法查询"),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(
                    loop.executor, "execute",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("快麦 API 500"),
                ):
            result = await loop._execute_loop_v2([TextPart(text="查库存")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_hallucinated_tool_rejected(self):
        """Phase 2 大脑幻觉调用不存在的工具 → 被拒绝"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_tools([("fake_nonexistent_tool", {"query": "test"})]),
            _p2_text("已为您查询"),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="查库存")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_empty_choices_in_phase2(self):
        """Phase 2 大脑返回空 choices → 立即终止"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_empty(),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="查订单")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_multiple_tools_single_turn(self):
        """Phase 2 单轮返回多个工具调用 → 全部执行"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            # 一次返回 2 个 ERP 工具
            _p2_tools([
                ("erp_trade_query", {
                    "action": "order_list", "params": {},
                }),
                ("erp_product_query", {
                    "action": "stock_status", "params": {},
                }),
            ]),
            _p2_tools([("route_to_chat", {
                "system_prompt": "汇总",
            })]),
        ])
        call_count = 0

        async def mock_execute(tool_name, args):
            nonlocal call_count
            call_count += 1
            return f'{{"result": "ok_{call_count}"}}'

        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             side_effect=mock_execute):
            result = await loop._execute_loop_v2([TextPart(text="查订单和库存")])
        assert result.generation_type == GenerationType.CHAT
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_tool_invalid_json_args(self):
        """Phase 2 工具参数 JSON 损坏 → 被验证拒绝"""
        loop = _make_loop()
        resp2 = {
            "choices": [{"message": {"tool_calls": [{
                "id": "tc_bad", "type": "function",
                "function": {
                    "name": "erp_trade_query",
                    "arguments": "NOT_JSON{{{",
                },
            }]}}],
            "usage": {"total_tokens": 200},
        }
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            resp2,
            _p2_text("已重试"),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="查订单")])
        assert result.generation_type == GenerationType.CHAT


# ============================================================
# D. 安全护栏触发（3 场景）
# ============================================================


class TestGuardrailsTriggered:

    @pytest.mark.asyncio
    async def test_token_budget_exceeded(self):
        """Phase 2 Token 预算耗尽 → 优雅超时"""
        loop = _make_loop()
        loop._settings.agent_loop_max_tokens = 800  # 极小预算
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}, tokens=500),
            # Phase 2 第一轮再加 500 → 超 800 预算
            _p2_tools([("erp_trade_query", {
                "action": "order_list", "params": {},
            })], tokens=500),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             return_value="{}"):
            result = await loop._execute_loop_v2([TextPart(text="查订单")])
        # should_abort() 在下一轮检查
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_max_turns_exceeded(self):
        """Phase 2 轮次耗尽 → 优雅超时"""
        loop = _make_loop()
        loop._settings.agent_loop_max_turns = 2  # 只允许 2 轮
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            # Turn 0
            _p2_tools([("erp_trade_query", {
                "action": "order_list", "params": {},
            })]),
            # Turn 1 — 又调工具，不给 route_to_chat
            _p2_tools([("erp_product_query", {
                "action": "stock_status", "params": {},
            })]),
            # Turn 2 不会执行（max_turns=2）
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             return_value="{}"):
            result = await loop._execute_loop_v2([TextPart(text="查")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_loop_detection_abort(self):
        """Phase 2 连续 3 次相同工具调用 → 循环检测中止"""
        loop = _make_loop()
        loop._settings.agent_loop_max_turns = 5
        same_call = ("erp_trade_query", {
            "action": "order_list",
            "params": {"tid": "same"},
        })
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_tools([same_call]),
            _p2_tools([same_call]),
            _p2_tools([same_call]),  # 第3次 → 触发循环检测
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             return_value="{}"):
            result = await loop._execute_loop_v2([TextPart(text="查")])
        assert result.generation_type == GenerationType.CHAT


# ============================================================
# E. 数据畸形（5 场景）
# ============================================================


class TestMalformedData:

    @pytest.mark.asyncio
    async def test_prompts_non_string_items(self):
        """prompts 列表含非字符串（dict/int/None）→ 转 str"""
        loop = _make_loop()
        signals = {
            "prompts": [{"nested": "dict"}, 42, None],
            "aspect_ratio": "1:1",
        }
        brain = AsyncMock(return_value=_p1("route_image", signals))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="画")])
        assert result.generation_type == GenerationType.IMAGE
        assert result.batch_prompts is not None
        for item in result.batch_prompts:
            assert isinstance(item["prompt"], str)

    @pytest.mark.asyncio
    async def test_empty_user_text(self):
        """空字符串用户输入 → 仍能路由"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("route_chat", {}))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_signals_all_none(self):
        """Phase 1 信号全为 None → 不崩溃"""
        loop = _make_loop()
        signals = {
            "system_prompt": None, "brand_hint": None,
            "needs_code": None, "needs_search": None,
        }
        brain = AsyncMock(return_value=_p1("route_chat", signals))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="hi")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_video_no_prompt(self):
        """route_video 但 prompt 为空 → 不崩溃"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("route_video", {}))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="视频")])
        assert result.generation_type == GenerationType.VIDEO
        assert result.tool_params.get("prompt") == ""

    @pytest.mark.asyncio
    async def test_ask_user_no_message(self):
        """ask_user 但无 message 字段 → 空字符串兜底"""
        loop = _make_loop()
        brain = AsyncMock(return_value=_p1("ask_user", {}))
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="?")])
        assert result.generation_type == GenerationType.CHAT
        assert result.direct_reply == ""


# ============================================================
# F. 模型注入边界（3 场景）
# ============================================================


class TestModelInjectionEdge:

    @pytest.mark.asyncio
    async def test_phase2_route_preserves_existing_model(self):
        """Phase 2 route_to_chat 已带 model → 不覆盖"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_tools([("route_to_chat", {
                "system_prompt": "助手",
                "model": "user_chosen_model",
            })]),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="查")])
        assert result.model == "user_chosen_model"

    @pytest.mark.asyncio
    async def test_graceful_timeout_has_model(self):
        """Phase 2 超时也携带 Phase 1 模型"""
        loop = _make_loop()
        loop._settings.agent_loop_max_tokens = 300
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}, tokens=300),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="查")])
        # 虽然超时，model 仍来自 Phase 1 select_model
        assert result.model != ""

    @pytest.mark.asyncio
    async def test_phase2_text_exit_has_model(self):
        """Phase 2 大脑纯文本回复（非 route_to_chat）→ 也携带模型"""
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2_text("我不知道怎么查"),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            result = await loop._execute_loop_v2([TextPart(text="查")])
        assert result.model != ""
        assert result.generation_type == GenerationType.CHAT


# ============================================================
# G. 复合恶劣场景（3 场景）
# ============================================================


class TestCompoundFailures:

    @pytest.mark.asyncio
    async def test_tool_fail_then_retry_then_succeed(self):
        """工具第一次失败 → 大脑重试 → 第二次成功 → 出口"""
        import asyncio
        loop = _make_loop()
        loop._settings.agent_loop_max_turns = 5
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            # Turn 0: 调 ERP
            _p2_tools([("erp_trade_query", {
                "action": "order_list", "params": {"tid": "111"},
            })]),
            # Turn 1: 大脑看到错误后重试（不同参数避免循环检测）
            _p2_tools([("erp_trade_query", {
                "action": "order_list", "params": {"tid": "222"},
            })]),
            # Turn 2: 成功后出口
            _p2_tools([("route_to_chat", {
                "system_prompt": "订单助手",
            })]),
        ])
        exec_count = 0

        async def mock_exec(tool_name, args):
            nonlocal exec_count
            exec_count += 1
            if exec_count == 1:
                raise asyncio.TimeoutError()
            return '{"order":"222","status":"ok"}'

        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             side_effect=mock_exec):
            result = await loop._execute_loop_v2([TextPart(text="查订单")])
        assert result.generation_type == GenerationType.CHAT
        assert result.turns_used >= 3

    @pytest.mark.asyncio
    async def test_hallucinate_then_recover(self):
        """大脑先幻觉工具 → 被拒 → 再正确调用 → 成功"""
        loop = _make_loop()
        loop._settings.agent_loop_max_turns = 5
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            # Turn 0: 幻觉工具
            _p2_tools([("ghost_tool_xyz", {"a": 1})]),
            # Turn 1: 正确工具
            _p2_tools([("erp_trade_query", {
                "action": "order_list", "params": {},
            })]),
            # Turn 2: 出口
            _p2_tools([("route_to_chat", {
                "system_prompt": "助手",
            })]),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             return_value='{"ok":true}'):
            result = await loop._execute_loop_v2([TextPart(text="查")])
        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_crawler_timeout_then_text_exit(self):
        """爬虫超时 → 大脑放弃爬虫，直接文本回复"""
        import asyncio
        loop = _make_loop()
        brain = AsyncMock(side_effect=[
            _p1("route_crawler", {"platform_hint": "xhs"}),
            _p2_tools([("social_crawler", {
                "platform": "xhs", "keywords": "防晒霜",
            })]),
            _p2_text("抱歉，社交平台暂时无法访问"),
        ])
        ctx = _base_patches(loop, brain)
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], \
                patch.object(loop.executor, "execute",
                             new_callable=AsyncMock,
                             side_effect=asyncio.TimeoutError()):
            result = await loop._execute_loop_v2([TextPart(text="搜")])
        assert result.generation_type == GenerationType.CHAT
        assert result.direct_reply == "抱歉，社交平台暂时无法访问"
