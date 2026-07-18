"""把校验失败转换为原模型循环可消费的结构化 Observation。"""

from __future__ import annotations

import json

from services.agent.runtime.evidence_guard.models import GuardReceipt


def build_retry_observation(receipt: GuardReceipt) -> dict[str, str]:
    issues = [
        {
            "claim": issue.claim.raw,
            "reason": issue.reason,
        }
        for issue in receipt.issues
    ]
    payload = {
        "type": "evidence_validation_error",
        "retryable": True,
        "issues": issues,
        "instruction": (
            "最终回答包含尚无结构化工具证据支持的数值。"
            "请复用现有工具结果；需要计算时调用沙盒并用 emit_table "
            "输出结构化结果后重新回答；"
            "不要猜测或手工编造数字。"
        ),
    }
    return {
        "role": "system",
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
