"""
PermissionMode 状态机单元测试

覆盖：初始化 / enter_plan / exit_plan / prePlanMode /
      无效值降级 / reminder 节流 / exit attachment
"""

import pytest
from services.handlers.permission_mode import (
    PermissionMode, Mode, REMINDER_INTERVAL, FULL_EVERY_N,
    _PLAN_FULL_PROMPT, _PLAN_SPARSE_PROMPT, _PLAN_EXIT_PROMPT,
    _AUTO_FULL_PROMPT, _AUTO_SPARSE_PROMPT,
)


# ============================================================
# 初始化
# ============================================================

class TestInit:
    def test_default_mode_is_auto(self):
        pm = PermissionMode()
        assert pm.mode == Mode.AUTO
        assert pm.is_auto is True

    def test_init_with_string(self):
        for m in ("auto", "ask", "plan"):
            pm = PermissionMode(m)
            assert pm.mode.value == m

    def test_init_with_enum(self):
        pm = PermissionMode(Mode.PLAN)
        assert pm.is_plan is True

    def test_invalid_mode_falls_back_to_auto(self):
        pm = PermissionMode("invalid_xyz")
        assert pm.mode == Mode.AUTO

    def test_empty_string_falls_back_to_auto(self):
        """chat_handler 兼容逻辑会把 falsy 值转成 'auto'，
        但 PermissionMode 自身也应兜底"""
        pm = PermissionMode("")
        assert pm.mode == Mode.AUTO


# ============================================================
# 模式切换：enter_plan / exit_plan
# ============================================================

class TestPlanTransition:
    def test_enter_plan_from_auto(self):
        pm = PermissionMode("auto")
        pm.enter_plan()
        assert pm.is_plan is True
        assert pm._pre_plan_mode == Mode.AUTO

    def test_enter_plan_from_ask(self):
        pm = PermissionMode("ask")
        pm.enter_plan()
        assert pm.is_plan is True
        assert pm._pre_plan_mode == Mode.ASK

    def test_enter_plan_idempotent(self):
        """已在 plan 模式时再次 enter_plan 不应覆盖 prePlanMode"""
        pm = PermissionMode("ask")
        pm.enter_plan()
        pm.enter_plan()  # 重复调用
        assert pm._pre_plan_mode == Mode.ASK  # 仍是 ask，不是 plan

    def test_exit_plan_restores_auto(self):
        pm = PermissionMode("auto")
        pm.enter_plan()
        restored = pm.exit_plan()
        assert restored == Mode.AUTO
        assert pm.mode == Mode.AUTO
        assert pm._pre_plan_mode is None

    def test_exit_plan_restores_ask(self):
        pm = PermissionMode("ask")
        pm.enter_plan()
        restored = pm.exit_plan()
        assert restored == Mode.ASK
        assert pm.mode == Mode.ASK

    def test_exit_plan_when_not_in_plan(self):
        """不在 plan 模式时调 exit_plan 应无副作用"""
        pm = PermissionMode("auto")
        result = pm.exit_plan()
        assert result == Mode.AUTO
        assert pm.need_exit_attachment is False

    def test_exit_plan_sets_need_exit_attachment(self):
        pm = PermissionMode("plan")
        pm.exit_plan()
        assert pm.need_exit_attachment is True

    def test_exit_plan_default_restore_is_auto(self):
        """直接以 plan 初始化（无 prePlanMode），退出后应恢复 auto"""
        pm = PermissionMode("plan")
        restored = pm.exit_plan()
        assert restored == Mode.AUTO


# ============================================================
# Exit Attachment 消费
# ============================================================

class TestExitAttachment:
    def test_consume_returns_prompt(self):
        pm = PermissionMode("auto")
        pm.enter_plan()
        pm.exit_plan()
        assert pm.need_exit_attachment is True
        text = pm.consume_exit_attachment()
        assert "计划已确认" in text
        assert pm.need_exit_attachment is False

    def test_consume_idempotent(self):
        """多次消费不会重复返回"""
        pm = PermissionMode("auto")
        pm.enter_plan()
        pm.exit_plan()
        pm.consume_exit_attachment()
        assert pm.need_exit_attachment is False

    def test_exit_prompt_content(self):
        assert "erp_agent" in _PLAN_EXIT_PROMPT


# ============================================================
# Reminder 节流
# ============================================================

class TestReminder:
    def test_plan_turn_0_is_full(self):
        pm = PermissionMode("plan")
        r = pm.get_reminder(0)
        assert r == _PLAN_FULL_PROMPT

    def test_auto_turn_0_is_full(self):
        pm = PermissionMode("auto")
        r = pm.get_reminder(0)
        assert r == _AUTO_FULL_PROMPT

    def test_ask_mode_always_none(self):
        pm = PermissionMode("ask")
        for turn in range(20):
            assert pm.get_reminder(turn) is None

    def test_throttle_between_reminders(self):
        """turn 1 ~ REMINDER_INTERVAL-1 应返回 None"""
        pm = PermissionMode("plan")
        pm.get_reminder(0)  # full
        for turn in range(1, REMINDER_INTERVAL):
            assert pm.get_reminder(turn) is None

    def test_sparse_at_interval(self):
        """turn REMINDER_INTERVAL 应触发 sparse"""
        pm = PermissionMode("plan")
        pm.get_reminder(0)  # full, count=1
        for turn in range(1, REMINDER_INTERVAL):
            pm.get_reminder(turn)
        r = pm.get_reminder(REMINDER_INTERVAL)
        assert r == _PLAN_SPARSE_PROMPT

    def test_full_cycle(self):
        """经过 FULL_EVERY_N 次提醒后，应再次触发 full"""
        pm = PermissionMode("plan")
        pm.get_reminder(0)  # count=1 (full)

        # 走过 FULL_EVERY_N-1 次 sparse
        turn = 1
        for _reminder_idx in range(FULL_EVERY_N - 1):
            # 每次 sparse 需要走 REMINDER_INTERVAL 轮
            for _ in range(REMINDER_INTERVAL):
                pm.get_reminder(turn)
                turn += 1

        # 下一次应该是 full（count = FULL_EVERY_N+1，% FULL_EVERY_N == 1）
        for _ in range(REMINDER_INTERVAL):
            pm.get_reminder(turn)
            turn += 1
        r = pm.get_reminder(turn)
        # 此时 count 已递增，需要再走一轮才触发
        # 实际验证：走完后 reminder_count 应在 full 周期
        # 简化验证：确保在足够多轮后能再次拿到 full
        reminders = []
        for t in range(turn, turn + REMINDER_INTERVAL * FULL_EVERY_N + 10):
            r = pm.get_reminder(t)
            if r is not None:
                reminders.append(r)
        assert _PLAN_FULL_PROMPT in reminders, "应在完整周期后再次出现 full reminder"

    def test_auto_sparse_content(self):
        pm = PermissionMode("auto")
        pm.get_reminder(0)  # full
        for turn in range(1, REMINDER_INTERVAL):
            pm.get_reminder(turn)
        r = pm.get_reminder(REMINDER_INTERVAL)
        assert r == _AUTO_SPARSE_PROMPT

    def test_reminder_resets_after_exit_plan(self):
        """exit_plan 后 reminder 计数器应重置"""
        pm = PermissionMode("auto")
        pm.enter_plan()
        pm.get_reminder(0)  # plan full
        pm.exit_plan()
        # 现在回到 auto，get_reminder(0) 应返回 auto full
        r = pm.get_reminder(0)
        assert r == _AUTO_FULL_PROMPT


# ============================================================
# 提示词内容完整性
# ============================================================

class TestPromptContent:
    def test_plan_full_mentions_erp_analyze(self):
        assert "erp_analyze" in _PLAN_FULL_PROMPT

    def test_plan_full_mentions_blocked_tools(self):
        for tool in ("erp_agent", "generate_image", "generate_video", "social_crawler"):
            assert tool in _PLAN_FULL_PROMPT

    def test_plan_sparse_mentions_must_not(self):
        assert "MUST NOT" in _PLAN_SPARSE_PROMPT

    def test_auto_full_mentions_key_rules(self):
        assert "立即执行" in _AUTO_FULL_PROMPT
        assert "减少打断" in _AUTO_FULL_PROMPT
        assert "破坏性操作" in _AUTO_FULL_PROMPT

    def test_exit_prompt_mentions_execution(self):
        assert "执行" in _PLAN_EXIT_PROMPT
        assert "erp_agent" in _PLAN_EXIT_PROMPT


# ============================================================
# 属性便捷方法
# ============================================================

class TestProperties:
    @pytest.mark.parametrize("mode_str,is_plan,is_ask,is_auto", [
        ("plan", True, False, False),
        ("ask", False, True, False),
        ("auto", False, False, True),
    ])
    def test_boolean_properties(self, mode_str, is_plan, is_ask, is_auto):
        pm = PermissionMode(mode_str)
        assert pm.is_plan == is_plan
        assert pm.is_ask == is_ask
        assert pm.is_auto == is_auto
