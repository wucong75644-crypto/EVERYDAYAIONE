"""受控定时任务工作流：规划、预检、完成判定与配置指纹。"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List
from uuid import uuid4


_PREFLIGHT_BLOCKED_TOOLS = frozenset({
    "manage_scheduled_task", "erp_execute", "trigger_erp_sync", "file_delete",
    "generate_image", "generate_video", "image_agent",
})


def stable_json_hash(value: Dict[str, Any]) -> str:
    """计算任务定义的稳定指纹，防止预检后被静默篡改。"""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tool_name_set(tools: Iterable[Dict[str, Any]]) -> set[str]:
    return {
        item.get("function", {}).get("name", "")
        for item in tools
        if item.get("function", {}).get("name")
    }


def preflight_allowed_tool_names(org_id: str) -> set[str]:
    """预检可用工具：只允许读取或隔离沙盒计算，禁止业务副作用。"""
    from config.chat_tools import SafetyLevel, get_core_tools, get_safety_level

    names = tool_name_set(get_core_tools(org_id=org_id))
    return {
        name for name in names
        if name not in _PREFLIGHT_BLOCKED_TOOLS
        and get_safety_level(name) != SafetyLevel.DANGEROUS
    }


@dataclass(frozen=True)
class ScheduledExecutionPolicy:
    """用户确认后不可由模型扩张的执行边界。"""

    allowed_tools: frozenset[str]
    required_tools: frozenset[str]
    tool_timeout_sec: float
    erp_step_timeout_sec: float
    final_reserve_sec: float
    allow_empty_result: bool = False
    version: int = 1

    @classmethod
    def from_dict(cls, value: Dict[str, Any] | None, *, timeout_sec: int) -> "ScheduledExecutionPolicy":
        value = value or {}
        cap = max(10.0, float(timeout_sec))
        allowed = frozenset(str(x) for x in value.get("allowed_tools", []) if x)
        required = frozenset(str(x) for x in value.get("required_tools", []) if x)
        tool_timeout = min(float(value.get("tool_timeout_sec", 75.0)), cap - 5.0)
        erp_timeout = min(float(value.get("erp_step_timeout_sec", 60.0)), tool_timeout - 2.0)
        return cls(
            allowed_tools=allowed,
            required_tools=required,
            tool_timeout_sec=max(5.0, tool_timeout),
            erp_step_timeout_sec=max(3.0, erp_timeout),
            final_reserve_sec=max(3.0, min(float(value.get("final_reserve_sec", 15.0)), cap / 3)),
            allow_empty_result=bool(value.get("allow_empty_result", False)),
            version=int(value.get("version", 1)),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "allowed_tools": sorted(self.allowed_tools),
            "required_tools": sorted(self.required_tools),
            "tool_timeout_sec": self.tool_timeout_sec,
            "erp_step_timeout_sec": self.erp_step_timeout_sec,
            "final_reserve_sec": self.final_reserve_sec,
            "allow_empty_result": self.allow_empty_result,
        }


def validate_plan(raw: Dict[str, Any], *, available_tools: set[str], timeout_sec: int) -> tuple[Dict[str, Any], ScheduledExecutionPolicy]:
    """校验模型规划并编译为系统可执行的 policy，拒绝模型虚构的能力。"""
    if not isinstance(raw, dict):
        raise ValueError("规划结果不是对象")

    objective = str(raw.get("objective") or "").strip()
    if not objective:
        raise ValueError("规划缺少任务目标")

    raw_tools = raw.get("allowed_tools")
    if not isinstance(raw_tools, list):
        raise ValueError("规划缺少 allowed_tools")
    allowed = {str(x).strip() for x in raw_tools if str(x).strip()}
    if not allowed:
        raise ValueError("规划没有可执行工具")
    illegal = allowed - available_tools
    if illegal:
        raise ValueError(f"规划包含未授权工具: {', '.join(sorted(illegal))}")

    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("规划缺少执行步骤")
    normalized_steps: List[Dict[str, Any]] = []
    required_tools: set[str] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError("规划步骤格式无效")
        tools = {str(x).strip() for x in step.get("tools", []) if str(x).strip()}
        if not tools or not tools <= allowed:
            raise ValueError(f"第 {index} 步工具不在允许范围内")
        required = bool(step.get("required", True))
        if required:
            required_tools.update(tools)
        normalized_steps.append({
            "id": str(step.get("id") or f"step-{index}"),
            "intent": str(step.get("intent") or "执行数据查询").strip(),
            "tools": sorted(tools),
            "required": required,
            "verify": str(step.get("verify") or "工具返回可用结果").strip(),
        })

    contract = raw.get("output_contract") if isinstance(raw.get("output_contract"), dict) else {}
    allow_empty = bool(contract.get("allow_empty_result", False))
    evidence = contract.get("required_evidence")
    if not isinstance(evidence, list):
        evidence = []
    plan = {
        "version": 1,
        "objective": objective,
        "steps": normalized_steps,
        "output_contract": {
            "allow_empty_result": allow_empty,
            "required_evidence": [str(x)[:160] for x in evidence if str(x).strip()][:8],
        },
    }
    policy = ScheduledExecutionPolicy(
        allowed_tools=frozenset(allowed),
        required_tools=frozenset(required_tools),
        tool_timeout_sec=min(75.0, max(10.0, timeout_sec - 15.0)),
        erp_step_timeout_sec=min(60.0, max(5.0, timeout_sec - 20.0)),
        final_reserve_sec=min(15.0, max(5.0, timeout_sec / 6)),
        allow_empty_result=allow_empty,
    )
    return plan, policy


def parse_json_object(text: str) -> Dict[str, Any]:
    """从模型回复提取一个 JSON 对象；不接受模糊自然语言降级。"""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise ValueError("模型未返回 JSON 规划")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("模型规划不是 JSON 对象")
    return value


async def create_plan(*, db: Any, org_id: str, definition: Dict[str, Any]) -> tuple[Dict[str, Any], ScheduledExecutionPolicy]:
    """由模型选择工具和初始路径；系统随后验证并收敛权限。"""
    from config.chat_tools import get_core_tools
    from core.config import get_settings
    from services.adapters.factory import create_chat_adapter

    available = preflight_allowed_tool_names(org_id)
    schemas = get_core_tools(org_id=org_id)
    tool_descriptions = [
        {"name": tool["function"]["name"], "description": tool["function"].get("description", "")[:280]}
        for tool in schemas
        if tool["function"]["name"] in available
    ]
    prompt = {
        "task": {
            "name": definition["name"],
            "prompt": definition["prompt"],
            "schedule_type": definition["schedule_type"],
        },
        "available_tools": tool_descriptions,
        "required_output_schema": {
            "objective": "string",
            "allowed_tools": ["tool name; only from available_tools"],
            "steps": [{"id": "string", "intent": "string", "tools": ["tool"], "required": True, "verify": "string"}],
            "output_contract": {"allow_empty_result": False, "required_evidence": ["string"]},
        },
    }
    settings = get_settings()
    adapter = create_chat_adapter(
        getattr(settings, "agent_loop_model", None) or "qwen3.5-plus",
        org_id=org_id,
        db=db,
    )
    try:
        response = await adapter.chat_sync(messages=[
            {"role": "system", "content": "你是定时任务规划器。只输出符合 schema 的 JSON；不执行任务，不虚构工具。"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ])
        raw = parse_json_object(getattr(response, "content", ""))
        return validate_plan(raw, available_tools=available, timeout_sec=int(definition["timeout_sec"]))
    finally:
        await adapter.close()


def completion_gate(*, result: Any, policy: ScheduledExecutionPolicy) -> Dict[str, Any]:
    """决定一段文本能否成为可计费、可投递的定时任务结果。"""
    reasons: List[str] = []
    stop_reason = str(getattr(result, "stop_reason", "") or "")
    if stop_reason:
        reasons.append(f"loop_stopped:{stop_reason}")
    if not bool(getattr(result, "is_llm_synthesis", False)):
        reasons.append("final_synthesis_missing")
    text = str(getattr(result, "text", "") or "").strip()
    if not text:
        reasons.append("empty_final_output")
    tools_called = set(getattr(result, "tools_called", []) or [])
    successful_tools = {
        outcome.get("tool_name") for outcome in getattr(result, "tool_outcomes", []) or []
        if outcome.get("status") == "success" and outcome.get("tool_name")
    }
    missing = policy.required_tools - successful_tools
    if missing:
        reasons.append(f"required_tools_missing:{','.join(sorted(missing))}")
    if not policy.allow_empty_result and "查询结果为空" in text:
        reasons.append("empty_business_result")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "tools_called": sorted(tools_called),
        "successful_tools": sorted(successful_tools),
        "required_tools": sorted(policy.required_tools),
    }


def _trace_rows(*, org_id: str, execution_kind: str, execution_id: str, task_id: str | None,
                plan: Dict[str, Any], result: Any, gate: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, step in enumerate(plan.get("steps", []), start=1):
        rows.append({
            "org_id": org_id, "execution_kind": execution_kind, "execution_id": execution_id,
            "task_id": task_id, "step_order": index, "event_type": "planned_step",
            "tool_name": ",".join(step.get("tools", [])), "status": "planned",
            "summary": step.get("intent", ""), "metadata": {"verify": step.get("verify", "")},
        })
    for offset, tool_name in enumerate(getattr(result, "tools_called", []) or [], start=1):
        rows.append({
            "org_id": org_id, "execution_kind": execution_kind, "execution_id": execution_id,
            "task_id": task_id, "step_order": 100 + offset, "event_type": "tool_called",
            "tool_name": tool_name, "status": "completed" if gate.get("passed") else "checked",
            "summary": "工具调用已完成；详见工具审计记录", "metadata": {},
        })
    rows.append({
        "org_id": org_id, "execution_kind": execution_kind, "execution_id": execution_id,
        "task_id": task_id, "step_order": 999, "event_type": "completion_gate",
        "tool_name": None, "status": "passed" if gate.get("passed") else "failed",
        "summary": "; ".join(gate.get("reasons", [])) or "满足结果契约",
        "metadata": gate,
    })
    return rows


async def create_draft_and_preflight(
    *, db: Any, org_id: str, user_id: str, definition: Dict[str, Any],
) -> Dict[str, Any]:
    """保存不可执行草稿，规划并以同一 Agent 引擎做零额度预检。"""
    draft_id = str(uuid4())
    config_hash = stable_json_hash(definition)
    db.table("scheduled_task_drafts").insert({
        "id": draft_id, "org_id": org_id, "user_id": user_id,
        "definition": definition, "config_hash": config_hash, "status": "planning",
    }).execute()
    try:
        plan, policy = await create_plan(db=db, org_id=org_id, definition=definition)
        preflight_id = str(uuid4())
        db.table("scheduled_task_drafts").update({
            "status": "preflight_running", "plan": plan,
            "execution_policy": policy.as_dict(), "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", draft_id).execute()
        db.table("scheduled_task_preflight_runs").insert({
            "id": preflight_id, "draft_id": draft_id, "org_id": org_id,
            "config_hash": config_hash, "definition_snapshot": definition,
            "plan_snapshot": plan, "policy_snapshot": policy.as_dict(), "status": "running",
        }).execute()
        started = time.monotonic()
        from services.agent.scheduled_task_agent import ScheduledTaskAgent
        preflight_task = {
            "id": preflight_id, "org_id": org_id, "user_id": user_id,
            **definition, "execution_policy": policy.as_dict(), "last_summary": None,
        }
        result = await ScheduledTaskAgent(
            db, preflight_task, execution_mode="preflight",
        ).execute()
        gate = result.completion_gate or completion_gate(result=result, policy=policy)
        passed = result.status == "success" and bool(gate.get("passed"))
        final_status = "passed" if passed else ("timeout" if result.status == "timeout" else "failed")
        trace = _trace_rows(
            org_id=org_id, execution_kind="preflight", execution_id=preflight_id,
            task_id=None, plan=plan, result=result, gate=gate,
        )
        db.table("scheduled_task_preflight_runs").update({
            "status": final_status, "result_summary": result.summary or result.text[:500],
            "error_message": None if passed else (result.error_message or result.text[:500]),
            "completion_gate": gate, "tool_trace": trace,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }).eq("id", preflight_id).execute()
        if trace:
            db.table("scheduled_task_execution_events").insert(trace).execute()
        db.table("scheduled_task_drafts").update({
            "status": "ready" if passed else "failed", "latest_preflight_id": preflight_id,
            "preflight_config_hash": config_hash if passed else None,
            "error_message": None if passed else (result.error_message or result.text[:500]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", draft_id).execute()
    except Exception as exc:
        db.table("scheduled_task_drafts").update({
            "status": "failed", "error_message": str(exc)[:500],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", draft_id).execute()

    data = db.table("scheduled_task_drafts").select("*").eq("id", draft_id).limit(1).execute().data
    if not data:
        raise RuntimeError("预检草稿读取失败")
    draft = data[0]
    if draft.get("latest_preflight_id"):
        preflight = db.table("scheduled_task_preflight_runs").select("*").eq(
            "id", draft["latest_preflight_id"],
        ).limit(1).execute().data
        draft["latest_preflight"] = preflight[0] if preflight else None
    return draft
