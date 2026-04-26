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
        """
        # ask 模式无专属提示词（对齐 Claude Code default 模式）
        if self._mode == Mode.ASK:
            return None

        # 首轮总是 full
        if turn == 0:
            self._reminder_count = 1
            self._turns_since_last_reminder = 0
            return self._build_full()

        # 非首轮：节流
        self._turns_since_last_reminder += 1
        if self._turns_since_last_reminder < REMINDER_INTERVAL:
            return None

        # 触发提醒
        self._turns_since_last_reminder = 0
        self._reminder_count += 1
        if self._reminder_count % FULL_EVERY_N == 1:
            return self._build_full()
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

_PLAN_FULL_PROMPT = """\
=== 计划模式已激活 ===
你当前处于计划模式。用户希望先规划再执行——你 MUST NOT 直接执行任何数据操作。

## 允许的操作
- 调 erp_analyze 分析任务结构
- 调 ask_user 向用户确认方案或澄清需求
- 调 search_knowledge / web_search 辅助分析
- 调 code_execute 做辅助计算
- 读取文件（file_read / file_list / file_search / file_info）

## 工作流程
1. 调 erp_analyze 分析用户请求，获取执行计划
2. 向用户展示计划摘要（涉及哪些域、每步做什么、步骤间依赖）
3. 调 ask_user 请求用户确认
4. 用户确认后，系统会自动解锁执行工具

## 重要
- 你的回合只能以 ask_user（澄清/确认）或展示分析结果结束
- 不要假设用户意图，不确定就问
- MUST NOT 调用 erp_agent、generate_image、generate_video、social_crawler"""

_PLAN_SPARSE_PROMPT = """\
[提醒] 计划模式仍在生效（完整说明见前文）。\
只能分析不能执行，用 erp_analyze 分析，ask_user 确认。\
MUST NOT 调用 erp_agent。"""

_PLAN_EXIT_PROMPT = """\
=== 计划已确认，执行模式已解锁 ===
用户已批准你的方案。你现在可以调用 erp_agent 执行数据操作。
按照已确认的方案执行，不要偏离。如有疑问用 ask_user 澄清。"""

_AUTO_FULL_PROMPT = """\
=== 自动模式已激活 ===
用户选择了全自动执行模式。你应该：
1. 立即执行——做合理假设，直接推进
2. 减少打断——常规决策自行判断，不要频繁追问
3. 优先行动——不要主动进入计划模式，除非用户明确要求
4. 不要执行破坏性操作——删除数据、修改生产系统仍需用户确认
5. 不要泄露敏感信息——除非用户明确授权"""

_AUTO_SPARSE_PROMPT = """\
[提醒] 自动模式仍在生效（完整说明见前文）。\
自主执行，减少打断，优先行动。"""
