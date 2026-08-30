"""定时任务执行编排器

职责：
1. 创建执行记录
2. 用 credit_lock 锁定积分
3. 调用 ScheduledTaskAgent 执行
4. 原子写入执行成功和企微 Outbox
5. 更新任务状态 + 写日志
6. 失败处理（重试/暂停/通知）
7. WebSocket 推送任务状态变化到前端

设计文档: docs/document/TECH_定时任务心跳系统.md §4.3.3
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional
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
                agent = ScheduledTaskAgent(
                    self.db, task, execution_mode="scheduled",
                )
                result = await agent.execute()

                if result.status != "success":
                    raise RuntimeError(
                        f"Agent 执行失败: {result.error_message or result.text}"
                    )

                # 3. 按量计费：用 token 换算实际积分
                actual_credits = self._calc_actual_credits(
                    result.tokens_used, task
                )
                credit_handle.set_actual(actual_credits)

                # 4. 原子提交任务成功状态与企微待投递事实。事务失败时不把
                # Redis 发布或内存状态伪装成成功，交由失败路径恢复任务。
                await self._on_success(
                    task, run_id, result, agent_run_started_at, actual_credits,
                )

            # 5. Web 仅是在线通知；企微可靠投递已在上面的 DB 事务中入队。
            try:
                await self._record_execution_trace(task, run_id, result)
            except Exception as trace_error:
                logger.warning(
                    f"scheduled_task_trace_record_failed | run={run_id} | error={trace_error}"
                )
            await self._save_result_message(task, result)
            await self._push_web_result(task, result)

        except Exception as e:
            # credit_lock 会自动 refund
            if result is not None:
                try:
                    await self._record_execution_trace(task, run_id, result)
                except Exception as trace_error:
                    logger.warning(
                        f"scheduled_task_trace_record_failed | run={run_id} | error={trace_error}"
                    )
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
                "execution_id": run_id,
                "plan_snapshot": task.get("plan_snapshot"),
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            return run_id
        except Exception as e:
            logger.error(f"_create_run failed | task={task['id']} | error={e}")
            return None

    async def _record_execution_trace(self, task: Dict[str, Any], run_id: str, result: Any) -> None:
        """把实际运行的计划、工具轨迹和 Completion Gate 持久化给历史界面。"""
        from services.scheduler.scheduled_task_workflow import _trace_rows

        gate = getattr(result, "completion_gate", {}) or {}
        rows = _trace_rows(
            org_id=task["org_id"], execution_kind="run", execution_id=run_id,
            task_id=task["id"], plan=task.get("plan_snapshot") or {},
            result=result, gate=gate,
        )
        if rows:
            self.db.table("scheduled_task_execution_events").insert(rows).execute()
        self.db.table("scheduled_task_runs").update({
            "completion_gate": gate,
        }).eq("id", run_id).execute()

    async def _save_result_message(self, task: Dict[str, Any], result: Any) -> None:
        """保存任务结果供 Web 历史读取；不能重新触发企微直推。"""
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
                f"_save_result_message failed | "
                f"task={task['id']} | error={e}"
            )

    async def _push_web_result(self, task: Dict[str, Any], result: Any) -> None:
        """Web 是临时在线通知，不改变企微 Outbox 的投递状态。"""
        try:
            from services.websocket_manager import ws_manager
            for target in self._iter_targets(task.get("push_target")):
                if target.get("type") != "web" or not target.get("user_id"):
                    continue
                await ws_manager.send_to_user(
                    str(target["user_id"]),
                    {
                        "type": "scheduled_task_result",
                        "data": {"text": result.text, "files": result.files},
                    },
                    org_id=task["org_id"],
                )
        except Exception as error:
            logger.warning(
                f"_push_web_result failed | task={task['id']} | error={error}"
            )

    @staticmethod
    def _iter_targets(target: Any) -> Iterable[Dict[str, Any]]:
        if not isinstance(target, dict):
            return
        if target.get("type") == "multi":
            children = target.get("targets")
            if not isinstance(children, list):
                return
            for child in children:
                yield from ScheduledTaskExecutor._iter_targets(child)
            return
        yield target

    @classmethod
    def _build_wecom_deliveries(
        cls, task: Dict[str, Any], result: Any,
    ) -> list[Dict[str, Any]]:
        """把任务定义冻结为可重试的、去重的企微投递快照。"""
        deliveries: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for target in cls._iter_targets(task.get("push_target")):
            target_type = target.get("type")
            if target_type not in {"wecom_user", "wecom_group"}:
                continue
            chatid = target.get("chatid") or target.get("wecom_userid")
            if not chatid:
                logger.warning(
                    "scheduled_task_delivery_target_skipped | "
                    f"task={task['id']} | reason=chatid_missing"
                )
                continue
            context = {"type": target_type, "chatid": str(chatid)}
            canonical = json.dumps(context, ensure_ascii=False, sort_keys=True)
            delivery_key = "result:" + hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            if delivery_key in seen:
                continue
            seen.add(delivery_key)
            deliveries.append({
                "delivery_key": delivery_key,
                "delivery_kind": "result",
                "target_context": context,
                "payload": {
                    "text": str(result.text or ""),
                    "files": result.files if isinstance(result.files, list) else [],
                },
            })
        return deliveries

    async def _on_success(
        self,
        task: Dict[str, Any],
        run_id: str,
        result: Any,
        started_at: datetime,
        actual_credits: Optional[int] = None,
    ) -> str:
        """原子写入任务成功状态、运行结果与企微待投递记录。

        调度结果先持久化再由独立 Worker 发送，消除“Redis publish 即成功”的
        假象。数据库 RPC 失败必须上抛，避免把无投递事实的执行标记成功。
        """
        credits_used = actual_credits if actual_credits is not None else task["max_credits"]
        now = datetime.now(timezone.utc)
        duration_ms = int((now - started_at).total_seconds() * 1000)

        # 重新读 DB 获取最新 cron_expr / schedule_type，
        # 防止执行期间用户修改了定时配置，完成后用旧 cron 覆盖新的 next_run_at
        try:
            fresh = self.db.table("scheduled_tasks") \
                .select("cron_expr, schedule_type, timezone") \
                .eq("id", task["id"]).execute()
            if fresh.data:
                live = fresh.data[0]
                cron_expr = live.get("cron_expr") or task.get("cron_expr")
                schedule_type = live.get("schedule_type") or task.get("schedule_type")
                tz = live.get("timezone") or task.get("timezone") or "Asia/Shanghai"
            else:
                cron_expr = task.get("cron_expr")
                schedule_type = task.get("schedule_type")
                tz = task.get("timezone") or "Asia/Shanghai"
        except Exception:
            cron_expr = task.get("cron_expr")
            schedule_type = task.get("schedule_type")
            tz = task.get("timezone") or "Asia/Shanghai"

        previous_status = task.get("_previous_status")
        if task.get("_manual_run") and previous_status in {"paused", "error"}:
            next_status = previous_status
            next_run = None
        elif schedule_type == "once":
            next_status = "paused"
            next_run = None
        else:
            next_status = "active"
            next_run = calc_next_run(cron_expr, tz)

        response = self.db.rpc("complete_scheduled_task_success", {
            "p_task_id": task["id"],
            "p_run_id": run_id,
            "p_next_status": next_status,
            "p_next_run_at": next_run.isoformat() if next_run else None,
            "p_last_summary": result.summary,
            "p_last_result": {
                "tokens": result.tokens_used,
                "turns": result.turns_used,
                "files": result.files,
            },
            "p_credits_used": credits_used,
            "p_tokens_used": result.tokens_used,
            "p_duration_ms": duration_ms,
            "p_deliveries": self._build_wecom_deliveries(task, result),
        }).execute()
        payload = response.data if response else None
        if not isinstance(payload, dict) or payload.get("outcome") != "completed":
            raise RuntimeError(
                "SCHEDULED_TASK_SUCCESS_COMMIT_FAILED:"
                f"{payload.get('outcome') if isinstance(payload, dict) else 'invalid'}"
            )
        push_status = str(payload.get("push_status") or "skipped")

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
        return push_status

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

        # 手动运行暂停/异常任务只产生一次运行记录，不得意外重新开启长期调度。
        previous_status = task.get("_previous_status")
        if task.get("_manual_run") and previous_status in {"paused", "error"}:
            update["status"] = previous_status
            update["next_run_at"] = None
        # 强制暂停优先级最高（防止配置 retry_count 巨大导致永不暂停）
        else:
            pause_threshold = max(3, retry_count + 1)

            if consecutive >= pause_threshold:
                # 连续失败累计达到阈值 → 自动暂停 + 通知
                update["status"] = "error"
                logger.error(
                    f"ScheduledTask auto-paused | task={task['id']} | "
                    f"failures={consecutive} | threshold={pause_threshold}"
                )
                await self._notify_owner(
                    task, run_id,
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
                # 重试用完 — 重新读 DB 获取最新 cron（用户可能在执行期间改了时间）
                try:
                    fresh = self.db.table("scheduled_tasks") \
                        .select("cron_expr, schedule_type, timezone") \
                        .eq("id", task["id"]).execute()
                    live = fresh.data[0] if fresh.data else {}
                except Exception:
                    live = {}
                live_schedule = live.get("schedule_type") or task.get("schedule_type")
                live_cron = live.get("cron_expr") or task.get("cron_expr")
                live_tz = live.get("timezone") or task.get("timezone") or "Asia/Shanghai"

                if live_schedule == "once":
                    # 单次任务失败后不再调度，直接暂停
                    update["next_run_at"] = None
                    update["status"] = "paused"
                else:
                    # 周期任务按最新 cron 正常时间继续
                    next_run = calc_next_run(live_cron, live_tz)
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

    async def _notify_owner(
        self, task: Dict[str, Any], run_id: str, message: str,
    ) -> None:
        """失败通知任务创建者

        通过两个渠道：
        1. WebSocket 推到前端（如果用户在线）
        2. 企微消息持久化入 Outbox，供 WS 恢复后重试
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

        # 2. 失败告警也不能走 Redis 直推，写入同一条可靠投递链。
        try:
            mapping = self.db.table("wecom_user_mappings") \
                .select("wecom_userid") \
                .eq("user_id", task["user_id"]) \
                .eq("org_id", task["org_id"]) \
                .limit(1).execute()
            if mapping.data:
                chatid = str(mapping.data[0]["wecom_userid"])
                key = "owner_alert:" + hashlib.sha256(
                    chatid.encode("utf-8")
                ).hexdigest()
                self.db.rpc("enqueue_scheduled_task_owner_alert", {
                    "p_task_id": task["id"],
                    "p_run_id": run_id,
                    "p_org_id": task["org_id"],
                    "p_delivery_key": key,
                    "p_target_context": {"type": "wecom_user", "chatid": chatid},
                    "p_payload": {"text": message, "files": []},
                }).execute()
        except Exception as error:
            logger.warning(
                f"_notify_owner enqueue failed | task={task['id']} | error={error}"
            )

        # 3. 保留系统消息与 Web 历史，但禁止 MessageGateway 再次走企微直推。
        try:
            from services.message_gateway import MessageGateway
            gateway = MessageGateway(self.db)
            await gateway.save_system_message(
                user_id=task["user_id"],
                org_id=task["org_id"],
                text=message,
                source="task_failure_alert",
                skip_web=True,  # 上面 WS 已推过
                skip_wecom=True,
            )
        except Exception as e:
            logger.debug(f"_notify_owner gateway failed | {e}")
