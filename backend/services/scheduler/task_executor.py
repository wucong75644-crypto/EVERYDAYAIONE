"""定时任务执行编排器

职责：
1. 创建执行记录
2. 用 credit_lock 锁定积分
3. 调用 ScheduledTaskAgent 执行
4. 推送结果（push_dispatcher）
5. 更新任务状态 + 写日志
6. 失败处理（重试/暂停/通知）
7. WebSocket 推送任务状态变化到前端

设计文档: docs/document/TECH_定时任务心跳系统.md §4.3.3
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from loguru import logger

from services.scheduler.cron_utils import calc_next_run


class ScheduledTaskExecutor:
    """定时任务执行编排器"""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def _push_ws_event(self, user_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """通过 WebSocketManager 推送事件到任务创建者前端

        Args:
            user_id: 任务创建者的 user_id（推送给他自己 + 同 org 主管/老板）
            event_type: scheduled_task_started / scheduled_task_completed / scheduled_task_failed
            data: 事件数据

        异常用 warning 级别：WS 推送失败不影响任务执行，但需要在生产日志感知到。
        """
        try:
            from services.websocket_manager import ws_manager
            await ws_manager.send_to_user(user_id, {
                "type": event_type,
                "data": data,
            })
        except Exception as e:
            logger.warning(f"_push_ws_event failed | event={event_type} | error={e}")

    async def execute(self, task: Dict[str, Any]) -> None:
        """执行单个定时任务（被 Scanner.poll 调用）"""
        run_id = await self._create_run(task)
        if run_id is None:
            # 无法记录执行历史 → 放弃执行（防止 update WHERE id 全部静默失效）
            logger.error(
                f"ScheduledTask aborted: cannot create run record | "
                f"task={task['id']}"
            )
            return

        result = None
        agent_run_started_at = datetime.now(timezone.utc)

        # 推送"开始执行"事件
        await self._push_ws_event(task["user_id"], "scheduled_task_started", {
            "task_id": task["id"],
            "task_name": task["name"],
            "run_id": run_id,
        })

        credit_handle = None
        push_status = "skipped"
        try:
            # 1. 用 credit_lock 上下文管理器锁定积分（支持按量计费）
            from services.credit_service import CreditService
            credit_svc = CreditService(self.db, redis=None)
            async with credit_svc.credit_lock(
                task_id=run_id,
                user_id=task["user_id"],
                amount=task["max_credits"],
                reason=f"定时任务: {task['name']}",
                org_id=task["org_id"],
            ) as credit_handle:
                # 2. 跑 Agent
                from services.agent.scheduled_task_agent import ScheduledTaskAgent
                agent = ScheduledTaskAgent(self.db, task)
                result = await agent.execute()

                if result.status in ("error", "timeout"):
                    raise RuntimeError(
                        f"Agent 执行失败: {result.text or result.error_message}"
                    )

                # 3. 按量计费：用 token 换算实际积分
                actual_credits = self._calc_actual_credits(
                    result.tokens_used, task
                )
                credit_handle.set_actual(actual_credits)

                # 4. 推送
                push_status = await self._push_result(task, result)

            # 5. 成功收尾（在 credit_lock 之后，退回已完成，可读取最终状态）
            await self._on_success(
                task, run_id, result, push_status,
                agent_run_started_at, credit_handle.final_credits_used,
            )

        except Exception as e:
            # credit_lock 会自动 refund
            await self._on_failure(task, run_id, e, result, agent_run_started_at)

    # ════════════════════════════════════════════════════════
    # 内部方法
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _calc_actual_credits(tokens_used: int, task: Dict[str, Any]) -> int:
        """根据实际 token 消耗换算积分

        直接使用 DASHSCOPE_PRICING 定价表计算，不构造 adapter 实例。
        Agent 场景 input >> output，按 70/30 比例分配。
        保底 1 积分，上限 max_credits。
        """
        if tokens_used <= 0:
            return 1

        try:
            from decimal import Decimal
            from services.adapters.dashscope.chat_adapter import DASHSCOPE_PRICING
            from core.config import get_settings

            settings = get_settings()
            model_id = getattr(settings, "agent_loop_model", None) or "qwen3.5-plus"
            pricing = DASHSCOPE_PRICING.get(model_id)

            if pricing:
                # Agent 场景 input 占 70%，output 占 30%
                input_tokens = int(tokens_used * 0.7)
                output_tokens = tokens_used - input_tokens
                input_credits = int(
                    Decimal(input_tokens) * pricing.credits_per_1m_input / 1_000_000
                )
                output_credits = int(
                    Decimal(output_tokens) * pricing.credits_per_1m_output / 1_000_000
                )
                credits = max(1, input_credits + output_credits)
            else:
                credits = max(1, tokens_used // 5000)
        except Exception:
            # 兜底：每 5000 token = 1 积分，最低 1
            credits = max(1, tokens_used // 5000)

        max_credits = task.get("max_credits", 10)
        return min(credits, max_credits)

    async def _create_run(self, task: Dict[str, Any]) -> Optional[str]:
        """创建执行记录

        Returns:
            run_id 字符串 / None（DB 写入失败）

        失败时返回 None 让调用方放弃执行，避免 _on_success/_on_failure
        的 update WHERE id 全部静默失效。
        """
        run_id = str(uuid4())
        try:
            self.db.table("scheduled_task_runs").insert({
                "id": run_id,
                "task_id": task["id"],
                "org_id": task["org_id"],
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            return run_id
        except Exception as e:
            logger.error(f"_create_run failed | task={task['id']} | error={e}")
            return None

    async def _push_result(
        self, task: Dict[str, Any], result: Any
    ) -> str:
        """推送 Agent 执行结果到 push_target + 存入消息表

        流程：
        1. 通过 push_dispatcher 推送到企微（原有逻辑）
        2. 通过 MessageGateway 存入 messages 表 + 通知 Web（新增）

        Returns:
            'pushed' / 'push_failed' / 'skipped'
        """
        push_status = "skipped"
        try:
            from services.scheduler.push_dispatcher import push_dispatcher
            push_status = await push_dispatcher.dispatch(
                org_id=task["org_id"],
                target=task["push_target"],
                text=result.text,
                files=result.files,
            )
        except ImportError:
            logger.warning("push_dispatcher 未实现，跳过推送")
        except Exception as e:
            logger.error(f"_push_result failed | task={task['id']} | error={e}")
            push_status = "push_failed"

        # 存入 messages 表 + 通知 Web（企微已由上面推过，skip_wecom=True）
        try:
            from services.message_gateway import MessageGateway
            gateway = MessageGateway(self.db)
            await gateway.save_system_message(
                user_id=task["user_id"],
                org_id=task["org_id"],
                text=result.text,
                source="scheduled_task",
                skip_wecom=True,
            )
        except Exception as e:
            logger.warning(
                f"_push_result save_system_message failed | "
                f"task={task['id']} | error={e}"
            )

        return push_status

    async def _on_success(
        self,
        task: Dict[str, Any],
        run_id: str,
        result: Any,
        push_status: str,
        started_at: datetime,
        actual_credits: Optional[int] = None,
    ) -> None:
        """成功收尾：更新任务 + 记录日志 + WS 推送

        单次任务（schedule_type='once'）跑完后自动暂停，next_run_at=NULL，
        不会被 Scanner 再次领取。
        其他类型按 cron_expr 算下次执行时间。
        """
        credits_used = actual_credits if actual_credits is not None else task["max_credits"]
        now = datetime.now(timezone.utc)
        duration_ms = int((now - started_at).total_seconds() * 1000)

        is_once = task.get("schedule_type") == "once"
        if is_once:
            next_status = "paused"
            next_run = None
        else:
            next_status = "active"
            next_run = calc_next_run(
                task["cron_expr"], task.get("timezone") or "Asia/Shanghai"
            )

        try:
            self.db.table("scheduled_tasks").update({
                "status": next_status,
                "next_run_at": next_run.isoformat() if next_run else None,
                "last_run_at": now.isoformat(),
                "last_summary": result.summary,
                "last_result": {
                    "tokens": result.tokens_used,
                    "turns": result.turns_used,
                    "files": result.files,
                },
                "run_count": (task.get("run_count") or 0) + 1,
                "consecutive_failures": 0,
                "updated_at": now.isoformat(),
            }).eq("id", task["id"]).execute()
        except Exception as e:
            logger.error(f"_on_success update task failed | {e}")

        try:
            self.db.table("scheduled_task_runs").update({
                "status": "success",
                "result_summary": result.summary,
                "result_files": result.files,
                "push_status": push_status,
                "credits_used": credits_used,
                "tokens_used": result.tokens_used,
                "duration_ms": duration_ms,
                "finished_at": now.isoformat(),
            }).eq("id", run_id).execute()
        except Exception as e:
            logger.error(f"_on_success update run failed | {e}")

        # WebSocket 推送"完成"事件
        await self._push_ws_event(task["user_id"], "scheduled_task_completed", {
            "task_id": task["id"],
            "task_name": task["name"],
            "run_id": run_id,
            "status": "success",
            "summary": result.summary,
            "files": result.files,
            "duration_ms": duration_ms,
            "credits_used": credits_used,
            "next_run_at": next_run.isoformat() if next_run else None,
            "push_status": push_status,
        })

    async def _on_failure(
        self,
        task: Dict[str, Any],
        run_id: str,
        error: Exception,
        result: Optional[Any],
        started_at: datetime,
    ) -> None:
        """失败处理：重试 / 暂停（credit_lock 已自动 refund）"""
        consecutive = (task.get("consecutive_failures") or 0) + 1
        now = datetime.now(timezone.utc)
        duration_ms = int((now - started_at).total_seconds() * 1000)

        # 写失败日志
        try:
            self.db.table("scheduled_task_runs").update({
                "status": "failed",
                "error_message": str(error)[:500],
                "tokens_used": result.tokens_used if result else 0,
                "duration_ms": duration_ms,
                "finished_at": now.isoformat(),
            }).eq("id", run_id).execute()
        except Exception as e:
            logger.error(f"_on_failure update run failed | {e}")

        # 决定下一步：重试 / 暂停 / 恢复
        # retry_count 语义：每次失败时额外的快速重试次数（5 分钟后再试）
        # 用尽重试后，按 cron 正常时间继续；连续失败 3 次后强制暂停
        retry_count = task.get("retry_count") or 1
        attempts_used = consecutive - 1  # 第 N 次失败 = 已用 N-1 次重试
        update: Dict[str, Any] = {
            "consecutive_failures": consecutive,
            "updated_at": now.isoformat(),
        }

        # 强制暂停优先级最高（防止配置 retry_count 巨大导致永不暂停）
        pause_threshold = max(3, retry_count + 1)

        if consecutive >= pause_threshold:
            # 连续失败累计达到阈值 → 自动暂停 + 通知
            update["status"] = "error"
            logger.error(
                f"ScheduledTask auto-paused | task={task['id']} | "
                f"failures={consecutive} | threshold={pause_threshold}"
            )
            await self._notify_owner(
                task,
                f"⚠️ 定时任务「{task['name']}」连续失败 {consecutive} 次已自动暂停\n"
                f"最后错误: {str(error)[:200]}"
            )
        elif attempts_used < retry_count:
            # 还有重试机会 → 5 分钟后重试
            retry_at = now + timedelta(minutes=5)
            update["next_run_at"] = retry_at.isoformat()
            update["status"] = "active"
            logger.warning(
                f"ScheduledTask retry | task={task['id']} | "
                f"attempt={attempts_used + 1}/{retry_count}"
            )
        else:
            # 重试用完
            if task.get("schedule_type") == "once":
                # 单次任务失败后不再调度，直接暂停
                update["next_run_at"] = None
                update["status"] = "paused"
            else:
                # 周期任务按 cron 正常时间继续
                next_run = calc_next_run(
                    task["cron_expr"], task.get("timezone") or "Asia/Shanghai"
                )
                update["next_run_at"] = next_run.isoformat()
                update["status"] = "active"

        try:
            self.db.table("scheduled_tasks").update(update).eq("id", task["id"]).execute()
        except Exception as e:
            logger.error(f"_on_failure update task failed | {e}")

        # WebSocket 推送"失败"事件
        # will_retry: 任务下次仍会自动执行（不论是 5min 重试还是按 cron 正常时间）
        will_retry = update.get("status") == "active"
        await self._push_ws_event(task["user_id"], "scheduled_task_failed", {
            "task_id": task["id"],
            "task_name": task["name"],
            "run_id": run_id,
            "status": update.get("status", "active"),
            "error": str(error)[:500],
            "consecutive_failures": consecutive,
            "will_retry": will_retry,
            "duration_ms": duration_ms,
        })

    async def _notify_owner(self, task: Dict[str, Any], message: str) -> None:
        """失败通知任务创建者

        通过两个渠道：
        1. WebSocket 推到前端（如果用户在线）
        2. 企微消息推送（通过 push_dispatcher，让用户在企微也收到）
        """
        logger.warning(
            f"NOTIFY OWNER | task={task['id']} | user={task['user_id']} | msg={message}"
        )

        # 1. WS 推送
        await self._push_ws_event(task["user_id"], "scheduled_task_notification", {
            "task_id": task["id"],
            "task_name": task["name"],
            "level": "error",
            "message": message,
        })

        # 2. 通过 MessageGateway 存消息 + 推企微（统一入口）
        try:
            from services.message_gateway import MessageGateway
            gateway = MessageGateway(self.db)
            await gateway.save_system_message(
                user_id=task["user_id"],
                org_id=task["org_id"],
                text=message,
                source="task_failure_alert",
                skip_web=True,  # 上面 WS 已推过
            )
        except Exception as e:
            logger.debug(f"_notify_owner gateway failed | {e}")
