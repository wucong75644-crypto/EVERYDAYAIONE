"""One image-argument correction round, persisted in existing tool-step checkpoints.

Only a typed, pre-dispatch schema rejection opens this allowance. A correction
must preserve the rejected call's meaningful inputs and cannot resend accepted
siblings. The model cannot set the allowance, accepted fact, or checkpoint.
"""
from __future__ import annotations

import hashlib
import json

from services.agent.agent_result import AgentResult
from services.tools.argument_validation import ToolArgumentValidationError, decode_image_arguments
from services.tools.result import ToolResult

KEY = "image_argument_validation"
_STOP = "图片参数自动纠正已停止；未接受的请求没有创建图片任务或预扣图片积分。已接受的任务保持原状态。请检查参数或补充必要信息后继续。"
_INPUT_STOP = "图片请求未接受；本次请求未创建图片任务或预扣图片积分。已接受的其他任务保持原状态。请按具体错误补充必要信息后继续。"
_RECOVERY_STOP = "图片纠错调用已进入提交边界，恢复后不会自动重新提交。请查看已有图片任务核实受理状态；已接受的任务继续处理。"


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _arguments(call):
    try:
        return decode_image_arguments(call.get("arguments"))
    except (ValueError, TypeError):
        return {}


def _protected(args):
    if not isinstance(args.get("prompt"), str) or not args["prompt"].strip():
        return None
    fields = ("prompt", "mode", "references", "aspect_ratio", "resolution", "output_format",
              "background", "size_requirement", "source_prompt", "source_task_id", "plan_item_id", "variant_id")
    # Unknown fields may carry generation intent (mask, weight, batches or old
    # reference URLs). Removing them could silently change the requested image.
    if set(args) - set(fields) - {"model", "model_name", "format", "size"}:
        return None
    values = {key: args[key] for key in fields if key in args}
    values.setdefault("references", [])
    # Protect unambiguous legacy spellings too, without accepting them as input.
    for old, new in (("format", "output_format"), ("size", "resolution")):
        if old in args:
            if new in args:
                return None
            values[new] = args[old]
    if isinstance(values.get("output_format"), str):
        values["output_format"] = values["output_format"].lower()
    if isinstance(values.get("resolution"), str):
        values["resolution"] = values["resolution"].upper()
    return {key: _hash(value) for key, value in values.items()}


class ImageArgumentCorrection:
    def __init__(self, blocks):
        self.blocks = blocks
        self.repair = None
        self.pending = []
        self.stop_message = ""
        for block in blocks:
            facts = block.get(KEY)
            if not isinstance(facts, dict):
                continue
            if facts.get("initial") and facts.get("repairable"):
                self.pending.append(facts.get("protected"))
            if isinstance(facts.get("repair"), dict):
                self.repair = facts["repair"]

    def start_round(self, model_round):
        if not self.repair:
            return False
        if self.repair.get("done") or self.repair.get("dispatch_reserved"):
            self.stop_message = self.repair.get("completion_message") or _RECOVERY_STOP
            return False
        if self.repair.get("used"):
            self.stop_message = "图片参数纠错已尝试，恢复后停止自动提交。请查看参数错误和已有图片任务，补充必要信息后继续。"
            return False
        reserved = self.repair.get("model_round")
        if reserved is not None and reserved != model_round:
            self.stop_message = _STOP
            return False
        self.repair["model_round"] = model_round
        self.repair["used"] = True
        return True

    def record_usage(self, before, after, gateway):
        if not self.repair:
            return
        usage = {key: max(0, after.get(key, 0) - before.get(key, 0))
                 for key in ("prompt_tokens", "completion_tokens")}
        for key, count in usage.items():
            self.repair[key] = self.repair.get(key, 0) + count
        estimate = getattr(gateway, "estimate_cost_unified", None)
        if callable(estimate):
            try:
                cost = estimate(input_tokens=self.repair.get("prompt_tokens", 0),
                                output_tokens=self.repair.get("completion_tokens", 0))
                credits = float(cost.estimated_credits)
                self.repair["estimated_chat_credits"] = credits if 0 <= credits < float("inf") else None
            except Exception:
                # Observability must not turn a completed model round into a
                # failed business invocation or permission to resubmit.
                self.repair["estimated_chat_credits"] = None

    def filter_calls(self, calls):
        if not self.repair or not self.repair.get("used"):
            return calls, []
        remaining = list(self.pending)
        ready, rejected = [], []
        for call in calls:
            if call["name"] != "generate_image":
                ready.append(call)
                continue
            values = _protected(_arguments(call))
            matches = [index for index, original in enumerate(remaining)
                       if original and values and all(values.get(key) == digest for key, digest in original.items())]
            if not self.repair.get("done") and len(matches) == 1:
                remaining.pop(matches[0])
                ready.append(call)
            else:
                result = AgentResult(summary=_STOP, status="error", error_message="IMAGE_ARGUMENT_CORRECTION_STOPPED",
                    metadata={"accepted": False, "submission_state": "not_accepted", "retryable": False})
                rejected.append((call, result, True, result.summary))
                self.stop_message = _STOP
        return ready, rejected

    def reserve_dispatch(self, calls):
        """Save before IO. Recovery must not ask the model for a new call ID.

        The existing invocation ledger remains authoritative for acceptance.
        A crash at this boundary can precede or follow IO, so automatic recovery
        stops conservatively instead of guessing that a paid call did not run.
        """
        if self.repair and self.repair.get("used") and calls:
            self.repair["dispatch_reserved"] = True
            self.repair["dispatch_calls"] = [{"id": c["id"], "name": c["name"],
                                             "arguments_sha256": _hash(_arguments(c))} for c in calls]

    def observe(self, results):
        repairing = bool(self.repair and self.repair.get("used") and not self.repair.get("done"))
        image_results = []
        for call, result, _, display in results:
            if call["name"] != "generate_image":
                continue
            image_results.append((result, display))
            block = next((b for b in self.blocks if b.get("tool_call_id") == call["id"]), None)
            if block is None or KEY in block:
                continue
            invalid = (isinstance(result, ToolResult) and isinstance(result.exception, ToolArgumentValidationError)
                       and result.execution.status == "not_started" and not result.execution.handler_started)
            protected = _protected(_arguments(call)) if invalid else None
            metadata = result.metadata if isinstance(result, ToolResult) else getattr(result, "metadata", {})
            facts = {"initial": not repairing, "invalid": invalid, "accepted": metadata.get("accepted") is True,
                     "repairable": invalid and bool(protected), "corrected": repairing and metadata.get("accepted") is True}
            block[KEY] = facts
            if invalid and not repairing and protected:
                facts["protected"] = protected
                self.pending.append(protected)
                if self.repair is None:
                    self.repair = {"used": False, "done": False, "model_round": None}
                    facts["repair"] = self.repair
            elif invalid or metadata.get("accepted") is False:
                detail = ("参数校验未通过：" + "；".join(f"{i['path']}：{i['reason']}" for i in result.exception.issues[:3])
                          if invalid else str(display))
                self.stop_message = (_STOP if invalid else _INPUT_STOP) + "\n" + detail
        if repairing:
            self.repair["done"] = True
            # Deliver the real tool acknowledgements without another model
            # round that might submit more images while explaining the result.
            self.stop_message = self.stop_message or "\n".join(
                result.model_content("chat") if isinstance(result, ToolResult) and result.execution.status == "uncertain"
                else str(display) for result, display in image_results) or _STOP
            self.repair["completion_message"] = self.stop_message

    def summary(self):
        facts = [b[KEY] for b in self.blocks if isinstance(b.get(KEY), dict)]
        if not facts:
            return None
        initial = [f for f in facts if f.get("initial")]
        invalid = sum(f.get("invalid") is True for f in initial)
        corrected = sum(f.get("corrected") is True for f in facts)
        repair = self.repair or {}
        return {"version": 1, "initial_calls": len(initial), "invalid_initial_calls": invalid,
                "corrected_calls": corrected, "unresolved_calls": max(0, invalid - corrected),
                "correction_rounds": int(repair.get("used") is True),
                "prompt_tokens": repair.get("prompt_tokens", 0), "completion_tokens": repair.get("completion_tokens", 0),
                "estimated_chat_credits": repair.get("estimated_chat_credits")}


def summarize_metrics(rows):
    """Aggregate finished-chat facts only; never expose prompts or tool inputs."""
    counters = ("initial_calls", "invalid_initial_calls", "corrected_calls", "unresolved_calls",
                "correction_rounds", "prompt_tokens", "completion_tokens")
    summary = {key: 0 for key in counters}
    summary.update({"recorded_chats": 0, "estimated_chat_credits": 0.0, "cost_unknown_rounds": 0})
    for row in rows:
        metrics = row.get("metrics")
        if not isinstance(metrics, dict) or type(metrics.get("version")) is not int or metrics["version"] != 1:
            continue
        summary["recorded_chats"] += 1
        for key in counters:
            value = metrics.get(key)
            if type(value) is int and value >= 0:
                summary[key] += value
        cost = metrics.get("estimated_chat_credits")
        if type(cost) in (float, int) and cost >= 0 and cost < float("inf"):
            summary["estimated_chat_credits"] += cost
        elif metrics.get("correction_rounds"):
            summary["cost_unknown_rounds"] += 1
    summary["initial_error_rate"] = (summary["invalid_initial_calls"] / summary["initial_calls"]
                                     if summary["initial_calls"] else None)
    summary["correction_success_rate"] = (summary["corrected_calls"] / summary["invalid_initial_calls"]
                                          if summary["invalid_initial_calls"] else None)
    return summary
