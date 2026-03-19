"""
v2 工作场景多样性模拟 — 真实日常沟通交互

覆盖维度：
- ERP 多步工作流（识别编码→查询→聚合→汇总）
- 时间相关查询（今天/昨天/本周/上月）
- 紧急/口语化表达（急！/帮我看看/搞一下）
- 裸编码（直接发一串数字）
- 极短输入（?/嗯/好）
- 上下文连续对话（ERP 查完接 chat 讨论）
- 搜索 + 分析型（爬虫后用代码聚合）
- 品牌偏好各种写法（gpt4/GPT/用claude/deepseek）
- 图片/视频精细需求（风格/尺寸/批量组合）
- Phase 2 多轮复杂编排（3-4 步 ERP 联查）
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_loop import AgentLoop


# ============================================================
# Helpers
# ============================================================


def _loop(has_image=False, location=None, thinking=None):
    lp = AgentLoop(db=None, user_id="wp", conversation_id="wp_c")
    lp._settings = MagicMock()
    lp._settings.agent_loop_v2_enabled = True
    lp._settings.agent_loop_max_turns = 6
    lp._settings.agent_loop_max_tokens = 80000
    lp._has_image = has_image
    lp._thinking_mode = thinking
    lp._user_location = location
    lp._task_id = None
    lp._phase1_model = ""
    return lp


def _p1(name, args, tokens=150):
    return {
        "choices": [{"message": {"tool_calls": [{
            "id": "p1", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]}}],
        "usage": {"total_tokens": tokens},
    }


def _p2t(calls, tokens=400):
    tcs = [{"id": f"t{i}", "type": "function",
            "function": {"name": n, "arguments": json.dumps(a)}}
           for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": {"tool_calls": tcs}}],
            "usage": {"total_tokens": tokens}}


def _p2x(text="", tokens=100):
    return {"choices": [{"message": {"content": text}}],
            "usage": {"total_tokens": tokens}}


def _ctx(lp, brain, history=None, knowledge=None, executor_ret="{}"):
    """返回 with 语句用的 context managers"""
    return [
        patch.object(lp, "_get_recent_history",
                     new_callable=AsyncMock, return_value=history),
        patch.object(lp, "_fetch_knowledge",
                     new_callable=AsyncMock, return_value=knowledge),
        patch.object(lp, "_call_brain", brain),
        patch.object(lp, "_notify_progress", new_callable=AsyncMock),
        patch.object(lp, "_fire_and_forget_knowledge"),
        patch.object(lp, "_record_ask_user_context"),
        patch.object(lp.executor, "execute",
                     new_callable=AsyncMock, return_value=executor_ret),
    ]


async def _run(lp, text, brain, **kw):
    c = _ctx(lp, brain, **kw)
    with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
        return await lp._execute_loop_v2([TextPart(text=text)])


# ============================================================
# A. ERP 多步工作流（4 场景）
# ============================================================


class TestERPWorkflows:

    @pytest.mark.asyncio
    async def test_identify_then_query(self):
        """裸编码 → erp_identify → erp_product_query → 汇总"""
        lp = _loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "6901234567890"})]),
            _p2t([("erp_product_query", {
                "action": "stock_status",
                "params": {"outer_id": "SKU001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "商品助手"})]),
        ])
        r = await _run(lp, "6901234567890", brain,
                       executor_ret='{"type":"barcode","sku":"SKU001"}')
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3

    @pytest.mark.asyncio
    async def test_multi_shop_statistics(self):
        """各店铺销量 → 先查店铺列表 → 再聚合 → code_execute → 汇总"""
        lp = _loop()
        exec_results = [
            '{"shops":["店铺A","店铺B"]}',
            '{"sales":150}',
            '{"sales":200}',
            '{"summary":"A:150, B:200, total:350"}',
        ]
        call_idx = 0

        async def mock_exec(name, args):
            nonlocal call_idx
            ret = exec_results[min(call_idx, len(exec_results) - 1)]
            call_idx += 1
            return ret

        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {
                "action": "shop_list",
            })]),
            _p2t([
                ("erp_trade_query", {
                    "action": "order_list",
                    "params": {"shop_name": "店铺A"},
                }),
                ("erp_trade_query", {
                    "action": "order_list",
                    "params": {"shop_name": "店铺B"},
                }),
            ]),
            _p2t([("code_execute", {
                "code": "print('A:150, B:200')",
            })]),
            _p2t([("route_to_chat", {"system_prompt": "统计分析师"})]),
        ])
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], \
                patch.object(lp.executor, "execute",
                             new_callable=AsyncMock,
                             side_effect=mock_exec):
            r = await lp._execute_loop_v2([TextPart(text="各店铺今天销量")])
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 4

    @pytest.mark.asyncio
    async def test_order_then_logistics(self):
        """查订单 → 查物流 → 汇总（两步关联查询）"""
        lp = _loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"tid": "18001234567890123456"},
            })]),
            _p2t([("erp_trade_query", {
                "action": "logistics_query",
                "params": {"logistics_no": "SF1234567890"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "物流助手"})]),
        ])
        r = await _run(lp, "淘宝单18001234567890123456的物流到哪了", brain,
                       executor_ret='{"logistics_no":"SF1234567890"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_aftersales_cross_tool(self):
        """售后查询 → 需要跨工具（erp_aftersales + erp_taobao）"""
        lp = _loop()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"status": 1, "start_date": "2026-03-18"},
            })]),
            _p2t([("erp_taobao_query", {
                "action": "refund_list",
                "params": {"start_date": "2026-03-18"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "售后助手"})]),
        ])
        r = await _run(lp, "昨天有多少淘宝退款", brain,
                       executor_ret='{"total":5}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# B. 口语化 / 极短 / 紧急表达（6 场景）
# ============================================================


class TestColloquialExpressions:

    @pytest.mark.parametrize("text,domain,signals", [
        ("急！仓库A的货发不出去了", "erp", {}),
        ("帮我看看这个月退货率", "erp", {}),
        ("搞一下上周的销售报表", "erp", {}),
    ])
    @pytest.mark.asyncio
    async def test_urgent_erp(self, text, domain, signals):
        lp = _loop()
        brain = AsyncMock(side_effect=[
            _p1(f"route_{domain}", signals),
            _p2x("正在查询中..."),
        ])
        r = await _run(lp, text, brain)
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.parametrize("text,tool,signals", [
        ("?", "route_chat", {}),
        ("嗯", "route_chat", {}),
        ("好的", "route_chat", {}),
    ])
    @pytest.mark.asyncio
    async def test_minimal_input(self, text, tool, signals):
        lp = _loop()
        brain = AsyncMock(return_value=_p1(tool, signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2([TextPart(text=text)])
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# C. 品牌偏好多样写法（5 场景）
# ============================================================


class TestBrandVariations:

    @pytest.mark.parametrize("text,brand_hint", [
        ("用GPT帮我写", "gpt"),
        ("claude分析一下", "claude"),
        ("deepseek写个代码", "deepseek"),
        ("用gemini翻译", "gemini"),
        ("帮我问问千问", "qwen"),
    ])
    @pytest.mark.asyncio
    async def test_brand_routing(self, text, brand_hint):
        lp = _loop()
        signals = {"brand_hint": brand_hint}
        brain = AsyncMock(return_value=_p1("route_chat", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2([TextPart(text=text)])
        assert r.generation_type == GenerationType.CHAT
        assert r.model != ""


# ============================================================
# D. 图片精细需求（4 场景）
# ============================================================


class TestImageVariations:

    @pytest.mark.asyncio
    async def test_specific_aspect_ratio(self):
        """竖版手机壁纸 → 9:16"""
        lp = _loop()
        signals = {
            "prompts": ["dreamy galaxy wallpaper"],
            "aspect_ratio": "9:16",
        }
        brain = AsyncMock(return_value=_p1("route_image", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="画一张竖版星空手机壁纸")])
        assert r.generation_type == GenerationType.IMAGE
        assert r.tool_params["aspect_ratio"] == "9:16"

    @pytest.mark.asyncio
    async def test_batch_with_mixed_styles(self):
        """4张不同风格 → batch prompts"""
        lp = _loop()
        signals = {
            "prompts": [
                "cat in oil painting style",
                "cat in watercolor style",
                "cat in pixel art style",
                "cat in anime style",
            ],
            "aspect_ratio": "1:1",
        }
        brain = AsyncMock(return_value=_p1("route_image", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="画4张不同风格的猫")])
        assert len(r.batch_prompts) == 4
        for p in r.batch_prompts:
            assert p["aspect_ratio"] == "1:1"

    @pytest.mark.asyncio
    async def test_edit_with_reference_image(self):
        """带参考图编辑 → has_image + needs_edit"""
        lp = _loop(has_image=True)
        signals = {"prompts": ["remove background"], "needs_edit": True}
        brain = AsyncMock(return_value=_p1("route_image", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2([
                TextPart(text="把背景去掉"),
                ImagePart(url="https://example.com/photo.jpg"),
            ])
        assert r.generation_type == GenerationType.IMAGE

    @pytest.mark.asyncio
    async def test_image_max_batch(self):
        """8 张批量（最大值）"""
        lp = _loop()
        prompts = [f"scene_{i}" for i in range(8)]
        signals = {"prompts": prompts, "aspect_ratio": "16:9"}
        brain = AsyncMock(return_value=_p1("route_image", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="画8张场景")])
        assert len(r.batch_prompts) == 8


# ============================================================
# E. 搜索 + 分析型对话（3 场景）
# ============================================================


class TestSearchAndAnalysis:

    @pytest.mark.asyncio
    async def test_chat_with_search(self):
        """需要实时信息的 chat → needs_search + 搜索模型"""
        lp = _loop(location="杭州")
        signals = {"needs_search": True, "system_prompt": "搜索助手"}
        brain = AsyncMock(return_value=_p1("route_chat", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="今天杭州PM2.5多少")])
        assert r.tool_params.get("_needs_google_search") is True

    @pytest.mark.asyncio
    async def test_crawler_with_analysis(self):
        """爬虫 + 分析：搜小红书 → 汇总分析"""
        lp = _loop()
        brain = AsyncMock(side_effect=[
            _p1("route_crawler", {"platform_hint": "xhs"}),
            _p2t([("social_crawler", {
                "platform": "xhs",
                "keywords": "防晒霜 油皮 2026",
            })]),
            _p2t([("route_to_chat", {
                "system_prompt": "美妆分析师，擅长总结社交媒体口碑趋势",
            })]),
        ])
        r = await _run(lp, "帮我看看小红书上油皮适合的防晒霜", brain,
                       executor_ret='[{"title":"安耐晒测评","likes":5000}]')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_knowledge_enhanced_erp(self):
        """ERP 查询 + 知识库注入（历史经验辅助）"""
        lp = _loop()
        knowledge = [
            {"title": "退货率说明",
             "content": "退货率=退货数/总订单数×100%"},
            {"title": "常见退货原因",
             "content": "质量问题>尺码不合>描述不符"},
        ]
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"start_date": "2026-03-01"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "退货分析师"})]),
        ])
        r = await _run(lp, "这个月退货率多少", brain,
                       knowledge=knowledge,
                       executor_ret='{"returns":30,"total_orders":500}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# F. 上下文连续对话（4 场景）
# ============================================================


class TestContextContinuity:

    @pytest.mark.asyncio
    async def test_followup_after_erp(self):
        """ERP 查完后用户追问 → 可能 chat 也可能再 erp"""
        lp = _loop()
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "查一下订单123的状态"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "订单123已发货，物流SF999"},
            ]},
        ]
        brain = AsyncMock(return_value=_p1("route_chat", {
            "system_prompt": "物流顾问",
        }))
        c = _ctx(lp, brain, history=history)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="大概几天能到")])
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_switch_from_chat_to_image(self):
        """聊天后突然要画图 → 切到 image"""
        lp = _loop()
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "你觉得什么颜色好看"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "蓝色和金色搭配很经典"},
            ]},
        ]
        brain = AsyncMock(return_value=_p1("route_image", {
            "prompts": ["blue and gold abstract art"],
            "aspect_ratio": "1:1",
        }))
        c = _ctx(lp, brain, history=history)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="那帮我画一张蓝金配色的抽象画")])
        assert r.generation_type == GenerationType.IMAGE

    @pytest.mark.asyncio
    async def test_repeat_last_generation(self):
        """「再来一张」→ 依赖历史识别上次是图片生成"""
        lp = _loop()
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "画一只柯基"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "[图片已生成，使用的提示词: corgi]"},
            ]},
        ]
        brain = AsyncMock(return_value=_p1("route_image", {
            "prompts": ["corgi in different pose"],
        }))
        c = _ctx(lp, brain, history=history)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="再来一张")])
        assert r.generation_type == GenerationType.IMAGE
        # 验证历史确实传给了 Phase 1
        p1_call = brain.call_args
        msgs = p1_call[0][0]
        has_history = any(
            "柯基" in str(m.get("content", "")) for m in msgs
        )
        assert has_history

    @pytest.mark.asyncio
    async def test_video_after_image(self):
        """上次画了图，这次要做视频"""
        lp = _loop()
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "画一个日落"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "[图片已生成]"},
            ]},
        ]
        brain = AsyncMock(return_value=_p1("route_video", {
            "prompt": "sunset timelapse over ocean",
        }))
        c = _ctx(lp, brain, history=history)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="把这个日落做成视频")])
        assert r.generation_type == GenerationType.VIDEO


# ============================================================
# G. 特殊信号组合（4 场景）
# ============================================================


class TestSignalCombinations:

    @pytest.mark.asyncio
    async def test_code_plus_reasoning(self):
        """需要代码 + 推理 → 双能力打分"""
        lp = _loop()
        signals = {
            "needs_code": True,
            "needs_reasoning": True,
            "system_prompt": "算法专家",
        }
        brain = AsyncMock(return_value=_p1("route_chat", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="帮我写一个动态规划解背包问题")])
        assert r.generation_type == GenerationType.CHAT
        assert r.model != ""

    @pytest.mark.asyncio
    async def test_search_plus_image(self):
        """chat(needs_search) — 不是画图，是讨论图片内容"""
        lp = _loop(has_image=True)
        signals = {"needs_search": True, "system_prompt": "图片鉴赏师"}
        brain = AsyncMock(return_value=_p1("route_chat", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2([
                TextPart(text="这幅画是谁画的？帮我搜一下"),
                ImagePart(url="https://example.com/painting.jpg"),
            ])
        assert r.generation_type == GenerationType.CHAT
        assert r.tool_params.get("_needs_google_search") is True

    @pytest.mark.asyncio
    async def test_thinking_mode_deep(self):
        """深度思考模式 → model_selector 选思考模型"""
        lp = _loop(thinking="deep")
        signals = {"needs_reasoning": True}
        brain = AsyncMock(return_value=_p1("route_chat", signals))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="深度分析这个数学证明")])
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_location_injected(self):
        """用户位置注入 Phase 1 提示词"""
        lp = _loop(location="上海浦东")
        brain = AsyncMock(return_value=_p1("route_chat", {
            "needs_search": True,
        }))
        c = _ctx(lp, brain)
        with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
            r = await lp._execute_loop_v2(
                [TextPart(text="附近有什么好吃的")])
        # 验证位置注入到 Phase 1 消息
        p1_call = brain.call_args
        system_msg = p1_call[0][0][0]["content"]
        assert "上海浦东" in system_msg
