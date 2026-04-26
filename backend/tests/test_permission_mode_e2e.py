"""
权限模式 E2E 模拟测试

模拟 chat_handler._stream_generate 中的真实路径：
  1. 前端参数 → PermissionMode 初始化 → 工具列表构建 → 提示词注入
  2. Tool loop 内 reminder 注入
  3. Plan 模式 ask_user 冻结 → 恢复 → exit_plan → 工具解锁
  4. 模式切换（auto→plan→确认→恢复auto / ask→plan→确认→恢复ask）
  5. 旧参数兼容（plan_mode=True → permission_mode="plan"）

每个测试模拟 chat_handler 的关键路径，不 mock LLM 调用，
只验证 PermissionMode 状态机 + get_tools_for_mode 在每个阶段的输出。
"""

import pytest
from services.handlers.permission_mode import PermissionMode, Mode
from config.chat_tools import get_tools_for_mode, _PLAN_MODE_BLOCKED


def _tool_names(mode: str, org_id: str = "test") -> set:
    return {t["function"]["name"] for t in get_tools_for_mode(mode, org_id=org_id)}


# ============================================================
# 场景 1：Auto 模式完整链路
# ============================================================

class TestAutoModeE2E:
    """用户在自动模式下发送 "查昨天淘宝订单" """

    def test_step1_frontend_sends_auto(self):
        """前端发 permission_mode='auto'"""
        params = {"permission_mode": "auto"}
        permission_mode = params.get("permission_mode", "auto")
        assert permission_mode == "auto"

    def test_step2_perm_init(self):
        """chat_handler 初始化 PermissionMode"""
        perm = PermissionMode(mode="auto")
        assert perm.is_auto is True
        assert perm.is_plan is False

    def test_step3_tool_list_has_erp_agent(self):
        """auto 模式工具列表包含 erp_agent"""
        names = _tool_names("auto")
        assert "erp_agent" in names
        assert "erp_analyze" in names

    def test_step4_full_reminder_injected(self):
        """首轮注入 auto full prompt"""
        perm = PermissionMode(mode="auto")
        prompt = perm.get_reminder(turn=0)
        assert prompt is not None
        assert "自动模式" in prompt
        assert "立即执行" in prompt

    def test_step5_loop_turn1_no_reminder(self):
        """turn 1 不注入 reminder（节流）"""
        perm = PermissionMode(mode="auto")
        perm.get_reminder(0)  # turn 0
        assert perm.get_reminder(1) is None

    def test_step6_no_exit_attachment(self):
        """auto 模式不会触发 exit attachment"""
        perm = PermissionMode(mode="auto")
        assert perm.need_exit_attachment is False


# ============================================================
# 场景 2：Ask 模式完整链路
# ============================================================

class TestAskModeE2E:
    """用户在确认模式下发送 "查昨天淘宝订单" """

    def test_step1_frontend_sends_ask(self):
        params = {"permission_mode": "ask"}
        assert params["permission_mode"] == "ask"

    def test_step2_perm_init(self):
        perm = PermissionMode(mode="ask")
        assert perm.is_ask is True

    def test_step3_tool_list_same_as_auto(self):
        """ask 模式工具列表与 auto 完全一致"""
        assert _tool_names("ask") == _tool_names("auto")

    def test_step4_no_reminder(self):
        """ask 模式无专属提示词（对齐 Claude Code default）"""
        perm = PermissionMode(mode="ask")
        for turn in range(10):
            assert perm.get_reminder(turn) is None


# ============================================================
# 场景 3：Plan 模式完整链路（核心场景）
# ============================================================

class TestPlanModeE2E:
    """
    用户切到计划模式，发送 "查供应商纸制品01的商品，再用编码查订单"

    预期流程：
    请求1: plan_mode → erp_analyze → ask_user → 冻结
    请求2: 用户确认 → 恢复 → exit_plan → erp_agent 解锁 → 执行
    """

    def test_request1_step1_frontend_sends_plan(self):
        params = {"permission_mode": "plan"}
        assert params["permission_mode"] == "plan"

    def test_request1_step2_perm_init(self):
        perm = PermissionMode(mode="plan")
        assert perm.is_plan is True
        assert perm._pre_plan_mode is None  # 直接 plan 初始化，无 prePlanMode

    def test_request1_step3_tool_list_no_erp_agent(self):
        """plan 模式架构层移除 erp_agent"""
        names = _tool_names("plan")
        assert "erp_agent" not in names
        assert "generate_image" not in names
        assert "generate_video" not in names
        assert "social_crawler" not in names

    def test_request1_step4_tool_list_has_analysis_tools(self):
        """plan 模式保留分析工具"""
        names = _tool_names("plan")
        assert "erp_analyze" in names
        assert "ask_user" in names
        assert "code_execute" in names
        assert "search_knowledge" in names
        assert "web_search" in names

    def test_request1_step5_full_prompt_injected(self):
        """首轮注入 plan full prompt"""
        perm = PermissionMode(mode="plan")
        prompt = perm.get_reminder(turn=0)
        assert "计划模式已激活" in prompt
        assert "MUST NOT" in prompt
        assert "erp_analyze" in prompt

    def test_request1_step6_llm_sees_correct_tools(self):
        """
        LLM 收到的 tools 列表：
        - erp_analyze ✅（分析用）
        - ask_user ✅（确认用）
        - erp_agent ❌（被移除）
        """
        tools = get_tools_for_mode("plan", org_id="test")
        tool_dict = {t["function"]["name"]: t for t in tools}
        assert "erp_analyze" in tool_dict
        assert "ask_user" in tool_dict
        assert "erp_agent" not in tool_dict

    def test_request1_step7_ask_user_freezes(self):
        """
        LLM 调 ask_user → 冻结状态到 DB
        此时 perm 仍是 plan 模式（冻结前不变）
        """
        perm = PermissionMode(mode="plan")
        # 模拟 turn 0: erp_analyze
        # 模拟 turn 1: ask_user → 冻结
        # 冻结时 perm 状态不变
        assert perm.is_plan is True
        assert perm.need_exit_attachment is False

    def test_request2_step1_user_confirms(self):
        """
        用户发 "确认执行"，前端仍带 permission_mode="plan"
        后端检测到 pending → 恢复
        """
        # 模拟 chat_handler 兼容逻辑
        permission_mode = "plan"
        perm = PermissionMode(mode=permission_mode)
        assert perm.is_plan is True

    def test_request2_step2_pending_detected_exit_plan(self):
        """
        恢复 pending 后，检测 perm.is_plan → exit_plan()
        """
        perm = PermissionMode(mode="plan")
        # 模拟 _pending 存在 → 恢复 → exit_plan
        assert perm.is_plan is True
        restored = perm.exit_plan()
        # 直接 plan 初始化无 prePlanMode → 默认恢复 auto
        assert restored == Mode.AUTO
        assert perm.mode == Mode.AUTO
        assert perm.is_plan is False

    def test_request2_step3_tools_rebuilt_with_erp_agent(self):
        """exit_plan 后重建工具列表，erp_agent 回来了"""
        perm = PermissionMode(mode="plan")
        perm.exit_plan()
        # 现在 perm.mode == auto
        names = _tool_names(perm.mode.value)
        assert "erp_agent" in names
        assert "erp_analyze" in names

    def test_request2_step4_exit_attachment_injected(self):
        """exit_plan 后 need_exit_attachment=True，下一轮注入"""
        perm = PermissionMode(mode="plan")
        perm.exit_plan()
        assert perm.need_exit_attachment is True

        # 模拟 loop 内消费
        text = perm.consume_exit_attachment()
        assert "计划已确认" in text
        assert "erp_agent" in text
        assert perm.need_exit_attachment is False

    def test_request2_step5_llm_can_call_erp_agent(self):
        """
        LLM 看到：
        1. exit attachment: "计划已确认，可以执行"
        2. tools 列表含 erp_agent
        → 调 erp_agent 执行
        """
        perm = PermissionMode(mode="plan")
        perm.exit_plan()
        tools = get_tools_for_mode(perm.mode.value, org_id="test")
        tool_dict = {t["function"]["name"]: t for t in tools}
        assert "erp_agent" in tool_dict


# ============================================================
# 场景 4：模式切换（auto→plan→确认→恢复 auto）
# ============================================================

class TestAutoToPlanSwitchE2E:
    """
    用户原本在 auto 模式，手动切到 plan，确认后恢复 auto
    """

    def test_full_switch_flow(self):
        # Step 1: 用户在 auto 模式
        perm = PermissionMode(mode="auto")
        assert perm.is_auto is True

        # Step 2: 手动切到 plan（前端切换 → 新请求带 plan）
        # 注意：这里是新请求，所以创建新 PermissionMode
        # 但为了测试 prePlanMode，模拟 enter_plan
        perm.enter_plan()
        assert perm.is_plan is True
        assert perm._pre_plan_mode == Mode.AUTO

        # Step 3: 工具列表被过滤
        names = _tool_names("plan")
        assert "erp_agent" not in names

        # Step 4: ask_user 确认后 exit_plan
        restored = perm.exit_plan()
        assert restored == Mode.AUTO
        assert perm.is_auto is True

        # Step 5: 工具列表恢复
        names = _tool_names(perm.mode.value)
        assert "erp_agent" in names

        # Step 6: exit attachment
        assert perm.need_exit_attachment is True
        text = perm.consume_exit_attachment()
        assert "erp_agent" in text


# ============================================================
# 场景 5：模式切换（ask→plan→确认→恢复 ask）
# ============================================================

class TestAskToPlanSwitchE2E:
    """
    用户原本在 ask 模式，手动切到 plan，确认后恢复 ask
    """

    def test_full_switch_flow(self):
        perm = PermissionMode(mode="ask")
        assert perm.is_ask is True

        perm.enter_plan()
        assert perm.is_plan is True
        assert perm._pre_plan_mode == Mode.ASK

        restored = perm.exit_plan()
        assert restored == Mode.ASK
        assert perm.is_ask is True

        # ask 模式也有完整工具列表
        names = _tool_names("ask")
        assert "erp_agent" in names


# ============================================================
# 场景 6：旧参数兼容（plan_mode=True）
# ============================================================

class TestLegacyPlanModeCompat:
    """
    旧前端或企微可能还在发 plan_mode=True
    chat_handler 兼容逻辑：plan_mode=True → permission_mode="plan"
    """

    def test_bool_true_to_plan(self):
        """模拟 chat_handler 第 381-385 行的兼容逻辑"""
        permission_mode = True  # 旧参数

        # chat_handler 兼容逻辑
        if permission_mode is True or permission_mode == "true":
            permission_mode = "plan"
        elif permission_mode is False or permission_mode == "false" or not permission_mode:
            permission_mode = "auto"

        assert permission_mode == "plan"
        perm = PermissionMode(mode=permission_mode)
        assert perm.is_plan is True

    def test_bool_false_to_auto(self):
        permission_mode = False

        if permission_mode is True or permission_mode == "true":
            permission_mode = "plan"
        elif permission_mode is False or permission_mode == "false" or not permission_mode:
            permission_mode = "auto"

        assert permission_mode == "auto"

    def test_string_true_to_plan(self):
        permission_mode = "true"

        if permission_mode is True or permission_mode == "true":
            permission_mode = "plan"
        elif permission_mode is False or permission_mode == "false" or not permission_mode:
            permission_mode = "auto"

        assert permission_mode == "plan"

    def test_none_to_auto(self):
        permission_mode = None

        if permission_mode is True or permission_mode == "true":
            permission_mode = "plan"
        elif permission_mode is False or permission_mode == "false" or not permission_mode:
            permission_mode = "auto"

        assert permission_mode == "auto"

    def test_no_param_defaults_auto(self):
        """params.get("permission_mode", "auto") 无参数时默认 auto"""
        params = {}
        permission_mode = params.get("permission_mode", "auto")
        assert permission_mode == "auto"


# ============================================================
# 场景 7：Plan 模式 pending 恢复的完整状态链
# ============================================================

class TestPlanPendingRestoreChain:
    """
    模拟 chat_handler 中 pending 恢复时的完整状态链：

    请求1 (plan): perm 初始化 → plan 工具 → ask_user → 冻结
    请求2 (plan): perm 初始化(plan) → 检测 pending → exit_plan → auto 工具 → exit attachment → LLM 执行
    """

    def test_complete_chain(self):
        # ── 请求 1 ──
        perm1 = PermissionMode(mode="plan")
        tools1 = get_tools_for_mode(perm1.mode.value, org_id="test")
        tools1_names = {t["function"]["name"] for t in tools1}

        # 验证请求1工具列表
        assert "erp_analyze" in tools1_names
        assert "ask_user" in tools1_names
        assert "erp_agent" not in tools1_names

        # 验证请求1提示词
        prompt1 = perm1.get_reminder(turn=0)
        assert "计划模式已激活" in prompt1

        # ask_user 冻结（perm1 状态不变）
        assert perm1.is_plan is True

        # ── 请求 2（用户发"确认执行"）──
        # 前端仍带 permission_mode="plan"
        perm2 = PermissionMode(mode="plan")
        prompt2_initial = perm2.get_reminder(turn=0)
        assert "计划模式已激活" in prompt2_initial

        tools2_before = get_tools_for_mode(perm2.mode.value, org_id="test")
        assert "erp_agent" not in {t["function"]["name"] for t in tools2_before}

        # 检测到 pending → exit_plan
        has_pending = True  # 模拟
        if has_pending and perm2.is_plan:
            restored_mode = perm2.exit_plan()

            # 验证状态切换
            assert restored_mode == Mode.AUTO  # 直接 plan 初始化无 prePlanMode
            assert perm2.mode == Mode.AUTO
            assert perm2.is_plan is False

            # 重建工具列表
            tools2_after = get_tools_for_mode(perm2.mode.value, org_id="test")
            tools2_after_names = {t["function"]["name"] for t in tools2_after}

            # 验证 erp_agent 已解锁
            assert "erp_agent" in tools2_after_names
            assert "erp_analyze" in tools2_after_names

        # 进入 loop
        # turn 0: 检查 exit attachment
        assert perm2.need_exit_attachment is True
        exit_text = perm2.consume_exit_attachment()
        assert "计划已确认" in exit_text
        assert perm2.need_exit_attachment is False

        # turn 0: reminder（已切回 auto）
        auto_reminder = perm2.get_reminder(turn=0)
        assert "自动模式" in auto_reminder

        # LLM 现在能看到 erp_agent + exit attachment + auto reminder
        # → 调 erp_agent 执行


# ============================================================
# 场景 8：连续多轮 plan 模式 reminder 注入时序
# ============================================================

class TestPlanReminderTiming:
    """
    模拟 plan 模式下多轮 tool loop 的 reminder 注入时序
    （如果 LLM 多次调 erp_analyze 反复分析）
    """

    def test_reminder_sequence(self):
        perm = PermissionMode(mode="plan")
        results = []

        for turn in range(12):
            # 模拟 loop 内逻辑
            reminder = None
            if turn == 0:
                # 首轮在 loop 外已注入，此处模拟 loop 内 turn>0 的逻辑
                reminder = perm.get_reminder(turn)
            elif turn > 0:
                # exit attachment 检查（plan 未退出，不触发）
                assert perm.need_exit_attachment is False
                # reminder 检查
                reminder = perm.get_reminder(turn)

            results.append((turn, "full" if reminder and "计划模式已激活" in reminder
                           else "sparse" if reminder and "提醒" in reminder
                           else "none"))

        # turn 0: full
        assert results[0] == (0, "full")
        # turn 1-4: none（节流）
        for i in range(1, 5):
            assert results[i] == (i, "none")
        # turn 5: sparse
        assert results[5] == (5, "sparse")
        # turn 6-9: none
        for i in range(6, 10):
            assert results[i] == (i, "none")
        # turn 10: sparse
        assert results[10] == (10, "sparse")
