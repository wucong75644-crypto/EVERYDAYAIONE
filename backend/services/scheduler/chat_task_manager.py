"""聊天内定时任务管理器

通过 Agent 工具调用，在聊天中创建/查看/修改/暂停/恢复/删除定时任务。
返回结构化 FormPart / 文本结果，由前端渲染。

设计要点：
- create/update: 返回 FormPart 预填表单，用户确认后前端发 form_submit WS 事件
- list: 返回文本摘要
- pause/resume/delete: 直接执行，返回文本确认

设计文档: docs/document/TECH_定时任务心跳系统.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger

from services.scheduler.cron_utils import (
    calc_next_run,
    compose_cron,
    parse_cron_readable,
)
from services.scheduler.task_nl_parser import parse_task_nl


# ════════════════════════════════════════════════════════
# 工具返回类型（替代魔法字符串协议）
# ════════════════════════════════════════════════════════

@dataclass
class FormBlockResult:
    """工具返回表单块——chat_tool_mixin 用 isinstance 检测，
    直接作为 content_block_add 推送给前端。

    与 AgentResult 平级：AgentResult 用于子 Agent 结果，
    FormBlockResult 用于需要前端交互确认的结构化表单。
    """
    form: Dict[str, Any]
    llm_hint: str = ""  # 给 LLM 的简短提示（不展示给用户）


def _calc_once_run_at(time_str: str, tz: str = "Asia/Shanghai") -> datetime:
    """计算 once 类型的执行时间：今天该时刻，若已过则明天。

    用 timedelta(days=1) 而非 replace(day=day+1)，避免月末溢出。
    """
    local_tz = ZoneInfo(tz)
    now = datetime.now(local_tz)
    hh, mm = time_str.split(":")
    run_dt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if run_dt <= now:
        run_dt = run_dt + timedelta(days=1)
    return run_dt


# ════════════════════════════════════════════════════════
# FormPart 构建工具
# ════════════════════════════════════════════════════════

_SCHEDULE_OPTIONS = [
    {"label": "仅一次", "value": "once"},
    {"label": "每天", "value": "daily"},
    {"label": "每周", "value": "weekly"},
    {"label": "每月", "value": "monthly"},
]

_WEEKDAY_OPTIONS = [
    {"label": "周一", "value": "1"},
    {"label": "周二", "value": "2"},
    {"label": "周三", "value": "3"},
    {"label": "周四", "value": "4"},
    {"label": "周五", "value": "5"},
    {"label": "周六", "value": "6"},
    {"label": "周日", "value": "0"},
]

def _build_form_field(
    name: str,
    field_type: str,
    label: str,
    *,
    required: bool = False,
    default_value: Any = None,
    placeholder: str = "",
    options: Optional[List[Dict[str, str]]] = None,
    visible_when: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建单个表单字段"""
    field: Dict[str, Any] = {
        "type": field_type,
        "name": name,
        "label": label,
        "required": required,
    }
    if default_value is not None:
        field["default_value"] = default_value
    if placeholder:
        field["placeholder"] = placeholder
    if options:
        field["options"] = options
    if visible_when:
        field["visible_when"] = visible_when
    return field


async def _load_push_targets(db: Any, user_id: str, org_id: str) -> List[Dict[str, str]]:
    """加载可用推送目标（Web自己 + 企微群/人）"""
    targets: List[Dict[str, str]] = [
        {"label": "推送给我（网页）", "value": json.dumps({"type": "web", "user_id": user_id})},
    ]

    # 企微个人通道
    try:
        mapping = db.table("wecom_user_mappings") \
            .select("wecom_userid, wecom_nickname") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        if mapping.data:
            m = mapping.data[0]
            nick = m.get("wecom_nickname", "")
            targets.append({
                "label": f"推送给我（企微 · {nick}）",
                "value": json.dumps({
                    "type": "wecom_user",
                    "wecom_userid": m["wecom_userid"],
                    "name": nick,
                }),
            })
    except Exception as e:
        logger.warning(f"load wecom_user_mapping failed: {e}")

    # 企微群
    try:
        groups = db.table("wecom_chat_targets") \
            .select("chatid, chat_name") \
            .eq("is_active", True) \
            .eq("org_id", org_id) \
            .order("last_active", desc=True) \
            .limit(20) \
            .execute()
        for g in (groups.data or []):
            name = g.get("chat_name") or g["chatid"][:8]
            targets.append({
                "label": f"企微群 · {name}",
                "value": json.dumps({
                    "type": "wecom_group",
                    "chatid": g["chatid"],
                    "chat_name": name,
                }),
            })
    except Exception as e:
        logger.warning(f"load wecom_chat_targets failed: {e}")

    # 企业同事（网页 + 企微双通道）
    try:
        members = db.table("org_members") \
            .select("user_id") \
            .eq("org_id", org_id) \
            .eq("status", "active") \
            .neq("user_id", user_id) \
            .limit(50) \
            .execute()
        if members.data:
            member_ids = [m["user_id"] for m in members.data]
            # 查昵称
            users = db.table("users") \
                .select("id, nickname") \
                .in_("id", member_ids) \
                .execute()
            nick_map = {u["id"]: u.get("nickname", "") for u in (users.data or [])}
            # 查企微映射
            wecom_mappings = db.table("wecom_user_mappings") \
                .select("user_id, wecom_userid, wecom_nickname") \
                .in_("user_id", member_ids) \
                .execute()
            wecom_map = {m["user_id"]: m for m in (wecom_mappings.data or [])}

            for uid in member_ids:
                nick = nick_map.get(uid, "")
                if not nick:
                    continue
                # 网页通道
                targets.append({
                    "label": f"同事 · {nick}（网页）",
                    "value": json.dumps({
                        "type": "web",
                        "user_id": uid,
                        "name": nick,
                    }),
                })
                # 企微通道
                wm = wecom_map.get(uid)
                if wm:
                    targets.append({
                        "label": f"同事 · {nick}（企微）",
                        "value": json.dumps({
                            "type": "wecom_user",
                            "wecom_userid": wm["wecom_userid"],
                            "name": nick,
                        }),
                    })
    except Exception as e:
        logger.warning(f"load org_members failed: {e}")

    return targets


def _build_create_form(
    parsed: Dict[str, Any],
    push_targets: List[Dict[str, str]],
) -> Dict[str, Any]:
    """构建创建定时任务的 FormPart"""
    # 默认推送目标（第一个 = 推送给我网页）
    default_push = push_targets[0]["value"] if push_targets else ""

    # 预填周几
    default_weekdays = parsed.get("weekdays", [1, 2, 3, 4, 5])

    fields = [
        _build_form_field(
            "name", "text", "任务名称",
            required=True,
            default_value=parsed.get("name", ""),
            placeholder="如：每日销售日报",
        ),
        _build_form_field(
            "prompt", "textarea", "执行内容",
            required=True,
            default_value=parsed.get("prompt", ""),
            placeholder="AI 每次执行时的任务指令",
        ),
        _build_form_field(
            "schedule_type", "select", "执行频率",
            required=True,
            default_value=parsed.get("schedule_type", "daily"),
            options=_SCHEDULE_OPTIONS,
        ),
        _build_form_field(
            "time_str", "time", "执行时间",
            required=True,
            default_value=parsed.get("time_str", "09:00"),
        ),
        _build_form_field(
            "weekdays", "checkbox_group", "每周几",
            default_value=default_weekdays,
            options=_WEEKDAY_OPTIONS,
            visible_when={"field": "schedule_type", "value": "weekly"},
        ),
        _build_form_field(
            "day_of_month", "number", "每月几号",
            default_value=parsed.get("day_of_month", 1),
            placeholder="1-31",
            visible_when={"field": "schedule_type", "value": "monthly"},
        ),
        _build_form_field(
            "push_target", "select", "推送到",
            required=True,
            default_value=default_push,
            options=push_targets,
        ),
    ]

    return {
        "type": "form",
        "form_type": "scheduled_task_create",
        "form_id": f"task_create_{uuid4().hex[:8]}",
        "title": "创建定时任务",
        "description": "请确认以下信息，点击确认后将创建定时任务。",
        "fields": fields,
        "submit_text": "确认创建",
        "cancel_text": "取消",
    }


def _build_update_form(
    task: Dict[str, Any],
    changes: Dict[str, Any],
    push_targets: List[Dict[str, str]],
) -> Dict[str, Any]:
    """构建修改定时任务的 FormPart（当前值预填）"""
    # 合并：changes 覆盖 task 原值
    merged = {**task, **changes}

    # 推送目标当前值
    current_push = json.dumps(task.get("push_target", {}))

    # 解析当前 cron 的 time_str
    cron_expr = merged.get("cron_expr", "")
    time_str = "09:00"
    if cron_expr:
        parts = cron_expr.split()
        if len(parts) >= 2:
            try:
                time_str = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
            except ValueError:
                pass

    fields = [
        _build_form_field(
            "task_id", "hidden", "",
            default_value=task["id"],
        ),
        _build_form_field(
            "name", "text", "任务名称",
            required=True,
            default_value=merged.get("name", ""),
        ),
        _build_form_field(
            "prompt", "textarea", "执行内容",
            required=True,
            default_value=merged.get("prompt", ""),
        ),
        _build_form_field(
            "schedule_type", "select", "执行频率",
            required=True,
            default_value=merged.get("schedule_type", "daily"),
            options=_SCHEDULE_OPTIONS,
        ),
        _build_form_field(
            "time_str", "time", "执行时间",
            required=True,
            default_value=changes.get("time_str", time_str),
        ),
        _build_form_field(
            "weekdays", "checkbox_group", "每周几",
            default_value=merged.get("weekdays", [1, 2, 3, 4, 5]),
            options=_WEEKDAY_OPTIONS,
            visible_when={"field": "schedule_type", "value": "weekly"},
        ),
        _build_form_field(
            "day_of_month", "number", "每月几号",
            default_value=merged.get("day_of_month", 1),
            placeholder="1-31",
            visible_when={"field": "schedule_type", "value": "monthly"},
        ),
        _build_form_field(
            "push_target", "select", "推送到",
            required=True,
            default_value=current_push,
            options=push_targets,
        ),
    ]

    return {
        "type": "form",
        "form_type": "scheduled_task_update",
        "form_id": f"task_update_{uuid4().hex[:8]}",
        "title": f"修改定时任务「{task.get('name', '')}」",
        "description": "修改后点击确认保存。",
        "fields": fields,
        "submit_text": "确认修改",
        "cancel_text": "取消",
    }


# ════════════════════════════════════════════════════════
# 核心管理器
# ════════════════════════════════════════════════════════

class ChatTaskManager:
    """聊天内定时任务管理器"""

    def __init__(self, db: Any, user_id: str, org_id: str) -> None:
        self.db = db
        self.user_id = user_id
        self.org_id = org_id

    async def handle(self, action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """统一入口：根据 action 分发"""
        handlers = {
            "create": self._handle_create,
            "list": self._handle_list,
            "update": self._handle_update,
            "pause": self._handle_pause,
            "resume": self._handle_resume,
            "delete": self._handle_delete,
        }
        handler = handlers.get(action)
        if not handler:
            return {"type": "text", "text": f"不支持的操作: {action}"}
        return await handler(args)

    async def _handle_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """创建：NL 解析 → 返回表单"""
        description = args.get("description", "").strip()
        if not description:
            return {"type": "text", "text": "请描述你想创建的定时任务，例如：每天早上9点推销售日报"}

        # NL 解析
        parsed = await parse_task_nl(description)
        logger.info(f"chat_task_manager create | parsed={parsed}")

        # 加载推送目标
        push_targets = await _load_push_targets(self.db, self.user_id, self.org_id)

        # 返回表单
        form = _build_create_form(parsed, push_targets)
        return form

    async def _handle_list(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        """列出当前用户的定时任务"""
        result = self.db.table("scheduled_tasks") \
            .select("id, name, status, schedule_type, cron_expr, next_run_at, run_count") \
            .eq("user_id", self.user_id) \
            .eq("org_id", self.org_id) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()

        tasks = result.data or []
        if not tasks:
            return {"type": "text", "text": "你还没有定时任务。可以说「每天早上9点推销售日报」来创建一个。"}

        lines = ["**你的定时任务：**\n"]
        status_map = {"active": "✅ 运行中", "paused": "⏸ 已暂停", "completed": "✔ 已完成"}
        for t in tasks:
            status = status_map.get(t["status"], t["status"])
            schedule = parse_cron_readable(t["cron_expr"]) if t.get("cron_expr") else t.get("schedule_type", "")
            lines.append(
                f"- **{t['name']}** — {schedule} | {status} | "
                f"已执行 {t.get('run_count', 0)} 次 | ID: `{t['id'][:8]}`"
            )

        lines.append("\n要修改或暂停某个任务，告诉我任务名称即可。")
        return {"type": "text", "text": "\n".join(lines)}

    async def _handle_update(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """修改：查任务 → 返回预填表单"""
        task_id = args.get("task_id", "").strip()
        task_name = args.get("task_name", "").strip()

        task = await self._find_task(task_id=task_id, task_name=task_name)
        if not task:
            return {"type": "text", "text": "未找到该任务，请确认任务名称或 ID。"}

        # 从 description 解析变更意图
        description = args.get("description", "")
        changes: Dict[str, Any] = {}
        if description:
            parsed = await parse_task_nl(description)
            # 只取有意义的变更
            for key in ("schedule_type", "time_str", "weekdays", "day_of_month", "name", "prompt"):
                if key in parsed and parsed[key]:
                    changes[key] = parsed[key]

        push_targets = await _load_push_targets(self.db, self.user_id, self.org_id)
        form = _build_update_form(task, changes, push_targets)
        return form

    async def _handle_pause(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """暂停任务"""
        task = await self._find_task(
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
        if not task:
            return {"type": "text", "text": "未找到该任务。"}
        if task["status"] == "paused":
            return {"type": "text", "text": f"任务「{task['name']}」已经是暂停状态。"}

        self.db.table("scheduled_tasks") \
            .update({"status": "paused"}) \
            .eq("id", task["id"]) \
            .execute()
        return {"type": "text", "text": f"⏸ 已暂停任务「{task['name']}」。说「恢复 {task['name']}」可以重新启动。"}

    async def _handle_resume(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """恢复任务"""
        task = await self._find_task(
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
        if not task:
            return {"type": "text", "text": "未找到该任务。"}
        if task["status"] == "active":
            return {"type": "text", "text": f"任务「{task['name']}」已经在运行中。"}

        # 重新计算 next_run
        update: Dict[str, Any] = {"status": "active"}
        if task.get("cron_expr"):
            tz = task.get("timezone", "Asia/Shanghai")
            next_run = calc_next_run(task["cron_expr"], tz)
            update["next_run_at"] = next_run.isoformat()

        self.db.table("scheduled_tasks") \
            .update(update) \
            .eq("id", task["id"]) \
            .execute()
        return {"type": "text", "text": f"▶️ 已恢复任务「{task['name']}」。"}

    async def _handle_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """删除任务"""
        task = await self._find_task(
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
        if not task:
            return {"type": "text", "text": "未找到该任务。"}

        self.db.table("scheduled_tasks") \
            .delete() \
            .eq("id", task["id"]) \
            .execute()
        return {"type": "text", "text": f"🗑 已删除任务「{task['name']}」。"}

    async def _find_task(
        self, task_id: str = "", task_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """按 ID 前缀或名称模糊查找任务"""
        if task_id:
            # 支持短 ID（前 8 位）
            result = self.db.table("scheduled_tasks") \
                .select("*") \
                .eq("user_id", self.user_id) \
                .eq("org_id", self.org_id) \
                .execute()
            for t in (result.data or []):
                if t["id"].startswith(task_id):
                    return t

        if task_name:
            result = self.db.table("scheduled_tasks") \
                .select("*") \
                .eq("user_id", self.user_id) \
                .eq("org_id", self.org_id) \
                .ilike("name", f"%{task_name}%") \
                .limit(1) \
                .execute()
            if result.data:
                return result.data[0]

        return None


# ════════════════════════════════════════════════════════
# 表单提交处理（form_submit WS 事件调用）
# ════════════════════════════════════════════════════════

async def handle_form_submit(
    db: Any,
    user_id: str,
    org_id: str,
    form_type: str,
    form_data: Dict[str, Any],
) -> Dict[str, Any]:
    """处理前端表单提交，返回结果文本

    权限在此处统一校验（而非调用方），确保任何入口（REST / WS）
    都不会绕过权限检查。

    Args:
        db: 数据库连接
        user_id: 当前用户 ID
        org_id: 企业 ID
        form_type: 表单类型（scheduled_task_create / scheduled_task_update）
        form_data: 表单字段值

    Returns:
        {"success": bool, "message": str}
    """
    from services.permissions.checker import check_permission

    if form_type == "scheduled_task_create":
        if not await check_permission(db, user_id, org_id, "task.create"):
            return {"success": False, "message": "无权创建定时任务"}
        return await _submit_create(db, user_id, org_id, form_data)

    if form_type == "scheduled_task_update":
        if not await check_permission(db, user_id, org_id, "task.edit"):
            return {"success": False, "message": "无权修改定时任务"}
        return await _submit_update(db, user_id, org_id, form_data)

    return {"success": False, "message": f"未知表单类型: {form_type}"}


async def _submit_create(
    db: Any, user_id: str, org_id: str, data: Dict[str, Any],
) -> Dict[str, Any]:
    """创建定时任务（表单提交后调用）"""
    name = (data.get("name") or "").strip()
    prompt = (data.get("prompt") or "").strip()
    schedule_type = data.get("schedule_type", "daily")
    time_str = data.get("time_str", "09:00")
    weekdays = data.get("weekdays")
    day_of_month = data.get("day_of_month")
    push_target_str = data.get("push_target", "")
    tz = "Asia/Shanghai"

    if not name:
        return {"success": False, "message": "任务名称不能为空"}
    if not prompt:
        return {"success": False, "message": "执行内容不能为空"}

    # 解析推送目标
    try:
        push_target = json.loads(push_target_str) if isinstance(push_target_str, str) else push_target_str
    except json.JSONDecodeError:
        return {"success": False, "message": "推送目标格式无效"}

    # 组装 cron
    cron_expr = None
    run_at = None
    next_run_at = None

    if schedule_type == "once":
        run_dt = _calc_once_run_at(time_str, tz)
        run_at = run_dt.isoformat()
        next_run_at = run_dt.astimezone(timezone.utc).isoformat()
    else:
        try:
            cron_expr = compose_cron(
                schedule_type=schedule_type,
                time_str=time_str,
                weekdays=[int(w) for w in weekdays] if weekdays else None,
                day_of_month=int(day_of_month) if day_of_month else None,
            )
        except ValueError as e:
            return {"success": False, "message": f"频率配置错误: {e}"}

        try:
            next_run = calc_next_run(cron_expr, tz)
            next_run_at = next_run.isoformat()
        except Exception as e:
            return {"success": False, "message": f"计算执行时间失败: {e}"}

    # 写入 DB
    task_id = str(uuid4())
    row = {
        "id": task_id,
        "org_id": org_id,
        "user_id": user_id,
        "name": name,
        "prompt": prompt,
        "cron_expr": cron_expr,
        "schedule_type": schedule_type,
        "weekdays": [int(w) for w in weekdays] if weekdays and schedule_type == "weekly" else None,
        "day_of_month": int(day_of_month) if day_of_month and schedule_type == "monthly" else None,
        "run_at": run_at,
        "timezone": tz,
        "push_target": push_target,
        "template_file": None,
        "status": "active",
        "max_credits": 10,
        "retry_count": 1,
        "timeout_sec": 180,
        "next_run_at": next_run_at,
        "run_count": 0,
        "consecutive_failures": 0,
    }
    db.table("scheduled_tasks").insert(row).execute()

    schedule_desc = parse_cron_readable(cron_expr) if cron_expr else "单次执行"
    logger.info(f"chat_task_manager created | id={task_id} | name={name} | schedule={schedule_desc}")

    return {
        "success": True,
        "message": f"✅ 已创建定时任务「{name}」\n- 频率：{schedule_desc}\n- 执行时间：{time_str}\n- 推送到：{push_target.get('type', 'web')}",
    }


async def _submit_update(
    db: Any, user_id: str, org_id: str, data: Dict[str, Any],
) -> Dict[str, Any]:
    """修改定时任务（表单提交后调用）"""
    task_id = data.get("task_id", "").strip()
    if not task_id:
        return {"success": False, "message": "缺少任务 ID"}

    # 查任务
    result = db.table("scheduled_tasks") \
        .select("*") \
        .eq("id", task_id) \
        .eq("user_id", user_id) \
        .eq("org_id", org_id) \
        .execute()
    if not result.data:
        return {"success": False, "message": "任务不存在或无权修改"}

    name = (data.get("name") or "").strip()
    prompt = (data.get("prompt") or "").strip()
    schedule_type = data.get("schedule_type", "daily")
    time_str = data.get("time_str", "09:00")
    weekdays = data.get("weekdays")
    day_of_month = data.get("day_of_month")
    push_target_str = data.get("push_target", "")
    tz = "Asia/Shanghai"

    try:
        push_target = json.loads(push_target_str) if isinstance(push_target_str, str) else push_target_str
    except json.JSONDecodeError:
        return {"success": False, "message": "推送目标格式无效"}

    update: Dict[str, Any] = {}
    if name:
        update["name"] = name
    if prompt:
        update["prompt"] = prompt
    if push_target:
        update["push_target"] = push_target
    update["schedule_type"] = schedule_type

    if schedule_type == "once":
        run_dt = _calc_once_run_at(time_str, tz)
        update["run_at"] = run_dt.isoformat()
        update["next_run_at"] = run_dt.astimezone(timezone.utc).isoformat()
        update["cron_expr"] = None
    else:
        try:
            cron_expr = compose_cron(
                schedule_type=schedule_type,
                time_str=time_str,
                weekdays=[int(w) for w in weekdays] if weekdays else None,
                day_of_month=int(day_of_month) if day_of_month else None,
            )
        except ValueError as e:
            return {"success": False, "message": f"频率配置错误: {e}"}

        update["cron_expr"] = cron_expr
        update["weekdays"] = [int(w) for w in weekdays] if weekdays and schedule_type == "weekly" else None
        update["day_of_month"] = int(day_of_month) if day_of_month and schedule_type == "monthly" else None
        update["run_at"] = None
        try:
            next_run = calc_next_run(cron_expr, tz)
            update["next_run_at"] = next_run.isoformat()
        except Exception as e:
            return {"success": False, "message": f"计算执行时间失败: {e}"}

    db.table("scheduled_tasks").update(update).eq("id", task_id).execute()

    logger.info(f"chat_task_manager updated | id={task_id} | fields={list(update.keys())}")
    return {
        "success": True,
        "message": f"✅ 已更新任务「{name or task_id[:8]}」",
    }
