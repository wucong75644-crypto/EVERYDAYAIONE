"""定时任务独立 Agent — 共用 ToolLoopExecutor

设计文档: docs/document/TECH_定时任务心跳系统.md §4.3

核心设计：
- 不重构 ChatHandler，照抄 erp_agent.py 的 headless 模式
- 复用 ToolLoopExecutor / LoopConfig / LoopStrategy / LoopHook
- 无 WebSocket 依赖（task_id=None 让 ProgressNotifyHook 自然 no-op）
- 沙盒输出文件从 [FILE] 标记正则解析

2026-04-11 重构：删除 _run_tool_loop / _execute_tools 共 170 行重复代码，
改为装配 LoopConfig + LoopStrategy + 单 ToolAuditHook 走共享内核。
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass


# ════════════════════════════════════════════════════════
# 结果类型
# ════════════════════════════════════════════════════════

@dataclass
class ScheduledTaskResult:
    """定时任务执行结果"""
    text: str                                          # 结论文本（推送给用户）
    summary: str = ""                                  # ≤500 字摘要（写回 last_summary）
    status: str = "success"                            # success | partial | error | timeout
    tokens_used: int = 0
    turns_used: int = 0
    tools_called: List[str] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    is_truncated: bool = False
    error_message: str = ""


# ════════════════════════════════════════════════════════
# 安全护栏常量
# ════════════════════════════════════════════════════════

TOOL_TIMEOUT = 30.0                # 单工具超时上限
MAX_TOTAL_TOKENS = 50000           # Token 预算上限
DEFAULT_DEADLINE = 180.0           # 默认总执行时间预算（秒）
MAX_SCHEDULED_TURNS = 12           # 工具循环最大轮次（比 ERP 少，任务粒度更明确）

# 沙盒输出文件标记正则
# 来源：services/sandbox/functions.py 的 _auto_upload
# 格式：[FILE]{url}|{filename}|{mime_type}|{size}[/FILE]
_FILE_MARKER_RE = re.compile(
    r"\[FILE\](?P<url>[^|]+)\|(?P<name>[^|]+)\|(?P<mime>[^|]+)\|(?P<size>\d+)\[/FILE\]"
)


# ════════════════════════════════════════════════════════
# ScheduledTaskAgent
# ════════════════════════════════════════════════════════

class ScheduledTaskAgent:
    """定时任务 Agent — 复用 ToolLoopExecutor，无 WebSocket 依赖

    用法:
        agent = ScheduledTaskAgent(db, task_dict)
        result = await agent.execute()
    """

    def __init__(self, db: Any, task: Dict[str, Any]) -> None:
        self.db = db
        self.task = task
        self.task_id = task["id"]
        self.user_id = task["user_id"]
        self.org_id = task["org_id"]
        self.conversation_id = f"scheduled_{task['id']}"

        # RequestContext（时间事实层，复用 ERPAgent 模式）
        from utils.time_context import RequestContext
        self.request_ctx = RequestContext.build(
            user_id=self.user_id,
            org_id=self.org_id,
            request_id=str(self.task_id),
        )

    async def execute(self) -> ScheduledTaskResult:
        """主入口：执行定时任务，返回结构化结果"""
        total_tokens = 0
        tools_called: List[str] = []
        adapter = None

        try:
            # 1. 模板文件复制到 staging（如有）
            await self._prepare_template()

            # 2. 构建工具列表（chat 域全 13 工具集）
            from config.phase_tools import build_domain_tools
            all_tools = build_domain_tools("chat")

            # 3. 构建轻量上下文
            messages = self._build_light_context()

            # 4. 创建 LLM adapter
            from services.adapters.factory import create_chat_adapter
            from core.config import get_settings
            settings = get_settings()
            model_id = (
                getattr(settings, "agent_loop_model", None) or "qwen3.5-plus"
            )
            adapter = create_chat_adapter(
                model_id, org_id=self.org_id, db=self.db,
            )

            # 5. 创建 ToolExecutor
            from services.agent.tool_executor import ToolExecutor
            executor = ToolExecutor(
                db=self.db,
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                org_id=self.org_id,
                request_ctx=self.request_ctx,
            )

            # 6. 全局时间预算
            from services.agent.execution_budget import ExecutionBudget
            deadline = float(self.task.get("timeout_sec") or DEFAULT_DEADLINE)
            budget = ExecutionBudget(deadline)

            # 7. 共享 ToolLoopExecutor（无 WS 推送 / 无时间校验 / 无失败反思）
            tool_loop, hook_ctx = self._build_tool_loop(
                adapter, executor, all_tools,
            )

            result = await tool_loop.run(
                messages=messages,
                selected_tools=all_tools,  # 全工具可见
                tools_called=tools_called,
                hook_ctx=hook_ctx,
                budget=budget,
            )
            total_tokens += result.total_tokens
            text = result.text
            turns = result.turns

            # 8. 提取沙盒输出的文件
            files = self._extract_files(text)

            # 9. 生成摘要
            summary = await self._generate_summary(text, adapter)

            return ScheduledTaskResult(
                text=text or "",
                summary=summary,
                status="success",
                tokens_used=total_tokens,
                turns_used=turns,
                tools_called=tools_called,
                files=files,
                is_truncated="⚠ 输出已截断" in (text or ""),
            )

        except asyncio.TimeoutError:
            logger.warning(f"ScheduledTask timeout | task={self.task_id}")
            return ScheduledTaskResult(
                text="任务执行超时",
                status="timeout",
                tokens_used=total_tokens,
                tools_called=tools_called,
                error_message="execution_timeout",
            )
        except Exception as e:
            logger.error(
                f"ScheduledTask error | task={self.task_id} | error={e}"
            )
            return ScheduledTaskResult(
                text=f"任务执行出错: {e}",
                status="error",
                tokens_used=total_tokens,
                tools_called=tools_called,
                error_message=str(e)[:500],
            )
        finally:
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception:
                    pass
            # 延迟清理 staging
            asyncio.create_task(self._cleanup_staging_delayed())

    # ════════════════════════════════════════════════════════
    # 内部方法
    # ════════════════════════════════════════════════════════

    def _build_tool_loop(
        self,
        adapter: Any,
        executor: Any,
        all_tools: List[Dict[str, Any]],
    ) -> tuple:
        """装配 ToolLoopExecutor + HookContext（定时任务默认配置）

        与 ERPAgent 的差异：
        - 无 WebSocket 推送（task_id=None 让 ProgressNotifyHook 自然 no-op）
        - 无退出信号工具集（无人交互场景）
        - 无工具自动扩展（启动即 13 工具全可见）
        - 允许直接文本回复（任务可能不需要工具）
        - 仅挂 ToolAuditHook（不要 L4 校验/失败反思/进度推送）
        """
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.loop_types import (
            HookContext, LoopConfig, LoopStrategy,
        )
        from services.agent.loop_hooks import ToolAuditHook

        hook_ctx = HookContext(
            db=self.db,
            user_id=self.user_id,
            org_id=self.org_id,
            conversation_id=self.conversation_id,
            task_id=None,  # 无 WS 通道
            request_ctx=self.request_ctx,
        )

        tool_loop = ToolLoopExecutor(
            adapter=adapter,
            executor=executor,
            all_tools=all_tools,
            config=LoopConfig(
                max_turns=MAX_SCHEDULED_TURNS,
                max_tokens=MAX_TOTAL_TOKENS,
                tool_timeout=TOOL_TIMEOUT,
                no_synthesis_fallback_text=(
                    "定时任务执行未能生成完整结论，请检查任务指令。"
                ),
            ),
            strategy=LoopStrategy(
                exit_signals=frozenset(),
                enable_tool_expansion=False,
                force_tool_use_first=True,
            ),
            hooks=[ToolAuditHook()],
        )
        return tool_loop, hook_ctx

    def _build_light_context(self) -> List[Dict[str, Any]]:
        """轻量上下文：任务指令 + 模板提示 + 上次摘要"""
        system_prompt = (
            "你是一个定时任务执行器。执行以下任务并生成结果。\n"
            "要求：\n"
            "1. 完成任务指令中描述的工作\n"
            "2. 如需取数据，调用 erp_agent 工具\n"
            "3. 如需生成报表/计算，调用 code_execute 工具，文件输出到 OUTPUT_DIR\n"
            "4. 最终回复应简洁清晰，适合直接推送到企微群\n"
            "5. 不要使用 ask_user（无人交互场景）"
        )

        # 时间事实层
        time_injection = self.request_ctx.for_prompt_injection()

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": time_injection},
        ]

        # 用户任务消息
        user_msg = f"## 任务\n{self.task['prompt']}"

        # 模板文件提示
        if self.task.get("template_file"):
            tpl = self.task["template_file"]
            user_msg += (
                f"\n\n## 模板文件\n"
                f"已放入 staging 目录: staging/{tpl['name']}\n"
                f"使用 pd.read_excel(STAGING_DIR + '/{tpl['name']}') 读取模板结构，"
                f"按模板格式填入数据后输出到 OUTPUT_DIR"
            )

        # 上次执行摘要（跨次状态，借鉴 LangGraph stateful cron）
        if self.task.get("last_summary"):
            user_msg += (
                f"\n\n## 上次执行摘要（仅供对比参考）\n"
                f"{self.task['last_summary']}"
            )

        messages.append({"role": "user", "content": user_msg})
        return messages

    def _extract_files(self, text: str) -> List[Dict[str, Any]]:
        """从文本中提取沙盒输出的 [FILE] 标记

        沙盒 code_execute 的输出会自动包含 [FILE]url|name|mime|size[/FILE]
        参考 backend/services/sandbox/functions.py 的 _auto_upload
        """
        files: List[Dict[str, Any]] = []
        for match in _FILE_MARKER_RE.finditer(text or ""):
            try:
                files.append({
                    "url": match.group("url"),
                    "name": match.group("name"),
                    "mime": match.group("mime"),
                    "size": int(match.group("size")),
                })
            except (ValueError, KeyError):
                continue
        return files

    async def _generate_summary(self, text: str, adapter: Any) -> str:
        """生成 ≤500 字摘要，写回 last_summary 用于下次执行参考"""
        if not text:
            return ""
        if len(text) <= 500:
            return text

        try:
            messages = [
                {
                    "role": "system",
                    "content": "用 200 字以内总结以下定时任务执行结果，包含关键数据。",
                },
                {"role": "user", "content": text[:3000]},
            ]
            summary = ""
            async for chunk in adapter.stream_chat(
                messages=messages, temperature=0.3,
            ):
                if chunk.content:
                    summary += chunk.content
            return summary[:500]
        except Exception as e:
            logger.debug(
                f"_generate_summary failed | task={self.task_id} | error={e}"
            )
            return text[:500]

    async def _prepare_template(self) -> None:
        """模板文件复制到 staging 目录"""
        if not self.task.get("template_file"):
            return

        tpl = self.task["template_file"]
        try:
            from core.config import get_settings
            from services.file_executor import FileExecutor
            from pathlib import Path
            import shutil

            settings = get_settings()
            workspace_root = settings.file_workspace_root

            staging_dir = Path(workspace_root) / "staging" / self.conversation_id
            staging_dir.mkdir(parents=True, exist_ok=True)

            fe = FileExecutor(
                workspace_root=workspace_root,
                user_id=self.user_id,
                org_id=self.org_id,
            )
            src = fe.resolve_safe_path(tpl["path"])
            dst = staging_dir / tpl["name"]

            if src.exists():
                shutil.copy2(src, dst)
                logger.info(
                    f"Template prepared | task={self.task_id} | dst={dst}"
                )
            else:
                logger.warning(
                    f"Template not found | task={self.task_id} | path={tpl['path']}"
                )
        except Exception as e:
            logger.error(
                f"_prepare_template failed | task={self.task_id} | error={e}"
            )

    async def _cleanup_staging_delayed(self) -> None:
        """5 分钟后清理 staging 目录"""
        try:
            await asyncio.sleep(300)
            from core.config import get_settings
            from pathlib import Path
            import shutil

            settings = get_settings()
            staging_dir = (
                Path(settings.file_workspace_root)
                / "staging"
                / self.conversation_id
            )
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception as e:
            logger.debug(f"Staging cleanup failed | error={e}")
