"""聊天内定时任务管理器

通过 Agent 工具调用，在聊天中创建/查看/修改/暂停/恢复/删除定时任务。
返回结构化 FormPart / 文本结果，由前端渲染。

设计要点：
- create/update: 返回 FormPart 预填表单，提交后只创建 ChangeSet；确认由 ChangeSet 卡片完成
- list: 返回文本摘要
- pause/resume/delete: 生成 ChangeSet，等待用户确认

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
        "description": "步骤 1/4 · 尚未创建任务。提交后 AI 会规划调用路径并进行只读安全试跑；请在变更方案卡片中查看结果并确认。",
        "fields": fields,
        "submit_text": "规划并安全试跑",
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
        "description": "提交后将生成变更方案并进行只读试跑，确认前不会修改原任务。",
        "fields": fields,
        "submit_text": "规划并安全试跑",
        "cancel_text": "取消",
    }


# ════════════════════════════════════════════════════════
# 核心管理器
# ════════════════════════════════════════════════════════

class ChatTaskManager:
    """聊天内定时任务管理器"""

    def __init__(self, db: Any, user_id: str, org_id: str, *,
                 submission_mode: str = "proposal", idempotency_key: str | None = None) -> None:
        self.db = db
        self.user_id = user_id
        self.org_id = org_id
        self.submission_mode = submission_mode
        self.idempotency_key = idempotency_key

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
        from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeError
        try:
            return await handler(args)
        except ScheduledTaskChangeError as exc:
            return {"type": "text", "text": str(exc)}

    async def _handle_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """创建：NL 解析 → 返回表单"""
        description = args.get("description", "").strip()
        if not description:
            return {"type": "text", "text": "请描述你想创建的定时任务，例如：每天早上9点推销售日报"}

        if self.submission_mode == "apply_if_allowed":
            from services.scheduler.task_nl_parser import parse_task_request
            request = await parse_task_request(description)
            parsed = request["changes"]
            targets = await _load_push_targets(self.db, self.user_id, self.org_id)
            target = self._request_target(request["recipient"], targets)
            missing = list(request["missing_fields"])
            if target is None:
                missing.append("push_target")
            if not missing:
                return await self._begin_request("create", {**parsed, "push_target": target, "timezone": "Asia/Shanghai"})
            form = _build_create_form(parsed, targets)
            form.update({"title": "补充任务信息", "description": f"请核对执行内容并补齐安排。你的原始要求：{description}", "submit_text": "创建任务"})
            from services.scheduler.task_submission import unfilled_shop_placeholder
            if unfilled_shop_placeholder(description):
                form["description"] += "。请在执行内容中将店铺占位文字替换为实际店铺名称。"
            for field in form["fields"]:
                key = field["name"]
                if key == "push_target":
                    field["default_value"] = json.dumps(target) if target else ""
                elif key in parsed:
                    field["default_value"] = parsed[key]
                elif key == "prompt":
                    # The original remains in the form description; do not
                    # prefill an unparsed management request as executable work.
                    field["default_value"] = ""
                    field["placeholder"] = "请根据上方原始要求，补充每次需要执行的业务内容"
                else:
                    field["default_value"] = [] if key == "weekdays" else ""
                if key == "time_str":
                    field["visible_when"] = {"field": "schedule_type", "value": "once", "not": True}
                if key != "prompt" and key not in missing and (key in parsed or key == "push_target" and target):
                    field["type"] = "hidden"
            # A one-shot date must not be silently replaced with today/tomorrow.
            form["fields"].append(_build_form_field(
                "run_at", "hidden" if parsed.get("run_at") else "datetime-local", "执行日期和时间（北京时间）",
                required=True, default_value=parsed.get("run_at", ""),
                visible_when={"field": "schedule_type", "value": "once"},
            ))
            form["fields"].append(_build_form_field("_submission_mode", "hidden", "", default_value="apply_if_allowed"))
            return form

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
            .select("id, name, status, schedule_enabled, schedule_type, cron_expr, next_run_at, run_count") \
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
            if t["status"] == "running":
                status = "本次执行中 · 后续已暂停" if t.get("schedule_enabled") is False else "本次执行中"
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
        if task.get("_ambiguous"):
            return self._ambiguous_task_result(task)

        if self.submission_mode == "apply_if_allowed" and args.get("description"):
            from services.scheduler.task_nl_parser import parse_task_request
            request = await parse_task_request(args["description"], task.get("timezone") or "Asia/Shanghai", operation="update")
            changes = request["changes"]
            output_format = changes.pop("output_format", None)
            if output_format and "prompt" not in changes:
                changes["prompt"] = task["prompt"] + "\n输出格式：" + output_format
            if request["recipient"]:
                targets = await _load_push_targets(self.db, self.user_id, self.org_id)
                target = self._request_target(request["recipient"], targets)
                if target is None:
                    return {"type": "text", "text": "请明确要推送到哪个群或哪位同事，也可以在任务编辑页选择。"}
                changes["push_target"] = target
            if changes and not request["missing_fields"]:
                return await self._begin_request("update", changes, task)
            return {"type": "text", "text": "请补充要修改的具体内容，例如「改到每天上午十点」或「名称改为销售日报」。"}

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
        if self.submission_mode == "apply_if_allowed":
            form["fields"].append(_build_form_field("_submission_mode", "hidden", "", default_value="apply_if_allowed"))
            form.update({"description": "保存后检查并应用修改；涉及收件人、执行范围或用量增加时会请你确认。", "submit_text": "保存修改"})
        return form

    def _request_target(self, recipient: str, targets) -> Dict[str, Any] | None:
        if not recipient or recipient.strip() in {"我", "给我", "自己", "self", "网页"}:
            return {"type": "web", "user_id": self.user_id}
        matches = []
        for option in targets:
            target = json.loads(option["value"])
            name = target.get("chat_name") or target.get("name")
            if name and name in recipient:
                matches.append(target)
            elif "我" in recipient and "企微" in recipient and target.get("type") == "wecom_user" and option["label"].startswith("推送给我"):
                matches.append(target)
        return matches[0] if len(matches) == 1 else None

    async def _begin_request(self, operation: str, definition: Dict[str, Any], task=None):
        from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeSetService, ScheduledTaskChangeError
        from services.scheduler.task_submission import submission_receipt
        try:
            row = await ScheduledTaskChangeSetService(self.db, user_id=self.user_id, org_id=self.org_id).begin(
                operation=operation, proposed_snapshot=definition, base_snapshot=task,
                resource_id=task["id"] if task else None,
                submission_mode=self.submission_mode, idempotency_key=self.idempotency_key,
            )
        except ScheduledTaskChangeError as exc:
            return {"type": "text", "text": f"任务尚未生效：{exc}"}
        return {"type": "change_set", "data": row, "text": submission_receipt(row)}

    async def _handle_pause(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """暂停任务"""
        task = await self._find_task(
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
        if not task:
            return {"type": "text", "text": "未找到该任务。"}
        if task.get("_ambiguous"):
            return self._ambiguous_task_result(task)
        if task["status"] == "paused" or task.get("schedule_enabled") is False:
            return {"type": "text", "text": f"任务「{task['name']}」已经是暂停状态。"}
        return await self._propose_chat_change("pause", task)

    async def _handle_resume(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """恢复任务"""
        task = await self._find_task(
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
        if not task:
            return {"type": "text", "text": "未找到该任务。"}
        if task.get("_ambiguous"):
            return self._ambiguous_task_result(task)
        if task["status"] == "active" or task["status"] == "running" and task.get("schedule_enabled") is True:
            return {"type": "text", "text": f"任务「{task['name']}」已经在运行中。"}
        return await self._propose_chat_change("resume", task)

    async def _handle_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """删除任务"""
        task = await self._find_task(
            task_id=args.get("task_id", ""),
            task_name=args.get("task_name", ""),
        )
        if not task:
            return {"type": "text", "text": "未找到该任务。"}
        if task.get("_ambiguous"):
            return self._ambiguous_task_result(task)
        return await self._propose_chat_change("delete", task)

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
            rows = list(result.data or [])
            exact = [t for t in rows if t["id"] == task_id]
            matches = exact or [t for t in rows if t["id"].startswith(task_id)]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return {"_ambiguous": True, "candidates": matches}

        if task_name:
            result = self.db.table("scheduled_tasks") \
                .select("*") \
                .eq("user_id", self.user_id) \
                .eq("org_id", self.org_id) \
                .ilike("name", f"%{task_name}%") \
                .execute()
            rows = list(result.data or [])
            if len(rows) == 1:
                return rows[0]
            if len(rows) > 1:
                return {"_ambiguous": True, "candidates": rows}

        return None

    @staticmethod
    def _ambiguous_task_result(task: Dict[str, Any]) -> Dict[str, Any]:
        candidates = task.get("candidates") or []
        lines = ["找到多个同名或相似的定时任务，请先选择一个："]
        for item in candidates:
            lines.append(f"- {item.get('name', '未命名')}（ID: {item.get('id', '')}）")
        return {"type": "text", "text": "\n".join(lines)}

    async def _propose_chat_change(self, operation: str, task: Dict[str, Any]) -> Dict[str, Any]:
        """Propose or submit through the same ChangeSet checks and commit receipt."""
        from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeSetService

        proposed = dict(task)
        if operation == "pause":
            proposed.update({"status": "paused", "next_run_at": None})
        elif operation == "resume":
            if self.submission_mode == "apply_if_allowed":
                next_run = None  # Computed by the adapter after idempotent replay.
            elif task.get("schedule_type") == "once":
                next_run = task.get("run_at")
            else:
                next_run = calc_next_run(task["cron_expr"], task.get("timezone", "Asia/Shanghai")).isoformat()
            proposed.update({"status": "active", "next_run_at": next_run})
        row = await ScheduledTaskChangeSetService(
            self.db, user_id=self.user_id, org_id=self.org_id,
        ).begin(
            operation=operation, resource_id=task["id"], base_snapshot=task,
            proposed_snapshot=proposed,
            submission_mode=self.submission_mode, idempotency_key=self.idempotency_key,
        )
        from services.scheduler.task_submission import submission_receipt
        return {
            "type": "change_set", "data": row,
            "text": submission_receipt(row),
        }


# ════════════════════════════════════════════════════════
# 表单提交处理（form_submit WS 事件调用）
# ════════════════════════════════════════════════════════

async def handle_form_submit(
    db: Any,
    user_id: str,
    org_id: str,
    form_type: str,
    form_data: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
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
        if _direct_form(form_data):
            return await _submit_direct_form(db, user_id, org_id, "create", form_data, idempotency_key)
        return await _submit_create(db, user_id, org_id, form_data, idempotency_key=idempotency_key)

    if form_type == "scheduled_task_confirm":
        # 旧草稿不再是可提交的业务状态；新流程的确认只能通过 ChangeSet 卡片。
        return {
            "success": False,
            "status": "cancelled",
            "message": "该历史草稿已停止受理，请重新发起任务以生成新的变更方案。",
        }

    if form_type == "scheduled_task_update":
        if not await check_permission(db, user_id, org_id, "task.edit"):
            return {"success": False, "message": "无权修改定时任务"}
        if _direct_form(form_data):
            return await _submit_direct_form(db, user_id, org_id, "update", form_data, idempotency_key)
        return await _submit_update(db, user_id, org_id, form_data, idempotency_key=idempotency_key)

    return {"success": False, "message": f"未知表单类型: {form_type}"}


def _direct_form(data):
    from core.config import get_settings
    return data.get("_submission_mode") == "apply_if_allowed" and get_settings().scheduled_task_direct_enabled


async def _submit_direct_form(db, user_id, org_id, operation, data, idempotency_key):
    definition = {k: data[k] for k in ("name", "prompt", "schedule_type", "time_str", "weekdays", "day_of_month", "run_at") if k in data}
    task = None
    if operation == "update":
        task = await ChatTaskManager(db, user_id, org_id)._find_task(task_id=str(data.get("task_id") or ""))
        if not task or task.get("_ambiguous"):
            return {"success": False, "message": "任务不存在或无权修改"}
    try:
        target = data.get("push_target")
        definition["push_target"] = json.loads(target) if isinstance(target, str) else target
        definition["timezone"] = (task or {}).get("timezone") or "Asia/Shanghai"
        if definition.get("schedule_type") == "once":
            from zoneinfo import ZoneInfo
            dt = datetime.fromisoformat(str(definition.get("run_at") or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(definition["timezone"]))
            definition["run_at"] = dt.isoformat()
    except (ValueError, TypeError):
        return {"success": False, "message": "请补齐有效的执行日期、时间和推送目标"}
    return await _propose_form_change(
        db=db, user_id=user_id, org_id=org_id, operation=operation, definition=definition,
        task_id=task["id"] if task else None, base_snapshot=task, idempotency_key=idempotency_key,
        submission_mode="apply_if_allowed",
    )


async def _submit_create(
    db: Any, user_id: str, org_id: str, data: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    """把聊天创建表单转换为 ChangeSet，绝不写入 legacy draft。"""
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

    definition = {
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
        "max_credits": 10,
        "retry_count": 1,
        "timeout_sec": 180,
        "next_run_at": next_run_at,
    }
    return await _propose_form_change(
        db=db, user_id=user_id, org_id=org_id, operation="create",
        definition=definition, task_id=None, idempotency_key=idempotency_key,
    )


async def _submit_update(
    db: Any, user_id: str, org_id: str, data: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    """把聊天修改表单转换为 ChangeSet，原任务只由 ChangeSet 提交阶段修改。"""
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

    task = result.data[0]
    if not name or not prompt:
        return {"success": False, "message": "任务名称和执行内容不能为空"}
    if not isinstance(push_target, dict) or not push_target:
        return {"success": False, "message": "推送目标格式无效"}

    definition: Dict[str, Any] = {
        "name": name,
        "prompt": prompt,
        "push_target": push_target,
        "timezone": task.get("timezone") or tz,
        "template_file": task.get("template_file"),
        "max_credits": task.get("max_credits") or 10,
        "retry_count": task.get("retry_count") or 1,
        "timeout_sec": task.get("timeout_sec") or 180,
        "schedule_type": schedule_type,
    }

    if schedule_type == "once":
        run_dt = _calc_once_run_at(time_str, tz)
        definition["run_at"] = run_dt.isoformat()
        definition["next_run_at"] = run_dt.astimezone(timezone.utc).isoformat()
        definition["cron_expr"] = None
        definition["weekdays"] = None
        definition["day_of_month"] = None
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

        definition["cron_expr"] = cron_expr
        definition["weekdays"] = [int(w) for w in weekdays] if weekdays and schedule_type == "weekly" else None
        definition["day_of_month"] = int(day_of_month) if day_of_month and schedule_type == "monthly" else None
        definition["run_at"] = None
        try:
            next_run = calc_next_run(cron_expr, tz)
            definition["next_run_at"] = next_run.isoformat()
        except Exception as e:
            return {"success": False, "message": f"计算执行时间失败: {e}"}

    return await _propose_form_change(
        db=db, user_id=user_id, org_id=org_id, operation="update",
        definition=definition, task_id=task_id, base_snapshot=task,
        idempotency_key=idempotency_key,
    )


async def _propose_form_change(
    *,
    db: Any,
    user_id: str,
    org_id: str,
    operation: str,
    definition: Dict[str, Any],
    task_id: str | None,
    idempotency_key: str | None,
    base_snapshot: Dict[str, Any] | None = None,
    submission_mode: str = "proposal",
) -> Dict[str, Any]:
    """聊天表单进入统一 ChangeSet 服务；失败也以受控 ChangeSet 状态呈现。"""
    from services.scheduler.task_submission import submission_receipt
    from services.scheduler.scheduled_task_change_adapter import (
        ScheduledTaskChangeError,
        ScheduledTaskChangeSetService,
    )

    try:
        service = ScheduledTaskChangeSetService(db, user_id=user_id, org_id=org_id)
        submit = service.begin if submission_mode == "apply_if_allowed" else service.propose
        change_set = await submit(
            operation=operation,
            resource_id=task_id,
            base_snapshot=base_snapshot,
            proposed_snapshot=definition,
            idempotency_key=idempotency_key,
            **({"submission_mode": submission_mode} if submission_mode == "apply_if_allowed" else {}),
        )
    except ScheduledTaskChangeError as exc:
        return {"success": False, "message": str(exc)}
    except Exception:
        logger.exception(
            "chat_task_manager changeset_propose_failed | operation={} | task_id={}",
            operation, task_id,
        )
        return {"success": False, "message": "暂时无法生成变更方案，请稍后重试。"}

    logger.info(
        "chat_task_manager changeset_proposed | change_set={} | operation={} | task_id={}",
        change_set.get("id"), operation, task_id,
    )
    return {
        "success": True,
        "status": "submitted",
        "change_set_id": str(change_set["id"]),
        "message": submission_receipt(change_set),
    }
