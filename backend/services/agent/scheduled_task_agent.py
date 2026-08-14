"""已封存的定时任务旧 Agent 兼容边界。

Runtime Scheduled Worker 已取代本模块的执行职责。本文件保留结果类型、
任务上下文构建和兼容类名；旧 Agent 入口一律 fail-closed，不得创建 Provider、
ToolExecutor 或 ToolLoopExecutor。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


LEGACY_SCHEDULED_TASK_OWNER_DISABLED = "SCHEDULED_TASK_LEGACY_OWNER_DISABLED"
TOOL_TIMEOUT = 30.0
CONTEXT_WINDOW = 50000
DEFAULT_DEADLINE = 180.0
MAX_SCHEDULED_TURNS = 12
SCHEDULED_BLOCKED_TOOLS = frozenset({
    "get_conversation_context",
    "manage_scheduled_task",
})


class LegacyScheduledTaskOwnerDisabled(RuntimeError):
    """旧定时任务 Owner 被调用时的稳定 fail-closed 错误。"""

    code = LEGACY_SCHEDULED_TASK_OWNER_DISABLED

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"{self.code}: task_id={task_id}")


@dataclass
class ScheduledTaskResult:
    """保留给历史导入方的结果类型；Runtime 使用自己的 Projection。"""

    text: str
    summary: str = ""
    status: str = "success"
    tokens_used: int = 0
    turns_used: int = 0
    tools_called: List[str] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    is_truncated: bool = False
    error_message: str = ""


class ScheduledTaskAgent:
    """旧定时任务 Agent 的兼容定义；不可执行。"""

    def __init__(self, db: Any, task: Dict[str, Any]) -> None:
        self.db = db
        self.task = task
        self.task_id = task["id"]
        self.user_id = task["user_id"]
        self.org_id = task["org_id"]
        self.conversation_id = f"scheduled_{task['id']}"

        from utils.time_context import RequestContext

        self.request_ctx = RequestContext.build(
            user_id=self.user_id,
            org_id=self.org_id,
            request_id=str(self.task_id),
        )

    async def execute(self) -> ScheduledTaskResult:
        """拒绝旧执行链，确保调用前不产生任何副作用。"""
        raise LegacyScheduledTaskOwnerDisabled(task_id=str(self.task_id))

    def _build_light_context(self) -> List[Dict[str, Any]]:
        """保留无副作用的历史上下文格式化能力供诊断和兼容测试使用。"""
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是一个定时任务执行器。Runtime Scheduled Worker 负责执行任务。"
                ),
            },
            {"role": "system", "content": self.request_ctx.for_prompt_injection()},
        ]
        user_msg = f"## 任务\n{self.task['prompt']}"
        if self.task.get("template_file"):
            tpl = self.task["template_file"]
            user_msg += (
                f"\n\n## 模板文件\n模板文件路径: staging/{tpl['name']}"
                f", 使用 pd.read_excel('staging/{tpl['name']}') 读取模板结构"
                "，输出到 '下载/x.xlsx'"
            )
        if self.task.get("last_summary"):
            user_msg += f"\n\n## 上次执行摘要（仅供对比参考）\n{self.task['last_summary']}"
        messages.append({"role": "user", "content": user_msg})
        return messages

    def _build_tool_loop(self, *_args: Any, **_kwargs: Any) -> None:
        """拒绝旧 ToolLoop 装配；Runtime Worker 是唯一执行 Owner。"""
        raise LegacyScheduledTaskOwnerDisabled(task_id=str(self.task_id))

    async def _generate_summary(self, _text: str, _adapter: Any) -> str:
        """拒绝旧摘要模型调用；Runtime Projection 负责结果摘要。"""
        raise LegacyScheduledTaskOwnerDisabled(task_id=str(self.task_id))

    async def _prepare_template(self) -> None:
        """拒绝旧文件副作用；Runtime Artifact Executor 负责模板处理。"""
        raise LegacyScheduledTaskOwnerDisabled(task_id=str(self.task_id))
