"""One lossless, pre-dispatch planner-argument repair; never repair professional output."""
import json
from copy import deepcopy

from services.agent.agent_result import AgentResult
from services.tools.argument_validation import _issues, decode_image_arguments
from services.tools.result import ToolResult

from .recovery import RecoveryReceipt, RecoveryAction

KEY = "ecom_argument_correction"
STOP = "主图策划参数纠错已停止，请按具体字段错误补充信息。本次未自动重发策划或生图请求。"


def equivalent_arguments(value):
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    if "num_images" in result and "image_count" not in result:
        result["image_count"] = result.pop("num_images")
    def integer(raw):
        return int(raw) if isinstance(raw, str) and raw.isascii() and raw.isdecimal() else raw
    if "image_count" in result:
        result["image_count"] = integer(result["image_count"])
    if isinstance(result.get("references"), list):
        for reference in result["references"]:
            if isinstance(reference, dict) and "content_index" in reference:
                reference["content_index"] = integer(reference["content_index"])
    return result


class PlannerArgumentValidationError(ValueError):
    code = "ECOM_PLAN_ARGUMENTS_INVALID"
    def __init__(self, issues, arguments, schema):
        self.issues, self.arguments = issues, arguments
        normalized = equivalent_arguments(arguments)
        repairable = isinstance(normalized, dict) and normalized != arguments and not _issues(normalized, schema)
        if isinstance(normalized, dict) and type(normalized.get("image_count")) is int and not 1 <= normalized["image_count"] <= 15:
            repairable = False
        self.receipt = RecoveryReceipt(status="error", error={"code": self.code, "category": "arguments"},
            recovery=RecoveryAction(action="repair_arguments" if repairable else "report_error",
                allowed_changes=[i["path"] for i in issues] if repairable else [])).model_dump(mode="json")
        self.expected = normalized if repairable else None
        super().__init__(json.dumps({**self.receipt, "issues": issues, "parameters": schema,
            "message": "策划尚未派发，未调用模型。只修正等价字段格式，保留真实素材身份、顺序及用户原文。"}, ensure_ascii=False))


def validate_planner_arguments(spec, arguments):
    from services.tools.spec import thaw
    parameters = spec.to_schema()["function"]["parameters"]
    arguments = thaw(arguments)
    issues = _issues(arguments, parameters)
    count = arguments.get("image_count") if isinstance(arguments, dict) else None
    if type(count) is int and count > 15:
        issues.append({"path": "$.image_count", "reason": "最大值为15"})
    if issues:
        raise PlannerArgumentValidationError(issues, arguments, parameters)


class PlannerArgumentCorrection:
    def __init__(self, blocks):
        self.blocks = blocks
        self.state = next((b[KEY] for b in blocks if isinstance(b.get(KEY), dict)), None)
        self.stop_message = ""

    def before_model(self):
        if self.state and self.state.get("dispatch_reserved") and not self.state.get("done"):
            self.stop_message = "主图参数纠错已进入执行边界，恢复后不自动重发。请核验已有策划记录后明确重试。"

    def filter_calls(self, calls):
        if not self.state:
            return calls, []
        ready, rejected = [], []
        for call in calls:
            if call["name"] != "plan_ecommerce_images" and self.state.get("done"):
                ready.append(call)
                continue
            try:
                args = decode_image_arguments(call.get("arguments"))
            except (ValueError, TypeError):
                args = None
            if (not self.state.get("used") and call["name"] == "plan_ecommerce_images"
                    and args == self.state["expected"]):
                self.state["used"] = True
                ready.append(call)
            else:
                result = AgentResult(STOP, status="error", metadata={"stop_workflow": True})
                rejected.append((call, result, True, STOP))
                self.stop_message = STOP
        return ready, rejected

    def reserve(self, calls):
        if self.state and self.state.get("used") and not self.state.get("done") and calls:
            self.state["dispatch_reserved"] = True

    def observe(self, results):
        for call, result, _error, _display in results:
            if call["name"] != "plan_ecommerce_images":
                continue
            if (isinstance(result, ToolResult) and isinstance(result.exception, PlannerArgumentValidationError)
                    and result.execution.status == "not_started" and not result.execution.handler_started):
                error = result.exception
                if self.state is None and error.expected is not None:
                    self.state = {"expected": error.expected, "used": False, "done": False}
                    block = next(b for b in self.blocks if b.get("tool_call_id") == call["id"])
                    block[KEY] = self.state
                else:
                    self.stop_message = STOP + "\n" + str(error)
            elif self.state and self.state.get("used"):
                self.state["done"] = True
