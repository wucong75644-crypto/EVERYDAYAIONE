"""
v2 端到端模拟测试 — 模拟真实客户询问的完整路由流程

覆盖场景：
- 6 种 domain 路由（chat/erp/crawler/image/video/ask_user）
- 25 个真实用户输入场景
- Phase 1 → model_selector → dispatch 全链路
- Phase 2 ERP/crawler 多步工具循环
- 边界场景（再来一张 / 品牌指定 / Phase1 失败降级 chat）
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_loop import AgentLoop


# ============================================================
# Helpers
# ============================================================


def _make_loop(has_image: bool = False) -> AgentLoop:
    """创建模拟 AgentLoop 实例"""
    loop = AgentLoop(db=None, user_id="sim_user", conversation_id="sim_conv")
    loop._settings = MagicMock()
    loop._settings.agent_loop_max_turns = 5
    loop._settings.agent_loop_max_tokens = 50000
    loop._has_image = has_image
    loop._thinking_mode = None
    loop._user_location = None
    loop._task_id = None
    loop._phase1_model = ""
    return loop


def _p1_resp(tool_name: str, args: dict) -> dict:
    """构造 Phase 1 LLM 响应"""
    return {
        "choices": [{"message": {"tool_calls": [{
            "id": "tc_p1",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            },
        }]}}],
        "usage": {"total_tokens": 200},
    }


def _p2_tool_resp(tool_calls_data: list) -> dict:
    """构造 Phase 2 LLM 响应（含 tool_calls）"""
    tcs = []
    for i, (name, args) in enumerate(tool_calls_data):
        tcs.append({
            "id": f"tc_p2_{i}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            },
        })
    return {
        "choices": [{"message": {"tool_calls": tcs}}],
        "usage": {"total_tokens": 500},
    }


def _p2_text_resp(text: str = "汇总回复") -> dict:
    """构造 Phase 2 纯文本响应（无 tool_calls，循环终止）"""
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": 300},
    }


# ============================================================
# A. Chat Domain — 7 场景（Phase 1 直接返回）
# ============================================================


class TestSimChatDomain:
    """聊天路由：简单问候 / 代码 / 品牌 / 搜索 / 推理 / 翻译"""

    @pytest.mark.parametrize("user_text,signals,expect_search", [
        ("你好", {}, False),
        ("帮我写一段Python排序代码", {"needs_code": True}, False),
        ("用Claude帮我分析代码", {"brand_hint": "claude"}, False),
        ("今天杭州天气怎么样", {"needs_search": True}, True),
        ("翻译这段话成英文", {}, False),
        ("解方程 x²+2x-3=0", {"needs_reasoning": True}, False),
        (
            "用deepseek写代码",
            {"brand_hint": "deepseek", "needs_code": True},
            False,
        ),
    ])
    @pytest.mark.asyncio
    async def test_chat_routing(self, user_text, signals, expect_search):
        loop = _make_loop()
        content = [TextPart(text=user_text)]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_chat", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT
        assert result.turns_used == 1
        assert result.model != ""
        if expect_search:
            assert result.tool_params.get("_needs_google_search") is True


# ============================================================
# B. Image Domain — 4 场景
# ============================================================


class TestSimImageDomain:
    """图片生成：单图 / 批量 / 编辑 / 高清"""

    @pytest.mark.asyncio
    async def test_single_image(self):
        loop = _make_loop()
        content = [TextPart(text="画一只猫")]
        signals = {"prompts": ["a cute cat"], "aspect_ratio": "1:1"}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_image", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params.get("prompt") == "a cute cat"

    @pytest.mark.asyncio
    async def test_batch_images(self):
        loop = _make_loop()
        content = [TextPart(text="画4张不同风格的日落")]
        signals = {
            "prompts": ["sunset oil", "sunset water", "sunset pixel", "sunset"],
            "aspect_ratio": "16:9",
        }

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_image", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.IMAGE
        assert len(result.batch_prompts) == 4

    @pytest.mark.asyncio
    async def test_image_edit(self):
        loop = _make_loop(has_image=True)
        content = [TextPart(text="把天空改成星空")]
        signals = {"prompts": ["starry sky"], "needs_edit": True}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_image", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.IMAGE

    @pytest.mark.asyncio
    async def test_empty_prompts_fallback(self):
        """空 prompts → 降级为 chat"""
        loop = _make_loop()
        content = [TextPart(text="画")]
        signals = {"prompts": []}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_image", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT


# ============================================================
# C. Video Domain — 2 场景
# ============================================================


class TestSimVideoDomain:

    @pytest.mark.asyncio
    async def test_basic_video(self):
        loop = _make_loop()
        content = [TextPart(text="生成一段海浪视频")]
        signals = {"prompt": "ocean waves crashing"}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_video", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.VIDEO
        assert result.tool_params.get("prompt") == "ocean waves crashing"

    @pytest.mark.asyncio
    async def test_pro_video(self):
        loop = _make_loop()
        content = [TextPart(text="做一段电影级日出延时")]
        signals = {"prompt": "cinematic sunrise timelapse", "needs_pro": True}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_video", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.VIDEO


# ============================================================
# D. Ask User — 2 场景
# ============================================================


class TestSimAskUser:

    @pytest.mark.asyncio
    async def test_need_info(self):
        loop = _make_loop()
        content = [TextPart(text="帮我查一下")]
        signals = {"message": "请问你想查什么？", "reason": "need_info"}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("ask_user", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT
        assert result.direct_reply == "请问你想查什么？"
        assert result.tool_params.get("_ask_reason") == "need_info"

    @pytest.mark.asyncio
    async def test_out_of_scope(self):
        loop = _make_loop()
        content = [TextPart(text="帮我做个网站")]
        signals = {
            "message": "抱歉，我目前无法直接搭建网站",
            "reason": "out_of_scope",
        }

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("ask_user", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.direct_reply == "抱歉，我目前无法直接搭建网站"


# ============================================================
# E. ERP Domain — Phase 2 多步循环（3 场景）
# ============================================================


class TestSimERPDomain:
    """ERP 查询：Phase 1 分类 → Phase 2 工具循环 → route_to_chat 出口"""

    @pytest.mark.asyncio
    async def test_order_query(self):
        """订单查询：P1→erp → P2→erp_trade_query → route_to_chat"""
        loop = _make_loop()
        content = [TextPart(text="查一下订单123456的物流")]

        # Phase 2 brain 返回序列：
        # Turn 1: 调 erp_trade_query
        # Turn 2: 调 route_to_chat（出口）
        brain_responses = [
            # Phase 1
            _p1_resp("route_erp", {"system_prompt": "ERP助手"}),
            # Phase 2 Turn 1: 调 ERP 工具
            _p2_tool_resp([("erp_trade_query", {
                "action": "order_list",
                "params": {"tid": "123456"},
            })]),
            # Phase 2 Turn 2: 出口
            _p2_tool_resp([("route_to_chat", {
                "system_prompt": "ERP订单助手",
            })]),
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock, side_effect=brain_responses,
        ), patch.object(
            loop.executor, "execute",
            new_callable=AsyncMock,
            return_value='{"order_id":"123456","status":"已发货"}',
        ), patch.object(
            loop, "_notify_progress",
            new_callable=AsyncMock,
        ), patch.object(
            loop, "_fire_and_forget_knowledge",
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT
        assert result.turns_used >= 2

    @pytest.mark.asyncio
    async def test_stock_query(self):
        """库存查询：P1→erp → P2→erp_product_query → route_to_chat"""
        loop = _make_loop()
        content = [TextPart(text="仓库A的库存有多少")]

        brain_responses = [
            _p1_resp("route_erp", {"system_prompt": "ERP助手"}),
            _p2_tool_resp([("erp_product_query", {
                "action": "stock_status",
                "params": {"warehouse_name": "仓库A"},
            })]),
            _p2_tool_resp([("route_to_chat", {
                "system_prompt": "库存查询助手",
            })]),
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock, side_effect=brain_responses,
        ), patch.object(
            loop.executor, "execute",
            new_callable=AsyncMock,
            return_value='{"total_stock":5000}',
        ), patch.object(
            loop, "_notify_progress",
            new_callable=AsyncMock,
        ), patch.object(
            loop, "_fire_and_forget_knowledge",
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_erp_text_exit(self):
        """ERP 查询后大脑直接文本回复（无 route_to_chat）"""
        loop = _make_loop()
        content = [TextPart(text="今天有多少退货")]

        brain_responses = [
            _p1_resp("route_erp", {"system_prompt": "ERP助手"}),
            _p2_tool_resp([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"status": 1},
            })]),
            _p2_text_resp("今天共有3单退货"),
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock, side_effect=brain_responses,
        ), patch.object(
            loop.executor, "execute",
            new_callable=AsyncMock,
            return_value='{"total":3}',
        ), patch.object(
            loop, "_notify_progress",
            new_callable=AsyncMock,
        ), patch.object(
            loop, "_fire_and_forget_knowledge",
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT


# ============================================================
# F. Crawler Domain — Phase 2（2 场景）
# ============================================================


class TestSimCrawlerDomain:

    @pytest.mark.asyncio
    async def test_xhs_search(self):
        """小红书搜索：P1→crawler → P2→social_crawler → route_to_chat"""
        loop = _make_loop()
        content = [TextPart(text="小红书上防晒霜推荐")]

        brain_responses = [
            _p1_resp("route_crawler", {
                "platform_hint": "xhs", "keywords": "防晒霜",
            }),
            _p2_tool_resp([("social_crawler", {
                "platform": "xhs", "keywords": "防晒霜推荐",
            })]),
            _p2_tool_resp([("route_to_chat", {
                "system_prompt": "社交媒体分析师",
            })]),
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock, side_effect=brain_responses,
        ), patch.object(
            loop.executor, "execute",
            new_callable=AsyncMock,
            return_value='[{"title":"安耐晒超好用","likes":2000}]',
        ), patch.object(
            loop, "_notify_progress",
            new_callable=AsyncMock,
        ), patch.object(
            loop, "_fire_and_forget_knowledge",
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_douyin_search(self):
        """抖音搜索"""
        loop = _make_loop()
        content = [TextPart(text="抖音上最火的短视频")]

        brain_responses = [
            _p1_resp("route_crawler", {
                "platform_hint": "dy", "keywords": "热门",
            }),
            _p2_tool_resp([("social_crawler", {
                "platform": "dy", "keywords": "热门视频",
            })]),
            _p2_tool_resp([("route_to_chat", {
                "system_prompt": "内容分析师",
            })]),
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock, side_effect=brain_responses,
        ), patch.object(
            loop.executor, "execute",
            new_callable=AsyncMock,
            return_value='[{"title":"爆款视频","views":100000}]',
        ), patch.object(
            loop, "_notify_progress",
            new_callable=AsyncMock,
        ), patch.object(
            loop, "_fire_and_forget_knowledge",
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT


# ============================================================
# G. 边界 / 容灾场景（5 场景）
# ============================================================


class TestSimEdgeCases:

    @pytest.mark.asyncio
    async def test_phase1_failure_defaults_to_chat(self):
        """Phase 1 异常 → 重试 1 次 → 降级为 chat 域（不回退 v1）"""
        loop = _make_loop()
        content = [TextPart(text="你好")]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            side_effect=Exception("Phase 1 LLM timeout"),
        ) as mock_brain:
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT
        # _call_brain 被调用 2 次（初始 + 重试）
        assert mock_brain.await_count == 2
        assert result.model != ""

    @pytest.mark.asyncio
    async def test_with_image_selects_vision_model(self):
        """用户发图片（非生成）→ chat + 视觉模型"""
        loop = _make_loop(has_image=True)
        content = [
            TextPart(text="这张图好看吗"),
            ImagePart(url="https://example.com/img.jpg"),
        ]
        signals = {"system_prompt": "图片分析师"}

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_chat", signals),
        ):
            result = await loop._execute_loop_v2(content)

        assert result.generation_type == GenerationType.CHAT
        # model_selector 应考虑 has_image=True
        assert result.model != ""

    @pytest.mark.asyncio
    async def test_history_context_used(self):
        """历史上下文正确传递给 Phase 1"""
        loop = _make_loop()
        content = [TextPart(text="再来一张")]
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "画一只猫"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "[图片已生成]"},
            ]},
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=history,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock,
            return_value=_p1_resp("route_image", {
                "prompts": ["a cute cat"],
            }),
        ) as mock_brain:
            result = await loop._execute_loop_v2(content)

        # 验证历史被传递给 Phase 1
        call_args = mock_brain.call_args
        messages = call_args[0][0]
        # 应有 system + history(2条) + user
        assert len(messages) >= 4
        assert result.generation_type == GenerationType.IMAGE

    @pytest.mark.asyncio
    async def test_knowledge_injection(self):
        """知识库结果注入 Phase 2 系统提示词"""
        loop = _make_loop()
        content = [TextPart(text="查一下订单状态")]
        knowledge = [
            {"title": "订单状态说明", "content": "WAIT_BUYER_PAY=待付款"},
        ]

        brain_responses = [
            _p1_resp("route_erp", {"system_prompt": "ERP助手"}),
            _p2_text_resp("请提供订单号"),
        ]

        mock_brain = AsyncMock(side_effect=brain_responses)

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=knowledge,
        ), patch.object(
            loop, "_call_brain", mock_brain,
        ):
            result = await loop._execute_loop_v2(content)

        # Phase 2 的系统提示词应包含知识库内容
        p2_call = mock_brain.call_args_list[1]
        p2_messages = p2_call[0][0]
        system_msg = p2_messages[0]["content"]
        assert "订单状态说明" in system_msg

    @pytest.mark.asyncio
    async def test_erp_model_injection(self):
        """Phase 2 出口自动注入 Phase 1 选定的模型"""
        loop = _make_loop()
        content = [TextPart(text="查库存")]

        brain_responses = [
            _p1_resp("route_erp", {}),
            _p2_tool_resp([("route_to_chat", {
                "system_prompt": "库存助手",
            })]),
        ]

        with patch.object(
            loop, "_get_recent_history",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_fetch_knowledge",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            loop, "_call_brain",
            new_callable=AsyncMock, side_effect=brain_responses,
        ), patch.object(
            loop, "_notify_progress",
            new_callable=AsyncMock,
        ), patch.object(
            loop, "_fire_and_forget_knowledge",
        ), patch.object(
            loop, "_record_ask_user_context",
        ):
            result = await loop._execute_loop_v2(content)

        # _inject_phase1_model 应注入 model
        assert result.model != ""
