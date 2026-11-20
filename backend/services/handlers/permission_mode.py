"""
权限模式状态机（对齐 Claude Code ToolPermissionContext）

三种模式：
  auto — 全自动执行，不问用户
  ask  — 可执行，dangerous 工具需用户确认
  plan — 只分析不执行，用户确认后解锁（恢复 prePlanMode）

提示词注入策略（对齐 Claude Code attachment）：
  - 每种模式有专属 system prompt（plan/auto 有 full+sparse，ask 无）
  - 首轮注入 full，之后每 REMINDER_INTERVAL 轮注入一次
  - 每 FULL_EVERY_N 次注入中第 1 次为 full，其余为 sparse
  - 退出 plan 模式时一次性注入 exit 提示词
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class Mode(str, Enum):
    AUTO = "auto"
    ASK = "ask"
    PLAN = "plan"


# ── 节流配置（对齐 Claude Code ATTACHMENT_CONFIG）──
REMINDER_INTERVAL = 5       # 每 N 轮触发一次提醒
FULL_EVERY_N = 5            # 每 N 次提醒中第 1 次为 full


class PermissionMode:
    """会话级权限模式状态机"""

    def __init__(self, mode: str = Mode.AUTO):
        try:
            self._mode = Mode(mode) if isinstance(mode, str) else mode
        except ValueError:
            self._mode = Mode.AUTO  # 无效值降级为 auto
        self._pre_plan_mode: Optional[Mode] = None
        self._reminder_count = 0
        self._turns_since_last_reminder = 0
        self._need_exit_attachment = False

    # ── 属性 ──

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def is_plan(self) -> bool:
        return self._mode == Mode.PLAN

    @property
    def is_ask(self) -> bool:
        return self._mode == Mode.ASK

    @property
    def is_auto(self) -> bool:
        return self._mode == Mode.AUTO

    @property
    def need_exit_attachment(self) -> bool:
        return self._need_exit_attachment

    # ── 模式切换 ──

    def enter_plan(self) -> None:
        """进入 plan 模式，保存当前模式以便恢复"""
        if self._mode == Mode.PLAN:
            return
        self._pre_plan_mode = self._mode
        self._mode = Mode.PLAN
        self._reminder_count = 0
        self._turns_since_last_reminder = 0
        self._need_exit_attachment = False

    def exit_plan(self) -> Mode:
        """退出 plan 模式，恢复 prePlanMode，返回恢复到的模式"""
        if self._mode != Mode.PLAN:
            return self._mode
        restored = self._pre_plan_mode or Mode.AUTO
        self._mode = restored
        self._pre_plan_mode = None
        self._need_exit_attachment = True
        self._reminder_count = 0
        self._turns_since_last_reminder = 0
        return restored

    def consume_exit_attachment(self) -> str:
        """消费 exit attachment（一次性），返回提示词文本"""
        self._need_exit_attachment = False
        return _PLAN_EXIT_PROMPT

    # ── 提醒节流 ──

    def get_reminder(self, turn: int) -> Optional[str]:
        """返回当前轮次应注入的提示词，无需注入时返回 None

        turn: 0-based 轮次号

        V2 行为变化:
          turn > 0 时永远返回 sparse, 不再返回 full
          原因: full 内容已合并到 PromptBuilder Layer 1 的 templates/modes.md
                工具循环再注入一遍是重复 (~161 字符 / 每 5 轮)
                v2 只用 sparse 提醒 (~80 字符) 唤醒 LLM 当前模式

          turn == 0 路径保留 (v1 兼容, 但 chat_handler 已不再调用 turn=0)
        """
        # ask 模式无专属提示词（对齐 Claude Code default 模式）
        if self._mode == Mode.ASK:
            return None

        # 首轮: 保留兼容老测试 (chat_handler 实际不再调用 turn=0)
        if turn == 0:
            self._reminder_count = 1
            self._turns_since_last_reminder = 0
            return self._build_full()

        # 非首轮: 节流
        self._turns_since_last_reminder += 1
        if self._turns_since_last_reminder < REMINDER_INTERVAL:
            return None

        # 触发提醒 (V2: 永远 sparse, 不再周期性 full)
        # full 内容已在 Layer 1 modes.md, 工具循环只需轻量唤醒
        self._turns_since_last_reminder = 0
        self._reminder_count += 1
        return self._build_sparse()

    # ── 提示词构建 ──

    def _build_full(self) -> str:
        if self._mode == Mode.PLAN:
            return _PLAN_FULL_PROMPT
        if self._mode == Mode.AUTO:
            return _AUTO_FULL_PROMPT
        return ""

    def _build_sparse(self) -> str:
        if self._mode == Mode.PLAN:
            return _PLAN_SPARSE_PROMPT
        if self._mode == Mode.AUTO:
            return _AUTO_SPARSE_PROMPT
        return ""


# ============================================================
# 提示词模板
# ============================================================

_PLAN_FULL_PROMPT = (
    "=== 计划模式已激活 ===\n"
    "按 <permission_mode> 的 plan 约束和本轮工具定义规划。展示方案后停止，等待用户确认。"
)

_PLAN_SPARSE_PROMPT = (
    "[提醒] 计划模式仍在生效。MUST NOT 开始执行方案，展示方案后等待用户确认。"
)

_PLAN_EXIT_PROMPT = (
    "=== 已退出计划模式 ===\n"
    "按用户确认的范围和本轮实际权限及工具推进。"
)

_AUTO_FULL_PROMPT = (
    "=== 自动模式已激活 ===\n"
    "按 <permission_mode> 的 auto 约束推进。必要输入明确时直接执行。"
)

_AUTO_SPARSE_PROMPT = "[提醒] 自动模式仍在生效。自主推进已授权的任务。"
