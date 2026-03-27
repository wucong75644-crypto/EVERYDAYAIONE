"""
Agent Loop v2 集成测试 — 意图优先 + 动态工具加载

覆盖：
- v2 灰度开关分流
- Phase 1 分类 → 各 domain 直接返回
- Phase 1 失败 → 重试 + chat 降级
- Phase 2 ERP/crawler 多步循环
- _dispatch_direct_domain 各分支
- _build_image_result 格式转换
- _build_phase2_messages 消息构建
- _inject_phase1_model 模型注入
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_loop import AgentLoop
from services.agent_loop_v2 import AgentLoopV2Mixin
from services.agent_types import AgentResult, AgentGuardrails


# ── Helpers ──

def _text_content(text: str):
    return [TextPart(text=text)]


def _make_loop() -> AgentLoop:
    return AgentLoop(db=MagicMock(), user_id="u1", conversation_id="c1")


def _make_phase1_response(tool_name: str, arguments: dict) -> dict:
    """构造 Phase 1 格式响应"""
    return {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "tc_p1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    },
                }],
            },
        }],
        "usage": {"total_tokens": 80},
    }


def _v2_settings():
    """mock settings（v1 已废弃，全量走 v2）"""
    return MagicMock(
        agent_loop_max_turns=3,
        agent_loop_max_tokens=5000,
        agent_loop_provider="dashscope",
        agent_loop_model="qwen3.5-plus",
        dashscope_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        dashscope_api_key="test",
        agent_loop_timeout=30.0,
    )


# ============================================================
# TestV2GrayscaleSwitch — 灰度开关
# ============================================================


class TestV2EntryPoint:

    @pytest.mark.asyncio
    async def test_execute_loop_calls_v2(self):
        """_execute_loop 无条件走 v2 路径"""
        loop = _make_loop()
        loop._execute_loop_v2 = AsyncMock(
            return_value=AgentResult(
                generation_type=GenerationType.CHAT,
                model="test", turns_used=1, total_tokens=0,
            ),
        )

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value = _v2_settings()
            await loop._execute_loop(_text_content("hi"))

        loop._execute_loop_v2.assert_awaited_once()


# ============================================================
# TestV2ChatDomain — chat 域直接返回
# ============================================================


class TestV2ChatDomain:

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_chat_domain_returns_directly(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """route_chat → 直接返回 CHAT result（零 Phase 2）"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("route_chat", {
            "system_prompt": "你是AI助手",
            "needs_search": True,
        })

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("今天天气"))

        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "你是AI助手"
        assert result.tool_params["_needs_google_search"] is True
        assert result.turns_used == 1
        # Phase 1 只调用一次大脑
        assert mock_brain.await_count == 1

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_chat_with_brand_hint_selects_model(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """brand_hint=claude → model_selector 匹配 Claude 模型"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("route_chat", {
            "brand_hint": "claude",
        })

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("用claude回答"))

        assert result.generation_type == GenerationType.CHAT
        assert "claude" in result.model.lower()


# ============================================================
# TestV2ImageDomain — image 域
# ============================================================


class TestV2ImageDomain:

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_image_single_prompt(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """route_image 单图 → IMAGE result"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("route_image", {
            "prompts": ["a sunset over ocean"],
            "aspect_ratio": "16:9",
        })

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("画一幅日落"))

        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params["prompt"] == "a sunset over ocean"
        assert result.tool_params["aspect_ratio"] == "16:9"
        assert result.render_hints is not None

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_image_batch_prompts(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """route_image 多图 → batch_prompts"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("route_image", {
            "prompts": ["a cat", "a dog"],
            "aspect_ratio": "1:1",
        })

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("画猫和狗"))

        assert result.generation_type == GenerationType.IMAGE
        assert len(result.batch_prompts) == 2
        assert result.batch_prompts[0]["prompt"] == "a cat"

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_image_empty_prompts_fallback(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """route_image 无 prompts → fallback chat"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("route_image", {
            "prompts": [],
        })

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("画一张"))

        assert result.generation_type == GenerationType.CHAT


# ============================================================
# TestV2VideoDomain — video 域
# ============================================================


class TestV2VideoDomain:

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_video_domain(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """route_video → VIDEO result"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("route_video", {
            "prompt": "a timelapse of stars",
            "needs_pro": True,
        })

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("制作视频"))

        assert result.generation_type == GenerationType.VIDEO
        assert result.tool_params["prompt"] == "a timelapse of stars"
        assert result.render_hints is not None


# ============================================================
# TestV2AskUser — ask_user 域
# ============================================================


class TestV2AskUser:

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_ask_user_returns_direct_reply(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """ask_user → direct_reply + _ask_reason"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.return_value = _make_phase1_response("ask_user", {
            "message": "你想查什么数据？",
            "reason": "need_info",
        })

        loop = _make_loop()
        loop._settings = _v2_settings()
        loop._record_ask_user_context = MagicMock()

        result = await loop._execute_loop_v2(_text_content("帮我查一下"))

        assert result.direct_reply == "你想查什么数据？"
        assert result.tool_params["_ask_reason"] == "need_info"
        assert result.model == ""
        loop._record_ask_user_context.assert_called_once()


# ============================================================
# TestV2Phase1Fallback — Phase 1 失败回退
# ============================================================


class TestV2Phase1Fallback:

    @pytest.mark.asyncio
    @patch("services.agent_loop_v2.AgentLoopV2Mixin._fetch_knowledge", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._get_recent_history", new_callable=AsyncMock)
    @patch("services.agent_loop.AgentLoop._call_brain", new_callable=AsyncMock)
    async def test_phase1_exception_defaults_to_chat(
        self, mock_brain, mock_history, mock_knowledge,
    ):
        """Phase 1 异常 → 重试 1 次 → 降级为 chat 域（不回退 v1）"""
        mock_history.return_value = None
        mock_knowledge.return_value = None
        mock_brain.side_effect = Exception("API timeout")

        loop = _make_loop()
        loop._settings = _v2_settings()

        result = await loop._execute_loop_v2(_text_content("hello"))

        assert result.generation_type == GenerationType.CHAT
        # _call_brain 被调用 2 次（初始 + 重试）
        assert mock_brain.await_count == 2
        assert result.model != ""


# ============================================================
# TestDispatchDirectDomain — 域分发单元测试
# ============================================================


class TestDispatchDirectDomain:

    def test_chat_domain(self):
        loop = _make_loop()
        result = loop._dispatch_direct_domain(
            "chat", {"system_prompt": "测试", "needs_search": False},
            "model-x", 100,
        )
        assert result.generation_type == GenerationType.CHAT
        assert result.model == "model-x"
        assert result.system_prompt == "测试"

    def test_video_domain(self):
        loop = _make_loop()
        result = loop._dispatch_direct_domain(
            "video", {"prompt": "test"},
            "video-model", 50,
        )
        assert result.generation_type == GenerationType.VIDEO
        assert result.tool_params["prompt"] == "test"

    def test_ask_user_domain(self):
        loop = _make_loop()
        loop._record_ask_user_context = MagicMock()
        result = loop._dispatch_direct_domain(
            "ask_user", {"message": "追问", "reason": "need_info"},
            "", 30,
        )
        assert result.direct_reply == "追问"
        assert result.model == ""


# ============================================================
# TestBuildImageResult — 图片结果格式转换
# ============================================================


class TestBuildImageResult:

    def test_single_prompt_string(self):
        loop = _make_loop()
        result = loop._build_image_result(
            {"prompts": ["a cat"], "aspect_ratio": "4:3"},
            "image-model", 80,
        )
        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params["prompt"] == "a cat"
        assert result.tool_params["aspect_ratio"] == "4:3"

    def test_batch_prompts(self):
        loop = _make_loop()
        result = loop._build_image_result(
            {"prompts": ["a", "b", "c"], "aspect_ratio": "1:1"},
            "image-model", 80,
        )
        assert len(result.batch_prompts) == 3
        assert all(p["aspect_ratio"] == "1:1" for p in result.batch_prompts)

    def test_empty_prompts_fallback(self):
        loop = _make_loop()
        result = loop._build_image_result(
            {"prompts": []}, "image-model", 80,
        )
        assert result.generation_type == GenerationType.CHAT

    def test_default_aspect_ratio(self):
        loop = _make_loop()
        result = loop._build_image_result(
            {"prompts": ["a cat"]}, "m", 80,
        )
        assert result.tool_params["aspect_ratio"] == "1:1"


# ============================================================
# TestBuildPhase2Messages — Phase 2 消息构建
# ============================================================


class TestBuildPhase2Messages:

    def test_basic_messages(self):
        loop = _make_loop()
        loop._user_location = None
        msgs = loop._build_phase2_messages(
            "erp",
            [{"type": "text", "text": "查订单"}],
            None, None,
        )
        # system + user = 2
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert len(msgs) >= 2

    def test_with_history(self):
        loop = _make_loop()
        loop._user_location = None
        history = [
            {"role": "user", "content": [{"type": "text", "text": "上次"}]},
        ]
        msgs = loop._build_phase2_messages(
            "erp",
            [{"type": "text", "text": "这次"}],
            history, None,
        )
        # system + history_label + history + history_end_label + user
        assert any("对话记录" in m.get("content", "") for m in msgs)

    def test_with_knowledge(self):
        loop = _make_loop()
        loop._user_location = None
        knowledge = [{"title": "规则1", "content": "内容1"}]
        msgs = loop._build_phase2_messages(
            "erp",
            [{"type": "text", "text": "查"}],
            None, knowledge,
        )
        assert "经验知识" in msgs[0]["content"]


# ============================================================
# TestInjectPhase1Model — 模型注入
# ============================================================


class TestGetActionEnum:
    """_get_action_enum：从 schema 提取 action enum 列表"""

    def test_extracts_enum(self):
        schema = {
            "function": {
                "name": "erp_tool",
                "parameters": {
                    "properties": {
                        "action": {"type": "string", "enum": ["query", "create"]},
                    }
                }
            }
        }
        result = AgentLoopV2Mixin._get_action_enum(schema)
        assert result == ["query", "create"]

    def test_missing_action_returns_empty(self):
        schema = {"function": {"name": "t", "parameters": {"properties": {}}}}
        assert AgentLoopV2Mixin._get_action_enum(schema) == []

    def test_missing_enum_returns_empty(self):
        schema = {
            "function": {
                "name": "t",
                "parameters": {"properties": {"action": {"type": "string"}}},
            }
        }
        assert AgentLoopV2Mixin._get_action_enum(schema) == []

    def test_empty_schema(self):
        assert AgentLoopV2Mixin._get_action_enum({}) == []


class TestTryExpandTools:
    """_try_expand_tools：工具/action 兜底扩充"""

    def _tool_schema(self, name, actions=None):
        """构建工具 schema"""
        schema = {"function": {"name": name, "parameters": {"properties": {}}}}
        if actions:
            schema["function"]["parameters"]["properties"]["action"] = {
                "type": "string", "enum": actions,
            }
        return schema

    def _tool_call(self, name, arguments=None):
        return {
            "id": "tc1",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments or {}),
            },
        }

    def test_no_expansion_needed(self):
        """工具和 action 都在列表中 → 返回 None"""
        t1 = self._tool_schema("erp", ["query"])
        tc = self._tool_call("erp", {"action": "query"})
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools([tc], [t1], [t1], state)
        assert result is None

    def test_tool_expansion(self):
        """工具不在筛选列表 → 从全量补充"""
        t_current = self._tool_schema("erp", ["query"])
        t_missing = self._tool_schema("crawler")
        tc = self._tool_call("crawler")
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools(
            [tc], [t_current], [t_current, t_missing], state,
        )
        assert result is not None
        assert len(result) == 2
        assert state["tool_expanded"] is True

    def test_tool_expansion_only_once(self):
        """工具扩充仅限 1 次"""
        t_current = self._tool_schema("erp")
        t_missing = self._tool_schema("crawler")
        tc = self._tool_call("crawler")
        state = {"tool_expanded": True, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools(
            [tc], [t_current], [t_current, t_missing], state,
        )
        assert result is None

    def test_action_expansion(self):
        """action 不在当前 enum → 从全量 schema 补充"""
        t_partial = self._tool_schema("erp", ["query"])
        t_full = self._tool_schema("erp", ["query", "create", "delete"])
        tc = self._tool_call("erp", {"action": "create"})
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools(
            [tc], [t_partial], [t_full], state,
        )
        assert result is not None
        assert state["action_expanded"] is True
        # 返回的 schema 应该包含完整 enum
        expanded_enum = AgentLoopV2Mixin._get_action_enum(result[0])
        assert "create" in expanded_enum

    def test_action_expansion_only_once(self):
        """action 扩充仅限 1 次"""
        t_partial = self._tool_schema("erp", ["query"])
        t_full = self._tool_schema("erp", ["query", "create"])
        tc = self._tool_call("erp", {"action": "create"})
        state = {"tool_expanded": False, "action_expanded": True}
        result = AgentLoopV2Mixin._try_expand_tools(
            [tc], [t_partial], [t_full], state,
        )
        assert result is None

    def test_unknown_tool_ignored(self):
        """全量列表中也不存在的工具 → 跳过"""
        t1 = self._tool_schema("erp")
        tc = self._tool_call("nonexistent")
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools([tc], [t1], [t1], state)
        assert result is None

    def test_no_action_in_args(self):
        """tool_call 无 action 参数 → 跳过 action 检查"""
        t1 = self._tool_schema("erp", ["query"])
        tc = self._tool_call("erp", {"keyword": "test"})
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools([tc], [t1], [t1], state)
        assert result is None

    def test_invalid_json_arguments(self):
        """arguments 非法 JSON → 跳过"""
        t1 = self._tool_schema("erp", ["query"])
        tc = {"id": "tc1", "function": {"name": "erp", "arguments": "not-json{"}}
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools([tc], [t1], [t1], state)
        assert result is None

    def test_action_not_in_full_enum(self):
        """action 在全量 enum 中也不存在 → 不扩充"""
        t1 = self._tool_schema("erp", ["query"])
        t_full = self._tool_schema("erp", ["query", "create"])
        tc = self._tool_call("erp", {"action": "nonexistent"})
        state = {"tool_expanded": False, "action_expanded": False}
        result = AgentLoopV2Mixin._try_expand_tools(
            [tc], [t1], [t_full], state,
        )
        assert result is None


class TestInjectPhase1Model:

    def test_injects_when_empty(self):
        holder = {"decision": {
            "tool_name": "route_to_chat",
            "arguments": {"system_prompt": "test"},
        }}
        AgentLoopV2Mixin._inject_phase1_model(holder, "my-model")
        assert holder["decision"]["arguments"]["model"] == "my-model"

    def test_preserves_existing_model(self):
        holder = {"decision": {
            "tool_name": "route_to_chat",
            "arguments": {"model": "existing-model"},
        }}
        AgentLoopV2Mixin._inject_phase1_model(holder, "new-model")
        assert holder["decision"]["arguments"]["model"] == "existing-model"

    def test_no_decision_safe(self):
        holder = {}
        AgentLoopV2Mixin._inject_phase1_model(holder, "m")
        assert "decision" not in holder
