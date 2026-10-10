"""Page delivery orchestration over the existing planner, replay and credit ledger."""
import asyncio
import re
from uuid import uuid4

from loguru import logger

POLICY = {"version": 1, "retry_cost": "platform"}


def delivery_enabled(plan, project=None):
    return (plan.get("model_settings", {}).get("delivery_policy") == POLICY
        and not (project or {}).get("run_state", {}).get("delivery_stopped", False))


def latest_tasks(tasks):
    return {task["request_params"]["_media_request_v1"]["origin"]["plan_source"]["item_id"]: task
        for task in sorted(tasks, key=lambda t: (t["created_at"], str(t["id"])))}


def plan_blocked(plan):
    if plan["status"] in {"needs_input", "insufficient", "cancelled"}:
        return True
    code = plan.get("recovery_state", {}).get("last_error", {}).get("code", "")
    if code in {"ECOM_PLAN_INSUFFICIENT_CREDITS", "IMAGE_REFERENCE_CHANGED", "ANALYSIS_IMAGE_INVALID",
            "ANALYSIS_IMAGE_FORMAT", "ANALYSIS_IMAGE_DIMENSIONS", "ANALYSIS_IMAGE_TOO_LARGE",
            "ANALYSIS_IMAGE_ENCODING_TOO_LARGE", "ANALYSIS_IMAGE_TOTAL_TOO_LARGE", "RESOURCE_ACCESS_DENIED"}:
        return True
    attempts = plan.get("recovery_state", {}).get("attempts", {}).values()
    return any(a.get("usage", {}).get("finish_reason") in {"content_filter", "safety", "SAFETY"} for a in attempts)


def image_blocked(task):
    if task["status"] == "cancelled":
        return True
    error = task.get("error_message") or ""
    if (task.get('result') or {}).get('fail_code') in {'CONTENT_POLICY_VIOLATION','SAFETY','CONTENT_FILTER'}:
        return True
    return error in {"当前权限或积分不足，未提交供应商", "模型价格已变化，请重新确认",
        "参考原图超过模型允许大小", "参考原图文件大小信息无效，请重新选择图片"} or any(
        code in error for code in ("IMAGE_REFERENCE_CHANGED", "原图已变化", "原图已缺失", "无权读取", "无权访问"))


def safe_code(error, default="DETAIL_DELIVERY_TEMPORARY_ERROR"):
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,128}", code) else default


def job_blocked(plan, key, source):
    job = plan.get('delivery_recovery', {}).get(key, {})
    return job.get('source') == source and job.get('status') == 'blocked'


def plan_source(plan):
    window = plan.get('recovery_state', {}).get('window_task_id', 'initial')
    return f"{window}:{len(plan.get('stage_attempts', []))}:{plan['current_stage']}"


def recovery_projection(plan, project, tasks):
    if not delivery_enabled(plan, project):
        return None
    jobs = plan.get("delivery_recovery", {})
    def state(key, blocked):
        job = jobs.get(key, {})
        message = {'IMAGE_REFERENCE_CHANGED':'原始图片已变化，请重新上传后开始。',
            'DETAIL_PRICE_CHANGED':'图片费用已变化，请确认当前费用后重新开始。',
            'DETAIL_INSUFFICIENT_CREDITS':'积分不足，请补充积分后继续。',
            'DETAIL_IDENTITY_DENIED':'当前权限已变化，请核验后继续。'}.get(job.get('reason')) if blocked else None
        return {"status": "blocked" if blocked else "waiting", 'message':message,
            "attempts": job.get("attempts", 0), "next_retry_at": job.get("next_retry_at"),
            "platform_attention": job.get("attempts", 0) >= 5}
    items = {item: state("image:" + item, image_blocked(task) or job_blocked(plan, 'image:'+item, str(task['id'])))
        for item, task in latest_tasks(tasks).items() if task["status"] in {"failed", "cancelled"}}
    value = {"enabled": True, "status": "active", "items": items, "retry_cost": "platform"}
    if plan["status"] in {"failed", "needs_input", "insufficient", "cancelled"}:
        value.update(state("plan", plan_blocked(plan) or job_blocked(plan, 'plan', plan_source(plan))))
    elif plan.get("recovery_state", {}).get("acceptance_error"):
        value.update(state("acceptance", job_blocked(plan, 'acceptance', plan['recovery_state']['acceptance_error'].get('attempt_id'))))
    elif any(item["status"] == "waiting" for item in items.values()):
        value.update(status="waiting", platform_attention=any(item["platform_attention"] for item in items.values()))
    elif any(item['status']=='blocked' for item in items.values()):
        value.update(status='blocked')
    return value


class DetailDeliveryRecovery:
    def __init__(self, service):
        self.service = service

    async def rpc(self, name, **params):
        return await asyncio.to_thread(lambda: self.service.db.rpc(name, params).execute().data)

    async def retry(self, plan, key, source, reason, blocked, operation):
        token = str(uuid4())
        job = await self.rpc("claim_detail_delivery_retry", p_plan_id=plan["id"], p_key=key,
            p_source=source, p_token=token, p_reason=reason, p_blocked=blocked)
        if job.get("outcome") != "execute":
            return
        status, reason = "submitted", "DETAIL_DELIVERY_RETRY_SUBMITTED"
        try:
            await operation(token, job["request_id"])
        except asyncio.CancelledError:
            # Leave the durable lease and stable request id for the next worker.
            raise
        except Exception as error:
            reason = safe_code(error)
            text = str(error)
            if '原图已' in text or 'IMAGE_REFERENCE_CHANGED' in text: reason = 'IMAGE_REFERENCE_CHANGED'
            elif '模型价格已变化' in text: reason = 'DETAIL_PRICE_CHANGED'
            elif '积分不足' in text: reason = 'DETAIL_INSUFFICIENT_CREDITS'
            elif '无权' in text: reason = 'DETAIL_IDENTITY_DENIED'
            status = "blocked" if reason in {'IMAGE_REFERENCE_CHANGED', 'DETAIL_PRICE_CHANGED',
                'DETAIL_INSUFFICIENT_CREDITS', 'DETAIL_IDENTITY_DENIED'} else "waiting"
            logger.warning("detail_delivery_retry_pending | plan={} target={} attempt={} code={}",
                plan["id"], key, job["attempts"], reason)
        await self.rpc("finish_detail_delivery_retry", p_plan_id=plan["id"], p_key=key,
            p_token=token, p_status=status, p_reason=reason)

    async def advance(self, plan, project, tasks):
        if not delivery_enabled(plan, project):
            return
        if plan["status"] == "failed":
            source = plan_source(plan)
            async def resume(token, _request):
                await self.rpc("resume_detail_delivery_plan", p_plan_id=plan["id"], p_token=token)
            await self.retry(plan, "plan", source, plan.get("recovery_state", {}).get("last_error", {}).get(
                "code", "DETAIL_PLANNING_INTERRUPTED"), plan_blocked(plan) or job_blocked(plan, 'plan', source), resume)
        elif plan["status"] == "ready" and plan.get("recovery_state", {}).get("acceptance_error"):
            error = plan["recovery_state"]["acceptance_error"]
            async def accept(token, _request):
                await self.rpc('resume_detail_delivery_acceptance', p_plan_id=plan['id'], p_token=token)
            await self.retry(plan, "acceptance", error["attempt_id"], error["code"],
                error["code"] in {"IMAGE_REFERENCE_CHANGED", "DETAIL_SCOPE_DENIED"}
                    or job_blocked(plan, 'acceptance', error['attempt_id']), accept)
        if plan["status"] != "ready":
            return
        from services.handlers.chat_image_controls import ChatImageControls
        controls = ChatImageControls(self.service.db, self.service.user_id, self.service.org_id)
        for item, task in latest_tasks(tasks).items():
            if task["status"] not in {"failed", "cancelled"}:
                continue
            async def replay(token, request, task_id=task["id"]):
                await controls.replay(task_id, request, delivery_token=token)
            await self.retry(plan, "image:" + item, str(task["id"]), "DETAIL_IMAGE_ATTEMPT_FAILED",
                image_blocked(task) or job_blocked(plan, 'image:'+item, str(task['id'])), replay)
